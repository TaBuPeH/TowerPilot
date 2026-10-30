"""Golden traces of the control dispatch, Run now acknowledgements and saved-setting transforms (roadmap item 6, batch 2).

Scenario drivers run TODAY's BattleSession over the real GemWatch collectors and the real Shopper under a fake clock, and
`run_controls` / `run_workspace` over fixed presets and profiles, and record every observable result: which collector or
shopper state a control changes, the acknowledgements of a control command, the states and evidence of a Run now command,
the capability lists, the published `runtime.routines` / `runtime.actions`, and the saved-setting reads, applications and
edits. The traces in fixtures/action_dispatch_golden_v1.json were captured on the unchanged tree (batch 1 as landed, plus
r12f), before the dispatch was derived from the catalog. The parity tests replay the same scenarios and compare, so the
migration cannot change what a control, a Run now or a saved edit does.

This module imports none of the modules batch 2 adds (action_bindings, saved_controls). It drives the callers only through
the attributes they already had. Regeneration is an explicit developer command, never a test side effect:

    TP_REGENERATE_ACTION_DISPATCH_GOLDEN=1 python -m pytest backend/tests/unit/test_action_dispatch_capture.py
"""

import paths as test_paths
from fixture_json import write_fixture_json
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import action_golden as golden
from action_golden import clean

GOLDEN_PATH = test_paths.FIXTURES / 'action_dispatch_golden_v1.json'
REGENERATE = 'TP_REGENERATE_ACTION_DISPATCH_GOLDEN'
REPO = test_paths.REPO
SOURCES = ('backend/runtime/battle_session.py', 'backend/player/run_controls.py', 'backend/control/run_workspace.py',
           'backend/towerpilot_workflows/control_settings.py', 'backend/towerpilot_workflows/action_contract.py',
           'backend/runtime/action_session.py')

# The controls a host may publish, as literal data (never read from the code under test): path -> 'bool' or (min, max).
CONTROLS = {
    'routines.ad_gems.enabled': 'bool', 'routines.ad_gems.interval_sec': (1, 3600), 'routines.ad_gems.window_sec': (5, 60),
    'routines.floating_gems.enabled': 'bool', 'routines.floating_gems.interval_sec': (1, 3600),
    'routines.floating_gems.window_sec': (5, 60), 'routines.floating_gems.tap_interval_sec': (0.5, 30),
    'routines.shopping.enabled': 'bool', 'routines.shopping.interval_sec': (1, 86400),
    'routines.rewards.enabled': 'bool', 'routines.abilities.enabled': 'bool', 'routines.rescue.enabled': 'bool',
    'routines.repeat.enabled': 'bool',
}
HOSTS = ('full_plan', 'full_no_plan', 'gems_only')


def source_hashes():
    return {rel: hashlib.sha256((REPO / rel).read_bytes()).hexdigest().upper() for rel in SOURCES if (REPO / rel).exists()}


def load():
    return json.loads(GOLDEN_PATH.read_text(encoding='utf-8'))


def roundtrip(value):
    return json.loads(json.dumps(value))


def write(document):
    write_fixture_json(GOLDEN_PATH, document)


def digest(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True).encode()).hexdigest()[:16].upper()


def guarded(call):
    """A raised refusal is part of the trace."""
    try:
        return call()
    except Exception as exc:                  # noqa: BLE001
        return ['raised', type(exc).__name__, str(exc)]


# ------------------------------------------------------------------ a driver host
def plan_v2(**changes):
    from player.shopping_plan import normalize_plan
    plan = dict(version=2, cadence='while_affordable', interval_sec=90, cash_reserve=50, queue_mode='highest_affordable',
                queue=[{'id': 'health'}, {'id': 'damage'}])
    plan.update(changes)
    return normalize_plan(plan)


