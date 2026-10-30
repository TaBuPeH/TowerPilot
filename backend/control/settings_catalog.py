"""Explicit dashboard settings, independent of device-bound runner imports."""
import copy
import math
from device.preview.common import DEFAULTS as PREVIEW_DEFAULTS
from player.gather_settings import AD_GEM_FIELDS, ORBIT_TIMING_FIELDS

TWEAK_LIMIT = 50      # names in one standing tournament deck-change list


def field(key, label, kind, default, low=None, high=None, unit='', description='', whole_numbers=False, options=None, max_items=None):
    spec = dict(key=key, path=key, label=label, type=kind, default=default,
                min=low, max=high, step=1 if kind == 'integer' or whole_numbers else 'any',
                wholeNumbers=whole_numbers,
                unit=unit, description=description, options=options)
    if kind == 'string-list':
        spec['maxItems'] = max_items      # only a list has a length limit; no other field kind carries the key
    return spec


def runnable_presets(cfg):
    """Presets a runner can start: every preset except an explicit placeholder (`defined: false`). Derived from the
    configuration, never from an allowlist that drifts when a preset is added."""
    return [name for name, body in (cfg.get('presets') or {}).items() if isinstance(body, dict) and body.get('defined', True)]


def preset_choices(cfg):
    """The runnable presets as {value, label} options; the label is the one the tray menu shows (settings.preset_menu)."""
    presets = cfg.get('presets') or {}
    return [{'value': name, 'label': presets[name].get('label') or name.replace('_', ' ').title()} for name in runnable_presets(cfg)]


def read(data, key, default):
    node = data
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            return copy.deepcopy(default)
        node = node[part]
    return copy.deepcopy(node)


def write(data, key, value):
    parts = key.split('.')
    for part in parts[:-1]:
        if part not in data:
            data[part] = {}
        if not isinstance(data[part], dict):
            raise ValueError(f'{part}: existing value is not an object')
        data = data[part]
    data[parts[-1]] = copy.deepcopy(value)


def validate_string_list(spec, value):
    """An ordered list of non-blank strings, optionally at most `maxItems` long and drawn from `options`. Nothing is coerced:
    a number, a nested list, a blank string or a name outside the options is refused, and so is a value that is not a list."""
    label = spec['label']
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected string-list")
    limit = spec.get('maxItems')
    if limit is not None and len(value) > limit:
        raise ValueError(f"{label}: at most {limit} entries")
    allowed = [option['value'] for option in spec['options']] if spec.get('options') else None
    for number, item in enumerate(value, 1):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}: entry {number} must be a non-blank string")
        if allowed is not None and item not in allowed:
            raise ValueError(f"{label}: entry {number} is not one of the allowed values")


def validate(spec, value):
    kind = spec['type']
    if kind == 'string-list':
        validate_string_list(spec, value)
        return
    if kind == 'boolean':
        valid = isinstance(value, bool)
    elif kind in ('string', 'enum'):
        valid = isinstance(value, str)
        if valid and kind == 'enum':
            valid = value in [option['value'] for option in spec['options']]
    elif kind in ('range', 'point'):
        valid = isinstance(value, list) and len(value) == 2
    else:
        valid = not isinstance(value, bool) and isinstance(value, (int, float))
        if kind == 'integer':
            valid = type(value) is int
    if not valid:
        raise ValueError(f"{spec['label']}: expected {kind}")
    if kind in ('boolean', 'string', 'enum'):
        return
    numbers = value if kind in ('range', 'point') else [value]
    for number in numbers:
        if spec.get('wholeNumbers') and type(number) is not int:
            raise ValueError(f"{spec['label']}: both bounds must be integers")
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or (isinstance(number, float) and not math.isfinite(number))):
            raise ValueError(f"{spec['label']}: use finite numbers")
        if spec['min'] is not None and number < spec['min']:
            raise ValueError(f"{spec['label']}: minimum is {spec['min']}")
        if spec['max'] is not None and number > spec['max']:
            raise ValueError(f"{spec['label']}: maximum is {spec['max']}")
    if kind == 'range' and value[0] > value[1]:
        raise ValueError(f"{spec['label']}: minimum must not exceed maximum")


