"""Preview selection and ownership with synthetic frames and fake device sources."""
import socket
import subprocess

import numpy as np
import pytest

from device import adbclient
from device.preview.common import PreviewCleanupError, PreviewError, PreviewUnavailable, options
from device.preview.manager import PreviewCapture


@pytest.fixture(autouse=True)
def no_live_device_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Preview manager test attempted live device I/O')

    monkeypatch.setattr(adbclient, 'shell', forbidden)
    monkeypatch.setattr(adbclient, 'exec_out', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def pixels():
    return np.full((8, 4, 3), 23, dtype=np.uint8)


class Source:
    def __init__(self, *results, frame_age_ms=None, close_error=None):
        self.results = iter(results)
        self.timeouts = []
        self.closed = 0
        self.frame_age_ms = frame_age_ms
        self.close_error = close_error

    def read(self, timeout=2.0):
        self.timeouts.append(timeout)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def capture(pixels, **overrides):
    arguments = dict(serial='synthetic-emulator', adb_exe='unused-adb', settings=None,
                     raw_capture=lambda: pixels, physical_display=lambda: None)
    arguments.update(overrides)
    return PreviewCapture(**arguments)


def test_auto_uses_available_accelerated_source_and_closes_only_owned_source(monkeypatch, pixels):
    native = Source(pixels, pixels, frame_age_ms=12)
    probes, events = [], []

    def open_source(self, backend):
        probes.append(backend)
        if backend == 'mumu':
            raise PreviewUnavailable('MuMu is not installed')
        if backend == 'scrcpy':
            return native
        pytest.fail('Screenshot fallback opened despite available video')

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    preview = capture(pixels, event=lambda kind, **fields: events.append((kind, fields)))
    try:
        assert preview.read(timeout=.25) is pixels
        assert preview.read(timeout=.5) is pixels
        assert probes == ['mumu', 'scrcpy']
        status = preview.status()
        assert status['backend'] == 'scrcpy' and status['requested_backend'] == 'auto'
        assert status['effective_fps'] == 15 and status['frame_age_ms'] == 12
        assert status['backend_failures'][0]['reason'] == 'MuMu is not installed'
        selected = [fields for kind, fields in events if kind == 'preview_backend_selected']
        assert len(selected) == 1 and selected[0]['fallback'] is True
        assert native.timeouts == [.25, .5]
    finally:
        preview.close()
        preview.close()
    assert native.closed == 1
    with pytest.raises(PreviewError, match='closed'):
        preview.read()


def test_timeout_discards_failed_native_source_before_switching(monkeypatch, pixels):
    native, backup = Source(pixels, TimeoutError('new frame timed out')), Source(pixels)
    opened = []

    def open_source(self, backend):
        opened.append(backend)
        if backend == 'mumu':
            return native
        assert native.closed == 1, 'Replacement must not overlap the failed source'
        return backup

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    preview = capture(pixels, requested_backend='mumu', fps=30)
    try:
        assert preview.read() is pixels
        assert preview.status()['backend'] == 'mumu'
        assert preview.read() is pixels
        assert opened == ['mumu', 'scrcpy']
        status = preview.status()
        assert status['backend'] == 'scrcpy' and status['effective_fps'] == 30
        assert status['backend_failures'][0]['reason'] == 'new frame timed out'
    finally:
        preview.close()
    assert native.closed == backup.closed == 1


def test_missing_optional_decoder_uses_screenshots_without_installing(monkeypatch, tmp_path, pixels):
    from device.preview import scrcpy_package

    monkeypatch.setattr(scrcpy_package, 'verify_server', lambda path: tmp_path / 'server')
    monkeypatch.setattr(scrcpy_package.importlib.util, 'find_spec', lambda name: None)
    preview = capture(pixels, requested_backend='scrcpy', fps=30)
    try:
        assert preview.read() is pixels
        status = preview.status()
        assert status['backend'] == 'adb' and status['effective_fps'] == 4
        assert 'PyAV missing' in status['backend_failures'][0]['reason']
        assert not list(tmp_path.iterdir())
    finally:
        preview.close()


def test_screenshot_transient_failure_retries_without_reprobing_video(monkeypatch, pixels):
    probes, attempts = [], []
    results = iter([OSError('device offline'), pixels, pixels])
    original = PreviewCapture._open

    def raw_capture():
        attempts.append('screenshot')
        value = next(results)
        if isinstance(value, Exception):
            raise value
        return value

    def open_source(self, backend):
        probes.append(backend)
        if backend != 'adb':
            raise PreviewUnavailable(f'{backend} unavailable')
        return original(self, backend)

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    preview = capture(pixels, raw_capture=raw_capture, fps=30)
    try:
        with pytest.raises(PreviewError, match='Screenshot fallback failed: device offline'):
            preview.read()
        assert preview.read() is pixels
        assert preview.read() is pixels
        assert probes == ['mumu', 'scrcpy', 'adb', 'adb']
        assert len(attempts) == 3
        status = preview.status()
        assert status['backend'] == 'adb' and status['effective_fps'] == 4
        assert {failure['backend'] for failure in status['backend_failures']} == {'mumu', 'scrcpy', 'adb'}
    finally:
        preview.close()


def test_explicit_display_skips_mumu_and_passes_resolved_logical_display(monkeypatch, pixels):
    from device.preview import mumu, scrcpy, scrcpy_package

    def unexpected_mumu(*args, **kwargs):
        pytest.fail('Explicit display must bypass MuMu native capture')

    monkeypatch.setattr(mumu, 'discover_target', unexpected_mumu)
    monkeypatch.setattr(scrcpy_package, 'status', lambda: {'available': True, 'server_path': 'synthetic-server'})
    monkeypatch.setattr(adbclient, 'shell', lambda *args, **kwargs:
                        b'mDisplayId=7\n  mPrimaryDisplayDevice=game (local:123)\n')
    native, opened = Source(pixels), []

    def scrcpy_source(*args, **kwargs):
        opened.append((args, kwargs))
        return native

    monkeypatch.setattr(scrcpy, 'ScrcpyPreview', scrcpy_source)
    preview = capture(pixels, explicit_display=True, physical_display=lambda: '123')
    try:
        assert preview.read() is pixels
        assert opened[0][0] == ('synthetic-emulator', 7, 'synthetic-server')
        assert preview.status()['backend'] == 'scrcpy'
        assert 'explicit physical display' in preview.status()['backend_failures'][0]['reason']
    finally:
        preview.close()
    assert native.closed == 1


def test_unresolved_game_display_refuses_scrcpy_instead_of_guessing_zero(monkeypatch, pixels):
    from device.preview import scrcpy, scrcpy_package

    monkeypatch.setattr(scrcpy_package, 'status', lambda: {'available': True, 'server_path': 'synthetic-server'})
    monkeypatch.setattr(adbclient, 'shell', lambda *args, **kwargs:
                        b'mDisplayId=0\n  mPrimaryDisplayDevice=launcher (local:999)\n')

    def unexpected_scrcpy(*args, **kwargs):
        pytest.fail('Unresolved game display must never start scrcpy')

    monkeypatch.setattr(scrcpy, 'ScrcpyPreview', unexpected_scrcpy)
    preview = capture(pixels, requested_backend='scrcpy', physical_display=lambda: '123')
    try:
        assert preview.read() is pixels
        status = preview.status()
        assert status['backend'] == 'adb'
        assert "Cannot resolve the game's logical display" in status['backend_failures'][0]['reason']
    finally:
        preview.close()


@pytest.mark.parametrize('bad_frame', [None, b'pixels', np.empty((0, 4, 3), dtype=np.uint8),
                                    np.zeros((4, 4), dtype=np.uint8),
                                    np.zeros((4, 4, 4), dtype=np.uint8),
                                    np.zeros((4, 4, 3), dtype=np.float64)])
def test_invalid_video_frame_is_closed_and_never_delivered(monkeypatch, pixels, bad_frame):
    invalid, screenshots = Source(bad_frame), Source(pixels)
    monkeypatch.setattr(PreviewCapture, '_open', lambda self, backend:
                        screenshots if backend == 'adb' else invalid)
    preview = capture(pixels, requested_backend='scrcpy')
    try:
        assert preview.read() is pixels
        assert invalid.closed == 1
        assert preview.status()['backend'] == 'adb'
        assert 'invalid BGR pixels' in preview.status()['backend_failures'][0]['reason']
    finally:
        preview.close()
    assert screenshots.closed == 1


def test_cleanup_failure_is_observable_and_stops_replacement(monkeypatch, pixels):
    broken = Source(TimeoutError('frame timed out'), close_error=OSError('worker did not stop'))
    probes = []

    def open_source(self, backend):
        probes.append(backend)
        return broken

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    preview = capture(pixels)
    with pytest.raises(PreviewError, match='Preview cleanup failed: worker did not stop'):
        preview.read()
    assert probes == ['mumu']
    assert any(failure['stage'] == 'cleanup' and failure['reason'] == 'worker did not stop'
               for failure in preview.status()['backend_failures'])
    with pytest.raises(PreviewError, match='Preview cleanup failed: worker did not stop'):
        preview.read()
    assert probes == ['mumu']
    assert len(broken.timeouts) == 1
    with pytest.raises(PreviewError, match='Preview cleanup failed: worker did not stop'):
        preview.close()
    broken.close_error = None
    preview.close()
    preview.close()
    assert broken.closed == 4


def test_recovered_cleanup_allows_fallback_without_reusing_failed_source(monkeypatch, pixels):
    broken = Source(TimeoutError('frame timed out'), close_error=OSError('still stopping'))
    backup = Source(pixels)
    probes = []

    def open_source(self, backend):
        probes.append(backend)
        return broken if backend == 'mumu' else backup

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    preview = capture(pixels)
    with pytest.raises(PreviewError, match='still stopping'):
        preview.read()
    broken.close_error = None
    try:
        assert preview.read() is pixels
        assert probes == ['mumu', 'scrcpy']
        assert len(broken.timeouts) == 1
        assert broken.closed == 2
    finally:
        preview.close()


def test_close_before_first_frame_does_not_open_a_source(monkeypatch, pixels):
    def unexpected_open(*args, **kwargs):
        pytest.fail('A closed preview must never open a source')

    monkeypatch.setattr(PreviewCapture, '_open', unexpected_open)
    preview = capture(pixels)
    preview.close()
    preview.close()
    with pytest.raises(PreviewError, match='closed'):
        preview.read()


@pytest.mark.parametrize('settings', [[], {'backend': 'unknown'}, {'backend': True},
    {'fps': 0}, {'fps': 31}, {'fps': float('inf')}, {'fps': float('nan')}, {'fps': True},
    {'mumu': None}, {'mumu': {'index': -2}}, {'mumu': {'index': 1025}},
    {'mumu': {'index': True}}, {'mumu': {'install_dir': None}},
    {'mumu': {'install_dir': 'bad\x00path'}}, {'mumu': {'install_dir': 'a' * 4097}},
    {'scrcpy': None}, {'scrcpy': {'bitrate': 999999}}, {'scrcpy': {'bitrate': 40000001}},
    {'scrcpy': {'bitrate': 8000000.5}}, {'scrcpy': {'bitrate': True}}])
def test_invalid_config_is_rejected_before_device_work(pixels, settings):
    with pytest.raises(ValueError):
        capture(pixels, settings=settings)


def test_unknown_browser_override_is_rejected_before_device_work(pixels):
    with pytest.raises(ValueError, match='backend must be'):
        capture(pixels, requested_backend='unknown')


def test_failed_constructor_retains_cleanup_ownership_before_any_fallback(monkeypatch, pixels):
    partial = Source(close_error=OSError('helper still alive'))
    backup = Source(pixels)
    probes = []

    def open_source(self, backend):
        probes.append(backend)
        if backend == 'mumu':
            raise PreviewCleanupError('startup failed; helper still alive', partial)
        return backup

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    preview = capture(pixels)
    with pytest.raises(PreviewError, match='helper still alive'):
        preview.read()
    assert probes == ['mumu']
    assert partial.closed == 1
    assert preview._source is partial
    assert any(row['stage'] == 'cleanup' for row in preview.status()['backend_failures'])
    partial.close_error = None
    try:
        assert preview.read() is pixels
        assert probes == ['mumu', 'scrcpy']
        assert partial.closed == 2
        assert not partial.timeouts
    finally:
        preview.close()


def test_options_defaults_do_not_share_mutable_nested_settings():
    first = options({'mumu': {'index': 0}})
    first['mumu']['install_dir'] = 'changed locally'
    second = options()
    assert second['mumu'] == {'install_dir': '', 'index': -1}
    assert options({'fps': .5, 'mumu': {'index': 1024}, 'scrcpy': {'bitrate': 40000000}})['fps'] == .5