def build_host(tmp_path, tag, shape='full_plan', *, plan=None, gather=None):
    """The real BattleSession over the real collectors, the way tests/test_battle_session.py builds one. Must be called
    inside a patched clock (gem_world / shopping_world)."""
    from flows.shard import GemWatch
    from interactions.shopper import Shopper
    from runtime.battle_session import BattleSession
    from towerpilot_workflows.battles import Battles
    from towerpilot_workflows.operations import Operations
    path = Path(tmp_path) / f'{tag}.db'
    context = dict(device_key='device', account='account', instance='main', serial='fake')
    operation, _ = Operations(path).accept('farm', context, {})
    body = dict(gather=copy.deepcopy(gather) if gather is not None else {'ad_gems': True, 'gem_orbit': {'enabled': True}},
                shopping=[], shop_interval_sec=90)
    if shape == 'full_plan':
        body['shopping_plan'] = plan if plan is not None else plan_v2()
    session = BattleSession(Battles(path), operation['id'], body, {})
    gems = GemWatch(body['gather'])
    if shape == 'gems_only':
        rs = SimpleNamespace(gems=gems, tracker=SimpleNamespace(last=123), mission=None, shop=None)
    else:
        rs = SimpleNamespace(gems=gems, shop=Shopper(body), mission=SimpleNamespace(active=False), shopping_enabled=True,
                             rewards_enabled=True, repeat_enabled=True, tracker=SimpleNamespace(last=123), last_shop=0,
                             cl_normalized=True, uw_normalized=True, uw_done={'x'})
    return session, rs, operation


def seeded(world, rs):
    """The ad collector draws its cadence from a seeded source whose draws the world records."""
    rs.gems.ad_gems.rng = world.rng
    return rs


def host_state(session, rs):
    state = dict(ad=golden.ad_state(rs.gems.ad_gems), orbit=golden.orbit_state(rs.gems.orbit), gates=dict(session.gates),
                 effective=dict(shop_interval_sec=session.effective.get('shop_interval_sec'), gather=session.effective.get('gather'),
                                shopping_plan=None if session.effective.get('shopping_plan') is None else dict(
                                    enabled=session.effective['shopping_plan'].get('enabled'),
                                    interval_sec=session.effective['shopping_plan'].get('interval_sec'),
                                    queue_digest=digest(session.effective['shopping_plan'].get('queue')))))
    if getattr(rs, 'shop', None) is not None:
        shop = rs.shop
        state['shop'] = dict(active=shop.active, manual=shop._manual, manual_outcome=shop._manual_outcome,
                             command=shop.manual_command_id, plan=None if shop.plan is None else dict(
                                 enabled=shop.plan.get('enabled'), interval=shop.plan.get('interval_sec'), queue=shop.plan.get('queue')))
    state['flags'] = {name: getattr(rs, name, None) for name in
                      ('shopping_enabled', 'rewards_enabled', 'repeat_enabled', 'cl_normalized', 'uw_normalized')}
    state['uw_done'] = sorted(getattr(rs, 'uw_done', ()) or ())
    return state


def command_view(command):
    return {key: command.get(key) for key in ('kind', 'payload', 'state', 'applied_paths', 'reason', 'evidence', 'revision', 'request_keys')}


def leaves(value, prefix=''):
    out = {}
    for key, item in value.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(item, dict) and path != 'shopping_plan':
            out.update(leaves(item, path))
        else:
            out[path] = item
    return out


def delta(current, base):
    """The control paths whose value differs from the frozen envelope's (the whole tree is large and constant)."""
    now, then = leaves(current), leaves(base)
    changed = {path: now[path] for path in sorted(now) if path not in then or now[path] != then[path]}
    changed.update({path: '<removed>' for path in sorted(then) if path not in now})
    return changed


def battle_view(session, *, controls=True):
    battle = session.store.get(session.battle['id'])
    out = dict(commands=[command_view(c) for c in session.store.commands(battle['id'])])
    if controls:
        base = battle['envelope']['controls']
        out.update(control_revision=battle['control_revision'], applied_revision=battle['applied_revision'],
                   overrides=battle['overrides'], applied_overrides=battle['applied_overrides'],
                   requested_delta=delta(battle['requested_controls'], base), applied_delta=delta(battle['applied_controls'], base))
    return out


def view(session, rs, world, *, controls=True):
    events = world.drain()[0]
    return clean(dict(battle=battle_view(session, controls=controls), host=host_state(session, rs), events=events))


def recorder(steps, make_view):
    """A step is written in full when a command changed or a command was acknowledged; every other step is its digest (equal
    digests mean equal full states), which keeps the fixture readable and small without weakening the comparison."""
    last = [None]

    def rec(label):
        snapshot = make_view()
        commands = digest(snapshot['battle']['commands'])
        acknowledged = any(event[0] == 'battle_command_acknowledged' for event in snapshot['events'])
        if commands != last[0] or acknowledged:
            steps.append([label, snapshot])
        else:
            steps.append([label, digest(snapshot)])
        last[0] = commands
    return rec


