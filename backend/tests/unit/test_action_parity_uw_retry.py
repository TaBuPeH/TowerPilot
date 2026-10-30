"""Parity of the Ultimate Weapon retry (roadmap item 7, A3b kind B, row C-5): ONE retry state (`rs.uw_fails`, `rs.uw_next_try`) behind the three call sites of main():
the Chain Lightning normalisation, the preset's `uw_wanted` loop and the ongoing Chain Lightning toggle. Backoff 10, 2, 120 s; a success resets the count and leaves the
deadline alone; `uw_ok` is read once per pass.

The three sites live inside `main()`, so the harness cuts that block out of the orchestrator SOURCE (between its two marker comments) and runs it as a function on a
RunState stand-in, for the pinned unmigrated orchestrator and for the live one alike. fixtures/action_r18e/uw_retry_golden_v1.json was captured BEFORE the migration on the
orchestrator whose sha256 is `captured_on` (the latency gate's pinned unmigrated snapshot, re-run below). The history tests describe only what is new.
"""
import random
import textwrap
import types
from pathlib import Path

import pytest

import action_parity_r18e as shared
from towerpilot_workflows import action_contract

START, END = '# ---- CL normalization: first thing each run', '# ---- shopping: sprint LOCKS abilities'


def uw_block(mod):
    """The UW block of main() as a function (rs, wave), cut from `mod`'s source between the two marker comments and run in a copy of its globals."""
    lines = Path(mod.__file__).read_text(encoding='utf-8').splitlines()
    first = next(i for i, line in enumerate(lines) if START in line)
    last = next(i for i, line in enumerate(lines) if END in line and i > first)
    body = textwrap.indent(textwrap.dedent('\n'.join(lines[first:last])), '    ')
    namespace = dict(vars(mod))
    exec(compile('def _block(rs, wave):\n' + body + '\n', '<uw block>', 'exec'), namespace)
    return namespace['_block']


def new_rs(**initial):
    rs = types.SimpleNamespace(uw_next_try=0.0, uw_fails=0, cl_on=False, cl_normalized=False, uw_normalized=False, uw_done=set(), cl_blocked_logged=False,
                               shop=types.SimpleNamespace(active=False))
    for key, value in initial.items():
        setattr(rs, key, value)
    return rs


def state(rs):
    return dict(fails=rs.uw_fails, next_try=rs.uw_next_try, cl_on=rs.cl_on, cl_normalized=rs.cl_normalized, uw_normalized=rs.uw_normalized, done=sorted(rs.uw_done),
                blocked_logged=rs.cl_blocked_logged)


def step(now, toggles=(), window=False, wave=100, enabled=True, shop=False):
    return dict(now=now, toggles=list(toggles), window=window, wave=wave, enabled=enabled, shop=shop)


