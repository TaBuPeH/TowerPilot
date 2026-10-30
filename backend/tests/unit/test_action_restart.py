"""Restart matrix (spec 3.6): a host stopped at each lifecycle point, with no later action required.

The host is stopped the way the dashboard's reconciliation stops a dead worker (its operation turns terminal) or the way a process dies inside a step (a BaseException that
nothing catches). Required, and asserted here: the refresher (never a GET, never a later `begin`) settles a running row `interrupted` with a null duration; no row claims success
without its evidence; the daily flag is what a restart reads; a restarted host replays nothing it was not asked to, and an identical durable key replays its row.

Item-6 audit N3 (before the daystate flag) is exercised on a TEMPORARY daystate with the real chore dispatcher. The other half of N3 - a crash BEFORE the buffered flush of a
fast-site execution leaving `coverage: unknown` - needs the buffered-execution seam of the rescue and nuke fast rules (r18f). It does not exist at this base, so nothing here
claims it; the interface it must provide is stated in `roadmap-item7-r18h-foundation/report.md`.
"""
import datetime
from types import SimpleNamespace

import pytest

import action_history_cases as cases
from control import actions as actions_api
from scheduling import chores, daystate
from towerpilot_workflows import action_history
from towerpilot_workflows.actions import Actions


def rows_of(host, **filters):
    return Actions.read(str(host.path), device='device', **filters)[0]


# ------------------------------------------------------------------ every lifecycle point of an execution
def after_accept(host):
    """The operation is accepted and the battle prepared; no execution was begun."""


def after_start(host):
    host.session.begin('ad_gems')


def after_input(host):
    """Input was sent (the window's taps) but nothing is recorded until the window closes: the row still says only that it began."""
    host.session.begin('ad_gems')


def before_confirmation(host):
    host.session.begin('ad_gems', refs=dict(window_id='w1'))


def after_finish(host):
    host.session.finish(host.session.begin('ad_gems'), result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1, confirmed=1, evidence=['offer_disappeared'])


POINTS = [pytest.param(after_accept, 0, id='after_accept'), pytest.param(after_start, 1, id='after_start'), pytest.param(after_input, 1, id='after_input'),
          pytest.param(before_confirmation, 1, id='before_confirmation'), pytest.param(after_finish, 0, id='after_finish')]


@pytest.mark.parametrize('point,open_rows', POINTS)
def test_a_host_stopped_at_any_point_is_settled_by_the_refresher_alone_and_claims_nothing_it_did_not_prove(tmp_path, point, open_rows):
    host = cases.RealHost(tmp_path)
    point(host)
    before = rows_of(host)
    assert len([row for row in before if row['state'] == 'running']) == open_rows
    host.end_operation()
    assert rows_of(host) == before                                                                  # a stop settles nothing by itself, and nothing reads it as settled
    settled_ids = actions_api.settle(tmp_path)
    assert len(settled_ids) == open_rows
    for row in rows_of(host):
        if row['id'] in settled_ids:
            assert row['state'] == 'interrupted' and row['finished_at'] is None and row['duration_ms'] is None and row['duration_basis'] == 'none'
            assert row['outcome'] == dict(result='interrupted', reason='operation_ended', effect='unknown', attempts=0, confirmed=0, evidence=[]) and row['settled_reason'] == 'operation_ended'
        else:
            assert row['state'] == 'succeeded' and row['outcome']['effect'] == 'confirmed' and row['outcome']['evidence'] == ['offer_disappeared']       # a finished row is untouched
    assert actions_api.settle(tmp_path) == []                                                       # idempotent
    assert not [row for row in rows_of(host) if row['state'] == 'succeeded' and row['outcome']['effect'] != 'confirmed']


