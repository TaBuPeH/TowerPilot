"""Every pilot caller of the action contract records its executions (roadmap item 6, A2-2 to A2-5).

Each real host seam is driven with a fake clock, fake capture and fake input, against a temporary ledger, and must
leave a persisted execution. A host handed no session records nothing, so the source check below counts that as a
failure: "records nothing" is never a verified state.
"""

import paths as test_paths
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import action_golden as golden
from control import harvest
from flows.shard import GemWatch
from harvest_support import NOW, choose, finish_last, make_env
from runtime.action_session import ActionSession
from towerpilot_workflows.actions import Actions, new_record

BACKEND = test_paths.BACKEND
CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')
HOSTS = {'orchestrator.py': 'orchestrator', 'gem_collector.py': 'collector', 'flows/shard.py': 'shard',
         'flows/quest_sm.py': 'quest_sm', 'flows/quest_ilm.py': 'quest_ilm', 'runtime/harness.py': 'harness'}


# ------------------------------------------------------------------ the call sites
def calls(module, name):
    tree = ast.parse((BACKEND / module).read_text(encoding='utf-8'))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and (getattr(node.func, 'attr', None) == name or getattr(node.func, 'id', None) == name)]


@pytest.mark.parametrize('module,driver', sorted(HOSTS.items()))
def test_every_production_gem_watch_is_constructed_with_its_hosts_session(module, driver):
    assert 'orchestrator.py' in HOSTS
    found = calls(module, 'GemWatch')
    assert found, f'{module} no longer constructs a GemWatch: update the host table'
    for node in found:
        assert 'session' in {keyword.arg for keyword in node.keywords}, f'{module}:{node.lineno} builds a GemWatch with no session'
    source = (BACKEND / module).read_text(encoding='utf-8')
    assert (f"'{driver}'" in source), f'{module} does not name its driver {driver!r}'


def test_the_orchestrators_shopper_is_given_a_recorder_and_the_collector_follows_its_battle():
    assert calls('orchestrator.py', 'GemWatch')
    assert 'recorder' in (BACKEND / 'orchestrator.py').read_text(encoding='utf-8').split('self.shop = shopper.Shopper(preset())')[1][:200]
    collector = (BACKEND / 'gem_collector.py').read_text(encoding='utf-8')
    assert collector.count('session=actions') == 3 and "ActionSession.for_battle(session, 'collector', tracked_only=True)" in collector


# ---------------------------------------------------------------------- gem hosts
@pytest.mark.parametrize('driver', sorted(HOSTS.values()))
def test_each_gem_host_leaves_a_persisted_window_for_both_collectors(tmp_path, driver):
    path = tmp_path / 'operations.db'
    session = ActionSession(path, context=CONTEXT, driver=driver)
    world = golden.World(seed=3)
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': True}}, session=session)
        watch.ad_gems.rng = world.rng
        world.claim_confirms = True
        for second in range(130):
            world.now += 1.0
            if second == 2:
                world.offer = True                    # an offer appears early in the first window; the claim removes it
            watch.poll(world.frame())
    stored = Actions.read(str(path), device='device')[0]
    by_action = {}
    for row in stored:
        by_action.setdefault(row['action'], []).append(row)
    assert set(by_action) == {'ad_gems', 'floating_gems'}, sorted(by_action)
    assert all(row['host']['driver'] == driver and row['context']['account'] == 'account' for row in stored)
    assert all(row['state'] != 'running' for row in stored if row['finished_at'] is not None)
    ad = sorted(by_action['ad_gems'], key=lambda row: row['started_at'])[0]
    assert ad['state'] == 'succeeded' and ad['outcome']['effect'] == 'unconfirmed' and ad['outcome']['evidence'] == ['offer_disappeared']      # the offer vanished after the tap; no balance proves a gem
    assert ad['duration_ms'] is not None and ad['duration_ms'] > 0 and ad['refs']['window_id']
    assert all(row['occurrence_key'].startswith('window:') for row in stored)


def test_a_gem_watch_given_no_session_records_nothing_and_behaves_the_same(tmp_path):
    world = golden.World(seed=3)
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': True}})
        assert watch.ad_gems.recorder is None and watch.orbit.recorder is None
        world.now += 1.0
        watch.poll(world.frame())
    assert not (tmp_path / 'operations.db').exists()