def request_controls(session, operation, patch, key):
    battle = session.store.get(session.battle['id'])
    return session.store.request_controls(battle['id'], operation['id'], session.generation, battle['control_revision'], patch, key)


def request_action(session, operation, action, key):
    battle = session.store.get(session.battle['id'])
    return session.store.request_action(battle['id'], operation['id'], session.generation, action, key)


# --------------------------------------------------------- capabilities and the validator
def capability_lists(tmp_path):
    out = {}
    world = golden.World(seed=1)
    with golden.gem_world(world):
        for shape in HOSTS:
            session, rs, _ = build_host(tmp_path, f'cap-{shape}', shape)
            out[shape] = session.capabilities(rs)
        session, rs, _ = build_host(tmp_path, 'cap-queue', 'full_plan', plan=plan_v2(queue=[]))
        out['queue_empty'] = session.capabilities(rs)
    return clean(out)


def validator_cases():
    from player.run_controls import validate_patch, flatten
    values = [True, False, None, 0, 1, 0.5, 5, 60, 61, 3600, 3601, -1, 1e9, float('nan'), float('inf'), '5', [], {}, 86400, 86401, 30]
    cases = []
    for path in CONTROLS:
        _, routine, field = path.split('.')
        for value in values:
            patch = {'routines': {routine: {field: value}}}
            cases.append([path, repr(value), guarded(lambda: ['ok', validate_patch(patch)])])
    cases += [
        ['unknown routine', 'x', guarded(lambda: validate_patch({'routines': {'nothing': {'enabled': True}}}))],
        ['unknown field', 'x', guarded(lambda: validate_patch({'routines': {'ad_gems': {'nothing': 1}}}))],
        ['unknown top', 'x', guarded(lambda: validate_patch({'other': {}}))],
        ['empty', 'x', guarded(lambda: validate_patch({}))],
        ['empty routines', 'x', guarded(lambda: validate_patch({'routines': {}}))],
        ['not a dict', 'x', guarded(lambda: validate_patch([]))],
        ['routines not a dict', 'x', guarded(lambda: validate_patch({'routines': []}))],
        ['values not a dict', 'x', guarded(lambda: validate_patch({'routines': {'ad_gems': 5}}))],
        ['two routines', 'x', guarded(lambda: validate_patch({'routines': {'ad_gems': {'enabled': False}, 'shopping': {'interval_sec': 30}}}))],
        ['flatten', 'x', flatten({'routines': {'ad_gems': {'enabled': False}}, 'shopping_plan': {'queue': [1]}})],
    ]
    return clean(cases)


# ------------------------------------------------------------ the control matrix
def values_of(spec):
    if spec == 'bool':
        return [True, False, None]
    low, high = spec
    return [low, 7 if low <= 7 <= high else (low + high) / 2, high, None]


def one_control(tmp_path, tag, shape, path, value):
    world = golden.World(seed=2)
    with golden.gem_world(world):
        session, rs, operation = build_host(tmp_path, tag, shape)
        seeded(world, rs)
        session.observe(rs, 123)
        world.drain()
        _, routine, field = path.split('.')
        command = guarded(lambda: command_view(request_controls(session, operation, {'routines': {routine: {field: value}}}, 'ctl-1')))
        before = view(session, rs, world)
        world.now += 2
        outcome = guarded(lambda: session.checkpoint(rs, battle_visible=True))
        return clean(dict(admitted=command, before=before, checkpoint=outcome, after=view(session, rs, world)))


def control_matrix(tmp_path):
    out = {}
    for shape in HOSTS:
        world = golden.World(seed=1)
        with golden.gem_world(world):
            session, rs, _ = build_host(tmp_path, f'list-{shape}', shape)
            session.observe(rs, 123)
            out[f'{shape}|published'] = [path for path in session.capabilities(rs)['controls'] if path in CONTROLS]
        for path in sorted(CONTROLS):
            for index, value in enumerate(values_of(CONTROLS[path])):
                out[f'{shape}|{path}|{index}'] = one_control(tmp_path, f'm-{shape}-{len(out)}', shape, path, value)
    return out


