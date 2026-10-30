"""The host adapter: recording, failure isolation, battle following, settlement and the `runtime.actions` projection
(roadmap item 6, A2-1 to A2-3)."""

import paths as test_paths
import ast
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from flows.shard import GemWatch
from interactions.shopper import Shopper
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from towerpilot_workflows.action_contract import FakeClock
from towerpilot_workflows.actions import Actions, SchemaTooNew, UnusableRow, new_record
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

BACKEND = test_paths.BACKEND
CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')


@pytest.fixture
def clock():
    return FakeClock(mono=100.0, wall=5000.0)


@pytest.fixture
def session(tmp_path, clock):
    return ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator', clock=clock)


def rows(session, **kwargs):
    return Actions.read(session.path, device='device', **kwargs)[0]


# ---------------------------------------------------------------- the import graph
@pytest.mark.parametrize('module', ['towerpilot_workflows/action_contract.py', 'towerpilot_workflows/actions.py', 'runtime/action_session.py',
                                    'runtime/action_bindings.py', 'runtime/flow_history.py', 'player/saved_controls.py', 'towerpilot_workflows/control_settings.py'])
def test_the_action_modules_import_no_device_adb_capture_or_settings_module(module):
    """Nothing here may send input or reach the screen: the session records, it never calls a handler."""
    forbidden = {'device', 'cv2', 'settings', 'vision', 'interactions', 'flows', 'adbclient', 'capture', 'act', 'subprocess', 'socket'}
    tree = ast.parse((BACKEND / module).read_text(encoding='utf-8'))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
            imported |= {alias.name for alias in node.names if node.module == 'runtime'}
    assert not imported & forbidden, sorted(imported & forbidden)


# -------------------------------------------------------------------------- recording
def test_an_execution_records_its_host_context_trigger_and_monotonic_duration(session, clock):
    execution = session.begin('ad_gems', trigger='manual', occurrence_key='window:w1', scheduled_for=4990.0,
                              command_id='cmd-1', refs=dict(window_id='w1'))
    clock.advance(12.5)
    done = session.finish(execution, result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1,
                          confirmed=1, evidence=['offer_disappeared'])
    assert (done['started_at'], done['finished_at'], done['duration_ms']) == (5000.0, 5012.5, 12500.0)
    assert (done['trigger'], done['command_id'], done['scheduled_for'], done['instance']) == ('manual', 'cmd-1', 4990.0, 'ad_gems')
    assert done['context'] == dict(device_key='device', account='account', instance_name='main', profile='mine')
    assert done['host']['driver'] == 'orchestrator' and done['ledger_id'] == Actions.read_meta(session.path)['ledger_id']
    assert session.last['ad_gems']['result'] == 'succeeded' and rows(session) == [done]


def test_an_action_records_only_from_a_driver_kind_its_definition_declares_and_only_if_it_is_in_the_catalog(session, tmp_path):
    assert session.begin('shopping') is not None                              # an orchestrator host runs shopping
    assert session.begin('harvest_launch') is None and session.unrecorded == {'begin': 1}   # only the dashboard launches collectors
    assert session.begin('no_such_action') is None and session.unrecorded == {'begin': 2}
    assert session.check('harvest_launch', result='refused', reason='x') is None
    assert [row['action'] for row in rows(session)] == ['shopping']


