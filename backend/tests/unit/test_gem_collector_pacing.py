"""Pass pacing (FOCUS #11 step 3): the collector sleeps only what is left of a 1 s target period, not 1 s after its work."""
import types

import pytest

from gem_collector_run import run


def world(monkeypatch, work):
    """Frames whose identify costs work[n] fake seconds; returns (now, sleeps) once run."""
    import gem_collector as worker
    now, sleeps, costs = [0.0], [], iter(work)
    monkeypatch.setattr(worker.time, 'monotonic', lambda: now[0])

    def identify(frame):
        now[0] += next(costs)
        return types.SimpleNamespace(name='battle')

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    return now, sleeps, identify, sleep


def test_a_short_pass_waits_only_the_remainder_and_a_long_pass_does_not_wait(monkeypatch):
    now, sleeps, identify, sleep = world(monkeypatch, [0.4, 1.5, 0.99, 1.0])
    starts = []
    run(monkeypatch, ['battle'] * 4, screen=lambda f: starts.append(now[0]) or identify(f), sleep=sleep)
    assert sleeps == [pytest.approx(0.6), pytest.approx(0.01)]       # none after the 1.5 s pass or the 1.0 s pass
    assert starts[1] - starts[0] == pytest.approx(1.0)               # the period is max(work, 1 s), not work + 1 s
    assert starts[2] - starts[1] == pytest.approx(1.5)
    assert starts[3] - starts[2] == pytest.approx(1.0)


def test_the_home_branch_is_paced_the_same_way(monkeypatch):
    now, sleeps, identify, sleep = world(monkeypatch, [0.25, 0.25])
    fence = lambda: None                                             # noqa: E731 - a harvesting collector's fence
    ad = types.SimpleNamespace(enabled=True, poll_home=lambda f, reads=None: None, leave_home=lambda: None)
    gems = types.SimpleNamespace(ad_gems=ad, orbit=types.SimpleNamespace(enabled=True), busy=lambda: [],
                                 poll=lambda f, reads=None: None)
    run(monkeypatch, ['home', 'home'], fence=fence, gems=gems, sleep=sleep, read_wave=lambda f: None,
        screen=lambda f: types.SimpleNamespace(name='home') if identify(f) else None)
    assert sleeps == [pytest.approx(0.75), pytest.approx(0.75)]