def multi_control(tmp_path):
    """Several paths in one command, the rewards flow owning the screen, then released; the abilities gate; a later
    command; a command checkpointed while the battle is not visible."""
    world = golden.World(seed=3)
    with golden.gem_world(world):
        session, rs, operation = build_host(tmp_path, 'multi')
        seeded(world, rs)
        session.observe(rs, 123)
        rs.mission.active = True
        patch = {'routines': {'ad_gems': {'enabled': False, 'interval_sec': 45}, 'rewards': {'enabled': False},
                              'shopping': {'enabled': False, 'interval_sec': 33}, 'abilities': {'enabled': True}}}
        request_controls(session, operation, patch, 'multi-1')
        world.drain()
        steps = []
        for label, mission in (('mission_owns_the_screen', True), ('mission_released', False)):
            rs.mission.active = mission
            world.now += 2
            session.checkpoint(rs, battle_visible=True)
            steps.append([label, view(session, rs, world)])
        request_controls(session, operation, {'routines': {'repeat': {'enabled': False}, 'rescue': {'enabled': False},
                                                           'floating_gems': {'tap_interval_sec': 3, 'window_sec': 11}}}, 'multi-2')
        world.now += 2
        session.checkpoint(rs, battle_visible=False)
        steps.append(['second_command_not_visible', view(session, rs, world)])
        world.now += 2
        session.checkpoint(rs, battle_visible=True)
        steps.append(['second_command_visible', view(session, rs, world)])
    return clean(steps)


def plan_controls(tmp_path):
    """A shopping_plan edit alone, after an interval override, with the shopping gate off, and on a frozen-disabled plan."""
    out = {}
    for label, plan_changes, previous, off in (
            ('plan_only', dict(queue=[{'id': 'damage'}]), None, False),
            ('plan_after_interval_override', dict(queue=[{'id': 'damage'}], cash_reserve=7), {'shopping': {'interval_sec': 44}}, False),
            ('plan_while_gate_off', dict(queue=[{'id': 'health'}]), {'shopping': {'enabled': False}}, False),
            ('plan_frozen_disabled', dict(queue=[{'id': 'damage'}], enabled=True), None, True)):
        world = golden.World(seed=4)
        with golden.gem_world(world):
            plan = plan_v2(enabled=False) if off else plan_v2()
            session, rs, operation = build_host(tmp_path, f'plan-{label}', plan=plan)
            seeded(world, rs)
            rs.shopping_enabled = not off
            session.observe(rs, 123)
            world.drain()
            steps = []
            if previous:
                request_controls(session, operation, {'routines': previous}, 'prev')
                world.now += 2
                session.checkpoint(rs, battle_visible=True)
                steps.append(['previous', view(session, rs, world)])
            request_controls(session, operation, {'shopping_plan': plan_v2(**plan_changes)}, 'plan')
            world.now += 2
            session.checkpoint(rs, battle_visible=True)
            steps.append(['plan', view(session, rs, world)])
            out[label] = steps
    return clean(out)

# --------------------------------------------------------------------- Run now
def run_now_gem(tmp_path, action, *, offer, gem=True, hit=False, disabled=False, window_open=False, window=None):
    """A Run now on a collector: refused while the battle is not visible and while the rewards flow owns the screen, accepted
    at a checkpoint, started when the window opens, completed when it ends. `window_open` starts the collector's own window
    first, so the request is refused by the collector."""
    world = golden.World(seed=5)
    steps = []
    with golden.gem_world(world):
        session, rs, operation = build_host(tmp_path, f'now-{action}-{offer}-{gem}-{hit}-{disabled}-{window_open}-{window}', 'full_plan')
        seeded(world, rs)
        session.observe(rs, 123)
        world.gem_visible, world.offer = gem, False
        if window is not None:               # a short window, so that a request nobody can confirm runs out within the trace
            request_controls(session, operation, {'routines': {action: {'window_sec': window}}}, 'window')
            world.now += 2
            session.checkpoint(rs, battle_visible=True)
        if hit:
            world.hit_next_tap = True
        if disabled:
            request_controls(session, operation, {'routines': {action: {'enabled': False}}}, 'off')
            world.now += 2
            session.checkpoint(rs, battle_visible=True)
        collector = rs.gems.ad_gems if action == 'ad_gems' else rs.gems.orbit
        if window_open:
            collector.request_now()
            world.now += 1
            world.offer = offer
            rs.gems.poll(world.frame())
        world.drain()
        rec = recorder(steps, lambda: view(session, rs, world, controls=False))
        request_action(session, operation, action, 'once-1')
        rec('requested')
        world.now += 1
        session.checkpoint(rs, battle_visible=False)
        rec('not_visible')
        rs.mission.active = True
        world.now += 1
        session.checkpoint(rs, battle_visible=True)
        rec('mission_active')
        rs.mission.active = False
        world.now += 1
        session.checkpoint(rs, battle_visible=True)
        rec('accepted_or_refused')
        world.offer = offer
        for index in range(60):
            world.now += 1.0
            rs.gems.poll(world.frame())
            session.checkpoint(rs, battle_visible=True)
            rec(f'tick-{index}')
        steps.append(['second_request', clean(guarded(lambda: command_view(request_action(
            session, operation, 'ad_gems' if action != 'ad_gems' else 'floating_gems', 'second-1'))))])
        world.now += 1
        session.checkpoint(rs, battle_visible=True)
        rec('second_action')
    return clean(steps)


