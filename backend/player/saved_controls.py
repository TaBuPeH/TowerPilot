"""Saved run controls, derived from the action catalog (roadmap item 6, batch 2).

An action's saved settings live in a person's profile, and how a number becomes a stored value (a scalar or a `[v, v]` range,
null meaning "inherit", a policy several runs share being copied before it is changed) belongs to the action, not to the code
that walks every action. So each definition names a `SavedAdapter` (the catalog stores only the name, so it stays data), the
adapter is registered here by that name, and the three things that used to name every action - `run_controls.resolve` (the
effective controls of a preset), `run_controls.apply_to_preset` and the saved edit `run_workspace.update` - iterate the
catalog and ask the adapter each definition names. A new action adds a definition and an adapter; none of those three changes.

An adapter offers, for one definition and the routine keys that belong to it (the action id, or `<action>@<instance>`):

    keys(definition, bp)                                  the keys a blueprint carries (default: the action id)
    read(definition, preset, config, automation)          {key: {field: value}}: the effective values
    apply(definition, preset, by_key)                     write `by_key` into a COPY of a compiled preset
    prepare(ctx, definition, by_key, documents)           before any write of one saved edit (take ownership of a shared policy)
    write(ctx, definition, by_key, documents)             the saved edit itself
    finish(ctx)                                           after every write of one saved edit (validate what it changed)

The gates that are not timed actions yet (rewards, abilities, rescue, repeat) stay literal below until their migration.
"""
import copy

from player.gather_settings import ORBIT_TIMING_FIELDS, resolve_ad_gem_options
from towerpilot_workflows.action_contract import DEFAULT_SHOPPING_INTERVAL_SEC, catalog, shopping_interval, split_key
from towerpilot_workflows.control_settings import FIELDS, documents, validate_patch

REWARD_GATHER_KEYS = ('free_store_gems', 'guild_store', 'quests_8h', 'quest_rewards', 'guild')
REWARD_GLOBAL_KEYS = ('free_store_gems', 'daily_missions', 'event_missions', 'guild_progress')
GATE_KINDS = ('coin', 'tournament')      # the blueprint kinds that carry the saved gates
FULL_DRIVER_KINDS = GATE_KINDS           # the kinds whose driver runs the whole battle (live controls and per-battle history exist for them)
GATES = ('rewards', 'abilities', 'rescue', 'repeat')

SAVED = {}       # name -> adapter


def register(name, adapter):
    SAVED[name] = adapter
    return adapter


def adapter_for(definition):
    """The adapter a definition names, or None for an action with no saved settings."""
    if definition.saved is None:
        return None
    try:
        return SAVED[definition.saved]
    except KeyError:
        raise ValueError(f'No saved-control adapter named {definition.saved!r} for {definition.id}') from None


class WriteContext:
    """One saved edit: the profile copy being changed, the run's blueprint in it and what the adapters share."""

    def __init__(self, profile, run, blueprint):
        self.profile, self.run, self.bp = profile, run, blueprint
        self.gather = None


def assign(node: dict, key: str, value) -> None:
    if value is None:
        node.pop(key, None)
    else:
        node[key] = copy.deepcopy(value)


def owned_policy(profile: dict, run: str, section: str, slot: str) -> dict:
    """The run's own copy of a policy, made before its contents change: a policy shared with a sibling run is never edited in
    place. A name already used by an unrelated entry is never overwritten."""
    bp = profile['blueprints'][run]
    table = profile.setdefault('policies', {}).setdefault(section, {})
    ref = bp.get('shopping') if slot == 'shopping' else bp.setdefault('policies', {}).get(slot)
    original = copy.deepcopy(table.get(ref, {}))
    stem = f'{run}_controls_{slot}'
    name, suffix = stem, 2
    while name in table:
        shared = any(other_name != run and
                     (other.get('shopping') if slot == 'shopping' else other.get('policies', {}).get(slot)) == name
                     for other_name, other in profile.get('blueprints', {}).items())
        if name == ref and not shared:
            break
        name, suffix = f'{stem}_{suffix}', suffix + 1
    table[name] = original
    if slot == 'shopping':
        bp['shopping'] = name
    else:
        bp.setdefault('policies', {})[slot] = name
    return table[name]


