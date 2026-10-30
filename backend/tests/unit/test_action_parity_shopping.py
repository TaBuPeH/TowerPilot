"""Cadence parity of shopping (roadmap item 6, step A2-0 and every later step).

`Shopper.due` is `continuation_ready or now - last_shop > interval` (strictly greater), and
`advance_shopping` backs off after every terminal path. The traces in fixtures/action_golden_v1.json were
captured on the unchanged tree and pin the boundary, the continuation bypass and the back-off.
"""
import pytest

import action_golden as golden


@pytest.fixture(scope='module')
def captured():
    return golden.load()['shopping']


@pytest.fixture(scope='module')
def replayed():
    return golden.roundtrip(golden.shopping_traces())


def test_every_shopping_trace_replays_the_captured_one(captured, replayed):
    assert sorted(replayed) == sorted(captured)
    for name in captured:
        assert replayed[name] == captured[name], name


def test_a_sweep_is_due_only_strictly_after_the_interval_unless_a_confirmed_continuation_waits(captured):
    table = {(interval, delta, continuation): due for interval, delta, continuation, due in captured['due_table']}
    for interval in (20, 90):
        assert table[(interval, interval, False)] is False                 # `>`, never `>=`
        assert table[(interval, interval + 0.001, False)] is True
        assert table[(interval, interval - 0.001, False)] is False
        assert table[(interval, -1, True)] is True                         # a continuation bypasses the interval
        assert table[(interval, 0, True)] is True


def test_every_terminal_path_backs_off_from_the_moment_it_ends(captured):
    # a row is [clock, was_active, active, last_shop, sweep_id, outcome, confirmed, taps, continuation_ready]
    trace = captured['terminal_noop_backs_off']
    first = trace[0]
    assert first[5] == 'skipped' and first[3] == first[0]                  # it ended inside the first pass; back-off starts then
    inside = [row for row in trace if row[0] < first[0] + 90]
    assert {row[3] for row in inside} == {first[3]} and {row[4] for row in inside} == {first[4]}   # no second sweep inside the interval
    after = [row for row in trace if row[0] > first[0] + 90]
    assert after and after[-1][4] != first[4] and after[-1][3] > first[3]  # one more once it has elapsed
