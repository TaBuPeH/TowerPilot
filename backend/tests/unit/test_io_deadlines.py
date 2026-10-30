"""Offline absolute-budget contracts; every device boundary is a scripted fake."""
from collections import deque
import socket
import struct

import numpy as np
import pytest

import settings
from device import adbclient, capture, displays, overlays


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class Wire:
    """A socket whose scripted stages consume time, including partial reads."""
    def __init__(self, clock, script, cutoff, *, honor_timeout=True, close_cost=0):
        self.clock = clock
        self.script = deque(script)
        self.cutoff = cutoff
        self.honor_timeout = honor_timeout
        self.close_cost = close_cost
        self.timeout = None
        self.operations = []
        self.sent = []
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def stage(self, operation):
        if self.cutoff is not None:
            assert self.clock.now < self.cutoff, "new I/O after expiration"
            assert 0 < self.timeout <= self.cutoff - self.clock.now
        expected, cost, result = self.script.popleft()
        assert operation == expected
        self.operations.append((operation, self.clock.now, self.timeout))
        if self.honor_timeout and cost > self.timeout:
            self.clock.now += self.timeout
            raise TimeoutError("scripted socket timeout")
        self.clock.now += cost
        if isinstance(result, Exception):
            raise result
        return result

    def sendall(self, payload):
        self.sent.append(payload[4:].decode())
        self.stage("send")

    def connect(self, endpoint):
        assert endpoint == adbclient._HOST
        self.stage("connect")

    def recv(self, size):
        result = self.stage("recv")
        assert len(result) <= size
        return result

    def close(self):
        self.closed = True
        self.clock.now += self.close_cost


def exchange(payload=b"", *, cost=0):
    return [("connect", 0, None), ("send", 0, None),
            ("recv", 0, b"OKAY"), ("send", 0, None),
            ("recv", 0, b"OKAY"), ("recv", cost, payload),
            ("recv", 0, b"")]


@pytest.fixture
def clock(monkeypatch):
    result = Clock()
    # Replace this module's clock only, never Python's global time module.
    monkeypatch.setattr(adbclient, "time", result)
    return result


@pytest.fixture
def server(monkeypatch, clock):
    wires = []
    pending = deque()

    def add(script, cutoff=1.0, **kwargs):
        wire = Wire(clock, script, cutoff, **kwargs)
        wires.append(wire)
        pending.append(wire)
        return wire

    def connect(address, timeout):
        assert address == adbclient._HOST
        assert pending, "unexpected device connection"
        wire = pending.popleft()
        wire.settimeout(timeout)
        wire.stage("connect")
        return wire

    def raw_socket(family, kind):
        assert (family, kind) == (socket.AF_INET, socket.SOCK_STREAM)
        assert pending, "unexpected device socket"
        return pending.popleft()

    monkeypatch.setattr(adbclient.socket, "create_connection", connect)
    monkeypatch.setattr(adbclient.socket, "socket", raw_socket)
    return add


@pytest.fixture
def screen(monkeypatch):
    inst = {"serial": "deadline-test", "display": "1", "input_display": 1}
    monkeypatch.setattr(settings, "instance", lambda: inst)
    monkeypatch.setattr(capture, "_resolved_serial", inst["serial"])
    monkeypatch.setattr(capture, "CONFIG", {"screen": {"width": 16, "height": 16}})
    return inst


def raw_frame(width=16, height=16):
    return struct.pack("<III", width, height, 1) + bytes((10, 20, 30, 255)) * width * height


def test_transport_shrinks_one_budget_through_every_stage(server, clock):
    script = exchange(b"abc", cost=0.125)
    script = [(op, 0.125 if cost == 0 else cost, value) for op, cost, value in script]
    wire = server(script, cutoff=1)
    assert adbclient.exec_out("unit", "true", timeout=15, deadline=1) == b"abc"
    assert [item[2] for item in wire.operations] == [1, .875, .75, .625, .5, .375, .25]
    assert wire.closed and not wire.script
    assert clock.now == .875


@pytest.mark.parametrize("stage_index", range(7), ids=[
    "connect", "transport-send", "transport-status", "exec-send", "exec-status", "output", "eof",
])
def test_late_socket_stage_is_never_accepted(server, clock, stage_index):
    script = exchange(b"abc")
    op, _, result = script[stage_index]
    script[stage_index] = (op, 1, result)
    wire = server(script, honor_timeout=False)
    with pytest.raises(TimeoutError):
        adbclient.exec_out("unit", "true", deadline=1)
    assert clock.now == 1
    assert len(wire.operations) == stage_index + 1
    assert wire.closed