WANTED = {'death_wave': True, 'golden_tower': False}
F, T = False, True
SCENARIOS = {
    'happy_path_normalises_cl_then_the_wanted_set': dict(wanted=WANTED, steps=[step(0.0), step(1.0), step(2.0), step(3.0, window=True), step(4.0, window=True)]),
    'cl_normalisation_fails_twice_then_gives_up': dict(wanted=WANTED, steps=[step(0.0, [F]), step(5.0), step(10.0, [F]), step(29.9), step(30.0, [F]), step(31.0, [T, T]), step(32.0)]),
    'after_a_cl_giveup_one_wanted_failure_gives_up_at_once': dict(wanted=WANTED, steps=[step(0.0, [F]), step(10.0, [F]), step(30.0, [F]), step(70.0, [F, F]), step(71.0)]),
    'same_pass_failure_then_a_toggle_with_the_cached_uw_ok': dict(wanted={'death_wave': True}, initial=dict(uw_fails=2, cl_normalized=True),
                                                                  steps=[step(0.0, [F, T], window=True), step(10.0, window=True), step(39.9, window=True), step(40.0, window=True)]),
    'ongoing_cl_toggle_ladder_10_20_40_80_120_120': dict(wanted={}, initial=dict(cl_normalized=True, uw_normalized=True),
                                                         steps=[step(0.0, [F], window=True), step(9.9, window=True), step(10.0, [F], window=True), step(29.9, window=True),
                                                                step(30.0, [F], window=True), step(70.0, [F], window=True), step(150.0, [F], window=True),
                                                                step(269.9, window=True), step(270.0, [F], window=True), step(390.0, [F], window=True), step(509.9),
                                                                step(510.0, [T], window=True), step(511.0, [T])]),
    'a_success_keeps_the_old_deadline': dict(wanted={}, initial=dict(cl_normalized=True, uw_normalized=True, uw_fails=3, uw_next_try=50.0),
                                             steps=[step(49.0, window=True), step(50.0, [T], window=True), step(51.0, [F], window=False), step(52.0, window=False)]),
    'automation_off_and_shopping_defer_the_toggle': dict(wanted={}, initial=dict(cl_normalized=True, uw_normalized=True),
                                                         steps=[step(0.0, window=True, enabled=False), step(1.0, window=True, shop=True), step(2.0, window=True, shop=True),
                                                                step(3.0, [T], window=True), step(4.0, window=False, shop=True)]),
    'chain_lightning_compiled_out': dict(wanted=WANTED, cl=dict(always_on=False, enabled=False), steps=[step(0.0, [T, T]), step(1.0), step(2.0, window=True)]),
    'always_on_baseline_and_no_wave': dict(wanted={'death_wave': True}, cl=dict(always_on=True, enabled=True), steps=[step(0.0, [T, T], wave=None), step(1.0, wave=None), step(2.0)]),
}


def fuzz(seed):
    rng = random.Random(seed)
    now, plan = 0.0, []
    for _ in range(70):
        now += rng.choice([0.5, 1.0, 5.0, 9.5, 10.0, 20.0, 40.0, 120.0])
        plan.append(dict(step(now, [rng.random() < 0.45 for _ in range(rng.randint(0, 4))], window=rng.random() < 0.5, wave=rng.choice([None, 10, 100]),
                              enabled=rng.random() < 0.9, shop=rng.random() < 0.15), at_try=rng.random() < 0.15))
    return dict(wanted=rng.choice([{}, WANTED, {'death_wave': True}]), cl=dict(always_on=rng.random() < 0.3, enabled=rng.random() < 0.9), steps=plan,
                initial=rng.choice([{}, dict(cl_normalized=True), dict(uw_fails=2)]))


def run(mod, scenario, session=None, hook=None, holder=None):
    """The scenario through the UW block of `mod`. Returns the trace: the old fields only."""
    rs = new_rs(**scenario.get('initial', {}))
    if session is not None:
        rs.action_session = session
    if holder is not None:
        holder.append(rs)
    cl = scenario.get('cl', dict(always_on=False, enabled=True))
    cur, calls, events, trace = {}, [], [], []

    def toggle(weapon, want, **kw):
        result = cur['toggles'].pop(0) if cur['toggles'] else True
        calls.append((weapon, bool(want), result))
        if hook:
            hook('toggle', weapon, result)
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, 'preset', lambda: {'chain_lightning': dict(cl), 'uw_wanted': dict(scenario['wanted'])})
        mp.setattr(mod, 'automation_enabled', lambda routine: cur['enabled'])
        mp.setattr(mod, 'cl_window', lambda rs_, wave: cur['window'])
        mp.setattr(mod.shopper, 'uw_toggle', toggle)
        mp.setattr(mod.logger, 'event', lambda kind, **kw: events.append((kind, kw)))
        for item in scenario['steps']:
            cur.update(item, toggles=list(item['toggles']))
            now = rs.uw_next_try if item.get('at_try') else item['now']
            rs.shop.active = item['shop']
            mp.setattr(mod, 'time', types.SimpleNamespace(monotonic=lambda now=now: now, time=lambda: 0.0, sleep=lambda s: None))
            calls.clear(), events.clear()
            uw_block(mod)(rs, item['wave'])
            trace.append(dict(now=now, calls=[list(c) for c in calls], events=shared.roundtrip(events), state=state(rs)))
    return trace


