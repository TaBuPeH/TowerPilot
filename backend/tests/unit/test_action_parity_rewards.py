"""Cadence parity of the reward upkeep (roadmap item 7, A3a: rows R-1 to R-4).

fixtures/action_a3a_golden_v1.json was captured BEFORE the producers were replaced by schedule policies (`captured_on` holds the hashes of the callers it
pinned; `action_a3a_golden` replays the same scenarios, so a changed cadence, guard or random draw order fails). The assertions beside the replays state the
rules in plain numbers, so a regeneration of the fixture cannot quietly make the contract mean something else.
"""
import pytest

import action_a3a_golden as golden


@pytest.fixture(scope='module')
def captured():
    return golden.load()


@pytest.fixture(scope='module')
def replayed():
    return {'reward_clocks': golden.reward_clock_traces(), 'store': golden.store_traces(), 'rewards': golden.reward_traces(), 'fuzz': golden.fuzz_traces(),
            'rewards_extra': golden.extra_reward_traces(), 'rewards_finishing': golden.finishing_reward_traces()}


@pytest.mark.parametrize('family', ['reward_clocks', 'store'])
def test_the_account_clocks_replay_the_captured_traces(captured, replayed, family):
    assert golden.roundtrip(replayed[family]) == captured[family]


@pytest.mark.parametrize('family', ['rewards', 'rewards_extra', 'rewards_finishing'])
def test_every_reward_loop_scenario_replays_the_captured_trace(captured, replayed, family):
    assert sorted(replayed[family]) == sorted(captured[family])
    for name in captured[family]:
        assert golden.roundtrip(replayed[family][name]) == captured[family][name], name


def test_the_fuzzed_sequences_replay_the_captured_traces_draw_for_draw(captured, replayed):
    assert sorted(replayed['fuzz']) == sorted(captured['fuzz'])
    for seed in captured['fuzz']:
        assert golden.roundtrip(replayed['fuzz'][seed]) == captured['fuzz'][seed], seed


def test_the_fixture_still_names_the_callers_it_was_captured_on(captured):
    assert set(captured['captured_on']) == {'backend/scheduling/global_rewards.py', 'backend/scheduling/chores.py', 'backend/orchestrator.py',
                                            'backend/scheduling/housekeeping.py', 'backend/scheduling/daystate.py', 'backend/interactions/missions.py'}


# ------------------------------------------------------------------ the rules, in numbers
def test_a_global_check_is_due_from_exactly_300_seconds_after_the_last_one_and_per_behaviour(captured):
    rows = captured['reward_clocks']['alice']
    for name, start, seq, after, other, _ in rows:
        before = {at: due for at, due in seq}
        assert before[0] is True and before[1] is True                   # nothing stored: due at once
        late = {at: due for at, due in after}
        assert late[0] is False and late[299] is False and late[299.999] is False
        assert late[300] is True and late[300.001] is True
        assert all(due is True for _, due in other if _ != name) or True  # the others are not touched by this one's check
        others = {behaviour: due for behaviour, due in other}
        assert all(due is True for behaviour, due in others.items() if behaviour != name)


def test_the_free_store_opens_between_minus_ten_and_plus_ten_minutes_around_0100_utc_and_a_claim_closes_the_day(captured):
    for account, rows in captured['store'].items():
        for row in rows:
            if row[0] == 'claimed':
                continue
            day, first, grid, closed, nxt = row
            assert 3000 <= first <= 4200, (account, day, first)           # 3600 s +/- 600 s after UTC midnight
            by = {s: due for s, due in grid}
            assert by[0] is False and by[first - 1] is False and by[first] is True and by[43200] is True
            assert all(due is False for _, due in closed)                  # the claim closes that UTC day
            assert dict(nxt)[86400] is False                               # and the next day opens again only at its own offset


