"""Streaming contracts with synthetic pixels and an injected device clock."""

import paths as test_paths
import importlib.util
import struct
import time
from pathlib import Path

import numpy as np
import pytest


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __getattr__(self, name):
        return getattr(time, name)

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def raw_frame(header=16):
    return struct.pack("<III", 4, 4, 1) + (bytes(4) if header == 16 else b"") + bytes(64)


@pytest.fixture
def stream(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "tp_dashboard_stream", test_paths.DASHBOARD)
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    clock = Clock()
    calls = []
    monkeypatch.setattr(dash, "ROOT", str(tmp_path / 'backend'))
    monkeypatch.setattr(dash, "time", clock)
    monkeypatch.setattr(dash, "load_config", lambda: {
        "active_instance": "main", "instances": {"main": {"serial": "emulator-5554", "preview": {"backend": "adb"}}}})
    monkeypatch.setattr(dash, "_game_display", lambda *args: "123")
    from device import adbclient

    def capture(serial, cmd, timeout):
        calls.append((serial, cmd, timeout))
        clock.now += 0.35
        return raw_frame()

    monkeypatch.setattr(adbclient, "exec_out", capture)
    return dash, clock, calls


@pytest.mark.parametrize("query", ["fps=oops", "fps=nan", "fps=inf", "fps=0", "fps=30.1", "backend=unknown",
                                  "fps=-1", "q=oops", "q=100", "q=29", "q=89.5",
                                  "scale=nan", "scale=inf", "scale=0", "scale=1.1",
                                  "stream_id=bad%20id", "instance=other"])
def test_invalid_stream_parameters_never_capture(stream, query):
    dash, _, calls = stream
    response = dash.app.test_client().get("/api/stream.mjpg?" + query)
    assert response.status_code in (400, 409)
    assert response.get_json()["ok"] is False
    assert not calls