class SavedAdapter:
    """The defaults: one key (the action id), nothing to prepare, validate or write."""

    def keys(self, definition, bp):
        return (definition.id,)

    def read(self, definition, preset, config, automation):
        raise NotImplementedError

    def apply(self, definition, preset, by_key):
        pass

    def prepare(self, ctx, definition, by_key, docs):
        pass

    def write(self, ctx, definition, by_key, docs):
        raise NotImplementedError

    def finish(self, ctx):
        pass

    def extras(self, definition, profile, bp, preset, config):
        """What a read of the saved run carries beside the settings for this action (a catalog to choose from, a draft conversion)."""
        return {}


class GatherPolicyAdapter(SavedAdapter):
    """Ad gems and floating gems keep their settings in the run's gather policy (a policy several runs may share)."""

    def read(self, definition, preset, config, automation):
        gather = preset.get('gather', {})
        if definition.id == 'ad_gems':
            options = resolve_ad_gem_options(gather.get('ad_gem_options'))
            values = {'enabled': gather.get('ad_gems', True), 'interval_sec': options['confirmed_cooldown_sec'][0],
                      'window_sec': options['attempt_timeout_sec']}
        else:
            orbit = gather.get('gem_orbit') or {}
            values = {'enabled': orbit.get('enabled', False),
                      'interval_sec': orbit.get('interval_sec', ORBIT_TIMING_FIELDS['interval_sec']['default']),
                      'window_sec': orbit.get('attempt_timeout_sec', ORBIT_TIMING_FIELDS['attempt_timeout_sec']['default']),
                      'tap_interval_sec': orbit.get('retry_interval_sec', ORBIT_TIMING_FIELDS['retry_interval_sec']['default'])}
        if definition.id in automation:
            values['enabled'] = automation[definition.id]
        return {definition.id: values}

    def apply(self, definition, preset, by_key):
        gather = preset.setdefault('gather', {})
        values = by_key.get(definition.id, {})
        if definition.id == 'ad_gems':
            if values.get('enabled') is not None:
                gather['ad_gems'] = values['enabled']
            opts = gather.setdefault('ad_gem_options', {})
            if values.get('interval_sec') is not None:
                opts['confirmed_cooldown_sec'] = [values['interval_sec'], values['interval_sec']]
            if values.get('window_sec') is not None:
                opts['attempt_timeout_sec'] = values['window_sec']
        else:
            orbit = gather.setdefault('gem_orbit', {})
            for source, target in (('enabled', 'enabled'), ('interval_sec', 'interval_sec'),
                                   ('window_sec', 'attempt_timeout_sec'), ('tap_interval_sec', 'retry_interval_sec')):
                if values.get(source) is not None:
                    orbit[target] = values[source]

    def prepare(self, ctx, definition, by_key, docs):
        if ctx.gather is None:
            ctx.gather = owned_policy(ctx.profile, ctx.run, 'gather', 'gather')

    def write(self, ctx, definition, by_key, docs):
        values = by_key.get(definition.id, {})
        gather = ctx.gather
        if definition.id == 'ad_gems':
            if 'enabled' in values:
                assign(gather, 'ad_gems', values['enabled'])
            if 'interval_sec' in values:
                value = values['interval_sec']
                assign(gather.setdefault('ad_gem_options', {}), 'confirmed_cooldown_sec', None if value is None else [value, value])
            if 'window_sec' in values:
                assign(gather.setdefault('ad_gem_options', {}), 'attempt_timeout_sec', values['window_sec'])
        else:
            orbit = gather.setdefault('gem_orbit', {})
            for source, target in (('enabled', 'enabled'), ('interval_sec', 'interval_sec'),
                                   ('window_sec', 'attempt_timeout_sec'), ('tap_interval_sec', 'retry_interval_sec')):
                if source in values:
                    assign(orbit, target, values[source])

    def finish(self, ctx):
        if ctx.gather is not None:
            from player import playerprofile
            problems = playerprofile._validate_gather(f'blueprints.{ctx.run}.gather', ctx.gather)
            if problems:
                raise ValueError('; '.join(problems))