def catalog(cfg, profile, editable):
    modules = []

    def add(ident, title, group, scope, path, data, specs, description, apply_notice=None):
        modules.append(dict(id=ident, title=title, group=group, scope=scope,
                            description=description, apply_notice=apply_notice, editable=scope != 'profile' or editable,
                            fields=[dict(s, value=read(data, s['key'], s['default'])) for s in specs],
                            storage_path=path))

    add('tap', 'Tap timing', 'Machine', 'machine', ['tap'], cfg.get('tap', {}), [
        field('jitter_ms', 'Delay before each tap', 'range', [15, 50], 0, 5000, 'ms'),
        field('jitter_px', 'Position jitter', 'integer', 4, 0, 40, 'px'),
        field('max_rate_per_sec', 'Maximum tap rate', 'number', 12, .1, 30, 'taps/sec')],
        'Shared tap timing for every emulator on this machine.')
    inst = cfg.get('active_instance', 'main')
    instance = (cfg.get('instances') or {}).get(inst) or {}
    add('device-preview', 'Video preview', 'Setup', 'instance', ['instances', inst, 'preview'],
        instance.get('preview') or {}, [
            field('backend', 'Preview source', 'enum', PREVIEW_DEFAULTS['backend'], options=[
                {'value': 'auto', 'label': 'Auto'}, {'value': 'mumu', 'label': 'MuMu native'},
                {'value': 'scrcpy', 'label': 'scrcpy'}, {'value': 'adb', 'label': 'ADB screenshots'}]),
            field('fps', 'Target frame rate', 'number', PREVIEW_DEFAULTS['fps'], .5, 30, 'fps'),
            field('mumu.install_dir', 'MuMu installation directory', 'string', PREVIEW_DEFAULTS['mumu']['install_dir'],
                  description='Leave empty to detect the installation automatically.'),
            field('mumu.index', 'MuMu instance index', 'integer', PREVIEW_DEFAULTS['mumu']['index'], -1, 1024,
                  description='Use -1 to detect the instance from its emulator connection.'),
            field('scrcpy.bitrate', 'scrcpy bitrate', 'integer', PREVIEW_DEFAULTS['scrcpy']['bitrate'], 1000000, 40000000, 'bits/sec')],
        'Auto prefers MuMu native, then scrcpy, then ADB screenshots. ADB preview is capped at 4 FPS. Farming capture is independent.',
        apply_notice='Saved changes apply on the next preview connection. No automation restart is needed.')
    overridable = [
        ('loop', 'Observation loop', [field('fps', 'Frames per second', 'number', 2, .1, 30, 'fps'), field('dry_run', 'Log taps without sending them', 'boolean', False)]),
        ('missions', 'Mission visits', [field('visit_delay_sec', 'Delay between mission visits', 'range', [60, 600], 0, 86400, 'sec')]),
        ('fleet', 'Fleet windows', [field('hold_window', 'Hold window before fleet', 'integer', 100, 0, 100000, 'waves'), field('post_window', 'Window after fleet', 'integer', 150, 0, 100000, 'waves')])]
    for ident, title, specs in overridable:
        data = dict(cfg.get(ident) or {})
        data.update(instance.get(ident) or {})
        add(ident, title, 'Emulator', 'instance', ['instances', inst, ident], data, specs,
            'Applies to the selected emulator when its runner next starts.')
    # The same fields, but the machine's OWN values: the fallback every emulator uses unless it carries an override. They are not merged
    # with the selected emulator's override (the form above shows that), so saving one never creates or replaces an override.
    for ident, title, specs in overridable:
        add(ident + '-defaults', title + ' defaults', 'Machine', 'machine', [ident], dict(cfg.get(ident) or {}), specs,
            'Fallback for every emulator on this machine. An emulator that carries its own override keeps it.',
            apply_notice='Applies when a runner next starts; an emulator with its own override is unchanged.')
    choices = preset_choices(cfg)
    add('instance-defaults', 'Emulator defaults', 'Emulator', 'instance', ['instances', inst], instance, [
        field('label', 'Label', 'string', ''),
        field('preset', 'Default preset', 'enum', '', options=[{'value': '', 'label': 'Use machine fallback'}, *choices],
              description='Used when a runner starts without naming a preset. Empty uses the machine fallback.'),
        field('autostart', 'Start at the next tray start', 'boolean', False,
              description='Takes effect when the tray next starts; saving starts nothing now.')],
        'Saved defaults of the selected emulator. Saving changes the file only and starts or stops nothing.')
    add('active-instance', 'Selected emulator', 'Emulator', 'machine', [], cfg, [
        field('active_instance', 'Selected emulator', 'enum', 'main',
              options=[{'value': key, 'label': (body.get('label') if isinstance(body, dict) else None) or key}
                       for key, body in (cfg.get('instances') or {}).items()],
              description='The emulator every page shows, with its account and profile.')],
        'Choosing another emulator changes what every page shows. It starts and stops nothing and keeps every account as saved.')
    add('run-defaults', 'Run defaults', 'Runs', 'machine', [], cfg, [
        field('preset', 'Fallback preset', 'enum', 'normal_run', options=choices,
              description='Used when neither the runner nor the emulator names a preset.')],
        'Machine fallback for starting a run; an emulator\'s own default preset comes first. Saving starts nothing.')
    add('tournament-cards', 'Standing tournament deck changes', 'Equipment', 'machine', ['tourney_card_tweaks'],
        cfg.get('tourney_card_tweaks') or {}, [
            field('drop', 'Cards to drop', 'string-list', [], max_items=TWEAK_LIMIT,
                  description='Card template names taken out of the loaded preset, in this order.'),
            field('add', 'Cards to add', 'string-list', [], max_items=TWEAK_LIMIT,
                  description='Card template names put into the deck after the drops, in this order.')],
        'Applied on top of the loaded preset when a tournament is entered, by card template name (templates/cards/<name>.png). Empty means no changes. Ignored under a global preset.')
    add('logging', 'Logging', 'Machine', 'machine', ['logging'], cfg.get('logging', {}), [
        field('screenshot_on_trigger', 'Save screenshots on triggers', 'boolean', True),
        field('bench_interval_sec', 'Performance log interval', 'number', 30, 1, 86400, 'sec')], 'Shared diagnostic settings.')
    policies = profile.get('policies') or {}
    add('rewards', 'Global rewards', 'Rewards', 'profile', ['policies', 'global_behaviors'], policies.get('global_behaviors') or {}, [
        field(k, label, 'boolean', default) for k, label, default in [
            ('free_store_gems', 'Free Store gems', False), ('daily_missions', 'Daily missions', False),
            ('event_missions', 'Event missions', False), ('guild_progress', 'Guild progress', False)]], 'Shared by runs in this account profile.')
    add('global_abilities', 'Global abilities', 'Abilities', 'profile', ['policies', 'global_behaviors'],
        policies.get('global_behaviors') or {}, [
            field('demon_mode_always', 'Use Demon Mode whenever ready', 'boolean', False)],
        'Overrides the held Demon Mode rescue timing in every run in this profile.')
    for name, gather in sorted((policies.get('gather') or {}).items()):
        path = ['policies', 'gather', name]
        label = gather.get('label') or name.replace('_', ' ')
        affected = [bp.get('label', key) for key, bp in (profile.get('blueprints') or {}).items()
                    if (bp.get('policies') or {}).get('gather') == name]
        description = 'Shared gathering policy. Runs: ' + (', '.join(affected) or 'No runs currently bind this policy')
        add('gather:' + name, 'Gathering · ' + label, 'Gathering', 'profile', path, gather, [
            *[field(k, title, 'boolean', k in ('quests_8h', 'quest_rewards', 'guild')) for k, title in [
                ('quests_8h', 'Eight-hour quests'), ('quest_rewards', 'Quest rewards'),
                ('guild', 'Guild rewards'), ('free_store_gems', 'Free Store gems'), ('guild_store', 'Guild store')]]], description)
        add('ad:' + name, 'Ad Gems · ' + label, 'Ad Gems', 'profile', path, gather, [
            field('ad_gems', 'Collect HUD Ad Gems', 'boolean', True),
            field('gem_delay_sec', 'Gem response delay', 'range', [3, 10], 0, 3600, 'sec',
                  'Random response delay after observing a HUD Ad Gems offer.', whole_numbers=True),
            *[field('ad_gem_options.' + key, spec['label'],
                    'integer' if spec['type'] == 'int' else spec['type'],
                    spec['default'], spec['min'], spec['max'], spec['unit'], spec['description'])
              for key, spec in AD_GEM_FIELDS.items()]], description)
        add('orbit:' + name, 'Orbiting gems · ' + label, 'Orbit', 'profile', path, gather, [
            field('gem_orbit.enabled', 'Enable timed orbit taps', 'boolean', False),
            field('gem_orbit.center', 'Orbit center [x, y]', 'point', [510, 805], 0, 2559, 'px'),
            field('gem_orbit.radius', 'Radius', 'number', 170, 1, 1000, 'px'),
            field('gem_orbit.angle_deg', 'Angle', 'number', 135, -360, 360, 'degrees'),
            *[field('gem_orbit.' + key, spec['label'], 'number', spec['default'],
                    spec['min'], spec['max'], 'sec', spec['description'])
              for key, spec in ORBIT_TIMING_FIELDS.items()],
            field('gem_orbit.taps', 'Taps per set', 'integer', 3, 1, 20),
            field('gem_orbit.jitter_frac', 'Position variation', 'number', .03, 0, .5),
            field('gem_orbit.spread_deg', 'Angle between taps', 'number', 6, 0, 180, 'degrees')], description)
    return modules
