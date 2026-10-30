"""Raw diagnostic correlation and safe device-boundary outcomes, offline."""

import paths as test_paths
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from runtime import diagnostics, logger
from device import act, capture, adbclient


def records(directory):
    return [json.loads(line) for path in Path(directory).rglob('events_*.jsonl')
            for line in path.read_text(encoding='utf-8').splitlines()]


def test_diagnostics_import_has_no_device_or_settings_side_effects():
    source = "import sys; from runtime import diagnostics; assert 'settings' not in sys.modules; assert 'cv2' not in sys.modules; assert 'device.capture' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', source], cwd=test_paths.BACKEND,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_nested_contexts_and_concurrent_destinations_do_not_leak(tmp_path):
    barrier = threading.Barrier(2)

    def request(number):
        with logger.destination(f'request-{number}'), logger.context(request_id=str(number), trace_id=f'trace-{number}'):
            with logger.attempt('request') as outer:
                barrier.wait(timeout=5)
                with logger.attempt('child'):
                    logger.event('custom', value=number)
                logger.event('restored')
            return outer.span_id

    with ThreadPoolExecutor(2) as pool:
        outer_ids = list(pool.map(request, (1, 2)))
    all_rows = records(logger._LOG_DIR)
    assert len(all_rows) == 12
    assert len({row['event_id'] for row in all_rows}) == 12
    assert len({row['seq'] for row in all_rows}) == 12
    for number, outer_id in zip((1, 2), outer_ids):
        rows = records(logger._LOG_DIR / f'request-{number}')
        assert {row['request_id'] for row in rows} == {str(number)}
        assert {row['trace_id'] for row in rows} == {f'trace-{number}'}
        child = next(row for row in rows if row['kind'] == 'custom')
        assert child['parent_span_id'] == outer_id
        assert next(row for row in rows if row['kind'] == 'restored')['span_id'] == outer_id
        for start in [row for row in rows if row['kind'] == 'attempt_start']:
            end = next(row for row in rows if row['kind'] == 'attempt_end' and row['attempt_id'] == start['attempt_id'])
            assert end['duration_ms'] >= 0
            assert end['monotonic_ns'] >= start['monotonic_ns']
    assert 'request_id' not in logger.current_context()
    assert 'span_id' not in logger.current_context()


def test_exception_overrides_a_provisional_success_and_retains_traceback():
    error = ValueError('failed after dispatch')
    with pytest.raises(ValueError) as caught:
        with logger.attempt('failed') as attempt:
            attempt.finish('accepted')
            raise error
    assert caught.value is error
    start, end = records(logger._LOG_DIR)
    assert start['attempt_id'] == end['attempt_id']
    assert end['outcome'] == 'error'
    assert end['exception_type'] == 'ValueError'
    assert end['exception_message'] == 'failed after dispatch'
    assert 'raise error' in end['traceback']


@pytest.mark.parametrize('exit_code', [None, 0, 5])
@pytest.mark.parametrize('handled', [False, True])
def test_only_explicitly_handled_clean_exits_are_excluded_from_failure_queries(tmp_path, exit_code, handled):
    from functools import partial
    from control import diagnostics as query_store
    emit = partial(diagnostics.event_to, tmp_path / 'logs' / '_worker')
    with pytest.raises(SystemExit) as caught:
        with diagnostics.attempt('worker.exit', emit=emit) as attempt:
            if handled:
                attempt.finish('exited', exit_code=exit_code)
            raise SystemExit(exit_code)
    assert caught.value.code == exit_code
    start, end = records(tmp_path / 'logs')
    assert start['attempt_id'] == end['attempt_id']
    clean = handled and exit_code in (None, 0)
    assert query_store.is_failure(end) is not clean
    failed = query_store.query(tmp_path, {'failures': '1'})['events']
    assert len(failed) == (0 if clean else 1)
    if clean:
        assert end['outcome'] == 'exited' and end['exit_code'] == exit_code
        assert not {'exception_type', 'exception_message', 'traceback'} & end.keys()
    else:
        assert end['outcome'] == 'error'
        assert end['exception_type'] == 'SystemExit'
        assert 'raise SystemExit' in end['traceback']
        assert failed[0]['event_id'] == end['event_id']


