"""Talk to the adb SERVER directly over its socket. No subprocesses.

Every screencap and every tap used to spawn an adb.exe. At ~3 frames/sec plus
taps that is tens of thousands of process launches an hour, and on Windows it
killed two long farming runs outright - first "the system lacked sufficient
buffer space", then the daemon dropping its device entirely. Process creation
is also the single biggest slice of a frame grab (~50-100ms of the ~300ms).

The adb server speaks a simple protocol on 127.0.0.1:5037:

    -> "<4 hex length><payload>"        e.g. "001Chost:transport:127.0.0.1:5555"
    <- "OKAY" | "FAIL<4 hex len><why>"

After a successful `host:transport:<serial>` the socket IS that device, and a
following `exec:<cmd>` streams the command's raw stdout until EOF. That is
exactly what `adb exec-out` does - this just skips the executable.

A socket per command is still opened (the exec consumes the transport, so it
cannot be pooled), but a socket is vastly cheaper than a process and closes
cleanly, which is what the buffer-space exhaustion was really about.
"""
import socket

_HOST = ("127.0.0.1", 5037)
_TIMEOUT = 15.0


class AdbError(RuntimeError):
    pass


def _send(sock: socket.socket, payload: str):
    msg = payload.encode()
    sock.sendall(b"%04x" % len(msg) + msg)


def _status(sock: socket.socket, what: str):
    resp = _recv_exact(sock, 4)
    if resp == b"OKAY":
        return
    if resp == b"FAIL":
        n = int(_recv_exact(sock, 4), 16)
        raise AdbError(f"{what}: {_recv_exact(sock, n).decode(errors='replace')}")
    raise AdbError(f"{what}: bad status {resp!r}")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise AdbError(f"connection closed after {len(out)}/{n} bytes")
        out += chunk
    return out


def exec_out(serial: str, command: str, timeout: float = _TIMEOUT) -> bytes:
    """Run a command on the device, return its raw stdout. Binary-safe.

    The stdout==exec-out equivalence matters: `adb shell` allocates a PTY and
    mangles binary with CRLF translation, which would corrupt a screencap.
    `exec:` does not.
    """
    if not serial:
        # an empty serial matches EVERY device on the server and adb answers
        # "more than one device" - say what is actually wrong
        raise ConnectionError("no adb serial configured for this instance - "
                              "run the Setup wizard (instances.<name>.serial)")
    sock = socket.create_connection(_HOST, timeout=timeout)
    try:
        sock.settimeout(timeout)
        _send(sock, f"host:transport:{serial}")
        _status(sock, "transport")
        _send(sock, f"exec:{command}")
        _status(sock, "exec")
        chunks = []
        while True:
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        sock.close()


def shell(serial: str, command: str, timeout: float = _TIMEOUT) -> bytes:
    """Fire-and-read a shell command (taps, swipes). Returns its output."""
    return exec_out(serial, command, timeout)


def alive(serial: str) -> bool:
    try:
        return b"device" in exec_out(serial, "true", timeout=5) or True
    except (OSError, AdbError):
        return False


def reconnect(serial: str) -> bool:
    """Re-attach a device that dropped, without killing the server.

    kill-server was the old recovery and it was actively wrong: MuMu is
    reached over TCP, so killing the server drops the device and every later
    call fails with "device not found". A plain host:connect re-attaches it and
    leaves every other client alone.
    """
    try:
        sock = socket.create_connection(_HOST, timeout=10)
    except OSError:
        return False
    try:
        _send(sock, f"host:connect:{serial}")
        _status(sock, "connect")
        n = int(_recv_exact(sock, 4), 16)
        return b"connected" in _recv_exact(sock, n).lower()
    except (OSError, AdbError):
        return False
    finally:
        sock.close()
