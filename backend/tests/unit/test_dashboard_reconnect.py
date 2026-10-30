"""Dashboard auto-reconnect: while the adb server does not list the active
device as `device` and the emulator's adb port answers (the VM is up), each
/api/status poll may start ONE background adbclient.recover - one in flight,
at most one per 5 s, backoff doubling to 60 s, 'gave_up' after 10 failed
attempts until the device reads online, the serial changes, a manual
reconnect / adopt resets it, or the server's situation flips (once per outage).
/api/status reports it as `reconnect`. Live 2026-10-12 ~16:39: the server
restarted, the TCP device stayed detached, Live read "missing" until a
manual host:connect.

adbclient.get_state / recover are stubbed; no socket reaches an adb server.
"""

import paths as test_paths

import importlib.util
import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from device import adbclient

_REAL_GET_STATE = adbclient.get_state
_REAL_RECOVER = adbclient.recover

BACKEND = str(test_paths.BACKEND)
SERIAL = "127.0.0.1:16384"
ADB = "C:\\emu\\adb.exe"


def _dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    return ("127.0.0.1", port)


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    # any adbclient call a stub below misses meets a dead loopback port,
    # never the real adb server on 5037
    monkeypatch.setattr(adbclient, "_HOST", _dead_port())
    path = str(test_paths.DASHBOARD)
    spec = importlib.util.spec_from_file_location("tp_dashboard_reconnect", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "backend"
    (root / "logs" / "main").mkdir(parents=True)
    monkeypatch.setattr(mod, "ROOT", str(root))
    cfg = {"active_instance": "main", "adb": {"exe": ADB},
           "instances": {"main": {"serial": SERIAL}}}
    monkeypatch.setattr(mod, "load_config", lambda: cfg)
    import player.scan_plan

    monkeypatch.setattr(player.scan_plan, "control_gate", lambda root, cfg: {})
    mod.process_rows = []
    monkeypatch.setattr(mod, "_scan_procs", lambda target: list(mod.process_rows))
    monkeypatch.setattr(mod, "_procs", lambda: [])
    monkeypatch.setattr(mod, "_setup_complete", lambda: True)
    monkeypatch.setattr(mod, "_calibrate_missing", lambda: [])
    mod.cfg = cfg
    mod.port = {"open": True}
    monkeypatch.setattr(mod, "_port_open", lambda serial: mod.port["open"])
    mod.state = {"now": "offline"}
    monkeypatch.setattr(adbclient, "get_state", lambda serial, timeout=1.0: mod.state["now"])
    mod.recovered = []
    mod.recover_kw = []
    mod.result = {"state": "offline"}

    def recover(serial, start_server=None, **kw):
        mod.recovered.append(serial)
        mod.recover_kw.append(kw)
        if mod.result.get("after"):
            mod.state["now"] = mod.result["after"]
        if mod.result.get("gate"):
            mod.result["gate"].wait(5)
        if mod.result.get("start"):
            start_server()
        if mod.result.get("raise"):
            raise mod.result["raise"]
        return mod.result["state"]

    monkeypatch.setattr(adbclient, "recover", recover)
    mod.spawned = []
    monkeypatch.setattr(mod, "_run", lambda args, **kw: mod.spawned.append(list(args)))
    yield mod
    _settle(mod)


def _settle(dash):
    """Wait for the in-flight recovery thread, if any."""
    t = dash._RECONNECT.get("thread")
    if t is not None:
        t.join(5)
        assert not t.is_alive()


def _status(c):
    return c.get("/api/status").get_json()


def _expire_backoff(dash):
    dash._RECONNECT["next_at"] = 0.0


def _set_process_rows(dash, rows):
    dash.process_rows[:] = rows
    dash._procs_refresh()


def _give_up(dash, c):
    """Ten failed attempts (each starts the moment its backoff is waived)."""
    for _ in range(10):
        _expire_backoff(dash)
        _status(c)
        _settle(dash)
    j = _status(c)["reconnect"]
    assert j["state"] == "gave_up" and j["attempts"] == 10
    assert len(dash.recovered) == 10


def test_status_carries_an_idle_reconnect_while_the_device_is_online(dash):
    dash.state["now"] = "device"
    with dash.app.test_client() as c:
        j = _status(c)
    assert j["reconnect"] == {"state": "idle", "attempts": 0, "next_try_in": None, "last_error": ""}
    assert dash.recovered == []
    assert (j["connected"], j["device_state"], j["port_open"]) == (True, "device", True)


def test_a_dropped_device_with_its_port_open_is_recovered_in_the_background(dash):
    gate = threading.Event()
    dash.result.update(state="device", gate=gate)
    with dash.app.test_client() as c:
        assert _status(c)["reconnect"] == {"state": "trying", "attempts": 0, "next_try_in": None, "last_error": ""}
        gate.set()
        _settle(dash)
        dash.state["now"] = "device"
        j = _status(c)
    assert dash.recovered == [SERIAL]
    assert j["reconnect"] == {"state": "idle", "attempts": 0, "next_try_in": None, "last_error": ""}


def _discovery_snapshot(dash, case):
    target = dash._process_target()
    cache = dict(target=target, at=time.time(), attempted_at=time.time(), procs=[], error=None)
    if case == 'pending':
        cache['at'] = 0.
    elif case == 'stale':
        cache['at'] -= 7
    elif case == 'error':
        cache['error'] = {'type': 'PermissionError', 'message': 'process discovery failed'}
    elif case == 'wrong_serial':
        cache['target'] = (*target[:2], 'emulator-5556')
    elif case == 'wrong_host':
        cache['target'] = ('other-adb-host', *target[1:])
    elif case == 'wrong_port':
        cache['target'] = (target[0], target[1] + 1, target[2])
    elif case == 'future':
        cache['at'] += 60
    elif case == 'active_runner':
        cache['procs'] = [{'pid': 1, 'runner': 'orchestrator', 'cmdline': ''}]
    dash._PROCS_CACHE = cache
    # Model normal server startup: discovery is asynchronous and a pending
    # snapshot cannot be replaced by an inline scan on the status request.
    dash._PROCS_THREAD = SimpleNamespace(is_alive=lambda: True)
    return cache


UNSAFE_DISCOVERY = ['pending', 'stale', 'error', 'wrong_serial', 'wrong_host',
                    'wrong_port', 'future', 'active_runner']


@pytest.mark.parametrize('case', UNSAFE_DISCOVERY)
def test_transport_recovery_requires_fresh_success_for_the_exact_target(dash, case):
    _discovery_snapshot(dash, case)
    dash._reconnect_tick(dash.cfg, SERIAL, 'offline', True)
    _settle(dash)
    assert dash.recovered == []
    assert dash._RECONNECT['state'] == 'idle' and dash._RECONNECT['attempts'] == 0


@pytest.mark.parametrize('case', UNSAFE_DISCOVERY)
def test_unknown_discovery_still_allows_server_only_recovery(dash, case):
    _discovery_snapshot(dash, case)
    dash.result.update(start=True, state='missing')
    dash._reconnect_tick(dash.cfg, SERIAL, 'no_server', True)
    _settle(dash)
    assert dash.recovered == [SERIAL]
    assert dash.recover_kw == [{'attempts': 0}]
    assert dash.spawned == [[ADB, 'start-server']]


def test_fresh_successful_empty_discovery_allows_transport_recovery(dash):
    _discovery_snapshot(dash, 'fresh_empty')
    dash._reconnect_tick(dash.cfg, SERIAL, 'offline', True)
    _settle(dash)
    assert dash.recovered == [SERIAL] and dash.recover_kw == [{}]


def test_an_in_progress_scan_cannot_authorize_recovery_from_its_previous_empty_result(dash):
    _discovery_snapshot(dash, 'fresh_empty')
    with dash._PROCS_LOCK:
        dash._reconnect_tick(dash.cfg, SERIAL, 'offline', True)
    _settle(dash)
    assert dash.recovered == [] and dash._RECONNECT['attempts'] == 0


def test_cache_replacement_during_snapshot_selection_cannot_combine_old_rows_with_new_evidence(dash, monkeypatch):
    _discovery_snapshot(dash, 'fresh_empty')
    original = dash._procs_snapshot
    def racing_snapshot(*args, **kwargs):
        cache, target = original(*args, **kwargs)
        dash._PROCS_CACHE = dict(cache, procs=[{'pid': 2}])
        return cache, target
    monkeypatch.setattr(dash, '_procs_snapshot', racing_snapshot)
    dash._reconnect_tick(dash.cfg, SERIAL, 'offline', True)
    _settle(dash)
    assert dash.recovered == []


@pytest.mark.parametrize('change', ['snapshot', 'serial', 'host', 'stale', 'pending_scan'])
def test_recovery_thread_rechecks_snapshot_and_target_before_touching_transport(dash, monkeypatch, change):
    cache = _discovery_snapshot(dash, 'fresh_empty')
    scheduled = []
    class DeferredThread:
        def __init__(self, *, target, args, **kwargs):
            self.run = lambda: target(*args)
        def start(self):
            scheduled.append(self)
        def is_alive(self):
            return False
        def join(self, timeout=None):
            pass
    monkeypatch.setattr(dash.threading, 'Thread', DeferredThread)
    dash._reconnect_tick(dash.cfg, SERIAL, 'offline', True)
    assert len(scheduled) == 1
    if change == 'snapshot':
        dash._PROCS_CACHE = dict(cache, procs=[{'pid': 2}])
    elif change == 'serial':
        dash.cfg['instances']['main']['serial'] = 'emulator-5556'
    elif change == 'host':
        monkeypatch.setattr(adbclient, '_HOST', ('different-host', adbclient._HOST[1]))
    elif change == 'stale':
        cache['at'] -= 7
    if change == 'pending_scan':
        with dash._PROCS_LOCK:
            scheduled[0].run()
    else:
        scheduled[0].run()
    assert dash.recovered == [] and dash._RECONNECT['attempts'] == 0
    assert dash._RECONNECT['state'] == 'idle'


def test_request_config_and_reported_serial_must_agree_before_recovery(dash):
    _discovery_snapshot(dash, 'fresh_empty')
    dash._reconnect_tick(dash.cfg, 'emulator-5556', 'offline', True)
    _settle(dash)
    assert dash.recovered == []


def test_no_attempt_while_the_emulator_port_is_closed(dash):
    dash.port["open"] = False
    with dash.app.test_client() as c:
        j = _status(c)
    assert dash.recovered == []
    assert j["reconnect"]["state"] == "idle"


def test_one_attempt_in_flight_and_status_never_waits_for_it(dash):
    import time

    gate = threading.Event()
    dash.result["gate"] = gate
    with dash.app.test_client() as c:
        for _ in range(3):
            # the 5 s floor is waived before every poll: only the in-flight
            # guard keeps a second attempt from starting beside the running
            # one, and the stub holds the attempt for up to 5 s, so a poll
            # that waited for it would take that long
            _expire_backoff(dash)
            t0 = time.monotonic()
            assert _status(c)["reconnect"]["state"] == "trying"
            assert time.monotonic() - t0 < 2
        gate.set()
        _settle(dash)
    assert dash.recovered == [SERIAL]


def test_failures_back_off_to_60_s_and_give_up_after_10(dash):
    waits = []
    with dash.app.test_client() as c:
        for _ in range(10):
            _expire_backoff(dash)
            _status(c)
            _settle(dash)
            waits.append(_status(c)["reconnect"])
        # within the backoff window nothing new starts
        _status(c)
        _settle(dash)
        assert len(dash.recovered) == 10
        _expire_backoff(dash)
        j = _status(c)
        _settle(dash)
    assert len(dash.recovered) == 10
    assert [w["attempts"] for w in waits] == list(range(1, 11))
    expected = [5, 10, 20, 40, 60, 60, 60, 60, 60]
    for w, want in zip(waits[:9], expected):
        assert w["state"] == "trying"
        assert want - 1 < w["next_try_in"] <= want
    assert waits[0]["last_error"]
    assert j["reconnect"]["state"] == "gave_up"
    assert j["reconnect"]["attempts"] == 10
    assert j["reconnect"]["next_try_in"] is None


def test_gave_up_holds_when_the_server_later_drops_the_transport_the_attempts_left(dash):
    # recover's last read follows its own host:connect, so it ends on
    # 'offline' (the new transport's handshake); the server later drops that
    # dead transport and the poll reads 'missing'. That is the attempts' own
    # doing, not a change from outside: the give-up must hold
    with dash.app.test_client() as c:
        _give_up(dash, c)
        dash.state["now"] = "missing"
        for _ in range(3):
            _expire_backoff(dash)
            j = _status(c)
            _settle(dash)
    assert len(dash.recovered) == 10
    assert j["reconnect"]["state"] == "gave_up" and j["reconnect"]["attempts"] == 10


def test_gave_up_holds_while_the_state_alternates(dash):
    with dash.app.test_client() as c:
        _give_up(dash, c)
        for now in ["missing", "offline"] * 3:
            dash.state["now"] = now
            _expire_backoff(dash)
            j = _status(c)
            _settle(dash)
    assert len(dash.recovered) == 10
    assert j["reconnect"]["state"] == "gave_up"


def test_the_failure_count_survives_ticks_with_nothing_to_do(dash):
    # the emulator's port flapping (or a runner coming and going) must not
    # restart the count: 10 failed attempts end in gave_up however many
    # polls found nothing to do in between
    with dash.app.test_client() as c:
        for _ in range(15):
            dash.port["open"] = False
            dash._CONN_CACHE["t"] = 0.0     # the port answer is held 10 s
            _status(c)
            dash.port["open"] = True
            dash._CONN_CACHE["t"] = 0.0
            _expire_backoff(dash)
            j = _status(c)
            _settle(dash)
    assert len(dash.recovered) == 10
    assert j["reconnect"]["state"] == "gave_up" and j["reconnect"]["attempts"] == 10


def test_gave_up_resets_when_the_serial_changes(dash):
    with dash.app.test_client() as c:
        _give_up(dash, c)
        dash.cfg["instances"]["main"]["serial"] = "127.0.0.1:16416"
        _status(c)
        _settle(dash)
    assert dash.recovered[-1] == "127.0.0.1:16416"
    assert len(dash.recovered) == 11
    assert dash._RECONNECT["attempts"] == 1     # the count started over


def test_a_reset_during_an_attempt_drops_its_outcome(dash):
    # the attempt belongs to the state before the reset: its failure must not
    # count against the fresh start
    gate = threading.Event()
    dash.result["gate"] = gate
    with dash.app.test_client() as c:
        _status(c)
        dash._reconnect_reset()
        gate.set()
        _settle(dash)
    assert dash._RECONNECT["attempts"] == 0 and dash._RECONNECT["last_error"] == ""


def test_the_5_s_floor_holds_after_an_attempt_that_reads_device_but_the_poll_does_not(dash):
    # recover answers 'device' (no failure, no backoff) while the next poll
    # still reads 'offline': the next attempt still waits out the floor
    dash.result["state"] = "device"
    with dash.app.test_client() as c:
        _status(c)
        _settle(dash)
        j = _status(c)
        _settle(dash)
    assert dash.recovered == [SERIAL]
    assert j["reconnect"]["state"] == "trying" and 0 < j["reconnect"]["next_try_in"] <= 5


def test_the_poll_after_an_attempt_asks_the_adb_server_again(dash):
    # a down server's state is held for 10 s, but an attempt may have started
    # it: the poll right after the attempt must see the new state
    dash.state["now"] = "no_server"
    dash.result.update(after="device", state="device")
    with dash.app.test_client() as c:
        assert _status(c)["device_state"] == "no_server"
        _settle(dash)
        j = _status(c)
    assert (j["connected"], j["device_state"]) == (True, "device")


def test_a_down_server_is_started_for_a_serial_without_a_port(dash):
    # emulator-NNNN: no port to dial, but starting the server is its whole
    # recovery (the old frame fallback ran start-server for every serial)
    dash.cfg["instances"]["main"]["serial"] = "emulator-5554"
    dash.port["open"] = False
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="device")
    with dash.app.test_client() as c:
        _status(c)
        _settle(dash)
    assert dash.recovered == ["emulator-5554"]
    assert dash.spawned == [[ADB, "start-server"]]


