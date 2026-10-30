"""Pure saved run controls; persistence, revisions and locking belong to callers.

Editing a run clones shared policies before changing their contents. Reading a
legacy policy offers a reviewable queue conversion without installing it.

What each action's saved settings mean, and which blueprint kinds carry them, is the action's saved adapter
(`player.saved_controls`), found through the action catalog: nothing here names an action.
"""
from __future__ import annotations

import copy

from player import playerprofile, run_controls, saved_controls
from towerpilot_workflows.control_settings import documents


def normalize_changes(patch: dict) -> dict:
    """Accept the HTTP dotted-path patch or the canonical nested settings patch."""
    if not isinstance(patch, dict):
        raise ValueError('changes must be an object')
    if any('.' in key for key in patch):
        nested = {}
        docs = documents()
        for path, value in patch.items():
            if path in docs:
                nested[path] = copy.deepcopy(value)
                continue
            parts = path.split('.')
            if len(parts) != 3 or parts[0] != 'routines':
                raise ValueError(f'Unknown saved control: {path}')
            nested.setdefault('routines', {}).setdefault(parts[1], {})[parts[2]] = copy.deepcopy(value)
    else:
        nested = copy.deepcopy(patch)
    return run_controls.validate_patch(nested)


def _blueprint(profile: dict, run: str) -> dict:
    if not isinstance(run, str) or run not in profile.get('blueprints', {}):
        raise ValueError(f'Unknown blueprint: {run}')
    return profile['blueprints'][run]


def _capabilities(bp: dict) -> dict:
    return saved_controls.capabilities(bp)


def read(profile: dict, cfg: dict, run: str) -> dict:
    """Return saved effective controls and a separate explicit conversion draft."""
    bp = _blueprint(profile, run)
    preset = playerprofile.compile_preset(profile, run)
    constraints = {f'routines.{routine}.{field}': {'min': spec[0], 'max': spec[1]}
                   for routine, fields in run_controls.FIELDS.items()
                   for field, spec in fields.items() if spec is not bool}
    capabilities = _capabilities(bp)
    for path in capabilities['controls']:
        parts = path.split('.')
        if len(parts) == 3 and '@' in parts[1] and path not in constraints and run_controls.FIELDS[parts[1]][parts[2]] is not bool:
            spec = run_controls.FIELDS[parts[1]][parts[2]]
            constraints[path] = {'min': spec[0], 'max': spec[1]}
    result = {'blueprint': run, 'settings': run_controls.resolve(preset, cfg), 'capabilities': capabilities, 'constraints': constraints}
    if bp.get('kind') not in saved_controls.FULL_DRIVER_KINDS:
        result['notice'] = 'This driver supports saved collection settings. Live controls and per-battle history are currently available for farm and tournament drivers.'
    result.update(saved_controls.read_extras(profile, bp, preset, cfg))
    return result


def update(profile: dict, cfg: dict, run: str, patch: dict) -> dict:
    """Return {profile, settings, ...}; inputs and all unrelated runs stay intact."""
    changes = normalize_changes(patch)
    allowed = set(_capabilities(_blueprint(profile, run))['controls'])
    for path in run_controls.flatten(changes):
        if path not in allowed:
            raise ValueError(f'Control is unavailable for this run: {path}')
    updated = copy.deepcopy(profile)
    saved_controls.write(saved_controls.WriteContext(updated, run, _blueprint(updated, run)), changes)
    result = read(updated, cfg, run)
    return {'profile': updated, **result}