def test_late_socket_close_does_not_return_evidence(server):
    wire = server(exchange(b"abc"), close_cost=1)
    with pytest.raises(TimeoutError):
        adbclient.shell("unit", "true", deadline=1)
    assert wire.closed


@pytest.mark.parametrize("phase", ["transport", "exec", "failure-length", "failure-reason"])
def test_fragmented_handshakes_cannot_reset_deadline(server, phase):
    prefix = [("connect", 0, None), ("send", 0, None)]
    if phase == "exec":
        prefix += [("recv", 0, b"OKAY"), ("send", 0, None)]
    elif phase.startswith("failure"):
        prefix += [("recv", 0, b"FAIL")]
        if phase == "failure-reason":
            prefix += [("recv", 0, b"0004")]
    fragment = b"0004" if phase == "failure-length" else b"OKAY"
    wire = server(prefix + [("recv", .25, bytes([char])) for char in fragment])
    with pytest.raises(TimeoutError):
        adbclient.exec_out("unit", "true", deadline=1)
    assert wire.closed and not wire.script
    assert wire.sent == (["host:transport:unit", "exec:true"] if phase == "exec"
                         else ["host:transport:unit"])


def test_trickling_stdout_uses_remaining_time(server, clock):
    script = exchange()[:5] + [("recv", .375, b"x")] * 4
    wire = server(script)
    with pytest.raises(TimeoutError):
        adbclient.exec_out("unit", "true", deadline=1)
    assert clock.now == 1
    assert [row[2] for row in wire.operations[-3:]] == [1, .625, .25]
    assert wire.closed and len(wire.script) == 1


def test_relative_timeout_remains_an_additional_cap(server, clock):
    wire = server(exchange(b"abc", cost=.5), cutoff=10)
    with pytest.raises(TimeoutError, match="scripted"):
        adbclient.exec_out("unit", "true", timeout=.25, deadline=10)
    assert clock.now == .25
    assert all(row[2] == .25 for row in wire.operations)


def test_default_socket_behavior_retains_relative_timeout(server, clock):
    script = [(op, .25, value) for op, _, value in exchange(b"abc")]
    wire = server(script, cutoff=None)
    assert adbclient.shell("unit", "true", timeout=.5) == b"abc"
    assert clock.now == 1.75
    assert all(row[2] == .5 for row in wire.operations)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_reviewer_multi_address_probe_cannot_spend_a_second_budget(monkeypatch, clock, host):
    """Keep create_connection real; intercept only resolver/raw socket edges.

    D1's exact two-candidate probe spent [100, 101] and then [101, 102].
    Deadline calls now reject hostnames before DNS and connect numeric IPs once.
    """
    clock.now = 100
    calls, resolved, closed = [], [], []
    candidates = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 5037, 0, 0)),
                  (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5037))]

    def resolve(*args, **kwargs):
        resolved.append(args)
        return candidates

    class Candidate:
        def settimeout(self, value):
            self.timeout = value

        def connect(self, endpoint):
            calls.append((clock.now, self.timeout))
            clock.now += self.timeout
            raise TimeoutError("candidate timed out")

        def close(self):
            closed.append(self)

    monkeypatch.setattr(adbclient.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(adbclient.socket, "socket", lambda *a: Candidate())
    monkeypatch.setattr(adbclient, "_HOST", (host, 5037))
    expected_error = ValueError if host == "localhost" else TimeoutError
    with pytest.raises(expected_error):
        adbclient.exec_out("synthetic", "true", deadline=101)
    assert resolved == []
    assert calls == ([] if host == "localhost" else [(100, 1)])
    assert len(closed) == len(calls)
    assert clock.now == (100 if host == "localhost" else 101)


def test_default_hostname_candidates_keep_existing_relative_timeout(monkeypatch, clock):
    clock.now = 100
    calls = []
    candidates = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 5037, 0, 0)),
                  (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5037))]

    class Candidate:
        def settimeout(self, value):
            self.timeout = value

        def connect(self, endpoint):
            calls.append((clock.now, self.timeout, endpoint))
            clock.now += .25
            raise ConnectionRefusedError("candidate refused")

        def close(self):
            pass

    monkeypatch.setattr(adbclient.socket, "getaddrinfo", lambda *a, **k: candidates)
    monkeypatch.setattr(adbclient.socket, "socket", lambda *a: Candidate())
    monkeypatch.setattr(adbclient, "_HOST", ("localhost", 5037))
    with pytest.raises(ConnectionRefusedError):
        adbclient.exec_out("synthetic", "true", timeout=1)
    assert calls == [(100, 1, candidates[0][-1]), (100.25, 1, candidates[1][-1])]