def traces(mod):
    out = {name: run(mod, scenario) for name, scenario in SCENARIOS.items()}
    out.update({f'fuzz_{seed}': run(mod, fuzz(seed)) for seed in range(1, 13)})
    return shared.roundtrip(out)


@pytest.fixture(scope='module')
def captured():
    return shared.load_golden('uw_retry')


@shared.capture_only_on_unmigrated_code
def test_capture_the_golden_traces_from_the_current_code():
    shared.capture_golden('uw_retry', traces)


def test_the_fixture_was_captured_on_the_pinned_unmigrated_orchestrator(captured):
    shared.assert_captured_on_the_pinned_snapshot(captured)


def test_the_pinned_unmigrated_code_still_produces_the_fixture(captured):
    shared.assert_snapshot_reproduces(captured, traces)


def test_the_live_orchestrator_replays_the_captured_traces(captured):
    shared.assert_live_replays(captured, traces)


# ------------------------------------------------------------------ the rules, in numbers
def test_the_ladder_is_10_20_40_80_120_120_and_each_step_waits_for_its_deadline(captured):
    rows = captured['traces']['ongoing_cl_toggle_ladder_10_20_40_80_120_120']
    stamps = [(row['now'], row['state']['fails'], row['state']['next_try']) for row in rows if row['calls'] and not row['calls'][0][2]]
    assert stamps == [(0.0, 1, 10.0), (10.0, 2, 30.0), (30.0, 3, 70.0), (70.0, 4, 150.0), (150.0, 5, 270.0), (270.0, 6, 390.0), (390.0, 7, 510.0)]
    assert not [row for row in rows if row['now'] in (9.9, 29.9, 269.9) and row['calls']]               # inside the window: no panel visit


def test_a_success_resets_the_count_and_leaves_the_deadline(captured):
    rows = captured['traces']['a_success_keeps_the_old_deadline']
    assert [(row['state']['fails'], row['state']['next_try']) for row in rows] == [(3, 50.0), (0, 50.0), (1, 61.0), (1, 61.0)]


def test_the_cached_uw_ok_lets_the_third_site_run_after_the_second_failed_in_the_same_pass(captured):
    first = captured['traces']['same_pass_failure_then_a_toggle_with_the_cached_uw_ok'][0]
    assert first['calls'] == [['death_wave', True, False], ['chain_lightning', True, True]]
    assert (first['state']['fails'], first['state']['next_try']) == (0, 40.0)             # 3 failures stamped 40 s, the success reset the count only


def test_the_giveup_leaves_the_count_so_the_next_wanted_failure_gives_up_at_once(captured):
    rows = captured['traces']['after_a_cl_giveup_one_wanted_failure_gives_up_at_once']
    kinds = [event[0] for row in rows for event in row['events']]
    assert 'cl_normalize_giveup' in kinds and 'uw_normalize_giveup' in kinds


# ------------------------------------------------------------------ the shared state, one definition (new)
def test_the_catalog_holds_the_ladder_once():
    policy = action_contract.catalog().get('uw_toggle').schedule
    assert policy == action_contract.Backoff(10, 2, 120)
    assert [policy.delay(n) for n in range(1, 8)] == [10, 20, 40, 80, 120, 120, 120]


# ------------------------------------------------------------------ history (new)


def test_every_toggle_of_the_three_sites_leaves_one_row_on_the_one_instance(tmp_path):
    session = shared.make_session(tmp_path)
    live = shared.live()
    plain = run(live, SCENARIOS['same_pass_failure_then_a_toggle_with_the_cached_uw_ok'], session)
    run(live, SCENARIOS['cl_normalisation_fails_twice_then_gives_up'], session)
    run(live, SCENARIOS['happy_path_normalises_cl_then_the_wanted_set'], session)
    rows = shared.history_rows(session, 'uw_toggle')
    expected = sum(len(row['calls']) for scenario in ('same_pass_failure_then_a_toggle_with_the_cached_uw_ok', 'cl_normalisation_fails_twice_then_gives_up',
                                                      'happy_path_normalises_cl_then_the_wanted_set') for row in run(shared.snapshot_module(), SCENARIOS[scenario]))
    assert len(rows) == expected > 0 and plain
    assert {row['instance'] for row in rows} == {'uw_toggle'}
    assert {row['refs']['site'] for row in rows} == {'cl_normalize', 'uw_wanted', 'cl_toggle'}
    for row in rows:
        assert row['state'] in ('succeeded', 'failed') and row['trigger'] == 'automatic'
        assert (row['state'] == 'succeeded') == (row['outcome']['effect'] == 'confirmed')
        assert row['outcome']['evidence'] == (['toggle_verified'] if row['state'] == 'succeeded' else [])