def run_now_cases(tmp_path):
    return {
        'ad_offer_confirmed': run_now_gem(tmp_path, 'ad_gems', offer=True),
        'ad_no_offer': run_now_gem(tmp_path, 'ad_gems', offer=False),
        'ad_no_offer_short_window': run_now_gem(tmp_path, 'ad_gems', offer=False, window=5),
        'ad_disabled_offer': run_now_gem(tmp_path, 'ad_gems', offer=True, disabled=True),
        'ad_window_already_open': run_now_gem(tmp_path, 'ad_gems', offer=True, window_open=True),
        'orbit_gem_visible': run_now_gem(tmp_path, 'floating_gems', offer=False, gem=True, hit=True),
        'orbit_no_gem': run_now_gem(tmp_path, 'floating_gems', offer=False, gem=False),
        'orbit_no_gem_short_window': run_now_gem(tmp_path, 'floating_gems', offer=False, gem=False, window=5),
        'orbit_gem_visible_never_confirmed_short_window': run_now_gem(tmp_path, 'floating_gems', offer=False, gem=True, window=5),
        'orbit_disabled': run_now_gem(tmp_path, 'floating_gems', offer=False, gem=True, disabled=True),
        'orbit_window_already_open': run_now_gem(tmp_path, 'floating_gems', offer=False, gem=True, window_open=True),
    }


def run_now_shopping(tmp_path, tag, action, *, sweep_active=False, steps_after=120):
    """A Run now on the shopper: accepted at a checkpoint (the sweep starts and the command is started at once), completed
    when the sweep ends; held while the battle is not visible or the rewards flow owns the screen; refused while a sweep runs."""
    from runtime import logger
    clock, events = [100.0], []
    steps = []
    with golden.shopping_world(clock), mock.patch.object(logger, 'event', lambda name, **fields: events.append([name, clean(fields)])):
        session, rs, operation = build_host(tmp_path, f'now-shop-{tag}', 'full_plan')
        session.observe(rs, 123)
        if sweep_active:
            rs.shop.start()
        events.clear()

        def shopping_view():
            snapshot = clean(dict(battle=battle_view(session, controls=False), host=host_state(session, rs), events=list(events)))
            events.clear()
            return snapshot
        rec = recorder(steps, shopping_view)
        steps.append(['request', clean(guarded(lambda: command_view(request_action(session, operation, action, 'shop-1'))))])
        rec('requested')
        clock[0] += 1
        session.checkpoint(rs, battle_visible=False)
        rec('not_visible')
        rs.mission.active = True
        clock[0] += 1
        session.checkpoint(rs, battle_visible=True)
        rec('mission_active')
        rs.mission.active = False
        clock[0] += 1
        session.checkpoint(rs, battle_visible=True)
        rec('after_checkpoint')
        for index in range(steps_after):
            clock[0] += 0.1
            rs.shop.step(None, wave=20, after_intro_sprint=True)
            session.checkpoint(rs, battle_visible=True)
            rec(f'step-{index}')
        steps.append(['status', clean(rs.shop.status())])
    return clean(steps)


def run_now_shopping_cases(tmp_path):
    return {label: run_now_shopping(tmp_path, label, action, **kwargs) for label, action, kwargs in (
        ('whole', 'shopping', {}), ('section', 'shopping:attack', {}), ('upgrade', 'shopping:attack:damage', {}),
        ('utility_upgrade', 'shopping:utility:cash_bonus', {}), ('defense_upgrade', 'shopping:defense:health', {}),
        ('not_in_the_catalog', 'shopping:utility:health', {}), ('sweep_active', 'shopping', dict(sweep_active=True)))}


