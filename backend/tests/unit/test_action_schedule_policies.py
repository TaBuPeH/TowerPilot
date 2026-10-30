"""The schedule policies of item 7, A3a: Cooldown, DailyCalendar and EventOnly (pure decisions; the callers' parity is test_action_parity_rewards and test_chores_gap_policy)."""
import datetime
import math

import pytest

import denver_zone
from towerpilot_workflows.action_contract import (CATALOG, CHORE_SCHEDULE, REWARD_CHECK, STORE_SCHEDULE, VISIT_SCHEDULE, Cooldown, DailyCalendar, EventOnly, use_catalog)

utc = denver_zone.utc


# ----------------------------------------------------------------------- Cooldown
def cooldown(now, next_at=None, policy=REWARD_CHECK):
    return policy.evaluate(now, {} if next_at is None else {'next_at': next_at})[0]


def test_a_cooldown_with_nothing_stored_is_due_at_once_with_an_unknown_due_time():
    decision = cooldown(5.0)
    assert (decision.kind, decision.eligible_at, decision.next_due_at) == ('due', None, None)
    assert cooldown(0.0).kind == 'due'                                  # zero stored is "nothing owed", not a time in the past


@pytest.mark.parametrize('now,kind', [(1299.999, 'wait'), (1300.0, 'due'), (1300.001, 'due')])
def test_a_cooldown_is_due_from_exactly_its_time(now, kind):
    decision = cooldown(now, 1300.0)
    assert decision.kind == kind
    assert (decision.next_due_at == 1300.0) if kind == 'wait' else (decision.eligible_at == 1300.0)


def test_the_next_time_is_a_fixed_floor_or_the_hosts_sampled_delay():
    assert REWARD_CHECK.after(1000.0) == 1300.0 and REWARD_CHECK.floor_sec == 300
    assert VISIT_SCHEDULE.after(1000.0, 61.5) == 1061.5 and VISIT_SCHEDULE.after(1000.0, 0.0) == 1000.0    # a delay of zero is a delay, not "use the floor"


def test_a_recent_occurrence_is_strictly_inside_the_window_and_never_without_a_last_time():
    assert Cooldown.recent(2000.0, 800.001, 1200) is True
    assert Cooldown.recent(2000.0, 800.0, 1200) is False and Cooldown.recent(2000.0, 799.0, 1200) is False
    assert Cooldown.recent(2000.0, None, 1200) is False
    assert Cooldown.recent(2000.0, 2000.0, 1200) is True                 # just now


def test_the_delay_range_is_the_recent_one_only_inside_the_window():
    usual = (60, 600)
    assert VISIT_SCHEDULE.delay_range(2000.0, 900.0, usual) == (1500, 2400)
    assert VISIT_SCHEDULE.delay_range(2000.0, 800.0, usual) == usual
    assert VISIT_SCHEDULE.delay_range(2000.0, None, usual) == usual
    assert REWARD_CHECK.delay_range(2000.0, 1999.0, usual) == usual      # a policy with no recent delay always uses the host's range


@pytest.mark.parametrize('now', [math.nan, math.inf, -math.inf, -1.0, -0.001, '5', None, True, [1]])
def test_an_unreadable_clock_is_blocked_never_read_as_zero(now):
    decision = cooldown(now, 100.0)
    assert (decision.kind, decision.reason) == ('blocked', 'invalid_state')


@pytest.mark.parametrize('next_at', [math.nan, math.inf, -math.inf, '5', [1], True])
def test_an_unreadable_stored_time_is_blocked_never_due(next_at):
    assert cooldown(1000.0, next_at).kind == 'blocked'


def test_a_negative_stored_time_is_a_finite_time_in_the_past_so_a_repaired_value_is_due():
    assert cooldown(10.0, -50.0).kind == 'due'                           # the old comparison did the same; a stale negative must not suspend checks for good


def test_evaluate_never_changes_the_state_it_was_given():
    state = {'next_at': 100.0}
    REWARD_CHECK.evaluate(1000.0, state)
    VISIT_SCHEDULE.evaluate(1000.0, state)
    assert state == {'next_at': 100.0}