def test_no_transport_is_touched_while_a_runner_owns_the_device(dash):
    # the manual reconnect refuses with 409 while runners live (a reconnect
    # can bounce the transport); the runner's capture.grab recovers its own
    _set_process_rows(dash, [{"pid": 1, "runner": "orchestrator", "cmdline": ""}])
    with dash.app.test_client() as c:
        j = _status(c)
        _settle(dash)
    assert dash.recovered == []
    assert j["reconnect"]["state"] == "idle"


def test_a_down_server_is_only_started_while_a_runner_lives(dash):
    # no server = no transport to bounce: start it, but connect nothing
    # (attempts=0) - the runner re-attaches its own device
    _set_process_rows(dash, [{"pid": 1, "runner": "orchestrator", "cmdline": ""}])
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="missing")
    with dash.app.test_client() as c:
        _status(c)
        _settle(dash)
    assert dash.spawned == [[ADB, "start-server"]]
    assert dash.recover_kw == [{"attempts": 0}]


def test_a_server_that_stays_down_for_a_runner_still_gives_up(dash):
    _set_process_rows(dash, [{"pid": 1, "runner": "orchestrator", "cmdline": ""}])
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="no_server")
    with dash.app.test_client() as c:
        _give_up(dash, c)


def test_a_server_start_that_raises_for_a_runner_is_still_a_failure(dash):
    _set_process_rows(dash, [{"pid": 1, "runner": "orchestrator", "cmdline": ""}])
    dash.state["now"] = "no_server"
    dash.result["raise"] = FileNotFoundError("adb.exe not found")
    with dash.app.test_client() as c:
        _status(c)
        _settle(dash)
    assert dash._RECONNECT["attempts"] == 1