# ------------------------------------------------------------------- publication
def published(tmp_path):
    """`runtime.routines`, the collection evidence and `runtime.actions` after each of a set of host states."""
    out = {}
    world = golden.World(seed=6)
    with golden.gem_world(world):
        from runtime.action_session import ActionSession
        session, rs, operation = build_host(tmp_path, 'publish')
        seeded(world, rs)
        ActionSession.for_battle(session, 'orchestrator')
        session.observe(rs, 123)

        def publish(label, visible=True):
            world.now += 2
            session.publish(rs, battle_visible=visible)
            runtime = session.store.get(session.battle['id'])['runtime']
            out[label] = clean({key: runtime.get(key) for key in ('routines', 'collection_evidence', 'actions', 'observed_wave',
                                                                  'observation_status', 'driver_generation', 'observed_duration_sec')})
        publish('idle')
        publish('not_visible', visible=False)
        rs.gems.ad_gems.window_started_at, rs.gems.ad_gems.window_deadline = world.now - 3, world.now + 20
        publish('ad_window_open')
        rs.gems.ad_gems.window_deadline = None
        rs.shopping_enabled = False
        publish('shopping_off')
        rs.shopping_enabled = True
        rs.shop._continuation_ready = True
        publish('shopping_continuation_ready')
        rs.shop._continuation_ready = False
        rs.mission.active = True
        publish('rewards_active')
        rs.mission.active = False
        rs.rewards_enabled = False
        rs.repeat_enabled = False
        session.gates['abilities'] = False
        publish('gates_off')
        rs.gems.orbit.attempt_started, rs.gems.orbit.window_ends_at = world.now - 2, world.now + 9
        publish('orbit_attempt_open')
        rs.gems.orbit.attempt_started = None
        rs.gems.orbit.enabled = False
        rs.gems.ad_gems.enabled = False
        publish('gems_disabled')
        rs.gems.ad_gems.manual_requested = True
        publish('ad_manual_pending')
    world = golden.World(seed=6)
    with golden.gem_world(world):
        session, rs, operation = build_host(tmp_path, 'publish-gems-only', 'gems_only')
        seeded(world, rs)
        session.observe(rs, 123)
        world.now += 2
        session.publish(rs, battle_visible=True)
        runtime = session.store.get(session.battle['id'])['runtime']
        out['gems_only'] = clean({key: runtime.get(key) for key in ('routines', 'collection_evidence', 'observed_wave')})
    return out


# ------------------------------------------------------------- the saved settings
PRESETS = {
    'empty': {},
    'gather_values': {'gather': {'ad_gems': False, 'ad_gem_options': {'confirmed_cooldown_sec': [30, 60], 'attempt_timeout_sec': 20},
                                  'gem_orbit': {'enabled': True, 'interval_sec': 45, 'attempt_timeout_sec': 15, 'retry_interval_sec': 2}},
                      'shopping': [], 'shop_interval_sec': 60},
    'plan_and_automation': {'shopping_plan': {'version': 2, 'enabled': False, 'interval_sec': 33, 'cadence': 'single_attempt', 'queue': []},
                            'automation': {'shopping': True, 'rewards': False, 'ad_gems': False, 'floating_gems': True}},
    'legacy_shopping': {'shopping': [{'tab': 'attack', 'stats': ['damage'], 'mode': 'repeat'}], 'shop_interval_sec': 120,
                        'global_behaviors': {'free_store_gems': True}, 'gather': {'quests_8h': False, 'guild': True}},
    'global_rewards_only': {'gather': {'quests_8h': False, 'quest_rewards': False, 'guild': False}, 'global_behaviors': {'event_missions': True}},
    'automation_unknown': {'automation': {'xyz': False, 'repeat': False, 'abilities': False, 'rescue': False, 'shopping': False}},
    'plan_default_enabled': {'shopping_plan': {'version': 2, 'cadence': 'single_attempt', 'queue': []}, 'automation': {'shopping': False}},
    'plan_interval_and_flat': {'shopping_plan': {'version': 2, 'interval_sec': 75, 'cadence': 'single_attempt', 'queue': []}, 'shop_interval_sec': 15},
    'orbit_partial': {'gather': {'gem_orbit': {'enabled': True}, 'ad_gem_options': {'confirmed_cooldown_sec': [10, 10]}}},
}
SETTINGS = {
    'ad_interval_window_enabled': {'routines': {'ad_gems': {'enabled': True, 'interval_sec': 90, 'window_sec': 25}}},
    'ad_null_interval': {'routines': {'ad_gems': {'interval_sec': None, 'enabled': False}}},
    'orbit_all': {'routines': {'floating_gems': {'enabled': True, 'interval_sec': 50, 'window_sec': 12, 'tap_interval_sec': 1.5}}},
    'orbit_null': {'routines': {'floating_gems': {'enabled': None, 'interval_sec': None, 'window_sec': None, 'tap_interval_sec': None}}},
    'shopping_interval': {'routines': {'shopping': {'interval_sec': 45, 'enabled': False}}},
    'rewards_off': {'routines': {'rewards': {'enabled': False}}},
    'rewards_on': {'routines': {'rewards': {'enabled': True}}},
    'plan': {'shopping_plan': {'version': 2, 'enabled': True, 'interval_sec': 12, 'cadence': 'single_attempt', 'queue': [{'id': 'damage'}]}},
    'gates': {'routines': {'abilities': {'enabled': False}, 'rescue': {'enabled': False}, 'repeat': {'enabled': False}}},
    'invalid_range': {'routines': {'ad_gems': {'window_sec': 500}}},
    'invalid_type': {'routines': {'shopping': {'enabled': 1}}},
    'empty': {},
}


