"""Parity of the compiled rules (roadmap item 7, A3b kind B, row C-1): per-rule refire floor, once per run unless `repeat`, retired beats repeat, ONE CONTACT per tick.

fixtures/action_r18e/rules_golden_v1.json was captured BEFORE the refire floor moved to the catalog's `compiled_rule` Cooldown, on the orchestrator whose sha256 is
`captured_on` (the same bytes as the latency gate's pinned unmigrated snapshot, which this file re-runs, so the fixture can be re-derived without trusting the capture).
Only `_rule_act` (the verified helpers behind each action) is replaced; the interpreter, its admission, its triggers, its logs and its per-rule state are the real ones.
The history tests describe only what is new: a row per CONTACT, written after the action returned and after the rule's own state was updated.
"""
import random
import types

import pytest

import action_parity_r18e as shared


def rule(rid, wave=1, do='nuke', **extra):
    out = {'when': {'wave_at_least': wave}, 'do': {'fire': do}}
    if rid is not None:
        out['id'] = rid
    out.update(extra)
    return out


UNSUPPORTED = {'when': {'wave_at_least': 1}, 'do': {'no_such_action': 1}, 'id': 'bad#0', 'repeat': True}


def steps(*pairs):
    return [dict(now=now, wave=wave) for now, wave in pairs]


def clock(*nows, wave=5):
    return steps(*[(now, wave) for now in nows])


SCENARIOS = {
    'once_per_run': dict(rules=[rule('a#0')], steps=clock(0.0, 1.0, 2.0, 30.0, 400.0)),
    'repeat_floor_boundary': dict(rules=[rule('a#0', repeat=True, refire_sec=7)], steps=clock(0.0, 1.0, 6.5, 6.75, 7.0, 7.25, 14.0, 14.5, 21.0)),
    'failed_contact_retries_after_the_floor': dict(rules=[rule('a#0', refire_sec=4)], results={'a#0': [(True, False), (True, False), (True, True)]},
                                                   steps=clock(0.0, 3.0, 4.0, 7.5, 8.0, 12.0, 20.0)),
    'no_contact_still_stamps_the_floor': dict(rules=[rule('a#0', repeat=True)], results={'a#0': [(False, False), (False, True), (True, True)]},
                                              steps=clock(0.0, 1.0, 5.0, 6.0, 10.0, 15.0)),
    'one_contact_per_tick': dict(rules=[rule('a#0', repeat=True, refire_sec=3), rule('b#0'), rule('c#0', wave=3)], steps=clock(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.5, 9.0)),
    'a_pass_without_contact_spends_nothing': dict(rules=[rule('a#0', repeat=True), rule('b#0')], results={'a#0': [(False, False)]}, steps=clock(0.0, 1.0, 6.0)),
    'retired_beats_repeat': dict(rules=[UNSUPPORTED], steps=clock(0.0, 40.0, 100.0)),
    'default_floor_is_five_seconds': dict(rules=[rule('a#0', repeat=True)], steps=clock(0.0, 4.5, 5.0, 5.5, 10.0, 10.25)),
    'index_keyed_rules': dict(rules=[rule(None, repeat=True), rule(None, wave=2)], steps=clock(0.0, 1.0, 2.0, 6.0, 12.0)),
    'trigger_not_reached_logs_nothing': dict(rules=[rule('a#0', wave=50)], steps=steps((0.0, 10), (1.0, 49), (2.0, 50), (3.0, 60), (10.0, 60))),
    'suppression_log_is_rate_limited': dict(rules=[rule('a#0', repeat=True, refire_sec=100)], steps=clock(*[float(t) for t in range(0, 101, 5)])),
    'another_phase_is_skipped_in_silence': dict(rules=[rule('a#0', latency='death_handler')], steps=clock(0.0, 1.0)),
    'the_death_phase_retires_what_it_cannot_run': dict(rules=[rule('a#0', latency='death_handler')], phase='death', steps=clock(0.0, 1.0)),
    'normalized_spelling_and_alias_floors': dict(rules=[{'id': 'n#0', 'when': {'kind': 'wave_at_least', 'value': 3}, 'do': {'kind': 'fire', 'button': 'nuke'}, 'repeat': True,
                                                         'refire_sec': 2.5}, {'id': 'n#1', 'when': {'wave_at_least': 3}, 'do': {'fire': {'button': 'demon_mode', 'throttle_sec': 9}},
                                                                              'repeat': True}], steps=clock(0.0, 1.0, 2.5, 3.0, 5.0, 9.0, 12.0)),
}


