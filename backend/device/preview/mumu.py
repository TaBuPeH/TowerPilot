"""Read-only MuMu preview, with all vendor DLL calls isolated in a helper.

The helper captures on demand. A single request owns the connection until its
whole frame arrives; a timeout discards the connection instead of queuing work.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import TypedDict
import xml.etree.ElementTree as ET

import numpy as np
import numpy.typing as npt

from device.preview.common import PreviewCleanupError, PreviewError, PreviewUnavailable
from runtime import process_handle


_LOG = logging.getLogger(__name__)
_START_TIMEOUT = 8.0
_CLOSE_TIMEOUT = 0.25
_MAX_BYTES = 256 * 1024 * 1024
_MAX_MESSAGE = 16 * 1024


class MuMuTarget(TypedDict):
    install_dir: str
    index: int


def _dll_path(root: Path) -> Path:
    for directory in ("nx_main", "shell"):
        path = root / directory / "sdk" / "external_renderer_ipc.dll"
        if path.is_file():
            return path
    raise PreviewUnavailable(f"MuMu capture DLL missing below {root}")


def _port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid MuMu ADB port")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("invalid MuMu ADB port")
    return port


def _vm_ports(vm: Path) -> set[int]:
    """Read actual configured forwarding rules, never the index/port formula."""
    ports: set[int] = set()
    config = vm / "configs" / "vm_config.json"
    primary = None
    if config.is_file():
        data = json.loads(config.read_text(encoding="utf-8-sig"))
        primary = _port(data["vm"]["nat"]["port_forward"]["adb"]["host_port"])
        ports.add(primary)
    definition = vm / f"{vm.name}.nemu"
    if definition.is_file():
        root = ET.parse(definition).getroot()
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "Forwarding":
                continue
            name = node.get("name")
            if name not in {"ADB_PORT", "ADB_PORT_EX", "ADB_PORT_OLD"}:
                continue
            if node.get("hostip") not in {"127.0.0.1", "localhost"}:
                continue
            if node.get("guestport") != "5555" or node.get("proto") != "1":
                continue
            port = _port(node.get("hostport"))
            if name == "ADB_PORT" and primary is not None and primary != port:
                raise ValueError("MuMu JSON/XML ADB port mappings disagree")
            ports.add(port)
    return ports


def discover_target(serial: str, adb_exe: str, options: dict) -> MuMuTarget:
    """Resolve exactly one MuMu instance with a proven local ADB-port binding."""
    match = re.fullmatch(r"(?:127\.0\.0\.1|localhost):([0-9]+)", serial)
    if match is None:
        raise PreviewUnavailable("MuMu native preview needs a verified loopback TCP serial")
    try:
        requested_port = _port(match[1])
        explicit = options.get("install_dir", "")
        if explicit:
            if not isinstance(explicit, str):
                raise ValueError("MuMu install_dir must be a path string")
            root = Path(explicit).expanduser().resolve()
        else:
            adb_path = Path(adb_exe).expanduser().resolve()
            if adb_path.name.lower() != "adb.exe" or adb_path.parent.name.lower() not in {"nx_main", "shell"}:
                raise ValueError("configured adb.exe does not identify a MuMu installation")
            root = adb_path.parent.parent
        _dll_path(root)
        selected = options.get("index")
        if type(selected) is int and selected == -1:
            selected = None
        if selected is not None and (type(selected) is not int or selected < 0):
            raise ValueError("MuMu index must be a nonnegative integer")
        matches: list[int] = []
        invalid: list[str] = []
        for vm in sorted((root / "vms").iterdir()):
            name = re.fullmatch(r"MuMuPlayer(?:Global)?-[0-9]+(?:\.[0-9]+)*-([0-9]+)", vm.name)
            if name is None or not vm.is_dir():
                continue
            try:
                if requested_port in _vm_ports(vm):
                    matches.append(int(name[1]))
            except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                invalid.append(f"{vm.name}: {exc}")
        if len(matches) != 1 or invalid:
            detail = f"; unreadable mappings: {'; '.join(invalid)}" if invalid else ""
            raise ValueError(f"cannot uniquely bind {serial} to a MuMu instance{detail}")
        if selected is not None and selected != matches[0]:
            raise ValueError(f"configured MuMu index {selected} does not match {serial} (index {matches[0]})")
        return {"install_dir": str(root), "index": matches[0]}
    except (OSError, ValueError) as exc:
        raise PreviewUnavailable(f"MuMu target unavailable: {exc}") from exc


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("MuMu helper deadline exceeded")
    return remaining


def _receive(sock: socket.socket, buffer: memoryview, deadline: float) -> None:
    offset = 0
    while offset < len(buffer):
        sock.settimeout(_remaining(deadline))
        received = sock.recv_into(buffer[offset:])
        if received == 0:
            raise PreviewError("MuMu helper exited or closed its connection")
        offset += received


def _message(sock: socket.socket, deadline: float) -> dict:
    header = bytearray(4)
    _receive(sock, memoryview(header), deadline)
    size = struct.unpack("!I", header)[0]
    if size > _MAX_MESSAGE:
        raise PreviewError("MuMu helper returned an oversized message")
    payload = bytearray(size)
    _receive(sock, memoryview(payload), deadline)
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise PreviewError("MuMu helper returned an invalid message")
    return result


def _dimensions(width: object, height: object) -> tuple[int, int]:
    if type(width) is not int or type(height) is not int:
        raise PreviewError("MuMu returned noninteger capture dimensions")
    if not (1 <= width <= 8192 and 1 <= height <= 8192) or width * height * 4 > _MAX_BYTES:
        raise PreviewError(f"MuMu returned invalid capture dimensions: {width}x{height}")
    return width, height


class MuMuPreview:
    """Full-size, upright BGR frames from one owned, bounded native helper."""

    def __init__(self, install_dir: str, index: int, package: str, display_id: int | None = None) -> None:
        if type(index) is not int or index < 0 or not package or "\x00" in package:
            raise PreviewUnavailable("MuMu preview requires a valid instance index and package")
        if display_id is not None and (type(display_id) is not int or display_id < 0):
            raise PreviewUnavailable("MuMu preview display_id must be a nonnegative integer")
        root = Path(install_dir).resolve()
        _dll_path(root)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._process: subprocess.Popen | None = None
        self._socket: socket.socket | None = None
        deadline = time.monotonic() + _START_TIMEOUT
        token = secrets.token_hex(32)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                command = [sys.executable, "-m", "device.preview.mumu_worker",
                           str(listener.getsockname()[1]), token, str(root), str(index), package]
                if display_id is not None:
                    command.append(str(display_id))
                self._process = subprocess.Popen(
                    command, cwd=str(Path(__file__).resolve().parents[2]),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                listener.settimeout(_remaining(deadline))
                self._socket, _ = listener.accept()
                self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                hello = _message(self._socket, deadline)
                if hello.get("token") != token:
                    raise PreviewUnavailable("MuMu helper authentication failed")
                if "error" in hello:
                    raise PreviewUnavailable(f"MuMu helper initialization failed: {hello['error']}")
                self.width, self.height = _dimensions(hello.get("width"), hello.get("height"))
        except (OSError, ValueError, PreviewError) as exc:
            try:
                self.close()
            except PreviewError as cleanup:
                raise PreviewCleanupError(f"MuMu native preview unavailable: {exc}; {cleanup}", self) from exc
            raise PreviewUnavailable(f"MuMu native preview unavailable: {exc}") from exc

    def read(self, timeout: float = 2.0) -> npt.NDArray[np.uint8]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("MuMu preview timeout must be positive and finite")
        deadline = time.monotonic() + timeout
        if not self._lock.acquire(timeout=timeout):
            raise PreviewError("MuMu preview already has an outstanding frame request")
        try:
            connection = self._socket
            if self._closed.is_set() or connection is None:
                raise PreviewError("MuMu preview is closed")
            connection.settimeout(_remaining(deadline))
            connection.sendall(b"F")
            status = bytearray(1)
            _receive(connection, memoryview(status), deadline)
            if status == b"E":
                raise PreviewError(f"MuMu native capture failed: {_message(connection, deadline).get('error')}")
            if status != b"F":
                raise PreviewError("MuMu helper returned an invalid frame response")
            frame = np.empty((self.height, self.width, 3), dtype=np.uint8)
            _receive(connection, memoryview(frame).cast("B"), deadline)
            return frame
        except (OSError, ValueError, PreviewError) as exc:
            try:
                self.close()
            except PreviewError as cleanup:
                raise PreviewError(f"MuMu preview read failed: {exc}; {cleanup}") from exc
            raise PreviewError(f"MuMu preview read failed: {exc}") from exc
        finally:
            self._lock.release()

    def close(self) -> None:
        """Interrupt reads immediately; never wait for a stuck DLL disconnect."""
        if self._closed.is_set() and self._process is None:
            return
        self._closed.set()
        failures: list[str] = []
        connection, self._socket = self._socket, None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                # Already disconnected: closing the handle below is sufficient.
                pass
            finally:
                try:
                    connection.close()
                except OSError as exc:
                    failures.append(f"socket close failed: {exc}")
        process, self._process = self._process, None
        if process is not None:
            try:
                # Windows virtualenv executables may be redirectors. The family (the launcher and the
                # native interpreter it starts) is stopped through kernel handles opened while this
                # object's own handle still pins the launcher's pid (runtime.process_handle), so the
                # native interpreter cannot become an unowned, hung orphan and no reissued pid is
                # ever signalled. The helpers get _CLOSE_TIMEOUT to exit on their own first.
                stop = process_handle.terminate_owned(process.pid, grace=_CLOSE_TIMEOUT, timeout=_CLOSE_TIMEOUT)
                if stop.state == "unverified":
                    raise OSError(f"cannot verify the helper family of pid {process.pid}: {stop.errors}")
                if not stop.confirmed:
                    survivors = sorted({error["pid"] for error in stop.errors if error.get("pid")})
                    failures.append(f"helper processes did not exit after kill: {survivors}")
            except OSError as exc:
                _LOG.exception("Failed to reap MuMu preview helper (pid %s)", process.pid)
                failures.append(f"cannot verify helper process cleanup: {exc}")
                try:
                    process.kill()
                except OSError as kill_error:
                    _LOG.exception("Failed to kill MuMu preview launcher (pid %s)", process.pid)
                    failures.append(f"launcher kill failed: {kill_error}")
            finally:
                try:
                    process.wait(timeout=_CLOSE_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    failures.append(f"helper launcher did not exit: {exc}")
        if failures:
            # Keep an unresolved owned process available for another bounded
            # cleanup attempt, even though frame requests are already closed.
            self._process = process
            raise PreviewError(f"MuMu preview cleanup failed: {'; '.join(failures)}")