@pytest.mark.parametrize('damaged', ['{"host":null}', '{', '{}', 'null'])
def test_a_damaged_history_row_can_neither_stop_a_live_gem_collector_nor_change_what_it_does(tmp_path, damaged):
    """Review R11-01, executed on the real GemWatch: an open window nobody can place (its host is alive as far as the ledger knows)
    makes every record of that instance fail. The collector must tap and decide exactly as it does with no history at all."""
    import sqlite3
    path = tmp_path / 'operations.db'
    row = Actions(path).begin(new_record(device='device', action='ad_gems', instance='ad_gems', trigger='automatic'))
    db = sqlite3.connect(path)
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (damaged, row['id']))
    db.commit()
    db.close()

    def run(session):
        world = golden.World(seed=3)
        world.claim_confirms = True
        with golden.gem_world(world):
            watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': False}}, session=session)
            watch.ad_gems.rng = world.rng
            for second in range(130):
                world.now += 1.0
                if second == 2:
                    world.offer = True
                watch.poll(world.frame())
        return world.taps, [event for event in world.events if event[0] != 'action_record_failed'], world.draws

    session = ActionSession(path, context=CONTEXT, driver='orchestrator')
    with_history = run(session)
    assert with_history == run(None) and with_history[0], 'the collector tapped differently, or not at all'
    assert session.unrecorded and set(session.unrecorded) <= {'open_execution', 'begin'}


# ============================== review round 2, R11-01: the open window is damaged in EVERY field, and the collector never notices
CORRUPTIONS = {'deleted': lambda body, key: body.pop(key), 'null': lambda body, key: body.__setitem__(key, None), 'text': lambda body, key: body.__setitem__(key, 'x'),
               'number': lambda body, key: body.__setitem__(key, 7), 'list': lambda body, key: body.__setitem__(key, []),
               'mapping': lambda body, key: body.__setitem__(key, {}), 'negative': lambda body, key: body.__setitem__(key, -1)}
NESTED = [('outcome', key) for key in ('result', 'reason', 'effect', 'attempts', 'confirmed', 'evidence')] + \
         [('host', key) for key in ('driver', 'operation_id', 'battle_id', 'generation', 'parent_execution')] + [('context', 'account')]


def damage(path, execution_id, mutate):
    import json
    import sqlite3
    db = sqlite3.connect(path)
    body = json.loads(db.execute('SELECT body FROM action_executions WHERE id=?', (execution_id,)).fetchone()[0])
    mutate(body)
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (json.dumps(body), execution_id))
    db.commit()
    db.close()


def gem_collection(tmp_path, name, mutate=None):
    """The reviewer's flow on the real GemWatch, second by second: a window opens, ITS stored row is damaged the moment it exists, an offer
    appears and is claimed (the window closes through the damaged row), and the collector runs on through the next windows. Returns what
    the collector did: its taps, its events (the recording failure aside) and its random draws, and the session."""
    path = tmp_path / f'{name}.db'
    session = ActionSession(path, context=CONTEXT, driver='collector') if mutate is not None else None
    world = golden.World(seed=7)
    world.claim_confirms = True
    damaged = False
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': False}}, session=session)
        watch.ad_gems.rng = world.rng
        for second in range(330):
            world.now += 1.0
            if second == 2:
                world.offer = True
            watch.poll(world.frame())
            if mutate is not None and not damaged and watch.ad_gems.recorder.execution_id is not None:
                damage(path, watch.ad_gems.recorder.execution_id, mutate)
                damaged = True
    if mutate is not None:
        assert damaged, 'no window was opened, so nothing was damaged'
    return world.taps, [event for event in world.events if event[0] != 'action_record_failed'], world.draws, session


@pytest.mark.parametrize('section,key', [(None, 'trigger')] + [(None, key) for key in (
    'schema_version', 'id', 'ledger_id', 'device', 'action', 'instance', 'occurrence_key', 'host', 'context', 'config_revision', 'policy_revision',
    'request_key', 'command_id', 'state', 'scheduled_for', 'accepted_at', 'started_at', 'finished_at', 'duration_ms', 'duration_basis', 'outcome',
    'aggregate', 'coverage', 'refs')] + NESTED, ids=lambda value: str(value))
