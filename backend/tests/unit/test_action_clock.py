"""Clock behaviour of the timed-action history and its schedules (spec 3.6, "Clock"). Fake clocks only: nothing waits, nothing reads the machine's zone.

What is pinned here is the seam between a clock and the history: wall time is recorded as measured and never trusted alone, durations are monotonic, an unreadable
reading becomes unknown (never zero), and the calendar day an occurrence is filed under is the policy's, whatever the clock does. The policies' own DST cases are in
`test_action_schedule_policies.py` (a skipped hour, a repeated hour, the 25-hour day): not repeated. Their pure arithmetic is not re-tested either; what is added is what a stepped or
unreadable clock does to the rows a host writes and to the decision of the policies that have no reading guard of their own.
"""
import datetime
import math

import pytest

import action_history_cases as cases
import denver_zone
from towerpilot_workflows import action_history
from towerpilot_workflows.action_contract import (CHORE_SCHEDULE, REWARD_CHECK, STORE_SCHEDULE, AfterCompletion, AppearanceDriven, Backoff, Cooldown, DailyCalendar,
                                                  StartToStart)
from towerpilot_workflows.actions import Actions, duration_or_none, finite_or_none

DENVER = denver_zone.Denver()
utc = denver_zone.utc


def session(tmp_path, clock, **kwargs):
    from runtime.action_session import ActionSession
    return ActionSession(str(tmp_path / 'ledger.db'), context=dict(device_key='device', account='account', instance='main'), driver='orchestrator', clock=clock, **kwargs)


def only_row(tmp_path):
    (row,) = Actions.read(str(tmp_path / 'ledger.db'))[0]
    return row


# ------------------------------------------------------------------ wall steps never change a measured duration
@pytest.mark.parametrize('step', [-86400.0, -1.0, 0.0, 3600.0, 10 * 86400.0], ids=['day-back', 'second-back', 'none', 'hour-forward', 'ten-days-forward'])
def test_a_wall_step_inside_an_execution_does_not_change_its_monotonic_duration(tmp_path, step):
    clock = cases.SteppedClock(mono=1000.0, wall=1_700_000_000.0)
    s = session(tmp_path, clock)
    execution_id = s.begin('ad_gems')
    clock.advance_mono(12.5)
    clock.step_wall(step)
    s.finish(execution_id, result='no_op', reason='x')
    row = only_row(tmp_path)
    assert row['duration_ms'] == 12500.0 and row['duration_basis'] == 'monotonic'
    assert row['started_at'] == 1_700_000_000.0 and row['finished_at'] == 1_700_000_000.0 + step     # the wall times are what was measured, not corrected


def test_a_wall_clock_that_ran_backwards_is_a_stepped_clock_for_the_validator_not_a_zero_length_overlap(tmp_path):
    clock = cases.SteppedClock(mono=0.0, wall=2000.0)
    host = cases.RealHost(tmp_path, clock=clock)
    first = host.session.begin('ad_gems')
    clock.advance_mono(20.0)
    clock.set_wall(100.0)                                                                           # the wall clock is corrected to long before the execution began
    host.session.finish(first, result='no_op', reason='x')
    clock.advance_mono(10.0)
    second = host.session.begin('ad_gems')
    host.session.finish(second, result='no_op', reason='y')
    rows = host.rows()
    report = action_history.validate_history(host.battle_record(), rows)
    assert report['status'] == 'ok' and report['rejected'] == []
    assert 'clock_step' in {entry['id']: entry['notes'] for entry in report['notes']}[first]       # finished (wall) before it started: annotated, a duration still measured
    assert {row['id']: row['duration_ms'] for row in rows} == {first: 20000.0, second: 0.0} and report['flagged'] == []


def test_a_late_poll_after_a_long_sleep_opens_nothing_it_does_not_owe(tmp_path):
    """Sleep/wake: the monotonic clock may jump by hours. A fixed cadence skips the windows it missed and owes no burst; an appearance-driven action owes nothing for a gap."""
    s2s = StartToStart()
    state = dict(next_window_at=100.0, window_open=False)
    conditions = dict(interval=90.0, skip_duration=30.0)
    woke = 100.0 + 5 * 3600 + 17.0
    decision, state = s2s.evaluate(woke, state, conditions)
    assert decision.kind == 'skip_missed' and decision.missed == math.floor((woke - 100.0) / 90.0) + 1 and state['next_window_at'] > woke
    assert state['next_window_at'] - woke <= 90.0                                                   # the next window is the NEXT one on the grid, not an owed one
    again, state = s2s.evaluate(woke, state, conditions)
    assert again.kind == 'wait' and again.missed == 0                                               # and nothing follows it: no catch-up burst
    manual, _ = s2s.evaluate(woke, dict(next_window_at=100.0, window_open=False), dict(conditions, manual=True))
    assert manual.kind == 'due'                                                                     # an explicit request is never skipped
    appearance = AppearanceDriven()
    quiet, _ = appearance.evaluate(woke, dict(next_window_at=0.0, window_open=False), dict(appearance=False))
    seen, _ = appearance.evaluate(woke, dict(next_window_at=0.0, window_open=False), dict(appearance=True))
    assert (quiet.kind, quiet.missed, seen.kind) == ('wait', 0, 'due')                              # nothing is owed for the gap; a recognized gem opens an attempt