@pytest.mark.parametrize("host,family,endpoint", [
    ("127.0.0.1", socket.AF_INET, ("127.0.0.1", 5037)),
    ("::1", socket.AF_INET6, ("::1", 5037, 0, 0)),
])
def test_numeric_connection_never_resolves_and_recomputes_remaining_time(
        monkeypatch, clock, host, family, endpoint):
    wire = Wire(clock, exchange(b"ok"), cutoff=1)
    wire.script.popleft()  # raw connect below checks its own endpoint/timing
    connections = []

    def connect(address):
        connections.append((clock.now, wire.timeout, address))

    def create(kind, protocol):
        assert (kind, protocol) == (family, socket.SOCK_STREAM)
        clock.now += .25
        return wire

    wire.connect = connect
    monkeypatch.setattr(adbclient, "_HOST", (host, 5037))
    monkeypatch.setattr(adbclient.socket, "socket", create)
    monkeypatch.setattr(adbclient.socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS"))
    assert adbclient.exec_out("unit", "true", deadline=1) == b"ok"
    assert connections == [(.25, .75, endpoint)]
    assert wire.closed


@pytest.mark.parametrize("host", ["localhost", "adb.example.invalid", "fe80::1%eth0", "::1%0"])
def test_deadline_endpoint_policy_fails_before_dns_or_socket_creation(monkeypatch, clock, host):
    monkeypatch.setattr(adbclient, "_HOST", (host, 5037))
    monkeypatch.setattr(adbclient.socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS"))
    monkeypatch.setattr(adbclient.socket, "socket", lambda *a, **k: pytest.fail("socket creation"))
    with pytest.raises(ValueError, match="numeric ADB server"):
        adbclient.exec_out("unit", "true", deadline=1)
    assert clock.now == 0


def test_numeric_connect_failure_before_cutoff_is_preserved_without_fallback(monkeypatch, clock):
    attempts, closed = [], []
    error = ConnectionRefusedError("unavailable")

    class Candidate:
        def settimeout(self, value):
            self.timeout = value

        def connect(self, endpoint):
            attempts.append((clock.now, self.timeout))
            clock.now += .25
            raise error

        def close(self):
            closed.append(self)

    monkeypatch.setattr(adbclient.socket, "socket", lambda *a: Candidate())
    monkeypatch.setattr(adbclient.socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS"))
    with pytest.raises(ConnectionRefusedError) as raised:
        adbclient.exec_out("unit", "true", deadline=1)
    assert raised.value is error
    assert attempts == [(0, 1)] and len(closed) == 1


def test_expiry_while_creating_numeric_socket_prevents_connect(monkeypatch, clock):
    closed = []

    class Candidate:
        def settimeout(self, value):
            pytest.fail("expired socket configured")

        def connect(self, endpoint):
            pytest.fail("expired socket connected")

        def close(self):
            closed.append(self)

    def create(*args):
        clock.now = 1
        return Candidate()

    monkeypatch.setattr(adbclient.socket, "socket", create)
    with pytest.raises(TimeoutError):
        adbclient.exec_out("unit", "true", deadline=1)
    assert len(closed) == 1


CALLS = [
    lambda deadline: adbclient.exec_out("unit", "true", deadline=deadline),
    lambda deadline: adbclient.shell("unit", "true", deadline=deadline),
    lambda deadline: capture.grab("unit", deadline=deadline),
    lambda deadline: capture._grab("unit", diagnostic={}, deadline=deadline),
    lambda deadline: capture.refresh_display("unit", deadline=deadline),
    lambda deadline: overlays.windows("unit", deadline=deadline),
]


@pytest.mark.parametrize("call", CALLS, ids=["exec", "shell", "grab", "internal-grab", "display", "windows"])
@pytest.mark.parametrize("deadline", [0, -1])
def test_expired_deadline_starts_no_io(server, call, deadline):
    with pytest.raises(TimeoutError):
        call(deadline)


@pytest.mark.parametrize("call", CALLS, ids=["exec", "shell", "grab", "internal-grab", "display", "windows"])
@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), float("-inf"), "1", True, object(), 10**1000],
                         ids=["nan", "inf", "-inf", "text", "bool", "object", "overflow"])