def _fuzz_rules(rng):
    out = []
    for index in range(rng.randint(1, 4)):
        out.append(rule(rng.choice([f'f#{index}', None]), wave=rng.choice([1, 3, 5, 9]), repeat=rng.random() < 0.5,
                        **rng.choice([{}, {'refire_sec': 2}, {'refire_sec': 0}, {'refire_sec': 6.5}])))
    if rng.random() < 0.3:
        out.append(UNSUPPORTED)
    return out


def fuzz(seed):
    rng = random.Random(seed)
    rules_ = _fuzz_rules(rng)
    results = {r.get('id'): [rng.choice([(True, True), (True, False), (False, False), (False, True)]) for _ in range(6)] for r in rules_ if r.get('id')}
    now, plan = 0.0, []
    for _ in range(60):
        now += rng.choice([0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 7.0])
        plan.append(dict(now=now, wave=rng.choice([None, 0, 2, 4, 6, 10])))
    return dict(rules=rules_, results=results, steps=plan)


def run(mod, scenario, session=None, hook=None, holder=None):
    """The scenario through `mod._run_rules`. Returns the trace: the old fields only (state is read back from the RunState stand-in)."""
    rs = types.SimpleNamespace(rules_fired=set(), rule_next={}, nuked_marks=set(), sprint_ended=False)
    if session is not None:
        rs.action_session = session
    if holder is not None:
        holder.append(rs)
    queues = {rid: list(seq) for rid, seq in scenario.get('results', {}).items()}
    trace, acts, events = [], [], []

    def act(rs_, frame, k, rid, name, p):
        result = queues.get(k, []).pop(0) if queues.get(k) else (True, True)
        acts.append((rid, name, result[0], result[1]))
        if hook:
            hook('act', rid, result)
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, 'preset', lambda: {'rules': scenario['rules']})
        mp.setattr(mod, '_rule_act', act)
        mp.setattr(mod.logger, 'event', lambda kind, **kw: events.append((kind, kw)))
        for step in scenario['steps']:
            acts.clear(), events.clear()
            fake = types.SimpleNamespace(monotonic=lambda now=step['now']: now, time=lambda: 0.0, sleep=lambda s: None)
            mp.setattr(mod, 'time', fake)
            touched = mod._run_rules(rs, 'F', step['wave'], scenario.get('phase', 'battle'))
            trace.append(dict(now=step['now'], wave=step['wave'], touched=touched, acts=[list(a) for a in acts], events=shared.roundtrip(events),
                              rule_next={str(k): v for k, v in sorted(rs.rule_next.items(), key=lambda kv: str(kv[0]))},
                              fired=sorted(str(k) for k in rs.rules_fired), retired=sorted(str(k) for k in getattr(rs, 'rules_retired', {}))))
    return trace


def traces(mod):
    out = {name: run(mod, scenario) for name, scenario in SCENARIOS.items()}
    out.update({f'fuzz_{seed}': run(mod, fuzz(seed)) for seed in range(1, 9)})
    return shared.roundtrip(out)


@pytest.fixture(scope='module')
def captured():
    return shared.load_golden('rules')


@shared.capture_only_on_unmigrated_code
def test_capture_the_golden_traces_from_the_current_code():
    shared.capture_golden('rules', traces)


def test_the_fixture_was_captured_on_the_pinned_unmigrated_orchestrator(captured):
    shared.assert_captured_on_the_pinned_snapshot(captured)


def test_the_pinned_unmigrated_code_still_produces_the_fixture(captured):
    shared.assert_snapshot_reproduces(captured, traces)


def test_the_live_orchestrator_replays_the_captured_traces(captured):
    shared.assert_live_replays(captured, traces)


# ------------------------------------------------------------------ the rules, in numbers
def fired_at(trace):
    return [row['now'] for row in trace if any(event[0] == 'rule_fire' for event in row['events'])]


def test_a_rule_fires_once_per_run_and_a_repeat_rule_again_exactly_at_its_floor(captured):
    assert fired_at(captured['traces']['once_per_run']) == [0.0]
    assert fired_at(captured['traces']['repeat_floor_boundary']) == [0.0, 7.0, 14.0, 21.0]          # >= the stamp, not >
    assert fired_at(captured['traces']['default_floor_is_five_seconds']) == [0.0, 5.0, 10.0]