def test_gave_up_resets_on_a_manual_reconnect(dash):
    from types import SimpleNamespace

    def connect(args, **kw):
        dash.state["now"] = "device"        # the transport the connect made is online
        return SimpleNamespace(stdout="connected to " + SERIAL, stderr="")

    dash._run = connect
    with dash.app.test_client() as c:
        _give_up(dash, c)
        assert c.post("/api/wizard/reconnect", json={}).get_json()["ok"] is True
        assert dash._RECONNECT["state"] == "idle" and dash._RECONNECT["attempts"] == 0
        dash.state["now"] = "offline"       # ...and drops again before the next poll
        _status(c)
        _settle(dash)
    assert len(dash.recovered) == 11


def test_a_down_server_is_started_by_recover_with_the_configured_adb(dash):
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="device")
    with dash.app.test_client() as c:
        _status(c)
        _settle(dash)
    assert dash.spawned == [[ADB, "start-server"]]


def test_a_recover_exception_is_a_failed_attempt_with_its_message(dash):
    dash.result["raise"] = FileNotFoundError("adb.exe not found")
    with dash.app.test_client() as c:
        _status(c)
        _settle(dash)
        j = _status(c)
    assert j["reconnect"]["attempts"] == 1
    assert "adb.exe not found" in j["reconnect"]["last_error"]