# -------------------------------------------------------------- DailyCalendar, UTC
def store(now, consumed=None, *, seed='global_rewards:alice:store', retry_at=None, retry_now=None):
    state = {'consumed': consumed, 'retry_at': retry_at}
    conditions = {'seed': seed} | ({} if retry_now is None else {'retry_now': retry_now})
    return STORE_SCHEDULE.evaluate(now, state, conditions)[0]


def test_the_store_offset_is_stable_per_seed_and_day_inside_ten_minutes_and_differs_between_them():
    offsets = {(seed, day): STORE_SCHEDULE.offset(seed, day) for seed in ('a', 'b', 'c') for day in ('2026-09-10', '2026-09-11', '2026-09-12', '2026-09-13')}
    assert all(-600 <= value <= 600 for value in offsets.values())
    assert STORE_SCHEDULE.offset('a', '2026-09-10') == offsets[('a', '2026-09-10')]          # the same inputs, the same answer, in any process
    assert len(set(offsets.values())) > 6                                                    # not a constant
    assert CHORE_SCHEDULE.offset('anything', '2026-09-10') == 0                              # no spread: no offset


def test_the_store_opens_one_hour_after_utc_midnight_plus_its_offset_and_a_claim_closes_the_day():
    day = '2026-09-10'
    opens = STORE_SCHEDULE.eligible_at(day, 'global_rewards:alice:store')
    assert opens == utc(2026, 9, 10) + 3600 + STORE_SCHEDULE.offset('global_rewards:alice:store', day)
    before, at = store(opens - 0.001), store(opens)
    assert (before.kind, before.reason, before.next_due_at, before.occurrence_key) == ('wait', 'before_floor', opens, day)
    assert (at.kind, at.occurrence_key, at.eligible_at) == ('due', day, opens)
    closed = store(opens + 5, consumed=day)
    assert (closed.kind, closed.reason) == ('wait', 'consumed')
    assert closed.next_due_at == STORE_SCHEDULE.eligible_at('2026-09-11', 'global_rewards:alice:store')     # tomorrow's own opening


def test_a_claim_of_one_utc_day_does_not_close_the_next_one_and_the_day_rolls_at_utc_midnight_not_local():
    midnight = utc(2026, 9, 11)
    assert STORE_SCHEDULE.day(midnight - 1) == '2026-09-10' and STORE_SCHEDULE.day(midnight) == '2026-09-11'
    assert store(midnight + 3600 + 700, consumed='2026-09-10').kind == 'due'         # yesterday's claim; the offset is at most 600 s


def test_a_failed_attempt_waits_for_the_hosts_retry_gate_which_runs_on_its_own_clock():
    opens = STORE_SCHEDULE.eligible_at('2026-09-10', 'k')
    retry_at = STORE_SCHEDULE.attempt(500.0)
    assert retry_at == 800.0
    waiting = store(opens + 10, seed='k', retry_at=retry_at, retry_now=799.999)
    assert (waiting.kind, waiting.reason, waiting.next_attempt_at) == ('wait', 'retry', 800.0)
    assert store(opens + 10, seed='k', retry_at=retry_at, retry_now=800.0).kind == 'due'      # `now >= retry_at`, the old comparison
    assert store(opens + 10, seed='k', retry_at=None, retry_now=0.0).kind == 'due'


def test_leap_day_and_year_end_keys():
    assert STORE_SCHEDULE.day(utc(2028, 2, 29, 23, 59, 59)) == '2028-02-29' and STORE_SCHEDULE.day(utc(2028, 3, 1)) == '2028-03-01'
    assert STORE_SCHEDULE.day(utc(2026, 12, 31, 23, 59, 59)) == '2026-12-31' and STORE_SCHEDULE.day(utc(2027, 1, 1)) == '2027-01-01'
    assert store(utc(2028, 3, 1, 0, 30), consumed='2028-02-29').reason == 'before_floor'        # the 29th's claim, a new day that has not opened yet
    decision = store(utc(2026, 12, 31, 23, 59, 59), consumed='2026-12-31')
    assert decision.next_due_at == STORE_SCHEDULE.eligible_at('2027-01-01', 'global_rewards:alice:store')


