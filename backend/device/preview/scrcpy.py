"""Capture-only scrcpy 5 protocol, using the existing socket ADB client.

One reader decodes every access unit so H.264 reference frames stay valid.
Consumers take a bounded, latest-frame slot; no delta packet queue accumulates.
"""

from __future__ import annotations

from contextlib import closing
from fractions import Fraction
import importlib
import logging
import math
from pathlib import Path
import secrets
import shlex
import socket
import struct
import threading
import time
from types import ModuleType

import numpy as np

from device import adbclient
from .common import PreviewCleanupError, PreviewError, PreviewUnavailable
from .scrcpy_package import VERSION, verify_server

_LOG = logging.getLogger(__name__)
_SESSION = 1 << 63
_CONFIG = 1 << 62
_KEYFRAME = 1 << 61
_PTS_MASK = _KEYFRAME - 1
_MAX_PACKET = 32 * 1024 * 1024
_MAX_FRAME_AGE = 1.0
_STARTUP_TIMEOUT = 8.0
_CLEANUP_TIMEOUT = 4.0


def _time_left(deadline: float, context: str = "startup") -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PreviewError(f"scrcpy {context} timed out")
    return remaining


class _DeadlineSocket:
    """Apply one absolute deadline inside adbclient's fragmented wire reads."""

    def __init__(self, sock: socket.socket, deadline: float, timeout: float,
                 context: str = "startup") -> None:
        self.sock, self.deadline, self.timeout = sock, deadline, timeout
        self.context = context

    def _prepare(self) -> None:
        self.sock.settimeout(min(self.timeout, _time_left(self.deadline, self.context)))

    def recv(self, count: int) -> bytes:
        self._prepare()
        result = self.sock.recv(count)
        _time_left(self.deadline, self.context)
        return result

    def sendall(self, data: bytes) -> None:
        self._prepare()
        self.sock.sendall(data)
        _time_left(self.deadline, self.context)


def _transport(serial: str, service: str, timeout: float,
               deadline: float | None = None, context: str = "startup") -> socket.socket:
    """Open a persistent service using adbclient's shared wire protocol."""
    if deadline is None:
        deadline = time.monotonic() + timeout
    sock = socket.create_connection(adbclient._HOST, timeout=min(timeout, _time_left(deadline, context)))
    try:
        wire = _DeadlineSocket(sock, deadline, timeout, context)
        adbclient._send(wire, f"host:transport:{serial}")
        adbclient._status(wire, "transport")
        adbclient._send(wire, service)
        adbclient._status(wire, service)
        return sock
    except BaseException:
        sock.close()
        raise


def _exec_bounded(serial: str, command: str, deadline: float) -> bytes:
    """Small cleanup responses share one deadline through ADB negotiation/EOF."""
    timeout = min(1.0, _time_left(deadline, "cleanup"))
    with closing(_transport(serial, f"exec:{command}", timeout, deadline, "cleanup")) as sock:
        wire = _DeadlineSocket(sock, deadline, timeout, "cleanup")
        output = bytearray()
        while chunk := wire.recv(4096):
            output.extend(chunk)
            if len(output) > 16384:
                raise PreviewError("scrcpy cleanup response exceeds size limit")
        return bytes(output)


def _push(serial: str, source: Path, remote: str, timeout: float,
          deadline: float | None = None) -> None:
    if deadline is None:
        deadline = time.monotonic() + timeout
    with closing(_transport(serial, "sync:", timeout, deadline)) as sock:
        wire = _DeadlineSocket(sock, deadline, timeout)
        destination = f"{remote},33200".encode()  # regular file, mode 0600
        wire.sendall(struct.pack("<4sI", b"SEND", len(destination)) + destination)
        with source.open("rb") as jar:
            while chunk := jar.read(65536):
                wire.sendall(struct.pack("<4sI", b"DATA", len(chunk)) + chunk)
        wire.sendall(struct.pack("<4sI", b"DONE", int(source.stat().st_mtime)))
        code, size = struct.unpack("<4sI", adbclient._recv_exact(wire, 8))
        if code != b"OKAY":
            if size > 65536:
                raise PreviewError("invalid ADB sync error length")
            reason = adbclient._recv_exact(wire, size).decode(errors="replace")
            raise PreviewError(f"scrcpy server push failed: {reason}")
        wire.sendall(struct.pack("<4sI", b"QUIT", 0))