def test_a_failed_frame_starts_the_server_at_most_once_per_backoff_window(dash, monkeypatch):
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="no_server")
    monkeypatch.setattr(adbclient, "exec_out", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("no server")))
    dash._DISPLAY_CACHE[SERIAL] = (None, 1e18)
    with dash.app.test_client() as c:
        for _ in range(3):
            assert c.get("/api/frame.png").status_code == 502
            _settle(dash)
    assert dash.spawned == [[ADB, "start-server"]]
    assert dash.recovered == [SERIAL]


def test_a_failed_frame_hands_a_device_cached_as_online_to_the_recovery(dash, monkeypatch):
    # the device state is held for 10 s while it reads 'device'; a frame that
    # fails inside that window is evidence the held answer is stale
    dash.state["now"] = "device"
    monkeypatch.setattr(adbclient, "exec_out", lambda *a, **k: (_ for _ in ()).throw(ConnectionResetError("gone")))
    dash._DISPLAY_CACHE[SERIAL] = (None, 1e18)
    with dash.app.test_client() as c:
        assert _status(c)["device_state"] == "device"
        dash.state["now"] = "missing"
        assert c.get("/api/frame.png").status_code == 502
        _settle(dash)
    assert dash.recovered == [SERIAL]


@pytest.mark.parametrize("down", ["no_server", "unknown"])
def test_status_does_not_ask_a_down_or_wedged_server_on_every_poll(dash, monkeypatch, down):
    # a refused loopback connect takes ~2 s on Windows and a wedged server
    # never answers: get_state then waits out its whole 1 s timeout, so every
    # tab's 2.5 s poll would hold a request thread for 1 s. The answer is held
    # for the 10 s TTL like any other; a server that answers (offline,
    # missing...) is re-asked each poll because that costs nothing
    asked = []
    monkeypatch.setattr(adbclient, "get_state", lambda serial, timeout=1.0: asked.append(serial) or down)
    gate = threading.Event()
    dash.result["gate"] = gate      # the recovery stays in flight: it drops the cache when done
    with dash.app.test_client() as c:
        for _ in range(4):
            assert _status(c)["device_state"] == down
        gate.set()
        _settle(dash)
    assert asked == [SERIAL]


