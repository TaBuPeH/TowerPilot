"""Parity of the global Demon Mode behaviour (roadmap item 7, A3b kind B, row C-2).

fixtures/action_r18e/demon_mode_golden_v1.json was captured BEFORE the producer moved to the catalog's `Cooldown(15)`: on the orchestrator whose sha256 is
`captured_on` (the same bytes as the latency gate's pinned unmigrated snapshot, which this file re-runs below, so the fixture can be re-derived without trusting
the capture). A changed cadence, guard, stamp order or log line fails. The history tests describe only what is new: a row for a CONFIRMED fire, written after it.
"""
import random
import types

import pytest

import action_parity_r18e as shared
from towerpilot_workflows import action_contract


def _step(now, wave=2500, enabled=True, sprint=False, sprint_ended=False, fire=True):
    return dict(now=now, wave=wave, enabled=enabled, sprint=sprint, sprint_ended=sprint_ended, fire=fire)


SCENARIOS = {
    'confirmed_fire_then_the_15s_grain': [_step(100.0), _step(105.0), _step(114.5), _step(115.0), _step(115.5), _step(130.0)],
    'unconfirmed_fire_still_spaces_attempts': [_step(10.0, fire=False), _step(24.5, fire=True), _step(25.0, fire=True), _step(40.0, fire=False), _step(55.0)],
    'no_wave_never_stamps': [_step(5.0, wave=None), _step(5.5, wave=0), _step(6.0), _step(6.5)],
    'disabled_behaviour_never_stamps': [_step(1.0, enabled=False), _step(1.5, enabled=False), _step(2.0, enabled='raises'), _step(2.5)],
    'the_sprint_blocks_the_tap_but_not_the_stamp': [_step(50.0, sprint=True), _step(60.0, sprint=True), _step(65.0, sprint=True), _step(66.0, sprint=True, sprint_ended=True),
                                                    _step(80.0, sprint=True, sprint_ended=True)],
    'a_late_pass_after_a_long_gap': [_step(0.0), _step(900.0), _step(901.0), _step(915.0)],
}


def _fuzz(seed):
    rng = random.Random(seed)
    now, steps = 0.0, []
    for _ in range(80):
        now += rng.choice([0.5, 1.0, 3.0, 7.0, 14.5, 15.0, 15.5, 30.0])
        steps.append(_step(now, wave=rng.choice([None, 0, 1, 40, 2500]), enabled=rng.choice([True, True, True, False, 'raises']),
                           sprint=rng.random() < 0.3, sprint_ended=rng.random() < 0.3, fire=rng.random() < 0.6))
    steps.append(dict(_step(0.0), now='at_try'))      # a pass at exactly the stamped time: due (>=)
    return steps


def run(mod, steps, session=None):
    """The steps through `mod.demon_mode_always`, with every leaf replaced. Returns the trace: the old fields only."""
    rs = types.SimpleNamespace(dm_always_try_at=0.0, dm_always_count=0, sprint_ended=False)
    if session is not None:
        rs.action_session = session
    trace = []
    with pytest.MonkeyPatch.context() as mp:
        state, calls, events = {}, [], []
        mp.setattr(mod, 'preset', lambda: _preset(state))
        mp.setattr(mod.detect, 'intro_sprint_active', lambda frame: state['sprint'])
        mp.setattr(mod, 'fire_button', lambda frame, name, reason, require_ready=True: calls.append((name, reason, require_ready)) or state['fire'])
        mp.setattr(mod.logger, 'event', lambda kind, **kw: events.append((kind, kw)))
        for step in steps:
            state.update(step)
            now = rs.dm_always_try_at if step['now'] == 'at_try' else step['now']
            rs.sprint_ended = step['sprint_ended']
            calls.clear(), events.clear()
            returned = mod.demon_mode_always(rs, 'F', step['wave'], now)
            trace.append(dict(now=now, returned=returned, calls=list(calls), events=shared.roundtrip(events), try_at=rs.dm_always_try_at, count=rs.dm_always_count))
    return trace


def _preset(state):
    if state['enabled'] == 'raises':
        raise RuntimeError('no preset bound')
    return {'global_behaviors': {'demon_mode_always': state['enabled']}}