def test_a_stored_window_damaged_in_any_field_can_neither_stop_a_live_gem_collector_nor_change_what_it_does(tmp_path, section, key):
    control = gem_collection(tmp_path, 'control')
    assert control[0], 'the control run tapped nothing, so the comparison would prove nothing'
    for kind, corrupt in CORRUPTIONS.items():
        def mutate(body, corrupt=corrupt):
            corrupt(body[section] if section else body, key)
        got = gem_collection(tmp_path, f'{section}-{key}-{kind}', mutate)
        assert got[:3] == control[:3], (section, key, kind)


def test_missing_trigger_in_open_history_cannot_escape_gemwatch_poll(tmp_path):
    """The reviewer's own probe (round 2), verbatim in effect: the open row loses only `trigger`, then the window closes."""
    taps, events, draws, session = gem_collection(tmp_path, 'trigger', lambda body: body.pop('trigger'))
    assert session.unrecorded, 'the failure must be counted'
    assert (taps, events, draws) == gem_collection(tmp_path, 'control-2')[:3]


def test_an_ad_gem_claim_that_was_sent_but_never_confirmed_is_failed_unconfirmed_never_a_success(tmp_path):
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator')
    world = golden.World(seed=3)
    world.claim_confirms = False                      # the offer is still on the HUD after every tap
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': False}}, session=session)
        watch.ad_gems.rng = world.rng
        for second in range(130):
            world.now += 1.0
            if second == 2:
                world.offer = True
            watch.poll(world.frame())
    done = [row for row in Actions.read(str(tmp_path / 'operations.db'), device='device')[0] if row['finished_at']]
    assert done and done[-1]['state'] == 'failed' and done[-1]['outcome']['effect'] == 'unconfirmed'
    assert done[-1]['outcome']['attempts'] > 0 and done[-1]['outcome']['confirmed'] == 0 and done[-1]['outcome']['evidence'] == []
    assert world.taps                                  # input really was sent: that is why this is not a no-op


def test_an_ad_gem_claim_the_input_boundary_refused_is_recorded_refused_not_failed(tmp_path):
    from unittest import mock
    from device import act
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator')
    world = golden.World(seed=3)
    with golden.gem_world(world), mock.patch.object(act, 'tap', side_effect=act.TapRefused('taps are revoked')):
        watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': False}}, session=session)
        watch.ad_gems.rng = world.rng
        for second in range(130):
            world.now += 1.0
            if second == 2:
                world.offer = True
            watch.poll(world.frame())
    done = [row for row in Actions.read(str(tmp_path / 'operations.db'), device='device')[0] if row['finished_at']]
    assert done and done[-1]['state'] == 'refused' and done[-1]['outcome']['effect'] == 'unconfirmed' and done[-1]['outcome']['reason'] == 'tap_refused'


def test_an_orbit_window_that_taps_without_a_confirmed_gain_is_failed_unconfirmed_never_a_success(tmp_path):
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='shard')
    world = golden.World(seed=3)
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': False, 'gem_orbit': {'enabled': True}}, session=session)
        for _ in range(130):
            world.now += 1.0
            watch.poll(world.frame())
    orbit = [row for row in Actions.read(str(tmp_path / 'operations.db'), device='device')[0] if row['action'] == 'floating_gems']
    assert orbit and all(row['state'] == 'failed' and row['outcome']['effect'] == 'unconfirmed' for row in orbit if row['finished_at'])
    assert orbit[0]['outcome']['attempts'] > 0 and orbit[0]['outcome']['confirmed'] == 0


def orbit_rows(path):
    return [row for row in Actions.read(str(path), device='device')[0] if row['action'] == 'floating_gems' and row['finished_at']]


def test_an_orbit_window_whose_gain_is_confirmed_is_recorded_succeeded_with_the_taps_that_were_sent(tmp_path):
    """The confirmed half of the orbit record, over the r14c tapper (a gem must be recognized before any tap): the +2 the tapper
    verifies closes the window as a success carrying the taps it sent, never as a timeout. Since r14g a confirmed claim resumes watching
    at once, so the collected gem leaves the screen as soon as it has been tapped (a gem that stayed would open the next attempt)."""
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='shard')
    world = golden.World(seed=3)
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': False, 'gem_orbit': {'enabled': True}}, session=session)
        for second in range(130):
            world.now += 1.0
            if second == 1:
                world.hit_next_tap = True
            if world.balance > 200:
                world.gem_visible = False          # the tap that raised the balance collected it
            watch.poll(world.frame())
    confirmed = [row for row in orbit_rows(tmp_path / 'operations.db') if row['state'] == 'succeeded']
    assert confirmed and confirmed[0]['outcome']['effect'] == 'confirmed' and confirmed[0]['outcome']['confirmed'] == 1
    assert confirmed[0]['outcome']['attempts'] > 0 and confirmed[0]['outcome']['evidence'] == ['balance_increased']
    assert watch.orbit.last_outcome['confirmed'] is True and world.taps


