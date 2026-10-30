"""Cadence parity of the harvest launch decisions (roadmap item 6, step A2-0 and every later step).

The traces in fixtures/action_golden_v1.json were captured on the unchanged tree: back-off 30/60/120/240 s,
a ten second minimum spacing, a ten second refusal recheck and a circuit that latches at five fast failures
until an explicit On. The operations rows stay the authority for the circuit, so these run the real
reconciler against a temporary ledger.
"""
import pytest

import action_golden as golden
from harvest_support import make_env


@pytest.fixture(scope='module')
def captured():
    return golden.load()['harvest']


@pytest.fixture
def replayed(tmp_path, monkeypatch):
    cases = iter(range(1000))
    make_h = lambda: make_env(tmp_path / f'case-{next(cases)}', monkeypatch)
    traces = golden.harvest_traces(make_h())
    traces.update(golden.harvest_spacing_traces(make_h))
    return golden.roundtrip(traces)


def test_every_harvest_trace_replays_the_captured_one(captured, replayed):
    assert sorted(replayed) == sorted(captured)
    for name in captured:
        assert replayed[name] == captured[name], name


def test_the_backoff_series_the_spacing_and_the_circuit_are_the_documented_ones(captured):
    assert captured['backoff_table'][:7] == [0, 30, 60, 120, 240, 480, 900]
    assert set(captured['backoff_table'][6:]) == {900}
    assert captured['constants'] == dict(min_spacing=10, fail_window=120, circuit=5, refusal_retry=10)
    circuit = {row[0]: row for row in captured['circuit']}
    assert [circuit[f'after_failure_{n}'][6] for n in (1, 2, 3, 4)] == [30.0, 90.0, 210.0, 450.0]
    assert circuit['after_failure_5'][2:4] == ['blocked', 'collector_failing']
    assert circuit['latched_a_day_later'][1] == 'none'                     # latched until an explicit On
    assert circuit['explicit_on_resets'][1] == 'launched'


def test_a_refusal_is_rechecked_every_ten_seconds_and_a_quick_exit_still_waits_the_minimum(captured):
    refusals = {row[0]: row for row in captured['refusals_then_launch']}
    assert refusals['refused'][6] == 10.0 and refusals['inside_the_recheck'][1] == 'none'
    assert refusals['recheck_refused_again'][6] == 20.0
    assert captured['quick_clean_exit'][1][6] == 10.0