def test_gave_up_resets_on_an_adopt_of_the_same_serial(dash, monkeypatch):
    # adopt repoints the config at a serial a person chose: the count starts
    # over even when it is the serial the attempts already failed on
    from types import SimpleNamespace

    monkeypatch.setattr(dash, "save_config", lambda cfg: "backup")
    monkeypatch.setattr(dash, "_wiz_save", lambda name, data: data)
    monkeypatch.setattr(dash, "_mark_setup_complete", lambda why: None)
    monkeypatch.setattr(dash, "_run", lambda args, **kw: SimpleNamespace(stdout="connected to " + SERIAL, stderr=""))
    with dash.app.test_client() as c:
        _give_up(dash, c)
        assert c.post("/api/wizard/adopt", json={"serial": SERIAL}).get_json()["ok"] is True
        assert dash._RECONNECT["state"] == "idle" and dash._RECONNECT["attempts"] == 0
        _status(c)
        _settle(dash)
    assert len(dash.recovered) == 11


def _real_recover(dash, monkeypatch, **fast):
    """Run _reconnect_attempt against the real get_state / recover (the
    loopback fake a test starts), with `fast` settle/attempts merged in."""
    monkeypatch.setattr(adbclient, "get_state", _REAL_GET_STATE)
    monkeypatch.setattr(adbclient, "recover", lambda serial, start_server=None, **kw: _REAL_RECOVER(serial, start_server, **kw, **fast))
    adbclient._offline_since.clear()


def test_a_slow_handshake_finishes_across_successive_attempts(dash, monkeypatch):
    # end to end against real recover: a transport still handshaking when
    # one attempt ends must not be disconnected by the next one
    from test_adb_recover import FakeAdb

    fake = FakeAdb(monkeypatch, state="offline")
    fake.handshake = 1.2
    _real_recover(dash, monkeypatch, attempts=2, settle=0.3)
    # a stale transport: offline for the whole bound, so the first attempt tears it down once
    adbclient._offline_since[(adbclient._HOST, SERIAL)] = time.monotonic() - adbclient._OFFLINE_STALE_S
    try:
        with dash.app.test_client() as c:
            for _ in range(10):
                _expire_backoff(dash)
                j = _status(c)
                _settle(dash)
                if j["device_state"] == "device":
                    break
        assert j["device_state"] == "device" and j["reconnect"]["state"] == "idle"
        assert fake.count("host:disconnect:") == 1
    finally:
        fake.close()
        adbclient._offline_since.clear()


def test_a_down_server_is_started_through_the_dashboards_run_then_the_device_connected(dash, monkeypatch):
    from test_adb_recover import FakeAdb

    port = _dead_port()[1]
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", port))
    _real_recover(dash, monkeypatch, settle=0.5)
    started = []
    monkeypatch.setattr(dash, "_run", lambda args, **kw: (dash.spawned.append(list(args)),
                                                          started.append(FakeAdb(monkeypatch, port=port))))
    try:
        with dash.app.test_client() as c:
            assert _status(c)["device_state"] == "no_server"
            _settle(dash)
            j = _status(c)
        assert dash.spawned == [[ADB, "start-server"]] and len(started) == 1
        assert (j["device_state"], j["reconnect"]["state"]) == ("device", "idle")
        assert started[0].count("host:connect:") == 1
    finally:
        for srv in started:
            srv.close()
        adbclient._offline_since.clear()