def test_orbit_taps_the_input_boundary_only_simulated_are_not_counted_as_input(tmp_path):
    """A dry-run tap sends nothing (act.tap reports `dry_run`): the window it belongs to is a no-op, not a failed attempt."""
    from unittest import mock
    from device import act
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='shard')
    world = golden.World(seed=3)
    with golden.gem_world(world), mock.patch.object(act, 'tap', lambda *args, **kwargs: {'dry_run': True}):
        watch = GemWatch({'ad_gems': False, 'gem_orbit': {'enabled': True}}, session=session)
        for _ in range(130):
            world.now += 1.0
            watch.poll(world.frame())
    rows = orbit_rows(tmp_path / 'operations.db')
    assert rows and all(row['state'] == 'no_op' and row['outcome']['attempts'] == 0 and row['outcome']['effect'] == 'none' for row in rows)
    assert not watch.orbit.sent_tap and world.taps == []


def test_a_window_without_an_offer_is_a_no_op_with_no_effect(tmp_path):
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='harness')
    world = golden.World(seed=3)
    with golden.gem_world(world):
        watch = GemWatch({'ad_gems': True, 'gem_orbit': {'enabled': False}}, session=session)
        for _ in range(130):
            world.now += 1.0
            watch.poll(world.frame())
    done = [row for row in Actions.read(str(tmp_path / 'operations.db'), device='device')[0] if row['finished_at']]
    assert done and done[0]['state'] == 'no_op' and done[0]['outcome'] == dict(
        result='no_op', reason='no_offer_seen', effect='none', attempts=0, confirmed=0, evidence=[])


# --------------------------------------------------------------------------- shopping
def test_a_shopping_sweep_that_buys_is_its_own_row_with_the_confirmed_purchases(tmp_path):
    from interactions import shopper
    from orchestrator import advance_shopping
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator')
    clock = [100.0]
    with golden.shopping_world(clock) as market:
        instance = shopper.Shopper({'shopping_plan': golden.shopping_plan()})
        instance.recorder = session.recorder('shopping')
        rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False, mission=SimpleNamespace(active=False))
        for index in range(120):
            clock[0] += 0.1
            advance_shopping(rs, index + 1, 20, False, clock[0], 90)
        instance.abort()
    sweeps = [row for row in Actions.read(str(tmp_path / 'operations.db'), device='device')[0] if row['action'] == 'shopping']
    bought = [row for row in sweeps if row['outcome']['confirmed']]
    assert bought and all(row['state'] == 'succeeded' and row['outcome']['effect'] == 'confirmed'
                          and row['outcome']['evidence'] == ['confirmed_purchase'] for row in bought)
    assert all(row['occurrence_key'].startswith('sweep:') and row['refs']['sweep_id'] for row in sweeps)
    assert bought[0]['outcome']['confirmed'] == len(market.taps) or bought[0]['outcome']['confirmed'] > 0


def test_sweeps_that_send_no_input_are_counted_checks_not_a_row_each(tmp_path):
    from interactions import shopper
    from orchestrator import advance_shopping
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator')
    clock = [100.0]
    with golden.shopping_world(clock) as market:
        market.cash = 50                                           # nothing affordable: every sweep ends with no input
        instance = shopper.Shopper({'shopping_plan': golden.shopping_plan()})
        instance.recorder = session.recorder('shopping')
        rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False, mission=SimpleNamespace(active=False))
        for index in range(4):
            clock[0] += 100.0                                      # one sweep per interval
            advance_shopping(rs, index + 1, 20, False, clock[0], 90)
        assert market.taps == []
    sweeps = [row for row in Actions.read(str(tmp_path / 'operations.db'), device='device')[0] if row['action'] == 'shopping']
    assert len(sweeps) == 1 and sweeps[0]['aggregate']['count'] == 4 and sweeps[0]['state'] == 'no_op'
    assert sweeps[0]['outcome']['effect'] == 'none' and sweeps[0]['duration_ms'] is None