@pytest.mark.parametrize('now', [math.nan, math.inf, -1.0, -math.inf, 1e30, '5', None, True])
def test_the_store_blocks_on_a_clock_it_cannot_read_or_express(now):
    assert store(now).kind == 'blocked' and store(now).reason == 'invalid_state'


@pytest.mark.parametrize('retry_now', [math.nan, math.inf, -1.0])
def test_the_store_blocks_on_an_unreadable_retry_clock(retry_now):
    assert store(utc(2026, 9, 10, 12), retry_at=10.0, retry_now=retry_now).kind == 'blocked'


# --------------------------------------------------------------- DailyCalendar, local
DENVER = denver_zone.Denver()


def local(floor_hour, close_on='attempted'):
    return DailyCalendar('local', floor_hour=floor_hour, close_on=close_on, zone=DENVER)


def at(calendar, now, consumed=None):
    return calendar.evaluate(now, {'consumed': consumed})[0]


def test_the_chore_gap_opens_at_the_local_floor_hour_and_the_day_is_the_local_date():
    chores = DailyCalendar('local', floor_hour=3, close_on='attempted', zone=DENVER)
    assert chores.day(utc(2026, 9, 10, 8, 59, 59)) == '2026-09-10'          # 02:59:59 MDT
    assert at(chores, utc(2026, 9, 10, 8, 59, 59)).kind == 'wait' and at(chores, utc(2026, 9, 10, 9, 0, 0)).kind == 'due'      # 03:00:00 MDT
    assert chores.day(utc(2026, 9, 10, 5, 59, 59)) == '2026-09-09' and chores.day(utc(2026, 9, 10, 6, 0, 0)) == '2026-09-10'  # local midnight, not 03:00


def test_the_day_rolls_at_local_midnight_and_03_00_is_only_an_eligibility_floor():
    chores = local(3)
    closed = '2026-09-10'
    assert at(chores, utc(2026, 9, 11, 5, 59), closed).reason == 'consumed'                  # 23:59 local: still the closed day
    after_midnight = at(chores, utc(2026, 9, 11, 6, 30), closed)                              # 00:30 local on the 11th: a new day that has not opened
    assert (after_midnight.kind, after_midnight.reason, after_midnight.occurrence_key) == ('wait', 'before_floor', '2026-09-11')
    assert at(chores, utc(2026, 9, 11, 9, 0), closed).kind == 'due'                           # 03:00 on the 11th


def test_a_calendar_reads_the_zone_it_was_given_not_the_machines():
    plus5 = datetime.timezone(datetime.timedelta(hours=5))
    calendar = DailyCalendar('local', floor_hour=3, close_on='attempted', zone=plus5)
    just_before = utc(2026, 9, 10, 21, 59, 59)                           # 02:59:59 on the 11th at UTC+5
    assert calendar.day(just_before) == '2026-09-11' and at(calendar, just_before).kind == 'wait'
    assert at(calendar, just_before + 1).kind == 'due'
    assert calendar.eligible_at('2026-09-11') == utc(2026, 9, 10, 22)


def test_a_skipped_hour_still_makes_a_chore_eligible_once_the_local_hour_passes_the_floor():
    # 2026-03-08: at 02:00 MST the clocks go to 03:00 MDT. A floor of 02:00 is never read as a wall time; the first reading at or past it is 03:00 MDT.
    chores = local(2)
    assert at(chores, utc(2026, 3, 8, 8, 59, 59)).kind == 'wait'              # 01:59:59 MST
    assert at(chores, utc(2026, 3, 8, 9, 0, 0)).kind == 'due'                  # 03:00:00 MDT
    shipped = local(3)
    assert at(shipped, utc(2026, 3, 8, 9, 0, 0)).kind == 'due' and at(shipped, utc(2026, 3, 8, 8, 59, 59)).kind == 'wait'


