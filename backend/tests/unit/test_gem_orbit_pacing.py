"""Orbit taps after the first are aimed from a frame captured after the tap-rate wait."""

from types import SimpleNamespace

import pytest

from device import act
from flows import shard

CAP_PER_SEC = 5  # injected: never read the machine config value


@pytest.fixture
def burst(monkeypatch):
    state = SimpleNamespace(now=100., log=[], gem_visible=lambda: True)
    monkeypatch.setitem(act.CONFIG, 'tap', {'jitter_px': 0, 'jitter_ms': [0, 0],
                                            'max_rate_per_sec': CAP_PER_SEC})
    monkeypatch.setattr(act, '_last_tap', 0.0)
    monkeypatch.setattr(shard.time, 'monotonic', lambda: state.now)

    def sleep(seconds):
        state.log.append(('sleep', round(seconds, 3)))
        state.now += seconds
    monkeypatch.setattr(shard.time, 'sleep', sleep)

    def grab():
        state.log.append(('grab', round(state.now, 3)))
        return 200

    def tap(x, y, **kw):
        act.wait_for_tap_slot()  # the real boundary still paces; it must have nothing left to wait
        state.log.append(('tap', round(state.now, 3)))
        act._last_tap = state.now
        return {'dry_run': False}

    def find_gem(frame, **kw):
        state.log.append(('find', round(state.now, 3)))
        if not state.gem_visible():
            return None
        return SimpleNamespace(center=(680., 805.), rect=(640, 765, 80, 80), score=.99)
    monkeypatch.setattr(shard.capture, 'grab', grab)
    monkeypatch.setattr(shard.capture, 'layout_offset', 0)
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda frame: 123)
    monkeypatch.setattr(shard.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: frame)
    monkeypatch.setattr(shard, 'find_orbit_gem', find_gem)
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a, **kw: (True, 'live', a[1]))
    monkeypatch.setattr(shard, '_in_ability_row', lambda p: False)
    monkeypatch.setattr(shard.logger, 'event', lambda kind, **kw: None)
    monkeypatch.setattr(shard.act, 'tap', tap)
    return state, shard.GemOrbitTapper(enabled=True, jitter=0)


def test_taps_after_the_first_grab_after_the_pacing_wait(burst):
    state, tapper = burst
    tapper.poll(200)
    kinds = [k for k, _ in state.log]
    assert kinds.count('tap') == 3
    interval = 1 / CAP_PER_SEC
    # Walk the log: every grab that precedes a tap after the first must come after the sleep.
    tap_times = [t for k, t in state.log if k == 'tap']
    assert tap_times[1] - tap_times[0] == pytest.approx(interval)
    assert tap_times[2] - tap_times[1] == pytest.approx(interval)
    for index in (1, 2):
        tap_pos = [i for i, (k, _) in enumerate(state.log) if k == 'tap'][index]
        grab_pos = max(i for i, (k, _) in enumerate(state.log[:tap_pos]) if k == 'grab')
        sleep_pos = max(i for i, (k, _) in enumerate(state.log[:tap_pos]) if k == 'sleep')
        assert sleep_pos < grab_pos < tap_pos
        assert state.log[tap_pos][1] - state.log[grab_pos][1] == 0  # frame is as old as the tap


def test_target_that_vanishes_during_the_wait_is_not_tapped(burst):
    state, tapper = burst
    state.gem_visible = lambda: not any(k == 'sleep' for k, _ in state.log)
    tapper.poll(200)
    assert [k for k, _ in state.log].count('tap') == 1
    assert ('sleep', 0.2) in state.log
    assert state.log[-1][0] != 'tap'


def test_first_tap_is_not_delayed_by_the_new_wait(burst):
    state, tapper = burst
    tapper.poll(200)
    first_tap = next(i for i, (k, _) in enumerate(state.log) if k == 'tap')
    assert not any(k == 'sleep' for k, _ in state.log[:first_tap])