# ------------------------------------------------------------------ unreadable readings are unknown, never zero
@pytest.mark.parametrize('reading', [math.nan, math.inf, -math.inf], ids=['nan', 'inf', '-inf'])
def test_a_wall_clock_that_cannot_be_read_records_nothing_rather_than_a_time_made_up(tmp_path, reading):
    clock = cases.SteppedClock(wall=1_700_000_000.0)
    s = session(tmp_path, clock)
    clock.set_wall(reading)
    assert s.begin('ad_gems') is None and s.check('shopping', result='no_op', reason='x') is None and s.unrecorded == {'begin': 1, 'check': 1}
    assert Actions.read(str(tmp_path / 'ledger.db'))[0] == []                                       # no row carries an invented start


@pytest.mark.parametrize('reading', [math.nan, math.inf, -math.inf, 400.0], ids=['nan', 'inf', '-inf', 'earlier-than-start'])
def test_a_monotonic_clock_that_cannot_give_a_length_leaves_the_duration_unknown_not_zero(tmp_path, reading):
    clock = cases.SteppedClock(mono=500.0, wall=1_700_000_000.0)
    s = session(tmp_path, clock)
    execution_id = s.begin('ad_gems')
    clock._mono = reading
    s.finish(execution_id, result='no_op', reason='x')
    row = only_row(tmp_path)
    assert row['duration_ms'] is None and row['duration_basis'] == 'none' and row['state'] == 'no_op' and s.last['ad_gems']['duration_ms'] is None


@pytest.mark.parametrize('value,expected', [(12.5, 12.5), (0, 0.0), (-0.001, None), (-1e9, None), (math.nan, None), (math.inf, None), ('3', None), (True, None), (None, None)])
def test_a_duration_is_a_finite_non_negative_number_or_unknown(value, expected):
    assert duration_or_none(value) == expected


def test_the_ledger_never_stores_a_negative_or_non_finite_duration_whoever_the_caller_is(tmp_path):
    store = Actions(str(tmp_path / 'ledger.db'))
    from towerpilot_workflows import actions
    for index, bad in enumerate((-5.0, math.nan, math.inf)):
        body = actions.new_record(device='device', action='ad_gems', instance=f'i{index}', trigger='automatic', started_at=1000.0 + index, host=dict(driver='orchestrator'), context=dict(account='account'))
        opened = store.begin(body)
        finished = store.finish(opened['id'], result='no_op', duration_ms=bad)
        direct = store.record_terminal(dict(body, id=f'direct{index}', instance=f'd{index}'), result='no_op', duration_ms=bad)
        assert finished['duration_ms'] is None == direct['duration_ms'] and finished['duration_basis'] == direct['duration_basis'] == 'none'
    assert finite_or_none(-5.0) == -5.0        # the general reader of a wall time is unchanged: a time before the epoch is a time, a length is not


# ------------------------------------------------------------------ calendar identity
def test_the_utc_day_of_the_store_rolls_at_utc_midnight_and_the_occurrence_is_filed_under_the_policys_day_not_the_hosts():
    last_second, first_second = utc(2026, 3, 8, 23, 59, 59), utc(2026, 3, 9, 0, 0, 0)
    assert STORE_SCHEDULE.day(last_second) == '2026-03-08' and STORE_SCHEDULE.day(first_second) == '2026-03-09'
    assert STORE_SCHEDULE.offset('seed', '2026-03-09') == STORE_SCHEDULE.offset('seed', '2026-03-09') and abs(STORE_SCHEDULE.offset('seed', '2026-03-09')) <= 600


def test_leap_days_and_the_end_of_a_leap_year_have_their_own_keys_in_both_calendars():
    assert [STORE_SCHEDULE.day(utc(2028, 2, 28, 12) + n * 86400) for n in range(3)] == ['2028-02-28', '2028-02-29', '2028-03-01']
    assert STORE_SCHEDULE.day(utc(2028, 12, 31, 23, 59, 59)) == '2028-12-31' and STORE_SCHEDULE.day(utc(2029, 1, 1)) == '2029-01-01'
    local = DailyCalendar('local', floor_hour=3, close_on='attempted', zone=DENVER)
    assert [local.day(utc(2028, 2, 29, 12) + n * 86400) for n in range(2)] == ['2028-02-29', '2028-03-01']