def test_a_repeated_hour_cannot_grant_a_second_daily_occurrence():
    # 2026-11-01: 01:00-02:00 happens twice (MDT, then MST). A floor of 01:00 opens in the first pass; closing that day keeps it closed in the second.
    chores = local(1)
    first_pass, second_pass = utc(2026, 11, 1, 7, 30), utc(2026, 11, 1, 8, 30)                # both read 01:30 on the wall clock
    assert datetime.datetime.fromtimestamp(first_pass, DENVER).strftime('%H:%M') == datetime.datetime.fromtimestamp(second_pass, DENVER).strftime('%H:%M') == '01:30'
    assert at(chores, first_pass).kind == 'due'
    day = chores.day(first_pass)
    assert day == chores.day(second_pass) == '2026-11-01'
    assert at(chores, second_pass, consumed=day).kind == 'wait' and at(chores, second_pass, consumed=day).reason == 'consumed'
    assert at(chores, utc(2026, 11, 1, 6, 59)).kind == 'wait'                                  # 00:59 MDT: before the floor


def test_the_25_hour_day_and_the_23_hour_day_each_have_exactly_one_eligible_window():
    chores = local(3)
    for day_start, day_end, hours in ((utc(2026, 11, 1, 6), utc(2026, 11, 2, 7), 25), (utc(2026, 3, 8, 7), utc(2026, 3, 9, 6), 23)):
        assert (day_end - day_start) / 3600 == hours
        due_hours = [t for t in range(int(day_start), int(day_end), 1800) if at(chores, t, consumed=None).kind == 'due']
        assert due_hours and due_hours[0] == int(utc(2026, 11, 1, 10) if hours == 25 else utc(2026, 3, 8, 9))     # 03:00 local in both
        key = {chores.day(t) for t in due_hours}
        assert len(key) == 1                                                                   # one day key, however long the day is


# ------------------------------------------------------------------------ EventOnly
def test_an_event_action_is_due_only_while_its_event_is_pending_and_never_has_a_time():
    policy = EventOnly('guild_claim')
    waiting, _ = policy.evaluate(100.0, {'pending': False})
    due, _ = policy.evaluate(100.0, {'pending': True})
    assert (waiting.kind, waiting.next_due_at, waiting.next_attempt_at, waiting.reason) == ('wait', None, None, 'guild_claim')
    assert (due.kind, due.reason) == ('due', 'guild_claim')
    assert policy.evaluate(100.0, {})[0].kind == 'wait'                                      # no state is not an event


def test_raising_and_consuming_an_event_return_a_new_state():
    state = {'pending': False, 'other': 1}
    raised = EventOnly.raised(state)
    assert raised == {'pending': True, 'other': 1} and state == {'pending': False, 'other': 1}
    assert EventOnly.consumed(raised) == {'pending': False, 'other': 1} and raised['pending'] is True


# ------------------------------------------------------------------- the catalog
def test_the_reward_and_chore_actions_share_their_schedules_and_say_whose_state_they_keep():
    for name in ('daily_missions', 'guild_progress', 'event_missions'):
        assert CATALOG.get(name).schedule is REWARD_CHECK and CATALOG.get(name).scope == 'account'
    assert CATALOG.get('quest_visit').schedule is CATALOG.get('guild_visit').schedule is VISIT_SCHEDULE
    assert CATALOG.get('free_store_gems').schedule is STORE_SCHEDULE and CATALOG.get('free_store_gems').consumes == 'confirmed'
    for name in ('quest_scan', 'shatter', 'housekeeping'):
        definition = CATALOG.get(name)
        assert definition.schedule is CHORE_SCHEDULE and definition.consumes == 'attempted' and definition.scope == 'installation'
    assert CATALOG.get('guild_store').schedule.basis == 'event' and CATALOG.get('quest_visit').scope is None


def test_none_of_these_actions_offers_a_control_run_now_or_a_saved_setting():
    for name in ('daily_missions', 'guild_progress', 'event_missions', 'quest_visit', 'guild_visit', 'guild_store', 'free_store_gems', 'quest_scan', 'shatter', 'housekeeping'):
        definition = CATALOG.get(name)
        assert not definition.fields and definition.saved is None and definition.capabilities.run_now is False and not definition.capabilities.live_controls, name
        assert definition.hosts == frozenset({'orchestrator'}), name


def test_a_pilot_catalog_swap_does_not_reach_the_policies_the_hosts_share():
    copy = CATALOG.copy()
    with use_catalog(copy):
        assert copy.get('free_store_gems').schedule is STORE_SCHEDULE