def resolve_cases():
    from player.run_controls import resolve
    out = {}
    for name, preset in PRESETS.items():
        for config_name, config in (('default', {}), ('no_repeat', {'repeat': False}), ('repeat', {'repeat': True})):
            source = copy.deepcopy(preset)
            result = guarded(lambda: resolve(source, config))
            out[f'{name}|{config_name}'] = dict(result=result, preset_unchanged=source == preset)
    return clean(out)


def apply_cases():
    from player.run_controls import apply_to_preset
    out = {}
    for name in ('empty', 'gather_values', 'plan_and_automation', 'legacy_shopping'):
        for setting, patch in SETTINGS.items():
            source = copy.deepcopy(PRESETS[name])
            result = guarded(lambda: apply_to_preset(source, copy.deepcopy(patch)))
            out[f'{name}|{setting}'] = dict(result=result, preset_unchanged=source == PRESETS[name])
    return clean(out)


def merge_cases():
    from player.run_controls import merge_patch, resolve
    base = resolve(PRESETS['gather_values'], {})
    return clean({name: guarded(lambda: merge_patch(base, patch)) for name, patch in SETTINGS.items()})


def profile_fixture(variant='shared'):
    profile = {'player': {}, 'blueprints': {
        'farm': {'kind': 'coin', 'loadout': 'as_is', 'tier': 1, 'policies': {'gather': 'shared'}, 'shopping': 'shared'},
        'other': {'kind': 'coin', 'loadout': 'as_is', 'tier': 1, 'policies': {'gather': 'shared'}, 'shopping': 'shared'},
        'cup': {'kind': 'tournament', 'tier': 1, 'policies': {'gather': 'shared'}, 'shopping': 'shared'},
        'shards': {'kind': 'shard', 'policies': {'gather': 'shared'}}},
        'policies': {'gather': {'shared': {'ad_gems': True, 'gem_delay_sec': [3, 10], 'quests_8h': True,
                                           'ad_gem_options': {'poll_sec': [2, 4], 'edge_inset_px': 8},
                                           'gem_orbit': {'enabled': True, 'radius': 5, 'taps': 4}}},
                     'shopping_lists': {'shared': {'enabled': True, 'directives': [{'tab': 'attack', 'stats': ['damage'], 'mode': 'repeat'}]}},
                     'global_behaviors': {'free_store_gems': True}}}
    if variant == 'plan':
        profile['policies']['shopping_lists']['shared'] = plan_v2()
        profile['blueprints']['farm']['automation'] = {'shopping': True, 'ad_gems': False}
    if variant == 'owned':
        profile['policies']['gather']['farm_controls_gather'] = copy.deepcopy(profile['policies']['gather']['shared'])
        profile['blueprints']['farm']['policies']['gather'] = 'farm_controls_gather'
    return profile


