"""adbclient.recover(): the one device-recovery primitive, socket only.

Live 2026-10-12 ~16:39: the adb server was found freshly restarted and the
TCP device 127.0.0.1:16384 was no longer attached, so the dashboard read
"missing" until a manual host:connect. recover() re-attaches a dropped TCP
device (host:connect), clears an 'offline' transport first (host:disconnect -
a connect to a transport the server still holds answers "already connected"
and changes nothing) once it has read 'offline' for _OFFLINE_STALE_S, and starts a down server ONLY through the caller's
injected callable, once. It never sends kill-server.

Every test talks to a loopback fake adb server on an ephemeral port
(adbclient._HOST is monkeypatched) - never the real server on 5037.
"""

import itertools
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from device import adbclient

SERIAL = "127.0.0.1:16384"


def _okay(text: str) -> bytes:
    return b"OKAY" + b"%04x" % len(text) + text.encode()


def _fail(text: str) -> bytes:
    return b"FAIL" + b"%04x" % len(text) + text.encode()


class FakeAdb:
    """A loopback adb server holding one TCP device. One host request per
    connection, like the real host services. `state` is the device as the
    server sees it ('device' / 'offline' / 'missing'); `states`, when set,
    overrides the get-state answers one by one (a flapping transport).
    host:connect attaches a missing device (`connect_ok`), and answers
    "already connected" for one the server still holds, changing nothing;
    host:disconnect drops it."""

    def __init__(self, monkeypatch, state="missing", connect_ok=True, port=0):
        self.state = state
        self.states = None
        self.connect_ok = connect_ok
        # a transport attached by host:connect reads 'offline' for this many
        # seconds before 'device' (a slow handshake)
        self.handshake = 0.0
        self.connected_at = 0.0
        self.requests = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(8)
        monkeypatch.setattr(adbclient, "_HOST", self.srv.getsockname())
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                n = int(conn.recv(4), 16)
                req = conn.recv(n).decode()
                self.requests.append(req)
                if req.startswith("host:transport:") and self.state == "device":
                    # a transport that opens: OKAY, then the exec request, OKAY, EOF
                    conn.sendall(b"OKAY")
                    conn.recv(int(conn.recv(4), 16))
                    conn.sendall(b"OKAY")
                    continue
                conn.sendall(self._answer(req))

    def _answer(self, req):
        if req.startswith("host-serial:") and req.endswith(":get-state"):
            st = next(self.states) if self.states is not None else self.state
            if st == "device" and time.monotonic() < self.connected_at + self.handshake:
                st = "offline"
            if st == "missing":
                return _fail(f"device '{SERIAL}' not found")
            if st == "offline":
                return _fail("device offline")
            return _okay(st)
        if req == f"host:disconnect:{SERIAL}":
            self.state = "missing"
            return _okay(f"disconnected {SERIAL}")
        if req == f"host:connect:{SERIAL}":
            if not self.connect_ok:
                return _okay(f"failed to connect to '{SERIAL}': No connection could be made")
            if self.state != "missing":
                return _okay(f"already connected to {SERIAL}")
            self.state = "device"
            self.connected_at = time.monotonic()
            return _okay(f"connected to {SERIAL}")
        if req.startswith("host:transport:"):       # only reached while the device is not online
            return _fail("device offline" if self.state == "offline" else f"device '{SERIAL}' not found")
        if req == "host:devices":
            return _okay(f"{SERIAL}\tdevice\nemulator-5554\toffline\n")
        return _fail(f"unknown host service {req}")

    def count(self, prefix):
        return sum(r.startswith(prefix) for r in self.requests)

    def close(self):
        self.srv.close()


@pytest.fixture(autouse=True)
def _no_offline_clock():
    """recover() keeps a per-process clock of how long a serial read 'offline';
    start each test clean."""
    adbclient._offline_since.clear()
    yield
    adbclient._offline_since.clear()


@pytest.fixture()
def fake(monkeypatch):
    srv = FakeAdb(monkeypatch)
    yield srv
    srv.close()
    # the server is shared with every other client: never kill it
    assert not any("kill" in r for r in srv.requests)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    return port


def _offline_for(seconds):
    """Make recover() believe this process has read SERIAL 'offline' for
    `seconds` already (on the real clock)."""
    adbclient._offline_since[(adbclient._HOST, SERIAL)] = time.monotonic() - seconds


