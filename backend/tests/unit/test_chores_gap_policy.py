"""Chores under the schedule policy (roadmap item 7, A3a: rows R-5 and R-7, and correction C1).

`fixtures/action_a3a_golden_v1.json` was captured on the dispatcher BEFORE the migration. Every scenario in which no chore fails replays it unchanged; the three in
which a chore fails differ in exactly the one way C1 decided (a failed attempt uses the gap, so the next chore waits for the next gap), and the tests below state that
difference in plain numbers instead of trusting a regenerated fixture.
"""
import datetime
from types import SimpleNamespace

import pytest

import action_a3a_golden as golden
from runtime.action_session import ActionSession
from scheduling import chores, daystate
from towerpilot_workflows.action_contract import CATALOG, CHORE_SCHEDULE, FakeClock
from towerpilot_workflows.actions import Actions

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')
UNCHANGED = ('plain', 'shatter_disabled', 'all_disabled', 'policy_unreadable', 'limit_two', 'housekeeping_only', 'due_raises')


@pytest.fixture(scope='module')
def captured():
    return golden.load()


@pytest.fixture(scope='module')
def replayed():
    return golden.roundtrip(golden.chore_traces())


# ------------------------------------------------------------------ parity with the old dispatcher
def test_every_scenario_without_a_failing_chore_replays_the_captured_trace(captured, replayed):
    assert sorted(replayed) == sorted(captured['chores'])
    for name in UNCHANGED:
        assert replayed[name] == captured['chores'][name], name


def test_the_shipped_registry_keeps_its_names_order_floor_and_due_answers(captured):
    assert golden.roundtrip(golden.chore_real_registry()) == captured['chore_registry']
    assert captured['chore_registry']['names'] == ['quest_scan', 'shatter', 'housekeeping'] and captured['chore_registry']['start_hour'] == 3 == chores.START_HOUR


def gaps(trace):
    """[(moment, calls, ran)] of the gaps in which at least one chore was attempted."""
    return [(row[0], row[2], row[1]) for row in trace if row[2]]


# ------------------------------------------------------------------ C1
def test_a_failed_chore_uses_the_gap_so_the_next_chore_waits_for_the_next_one(replayed):
    # old behaviour (captured): the failing quest_scan did not use the gap, so shatter ran in the same one.
    old = gaps(golden.load()['chores']['first_fails'])
    assert old[0] == ('09-10 03:00', ['quest_scan', 'shatter'], ['shatter'])
    new = gaps(replayed['first_fails'])
    assert new[:3] == [('09-10 03:00', ['quest_scan'], []), ('09-10 03:01', ['shatter'], ['shatter']), ('09-10 09:00', ['housekeeping'], ['housekeeping'])]
    assert new[3:] == [('09-11 03:00', ['quest_scan'], []), ('09-11 03:01', ['shatter'], ['shatter']), ('09-11 04:00', ['housekeeping'], ['housekeeping'])]     # the next local day, the same


def test_chore_failure_consumes_the_gap():
    """The regression of C1 in its smallest form: it fails on the dispatcher before the correction."""
    registry, calls = golden.chore_world(('quest_scan', 'shatter'), fail=('quest_scan',))          # quest_scan raises ("the menu never opened"), shatter would work
    store, clock = golden.Store(), golden.Clock(datetime.datetime(2026, 9, 10, 9, 0))
    with golden.frozen(clock), golden.daily_state(store), pytest_patch(chores, 'CHORES', registry), pytest_patch(chores, '_policy', lambda: {}):
        assert chores.run_due() == []
        assert calls == ['quest_scan']                          # the failed attempt used this gap: shatter did not run in it
        assert chores.run_due() == ['shatter']
        assert calls == ['quest_scan', 'shatter']


def test_every_chore_that_fails_is_still_attempted_once_marked_and_not_retried_that_day(replayed):
    trace = replayed['all_fail']
    assert gaps(golden.load()['chores']['all_fail'])[0] == ('09-10 03:00', ['quest_scan', 'shatter', 'housekeeping'], [])      # captured: all three in one gap
    assert gaps(trace) == [('09-10 03:00', ['quest_scan'], []), ('09-10 03:01', ['shatter'], []), ('09-10 09:00', ['housekeeping'], []), ('09-11 03:00', ['quest_scan'], []),
                           ('09-11 03:01', ['shatter'], []), ('09-11 04:00', ['housekeeping'], [])]
    day_ten = [row for row in trace if row[0].startswith('09-10') and row[2]]
    assert day_ten[-1][3] == {'chore_housekeeping': '2026-09-10', 'chore_quest_scan': '2026-09-10', 'chore_shatter': '2026-09-10'}
    assert not [row for row in trace if row[0] in ('09-10 09:05', '09-10 09:10', '09-10 23:59') and row[2]]    # nothing runs again the same day