def test_a_failed_contact_is_retried_after_its_floor_and_a_success_retires_the_rule(captured):
    trace = captured['traces']['failed_contact_retries_after_the_floor']
    assert fired_at(trace) == [0.0, 4.0, 8.0] and trace[-1]['fired'] == ['a#0']


def test_one_contact_per_tick_defers_without_stamping_or_retiring(captured):
    trace = captured['traces']['one_contact_per_tick']
    for row in trace:
        assert len(row['acts']) <= 1
    deferred = [e for row in trace for e in row['events'] if e[0] == 'rule_suppressed' and e[1]['why'] == 'one_action_per_tick']
    assert deferred


def test_a_pass_without_contact_spends_no_budget(captured):
    first = captured['traces']['a_pass_without_contact_spends_nothing'][0]
    assert [a[0] for a in first['acts']] == ['a#0', 'b#0']


def test_a_retired_rule_stays_retired_whatever_repeat_says(captured):
    trace = captured['traces']['retired_beats_repeat']
    assert [sum(1 for e in row['events'] if e[0] == 'rule_unsupported') for row in trace] == [1, 0, 0] and all(not row['acts'] for row in trace)


# ------------------------------------------------------------------ history (new)


def test_a_row_per_contact_and_none_for_a_suppression_a_retired_rule_or_a_pass_without_contact(tmp_path):
    session = shared.make_session(tmp_path)
    live = shared.live()
    for name in ('one_contact_per_tick', 'failed_contact_retries_after_the_floor', 'no_contact_still_stamps_the_floor', 'retired_beats_repeat', 'trigger_not_reached_logs_nothing'):
        run(live, SCENARIOS[name], session)
        session.settle()
    plain = [(row['now'], [a for a in row['acts'] if a[2]]) for name in ('one_contact_per_tick', 'failed_contact_retries_after_the_floor', 'no_contact_still_stamps_the_floor',
                                                                           'retired_beats_repeat', 'trigger_not_reached_logs_nothing')
             for row in run(shared.snapshot_module(), SCENARIOS[name])]
    contacts = sum(len(acts) for _, acts in plain)
    rows = shared.history_rows(session, 'compiled_rule')
    assert len(rows) == contacts > 0
    assert {row['instance'] for row in rows} <= {'compiled_rule@a#0', 'compiled_rule@b#0', 'compiled_rule@c#0'}
    states = {row['state'] for row in rows}
    assert states == {'succeeded', 'failed'}
    for row in rows:
        assert (row['trigger'], row['outcome']['attempts']) == ('automatic', 1)
        if row['state'] == 'succeeded':
            assert (row['outcome']['effect'], row['outcome']['evidence'], row['outcome']['confirmed']) == ('confirmed', ['rule_confirmed'], 1)
        else:
            assert (row['outcome']['effect'], row['outcome']['evidence'], row['outcome']['confirmed']) == ('unknown', [], 0)
        assert row['refs'].get('rule_id') == row['instance'].split('@', 1)[1]


def test_a_rule_row_is_written_after_the_fact_and_claims_no_measured_duration(tmp_path):
    session = shared.make_session(tmp_path)
    run(shared.live(), SCENARIOS['repeat_floor_boundary'], session)
    shared.assert_rows_claim_no_measured_duration(shared.history_rows(session, 'compiled_rule'))


def test_the_row_is_written_after_the_action_returned_and_after_the_rules_state_was_updated():
    order, holder = [], []

    class Session:
        def record(self, action, instance, **kwargs):
            rs = holder[0]
            order.append(('record', instance, dict(rs.rule_next), set(rs.rules_fired)))

    run(shared.live(), dict(rules=[rule('a#0')], steps=clock(0.0)), Session(), lambda kind, rid, result: order.append(kind), holder)
    assert order == ['act', ('record', 'compiled_rule@a#0', {'a#0': 5.0}, {'a#0'})]


def test_a_host_with_no_session_and_a_broken_ledger_run_the_rules_exactly_as_before(tmp_path):
    live = shared.live()
    plain = run(live, SCENARIOS['repeat_floor_boundary'])
    broken = shared.broken_session(tmp_path)
    assert shared.without_record_failures(run(live, SCENARIOS['repeat_floor_boundary'], broken)) == plain and broken.unrecorded