def test_a_repeated_hour_files_both_passes_under_one_day_key_so_a_host_that_recorded_both_is_caught_by_the_history(tmp_path):
    calendar = DailyCalendar('local', floor_hour=1, close_on='attempted', zone=DENVER)
    first_pass = datetime.datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=DENVER).timestamp()
    second_pass = datetime.datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=DENVER).timestamp()
    assert second_pass - first_pass == 3600.0 and calendar.day(first_pass) == calendar.day(second_pass) == '2026-11-01'
    decision, _ = calendar.evaluate(first_pass, {})
    assert decision.kind == 'due'
    consumed = calendar.evaluate(second_pass, {'consumed': calendar.day(first_pass)})[0]
    assert consumed.kind == 'wait' and consumed.reason == 'consumed'                                  # the policy does not grant the second pass
    record = cases.battle(origin='started')
    rows = [cases.execution(action='quest_scan', started_at=first_pass, duration_ms=1000.0, occurrence_key=calendar.day(first_pass), effect='unknown', reason='completed'),
            cases.execution(action='quest_scan', started_at=second_pass, duration_ms=1000.0, occurrence_key=calendar.day(second_pass), effect='unknown', reason='completed')]
    report = action_history.validate_history(record, rows)
    assert {entry['id']: entry['rules'] for entry in report['flagged']} == {rows[1]['id']: ['duplicate_occurrence']}


def test_a_skipped_hour_still_opens_the_day_once_the_local_hour_passes_the_floor_and_files_one_key():
    calendar = DailyCalendar('local', floor_hour=3, close_on='attempted', zone=DENVER)
    spring_before, spring_after = utc(2026, 3, 8, 8, 59), utc(2026, 3, 8, 9, 0)                        # 01:59 MST, then 03:00 MDT: 02:00-03:00 does not exist
    assert calendar.evaluate(spring_before, {})[0].kind == 'wait' and calendar.evaluate(spring_after, {})[0].kind == 'due'
    assert calendar.day(spring_before) == calendar.day(spring_after) == '2026-03-08'


# ------------------------------------------------------------------ policies that read a clock
@pytest.mark.parametrize('policy', [REWARD_CHECK, STORE_SCHEDULE, CHORE_SCHEDULE], ids=['reward-check', 'store', 'chores'])
@pytest.mark.parametrize('reading', [math.nan, math.inf, -math.inf, -1.0], ids=['nan', 'inf', '-inf', 'negative'])
def test_the_calendar_and_cooldown_policies_block_on_an_unreadable_clock_never_due_and_never_zero(policy, reading):
    state = {} if not isinstance(policy, DailyCalendar) else {'consumed': None}
    decision = policy.evaluate(reading, state, {} if isinstance(policy, Cooldown) else {'seed': 's'})[0]
    assert decision.kind == 'blocked' and decision.reason == 'invalid_state'


# The four policies without a calendar of their own refuse an unreadable reading exactly as Cooldown does: `blocked`/`invalid_state` with their own basis, the state returned
# unchanged, and no exception into the host. Each policy is given a state in which a good reading would have decided something other than `blocked`.
READING_GUARDED = [
    pytest.param(AppearanceDriven(), dict(next_window_at=100.0, window_open=False), dict(appearance=True), id='appearance-driven'),
    pytest.param(AfterCompletion(), dict(last_completed_at=10.0), dict(interval=60.0), id='after-completion'),
    pytest.param(Backoff(), dict(newest_created_at=1.0, newest_updated_at=2.0, failures=1), {}, id='backoff'),
    pytest.param(StartToStart(), dict(next_window_at=100.0, window_open=False), dict(interval=60.0, skip_duration=30.0), id='start-to-start'),
]


@pytest.mark.parametrize('policy,state,conditions', READING_GUARDED)
@pytest.mark.parametrize('reading', [math.nan, math.inf, -math.inf, -1.0], ids=['nan', 'inf', '-inf', 'negative'])
def test_every_policy_refuses_an_unreadable_clock_reading_instead_of_deciding_on_it(policy, state, conditions, reading):
    before = dict(state)
    decision, after = policy.evaluate(reading, state, conditions)          # raising into the host would fail here
    assert (decision.kind, decision.reason, decision.basis) == ('blocked', 'invalid_state', policy.basis)
    assert decision.eligible_at is None and decision.next_due_at is None
    assert after == before and state == before


@pytest.mark.parametrize('policy,state,conditions', READING_GUARDED)
def test_a_readable_clock_reading_is_still_decided_not_refused(policy, state, conditions):
    """The guard is on the reading only: a finite non-negative `now` (zero included) never answers `invalid_state`."""
    for reading in (0.0, 1.0, 1e9):
        assert policy.evaluate(reading, state, conditions)[0].reason != 'invalid_state'