def test_a_restarted_host_replays_no_input_and_its_first_begin_closes_the_dead_hosts_row_as_a_courtesy_not_as_success(tmp_path):
    first = cases.RealHost(tmp_path, key='op-1')
    first.session.begin('ad_gems')
    first.end_battle(status='interrupted')
    first.end_operation()
    second = cases.RealHost(tmp_path, key='op-2', origin='partial_adoption', previous_battle_id=first.battle_id)
    second_id = second.session.begin('ad_gems')
    rows = {row['id']: row for row in rows_of(second)}
    (dead,) = [row for row in rows.values() if row['id'] != second_id]
    assert dead['state'] == 'interrupted' and dead['outcome']['effect'] == 'unknown' and rows[second_id]['state'] == 'running' and rows[second_id]['coverage'] == 'since_adoption'
    assert [row['attempts'] if 'attempts' in row else row['outcome']['attempts'] for row in rows.values()] == [0, 0]             # nothing was sent on anyone's behalf


def test_an_identical_durable_key_replays_its_row_after_a_restart_and_a_changed_scope_does_not(tmp_path):
    first = cases.RealHost(tmp_path, key='op-1')
    made = first.session.record('shopping', result='failed', reason='error', trigger='manual', request_key='launch-1', command_id='c1')
    first.end_battle(status='interrupted')
    first.end_operation()
    second = cases.RealHost(tmp_path, key='op-2')
    again = second.session.record('shopping', result='failed', reason='error', trigger='manual', request_key='launch-1', command_id='c1')
    assert again['id'] == made['id'] and again.get('replayed') is True and len(rows_of(second)) == 1
    assert second.session.begin('shopping', request_key='launch-1') == made['id']                      # a host that restarts and begins the same key gets the same row back, opens nothing
    assert len(rows_of(second)) == 1 and not [row for row in rows_of(second) if row['state'] == 'running']


def test_the_settlement_of_a_dead_hosts_row_is_visible_to_the_validator_as_clean_history(tmp_path):
    host = cases.RealHost(tmp_path)
    host.session.begin('ad_gems')
    host.end_battle(status='interrupted')
    host.end_operation()
    stale = action_history.validate_history(host.battle_record(), rows_of(host))
    assert {entry['id']: entry['rules'] for entry in stale['flagged']} == {rows_of(host)[0]['id']: ['stale_running']}
    actions_api.settle(tmp_path)
    settled = action_history.validate_history(host.battle_record(), rows_of(host))
    assert settled['status'] == 'ok' and settled['items'][0]['state'] == 'interrupted'


# ------------------------------------------------------------------ the daily flag: restart reads it; a stop before it is not success
class Stopped(BaseException):
    """A process dying inside a step: nothing in the dispatcher catches a BaseException."""


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temporary daystate, a frozen local clock and one real chore, over a real operation-scoped session. Nothing here touches the machine's state."""
    from runtime.action_session import ActionSession
    monkeypatch.setattr(daystate, 'STATE', str(tmp_path / 'logs' / 'daily_state.json'))
    now = datetime.datetime(2026, 9, 10, 9, 0)
    moment = SimpleNamespace(value=now.timestamp())
    monkeypatch.setattr(daystate, '_today', lambda: now.date().isoformat())
    monkeypatch.setattr(chores, 'time', SimpleNamespace(time=lambda: moment.value))
    monkeypatch.setattr(chores, '_policy', lambda: {})
    calls = []
    registry = [('quest_scan', lambda: chores._daily('quest_scan'), lambda: calls.append('quest_scan') or 'scanned')]
    monkeypatch.setattr(chores, 'CHORES', registry)
    chores._unsaved.clear()
    host = cases.RealHost(tmp_path, driver='orchestrator')
    op_session = ActionSession(str(host.path), context=dict(device_key='device', account='account', instance='main', profile='mine'), driver='orchestrator',
                               operation_id=host.operation['id'], clock=cases.SteppedClock())
    return SimpleNamespace(root=tmp_path, host=host, session=op_session, calls=calls, path=tmp_path / 'logs' / 'daily_state.json', rows=lambda: Actions.read(str(host.path), device='device', action='quest_scan')[0])


def restart(world, monkeypatch):
    """A new process: the in-process memory of the dispatcher is gone; the file and the ledger are what is left."""
    from runtime.action_session import ActionSession
    chores._unsaved.clear()
    world.session = ActionSession(str(world.host.path), context=dict(device_key='device', account='account', instance='main', profile='mine'), driver='orchestrator',
                                  operation_id=world.host.operation['id'], clock=cases.SteppedClock())