def _load_decoder() -> ModuleType:
    try:
        return importlib.import_module("av")
    except (ImportError, OSError) as exc:
        raise PreviewUnavailable("PyAV unavailable; install requirements-scrcpy.txt") from exc


def _options(scid: str, display_id: int, fps: float, bitrate: int) -> list[str]:
    # The dashboard may deliver at 0.5 fps; keep capture fresh between requests.
    # SurfaceEncoder repeats idle frames after 100ms, subject to max-fps.
    capture_fps = max(2.0, fps)
    return [f"scid={scid}", "log_level=warn", "video=true", "audio=false",
            "control=false", "tunnel_forward=true", "video_codec=h264",
            f"video_bit_rate={bitrate}", f"max_fps={capture_fps:g}", "max_size=0",
            f"display_id={display_id}", "power_on=false", "stay_awake=false",
            "show_touches=false", "power_off_on_close=false", "keep_active=false",
            "clipboard_autosync=false", "cleanup=false", "downsize_on_error=false",
            "send_device_meta=true", "send_frame_meta=true", "send_dummy_byte=true",
            "send_stream_meta=true"]


def _owned(cmdline: bytes, scid: str) -> bool:
    tokens = cmdline.split(b"\0")
    return (b"com.genymobile.scrcpy.Server" in tokens
            and f"scid={scid}".encode() in tokens)