@pytest.mark.parametrize("query,scale,quality,width,height", [
    ("", 1, 90, 100, 200),
    ("scale=1&q=90", 1, 90, 100, 200),
    ("scale=.65&q=85", .65, 85, 65, 130),
    ("scale=.4&q=70", .4, 70, 40, 80),
    ("scale=.2&q=30", .2, 30, 20, 40),
])
def test_stream_encodes_requested_resolution_and_quality(stream, monkeypatch, query, scale, quality,
                                                        width, height):
    import cv2
    from device import adbclient
    dash, _, _ = stream
    # Synthetic detail makes the actual JPEG quality observable in encoded bytes.
    pixels = np.arange(100 * 200 * 4, dtype=np.uint8).reshape(200, 100, 4)
    raw = struct.pack("<IIII", 100, 200, 1, 0) + pixels.tobytes()
    monkeypatch.setattr(adbclient, "exec_out", lambda *args, **kwargs: raw)
    client = dash.app.test_client()
    response = client.get("/api/stream.mjpg?stream_id=quality&" + query)
    try:
        part = next(iter(response.response))
        jpg = part.split(b"\r\n\r\n", 1)[1][:-2]
        image = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        assert image.shape == (height, width, 3)
        expected = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)
        if scale != 1:
            expected = cv2.resize(expected, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", expected, [cv2.IMWRITE_JPEG_QUALITY, quality])
        assert ok and jpg == encoded.tobytes()
        stats = client.get("/api/stream/status?stream_id=quality").get_json()
        assert (stats["width"], stats["height"], stats["quality"], stats["scale"]) == (width, height, quality, scale)
        assert stats["jpeg_bytes"] == len(jpg)
        assert stats["frames"] == 1 and stats["errors"] == 0
    finally:
        response.close()


@pytest.mark.parametrize("header", [12, 16])
@pytest.mark.parametrize("warning", [b"", b"[Warning] Multiple displays were found\n"])
def test_raw_decoder_accepts_android_headers_and_display_warning(stream, header, warning):
    dash, _, _ = stream
    image = dash._stream_decode(warning + raw_frame(header))
    assert image.shape == (4, 4, 3)
    assert np.count_nonzero(image) == 0


@pytest.mark.parametrize("raw", [b"", b"failed to take screenshot", raw_frame()[:-1],
                                struct.pack("<IIII", 4, 4, 4, 0) + bytes(64)])
def test_raw_decoder_rejects_bad_frame_instead_of_silently_freezing(stream, raw):
    dash, _, _ = stream
    with pytest.raises(ValueError):
        dash._stream_decode(raw)


def test_target_pacing_subtracts_capture_cost_and_measures_actual_frames(stream):
    dash, clock, calls = stream
    client = dash.app.test_client()
    response = client.get("/api/stream.mjpg?fps=2&stream_id=pace")
    iterator = iter(response.response)
    assert next(iterator).startswith(b"--frame")
    assert next(iterator).startswith(b"--frame")
    stats = client.get("/api/stream/status?stream_id=pace").get_json()
    assert stats["frames"] == 2
    assert stats["actual_fps"] == 2.0
    assert stats["capture_ms"] == 350.0
    assert stats["encode_ms"] == 0.0
    assert clock.sleeps == pytest.approx([0.15])
    assert calls == [("emulator-5554", "screencap -d 123", 2.0)] * 2
    response.close()
    assert len(calls) == 2
    assert client.get("/api/stream/status?stream_id=pace").get_json()["active"] is False


def test_capture_bottleneck_reports_actual_below_target_without_extra_sleep(stream):
    dash, clock, _ = stream
    client = dash.app.test_client()
    response = client.get("/api/stream.mjpg?fps=4&stream_id=slow")
    iterator = iter(response.response)
    next(iterator)
    first = client.get("/api/stream/status?stream_id=slow").get_json()
    assert first["actual_fps"] is None  # one frame cannot establish an interval
    next(iterator)
    stats = client.get("/api/stream/status?stream_id=slow").get_json()
    assert stats["actual_fps"] == 2.86
    assert stats["target_fps"] == 4.0
    assert not clock.sleeps
    response.close()


def test_low_rate_and_consumer_backpressure_do_not_queue_catchup_frames(stream):
    dash, clock, calls = stream
    client = dash.app.test_client()
    response = client.get("/api/stream.mjpg?fps=.5&stream_id=low")
    iterator = iter(response.response)
    next(iterator)
    next(iterator)
    assert clock.sleeps == pytest.approx([1.65])
    assert client.get("/api/stream/status?stream_id=low").get_json()["actual_fps"] == 0.5
    clock.now += 5  # slow receiver: no extra sleep and no capture burst
    next(iterator)
    assert len(calls) == 3
    assert len(clock.sleeps) == 1
    clock.now += 5
    assert client.get("/api/stream/status?stream_id=low").get_json()["actual_fps"] == 0
    response.close()


def test_capture_error_marker_is_not_counted_as_game_frame_and_recovery_is_visible(stream, monkeypatch):
    dash, clock, _ = stream
    from device import adbclient
    attempts = iter([b"display changed", raw_frame(), raw_frame()])
    monkeypatch.setattr(adbclient, "exec_out", lambda *args, **kwargs: next(attempts))
    forgotten = []
    monkeypatch.setattr(dash, "_forget_display", forgotten.append)
    client = dash.app.test_client()
    response = client.get("/api/stream.mjpg?stream_id=recover")
    iterator = iter(response.response)
    assert next(iterator).startswith(b"--frame")
    failed = client.get("/api/stream/status?stream_id=recover").get_json()
    assert failed["errors"] == 1 and failed["frames"] == 0
    assert "invalid raw screencap" in failed["last_error"]
    assert failed["actual_fps"] is None
    next(iterator)
    recovered = client.get("/api/stream/status?stream_id=recover").get_json()
    assert recovered["frames"] == 1 and recovered["errors"] == 1
    assert recovered["last_error"] is None
    assert clock.sleeps == [3.0]
    assert forgotten == ["emulator-5554"]
    response.close()


def test_session_store_is_bounded_and_never_exposes_internal_timestamps(stream, monkeypatch):
    dash, _, _ = stream
    monkeypatch.setattr(dash, "_STREAM_LIMIT", 1)
    client = dash.app.test_client()
    first = client.get("/api/stream.mjpg?stream_id=one")
    refused = client.get("/api/stream.mjpg?stream_id=two")
    assert refused.status_code == 429
    assert len(dash._STREAMS) == 1
    first.close()
    second = client.get("/api/stream.mjpg?stream_id=two")
    stats = client.get("/api/stream/status?stream_id=two").get_json()
    assert not any(key.startswith("_") for key in stats)
    assert client.get("/api/stream/status?stream_id=one").status_code == 404
    second.close()


def test_head_probes_never_reserve_sessions_or_capture_frames(stream, monkeypatch):
    dash, _, calls = stream
    monkeypatch.setattr(dash, "_STREAM_LIMIT", 2)
    client = dash.app.test_client()
    for number in range(4):
        response = client.head(f"/api/stream.mjpg?stream_id=head_{number}")
        assert response.status_code == 200
        assert response.data == b""
        assert response.headers["Content-Type"] == "multipart/x-mixed-replace; boundary=frame"
        assert response.headers["Cache-Control"] == "no-cache"
        response.close()
    assert not dash._STREAMS
    assert not calls
    response = client.get("/api/stream.mjpg?stream_id=after_heads")
    assert response.status_code == 200
    assert len(calls) == 1
    response.close()


def test_closing_unstarted_response_releases_session_without_fake_start_stop_events(stream, monkeypatch):
    dash, _, calls = stream
    monkeypatch.setattr(dash, "_STREAM_LIMIT", 1)
    events = []
    monkeypatch.setattr(dash, "_diagnostic_event", lambda kind, **fields: events.append((kind, fields)))
    # Calling the view builds the response while leaving its body unconsumed,
    # as happens when WSGI abandons a request before the first frame.
    with dash.app.test_request_context("/api/stream.mjpg?stream_id=unstarted"):
        response = dash.api_stream()
    assert dash._STREAMS["unstarted"]["active"] is True
    assert not calls
    response.close()
    response.close()
    assert dash._STREAMS["unstarted"]["active"] is False
    assert not calls
    assert not events
    with dash.app.test_request_context("/api/stream.mjpg?stream_id=replacement"):
        replacement = dash.api_stream()
    assert replacement.status_code == 200
    assert "unstarted" not in dash._STREAMS
    replacement.close()


def test_generator_and_response_close_emit_one_stop_for_started_stream(stream, monkeypatch):
    dash, _, calls = stream
    events = []
    monkeypatch.setattr(dash, "_diagnostic_event", lambda kind, **fields: events.append((kind, fields)))
    response = dash.app.test_client().get("/api/stream.mjpg?stream_id=started")
    assert len(calls) == 1
    response.close()
    response.close()
    assert [kind for kind, _ in events] == ["stream_started", "preview_backend_selected", "stream_stopped"]
    assert events[-1][1]["frames"] == 1
    assert events[-1][1]["errors"] == 0
    assert dash._STREAMS["started"]["active"] is False


def test_accelerated_preview_exposes_backend_and_closes_helper(stream, monkeypatch):
    from device.preview.manager import PreviewCapture
    dash, clock, calls = stream

    class Source:
        closed = 0

        def read(self, timeout):
            clock.now += .005
            return np.zeros((4, 4, 3), dtype=np.uint8)

        def close(self):
            self.closed += 1

    source = Source()
    monkeypatch.setattr(PreviewCapture, '_open', lambda self, backend: source)
    client = dash.app.test_client()
    response = client.get('/api/stream.mjpg?backend=mumu&fps=30&stream_id=native')
    iterator = iter(response.response)
    next(iterator)
    next(iterator)
    status = client.get('/api/stream/status?stream_id=native').get_json()
    assert status['backend'] == status['requested_backend'] == 'mumu'
    assert status['effective_fps'] == 30
    assert status['actual_fps'] == 30
    assert status['backend_failures'] == []
    assert not calls
    response.close()
    assert source.closed == 1


def test_screenshot_fallback_caps_high_target_and_explains_failure(stream, monkeypatch):
    from device.preview.manager import PreviewCapture
    from device.preview.common import PreviewUnavailable
    dash, _, calls = stream
    original = PreviewCapture._open

    def open_source(self, backend):
        if backend != 'adb':
            raise PreviewUnavailable(f'{backend} unavailable for this device')
        return original(self, backend)

    monkeypatch.setattr(PreviewCapture, '_open', open_source)
    client = dash.app.test_client()
    response = client.get('/api/stream.mjpg?backend=auto&fps=30&stream_id=fallback')
    status = client.get('/api/stream/status?stream_id=fallback').get_json()
    assert status['backend'] == 'adb'
    assert status['requested_backend'] == 'auto'
    assert status['target_fps'] == 30 and status['effective_fps'] == 4
    assert [row['backend'] for row in status['backend_failures']] == ['mumu', 'scrcpy']
    assert len(calls) == 1
    response.close()