def test_an_online_device_is_left_alone(fake):
    fake.state = "device"
    assert adbclient.recover(SERIAL) == "device"
    assert fake.requests == [f"host-serial:{SERIAL}:get-state"]


def test_a_missing_device_is_connected_and_verified(fake):
    assert adbclient.recover(SERIAL, settle=0.5) == "device"
    assert fake.requests == [
        f"host-serial:{SERIAL}:get-state",
        f"host:connect:{SERIAL}",
        f"host-serial:{SERIAL}:get-state",
    ]


def test_an_offline_device_is_disconnected_before_the_connect(fake):
    fake.state = "offline"
    _offline_for(adbclient._OFFLINE_STALE_S)    # stale: offline for the whole bound
    assert adbclient.recover(SERIAL, settle=0.5) == "device"
    assert fake.requests == [
        f"host-serial:{SERIAL}:get-state",
        f"host:disconnect:{SERIAL}",
        f"host:connect:{SERIAL}",
        f"host-serial:{SERIAL}:get-state",
    ]


def test_a_down_server_is_started_once_by_the_caller_then_connected(monkeypatch):
    port = _free_port()
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", port))
    started = []

    def start_server():
        started.append(FakeAdb(monkeypatch, port=port))  # a fresh server: no device

    try:
        assert adbclient.recover(SERIAL, start_server, settle=0.5) == "device"
        assert len(started) == 1
        assert started[0].count("host:connect:") == 1
    finally:
        for srv in started:
            srv.close()


def test_a_server_that_stays_down_is_started_once_not_per_attempt(monkeypatch):
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", _free_port()))
    calls = []
    assert adbclient.recover(SERIAL, lambda: calls.append(1), attempts=3, settle=0.1) == "no_server"
    assert calls == [1]
    # no callable: adbclient never spawns anything itself
    assert adbclient.recover(SERIAL, settle=0.1) == "no_server"


def test_a_server_that_accepts_and_never_answers_is_not_restarted(monkeypatch):
    # a wedged server is listening: start-server there can kill it (an adb
    # client of another version kills the server it finds), so recover leaves
    # it alone and reports the state it could not read
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    monkeypatch.setattr(adbclient, "_HOST", srv.getsockname())
    calls = []
    try:
        assert adbclient.recover(SERIAL, lambda: calls.append(1), attempts=0) == "unknown"
        assert calls == []
    finally:
        srv.close()


def test_a_connect_timeout_is_not_a_down_server(monkeypatch):
    # a server whose accept queue is full times the connect out; only a
    # REFUSED connect means nothing listens
    def timed_out(address, timeout=None, *a, **k):
        assert address == adbclient._HOST   # nothing else is dialled
        raise TimeoutError("timed out")

    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", _free_port()))
    monkeypatch.setattr(adbclient.socket, "create_connection", timed_out)
    calls = []
    assert adbclient.recover(SERIAL, lambda: calls.append(1), attempts=3, settle=0.1) == "no_server"
    assert calls == []


def test_a_refused_connect_is_retried_a_bounded_number_of_times(fake):
    fake.connect_ok = False
    t0 = time.monotonic()
    assert adbclient.recover(SERIAL, attempts=3, settle=0.5) == "missing"
    assert fake.count("host:connect:") == 3
    assert time.monotonic() - t0 < 5


def test_offline_right_after_the_connect_is_the_handshake_not_a_new_drop(fake):
    # a fresh TCP transport reads 'offline' until its handshake completes:
    # recover keeps polling instead of disconnecting it
    fake.state = "offline"
    fake.states = iter(["offline", "offline", "offline", "device"])
    assert adbclient.recover(SERIAL, settle=2.0) == "device"
    assert fake.count("host:disconnect:") == 0
    assert fake.count("host:connect:") == 1


def test_a_flapping_device_is_bounded_and_reports_its_last_state(fake):
    fake.state = "offline"
    fake.states = iter(["offline"] * 1000)
    t0 = time.monotonic()
    assert adbclient.recover(SERIAL, attempts=3, settle=0.3) == "offline"
    # offline for well under _OFFLINE_STALE_S: never disconnected, only
    # re-connected and waited on (an 'offline' then may be a handshake)
    assert fake.count("host:disconnect:") == 0
    assert fake.count("host:connect:") == 3
    assert time.monotonic() - t0 < 5


def test_an_emulator_token_is_reported_never_touched(monkeypatch):
    srv = FakeAdb(monkeypatch)
    try:
        assert adbclient.recover("emulator-5554") == "missing"
        assert adbclient.recover("") == "unconfigured"
        assert srv.requests == ["host-serial:emulator-5554:get-state"]
    finally:
        srv.close()