WORKSPACE_PATCHES = {
    'ad_numbers': {'routines.ad_gems.interval_sec': 60, 'routines.ad_gems.window_sec': 20},
    'ad_enabled_off': {'routines.ad_gems.enabled': False},
    'ad_enabled_null': {'routines.ad_gems.enabled': None, 'routines.ad_gems.interval_sec': None},
    'orbit_all': {'routines.floating_gems.enabled': False, 'routines.floating_gems.interval_sec': 40, 'routines.floating_gems.window_sec': 9,
                  'routines.floating_gems.tap_interval_sec': 3},
    'orbit_null': {'routines.floating_gems.interval_sec': None, 'routines.floating_gems.tap_interval_sec': None},
    'shopping_interval': {'routines.shopping.interval_sec': 45.5},
    'shopping_enabled': {'routines.shopping.enabled': False},
    'shopping_interval_null': {'routines.shopping.interval_sec': None},
    'plan': {'shopping_plan': plan_v2(queue=[{'id': 'damage'}])},
    'plan_null': {'shopping_plan': None},
    'gates': {'routines.rewards.enabled': False, 'routines.repeat.enabled': False, 'routines.rescue.enabled': True, 'routines.abilities.enabled': False},
    'nested': {'routines': {'ad_gems': {'window_sec': 33}, 'shopping': {'interval_sec': 70}}},
    'mixed_everything': {'routines.ad_gems.interval_sec': 20, 'routines.floating_gems.window_sec': 30, 'routines.shopping.interval_sec': 99,
                         'routines.rewards.enabled': False, 'shopping_plan': plan_v2(queue=[{'id': 'health'}], enabled=False)},
    'invalid_range': {'routines.ad_gems.window_sec': 500},
    'invalid_unknown_control': {'routines.nothing.enabled': True},
    'invalid_dotted': {'routines.ad_gems': True},
    'not_a_dict': [],
    'empty': {},
}


def slim(result):
    """A workspace answer with its (large, constant) shopping catalog replaced by a digest."""
    if isinstance(result, dict) and 'catalog' in result:
        return dict(result, catalog=digest(result['catalog']))
    return result


def workspace_cases():
    from control import run_workspace
    out = {}
    for variant in ('shared', 'plan', 'owned'):
        for run in ('farm', 'cup', 'shards', 'missing'):
            source = profile_fixture(variant)
            original = copy.deepcopy(source)
            out[f'read|{variant}|{run}'] = dict(result=slim(guarded(lambda: run_workspace.read(source, {'repeat': False}, run))),
                                                unchanged=source == original)
    for variant in ('shared', 'plan', 'owned'):
        for run in ('farm', 'cup', 'shards'):
            for name, patch in WORKSPACE_PATCHES.items():
                source = profile_fixture(variant)
                original = copy.deepcopy(source)
                result = slim(guarded(lambda: run_workspace.update(source, {}, run, copy.deepcopy(patch))))
                out[f'update|{variant}|{run}|{name}'] = dict(result=result, unchanged=source == original)
    first = run_workspace.update(profile_fixture('shared'), {}, 'farm', {'routines.ad_gems.interval_sec': 61})
    out['sibling|other_run_still_reads_the_shared_policy'] = slim(guarded(lambda: run_workspace.read(first['profile'], {}, 'other')))
    out['sibling|second_edit_reuses_the_owned_policy'] = slim(guarded(
        lambda: run_workspace.update(first['profile'], {}, 'farm', {'routines.ad_gems.window_sec': 21, 'routines.floating_gems.interval_sec': 9})))
    for kind in ('coin', 'tournament', 'shard', 'quest', 'dissonance', 'unknown_kind', None):
        out[f'capabilities|{kind}'] = run_workspace._capabilities({'kind': kind} if kind is not None else {})
    out['normalize|dotted'] = guarded(lambda: run_workspace.normalize_changes({'routines.ad_gems.enabled': False, 'shopping_plan': plan_v2()}))
    out['normalize|nested'] = guarded(lambda: run_workspace.normalize_changes({'routines': {'shopping': {'interval_sec': 30}}}))
    out['normalize|bad_path'] = guarded(lambda: run_workspace.normalize_changes({'routines.ad_gems': 1}))
    out['normalize|four_parts'] = guarded(lambda: run_workspace.normalize_changes({'routines.ad_gems.enabled.x': 1}))
    return clean(out)


def all_traces(tmp_path):
    tmp_path = Path(tmp_path)
    folders = {}
    for name in ('a', 'b', 'c', 'd', 'e'):
        folders[name] = tmp_path / name
        folders[name].mkdir(parents=True, exist_ok=True)
    return dict(version=1, captured_on=source_hashes(),
                capabilities=capability_lists(folders['a']), validator=validator_cases(),
                controls=control_matrix(folders['b']), multi_control=multi_control(folders['c']), plan_controls=plan_controls(folders['c']),
                run_now_gems=run_now_cases(folders['d']), run_now_shopping=run_now_shopping_cases(folders['e']),
                published=published(folders['a']),
                resolve=resolve_cases(), apply=apply_cases(), merge=merge_cases(), workspace=workspace_cases())