def test_a_chore_that_completes_closes_its_row_and_then_its_day_and_a_restart_reads_the_flag(world, monkeypatch):
    assert chores.run_due(1, session=world.session) == ['quest_scan']
    (row,) = world.rows()
    assert row['state'] == 'succeeded' and row['outcome']['effect'] == 'unknown' and row['occurrence_key'] == '2026-09-10' and daystate.get_raw('chore_quest_scan') == '2026-09-10'
    restart(world, monkeypatch)
    assert chores.run_due(1, session=world.session) == [] and world.calls == ['quest_scan'] and len(world.rows()) == 1


def test_a_stop_between_the_finished_action_and_the_day_flag_leaves_a_finished_row_and_no_flag_and_resends_nothing_in_that_process(world, monkeypatch):
    """The audit's missing stop point. The row is finished first (R-5), the flag is written second; a process that dies between the two leaves the row and not the flag."""
    def die(name):
        raise Stopped(name)
    monkeypatch.setattr(chores, '_mark', die)
    with pytest.raises(Stopped):
        chores.run_due(1, session=world.session)
    (row,) = world.rows()
    assert row['state'] == 'succeeded' and row['outcome']['effect'] == 'unknown' and row['outcome']['effect'] != 'confirmed'      # the game effect was never proven
    assert daystate.get_raw('chore_quest_scan') is None and world.calls == ['quest_scan']                                      # no flag: nothing invented, nothing sent again by the dying process
    world.host.end_operation()
    assert actions_api.settle(world.root) == []                                                                                    # even with its operation over, a finished row has nothing to settle


def test_after_that_stop_a_restart_makes_at_most_one_extra_attempt_and_the_history_shows_both(world, monkeypatch):
    """Spec 3.1: across a restart the worst case is one extra attempt, which is today's crash behaviour. It is asserted so it cannot grow."""
    original = chores._mark
    monkeypatch.setattr(chores, '_mark', lambda name: (_ for _ in ()).throw(Stopped(name)))
    with pytest.raises(Stopped):
        chores.run_due(1, session=world.session)
    monkeypatch.setattr(chores, '_mark', original)
    restart(world, monkeypatch)
    assert chores.run_due(1, session=world.session) == ['quest_scan']                                                              # the one extra attempt
    assert chores.run_due(1, session=world.session) == [] and chores.run_due(1, session=world.session) == []                        # and then the flag holds
    rows = world.rows()
    assert world.calls == ['quest_scan', 'quest_scan'] and len(rows) == 2 and {row['occurrence_key'] for row in rows} == {'2026-09-10'}
    assert [row['state'] for row in rows] == ['succeeded', 'succeeded'] and daystate.get_raw('chore_quest_scan') == '2026-09-10'


def test_a_stop_before_the_row_is_closed_leaves_it_running_for_the_refresher_and_no_flag(world, monkeypatch):
    monkeypatch.setattr(chores, '_close_row', lambda recorder, name, completed: (_ for _ in ()).throw(Stopped(name)))
    with pytest.raises(Stopped):
        chores.run_due(1, session=world.session)
    (row,) = world.rows()
    assert row['state'] == 'running' and daystate.get_raw('chore_quest_scan') is None
    world.host.end_operation()
    assert actions_api.settle(world.root) == [row['id']]
    (settled,) = world.rows()
    assert settled['state'] == 'interrupted' and settled['duration_ms'] is None and settled['outcome']['effect'] == 'unknown'


def test_a_flag_the_file_refused_is_kept_in_the_process_and_a_restart_forgets_it_which_is_the_old_crash_behaviour(world, monkeypatch):
    monkeypatch.setattr(daystate, 'mark_today', lambda key: (_ for _ in ()).throw(daystate.DaystateLockTimeout('held')))
    assert chores.run_due(1, session=world.session) == ['quest_scan'] and chores.run_due(1, session=world.session) == []        # this process: not repeated
    assert chores._unsaved == {'quest_scan': '2026-09-10'} and daystate.get_raw('chore_quest_scan') is None
    monkeypatch.undo()
    restart(world, monkeypatch)
    assert chores._unsaved == {}