# ---------------------------------------------------------------------------- harvest
@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


def attempts(h):
    return Actions.read(str(harvest.operations.ledger_path(h.root)), device=harvest.identity_for(h.cfg)['device_key'])[0]


def test_an_accepted_launch_is_one_row_keyed_by_its_durable_launch_key_and_linked_to_its_operation(h):
    choose(h)
    session = harvest.action_session(h.root, h.cfg)
    assert h.tick(NOW, actions=session)['action'] == 'launched'
    (row,) = attempts(h)
    operation = harvest.operations.ledger(h.root).active(harvest.identity_for(h.cfg)['device_key'])[0]
    assert row['action'] == 'harvest_launch' and row['state'] == 'succeeded' and row['outcome']['reason'] == 'launched'
    assert row['outcome']['effect'] == 'unconfirmed' and row['outcome']['evidence'] == []      # a launch starts a process; it does not show the collector ran
    assert row['request_key'] == operation['idempotency_key'] and row['refs']['operation_id'] == operation['id']
    assert row['host'] == dict(driver='dashboard', operation_id=None, battle_id=None, generation=None, parent_execution=None)
    again = harvest.action_session(h.root, h.cfg).record('harvest_launch', result='succeeded', request_key=row['request_key'])
    assert again['id'] == row['id'] and len(attempts(h)) == 1          # a second dashboard host makes no second row


def test_refusals_before_any_operation_exists_are_recorded_and_identical_ones_are_counted(h):
    choose(h)
    h.refusal = ('digits_missing', 'The digit font is not captured.')
    session = harvest.action_session(h.root, h.cfg)
    for offset in (0, harvest.REFUSAL_RETRY_SEC, 2 * harvest.REFUSAL_RETRY_SEC):
        assert h.tick(NOW + offset, actions=session)['action'] == 'refused'
    (row,) = attempts(h)
    assert (row['state'], row['outcome']['reason'], row['aggregate']['count'], row['host']['operation_id']) == ('refused', 'digits_missing', 3, None)
    h.refusal = None
    h.adb['state'] = 'offline'
    h.tick(NOW + 40, actions=session)
    assert [r['outcome']['reason'] for r in attempts(h)] == ['emulator_offline', 'digits_missing']      # a new reason: a new row


def test_refusals_under_different_desire_revisions_or_policy_revisions_are_never_one_row(h):
    choose(h)
    h.refusal = ('digits_missing', 'The digit font is not captured.')
    session = harvest.action_session(h.root, h.cfg)
    h.tick(NOW, actions=session)
    h.tick(NOW + harvest.REFUSAL_RETRY_SEC, actions=session)
    choose(h)                                                           # an explicit On again is a new desire revision
    h.tick(NOW + 2 * harvest.REFUSAL_RETRY_SEC, actions=session)
    h.source['edit'] += 1                                               # an edit of the run's gem settings is a new policy revision
    h.tick(NOW + 3 * harvest.REFUSAL_RETRY_SEC, actions=session)
    stored = sorted(attempts(h), key=lambda row: row['started_at'])
    assert [(row['config_revision'], row['aggregate']['count']) for row in stored] == [(1, 2), (2, 1), (2, 1)]
    assert len({row['policy_revision'] for row in stored}) == 2 and stored[0]['policy_revision'] == stored[1]['policy_revision'] != stored[2]['policy_revision']


def test_the_launch_decision_and_the_history_agree_after_a_failure(h):
    choose(h)
    session = harvest.action_session(h.root, h.cfg)
    h.tick(NOW, actions=session)
    finish_last(h, 'failed', ran=5, now=NOW)
    waiting = h.tick(NOW + 1, actions=session)
    assert waiting['state'] == 'backing_off' and waiting['next_attempt_at'] == NOW + harvest.backoff(1)
    assert [r['state'] for r in attempts(h)] == ['succeeded']          # the failure belongs to the operation, not a second row


def test_a_tick_without_a_session_records_nothing_and_decides_the_same(h):
    choose(h)
    assert h.tick(NOW)['action'] == 'launched'
    assert Actions.read_meta(str(harvest.operations.ledger_path(h.root))) is None