# ------------------------------------------------ tick bookkeeping (STEP B2)
def test_a_config_without_an_adb_section_does_not_break_the_status_poll(dash):
    # other dashboard code reads (cfg.get('adb') or {}).get('exe'); the tick
    # used cfg['adb']['exe'] and raised on every poll. Attaching a device
    # needs no adb.exe
    del dash.cfg["adb"]
    with dash.app.test_client() as c:
        r = c.get("/api/status")
        _settle(dash)
    assert r.status_code == 200 and dash.recovered == [SERIAL]


def test_gave_up_on_a_failing_device_does_not_hold_when_the_server_later_dies(dash):
    # the give-up was reached with the server listening (device offline); a
    # server that dies afterwards is a different situation and gets its normal
    # attempt - the old tick returned on 'gave_up' before its no_server branch
    with dash.app.test_client() as c:
        _give_up(dash, c)
        dash.state["now"] = "no_server"
        dash.result.update(start=True, state="device")
        _expire_backoff(dash)
        _status(c)
        _settle(dash)
    assert len(dash.recovered) == 11
    assert dash.spawned == [[ADB, "start-server"]]


def test_gave_up_on_a_server_that_will_not_start_holds_while_it_stays_down(dash):
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="no_server")
    with dash.app.test_client() as c:
        _give_up(dash, c)
        for _ in range(3):
            _expire_backoff(dash)
            j = _status(c)
            _settle(dash)
    assert len(dash.recovered) == 10 and j["reconnect"]["state"] == "gave_up"


def test_gave_up_on_a_server_that_will_not_start_ends_when_the_server_answers_again(dash):
    # a person fixed the server by hand: the device it lists as missing is a new
    # situation, so the device gets its attempts
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="no_server")
    with dash.app.test_client() as c:
        _give_up(dash, c)
        dash.state["now"] = "missing"
        dash._CONN_CACHE["t"] = 0.0         # a down server's answer is held 10 s
        dash.result.update(start=False, state="device")
        _expire_backoff(dash)
        _status(c)
        _settle(dash)
    assert len(dash.recovered) == 11


def test_unauthorized_needs_the_person_so_no_attempt_starts_or_counts(dash):
    # recover returns 'unauthorized' untouched: attempts would only burn the
    # failure count (~375 s of 'trying') while the UI already shows the
    # instruction to allow the host at the emulator
    dash.state["now"] = "unauthorized"
    with dash.app.test_client() as c:
        for _ in range(3):
            _expire_backoff(dash)
            j = _status(c)
            _settle(dash)
    assert dash.recovered == []
    assert j["device_state"] == "unauthorized"
    assert j["reconnect"] == {"state": "idle", "attempts": 0, "next_try_in": None, "last_error": ""}


def test_the_reconnect_key_is_only_for_the_serial_being_reported(dash):
    # the config was switched to another instance while the old serial's
    # attempt is still running: its 'trying' record is not the new serial's
    gate = threading.Event()
    dash.result["gate"] = gate
    with dash.app.test_client() as c:
        assert _status(c)["reconnect"]["state"] == "trying"
        dash.cfg["instances"]["main"]["serial"] = "127.0.0.1:16416"
        j = _status(c)
        assert j["reconnect"] == {"state": "idle", "attempts": 0, "next_try_in": None, "last_error": ""}
        gate.set()
        _settle(dash)
        j = _status(c)
        _settle(dash)
    assert dash.recovered == [SERIAL, "127.0.0.1:16416"]
    # the old serial's failed attempt is not part of the new serial's count
    assert j["reconnect"]["state"] == "trying" and dash._RECONNECT["attempts"] == 1


# ------------------------------------------------- manual Reconnect (STEP B1)
def _manual(dash, reply):
    """Make the Reconnect button's `adb connect` answer `reply`."""
    from types import SimpleNamespace

    dash._run = lambda args, **kw: dash.spawned.append(list(args)) or SimpleNamespace(stdout=reply, stderr="")


def test_manual_reconnect_is_not_ok_when_the_connected_transport_still_reads_offline(dash, monkeypatch):
    # 'already connected' is adb's answer for a transport the server still
    # holds, offline or not: the message proves nothing about the device
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _manual(dash, "already connected to " + SERIAL)
    dash.state["now"] = "offline"
    with dash.app.test_client() as c:
        r = c.post("/api/wizard/reconnect", json={}).get_json()
    assert r["ok"] is False and r["device_state"] == "offline"
    assert "already connected" in r["message"] and "offline" in r["message"]