class ShoppingPlanAdapter(SavedAdapter):
    """Shopping keeps its settings in the run's shopping list (a policy several runs may share) and its interval on the blueprint."""

    def read(self, definition, preset, config, automation):
        enabled = ((preset['shopping_plan'].get('enabled', True)) if preset.get('shopping_plan') is not None
                   else bool(preset.get('shopping')))
        if definition.id in automation:
            enabled = automation[definition.id] and enabled
        return {definition.id: {'enabled': enabled, 'interval_sec': shopping_interval(preset)}}

    def apply(self, definition, preset, by_key):
        value = by_key.get(definition.id, {}).get('interval_sec')
        if value is not None:
            preset['shop_interval_sec'] = value

    def write(self, ctx, definition, by_key, docs):
        from player import shopping_plan
        if 'shopping_plan' in docs:
            if docs['shopping_plan'] is None:
                raise ValueError('Saved shopping_plan cannot be null; disable shopping to retain its settings')
            normalized = shopping_plan.normalize_plan(docs['shopping_plan'])
            owned_policy(ctx.profile, ctx.run, 'shopping_lists', 'shopping')
            ctx.profile['policies']['shopping_lists'][ctx.bp['shopping']] = normalized
        values = by_key.get(definition.id, {})
        if 'interval_sec' in values:
            value = values['interval_sec']
            assign(ctx.bp, 'shop_interval_sec', value)
            source = ctx.profile.get('policies', {}).get('shopping_lists', {}).get(ctx.bp.get('shopping'))
            if isinstance(source, dict) and source.get('version') == 2:
                owned = owned_policy(ctx.profile, ctx.run, 'shopping_lists', 'shopping')
                assign(owned, 'interval_sec', value)

    def extras(self, definition, profile, bp, preset, config):
        from player import shopping_plan
        extras = {'catalog': shopping_plan.catalog()}
        source = profile.get('policies', {}).get('shopping_lists', {}).get(bp.get('shopping'))
        if source is not None and not (isinstance(source, dict) and source.get('version') == 2):
            directives = source if isinstance(source, list) else source.get('directives', [])
            conversion = shopping_plan.convert_legacy(copy.deepcopy(directives),
                                                       interval_sec=preset.get('shop_interval_sec', DEFAULT_SHOPPING_INTERVAL_SEC))
            if isinstance(source, dict):
                conversion['plan']['enabled'] = source.get('enabled', True)
            extras['legacy_conversion'] = conversion       # offered for review; never installed by a read
        return extras


register('gather_policy', GatherPolicyAdapter())
register('shopping_plan', ShoppingPlanAdapter())


# --------------------------------------------------------------- the generated entry points
def routines_by_definition(routines):
    """The routine keys of a patch grouped under their definition, in catalog order."""
    return {definition.id: {key: values for key, values in routines.items() if split_key(key)[0] == definition.id}
            for definition in catalog()}


def resolve(preset: dict, config: dict) -> dict:
    """The effective saved controls of a compiled preset: every action's adapter, then the gates, then the documents."""
    automation = preset.get('automation', {})
    routines = {}
    for definition in catalog():
        adapter = adapter_for(definition)
        if adapter is not None:
            routines.update(adapter.read(definition, preset, config, automation))
    gather, global_rewards = preset.get('gather', {}), preset.get('global_behaviors', {})
    routines['rewards'] = {'enabled': any(gather.get(key, key in ('quests_8h', 'quest_rewards', 'guild')) for key in REWARD_GATHER_KEYS)
                           or any(global_rewards.get(key, False) for key in REWARD_GLOBAL_KEYS)}
    routines['abilities'] = {'enabled': True}
    routines['rescue'] = {'enabled': True}
    routines['repeat'] = {'enabled': config.get('repeat', True)}
    for routine, enabled in automation.items():
        if routine in GATES:
            routines[routine]['enabled'] = enabled
    settings = {'routines': routines}
    for name in sorted(documents()):
        settings[name] = copy.deepcopy(preset.get(name))
    return settings


