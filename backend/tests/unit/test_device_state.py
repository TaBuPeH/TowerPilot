"""Connection truth is the adb server's device state, not an open port.

Live 2026-10-12: the adb server listed `127.0.0.1:16384 offline` while
/api/status said connected:true - the old probe only dialled the serial's TCP
port. `adbclient.get_state` asks the server (one host query, no transport);
/api/status reports it as `device_state` and derives `connected` from it.
/api/template answered a missing per-account template with a 500.
"""

import paths as test_paths

import importlib.util
import os
import socket
import threading

import pytest

from device import adbclient

BACKEND = str(test_paths.BACKEND)


def _fake_server(monkeypatch, reply: bytes):
    """A one-shot local listener standing in for the adb server: records the
    request payload and answers `reply` verbatim."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = {}

    def serve():
        conn, _ = srv.accept()
        with conn:
            n = int(conn.recv(4), 16)
            got["payload"] = conn.recv(n).decode()
            conn.sendall(reply)
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    monkeypatch.setattr(adbclient, "_HOST", srv.getsockname())
    return got, t


def _okay(text: str) -> bytes:
    return b"OKAY" + b"%04x" % len(text) + text.encode()


def _fail(text: str) -> bytes:
    return b"FAIL" + b"%04x" % len(text) + text.encode()


@pytest.mark.parametrize(
    "reply,expected",
    [
        (_okay("device"), "device"),
        (_okay("offline"), "offline"),
        (_fail("device offline"), "offline"),
        (
            _fail(
                "device unauthorized.\nThis adb server's $ADB_VENDOR_KEYS is not set"
            ),
            "unauthorized",
        ),
        (_fail("device '127.0.0.1:16384' not found"), "missing"),
        (_fail("protocol fault"), "unknown"),
        (b"JUNK", "unknown"),
    ],
)
def test_get_state_sends_one_host_query_and_maps_the_reply(
    monkeypatch, reply, expected
):
    got, t = _fake_server(monkeypatch, reply)
    assert adbclient.get_state("127.0.0.1:16384") == expected
    t.join(2)
    assert got["payload"] == "host-serial:127.0.0.1:16384:get-state"


def test_get_state_without_a_server_or_serial(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", port))
    assert adbclient.get_state("127.0.0.1:16384") == "no_server"
    assert adbclient.get_state("") == "unconfigured"


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    # a non-'device' state with the port open starts the dashboard's
    # background recovery: stub it, and point any adbclient call a stub
    # misses at a dead loopback port - never the real adb server on 5037
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", s.getsockname())
    s.close()
    monkeypatch.setattr(adbclient, "recover", lambda serial, start_server=None, **kw: "offline")
    path = str(test_paths.DASHBOARD)
    spec = importlib.util.spec_from_file_location("tp_dashboard_devstate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "backend"
    (root / "templates").mkdir(parents=True)
    monkeypatch.setattr(mod, "ROOT", str(root))
    monkeypatch.setattr(
        mod,
        "load_config",
        lambda: {
            "active_instance": "main",
            "adb": {"exe": "adb"},
            "instances": {"main": {"serial": "127.0.0.1:16384"}},
        },
    )
    yield mod
    t = mod._RECONNECT["thread"]
    if t is not None:
        t.join(5)


def _status_stubs(dash, monkeypatch):
    import player.scan_plan

    monkeypatch.setattr(player.scan_plan, "control_gate", lambda root, cfg: {})
    monkeypatch.setattr(dash, "_procs_cached", lambda: [])
    monkeypatch.setattr(dash, "_setup_complete", lambda: True)
    monkeypatch.setattr(dash, "_calibrate_missing", lambda: [])
    monkeypatch.setattr(dash, "_port_open", lambda serial: True)


def test_status_reports_offline_device_as_not_connected_while_port_is_open(
    dash, monkeypatch
):
    _status_stubs(dash, monkeypatch)
    # the recovery thread drops the held answer when it ends: keep it out so
    # only _device_state's own re-query can show the second state
    monkeypatch.setattr(dash, "_reconnect_tick", lambda *a, **k: None)
    states = iter(["offline", "device"])
    monkeypatch.setattr(adbclient, "get_state", lambda serial: next(states))
    with dash.app.test_client() as c:
        j = c.get("/api/status").get_json()
        assert (j["connected"], j["device_state"], j["port_open"]) == (
            False,
            "offline",
            True,
        )
        # a non-'device' state is never held: the next poll asks again
        j = c.get("/api/status").get_json()
    assert (j["connected"], j["device_state"]) == (True, "device")


def test_status_holds_an_online_state_only_for_the_same_serial(dash, monkeypatch):
    # review R9: a config save or _adopt_placeholder changes the serial without
    # resetting the cache; the old serial's 'device' must not clear the Start
    # blocker for the new one
    _status_stubs(dash, monkeypatch)
    cfg = {
        "active_instance": "main",
        "adb": {"exe": "adb"},
        "instances": {"main": {"serial": "127.0.0.1:16384"}},
    }
    monkeypatch.setattr(dash, "load_config", lambda: cfg)
    asked = []
    online = {"127.0.0.1:16384"}
    monkeypatch.setattr(
        adbclient,
        "get_state",
        lambda serial: asked.append(serial) or ("device" if serial in online else "missing"),
    )
    with dash.app.test_client() as c:
        assert c.get("/api/status").get_json()["device_state"] == "device"
        c.get("/api/status")  # same serial, online, within 10 s: cached
        assert asked == ["127.0.0.1:16384"]
        cfg["instances"]["main"]["serial"] = "127.0.0.1:16416"
        j = c.get("/api/status").get_json()
    assert (j["connected"], j["device_state"]) == (False, "missing")
    assert asked == ["127.0.0.1:16384", "127.0.0.1:16416"]


def test_status_redials_the_port_only_per_serial_and_ttl(dash, monkeypatch):
    # a non-'device' state re-runs ONE get_state query per poll; the TCP dial
    # (0.3 s against a closed port on Windows) is not part of the Start gate
    # and stays on the 10 s / serial cache
    _status_stubs(dash, monkeypatch)
    dials = []
    monkeypatch.setattr(dash, "_port_open", lambda serial: dials.append(serial) or False)
    monkeypatch.setattr(adbclient, "get_state", lambda serial: "missing")
    with dash.app.test_client() as c:
        for _ in range(3):
            assert c.get("/api/status").get_json()["device_state"] == "missing"
    assert dials == ["127.0.0.1:16384"]


def test_template_missing_is_404_and_bad_name_is_403(dash):
    with dash.app.test_client() as c:
        r = c.get("/api/template/cards/preset_farm.png")
        assert r.status_code == 404
        assert r.get_data(as_text=True) == "template not captured yet"
        assert c.get("/api/template/flat.png").status_code == 403
        assert c.get("/api/template/cards/x.txt").status_code == 403