def test_the_limit_counts_attempts_not_successes(replayed):
    new = gaps(replayed['limit_two_first_fails'])
    assert gaps(golden.load()['chores']['limit_two_first_fails'])[0][1] == ['quest_scan', 'shatter', 'housekeeping']    # captured: three attempts under a limit of two
    assert new[0] == ('09-10 03:00', ['quest_scan', 'shatter'], ['shatter'])      # two attempts, one of them failed: the gap is spent at two
    assert new[1] == ('09-10 03:01', ['housekeeping'], ['housekeeping'])
    assert all(len(calls) <= 2 for _, calls, _ in new)


def pytest_patch(target, name, value):
    from unittest import mock
    return mock.patch.object(target, name, value)


# ------------------------------------------------------------------ the day, the floor and the catalog
def test_every_chore_in_the_registry_has_a_catalog_definition_with_the_chore_schedule():
    for name, _, _ in chores.CHORES:
        definition = CATALOG.get(name)
        assert definition.schedule is CHORE_SCHEDULE and definition.consumes == 'attempted' and definition.scope == 'installation' and definition.family == 'chores'
        assert 'orchestrator' in definition.hosts


def test_the_floor_hour_has_one_definition_and_the_module_says_so():
    assert chores.START_HOUR == CHORE_SCHEDULE.floor_hour == 3
    assert '3 AM' not in open(chores.__file__, encoding='utf-8').read().split('def ')[0].split('START_HOUR =')[0]       # the day does not roll at 3 AM


def test_a_chore_is_due_from_03_00_local_and_closed_for_the_local_date():
    store, clock = golden.Store(), golden.Clock()
    with golden.frozen(clock), golden.daily_state(store):
        for hour, minute, kind in ((0, 5, 'wait'), (2, 59, 'wait'), (3, 0, 'due'), (23, 59, 'due')):
            clock.local = datetime.datetime(2026, 9, 10, hour, minute)
            assert chores.decide('quest_scan').kind == kind, (hour, minute)
        store.data['chore_quest_scan'] = '2026-09-10'
        clock.local = datetime.datetime(2026, 9, 10, 23, 59)
        assert chores.decide('quest_scan').reason == 'consumed'
        clock.local = datetime.datetime(2026, 9, 11, 0, 30)
        assert chores.decide('quest_scan').reason == 'before_floor'              # the new local date has not opened, and yesterday's mark does not hide it
        clock.local = datetime.datetime(2026, 9, 11, 3, 0)
        assert chores.decide('quest_scan').kind == 'due'


# ------------------------------------------------------------------ a mark the daily state refuses
def test_a_refused_mark_is_kept_in_this_process_so_the_chore_is_not_retried_in_every_gap(monkeypatch):
    registry, calls = golden.chore_world(('quest_scan',))
    monkeypatch.setattr(chores, 'CHORES', registry)
    monkeypatch.setattr(chores, '_policy', lambda: {})
    store, clock = golden.Store(), golden.Clock(datetime.datetime(2026, 9, 10, 9, 0))
    logged = []

    def refuse(key):
        raise daystate.DaystateLockTimeout('stayed locked')
    chores._unsaved.clear()
    with golden.frozen(clock), golden.daily_state(store), pytest_patch(daystate, 'mark_today', refuse):     # a nested patch: monkeypatch would restore the harness's stub after the harness left
        monkeypatch.setattr('runtime.logger.event', lambda kind, **fields: logged.append((kind, fields)))
        assert chores.run_due() == ['quest_scan']                               # the refused mark did not raise into the caller
        assert chores.run_due() == [] and calls == ['quest_scan']               # ... and the chore is closed for this process
        assert [kind for kind, _ in logged if kind == 'daystate_write_failed'] == ['daystate_write_failed']
        clock.local = datetime.datetime(2026, 9, 11, 9, 0)
        assert chores.run_due() == ['quest_scan']                               # the next local day is a new day
    chores._unsaved.clear()


def test_a_successful_mark_clears_the_in_process_memory(monkeypatch):
    chores._unsaved['quest_scan'] = '2026-09-10'
    store, clock = golden.Store(), golden.Clock(datetime.datetime(2026, 9, 10, 9, 0))
    with golden.frozen(clock), golden.daily_state(store):
        chores._mark('quest_scan')
    assert 'quest_scan' not in chores._unsaved and store.data['chore_quest_scan'] == '2026-09-10'


# ------------------------------------------------------------------ the history of an attempt
@pytest.fixture
def history(tmp_path):
    clock = FakeClock(mono=100.0, wall=5000.0)
    session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator', clock=clock)
    return SimpleNamespace(session=session, clock=clock, rows=lambda **kw: Actions.read(session.path, device='device', **kw)[0])