def test_a_failed_store_attempt_waits_300_seconds_and_a_confirmed_one_ends_the_day(captured):
    unavailable = captured['rewards']['store_unavailable_retries']
    starts = [step[0] for step in unavailable if step[2]['started'] == ['free_gems_flow']]
    assert len(starts) >= 2
    assert all(later - earlier >= 300 for earlier, later in zip(starts, starts[1:])), starts
    confirmed = captured['rewards']['store_confirmed_closes_the_day']
    claim_at = next(step[0] for step in confirmed if step[1] == 'end')
    same_day = [step for step in confirmed if claim_at < step[0] < 86400 and step[2]['started'] == ['free_gems_flow']]
    next_day = [step for step in confirmed if step[0] >= 86400 and step[2]['started'] == ['free_gems_flow']]
    assert not same_day and next_day                      # the claim closes that UTC day only; the next one opens again


def raised_at(steps):
    return [step[0] for step in steps if step[2]['pending_store']]


def stores_started(steps):
    return [step[0] for step in steps if step[2]['started'] == ['store_flow']]


def test_a_guild_visit_that_claimed_raises_the_store_follow_up_which_runs_once_and_is_consumed(captured):
    steps = captured['rewards_finishing']['guild_claims_raise_the_follow_up_and_it_runs_once']
    assert raised_at(steps) == [1200.0] and len(stores_started(steps)) == 1                       # raised by the event, started on the next pass, never again
    assert steps[-1][2]['pending_store'] is False and steps[-1][2]['await_guild_result'] is False


def test_nothing_raises_the_follow_up_without_a_claim_the_gather_switch_or_a_guild_visit(captured):
    for name in ('guild_without_claims_raises_nothing', 'guild_claims_but_the_store_gather_is_off', 'quest_visit_ends_with_no_follow_up', 'global_guild_check_claims_raise_no_follow_up'):
        steps = captured['rewards_finishing'][name]
        assert not raised_at(steps) and not stores_started(steps), name


def test_a_raised_follow_up_waits_for_the_shopping_sweep_to_end(captured):
    steps = captured['rewards_finishing']['guild_claims_while_shopping_wait_for_the_sweep']
    assert raised_at(steps)[:3] == [1200.0, 1205.0, 1210.0]
    after_sweep = [step[0] for step in steps if step[0] > 1210.0 and step[2]['started'] == ['store_flow']]
    assert len(after_sweep) == 1 and after_sweep[0] >= 1215.0


def test_a_confirmed_store_claim_inside_the_flow_closes_the_day_and_an_unconfirmed_end_retries_after_300_s(captured):
    closed = captured['rewards_finishing']['store_claim_confirmed_inside_the_flow_closes_the_day']
    assert len([1 for step in closed if step[2]['started'] == ['free_gems_flow']]) == 1                  # one attempt; the confirmed claim ends the day's attempts
    assert closed[-1][2]['daystate'].get('global_rewards:alice:store') == '2026-09-10'
    retried = captured['rewards_finishing']['store_flow_ends_without_a_confirmed_claim_and_retries_after_300_s']
    starts = [step[0] for step in retried if step[2]['started'] == ['free_gems_flow']]
    assert len(starts) == 2 and starts[1] - starts[0] >= 300 and 'global_rewards:alice:store' not in retried[-1][2]['daystate']


def visit_ranges(steps):
    return [(draw[1], draw[2]) for _, _, view in steps for draw in view['draws'] if (draw[1], draw[2]) != (0.15, 0.55)]   # the step spacing of a running flow is not a delay


def test_a_visit_within_1200_seconds_of_the_last_one_draws_from_1500_to_2400_and_any_other_from_the_configured_range(captured):
    extra = captured['rewards_extra']
    assert set(visit_ranges(captured['rewards']['visit_quests'])) == {(60, 600)}
    assert visit_ranges(extra['recent_visit_1199_999_s_ago']) == [(1500, 2400)]          # strictly less than 1200 s ago
    assert visit_ranges(extra['recent_visit_1200_s_ago']) == [(60, 600)]                 # exactly 1200 s ago is not recent
    assert visit_ranges(extra['recent_visit_1200_001_s_ago']) == [(60, 600)]
    assert visit_ranges(extra['recent_guild_visit_600_s_ago']) == [(1500, 2400)]
    assert visit_ranges(extra['recent_visit_of_the_other_badge_does_not_count']) == [(60, 600)]
    assert visit_ranges(extra['recent_repeat_after_a_real_visit'])[-1] == (1500, 2400)    # a badge that persists after a visit is not ping-ponged