def test_manual_reconnect_waits_out_the_handshake_of_a_fresh_transport(dash, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _manual(dash, "connected to " + SERIAL)
    states = iter(["offline", "offline", "device"])
    monkeypatch.setattr(adbclient, "get_state", lambda serial, timeout=1.0: next(states))
    with dash.app.test_client() as c:
        r = c.post("/api/wizard/reconnect", json={}).get_json()
    assert (r["ok"], r["device_state"], r["message"]) == (True, "device", "connected to " + SERIAL)
    assert dash.spawned == [[ADB, "connect", SERIAL]]


def test_manual_reconnect_failing_to_connect_is_not_ok_and_keeps_adbs_message(dash, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _manual(dash, "cannot connect to " + SERIAL + ": refused")
    dash.state["now"] = "missing"
    with dash.app.test_client() as c:
        r = c.post("/api/wizard/reconnect", json={}).get_json()
    assert r["ok"] is False and r["message"].startswith("cannot connect to " + SERIAL + ": refused")


def test_manual_reconnect_is_refused_while_runners_live(dash):
    dash._procs = lambda: [{"pid": 1, "runner": "orchestrator", "cmdline": ""}]
    _manual(dash, "connected to " + SERIAL)
    with dash.app.test_client() as c:
        r = c.post("/api/wizard/reconnect", json={})
    assert r.status_code == 409 and dash.spawned == []


@pytest.mark.parametrize("serial", ["127.0.0.1:5555;reboot", "x\nhost:kill", "127.0.0.1:5555\n", "-a:1 --x", 5555])
def test_manual_reconnect_validates_a_posted_serial(dash, serial):
    # the serial goes to adb.exe's argv and the adb server: the same form
    # adopt and the screen check accept (host:port, emulator-NNNN)
    _manual(dash, "connected to " + str(serial))
    with dash.app.test_client() as c:
        r = c.post("/api/wizard/reconnect", json={"serial": serial})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert dash.spawned == []


def test_manual_reconnect_takes_a_well_formed_posted_serial_and_the_configured_one(dash, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _manual(dash, "connected to x")
    dash.state["now"] = "device"
    dash.cfg["instances"]["main"]["serial"] = "R58M12ABCDE"     # hand-written usb serial: this machine's own
    with dash.app.test_client() as c:
        assert c.post("/api/wizard/reconnect", json={"serial": "127.0.0.1:16416"}).get_json()["ok"] is True
        assert c.post("/api/wizard/reconnect", json={"serial": "R58M12ABCDE"}).get_json()["ok"] is True
        assert c.post("/api/wizard/reconnect", json={}).get_json()["ok"] is True
    assert dash.spawned == [[ADB, "connect", "127.0.0.1:16416"], [ADB, "connect", "R58M12ABCDE"], [ADB, "connect", "R58M12ABCDE"]]


def _handshaking_manual_connect(dash, fake):
    """The Reconnect button's `adb connect`, against the loopback server: the
    device attaches and reads 'offline' for the next 4 s (a slow handshake)."""
    from types import SimpleNamespace

    def connect(args, **kw):
        dash.spawned.append(list(args))
        fake.state = "device"
        fake.connected_at = time.monotonic()
        return SimpleNamespace(stdout="connected to " + SERIAL, stderr="")

    fake.handshake = 4.0
    dash._run = connect


def test_a_status_poll_does_not_tear_down_the_transport_a_manual_reconnect_made(dash, monkeypatch):
    from test_adb_recover import FakeAdb

    fake = FakeAdb(monkeypatch, state="missing")
    _real_recover(dash, monkeypatch, attempts=1, settle=0.3)
    _handshaking_manual_connect(dash, fake)
    try:
        with dash.app.test_client() as c:
            r = c.post("/api/wizard/reconnect", json={}).get_json()
            assert r["ok"] is False and r["device_state"] == "offline"      # still handshaking
            j = _status(c)
            _settle(dash)
        assert j["device_state"] == "offline"
        assert fake.count("host:connect:") == 1         # the poll's recover: already connected
        assert fake.count("host:disconnect:") == 0
    finally:
        fake.close()
        adbclient._offline_since.clear()


def test_a_poll_after_a_give_up_does_not_tear_down_the_manual_transport_either(dash, monkeypatch):
    # gave_up: no attempt reads the device any more, but every poll still asks
    # the server. A clock the last attempt left running must not outlive the
    # transport the poll then saw dropped ('missing'), or the fresh transport
    # of the person's Reconnect would be torn down on its first offline poll
    from test_adb_recover import FakeAdb

    fake = FakeAdb(monkeypatch, state="missing")
    _real_recover(dash, monkeypatch, attempts=1, settle=0.3)
    _handshaking_manual_connect(dash, fake)
    adbclient._offline_since[(adbclient._HOST, SERIAL)] = time.monotonic() - 2 * adbclient._OFFLINE_STALE_S
    dash._RECONNECT.update(state="gave_up", attempts=10, serial=SERIAL, next_at=0.0)
    try:
        with dash.app.test_client() as c:
            assert _status(c)["reconnect"]["state"] == "gave_up"
            assert fake.count("host:connect:") == 0
            c.post("/api/wizard/reconnect", json={})
            _status(c)
            _settle(dash)
        assert fake.count("host:disconnect:") == 0
    finally:
        fake.close()
        adbclient._offline_since.clear()


# --------------------------------- give-up flips (repair round 1 of STEP B2)
def test_a_give_up_whose_last_attempt_started_the_server_holds(dash):
    # ten attempts against a down server; the tenth starts it and the device
    # stays missing. The server now listening is that attempt's own doing, not
    # a change from outside: the give-up must hold
    dash.state["now"] = "no_server"
    dash.result.update(start=True, state="no_server")
    with dash.app.test_client() as c:
        for _ in range(9):
            _expire_backoff(dash)
            _status(c)
            _settle(dash)
        dash.result.update(state="missing", after="missing")
        _expire_backoff(dash)
        _status(c)
        _settle(dash)
        for _ in range(3):
            _expire_backoff(dash)
            j = _status(c)
            _settle(dash)
    assert len(dash.recovered) == 10
    assert j["reconnect"]["state"] == "gave_up" and j["reconnect"]["attempts"] == 10


def _server_that_dies_after_every_start(dash, c, rounds):
    """Each round: the server is down, a poll starts it (the stub leaves the
    device 'missing' on the running server), a second poll within the backoff
    sees that server answer, and then it dies again."""
    dash.result.update(start=True, state="missing", after="missing")
    for _ in range(rounds):
        dash.state["now"] = "no_server"
        dash._CONN_CACHE["t"] = 0.0
        _expire_backoff(dash)
        _status(c)
        _settle(dash)
        j = _status(c)
    return j


def test_a_server_that_dies_after_every_start_gets_a_bounded_number_of_starts(dash):
    # the first give-up gets one fresh set of attempts when the server dies
    # after it; the second is final until the device reads online or a person acts
    with dash.app.test_client() as c:
        j = _server_that_dies_after_every_start(dash, c, 60)
    assert len(dash.recovered) == 20 and len(dash.spawned) == 20
    assert j["reconnect"]["state"] == "gave_up" and j["reconnect"]["attempts"] == 10


@pytest.mark.parametrize("outage", ["runner_live", "port_closed"])
def test_a_server_that_dies_after_every_start_is_bounded_without_a_connect_too(dash, outage):
    # the server-only attempt (a runner live, or the emulator port closed) is
    # counted like any other: a start that leaves the server answering but
    # the device never `device` does not zero the count, so the same loop
    # gets the same 20 starts and the same final give-up as the connect path
    if outage == "runner_live":
        _set_process_rows(dash, [{"pid": 1, "runner": "orchestrator", "cmdline": ""}])
    else:
        dash.port["open"] = False
    with dash.app.test_client() as c:
        j = _server_that_dies_after_every_start(dash, c, 60)
    assert len(dash.recovered) == 20 and len(dash.spawned) == 20
    assert j["reconnect"]["state"] == "gave_up" and j["reconnect"]["attempts"] == 10
    assert dash.recover_kw == [{"attempts": 0}] * 20


def test_the_final_give_up_ends_with_the_device_online(dash):
    with dash.app.test_client() as c:
        _server_that_dies_after_every_start(dash, c, 60)
        assert len(dash.recovered) == 20
        dash.state["now"] = "device"
        dash._CONN_CACHE["t"] = 0.0
        assert _status(c)["reconnect"]["state"] == "idle"
        dash.result.update(after=None)
        dash.state["now"] = "no_server"         # a later outage is a new one
        dash._CONN_CACHE["t"] = 0.0
        _expire_backoff(dash)
        _status(c)
        _settle(dash)
    assert len(dash.recovered) == 21


def test_a_give_up_that_flips_still_waits_out_the_backoff(dash):
    # the next attempt starts no sooner than the last one's backoff allows,
    # also when the server's situation flipped: not on the very next poll
    with dash.app.test_client() as c:
        _give_up(dash, c)
        dash.state["now"] = "no_server"
        dash._CONN_CACHE["t"] = 0.0
        j = _status(c)
        _settle(dash)
    assert len(dash.recovered) == 10
    assert j["reconnect"]["state"] == "trying" and 50 < j["reconnect"]["next_try_in"] <= 60


def test_a_manual_reconnect_after_an_unobserved_server_drop_is_not_torn_down_by_the_next_poll(dash, monkeypatch):
    # no poll read 'missing' between the server dropping the old offline
    # transport and the person's Reconnect, so the old transport's clock is
    # still running (aged past the bound): the connect makes a NEW transport,
    # still handshaking, and the next poll's recover must leave it alone
    from test_adb_recover import FakeAdb

    fake = FakeAdb(monkeypatch, state="offline")
    _real_recover(dash, monkeypatch, attempts=1, settle=0.3)
    try:
        with dash.app.test_client() as c:
            _status(c)                          # a poll reads offline: the clock starts
            _settle(dash)
            adbclient._offline_since[(adbclient._HOST, SERIAL)] = time.monotonic() - 40
            dash._RECONNECT.update(state="gave_up", attempts=10, serial=SERIAL, next_at=0.0)
            fake.state = "missing"              # the server dropped it; nobody polled
            _handshaking_manual_connect(dash, fake)
            r = c.post("/api/wizard/reconnect", json={}).get_json()
            assert r["ok"] is False and r["device_state"] == "offline"
            _status(c)
            _settle(dash)
        assert fake.count("host:disconnect:") == 0
    finally:
        fake.close()
        adbclient._offline_since.clear()