def test_a_recording_failure_is_counted_and_logged_never_raised_into_the_driver(session, monkeypatch):
    logged = []
    monkeypatch.setattr('runtime.action_session.logger.event', lambda name, **fields: logged.append((name, fields)))
    monkeypatch.setattr(Actions, 'begin', lambda self, body: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
    assert session.begin('ad_gems') is None and session.unrecorded == {'begin': 1}
    assert session.finish(None, result='no_op') is None                     # a window that was never recorded finishes quietly
    assert logged[0][0] == 'action_record_failed' and logged[0][1]['reason'] == 'begin'


def test_a_second_open_execution_of_a_live_instance_is_refused_and_counted_without_stopping_the_handler(session):
    first = session.begin('ad_gems')
    assert first is not None
    assert session.begin('ad_gems') is None and session.unrecorded == {'open_execution': 1}
    assert [row['state'] for row in rows(session)] == ['running']


def test_a_recorder_that_never_saw_its_window_end_does_not_hide_it_when_the_next_one_opens(session):
    recorder = session.recorder('ad_gems')
    first = recorder.opened(occurrence_key='window:1')
    second = recorder.opened(occurrence_key='window:2')
    by_id = {row['id']: row for row in rows(session)}
    assert by_id[first]['state'] == 'failed' and by_id[first]['outcome']['reason'] == 'superseded'
    assert by_id[first]['outcome']['effect'] == 'unknown' and by_id[second]['state'] == 'running'


def test_identical_automatic_no_input_checks_share_one_counted_row_and_a_manual_one_does_not(session):
    session = ActionSession(session.path, context=CONTEXT, driver='dashboard', clock=session.clock)
    recorder = session.recorder('harvest_launch')
    for _ in range(3):
        recorder.check(result='refused', reason='device_owned')
    recorder.check(result='refused', reason='device_owned', trigger='manual')
    shared = [row for row in rows(session) if row['trigger'] == 'automatic']
    assert len(shared) == 1 and shared[0]['aggregate']['count'] == 3 and len(rows(session)) == 2


# ------------------------------------------------------------------- battle following
@pytest.fixture
def driver(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda *args, **kwargs: None)
    path = tmp_path / 'operations.db'
    context = dict(device_key='device', account='account', instance='main', serial='fake')
    operation, _ = Operations(path).accept('farm', context, {})
    body = dict(gather={'ad_gems': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
    battle = BattleSession(Battles(path), operation['id'], body, {'active_profile': 'mine'})
    actions = ActionSession.for_battle(battle)
    rs = SimpleNamespace(gems=GemWatch(body['gather'], session=actions), shop=Shopper(body), mission=SimpleNamespace(active=False),
                         shopping_enabled=False, rewards_enabled=True, repeat_enabled=True, tracker=SimpleNamespace(last=123), last_shop=0)
    battle.observe(rs, 123)
    return battle, actions, rs, operation


def test_the_battle_session_attaches_its_action_session_and_records_under_the_current_battle(driver):
    battle, actions, rs, operation = driver
    assert battle.actions is actions and actions.context['profile'] == 'mine'
    first = actions.begin('ad_gems')
    battle.finish('observed_results')                                          # the driver's own finalization settles it
    assert Actions.read(actions.path, device='device')[0][0]['state'] == 'interrupted'
    assert Actions.read(actions.path, device='device')[0][0]['host']['battle_id'] == battle.battle['id'] and first
    previous = battle.battle['id']
    battle.prepare_next(origin='adopted')
    second = actions.begin('ad_gems')
    newest = Actions.read(actions.path, device='device')[0][0]
    assert newest['id'] == second and newest['host']['battle_id'] == battle.battle['id'] != previous
    assert newest['host']['generation'] == battle.generation and newest['host']['operation_id'] == operation['id']


def test_the_projection_is_published_beside_the_unchanged_routines_and_mirrors_them(driver):
    battle, actions, rs, operation = driver
    battle.publish(rs, battle_visible=True)
    runtime = battle.store.get(battle.battle['id'])['runtime']
    projected = {item['id']: item for item in runtime['actions']['items']}
    assert runtime['actions']['schema_version'] == 1 and set(projected) >= {'ad_gems', 'floating_gems'}
    for name in ('ad_gems', 'floating_gems'):
        routine = runtime['routines'][name]
        assert projected[name]['enabled'] == routine['enabled']
        assert projected[name]['next']['due_at'] == routine['next_due_at']
    assert projected['ad_gems']['state'] == 'due' and projected['floating_gems']['state'] == 'disabled'   # first window at once; orbit off
    assert projected['ad_gems']['next']['basis'] == 'start_to_start' and projected['ad_gems']['descriptor']['fields']
    assert runtime['actions']['context']['account'] == 'account' and runtime['actions']['ledger_id']


def test_a_projection_that_cannot_be_built_never_stops_the_driver_and_publishes_the_routines_as_before(driver, monkeypatch):
    battle, actions, rs, operation = driver
    monkeypatch.setattr(actions, 'project', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('projection broke')))
    battle.publish(rs, battle_visible=True)
    runtime = battle.store.get(battle.battle['id'])['runtime']
    assert 'actions' not in runtime and set(runtime['routines']) >= {'ad_gems', 'floating_gems'}


def test_a_manual_run_now_is_recorded_as_a_manual_execution_linked_to_its_command(driver):
    import action_golden as golden
    battle, actions, rs, operation = driver
    command = battle.store.request_action(battle.battle['id'], operation['id'], battle.generation, 'ad_gems', 'once')
    battle.checkpoint(rs, battle_visible=True)
    assert rs.gems.ad_gems.manual_requested and rs.gems.ad_gems.manual_command_id == command['id']
    world = golden.World(seed=3)
    with golden.gem_world(world):
        rs.gems.ad_gems.rng = world.rng
        world.now += 1.0
        rs.gems.ad_gems.poll(world.frame())
    (row,) = Actions.read(actions.path, device='device')[0]
    assert (row['trigger'], row['command_id'], row['state']) == ('manual', command['id'], 'running')
    assert rs.gems.ad_gems.manual_command_id is None                          # consumed by the window it started


def test_a_manual_floating_gem_run_now_is_recorded_as_a_manual_execution_linked_to_its_command(driver):
    import action_golden as golden
    battle, actions, rs, operation = driver
    command = battle.store.request_action(battle.battle['id'], operation['id'], battle.generation, 'floating_gems', 'once')
    battle.checkpoint(rs, battle_visible=True)
    assert rs.gems.orbit.manual_requested and rs.gems.orbit.manual_command_id == command['id']
    world = golden.World(seed=3)
    with golden.gem_world(world):
        world.gem_visible = False                       # a manual request opens its bounded seeking attempt with no gem on screen
        world.now += 1.0
        rs.gems.orbit.poll(world.frame())
    rows = [row for row in Actions.read(actions.path, device='device')[0] if row['action'] == 'floating_gems']
    assert len(rows) == 1 and (rows[0]['trigger'], rows[0]['command_id'], rows[0]['state']) == ('manual', command['id'], 'running')
    assert rs.gems.orbit.manual_command_id is None                            # consumed by the attempt it started


def test_a_manual_shopping_sweep_is_linked_to_its_command_too(tmp_path):
    import action_golden as golden
    from orchestrator import advance_shopping
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator')
    clock = [100.0]
    with golden.shopping_world(clock):
        instance = Shopper({'shopping_plan': golden.shopping_plan()})
        instance.recorder = session.recorder('shopping')
        instance.manual_command_id = 'cmd-shop'
        assert instance.request_once()
        rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False, mission=SimpleNamespace(active=False))
        for index in range(40):
            clock[0] += 0.1
            advance_shopping(rs, index + 1, 20, False, clock[0], 90)
        instance.abort()
    shopping = [row for row in Actions.read(session.path, device='device')[0] if row['action'] == 'shopping']
    assert shopping and shopping[0]['trigger'] == 'manual' and shopping[0]['command_id'] == 'cmd-shop'


def test_a_session_attached_battle_without_actions_publishes_exactly_what_it_did_before(driver):
    battle, actions, rs, operation = driver
    battle.actions = None
    battle.publish(rs, battle_visible=True)
    assert 'actions' not in battle.store.get(battle.battle['id'])['runtime']


# ------------------------------------------------------------------ operation scope
def test_an_operation_scoped_host_gets_a_session_only_under_a_ledger_operation(tmp_path, monkeypatch):
    for name in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB', 'TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION'):
        monkeypatch.delenv(name, raising=False)
    assert ActionSession.for_operation('shard') is None                       # no ledger: nothing is recorded, and nothing fails
    path = tmp_path / 'operations.db'
    operation, _ = Operations(path).accept('shard', dict(device_key='device', account='account', instance='main', serial='fake'), {})
    monkeypatch.setenv('TOWER_OPERATION_ID', operation['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', str(path))
    scoped = ActionSession.for_operation('shard')
    assert scoped.driver == 'shard' and scoped.operation_id == operation['id'] and scoped.context['device_key'] == 'device'
    assert scoped.begin('ad_gems') is not None and rows(scoped)[0]['host']['battle_id'] is None
    monkeypatch.setenv('TOWER_OPERATION_ID', 'no-such-operation')
    assert ActionSession.for_operation('shard') is None                       # an unreadable operation records nothing


# ------------------------------- the history is bookkeeping beside a live driver: nothing it raises reaches the driver (review R11-01)
CALLS = {
    # session method -> (the ledger method it calls, how to call it, the reason it counts under, what the driver gets back)
    'begin': (Actions, 'begin', lambda s, eid: s.begin('ad_gems'), 'begin', None),
    'finish': (Actions, 'finish', lambda s, eid: s.finish(eid, result='no_op'), 'finish', None),
    'check': (Actions, 'record_check', lambda s, eid: s.check('ad_gems', result='no_op', reason='x'), 'check', None),
    'record': (Actions, 'record_terminal', lambda s, eid: s.record('ad_gems', result='no_op'), 'record', None),
    'settle': (Actions, 'settle_open', lambda s, eid: s.settle(), 'settle', []),
}
ERRORS = [AttributeError("'NoneType' object has no attribute 'get'"), TypeError('unsupported operand'), KeyError('host'), RuntimeError('anything'),
          sqlite3.OperationalError('database is locked'), UnusableRow('damaged'), SchemaTooNew('newer')]


@pytest.mark.parametrize('error', ERRORS, ids=lambda error: type(error).__name__)
@pytest.mark.parametrize('call', sorted(CALLS))
def test_whatever_the_history_raises_is_counted_by_reason_and_never_raised_into_the_driver(session, monkeypatch, call, error):
    owner, method, invoke, reason, returns = CALLS[call]
    execution = session.begin('ad_gems')                                   # a real open row for finish to name
    monkeypatch.setattr(owner, method, lambda *args, **kwargs: (_ for _ in ()).throw(error))
    assert invoke(session, execution) == returns
    assert session.unrecorded.get(reason, 0) >= 1


def test_a_projection_whose_ledger_cannot_be_read_is_still_a_projection(session, monkeypatch):
    monkeypatch.setattr(Actions, 'read_meta', staticmethod(lambda path: (_ for _ in ()).throw(AttributeError('meta'))))
    shown = session.project({})
    assert shown['ledger_id'] is None and shown['items'] == [] and session.unrecorded == {'ledger': 1}


def test_a_session_without_an_operation_or_a_ledger_still_starts_when_the_ledger_cannot_be_read(monkeypatch):
    from runtime import operations as runtime_operations
    monkeypatch.setattr(runtime_operations, 'environment_operation', lambda: (_ for _ in ()).throw(AttributeError('operation')))
    assert ActionSession.for_operation('collector') is None


def test_settling_reports_the_damaged_rows_it_met_and_still_closes_the_good_ones(session, monkeypatch):
    events = []
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda kind, **fields: events.append((kind, fields)))
    store = session._actions()
    store.begin(new_record(device='device', action='floating_gems', instance='floating_gems', trigger='automatic',
                           host=dict(operation_id='gone'), context=dict(account='account')))
    db = sqlite3.connect(session.path)
    db.execute("UPDATE action_executions SET body='{' ")
    db.commit()
    db.close()
    assert session.settle() == [] and ('action_history_unusable', dict(rows=1, where='session')) in events


# ====================================================================================== review round 2, R11-01
# The guarantee is the DECORATOR around the whole method, not a try around the ledger call: the reviewer's escape was a summary field
# built from the returned row after the call ("KeyError: trigger"), outside the old boundary.
PUBLIC_ENTRY_POINTS = {
    ActionSession: ['begin', 'finish', 'check', 'record', 'settle'],
}
CONTAINED_ELSEWHERE = {                  # public, not contained here, and why
    'project': 'BattleSession.publish wraps the projection (test_a_projection_that_cannot_be_built_never_stops_the_driver)',
    'recorder': 'a factory: it builds an object and touches no ledger',
    'for_battle': 'a factory called while a host is built, before any window exists',
    'for_operation': 'a factory with its own boundary (test_a_session_without_an_operation...)',
}


def public_methods(cls):
    return sorted(name for name, value in vars(cls).items() if not name.startswith('_') and callable(getattr(cls, name)))


def test_every_public_method_of_the_session_and_its_recorder_is_contained_or_named_as_contained_elsewhere():
    from runtime.action_session import Recorder
    for cls in (ActionSession, Recorder):
        for name in public_methods(cls):
            method = getattr(cls, name)
            contained = getattr(method, 'contained_as', None)
            assert contained or name in CONTAINED_ELSEWHERE, f'{cls.__name__}.{name} can raise into a driver: contain it, or name where it is contained'
    assert sorted(set(public_methods(ActionSession)) - set(CONTAINED_ELSEWHERE)) == sorted(PUBLIC_ENTRY_POINTS[ActionSession])


class Refusing(dict):
    """The session's `last` summaries: a consumer-side failure AFTER the ledger call succeeded."""

    def __setitem__(self, key, value):
        raise KeyError('trigger')


SUMMARY_CALLS = {
    'finish': lambda s, eid: s.finish(eid, result='no_op'),
    'check': lambda s, eid: s.check('ad_gems', result='no_op', reason='x'),
    'record': lambda s, eid: s.record('ad_gems', result='no_op'),
}


@pytest.mark.parametrize('call', sorted(SUMMARY_CALLS))
def test_a_failure_after_the_ledger_call_succeeded_is_counted_not_raised_and_the_row_was_written(session, call):
    execution = session.begin('ad_gems') if call == 'finish' else None
    session.last = Refusing()
    assert SUMMARY_CALLS[call](session, execution) is None
    assert session.unrecorded == {call: 1}
    stored = rows(session)
    assert len(stored) == 1 and stored[0]['state'] != 'running', 'the ledger had already done its part'


@pytest.mark.parametrize('call', sorted(SUMMARY_CALLS))
def test_a_failure_before_the_ledger_is_reached_is_contained_too(session, monkeypatch, call):
    execution = session.begin('ad_gems') if call == 'finish' else None
    monkeypatch.setattr(session, '_body', lambda *args, **kwargs: (_ for _ in ()).throw(TypeError('catalog')))
    monkeypatch.setattr(session, '_actions', lambda: (_ for _ in ()).throw(AttributeError('store')))
    assert SUMMARY_CALLS[call](session, execution) is None
    assert session.unrecorded.get(call) == 1


def test_a_clock_that_fails_is_contained(session, monkeypatch):
    monkeypatch.setattr(session.clock, 'wall', lambda: (_ for _ in ()).throw(OSError('clock')))
    assert session.begin('ad_gems') is None and session.record('ad_gems', result='no_op') is None and session.unrecorded == {'begin': 1, 'record': 1}


def test_a_failing_log_cannot_turn_a_counted_failure_into_a_raised_one(session, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
    monkeypatch.setattr(Actions, 'begin', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('ledger')))
    assert session.begin('ad_gems') is None and session.unrecorded == {'begin': 1}


@pytest.mark.parametrize('method,arguments', [('opened', {}), ('closed', dict(result='no_op')), ('check', dict(result='no_op', reason='x')), ('record', dict(result='no_op'))])
def test_the_recorder_contains_a_session_that_raises(session, monkeypatch, method, arguments):
    recorder = session.recorder('ad_gems')
    for name in ('begin', 'finish', 'check', 'record'):
        monkeypatch.setattr(session, name, lambda *args, **kwargs: (_ for _ in ()).throw(KeyError('trigger')))
    recorder.execution_id = 'some-open-window' if method == 'closed' else None
    assert getattr(recorder, method)(**arguments) is None
    assert session.unrecorded == {'recorder': 1}


def test_a_recorder_that_loses_its_window_to_a_failure_does_not_keep_a_stale_id(session, monkeypatch):
    recorder = session.recorder('ad_gems')
    recorder.opened()
    monkeypatch.setattr(session, 'finish', lambda *args, **kwargs: (_ for _ in ()).throw(KeyError('trigger')))
    recorder.closed(result='no_op')
    assert recorder.execution_id is None
