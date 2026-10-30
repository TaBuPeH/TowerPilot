"""One /api/status reply pairs its process rows with the refresh evidence about THOSE rows.

`processes` and `process_refresh` used to be built from two separate reads of the process cache. The refresher
thread replaces the cache as a whole, so a reply could carry rows from an older scan next to the age and
timestamps of a newer one, and every "the emulator is free" claim drawn from the rows would rest on evidence
that describes other rows. The reply now pins ONE snapshot (rows, timestamps, error) and builds both fields
from it. The pin belongs to the reply: an action handler that refreshes and reads again still sees the live
cache. The race is driven deterministically: the cache is swapped right after the reply's first read."""
import time

import player.scan_plan
import pytest


def runner_row(pid):
    return {'pid': pid, 'runner': 'gem_collector', 'cmdline': 'python gem_collector.py', 'created': 1.0}


@pytest.fixture
def status(dash, monkeypatch):
    """The real /api/status over a temporary root, with everything except the process cache stubbed."""
    monkeypatch.setattr(player.scan_plan, 'control_gate', lambda root, cfg: {})
    monkeypatch.setattr(dash, '_setup_complete', lambda: True)
    monkeypatch.setattr(dash, '_calibrate_missing', lambda: [])
    monkeypatch.setattr(dash, '_reconnect_status', lambda serial: {'state': 'idle'})
    monkeypatch.setattr(dash, '_device_state', lambda cfg: {'device_state': 'device', 'port_open': True, 'serial': 'fake:1'})
    target = dash._process_target()
    now = time.time()
    older = dict(target=target, attempted_at=now - 3, at=now - 3, procs=[runner_row(111)], error=None)
    newer = dict(target=target, attempted_at=now - 0.5, at=now - 0.5, procs=[runner_row(222)], error=None)
    monkeypatch.setattr(dash, '_PROCS_CACHE', older)
    monkeypatch.setattr(dash, '_PROCS_THREAD', None)
    return dash, older, newer


def swap_after_the_first_read(dash, monkeypatch, newer):
    """The refresher publishes a newer scan the instant after the reply read the cache once."""
    real = dash._procs_snapshot
    reads = []

    def snapshot(*args, **kwargs):
        result = real(*args, **kwargs)
        reads.append(kwargs.get('refresh', True))
        if len(reads) == 1:
            monkeypatch.setattr(dash, '_PROCS_CACHE', newer)
        return result
    monkeypatch.setattr(dash, '_procs_snapshot', snapshot)
    return reads


def test_a_reply_built_while_the_cache_is_replaced_pairs_the_rows_with_their_own_refresh_evidence(status, monkeypatch):
    dash, older, newer = status
    reads = swap_after_the_first_read(dash, monkeypatch, newer)
    body = dash.app.test_client().get('/api/status').get_json()
    assert [row['pid'] for row in body['processes']] == [111]                  # the older scan's rows ...
    refresh = body['process_refresh']
    assert refresh['last_success_at'] == older['at'] and refresh['last_attempt_at'] == older['attempted_at']   # ... and the older scan's evidence
    assert refresh['state'] == 'fresh' and refresh['age_sec'] == pytest.approx(3, abs=1.5)                   # not the newer 0.5 s
    assert reads == [True]                                                      # the cache was read once for the whole reply


def test_control_the_next_reply_sees_the_newer_scan_whole(status, monkeypatch):
    dash, older, newer = status
    swap_after_the_first_read(dash, monkeypatch, newer)
    client = dash.app.test_client()
    client.get('/api/status')
    body = client.get('/api/status').get_json()
    assert [row['pid'] for row in body['processes']] == [222]
    assert body['process_refresh']['last_success_at'] == newer['at']


def test_the_pin_is_the_replys_alone_an_action_handler_reads_the_live_cache_every_time(status, monkeypatch):
    dash, older, newer = status
    swap_after_the_first_read(dash, monkeypatch, newer)
    with dash.app.test_request_context():
        assert [row['pid'] for row in dash._procs_cached()] == [111]
        assert [row['pid'] for row in dash._procs_cached()] == [222]           # no pin: a handler that refreshed sees what it refreshed
        assert dash._procs_status()['last_success_at'] == newer['at']
    assert [row['pid'] for row in dash._procs_cached()] == [222]               # and outside any request


def test_a_reply_that_pinned_a_stale_cache_says_stale_for_the_rows_it_carries(status, monkeypatch):
    dash, older, newer = status
    stale = dict(older, at=time.time() - 60, attempted_at=time.time() - 1, error={'type': 'PermissionError', 'message': 'process table unavailable'})
    monkeypatch.setattr(dash, '_PROCS_CACHE', stale)
    monkeypatch.setattr(dash, '_procs_refresh', lambda: None)                  # no scan from a test
    monkeypatch.setattr(dash, '_PROCS_THREAD', type('Alive', (), {'is_alive': staticmethod(lambda: True)})())
    swap_after_the_first_read(dash, monkeypatch, newer)
    body = dash.app.test_client().get('/api/status').get_json()
    assert [row['pid'] for row in body['processes']] == [111] and body['process_refresh']['state'] == 'stale'
    assert body['process_refresh']['error']['type'] == 'PermissionError'