class ScrcpyPreview:
    """A native-size BGR preview of an explicitly selected existing display."""

    def __init__(self, serial: str, display_id: int, server_path: str | Path,
                 fps: float = 15, bitrate: int = 8_000_000) -> None:
        if not serial or isinstance(display_id, bool) or not isinstance(display_id, int) or display_id < 0:
            raise PreviewUnavailable("scrcpy needs a serial and existing nonnegative display ID")
        if (isinstance(fps, bool) or not isinstance(fps, (int, float))
                or not math.isfinite(fps) or not 0 < fps <= 240
                or isinstance(bitrate, bool) or not isinstance(bitrate, int)
                or not 1 <= bitrate <= 100_000_000):
            raise PreviewUnavailable("invalid scrcpy fps or bitrate")
        source = verify_server(server_path)
        self._av = _load_decoder()
        self.serial = serial
        self.display_id = display_id
        self.scid = f"{secrets.randbits(31):08x}"
        self._remote = f"/data/local/tmp/towerpilot_preview_{self.scid}.jar"
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._closed = False
        self._cleanup_error: str | None = None
        self._latest: tuple[np.ndarray, float] | None = None
        self._error: Exception | None = None
        self._video: socket.socket | None = None
        self._server: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._logs: threading.Thread | None = None
        self._pid: int | None = None
        self._pushed = False
        self.last_frame_time: float | None = None
        self.last_decode_ms: float | None = None
        self.server_output = ""
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        try:
            self._pushed = True  # Also remove a partial SEND after failure.
            _push(serial, source, self._remote, self._remaining(deadline), deadline)
            marker = f"TOWERPILOT_PREVIEW_PID_{self.scid}="
            inner = (f"printf '{marker}%s\\n' \"$$\"; export CLASSPATH={shlex.quote(self._remote)}; "
                     f"exec app_process / com.genymobile.scrcpy.Server {VERSION} "
                     + " ".join(_options(self.scid, display_id, fps, bitrate)))
            self._server = _transport(serial, "exec:sh -c " + shlex.quote(inner), self._remaining(deadline), deadline)
            server_wire = _DeadlineSocket(self._server, deadline, _STARTUP_TIMEOUT)
            line = bytearray()
            while not line.endswith(b"\n") and len(line) < 256:
                line.extend(adbclient._recv_exact(server_wire, 1))
            pid_text = line.decode(errors="replace").strip().removeprefix(marker)
            if not line.startswith(marker.encode()) or not pid_text.isdecimal() or int(pid_text) <= 1:
                raise PreviewError("scrcpy own-process PID marker missing")
            self._pid = int(pid_text)
            self._server.settimeout(.25)
            self._logs = threading.Thread(target=self._drain_logs, name=f"scrcpy-log-{self.scid}", daemon=True)
            self._logs.start()
            while self._video is None:
                try:
                    self._video = _transport(serial, f"localabstract:scrcpy_{self.scid}",
                                             min(.5, self._remaining(deadline)), deadline)
                except (OSError, adbclient.AdbError):
                    self._remaining(deadline)
                    self._stop.wait(.05)
            video_wire = _DeadlineSocket(self._video, deadline, _STARTUP_TIMEOUT)
            if adbclient._recv_exact(video_wire, 1) != b"\0":
                raise PreviewError("invalid scrcpy connection byte")
            adbclient._recv_exact(video_wire, 64)  # device name
            if adbclient._recv_exact(video_wire, 4) != b"h264":
                raise PreviewError("scrcpy did not provide the requested H.264 stream")
            self._video.settimeout(2.0)
            self._reader = threading.Thread(target=self._receive, name=f"scrcpy-video-{self.scid}", daemon=True)
            self._reader.start()
            with self._condition:
                while self._latest is None and self._error is None:
                    self._condition.wait(self._remaining(deadline))
                if self._error is not None:
                    raise PreviewError(f"scrcpy startup failed: {self._error}") from self._error
        except Exception as exc:
            try:
                self.close()
            except PreviewError as cleanup:
                raise PreviewCleanupError(f"{exc}; scrcpy cleanup also failed: {cleanup}", self) from exc
            if isinstance(exc, PreviewError):
                raise
            raise PreviewError(f"scrcpy startup failed: {exc}") from exc

    @staticmethod
    def _remaining(deadline: float) -> float:
        return _time_left(deadline)

    def _drain_logs(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                chunk = self._server.recv(4096)
                if not chunk:
                    return
                self.server_output = (self.server_output + chunk.decode(errors="replace"))[-4096:]
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    _LOG.debug("scrcpy server log closed: %s", exc)
                return

    def _receive(self) -> None:
        assert self._video is not None
        codec = None
        dimensions: tuple[int, int] | None = None
        pending_config = b""
        anchor: tuple[int, float] | None = None
        try:
            while not self._stop.is_set():
                header = adbclient._recv_exact(self._video, 12)
                flags, size = struct.unpack(">QI", header)
                if flags & _SESSION:
                    _, width, height = struct.unpack(">III", header)
                    if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > 33_554_432:
                        raise PreviewError(f"invalid scrcpy session size {width}x{height}")
                    dimensions = (height, width)
                    codec = self._av.CodecContext.create("h264", "r")
                    codec.thread_count = 1
                    pending_config, anchor = b"", None
                    with self._condition:
                        self._latest = None
                    continue
                if dimensions is None or codec is None:
                    raise PreviewError("scrcpy media arrived before SESSION")
                if not 0 < size <= _MAX_PACKET:
                    raise PreviewError(f"invalid scrcpy packet length {size}")
                payload = adbclient._recv_exact(self._video, size)
                arrived = time.monotonic()
                if flags & _CONFIG:
                    if len(pending_config) + size > _MAX_PACKET:
                        raise PreviewError("scrcpy codec configuration exceeds size limit")
                    pending_config += payload
                    continue
                pts = flags & _PTS_MASK
                if anchor is None:
                    anchor = (pts, arrived)
                packet = self._av.Packet(pending_config + payload)
                packet.pts = packet.dts = pts
                packet.time_base = Fraction(1, 1_000_000)
                pending_config = b""
                decode_started = time.monotonic()
                frames = codec.decode(packet)
                self.last_decode_ms = (time.monotonic() - decode_started) * 1000
                if not frames:
                    continue
                # Decode all reference frames; only convert the newest output.
                newest = frames[-1]
                image = newest.to_ndarray(format="bgr24")
                if image.shape != (*dimensions, 3):
                    raise PreviewError(f"scrcpy frame size disagrees with SESSION: {image.shape}")
                frame_pts = pts if newest.pts is None else newest.pts
                acquired = min(arrived, anchor[1] + (frame_pts - anchor[0]) / 1_000_000)
                with self._condition:
                    self._latest = (image, acquired)
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                if not self._stop.is_set():
                    self._error = exc
                self._latest = None
                self._condition.notify_all()

    def read(self, timeout: float = 2.0) -> np.ndarray:
        """Take a new frame, or fail; a consumed/stale frame is never replayed."""
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("preview timeout must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._closed:
                    raise PreviewError("scrcpy preview is closed")
                if self._error is not None:
                    raise PreviewError(f"scrcpy video failed: {self._error}; {self.server_output}") from self._error
                if self._latest is not None:
                    image, acquired = self._latest
                    self._latest = None
                    if time.monotonic() - acquired <= _MAX_FRAME_AGE:
                        self.last_frame_time = acquired
                        return image
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PreviewError("scrcpy timed out waiting for a fresh frame")
                self._condition.wait(remaining)

    @property
    def frame_age_ms(self) -> float | None:
        """Age of the last delivered frame's local PTS/arrival estimate, not absolute latency."""
        if self.last_frame_time is None:
            return None
        return max(0.0, (time.monotonic() - self.last_frame_time) * 1000)

    def close(self) -> None:
        with self._condition:
            if self._closed and getattr(self, "_cleanup_error", None) is None:
                return
            self._closed = True
            self._latest = None
            self._stop.set()
            self._condition.notify_all()
        deadline = time.monotonic() + _CLEANUP_TIMEOUT
        errors: list[str] = []

        def process_owned() -> bool:
            # An unreadable proc entry is distinct from an absent or reused PID.
            command = (f"if [ ! -d /proc/{self._pid} ]; then printf 'GONE\\n'; "
                       f"elif [ -r /proc/{self._pid}/cmdline ]; then printf 'ARGV\\n'; "
                       f"cat /proc/{self._pid}/cmdline; else printf 'UNREADABLE\\n'; fi")
            result = _exec_bounded(self.serial, command, deadline)
            if result == b"GONE\n":
                return False
            if result.startswith(b"ARGV\n"):
                return _owned(result.removeprefix(b"ARGV\n"), self.scid)
            raise PreviewError("could not verify own scrcpy PID state")

        for sock in (self._video, self._server):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Already disconnected; close still releases our descriptor.
                try:
                    sock.close()
                except OSError as exc:
                    errors.append(f"socket close: {exc}")
        if self._pid is not None:
            try:
                if process_owned():
                    # Recheck exact argv tokens in the same shell that sends TERM.
                    command = (f"args=$(tr '\\000' '\\n' < /proc/{self._pid}/cmdline); "
                               "server=false; session=false; "
                               "while IFS= read -r arg; do case \"$arg\" in "
                               "com.genymobile.scrcpy.Server) server=true;; "
                               f"scid={self.scid}) session=true;; esac; done <<EOF\n$args\nEOF\n"
                               f"if $server && $session; then kill -TERM {self._pid}; fi")
                    _exec_bounded(self.serial, command, deadline)
                    process_deadline = min(deadline, time.monotonic() + 1.5)
                    poll_pause = threading.Event()
                    while True:
                        if time.monotonic() >= process_deadline:
                            raise PreviewError("own scrcpy process remains after TERM")
                        if not process_owned():
                            break
                        # Observe the exact PID, never enumerate or kill other sessions.
                        poll_pause.wait(max(0.0, min(.05, process_deadline - time.monotonic())))
            except (OSError, adbclient.AdbError, PreviewError) as exc:
                errors.append(f"own-process cleanup: {exc}")
        if self._pushed:
            try:
                remote = shlex.quote(self._remote)
                command = (f"rm -f {remote}; if [ ! -e {remote} ] && [ ! -L {remote} ]; "
                           "then printf 'REMOVED\\n'; else printf 'PRESENT\\n'; fi")
                result = _exec_bounded(self.serial, command, deadline)
                if result != b"REMOVED\n":
                    raise PreviewError("could not verify own scrcpy jar removal")
            except (OSError, adbclient.AdbError, PreviewError) as exc:
                errors.append(f"own-jar cleanup: {exc}")
        for worker in (self._reader, self._logs):
            if worker is not None and worker is not threading.current_thread():
                worker.join(max(0.0, min(1.0, deadline - time.monotonic())))
                if worker.is_alive():
                    errors.append(f"thread did not stop: {worker.name}")
        if errors:
            self._cleanup_error = "; ".join(errors)
            raise PreviewError(self._cleanup_error)
        self._cleanup_error = None
