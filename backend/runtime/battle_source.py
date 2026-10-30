"""Resolve sources at battle boundaries, keeping the driver's device pinned."""
import copy
import hashlib
import os
from pathlib import Path

import yaml


def accepted_envelope(operation):
    """Resolve a delegated child's immutable source through the existing ledger."""
    from runtime.operations import validate_worker_context
    validate_worker_context(operation)
    battle_id = os.environ.get('TOWER_BATTLE_ID')
    if not battle_id:
        if os.environ.get('TOWER_BATTLE_GENERATION'):
            raise ValueError('Delegated generation has no battle identity')
        return operation['request'].get('battle_envelope')
    from towerpilot_workflows.battles import Battles
    battle = Battles(os.environ['TOWER_OPERATION_DB']).validate_driver(
        battle_id, os.environ.get('TOWER_BATTLE_GENERATION'), operation['id'])
    return copy.deepcopy(battle['envelope'])


def accepted_source(name, chosen=None):
    """Return a worker's accepted source without re-reading editable profiles."""
    from runtime.operations import environment_operation, validate_worker_context
    store, operation_id = environment_operation()
    if store is None:
        return None
    operation = store.get(operation_id)
    validate_worker_context(operation, name)
    envelope = accepted_envelope(operation)
    if not envelope:
        return None
    cfg = copy.deepcopy(envelope['config'])
    selected = envelope.get('preset_key', cfg['preset'])
    if chosen is not None and chosen != selected:
        raise ValueError('Accepted run selected a different frozen preset')
    if selected not in cfg.get('presets', {}):
        raise ValueError('Selected run is absent from the accepted configuration')
    cfg['preset'] = selected
    cfg['active_instance'] = name
    validate_worker_context(operation, name, cfg)
    return cfg, copy.deepcopy(envelope.get('profile'))


def reload_battle_source():
    """Validate the latest source before installing it for the next battle.

    A changed device/account binding is refused. A dashboard selection cannot
    redirect an existing driver to another emulator or another run.
    """
    import settings
    from player import accounts, playerprofile
    import flows

    current = settings.CONFIG
    name, selected = current['active_instance'], current['preset']
    raw = yaml.safe_load(settings.CONFIG_PATH.read_text(encoding='utf-8'))
    raw['active_instance'] = name
    if name not in raw.get('instances', {}):
        raise ValueError('The running emulator was removed from configuration')
    old_device, new_device = current['instances'][name], raw['instances'][name]
    for field in ('serial', 'account', 'rendering'):
        if old_device.get(field) != new_device.get(field):
            raise ValueError(f'Emulator {field} changed; stop the driver before rebinding')
    cfg = accounts.effective(raw)
    if cfg.get('active_profile') != current.get('active_profile'):
        raise ValueError('Active profile changed; restart the driver to switch profiles')
    for key in settings._OVERRIDABLE:
        if isinstance(new_device.get(key), dict):
            cfg.setdefault(key, {}).update(copy.deepcopy(new_device[key]))
    profile = None
    previous = playerprofile.CONFIG
    try:
        playerprofile.CONFIG = cfg
        if cfg.get('active_profile'):
            path = Path(playerprofile.PROFILES_DIR) / f"{cfg['active_profile']}.yaml"
            profile_bytes = path.read_bytes()
            profile = yaml.safe_load(profile_bytes)
            if not isinstance(profile, dict):
                raise ValueError('The saved profile must be a mapping')
            profile.update(_name=cfg['active_profile'], _path=str(path))
            problems = playerprofile.validate(profile)
            if problems:
                raise ValueError('; '.join(problems))
            revision = hashlib.sha256(profile_bytes).hexdigest()
            compiled = {}
            for key in profile.get('blueprints', {}):
                body = playerprofile.compile_preset(profile, key)
                body['runner'] = flows.script(body['kind'])
                body.setdefault('_source', {})['profile_revision'] = revision
                compiled['bp_' + key] = body
            cfg['presets'] = {key: value for key, value in cfg.get('presets', {}).items()
                              if not key.startswith('bp_')}
            cfg['presets'].update(compiled)
        body = cfg.get('presets', {}).get(selected)
        if body is None or not body.get('defined', True):
            raise ValueError('The running run configuration is no longer available')
        cfg['preset'] = selected
        from player.run_controls import effective_preset
        body = effective_preset(cfg, selected)
    finally:
        playerprofile.CONFIG = previous
    from player import readiness
    readiness.require(settings.ROOT, cfg, body)
    # Commit only after every check passed. Preserve the shared CONFIG object.
    settings.CONFIG.clear()
    settings.CONFIG.update(copy.deepcopy(cfg))
    if profile is not None:
        playerprofile.PROFILE = profile
    return copy.deepcopy(body), copy.deepcopy(cfg)