def test_event_preserves_battle_and_environment_correlation(monkeypatch):
    monkeypatch.setenv('TOWER_OPERATION_ID', 'operation')
    monkeypatch.setenv('TOWER_TRACE_ID', 'trace')
    monkeypatch.setenv('TOWER_REQUEST_ID', 'request')
    monkeypatch.setattr(logger, '_battle_context', {'battle_id': 'battle', 'driver_segment': 'segment'})
    row = logger.event('custom', event_id='caller-cannot-replace', seq=-1)
    saved, = records(logger._LOG_DIR)
    assert saved == row
    assert row['operation_id'] == 'operation'
    assert row['trace_id'] == 'trace' and row['request_id'] == 'request'
    assert row['battle_id'] == 'battle' and row['driver_segment'] == 'segment'
    assert row['event_id'] != 'caller-cannot-replace' and row['seq'] > 0
    assert row['wall_time'] > 0 and row['monotonic_ns'] > 0


@pytest.fixture
def input_boundary(monkeypatch):
    monkeypatch.setattr(act, 'instance', lambda: {'allow_taps': True})
    monkeypatch.setitem(act.CONFIG, 'tap', {'jitter_px': 0, 'jitter_ms': [0, 0], 'max_rate_per_sec': 100})
    monkeypatch.setitem(act.CONFIG['loop'], 'dry_run', False)
    monkeypatch.setattr(act, '_last_tap', 0)
    monkeypatch.setattr(act.time, 'sleep', lambda seconds: None)
    commands = []
    monkeypatch.setattr(act, '_input', lambda *parts, deadline=None: commands.append(parts))
    return commands


def test_refused_and_dry_run_inputs_are_visible_without_dispatch(input_boundary, monkeypatch):
    with pytest.raises(act.TapRefused):
        act.tap(-100, -100)
    with pytest.raises(act.TapRefused):
        act.tap(100, 100, deadline=0)
    monkeypatch.setitem(act.CONFIG['loop'], 'dry_run', True)
    assert act.tap(100, 100)['dry_run'] is True
    assert act.swipe(100, 100, 100, 200)['dry_run'] is True
    assert not input_boundary
    ends = [row for row in records(logger._LOG_DIR) if row['kind'] == 'attempt_end']
    assert [row['outcome'] for row in ends] == ['refused', 'refused', 'dry_run', 'dry_run']
    assert ends[0]['exception_type'] == 'TapRefused'


def test_disk_failure_after_dispatch_cannot_retry_input(input_boundary, monkeypatch, tmp_path, capsys):
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('blocked')
    monkeypatch.setattr(logger, '_LOG_DIR', blocked)
    result = act.tap(100, 100, instant=True)
    assert result['type'] == 'tap' and not result['dry_run']
    assert input_boundary == [('tap', 100, 100)]
    output = capsys.readouterr().err
    assert 'diagnostic_write_error' in output and 'dispatched' in output


def test_transport_input_failure_is_not_reported_as_dispatch(input_boundary, monkeypatch):
    def fail(*parts, deadline=None):
        input_boundary.append(parts)
        raise adbclient.AdbError('transport closed')
    monkeypatch.setattr(act, '_input', fail)
    with pytest.raises(act.TapRefused, match='transport closed'):
        act.swipe(100, 100, 200, 200)
    assert len(input_boundary) == 1
    end = records(logger._LOG_DIR)[-1]
    assert end['outcome'] == 'failed'
    assert end['dispatched'] is False
    assert 'AdbError' in end['traceback']


def test_capture_retries_and_recovery_are_timed_without_logging_every_frame(monkeypatch):
    monkeypatch.setitem(capture.CONFIG['screen'], 'width', 16)
    monkeypatch.setitem(capture.CONFIG['screen'], 'height', 16)
    raw = (16).to_bytes(4, 'little') * 2 + (1).to_bytes(4, 'little') + bytes(16 * 16 * 4)
    replies = [adbclient.AdbError('offline'), raw, raw]
    recoveries = []

    def receive(*args, **kwargs):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(adbclient, 'exec_out', receive)
    monkeypatch.setattr(adbclient, 'recover', recoveries.append)
    assert capture.grab('fake', '1').shape == (16, 16, 3)
    assert capture.grab('fake', '1').shape == (16, 16, 3)
    rows = records(logger._LOG_DIR)
    assert [row['kind'] for row in rows] == ['capture_retry', 'capture_recovered']
    assert rows[0]['capture_id'] == rows[1]['capture_id']
    assert rows[1]['duration_ms'] >= rows[0]['duration_ms']
    assert recoveries == ['fake']