def run_gap(history, *, fail=(), policy=None, names=('quest_scan', 'shatter', 'housekeeping'), hour=9, limit=1, day=10):
    store_clock = golden.Clock(datetime.datetime(2026, 9, day, hour, 0))
    registry, calls = golden.chore_world(names, fail)
    store = history.store if hasattr(history, 'store') else golden.Store()
    history.store = store
    with golden.frozen(store_clock), golden.daily_state(store), pytest_patch(chores, 'CHORES', registry), pytest_patch(chores, '_policy', lambda: policy or {}):
        ran = chores.run_due(limit, session=history.session)
    return ran, calls


def test_a_completed_chore_is_one_finished_row_with_the_local_day_as_its_occurrence(history):
    ran, _ = run_gap(history)
    row, = history.rows()
    assert ran == ['quest_scan'] and (row['action'], row['instance'], row['state'], row['trigger']) == ('quest_scan', 'quest_scan', 'succeeded', 'automatic')
    assert row['occurrence_key'] == '2026-09-10' and row['outcome'] == dict(result='succeeded', reason='completed', effect='unknown', attempts=0, confirmed=0, evidence=[])
    assert row['host']['battle_id'] is None and row['host']['driver'] == 'orchestrator'                       # operation-scoped: a chore runs between battles
    assert row['duration_ms'] is not None and row['context']['account'] == 'account'


def test_housekeeping_claims_no_game_effect_and_the_game_facing_chores_say_unknown(history):
    for _ in range(3):
        run_gap(history)
    effects = {row['action']: row['outcome']['effect'] for row in history.rows()}
    assert effects == {'quest_scan': 'unknown', 'shatter': 'unknown', 'housekeeping': 'none'}


def test_a_failed_chore_is_a_failed_row_and_still_closes_the_day(history):
    ran, calls = run_gap(history, fail=('quest_scan',))
    row, = history.rows()
    assert ran == [] and calls == ['quest_scan'] and (row['state'], row['outcome']['reason']) == ('failed', 'error')
    assert history.store.data['chore_quest_scan'] == '2026-09-10'


def test_the_row_is_finished_before_the_day_flag_is_written(history):
    seen = []
    store = golden.Store()
    history.store = store
    original = store.mark_today

    def mark(key):
        seen.append([row['state'] for row in history.rows()])
        original(key)
    store.mark_today = mark
    run_gap(history)
    assert seen == [['succeeded']]                                      # when the flag was set, the row was already terminal


def test_a_chore_the_profile_disabled_is_one_aggregated_refusal_and_is_not_marked(history):
    run_gap(history, policy={'quest_scan': False}, limit=1)
    run_gap(history, policy={'quest_scan': False}, limit=1, hour=9)
    rows = {row['action']: row for row in history.rows()}
    refused = rows['quest_scan']
    assert refused['state'] == 'refused' and refused['outcome']['reason'] == 'disabled_by_profile'
    assert refused['aggregate']['count'] == 2                            # the same refusal twice is one row counted twice
    assert 'chore_quest_scan' not in history.store.data                 # NOT marked: re-enabling later the same day lets it run


def test_with_no_session_nothing_is_recorded_and_the_chore_runs_exactly_as_before(tmp_path):
    store, clock = golden.Store(), golden.Clock(datetime.datetime(2026, 9, 10, 9, 0))
    registry, calls = golden.chore_world()
    with golden.frozen(clock), golden.daily_state(store), pytest_patch(chores, 'CHORES', registry), pytest_patch(chores, '_policy', lambda: {}):
        assert chores.run_due() == ['quest_scan'] and chores.run_due(1, None) == ['shatter']
    assert calls == ['quest_scan', 'shatter'] and not (tmp_path / 'operations.db').exists()


def test_a_history_failure_never_stops_a_chore_or_its_mark(history, monkeypatch):
    def broken(self, body):
        raise RuntimeError('the ledger is on fire')
    monkeypatch.setattr(Actions, 'begin', broken)
    monkeypatch.setattr(Actions, 'record_check', broken)
    ran, calls = run_gap(history)
    assert ran == ['quest_scan'] and calls == ['quest_scan'] and history.store.data['chore_quest_scan'] == '2026-09-10'
    assert history.session.unrecorded.get('begin') == 1


def test_a_session_that_cannot_make_a_recorder_is_logged_and_the_chore_still_runs(monkeypatch):
    class Hostile:
        def recorder(self, name):
            raise RuntimeError('no recorder')
    store, clock = golden.Store(), golden.Clock(datetime.datetime(2026, 9, 10, 9, 0))
    registry, calls = golden.chore_world()
    logged = []
    with golden.frozen(clock), golden.daily_state(store), pytest_patch(chores, 'CHORES', registry), pytest_patch(chores, '_policy', lambda: {}):
        monkeypatch.setattr('runtime.logger.event', lambda kind, **fields: logged.append(kind))
        assert chores.run_due(session=Hostile()) == ['quest_scan']
    assert calls == ['quest_scan'] and 'action_record_failed' in logged and store.data['chore_quest_scan'] == '2026-09-10'