def test_a_down_server_is_started_for_an_emulator_token_too(monkeypatch):
    # starting the server is the one recovery an emulator-NNNN serial has (the
    # old shard _kick_adb and /api/frame.png fallback ran start-server for
    # every serial); recover still sends it no connect
    port = _free_port()
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", port))
    started = []

    def start_server():
        started.append(FakeAdb(monkeypatch, state="device", port=port))

    try:
        assert adbclient.recover("emulator-5554", start_server) == "device"
        assert len(started) == 1
        assert started[0].requests == ["host-serial:emulator-5554:get-state"]
    finally:
        for srv in started:
            srv.close()


def test_a_slow_handshake_is_never_torn_down_by_the_same_recover(fake):
    # the transport recover connected still reads 'offline' when its settle
    # window ends: a later round must not host:disconnect it and restart the
    # handshake from zero (review: a 2.5 s handshake never converged)
    fake.handshake = 0.8
    assert adbclient.recover(SERIAL, attempts=3, settle=0.5) == "device"
    assert fake.count("host:disconnect:") == 0


def test_a_slow_handshake_is_never_torn_down_by_the_next_recover_either(fake):
    # boot polls recover every 3 s and shard runs two back to back: a
    # transport whose handshake outlasts ONE call (here 1.2 s against two
    # 0.3 s settles) reads 'offline' to the next call, which must not
    # host:disconnect what the previous call connected (review: a 12 s
    # handshake never converged, boot spent its 240 s disconnecting)
    fake.state = "offline"      # a stale transport the first call clears
    _offline_for(adbclient._OFFLINE_STALE_S)
    fake.handshake = 1.2
    states = []
    for _ in range(10):
        states.append(adbclient.recover(SERIAL, attempts=2, settle=0.3))
        if states[-1] == "device":
            break
    assert states[-1] == "device", states
    assert fake.count("host:disconnect:") == 1


def test_an_offline_transport_older_than_the_bound_is_dropped_and_the_clock_restarts(fake, clock):
    # the bound runs from the first 'offline' read: a transport that stays
    # 'offline' past it is stale, not slow, and the next bound starts at the
    # teardown (virtual clock, so no wall-clock timing)
    fake.state = "offline"
    fake.handshake = 60.0
    assert adbclient.recover(SERIAL, attempts=1, settle=0.0) == "offline"
    assert fake.count("host:disconnect:") == 0
    clock.t = adbclient._OFFLINE_STALE_S
    assert adbclient.recover(SERIAL, attempts=1, settle=0.0) == "offline"
    assert fake.count("host:disconnect:") == 1
    assert adbclient.recover(SERIAL, attempts=1, settle=0.0) == "offline"
    assert fake.count("host:disconnect:") == 1      # the new clock is young
    clock.t = 2 * adbclient._OFFLINE_STALE_S
    assert adbclient.recover(SERIAL, attempts=1, settle=0.0) == "offline"
    assert fake.count("host:disconnect:") == 2


def test_a_connect_that_reached_device_leaves_no_offline_clock(fake):
    # once the transport read 'device' the clock is gone: a later 'offline' is
    # a new drop with a new clock, not disconnected on first sight
    fake.state = "missing"
    assert adbclient.recover(SERIAL, settle=0.5) == "device"
    assert (adbclient._HOST, SERIAL) not in adbclient._offline_since
    fake.state = "offline"
    assert adbclient.recover(SERIAL, attempts=1, settle=0.1) == "offline"
    assert fake.count("host:disconnect:") == 0


