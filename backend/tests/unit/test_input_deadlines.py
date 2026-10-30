"""Expired visual proof must never reach the socket, including after lock wait."""
from contextlib import contextmanager

import pytest

import settings
from device import act
from runtime import device_lease


@pytest.fixture
def input_clock(monkeypatch):
    now = [100.0]
    commands = []
    inst = {'allow_taps': True, 'serial': 'synthetic-device', 'input_display': 2}
    monkeypatch.setattr(settings, 'instance', lambda: inst)
    monkeypatch.setattr(act, 'instance', lambda: inst)
    monkeypatch.setattr(act, 'CONFIG', {
        'tap': {'jitter_px': 0, 'jitter_ms': [0, 0], 'max_rate_per_sec': 12},
        'loop': {'dry_run': False}, 'screen': {'width': 1080, 'height': 2560},
        'active_instance': 'test',
    })
    monkeypatch.setattr(act, '_last_tap', 0)
    monkeypatch.setattr(act.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(act.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(act.adbclient, 'shell', lambda *args, **kwargs: commands.append((args, kwargs)))
    return now, commands


@pytest.mark.parametrize('kind', ['tap', 'swipe'])
@pytest.mark.parametrize('deadline', [100.5, 101.0, 101.1, None])
def test_deadline_checked_inside_device_ownership(kind, deadline, input_clock, monkeypatch):
    now, commands = input_clock

    @contextmanager
    def slow_scope():
        now[0] = 101.0
        yield

    monkeypatch.setattr(device_lease, 'input_scope', slow_scope)

    def dispatch():
        if kind == 'tap':
            return act.tap(100, 200, instant=True, deadline=deadline)
        return act.swipe(100, 200, 100, 400, deadline=deadline)

    if deadline is not None and deadline <= 101:
        with pytest.raises(act.TapRefused, match='deadline expired before socket dispatch'):
            dispatch()
        assert commands == []
        assert act._last_tap == 0
    else:
        assert dispatch()['dry_run'] is False
        assert len(commands) == 1
        expected = 'input -d 2 tap 100 200' if kind == 'tap' else 'input -d 2 swipe 100 200 100 400 400'
        assert commands[0][0] == ('synthetic-device', expected)
        assert act._last_tap == 101


def test_expired_swipe_refused_before_lease_or_socket(input_clock, monkeypatch):
    _, commands = input_clock
    monkeypatch.setattr(device_lease, 'input_scope', lambda: pytest.fail('expired input acquired device'))
    with pytest.raises(act.TapRefused, match='swipe deadline expired'):
        act.swipe(100, 200, 100, 400, deadline=100)
    assert commands == []


def test_dry_swipe_is_not_dispatched_even_with_expired_proof(input_clock, monkeypatch):
    _, commands = input_clock
    monkeypatch.setitem(act.CONFIG['loop'], 'dry_run', True)
    monkeypatch.setattr(device_lease, 'input_scope', lambda: pytest.fail('dry input acquired device'))
    assert act.swipe(100, 200, 100, 400, deadline=0)['dry_run'] is True
    assert commands == []