def traces(mod):
    out = {name: run(mod, steps) for name, steps in SCENARIOS.items()}
    out.update({f'fuzz_{seed}': run(mod, _fuzz(seed)) for seed in range(1, 7)})
    return shared.roundtrip(out)


@pytest.fixture(scope='module')
def captured():
    return shared.load_golden('demon_mode')


@shared.capture_only_on_unmigrated_code
def test_capture_the_golden_traces_from_the_current_code():
    shared.capture_golden('demon_mode', traces)


def test_the_fixture_was_captured_on_the_pinned_unmigrated_orchestrator(captured):
    shared.assert_captured_on_the_pinned_snapshot(captured)


def test_the_pinned_unmigrated_code_still_produces_the_fixture(captured):
    shared.assert_snapshot_reproduces(captured, traces)


def test_the_live_orchestrator_replays_the_captured_traces(captured):
    shared.assert_live_replays(captured, traces)


# ------------------------------------------------------------------ the rules, in numbers
def test_attempts_are_15_seconds_apart_from_the_stamp_not_from_the_result(captured):
    trace = captured['traces']['unconfirmed_fire_still_spaces_attempts']
    assert [(row['now'], row['returned']) for row in trace] == [(10.0, False), (24.5, False), (25.0, True), (40.0, False), (55.0, True)]
    assert [row['try_at'] for row in trace] == [25.0, 25.0, 40.0, 55.0, 70.0]      # the stamp follows the attempt, success or not


def test_the_floor_is_the_catalogs_cooldown_15():
    schedule = action_contract.catalog().get('demon_mode_always').schedule
    assert schedule == action_contract.Cooldown(15.0) and shared.live().DM_ALWAYS_RETRY_SEC == 15.0


# ------------------------------------------------------------------ history (new)


def test_a_confirmed_fire_leaves_one_succeeded_row_and_nothing_else_does(tmp_path):
    session = shared.make_session(tmp_path)
    run(shared.live(), [_step(100.0), _step(105.0), _step(115.0, fire=False), _step(130.0, wave=None), _step(131.0, sprint=True), _step(146.0, enabled=False)], session)
    rows = shared.history_rows(session, 'demon_mode_always')
    assert len(rows) == 1
    row = rows[0]
    assert (row['action'], row['trigger'], row['state']) == ('demon_mode_always', 'automatic', 'succeeded')
    assert row['outcome']['effect'] == 'confirmed' and row['outcome']['evidence'] == ['fire_confirmed'] and row['outcome']['confirmed'] == 1


def test_a_demon_mode_row_is_written_after_the_fact_and_claims_no_measured_duration(tmp_path):
    session = shared.make_session(tmp_path)
    run(shared.live(), [_step(100.0), _step(115.0)], session)
    shared.assert_rows_claim_no_measured_duration(shared.history_rows(session, 'demon_mode_always'))


def test_the_row_is_written_after_the_tap_and_after_the_stamp():
    live, order = shared.live(), []

    class Session:
        def record(self, *args, **kwargs):
            order.append(('record', rs.dm_always_try_at))

    rs = types.SimpleNamespace(dm_always_try_at=0.0, dm_always_count=0, sprint_ended=False, action_session=Session())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(live, 'preset', lambda: {'global_behaviors': {'demon_mode_always': True}})
        mp.setattr(live.detect, 'intro_sprint_active', lambda frame: False)
        mp.setattr(live, 'fire_button', lambda *a, **k: order.append('tap') or True)
        mp.setattr(live.logger, 'event', lambda kind, **kw: None)
        assert live.demon_mode_always(rs, 'F', 10, 100.0) is True
    assert order == ['tap', ('record', 115.0)]


def test_a_host_with_no_session_and_a_broken_ledger_both_fire_exactly_as_before(tmp_path):
    live = shared.live()
    trace = run(live, [_step(100.0)])
    assert trace[0]['returned'] is True
    broken = shared.broken_session(tmp_path)         # a directory is not a ledger: the write fails inside the session
    assert shared.without_record_failures(run(live, [_step(100.0)], broken)) == trace and broken.unrecorded
