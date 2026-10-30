"""Verify tap variation at the device-command boundary without device I/O."""
import random

import pytest

from device import act


def test_claim_taps_vary_position_delay_and_press(monkeypatch):
    commands = []
    sleeps = []
    monkeypatch.setattr(act, 'CONFIG', {
        'tap': {'jitter_px': 4, 'jitter_ms': [15, 50], 'max_rate_per_sec': 12},
        'loop': {'dry_run': False}, 'screen': {'width': 1080, 'height': 2560},
        'active_instance': 'test',
    })
    monkeypatch.setattr(act, 'instance', lambda: {'allow_taps': True})
    monkeypatch.setattr(act, 'random', random.Random(192))
    monkeypatch.setattr(act, '_input', lambda *parts, deadline=None: commands.append(parts))
    monkeypatch.setattr(act.time, 'sleep', sleeps.append)
    monkeypatch.setattr(act.time, 'monotonic', lambda: 1000)
    monkeypatch.setattr(act, '_last_tap', 0)

    events = [act.tap(140, 1340, reason='HUD Ad Gems claim') for _ in range(64)]

    for event, command in zip(events, commands, strict=True):
        kind, x, y, end_x, end_y, press = command
        assert kind == 'swipe'
        assert (x, y) == (event['x'], event['y'])
        assert 136 <= x <= 144 and 1336 <= y <= 1344
        assert abs(end_x - x) <= 2 and abs(end_y - y) <= 2
        assert 45 <= press <= 160 and press == event['press_ms']
        assert 15 <= event['delay_ms'] <= 50
    assert len({(event['x'], event['y']) for event in events}) > 1
    assert len({event['delay_ms'] for event in events}) > 1
    assert len({event['press_ms'] for event in events}) > 1
    assert all(0.015 <= delay <= 0.05 for delay in sleeps[::2])


@pytest.mark.parametrize('instant', [False, True])
@pytest.mark.parametrize('deadline', [100.5, 101.0, 101.1, None])
def test_deadline_checked_after_rate_and_jitter_waits(monkeypatch, instant, deadline):
    now = [100.0]
    commands, sleeps = [], []
    monkeypatch.setattr(act, 'CONFIG', {
        'tap': {'jitter_px': 0, 'jitter_ms': [500, 500], 'max_rate_per_sec': 2},
        'loop': {'dry_run': False}, 'screen': {'width': 1080, 'height': 2560},
        'active_instance': 'test',
    })
    monkeypatch.setattr(act, 'instance', lambda: {'allow_taps': True})
    monkeypatch.setattr(act, 'random', random.Random(1))
    monkeypatch.setattr(act.time, 'monotonic', lambda: now[0])
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(act.time, 'sleep', sleep)
    monkeypatch.setattr(act, '_last_tap', 100.0)
    monkeypatch.setattr(act, '_input', lambda *parts, deadline=None: commands.append((now[0], parts)))
    if deadline is not None and deadline <= 101:
        with pytest.raises(act.TapRefused, match='deadline expired'):
            act.tap(140, 1340, instant=instant, deadline=deadline)
        assert commands == []
        assert act._last_tap == 100
    else:
        act.tap(140, 1340, instant=instant, deadline=deadline)
        assert commands[0][0] == 101
        assert commands[0][1][0] == ('tap' if instant else 'swipe')
        assert act._last_tap == 101
    assert sleeps == [.5, .5]