def test_capture_terminal_failure_retains_both_attempts(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise adbclient.AdbError('still offline')
    monkeypatch.setattr(adbclient, 'exec_out', fail)
    monkeypatch.setattr(adbclient, 'recover', lambda serial: None)
    with pytest.raises(capture.CaptureError, match='still offline'):
        capture.grab('fake', '1')
    assert len(calls) == 2
    retry, failure = records(logger._LOG_DIR)
    assert retry['kind'] == 'capture_retry'
    assert failure['kind'] == 'capture_failed'
    assert failure['retries'] == 1
    assert failure['exception_type'] == 'CaptureError'
    assert 'AdbError' in failure['traceback']


def test_write_health_retains_failure_when_pythonw_has_no_streams(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, '_write_errors', 0)
    monkeypatch.setattr(diagnostics, '_last_write_error', None)
    monkeypatch.setattr(diagnostics.sys, 'stderr', None)
    monkeypatch.setattr(diagnostics.sys, 'stdout', None)
    def closed(*args):
        raise OSError('no stderr file descriptor')
    monkeypatch.setattr(diagnostics.os, 'write', closed)
    blocked = tmp_path / 'blocked'
    blocked.write_text('file')
    row = diagnostics.event_to(blocked, 'visible_failure')
    health = diagnostics.health()
    assert health['write_errors'] == 1
    assert health['last_error']['event_id'] == row['event_id']
    assert health['last_error']['exception_type'] in ('FileExistsError', 'NotADirectoryError')
    health['last_error']['message'] = 'caller mutation'
    assert diagnostics.health()['last_error']['message'] != 'caller mutation'


def test_shot_failure_is_visible_and_cannot_claim_an_artifact(monkeypatch):
    monkeypatch.setitem(logger.CONFIG['logging'], 'screenshot_on_trigger', True)
    monkeypatch.setattr(logger.cv2, 'imwrite', lambda *args: False)
    assert logger.shot(object(), 'evidence') is None
    row, = records(logger._LOG_DIR)
    assert row['kind'] == 'shot_failed'
    assert row['exception_type'] == 'OSError'
    assert row['exception_message'] == 'cv2.imwrite returned False'
    assert 'traceback' in row


def test_orbit_raw_events_and_status_retain_window_and_attempt_identity(monkeypatch):
    from types import SimpleNamespace
    from flows import shard
    state = SimpleNamespace(now=100., balance=200)
    monkeypatch.setattr(shard.time, 'monotonic', lambda: state.now)
    monkeypatch.setattr(shard.capture, 'grab', lambda: state.balance)
    monkeypatch.setattr(shard.capture, 'layout_offset', 0)
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda frame: 123)
    monkeypatch.setattr(shard.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: frame)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        SimpleNamespace(center=(680., 805.), rect=(640, 765, 80, 80), score=.99))
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *args: (True, 'live', args[1]))
    monkeypatch.setattr(shard, '_in_ability_row', lambda point: False)
    monkeypatch.setattr(shard.act, 'tap', lambda *args, **kwargs: {'dry_run': False})
    collector = shard.GemOrbitTapper(enabled=True, taps=1, jitter=0)
    collector.poll(200)
    active = collector.status()
    state.now, state.balance = 105., 202
    collector.poll(202)
    window, geometry, tap, claim = records(logger._LOG_DIR)
    assert geometry['kind'] == 'gem_orbit_geometry'
    assert window['kind'] == 'gem_orbit_window' and claim['kind'] == 'gem_orbit_claim'
    assert {row['window_id'] for row in (window, tap, claim)} == {active['window_id']}
    assert window['attempt_id'] == tap['attempt_id'] == active['attempt_id']
    assert claim['attempt_id'] != tap['attempt_id']
    assert collector.status()['last_outcome']['window_id'] == claim['window_id']
    assert collector.status()['last_outcome']['attempt_id'] == claim['attempt_id']
    assert 'window_id' not in logger.current_context()