def frozen_documents(preset: dict) -> dict:
    """The documents a preset carries, as the frozen controls of a battle hold them (those it does not carry are left out)."""
    return {name: copy.deepcopy(preset[name]) for name in sorted(documents()) if preset.get(name) is not None}


def apply_to_preset(preset: dict, settings: dict) -> dict:
    """Materialize supported aliases in a copy; runtime gates own enablement.

    In particular, disabling shopping must not erase the frozen plan/counters.
    Only driver gates interpret abilities/rescue/repeat.
    """
    validate_patch(settings)
    out = copy.deepcopy(preset)
    routines = settings.get('routines', {})
    for name in sorted(documents()):
        if settings.get(name) is not None:
            out[name] = copy.deepcopy(settings[name])
    grouped = routines_by_definition(routines)
    for definition in catalog():
        adapter = adapter_for(definition)
        if adapter is not None:
            adapter.apply(definition, out, grouped[definition.id])
    if routines.get('rewards', {}).get('enabled') is False:
        gather = out.setdefault('gather', {})
        for key in REWARD_GATHER_KEYS:
            gather[key] = False
        global_rewards = out.setdefault('global_behaviors', {})
        for key in REWARD_GLOBAL_KEYS:
            global_rewards[key] = False
    return out


def capabilities(bp: dict) -> dict:
    """The saved controls a run of this blueprint carries: the routine fields of every action that is saved for its kind (in
    catalog order), the gates for the kinds that carry them, then the documents of those actions."""
    kind = bp.get('kind')
    controls, documents_ = [], []
    for definition in catalog():
        if not definition.saved_for(kind):
            continue
        for key in adapter_for(definition).keys(definition, bp):
            controls += [f'routines.{key}.{f.path}' for f in definition.fields if f.saved]
        documents_ += list(definition.documents)
    if kind in GATE_KINDS:
        controls += [f'routines.{gate}.{field}' for gate in GATES for field in FIELDS[gate]]
    return {'controls': controls + documents_, 'actions': []}


def read_extras(profile: dict, bp: dict, preset: dict, config: dict) -> dict:
    """What every adapter adds to a read of a saved run, in catalog order."""
    extras = {}
    for definition in catalog():
        adapter = adapter_for(definition)
        if adapter is not None:
            extras.update(adapter.extras(definition, profile, bp, preset, config))
    return extras


def write(ctx: WriteContext, changes: dict) -> None:
    """One saved edit: every action's adapter takes ownership of what it will change, the gates' `enabled` is stored on the
    blueprint, each adapter writes, and each adapter validates what the edit changed."""
    routines = changes.get('routines', {})
    grouped = routines_by_definition(routines)
    steps = []
    for definition in catalog():
        adapter = adapter_for(definition)
        by_key = grouped[definition.id]
        mine = {name: changes[name] for name in definition.documents if name in changes}
        if adapter is not None and (by_key or mine):
            steps.append((definition, adapter, by_key, mine))
    for definition, adapter, by_key, mine in steps:
        adapter.prepare(ctx, definition, by_key, mine)
    for routine, values in routines.items():
        if 'enabled' in values:
            assign(ctx.bp.setdefault('automation', {}), routine, values['enabled'])
    for definition, adapter, by_key, mine in steps:
        adapter.write(ctx, definition, by_key, mine)
    finished = []
    for _, adapter, _, _ in steps:
        if all(adapter is not seen for seen in finished):
            finished.append(adapter)
            adapter.finish(ctx)
