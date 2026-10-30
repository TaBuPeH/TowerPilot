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
import ipaddress
import math
import select
import socket
import time
from numbers import Real

_HOST = ("127.0.0.1", 5037)
_TIMEOUT = 15.0


class AdbError(RuntimeError):
    pass


def check_deadline(deadline: float | None) -> float | None:
    """Return remaining monotonic seconds, or raise before accepting late data.

    None preserves the ordinary per-socket timeout contract. Absolute deadlines
    must be finite real numbers; equality is expired (never a nonblocking read).
    """
    if deadline is None:
        return None
    if isinstance(deadline, bool) or not isinstance(deadline, Real):
        raise ValueError("deadline must be a finite monotonic timestamp")
    try:
        finite = math.isfinite(deadline)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("deadline must be a finite monotonic timestamp")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ADB absolute deadline expired")
    return remaining


def _bounded_timeout(timeout: float, deadline: float | None) -> float:
    try:
        valid = (not isinstance(timeout, bool) and isinstance(timeout, Real)
                 and math.isfinite(timeout) and timeout > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("timeout must be finite and positive")
    remaining = check_deadline(deadline)
    return timeout if remaining is None else min(timeout, remaining)


def _before_io(sock: socket.socket, timeout: float, deadline: float | None) -> None:
    if deadline is not None:
        sock.settimeout(_bounded_timeout(timeout, deadline))


def _connect(timeout: float, deadline: float | None) -> socket.socket:
    """Deadline connections use one numeric endpoint, with no DNS or fallback.

    create_connection can resolve a hostname without a time limit and reuse its
    timeout for multiple addresses. Preserve that legacy behavior only when no
    deadline was requested; numeric deadline endpoints need just one connect.
    Scoped IPv6 addresses are excluded to avoid interface-name resolution.
    """
    if deadline is None:
        return socket.create_connection(_HOST, timeout=timeout)
    host, port = _HOST
    if not isinstance(host, str) or "%" in host:
        raise ValueError("deadline requires an unscoped numeric ADB server address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("deadline requires a numeric ADB server address; DNS is unbounded") from exc
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    endpoint = (str(address), port) if address.version == 4 else (str(address), port, 0, 0)
    check_deadline(deadline)
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        _before_io(sock, timeout, deadline)
        check_deadline(deadline)
        sock.connect(endpoint)
        check_deadline(deadline)
        return sock
    except BaseException:
        sock.close()
        check_deadline(deadline)
        raise


def stream_lines(serial: str, command: str, stopped, timeout=15):
    """Read a long-lived device stream; cancellation works even when idle."""
    if not serial:
        raise ConnectionError("No adb serial configured")
    with socket.create_connection(_HOST, timeout=timeout) as sock:
        _send(sock, f"host:transport:{serial}")
        _status(sock, "transport")
        _send(sock, f"exec:{command}")
        _status(sock, "exec")
        pending = b""
        while not stopped():
            if not select.select([sock], [], [], 0.25)[0]:
                continue
            chunk = sock.recv(65536)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace") + "\n"
        if pending:
            yield pending.decode("utf-8", errors="replace")


def _send(sock: socket.socket, payload: str, *, timeout: float = _TIMEOUT,
          deadline: float | None = None) -> None:
    check_deadline(deadline)
    msg = payload.encode()
    _before_io(sock, timeout, deadline)
    sock.sendall(b"%04x" % len(msg) + msg)
    check_deadline(deadline)


def _status(sock: socket.socket, what: str, *, timeout: float = _TIMEOUT,
            deadline: float | None = None) -> None:
    options = {"timeout": timeout, "deadline": deadline} if deadline is not None else {}
    resp = _recv_exact(sock, 4, **options)
    if resp == b"OKAY":
        return
    if resp == b"FAIL":
        n = int(_recv_exact(sock, 4, **options), 16)
        check_deadline(deadline)
        reason = _recv_exact(sock, n, **options).decode(errors='replace')
        check_deadline(deadline)
        raise AdbError(f"{what}: {reason}")
    raise AdbError(f"{what}: bad status {resp!r}")


def _recv_exact(sock: socket.socket, n: int, *, timeout: float = _TIMEOUT,
                deadline: float | None = None) -> bytes:
    out = b""
    while len(out) < n:
        _before_io(sock, timeout, deadline)
        chunk = sock.recv(n - len(out))
        check_deadline(deadline)
        if not chunk:
            raise AdbError(f"connection closed after {len(out)}/{n} bytes")
        out += chunk
    check_deadline(deadline)
    return out


def exec_out(serial: str, command: str, timeout: float = _TIMEOUT, *,
             deadline: float | None = None) -> bytes:
    """Run a command on the device, return its raw stdout. Binary-safe.

    The stdout==exec-out equivalence matters: `adb shell` allocates a PTY and
    mangles binary with CRLF translation, which would corrupt a screencap.
    `exec:` does not.

    deadline is an optional absolute time.monotonic() cutoff shared across
    connect, both handshakes, every write/read and assembly. timeout remains a
    per-operation cap; it cannot extend the deadline. Late output is discarded.
    Deadline calls require a numeric IPv4 or unscoped IPv6 ADB server address;
    hostnames raise ValueError before resolution. Ordinary calls still resolve
    hostnames through create_connection as before.
    """
    connect_timeout = _bounded_timeout(timeout, deadline)
    if not serial:
        # an empty serial matches EVERY device on the server and adb answers
        # "more than one device" - say what is actually wrong
        raise ConnectionError("no adb serial configured for this instance - "
                              "run the Setup wizard (instances.<name>.serial)")
    sock = _connect(connect_timeout, deadline)
    try:
        check_deadline(deadline)
        sock.settimeout(timeout)
        options = {"timeout": timeout, "deadline": deadline} if deadline is not None else {}
        _send(sock, f"host:transport:{serial}", **options)
        _status(sock, "transport", **options)
        _send(sock, f"exec:{command}", **options)
        _status(sock, "exec", **options)
        # the server opened a transport, so the serial is online: end its
        # offline clock (a runner reads no get_state outside recover())
        _offline_since.pop((_HOST, serial), None)
        chunks = []
        while True:
            _before_io(sock, timeout, deadline)
            chunk = sock.recv(1 << 20)
            check_deadline(deadline)
            if not chunk:
                break
            chunks.append(chunk)
        result = b"".join(chunks)
        check_deadline(deadline)
    finally:
        sock.close()
    check_deadline(deadline)
    return result


def shell(serial: str, command: str, timeout: float = _TIMEOUT, *,
          deadline: float | None = None) -> bytes:
    """Fire-and-read a shell command (taps, swipes). Returns its output."""
    options = {"deadline": deadline} if deadline is not None else {}
    return exec_out(serial, command, timeout, **options)


def alive(serial: str) -> bool:
    """True when the server opens a transport to `serial` and runs `true` on
    it. `true` prints nothing, so success of the exec IS the answer. False
    when it raises OSError (no server, empty serial, socket timeout) or
    AdbError (device missing, offline or unauthorized)."""
    try:
        exec_out(serial, "true", timeout=5)
    except (OSError, AdbError):
        return False
    return True


def _ask_state(serial: str, timeout: float = 1.0) -> str:
    """The adb server's view of one device, from ONE host query.

    Sends `host-serial:<serial>:get-state` on a single socket to the server -
    no device transport, no exec, so a hung device cannot block it; the
    socket timeout bounds the whole call. Returns the server's state string
    as sent ("device", "offline", "unauthorized", "bootloader", ...). A FAIL
    reply is mapped: "not found" -> "missing", "offline" -> "offline",
    "unauthorized" -> "unauthorized" (the server answers a non-online device
    with FAIL "device offline" / "device unauthorized" rather than OKAY).
    A failed connect to 5037 (refused OR timed out - this cannot tell
    them apart; recover asks _server_refuses) -> "no_server"; empty serial ->
    "unconfigured" (the server is not contacted); any other failure ->
    "unknown". Never raises for those cases.
    """
    if not serial:
        return "unconfigured"
    try:
        sock = socket.create_connection(_HOST, timeout=timeout)
    except OSError:
        return "no_server"
    try:
        sock.settimeout(timeout)
        _send(sock, f"host-serial:{serial}:get-state")
        _status(sock, "get-state")
        n = int(_recv_exact(sock, 4), 16)
        return _recv_exact(sock, n).decode(errors="replace").strip() or "unknown"
    except AdbError as e:
        why = str(e).lower()
        for needle, state in (("not found", "missing"), ("offline", "offline"),
                              ("unauthorized", "unauthorized")):
            if needle in why:
                return state
        return "unknown"
    except (OSError, ValueError):
        return "unknown"
    finally:
        sock.close()


def _host_reply(payload: str, what: str, timeout: float = 10) -> str:
    """Send one host-service request on its own socket and return the
    server's length-prefixed OKAY reply text. Raises OSError (no server,
    timeout) or AdbError (FAIL / bad status)."""
    with socket.create_connection(_HOST, timeout=timeout) as sock:
        sock.settimeout(timeout)
        _send(sock, payload)
        _status(sock, what)
        n = int(_recv_exact(sock, 4), 16)
        return _recv_exact(sock, n).decode(errors="replace")


def reconnect(serial: str) -> bool:
    """Re-attach a device that dropped, without killing the server.

    kill-server was the old recovery and it was actively wrong: MuMu is
    reached over TCP, so killing the server drops the device and every later
    call fails with "device not found". A plain host:connect re-attaches it and
    leaves every other client alone. True when the reply contains "connected"
    ("connected to" / "already connected to"); False on any other reply, no
    server or a FAIL.
    """
    try:
        return "connected" in _host_reply(f"host:connect:{serial}", "connect").lower()
    except (OSError, AdbError):
        return False


def disconnect(serial: str) -> bool:
    """host:disconnect:<serial> - drop the server's transport to one TCP
    device (nothing else). True on OKAY; False on no server or a FAIL (the
    server holds no such transport)."""
    try:
        _host_reply(f"host:disconnect:{serial}", "disconnect")
    except (OSError, AdbError):
        return False
    return True


def devices(timeout: float = 5.0) -> list[str]:
    """Serials the adb server lists in state `device`, from ONE host:devices
    query (rows "<serial>\\t<state>"). Raises OSError when no server listens
    and AdbError on a FAIL."""
    reply = _host_reply("host:devices", "devices", timeout)
    return [line.split("\t")[0] for line in reply.splitlines()
            if line.endswith("\tdevice")]


def _server_refuses(timeout: float = 5.0) -> bool:
    """True only when a plain TCP connect to the adb server address is
    REFUSED (ConnectionRefusedError, WinError 10061): nothing listens. A
    timeout, any other OSError or an accepted connect is False - a slow or
    wedged server is listening, and `adb start-server` from a client whose
    version differs from the running server's kills that server. The default
    timeout outlasts Windows' SYN retries: a closed loopback port answers
    WinError 10061 only after ~2 s (measured), so a shorter wait reads a
    refused connect as a timeout."""
    try:
        socket.create_connection(_HOST, timeout=timeout).close()
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    return False


# How long a serial must keep reading 'offline' (as observed by this process)
# before recover() drops the server's transport to it with host:disconnect. A
# fresh TCP transport reads 'offline' until its handshake completes, and the
# callers poll recover() every 3-60 s (boot 3 s, shard twice per capture
# failure, the dashboard 5-60 s), so a transport this process first read
# 'offline' less than that long ago is never torn down.
_OFFLINE_STALE_S = 30.0
# (server address, serial) -> time.monotonic() of the FIRST read of the serial
# as 'offline' since this process last saw it online or gone. get_state stamps
# it on an 'offline' read if absent (never re-stamps) and drops it on a
# 'device', 'missing' or 'no_server' read; a successful exec_out drops it too;
# recover() also drops it when it sends the host:disconnect. host:connect never
# touches it. Time nobody read the serial counts as offline. Per process: two
# processes recovering one serial keep separate clocks.
_offline_since: dict = {}


def get_state(serial: str, timeout: float = 1.0) -> str:
    """The adb server's view of one device, from ONE host query (_ask_state:
    its states and failure mapping), and the _offline_since clock of (server,
    serial) kept in step with the answer: 'offline' starts it if it is not
    running, 'device', 'missing' and 'no_server' stop it, any other answer
    leaves it as it is. ('no_server' also answers a connect timeout, which
    only delays a teardown; a restarted server drops every transport, so what
    is attached afterwards is a new one.) Every read in this process goes
    through here, the dashboard's status polls included, so the clock cannot
    outlive a transport some poll saw dropped."""
    state = _ask_state(serial, timeout)
    key = (_HOST, serial)
    if state == "offline":
        _offline_since.setdefault(key, time.monotonic())
    elif state in ("device", "missing", "no_server"):
        _offline_since.pop(key, None)
    return state


def recover(serial: str, start_server=None, *, attempts: int = 3,
            settle: float = 2.0) -> str:
    """Bring a TCP device (`host:port` serial) back to `device` over the
    server socket; returns the state it ends on.

    First, for ANY serial: 'no_server' AND a refused plain connect to the
    server address (nothing listens; get_state also says 'no_server' for a
    connect timeout, which leaves the state as read) -> call `start_server()`
    once - the caller's sanctioned daemon spawn; this module never spawns -
    and ask again. A serial without ':' (emulator-NNNN, usb, empty) has no host:port
    to connect, so its state is returned here, as is every state when
    `attempts` is 0. Then each of at most `attempts` rounds acts on the
    current state:
      'offline'   -> host:disconnect first, but only when this process's first
                     'offline' read of the serial is at least _OFFLINE_STALE_S
                     old (see _offline_since; the clock restarts after the
                     disconnect). The server still holds the dead
                     transport, and a connect for a serial it holds only
                     answers "already connected". A younger 'offline' is left
                     in place: it may be a handshake still running;
      'missing'   -> host:connect.
    After either the round sends host:connect; when the server answered
    "connected" ("already connected" included) the state is re-read every
    0.2 s for up to `settle` s and the round ends at the first 'device'. The
    connect never restarts the offline clock, so a caller polling more often
    than _OFFLINE_STALE_S still tears down a transport that stays offline,
    once per _OFFLINE_STALE_S. Any other answer ('device', 'unauthorized',
    'unknown', a 'no_server' with no or a spent `start_server` or a connect
    that was not refused) is returned untouched. Never kill-server: the
    server is shared and killing it drops every TCP device.
    """
    state = get_state(serial)
    if state == "no_server" and start_server is not None and _server_refuses():
        start_server()
        state = get_state(serial)
    if ":" not in serial:
        return state
    key = (_HOST, serial)
    for _ in range(attempts):
        if state == "offline":
            # a stamp another thread's 'device' / 'missing' read just dropped
            # counts as age 0 (no teardown)
            now = time.monotonic()
            if now - _offline_since.get(key, now) >= _OFFLINE_STALE_S:
                disconnect(serial)
                _offline_since.pop(key, None)
        elif state != "missing":
            return state
        if reconnect(serial):
            deadline = time.monotonic() + settle
            state = get_state(serial)
            while state != "device" and time.monotonic() < deadline:
                time.sleep(0.2)
                state = get_state(serial)
        else:
            state = get_state(serial)
    return state


def exec_to(serial, command, sink, *, timeout=30, progress=None, check_stop=None):
    """Stream binary stdout to a local file through the existing ADB socket.

    Used for installed APK acquisition. No ADB subprocess or game input.
    Cancellation is checked between chunks; callers own atomic publication.
    """
    if not serial:
        raise ConnectionError("No adb serial configured")
    total = 0
    with socket.create_connection(_HOST, timeout=timeout) as sock:
        sock.settimeout(timeout)
        _send(sock, f"host:transport:{serial}")
        _status(sock, "transport")
        _send(sock, f"exec:{command}")
        _status(sock, "exec")
        while True:
            if check_stop:
                check_stop()
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            sink.write(chunk)
            total += len(chunk)
            if progress:
                progress(total)
    return total