def test_a_toggle_row_is_written_after_the_fact_and_claims_no_measured_duration(tmp_path):
    session = shared.make_session(tmp_path)
    run(shared.live(), SCENARIOS['ongoing_cl_toggle_ladder_10_20_40_80_120_120'], session)
    shared.assert_rows_claim_no_measured_duration(shared.history_rows(session, 'uw_toggle'))


def test_rows_are_written_once_after_every_decision_and_stamp_of_the_pass():
    order, holder = [], []

    class Session:
        def record(self, action, instance, **kwargs):
            rs = holder[0]
            order.append(('record', kwargs['refs']['weapon'], rs.uw_fails, rs.uw_next_try))

    run(shared.live(), SCENARIOS['same_pass_failure_then_a_toggle_with_the_cached_uw_ok'] | dict(steps=[step(0.0, [F, T], window=True)]), Session(),
        lambda kind, weapon, result: order.append((kind, weapon, result)), holder)
    assert order == [('toggle', 'death_wave', False), ('toggle', 'chain_lightning', True), ('record', 'death_wave', 0, 40.0), ('record', 'chain_lightning', 0, 40.0)]


def test_no_session_and_a_broken_ledger_run_the_block_exactly_as_before(tmp_path):
    live = shared.live()
    plain = run(live, SCENARIOS['ongoing_cl_toggle_ladder_10_20_40_80_120_120'])
    broken = shared.broken_session(tmp_path)
    assert shared.without_record_failures(run(live, SCENARIOS['ongoing_cl_toggle_ladder_10_20_40_80_120_120'], broken)) == plain and broken.unrecorded


def test_a_toggle_that_returned_before_a_capture_failure_still_leaves_its_row(tmp_path):
    live, holder, seen = shared.live(), [], []
    session = shared.make_session(tmp_path)

    def hook(kind, weapon, result):
        seen.append(weapon)
        if len(seen) == 2:
            raise live.capture.CaptureError('screen lost')

    with pytest.raises(live.capture.CaptureError):
        run(live, SCENARIOS['same_pass_failure_then_a_toggle_with_the_cached_uw_ok'] | dict(steps=[step(0.0, [F, T], window=True)]), session, hook, holder)
    assert shared.history_rows(session, 'uw_toggle') == []             # the pass was cut short before its flush
    live.uw_flush_history(holder[0])                                    # what both of main()'s handlers now do
    assert [row['refs']['weapon'] for row in shared.history_rows(session, 'uw_toggle')] == ['death_wave'] and holder[0].uw_attempts == []


def test_both_exception_handlers_of_the_main_loop_flush_the_buffered_toggle_rows():
    source = Path(shared.live().__file__).read_text(encoding='utf-8')
    for handler in ('except capture.CaptureError as e:', 'except Exception:\n            uw_flush_history(rs)'):
        assert handler in source
    assert 'except capture.CaptureError as e:\n            uw_flush_history(rs)' in source


def test_a_history_row_that_cannot_be_built_never_escapes_into_the_loop():
    live = shared.live()
    rs = new_rs(action_session=types.SimpleNamespace(record=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('ledger'))))
    live.record_upkeep(rs, 'no_such_action', 'x', True)                  # upkeep_outcome raises KeyError before the session is reached
    live.record_upkeep(rs, 'uw_toggle', 'uw_toggle', True)               # the session itself raises