class _Clock:
    """A virtual clock: recover()'s time.monotonic / time.sleep read and move
    `t`, so a hundred seconds of polling costs no wall time."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(adbclient, "time", SimpleNamespace(monotonic=c.monotonic, sleep=c.sleep))
    return c


@pytest.mark.parametrize("every", [1.0, 3.0])
def test_a_transport_that_stays_offline_gets_one_disconnect_per_bound(fake, clock, every):
    # host:connect answers "already connected" for a transport the server holds
    # and changes nothing, so it must not restart the stale clock: over 100 s of
    # continuous 'offline' there are exactly three disconnects (t = 30, 60, 90)
    # however often recover is called. Every call costs three loopback
    # connects to the fake, so the step stays coarse (a 0.25 s step made 1206
    # connects in this one test and could exhaust Windows socket buffers)
    fake.states = itertools.repeat("offline")
    while clock.t <= 100:
        adbclient.recover(SERIAL, attempts=1, settle=0.0)
        clock.t += every
    assert fake.count("host:disconnect:") == 3


def test_the_first_sight_of_an_offline_transport_is_never_disconnected(fake, clock):
    # a manual 'adb connect' followed by an immediate poll reads 'offline' for
    # the handshake: the clock starts at that first read and the teardown waits
    # for the full bound after it
    fake.state = "offline"
    fake.states = itertools.repeat("offline")
    for _ in range(3):
        assert adbclient.recover(SERIAL, attempts=3, settle=0.4) == "offline"
    assert fake.count("host:disconnect:") == 0
    clock.t = adbclient._OFFLINE_STALE_S - 0.1
    adbclient.recover(SERIAL, attempts=1, settle=0.0)
    assert fake.count("host:disconnect:") == 0
    clock.t = adbclient._OFFLINE_STALE_S
    adbclient.recover(SERIAL, attempts=1, settle=0.0)
    assert fake.count("host:disconnect:") == 1


def test_an_offline_transport_that_turns_device_after_12_s_is_never_disconnected(fake, clock):
    fake.states = (("offline" if clock.t < 12 else "device") for _ in itertools.count())
    results = []
    for _ in range(8):
        results.append(adbclient.recover(SERIAL, attempts=1, settle=0.0))
        clock.t += 3
    assert results == ["offline"] * 4 + ["device"] * 4
    assert fake.count("host:disconnect:") == 0


@pytest.mark.parametrize("state", ["device", "missing"])
def test_reading_device_or_missing_restarts_the_offline_clock(fake, clock, state):
    # a read of 'device' or 'missing' drops the stamp: a later 'offline' is a new
    # drop with a new clock, not the old one still running (40 s after the first
    # stamp, but only a first sight now)
    phase = {"s": "offline"}
    fake.states = (phase["s"] for _ in itertools.count())
    adbclient.recover(SERIAL, attempts=1, settle=0.0)           # t=0: stamped
    clock.t, phase["s"] = 10.0, state
    connects = fake.count("host:connect:")
    adbclient.recover(SERIAL, attempts=1, settle=0.0)
    if state == "missing":
        assert fake.count("host:connect:") == connects + 1      # missing still gets a connect
    clock.t, phase["s"] = 40.0, "offline"
    adbclient.recover(SERIAL, attempts=1, settle=0.0)
    clock.t = 69.9
    adbclient.recover(SERIAL, attempts=1, settle=0.0)
    assert fake.count("host:disconnect:") == 0
    clock.t = 70.0
    adbclient.recover(SERIAL, attempts=1, settle=0.0)
    assert fake.count("host:disconnect:") == 1


def test_devices_lists_only_online_serials(fake):
    assert adbclient.devices() == [SERIAL]
    assert fake.requests == ["host:devices"]


def test_devices_without_a_listening_server_raises_connection_refused(monkeypatch):
    # the wizard starts a server only on this exception (not on a timeout)
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", _free_port()))
    with pytest.raises(ConnectionRefusedError):
        adbclient.devices()


def test_a_failed_screencap_recovers_the_device_before_its_retry(monkeypatch):
    # every runner's frames come through capture.grab: its one retry used
    # host:connect alone, which answers "already connected" for an offline
    # transport and changes nothing (review R14)
    from device import capture

    calls = []

    def exec_out(serial, cmd, timeout=None):
        calls.append(("exec", serial))
        raise adbclient.AdbError("transport: device offline")

    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", _free_port()))   # a missed stub never meets 5037
    monkeypatch.setattr(adbclient, "exec_out", exec_out)
    monkeypatch.setattr(adbclient, "recover", lambda serial, start_server=None, **kw: calls.append(("recover", serial, start_server)) or "device")
    monkeypatch.setattr(adbclient, "reconnect", lambda serial: calls.append(("reconnect", serial)) or True)
    with pytest.raises(capture.CaptureError, match="device offline"):
        capture.grab(SERIAL)
    # no start_server: capture never spawns the daemon
    assert calls == [("exec", SERIAL), ("recover", SERIAL, None), ("exec", SERIAL)]


def _boot_over(fake, monkeypatch, tmp_path, wait, **recover_kw):
    """Stub everything boot.main touches except adbclient.recover (real, run
    against `fake`, with `recover_kw` merged into each call); boot._wait is
    `wait`. Returns (boot, events, recover_calls)."""
    import sys
    import settings
    from device import boot, tool_install
    from runtime import logger

    events, recover_calls = [], []
    real_recover = adbclient.recover

    def recover(serial, *a, **kw):
        recover_calls.append((serial, a, kw))
        return real_recover(serial, *a, **kw, **recover_kw)

    monkeypatch.setattr(sys, "argv", ["boot.py", "--instance", "main"])
    monkeypatch.setattr(settings, "bind_device", lambda *a: None)
    monkeypatch.setattr(settings, "instance", lambda name=None: {"serial": SERIAL})
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "CONFIG", {"active_instance": "main"})
    monkeypatch.setattr(tool_install, "ensure_server", lambda *a, **k: None)
    monkeypatch.setattr(adbclient, "recover", recover)
    # the fake speaks host services only: "a transport opens" = the server reads 'device'
    monkeypatch.setattr(adbclient, "alive", lambda serial: fake.state == "device")
    monkeypatch.setattr(adbclient, "shell", lambda *a, **k: b"1")
    monkeypatch.setattr(boot.overlays, "clean", lambda: True)
    monkeypatch.setattr(boot.overlays, "windows", lambda *a: [boot.overlays.GAME_PKG])
    monkeypatch.setattr(boot, "_wait", wait)
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append(dict(kind=kind, **kw)))
    return boot, events, recover_calls


def test_boot_clears_an_offline_transport_without_spawning_adb(fake, monkeypatch, tmp_path):
    # architecture review R14: boot's adb stage ran host:connect alone, which
    # answers "already connected" for an 'offline' transport, so a boot over a
    # stale transport gave up after 240.9 s with "device offline". The stage
    # now runs adbclient.recover (disconnect + connect) and passes it no
    # start_server: tool_install.ensure_server is boot's one daemon start.
    fake.state = "offline"
    _offline_for(adbclient._OFFLINE_STALE_S)    # a stale transport (the bound has passed)
    boot, events, recover_calls = _boot_over(fake, monkeypatch, tmp_path, lambda timeout, fn: fn())
    assert boot.main() == 0
    assert {"kind": "boot_stage", "stage": "adb", "ok": True} == {
        k: v for k, v in next(e for e in events if e.get("stage") == "adb").items() if k != "waited"}
    assert fake.count("host:disconnect:") == 1
    assert recover_calls == [(SERIAL, (), {})]   # no start_server


def test_boot_polls_through_a_handshake_longer_than_one_recover(fake, monkeypatch, tmp_path):
    # the real _wait calls adb_up (one recover) on every poll: a 1.0 s
    # handshake against 0.4 s calls must finish across polls, not be
    # disconnected by each (review: boot spent its whole 240 s budget
    # disconnecting a transport that needed 12 s). Poll and budget shortened.
    from device import boot
    real_wait = boot._wait
    fake.state = "offline"
    _offline_for(adbclient._OFFLINE_STALE_S)    # stale: the first call tears it down once
    fake.handshake = 1.0
    monkeypatch.setitem(boot.STAGE_TIMEOUT, "adb", 6)
    boot, events, recover_calls = _boot_over(
        fake, monkeypatch, tmp_path, lambda timeout, fn: real_wait(timeout, fn, poll=0.05),
        attempts=2, settle=0.2)
    assert boot.main() == 0
    assert fake.count("host:disconnect:") == 1
    assert len(recover_calls) > 1       # it really took several polls


@pytest.mark.parametrize("state,exit_code", [("device", 0), ("offline", 1), ("missing", 1)])
def test_boot_over_an_emulator_token_reads_its_state_and_sends_no_connect(
        fake, monkeypatch, tmp_path, state, exit_code):
    # an emulator-NNNN serial has no host:port to host:connect, so recover()
    # only reads its state: boot's adb stage passes when the server lists it as
    # 'device' and otherwise fails after one get-state per poll, with no
    # connect or disconnect. (The old `reconnect(serial) and alive(serial)`
    # sent host:connect:emulator-NNNN, which cannot attach it.)
    import settings
    fake.state = state
    boot, events, recover_calls = _boot_over(fake, monkeypatch, tmp_path, lambda timeout, fn: fn())
    monkeypatch.setattr(settings, "instance", lambda name=None: {"serial": "emulator-5554"})
    assert boot.main() == exit_code
    assert next(e for e in events if e.get("stage") == "adb")["ok"] is (state == "device")
    assert recover_calls == [("emulator-5554", (), {})]
    assert fake.requests[0] == "host-serial:emulator-5554:get-state"
    assert not any(r.startswith(("host:connect", "host:disconnect")) for r in fake.requests)


def test_shard_kick_adb_recovers_without_kill_server(monkeypatch):
    # architecture review R15: _kick_adb ran kill-server + start-server +
    # connect on every capture failure; kill-server drops the TCP device
    import settings
    from flows import shard
    from runtime import logger

    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", _free_port()))   # a missed stub never meets 5037
    monkeypatch.setitem(settings.CONFIG["adb"], "exe", "C:\\emu\\adb.exe")
    inst = settings.CONFIG["active_instance"]
    monkeypatch.setitem(settings.CONFIG["instances"][inst], "serial", SERIAL)
    spawned, recovered = [], []
    monkeypatch.setattr(settings, "run_hidden", lambda args, **kw: spawned.append(list(args)))

    def recover(serial, start_server=None, **kw):
        recovered.append(serial)
        start_server()  # what recover does when no server listens
        return "device"

    events = []
    monkeypatch.setattr(adbclient, "recover", recover)
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))
    shard._kick_adb()
    assert recovered == [SERIAL]
    assert spawned == [["C:\\emu\\adb.exe", "start-server"]]
    assert events == [("shard_adb_recover", {"state": "device"})]


def test_every_get_state_read_keeps_the_offline_clock(fake, clock):
    # not only recover's own reads: the dashboard's status polls call
    # get_state too, and a transport one of them saw dropped is a new one
    key = (adbclient._HOST, SERIAL)
    fake.state = "offline"
    assert adbclient.get_state(SERIAL) == "offline"
    started = adbclient._offline_since[key]
    clock.t = 5.0                       # a re-stamp would show: the clock has moved
    assert adbclient.get_state(SERIAL) == "offline"
    assert adbclient._offline_since[key] == started         # never re-stamped
    fake.state = "unauthorized"
    assert adbclient.get_state(SERIAL) == "unauthorized"
    assert adbclient._offline_since[key] == started         # any other answer leaves it
    fake.state = "missing"
    assert adbclient.get_state(SERIAL) == "missing"
    assert key not in adbclient._offline_since
    fake.state = "offline"
    adbclient.get_state(SERIAL)
    fake.state = "device"
    assert adbclient.get_state(SERIAL) == "device"
    assert key not in adbclient._offline_since


def test_a_no_server_read_ends_the_offline_clock(fake):
    # a server restart drops every transport: whatever is attached afterwards is
    # a new one, so the clock an earlier 'offline' started must not run on into it
    key = (adbclient._HOST, SERIAL)
    fake.state = "offline"
    assert adbclient.get_state(SERIAL) == "offline"
    assert key in adbclient._offline_since
    fake.close()                                # nothing listens now
    assert adbclient.get_state(SERIAL) == "no_server"
    assert key not in adbclient._offline_since


def test_a_transport_that_executes_a_command_ends_the_offline_clock(fake):
    # a runner never reads get_state outside recover(); its successful commands
    # are the only proof the transport came back, so a later drop is a new one
    key = (adbclient._HOST, SERIAL)
    fake.state = "offline"
    adbclient.get_state(SERIAL)
    assert key in adbclient._offline_since
    with pytest.raises(adbclient.AdbError):     # still offline: the failed command proves nothing
        adbclient.exec_out(SERIAL, "true")
    assert key in adbclient._offline_since
    fake.state = "device"
    adbclient.exec_out(SERIAL, "true")
    assert key not in adbclient._offline_since


def test_a_second_drop_after_a_quiet_recovery_is_not_torn_down_at_first_sight(fake, clock):
    # capture.grab -> recover ends on 'offline' (clock starts at t=0); the
    # transport then comes back on its own and the runner's commands succeed;
    # a NEW drop ten minutes later is a first sight, not a stale transport
    from device import capture

    fake.state = "offline"
    with pytest.raises(capture.CaptureError):
        capture.grab(SERIAL, "0")
    clock.t = 5.0
    fake.state = "device"
    adbclient.exec_out(SERIAL, "screencap")
    clock.t = 600.0
    fake.state = "offline"
    with pytest.raises(capture.CaptureError):
        capture.grab(SERIAL, "0")
    assert fake.count("host:disconnect:") == 0