def test_invalid_deadlines_fail_before_io(server, call, deadline):
    with pytest.raises(ValueError, match="deadline"):
        call(deadline)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "1", True, 10**1000],
                         ids=["zero", "negative", "nan", "inf", "text", "bool", "overflow"])
def test_invalid_relative_timeout_fails_before_io(server, timeout):
    with pytest.raises(ValueError, match="timeout"):
        overlays.windows("unit", timeout=timeout, deadline=1)


def test_capture_short_retry_keeps_deadline(server, screen, clock):
    first = server(exchange(b"short", cost=.625))
    second = server(exchange(raw_frame(), cost=.5))
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)
    assert clock.now == 1
    assert first.closed and second.closed
    assert second.operations[0][2] == .375


def test_deadline_capture_retries_transport_without_recovery(server, screen, monkeypatch):
    error = [("connect", 0, None), ("send", .25, None),
             ("recv", 0, b"FAIL"), ("recv", 0, b"0007"), ("recv", 0, b"offline")]
    first = server(error)
    second = server(exchange(raw_frame(), cost=.25))
    monkeypatch.setattr(adbclient, "recover", lambda *a, **k: pytest.fail("unbounded recovery"))
    frame = capture.grab(deadline=1)
    assert frame.shape == (16, 16, 3)
    assert first.closed and second.closed
    assert second.operations[0][2] == .75


def test_deadline_capture_transport_failure_is_not_absence(server, screen, monkeypatch):
    error = [("connect", 0, None), ("send", 0, None),
             ("recv", 0, b"FAIL"), ("recv", 0, b"0007"), ("recv", 0, b"offline")]
    server(error)
    server(error)
    monkeypatch.setattr(adbclient, "recover", lambda *a, **k: pytest.fail("unbounded recovery"))
    with pytest.raises(capture.CaptureError, match="offline") as raised:
        capture.grab(deadline=1)
    assert isinstance(raised.value.__cause__, adbclient.AdbError)


def test_capture_socket_timeout_is_not_retried_or_wrapped(server, screen, monkeypatch):
    wire = server(exchange(raw_frame(), cost=2))
    monkeypatch.setattr(adbclient, "recover", lambda *a, **k: pytest.fail("unbounded recovery"))
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)
    assert wire.closed


def test_time_spent_logging_retry_cannot_authorize_another_capture(server, screen, clock, monkeypatch):
    wire = server(exchange(b"short"))

    def event(kind, **fields):
        if kind == "capture_retry":
            clock.now = 1

    monkeypatch.setattr(capture.logger, "event", event)
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)
    assert wire.closed


def test_time_spent_logging_recovery_cannot_return_late_frame(server, screen, clock, monkeypatch):
    server(exchange(b"short"))
    server(exchange(raw_frame()))

    def event(kind, **fields):
        if kind == "capture_recovered":
            clock.now = 1

    monkeypatch.setattr(capture.logger, "event", event)
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)


@pytest.mark.parametrize("kind", ["short-payload", "wrong-resolution"])
def test_display_fallback_cannot_start_a_new_budget(server, screen, clock, kind):
    if kind == "short-payload":
        server(exchange(b"short", cost=.25))
        server(exchange(b"short", cost=.25))
    else:
        screen.pop("display")
        server(exchange(raw_frame(20, 20), cost=.5))
    sf = b'Display 2 (HWC display 1): port=1 displayName="mumuscreen001"\n'
    dd = b'mDisplayId=2\n mPrimaryDisplayDevice=mumuscreen001(local:2)\n'
    first_probe = server(exchange(sf, cost=.125))
    second_probe = server(exchange(dd, cost=.125))
    retried = server(exchange(raw_frame(), cost=.5))
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)
    assert first_probe.sent[-1] == "exec:dumpsys SurfaceFlinger --display-id"
    assert second_probe.sent[-1] == "exec:dumpsys display"
    assert retried.sent[-1] == "exec:screencap -d 2"
    assert retried.operations[0][2] == .25
    assert clock.now == 1


