"""Offline scrcpy protocol/package tests; no connected emulator is contacted."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
from fractions import Fraction
from pathlib import Path
import re
import socket
import struct
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from device.preview import scrcpy, scrcpy_package as package
from device.preview.common import PreviewCleanupError, PreviewError, PreviewUnavailable


class FragmentedSocket:
    def __init__(self, sock):
        self.sock = sock

    def recv(self, count):
        return self.sock.recv(min(count, 3))

    def shutdown(self, how):
        self.sock.shutdown(how)

    def close(self):
        self.sock.close()


class ClockSocket:
    """A fragmented socket whose successful I/O advances an injected clock."""

    def __init__(self, clock, incoming=b"", receive_cost=0, send_cost=0):
        self.clock, self.incoming = clock, incoming
        self.receive_cost, self.send_cost = receive_cost, send_cost
        self.timeouts, self.sent = [], []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv(self, count):
        self.clock[0] += self.receive_cost
        chunk, self.incoming = self.incoming[:1], self.incoming[1:]
        return chunk

    def sendall(self, data):
        self.clock[0] += self.send_cost
        self.sent.append(data)

    def close(self):
        self.closed = True


def _preview():
    """Initialize only reader state; intentionally bypass all device startup."""
    preview = scrcpy.ScrcpyPreview.__new__(scrcpy.ScrcpyPreview)
    preview.serial, preview.scid = "offline-fixture", "000000ab"
    preview._remote = "/data/local/tmp/towerpilot_preview_000000ab.jar"
    preview._condition = threading.Condition()
    preview._stop = threading.Event()
    preview._closed = False
    preview._latest = preview._error = None
    preview._video = preview._server = None
    preview._reader = preview._logs = None
    preview._pid = None
    preview._pushed = False
    preview.last_frame_time = preview.last_decode_ms = None
    preview.server_output = ""
    return preview


class Decoder:
    def __init__(self, dimensions):
        self.dimensions = dimensions
        self.packets = []

    def decode(self, packet):
        self.packets.append(packet)
        image = np.full((*self.dimensions, 3), len(self.packets), np.uint8)
        return [SimpleNamespace(pts=packet.pts, to_ndarray=lambda format: image)]


class Packet:
    def __init__(self, data):
        self.data = data


def _session(width=3, height=2):
    return struct.pack(">III", 1 << 31, width, height)


def _packet(data=b"idr", flags=scrcpy._KEYFRAME, pts=1_000_000):
    return struct.pack(">QI", flags | pts, len(data)) + data


@contextmanager
def _reader(dimensions=((2, 3),)):
    preview = _preview()
    reader, writer = socket.socketpair()
    reader.settimeout(1)
    preview._video = FragmentedSocket(reader)
    codecs = []
    dimensions_iter = iter(dimensions)

    def create(*args):
        decoder = Decoder(next(dimensions_iter))
        codecs.append(decoder)
        return decoder

    preview._av = SimpleNamespace(CodecContext=SimpleNamespace(create=create), Packet=Packet)
    preview._reader = threading.Thread(target=preview._receive)
    preview._reader.start()
    try:
        yield preview, writer, codecs
    finally:
        preview.close()
        writer.close()


def test_fragmented_session_config_then_idr_produces_native_bgr():
    with _reader() as (preview, wire, codecs):
        wire.sendall(_session() + _packet(b"sps", scrcpy._CONFIG, 0)
                     + _packet(b"pps", scrcpy._CONFIG, 0) + _packet())
        image = preview.read(timeout=1)
        assert image.shape == (2, 3, 3)
        assert image.dtype == np.uint8
        assert codecs[0].packets[0].data == b"spsppsidr"
        assert codecs[0].packets[0].pts == 1_000_000
        assert preview.last_frame_time is not None
        with pytest.raises(PreviewError, match="fresh frame"):
            preview.read(timeout=.01)


def test_optional_real_pyav_decodes_synthetic_config_and_idr():
    """Exercise real FFmpeg decoding when the optional extra is installed."""
    av = pytest.importorskip("av")
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width = encoder.height = 16
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 15)
    encoder.framerate = Fraction(15, 1)
    encoder.options = {"preset": "ultrafast", "tune": "zerolatency"}
    pixels = np.zeros((16, 16, 3), np.uint8)
    pixels[:, :, 2] = 200
    frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
    encoded = b"".join(bytes(packet) for packet in encoder.encode(frame))
    assert encoded
    idr = next(match.start() for match in re.finditer(b"\x00\x00(?:\x00)?\x01", encoded)
               if encoded[match.end()] & 31 == 5)
    assert idr > 0  # SPS/PPS are sent as CONFIG before the IDR access unit.
    with _reader() as (preview, wire, _):
        preview._av = av
        wire.sendall(_session(16, 16) + _packet(encoded[:idr], scrcpy._CONFIG, 0)
                     + _packet(encoded[idr:]))
        result = preview.read(timeout=1)
        assert result.shape == (16, 16, 3)
        assert result.dtype == np.uint8
        assert result[:, :, 2].mean() > 150
        assert result[:, :, :2].max() < 10


def test_reader_decodes_all_delta_packets_and_keeps_one_latest_frame():
    with _reader() as (preview, wire, codecs):
        wire.sendall(_session() + b"".join(_packet(bytes([i]), pts=1_000_000 + i * 1000)
                                           for i in range(30)))
        with preview._condition:
            assert preview._condition.wait_for(lambda: len(codecs) == 1
                                               and len(codecs[0].packets) == 30, timeout=1)
        assert np.all(preview.read(timeout=1) == 30)
        assert len(codecs[0].packets) == 30


def test_session_reset_changes_dimensions_and_drops_old_codec_config():
    with _reader(((2, 3), (4, 5))) as (preview, wire, codecs):
        wire.sendall(_session() + _packet())
        assert preview.read(timeout=1).shape == (2, 3, 3)
        wire.sendall(_packet(b"old-config", scrcpy._CONFIG, 0)
                     + _session(5, 4) + _packet(b"new-idr", pts=100))
        assert preview.read(timeout=1).shape == (4, 5, 3)
        assert len(codecs) == 2
        assert codecs[1].packets[0].data == b"new-idr"


def test_stale_frame_and_consumed_frame_are_never_replayed():
    preview = _preview()
    preview._latest = (np.zeros((2, 3, 3), np.uint8), time.monotonic() - 10)
    with pytest.raises(PreviewError, match="fresh frame"):
        preview.read(timeout=0)
    assert preview._latest is None
    preview._latest = (np.zeros((2, 3, 3), np.uint8), time.monotonic())
    preview.read(timeout=0)
    with pytest.raises(PreviewError, match="fresh frame"):
        preview.read(timeout=0)


def test_frame_age_is_the_local_estimate_and_remains_unknown_until_delivery(monkeypatch):
    clock = [12.125]
    preview = _preview()
    monkeypatch.setattr(scrcpy, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert preview.frame_age_ms is None
    preview._latest = (np.zeros((2, 3, 3), np.uint8), 12.0)
    preview.read(timeout=0)
    assert preview.frame_age_ms == 125
    clock[0] = 12.5
    assert preview.frame_age_ms == 500


@pytest.mark.parametrize("wire_data, message", [
    (_packet(), "before SESSION"),
    (_session(0, 2), "session size"),
    (_session() + struct.pack(">QI", 1, scrcpy._MAX_PACKET + 1), "packet length"),
])
def test_invalid_protocol_is_reported_without_serving_cache(wire_data, message):
    with _reader() as (preview, wire, _):
        wire.sendall(wire_data)
        with pytest.raises(PreviewError, match=message):
            preview.read(timeout=1)


def test_mid_header_disconnect_is_visible():
    with _reader() as (preview, wire, _):
        wire.sendall(b"\x80\x00")
        wire.shutdown(socket.SHUT_WR)
        with pytest.raises(PreviewError, match="connection closed after 2/12"):
            preview.read(timeout=1)


def test_socket_timeout_fails_without_replaying_an_old_frame():
    with _reader() as (preview, wire, _):
        preview._video.sock.settimeout(.02)
        with pytest.raises(PreviewError, match="timed out"):
            preview.read(timeout=1)


def test_options_disable_input_power_audio_and_display_creation():
    options = scrcpy._options("000000ab", 7, 15, 8_000_000)
    for option in ("control=false", "audio=false", "power_on=false", "stay_awake=false",
                   "show_touches=false", "power_off_on_close=false", "keep_active=false",
                   "clipboard_autosync=false", "cleanup=false", "downsize_on_error=false",
                   "max_size=0", "display_id=7"):
        assert option in options
    assert not any(option.startswith("new_display=") for option in options)
    assert "max_fps=2" in scrcpy._options("000000ab", 7, .5, 8_000_000)


def test_push_sends_real_sync_wire_packets_and_file_mtime(monkeypatch, tmp_path):
    """Exercise the full SEND/DATA/DONE/QUIT path using only local sockets."""
    source = tmp_path / "synthetic-server.jar"
    content = bytes(range(256)) * 600
    source.write_bytes(content)
    client, server = socket.socketpair()
    server.settimeout(1)
    captured, failures = {}, []

    def transport(serial, service, timeout, deadline=None):
        assert (serial, service) == ("offline-fixture", "sync:")
        client.settimeout(timeout)
        return client

    def sync_peer():
        try:
            kind, length = struct.unpack("<4sI", scrcpy.adbclient._recv_exact(server, 8))
            assert kind == b"SEND"
            captured["target"] = scrcpy.adbclient._recv_exact(server, length)
            captured["chunks"] = []
            while True:
                kind, length = struct.unpack("<4sI", scrcpy.adbclient._recv_exact(server, 8))
                if kind == b"DONE":
                    captured["mtime"] = length
                    break
                assert kind == b"DATA"
                captured["chunks"].append(scrcpy.adbclient._recv_exact(server, length))
            server.sendall(struct.pack("<4sI", b"OKAY", 0))
            assert scrcpy.adbclient._recv_exact(server, 8) == struct.pack("<4sI", b"QUIT", 0)
        except Exception as exc:
            failures.append(exc)
        finally:
            server.close()

    monkeypatch.setattr(scrcpy, "_transport", transport)
    peer = threading.Thread(target=sync_peer)
    peer.start()
    try:
        scrcpy._push("offline-fixture", source, "/data/local/tmp/owned-fixture.jar", 1)
    finally:
        client.close()
        peer.join(2)
    assert not peer.is_alive()
    assert failures == []
    assert captured["target"] == b"/data/local/tmp/owned-fixture.jar,33200"
    assert b"".join(captured["chunks"]) == content
    assert [len(chunk) for chunk in captured["chunks"]] == [65536, 65536, 22528]
    assert captured["mtime"] == int(source.stat().st_mtime)


def test_transport_status_fragments_share_one_absolute_deadline(monkeypatch):
    clock = [0.0]
    sock = ClockSocket(clock, b"OKAYOKAY", receive_cost=.25)
    monkeypatch.setattr(scrcpy, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    connection_timeouts = []

    def connect(address, timeout):
        connection_timeouts.append(timeout)
        return sock

    monkeypatch.setattr(scrcpy.socket, "create_connection", connect)
    with pytest.raises(PreviewError, match="startup timed out"):
        scrcpy._transport("offline-fixture", "sync:", timeout=5, deadline=2)
    assert connection_timeouts == [2]
    assert len(sock.sent) == 2  # Both transport and service use the same budget.
    assert clock[0] == 2
    assert sock.timeouts[-1] == .25
    assert sock.closed


def test_handshake_slow_trickle_cannot_restart_timeout_for_each_fragment(monkeypatch):
    clock = [0.0]
    sock = ClockSocket(clock, b"\0" + b"device".ljust(64, b"\0") + b"h264", receive_cost=.25)
    monkeypatch.setattr(scrcpy, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    wire = scrcpy._DeadlineSocket(sock, deadline=1, timeout=8)
    assert scrcpy.adbclient._recv_exact(wire, 1) == b"\0"
    with pytest.raises(PreviewError, match="startup timed out"):
        scrcpy.adbclient._recv_exact(wire, 64)
    assert clock[0] == 1
    assert sock.timeouts == [1, .75, .5, .25]


def test_push_slow_sends_share_transport_and_entire_file_deadline(monkeypatch, tmp_path):
    clock = [0.0]
    sock = ClockSocket(clock, send_cost=.3)
    source = tmp_path / "synthetic.jar"
    source.write_bytes(b"x" * 140000)
    monkeypatch.setattr(scrcpy, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def transport(serial, service, timeout, deadline):
        assert (timeout, deadline) == (8, 1)
        clock[0] += .1
        return sock

    monkeypatch.setattr(scrcpy, "_transport", transport)
    with pytest.raises(PreviewError, match="startup timed out"):
        scrcpy._push("offline-fixture", source, "/data/local/tmp/owned.jar", timeout=8, deadline=1)
    assert [message[:4] for message in sock.sent] == [b"SEND", b"DATA", b"DATA"]
    assert sock.timeouts[-1] == pytest.approx(.3)
    assert sock.closed


@pytest.mark.parametrize("receive_frame, cleanup_fails", [(True, False), (False, False), (False, True)])
def test_startup_waits_for_first_frame_and_cleans_up_on_timeout(monkeypatch, tmp_path, receive_frame, cleanup_fails):
    video, video_source = socket.socketpair()
    server, server_source = socket.socketpair()
    server_source.sendall(b"TOWERPILOT_PREVIEW_PID_000000ab=12345\n")
    video_source.sendall(b"\0" + b"fixture-device".ljust(64, b"\0") + b"h264")
    if receive_frame:
        video_source.sendall(_session() + _packet())
    requests, cleanup, pushes = [], [], []
    process_terminated = False
    cleanup_recovered = False
    monkeypatch.setattr(scrcpy.secrets, "randbits", lambda bits: 0xab)
    monkeypatch.setattr(scrcpy, "verify_server", lambda path: Path(path))
    monkeypatch.setattr(scrcpy, "_STARTUP_TIMEOUT", .5)
    decoder = Decoder((2, 3))
    monkeypatch.setattr(scrcpy, "_load_decoder", lambda: SimpleNamespace(
        CodecContext=SimpleNamespace(create=lambda *args: decoder), Packet=Packet))
    monkeypatch.setattr(scrcpy, "_push", lambda *args: pushes.append(args))

    def transport(serial, service, timeout, deadline=None):
        assert serial == "offline-fixture"
        requests.append(service)
        return server if service.startswith("exec:") else video

    def execute(serial, command, timeout):
        nonlocal process_terminated
        cleanup.append(command)
        if command.startswith("if [ ! -d /proc/"):
            if cleanup_fails and not cleanup_recovered:
                return b"UNREADABLE\n"
            return (b"GONE\n" if process_terminated else
                    b"ARGV\napp_process\0com.genymobile.scrcpy.Server\0scid=000000ab\0")
        if "kill -TERM" in command:
            process_terminated = True
            return b""
        return b"PRESENT\n" if cleanup_fails and not cleanup_recovered else b"REMOVED\n"

    monkeypatch.setattr(scrcpy, "_transport", transport)
    monkeypatch.setattr(scrcpy, "_exec_bounded", execute)
    try:
        if receive_frame:
            preview = scrcpy.ScrcpyPreview("offline-fixture", 7, tmp_path / "server.jar")
            assert preview.read(timeout=0).shape == (2, 3, 3)
            preview.close()
            assert not preview._reader.is_alive()
            assert not preview._logs.is_alive()
        else:
            with pytest.raises(PreviewError, match="startup timed out") as caught:
                scrcpy.ScrcpyPreview("offline-fixture", 7, tmp_path / "server.jar")
            if cleanup_fails:
                assert isinstance(caught.value, PreviewCleanupError)
                assert "cleanup also failed: own-process cleanup:" in str(caught.value)
                assert isinstance(caught.value.__cause__, PreviewError)
                assert str(caught.value.__cause__) == "scrcpy startup timed out"
                retained = caught.value.source
                assert isinstance(retained, scrcpy.ScrcpyPreview)
                assert retained.scid == "000000ab"
                assert retained._pid == 12345
                assert retained._cleanup_error is not None
                cleanup_recovered = True
                retained.close()
                assert retained._cleanup_error is None
                # _reader exists only once the video wire is up; a loaded machine can spend the whole startup budget before that
                assert retained._reader is None or not retained._reader.is_alive()
                assert retained._logs is None or not retained._logs.is_alive()
        assert "control=false" in requests[0]
        assert "display_id=7" in requests[0]
        assert requests[1] == "localabstract:scrcpy_000000ab"
        assert pushes[0][2] == "/data/local/tmp/towerpilot_preview_000000ab.jar"
        assert any("kill -TERM 12345" in command for command in cleanup)
        assert cleanup[-1].startswith("rm -f /data/local/tmp/towerpilot_preview_000000ab.jar;")
    finally:
        for sock in (video, video_source, server, server_source):
            sock.close()


@pytest.mark.parametrize("cmdline, kill", [
    (b"app_process\0com.genymobile.scrcpy.Server\0scid=000000ab\0", True),
    (b"app_process\0com.genymobile.scrcpy.Server\0scid=000000abc\0", False),
    (b"app_process\0other.Server\0scid=000000ab\0", False),
    (b"", False),
])
def test_cleanup_checks_exact_owned_pid_and_scid(monkeypatch, cmdline, kill):
    preview = _preview()
    preview._pid, preview._pushed = 12345, True
    calls = []
    process_terminated = False

    def execute(serial, command, timeout):
        nonlocal process_terminated
        assert serial == "offline-fixture"
        calls.append(command)
        if command.startswith("if [ ! -d /proc/"):
            return b"GONE\n" if process_terminated else b"ARGV\n" + cmdline
        if "kill -TERM" in command:
            process_terminated = True
            return b""
        return b"REMOVED\n"

    monkeypatch.setattr(scrcpy, "_exec_bounded", execute)
    preview.close()
    preview.close()
    assert calls[0].startswith("if [ ! -d /proc/12345 ];")
    kill_commands = [command for command in calls if "kill -TERM" in command]
    assert bool(kill_commands) is kill
    if kill:
        assert "com.genymobile.scrcpy.Server)" in kill_commands[0]
        assert "scid=000000ab)" in kill_commands[0]
        assert "kill -TERM 12345" in kill_commands[0]
    assert calls[-1].startswith("rm -f /data/local/tmp/towerpilot_preview_000000ab.jar;")
    assert len(calls) == (4 if kill else 2)


def test_cleanup_reports_unreadable_process_and_failed_jar_removal(monkeypatch):
    preview = _preview()
    preview._pid, preview._pushed = 12345, True
    calls = []
    recovered = False

    def execute(serial, command, timeout):
        calls.append(command)
        if command.startswith("if [ ! -d"):
            return b"GONE\n" if recovered else b"UNREADABLE\n"
        return b"REMOVED\n" if recovered else b"PRESENT\n"

    monkeypatch.setattr(scrcpy, "_exec_bounded", execute)
    with pytest.raises(PreviewError, match="own-process cleanup.*own-jar cleanup"):
        preview.close()
    assert len(calls) == 2
    assert not any("kill -TERM" in command for command in calls)
    with pytest.raises(PreviewError, match="could not verify"):
        preview.close()
    assert len(calls) == 4  # Failed cleanup is retried, rather than replayed.
    recovered = True
    preview.close()
    assert len(calls) == 6
    assert preview._cleanup_error is None
    preview.close()
    assert len(calls) == 6  # Successful cleanup remains idempotent.


def test_cleanup_reports_owned_process_that_does_not_exit(monkeypatch):
    preview = _preview()
    preview._pid = 12345
    monkeypatch.setattr(scrcpy, "_CLEANUP_TIMEOUT", .02)
    monkeypatch.setattr(scrcpy, "_exec_bounded", lambda *args, **kwargs:
                        b"ARGV\napp_process\0com.genymobile.scrcpy.Server\0scid=000000ab\0")
    with pytest.raises(PreviewError, match="remains after TERM"):
        preview.close()


def test_cleanup_reports_threads_that_fail_to_stop():
    preview = _preview()
    preview._reader = SimpleNamespace(name="stuck-reader", join=lambda timeout: None,
                                      is_alive=lambda: True)
    with pytest.raises(PreviewError, match="thread did not stop: stuck-reader"):
        preview.close()


def test_slow_trickle_cleanup_is_bounded_and_retained_source_can_recover(monkeypatch):
    preview = _preview()
    preview._pid, preview._pushed = 12345, True
    clock, sockets, commands = [0.0], [], []
    recovered = False
    monkeypatch.setattr(scrcpy, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(scrcpy, "_CLEANUP_TIMEOUT", 1)

    def transport(serial, service, timeout, deadline, context):
        assert serial == "offline-fixture"
        assert context == "cleanup"
        commands.append(service)
        if recovered:
            content = b"GONE\n" if "/proc/12345" in service else b"REMOVED\n"
            sock = ClockSocket(clock, content)
        else:
            sock = ClockSocket(clock, b"ARGV\napp_process\0com.genymobile.scrcpy.Server\0", receive_cost=.25)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(scrcpy, "_transport", transport)
    with pytest.raises(PreviewError, match="cleanup timed out"):
        preview.close()
    assert clock[0] == 1
    assert preview._pid == 12345
    assert preview._pushed
    assert preview._cleanup_error is not None
    assert len(commands) == 1  # Exhausted budget prevents another device request.
    assert sockets[0].closed
    recovered = True
    preview.close()
    assert preview._cleanup_error is None
    assert len(commands) == 3
    assert all(sock.closed for sock in sockets)


def test_cleanup_exec_rejects_unbounded_output(monkeypatch):
    clock = [0.0]
    sock = ClockSocket(clock, b"x" * 16385)
    monkeypatch.setattr(scrcpy, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(scrcpy, "_transport", lambda *args: sock)
    with pytest.raises(PreviewError, match="response exceeds size limit"):
        scrcpy._exec_bounded("offline-fixture", "fixture-command", deadline=1)
    assert sock.closed


def test_missing_or_invalid_package_does_not_touch_device(monkeypatch, tmp_path):
    monkeypatch.setattr(scrcpy, "_transport", lambda *args: pytest.fail("device accessed"))
    with pytest.raises(PreviewUnavailable, match="missing"):
        scrcpy.ScrcpyPreview("offline-fixture", 0, tmp_path / "missing")
    invalid = tmp_path / "invalid.jar"
    invalid.write_bytes(b"invalid")
    with pytest.raises(PreviewUnavailable, match="checksum mismatch"):
        scrcpy.ScrcpyPreview("offline-fixture", 0, invalid)


def test_missing_decoder_is_optional_and_does_not_touch_device(monkeypatch, tmp_path):
    monkeypatch.setattr(scrcpy, "verify_server", lambda path: Path(path))
    monkeypatch.setattr(scrcpy, "_transport", lambda *args: pytest.fail("device accessed"))

    def missing(name):
        raise ModuleNotFoundError("No module named av")

    monkeypatch.setattr(scrcpy.importlib, "import_module", missing)
    with pytest.raises(PreviewUnavailable, match="PyAV unavailable"):
        scrcpy.ScrcpyPreview("offline-fixture", 0, tmp_path / "server")


def test_package_status_is_local_and_reports_absence(monkeypatch, tmp_path):
    monkeypatch.setattr(package.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("network"))
    monkeypatch.setattr(package.importlib.util, "find_spec", lambda name: None)
    result = package.status(tmp_path / "missing.jar")
    assert result["installed"] is False
    assert result["available"] is False
    assert result["decoder_available"] is False
    assert result["version"] == "5.0.1"
    assert "PyAV missing" in result["reason"]


def test_explicit_install_verifies_then_atomically_publishes(monkeypatch, tmp_path):
    content = b"synthetic pinned server"
    monkeypatch.setattr(package, "SERVER_SHA256", hashlib.sha256(content).hexdigest())
    requests = []

    def download(url, timeout):
        requests.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(package.urllib.request, "urlopen", download)
    target = tmp_path / "scrcpy" / "server.jar"
    assert package.install(target) == target
    assert target.read_bytes() == content
    assert requests == [package.SERVER_URL]
    package.install(target)
    assert len(requests) == 1
    assert list(target.parent.iterdir()) == [target]


def test_bad_download_preserves_previous_package_and_removes_temp(monkeypatch, tmp_path):
    target = tmp_path / "server.jar"
    target.write_bytes(b"previous local bytes")
    monkeypatch.setattr(package.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong"))
    with pytest.raises(PreviewUnavailable, match="checksum mismatch"):
        package.install(target)
    assert target.read_bytes() == b"previous local bytes"
    assert list(tmp_path.iterdir()) == [target]