def test_initial_display_discovery_consumes_capture_budget(server, screen, clock, monkeypatch):
    monkeypatch.setattr(capture, "_resolved_serial", None)
    server(exchange(b'Display 2 (HWC display 1): port=1 displayName="mumuscreen001"', cost=.5))
    server(exchange(b'mDisplayId=2\n mPrimaryDisplayDevice=mumuscreen001(local:2)', cost=.25))
    wire = server(exchange(raw_frame(), cost=.5))
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)
    assert wire.operations[0][2] == .25
    assert clock.now == 1


@pytest.mark.parametrize("parser", ["parse_screens", "secondary_display", "logical_display"])
def test_display_parsing_cannot_return_late_state(server, screen, clock, monkeypatch, parser):
    server(exchange(b'Display 2 (HWC display 1): port=1 displayName="mumuscreen001"'))
    if parser == "logical_display":
        server(exchange(b'mDisplayId=2\n mPrimaryDisplayDevice=mumuscreen001(local:2)'))
    original = getattr(displays, parser)

    def late(*args):
        result = original(*args)
        clock.now = 1
        return result

    monkeypatch.setattr(displays, parser, late)
    before = dict(screen)
    with pytest.raises(TimeoutError):
        capture.refresh_display(deadline=1)
    assert screen == before


@pytest.mark.parametrize("conversion", ["frombuffer", "cvtColor"])
def test_capture_discards_late_pixel_conversion(server, screen, clock, monkeypatch, conversion):
    server(exchange(raw_frame()))
    module = np if conversion == "frombuffer" else capture.cv2
    original = getattr(module, conversion)

    def late(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.now = 1
        return result

    monkeypatch.setattr(module, conversion, late)
    with pytest.raises(TimeoutError):
        capture.grab(deadline=1)


@pytest.mark.parametrize("reader", ["display", "windows"])
def test_text_decoding_expiry_does_not_return_evidence(server, screen, clock, monkeypatch, reader):
    class LateBytes(bytes):
        def decode(self, *args, **kwargs):
            clock.now = 1
            return super().decode(*args, **kwargs)

    monkeypatch.setattr(adbclient, "shell", lambda *a, **k: LateBytes(b""))
    with pytest.raises(TimeoutError):
        if reader == "display":
            capture.refresh_display(deadline=1)
        else:
            overlays.windows("unit", deadline=1)


def test_empty_window_list_is_only_returned_within_budget(server):
    server(exchange(b"", cost=.25))
    assert overlays.windows("unit", deadline=1) == []


def test_deadline_discovery_keeps_the_default_resolution_lock(server, screen):
    server(exchange(raw_frame(20, 20)))
    with pytest.raises(capture.CaptureError, match="unexpected resolution"):
        capture.grab("unit", deadline=1)
    server(exchange(raw_frame(20, 20)))
    assert capture.grab("unit", discover=True, deadline=1).shape == (20, 20, 3)


def test_window_inventory_honors_visibility_and_deadline(server):
    data = (b'Window #0 Window{ab1 u0 NotificationShade}\n isVisible=false\n'
            b'Window #1 Window{ab2 u0 com.TechTreeGames.TheTower/Activity}\n isOnScreen=true\n')
    server(exchange(data, cost=.25))
    assert overlays.windows("unit", visible_only=True, timeout=.5, deadline=1) == [
        "com.TechTreeGames.TheTower/Activity"]


def test_window_transport_error_is_not_empty_inventory(server):
    server([("connect", 0, ConnectionRefusedError("offline"))])
    with pytest.raises(ConnectionRefusedError):
        overlays.windows("unit", deadline=1)


@pytest.mark.parametrize("parser", ["finditer", "findall"])
def test_window_parsing_discards_late_even_empty_evidence(server, clock, monkeypatch, parser):
    data = b'Window #0 Window{ab1 u0 NotificationShade}\n isVisible=false\n'
    server(exchange(data))
    original = getattr(overlays.re, parser)

    def late(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.now = 1
        return result

    monkeypatch.setattr(overlays.re, parser, late)
    with pytest.raises(TimeoutError):
        overlays.windows("unit", visible_only=True, deadline=1)
