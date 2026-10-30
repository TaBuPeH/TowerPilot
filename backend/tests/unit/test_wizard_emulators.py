"""/api/wizard/emulators step-3 suggestion: one MuMu VM shows up in the adb
daemon twice (`127.0.0.1:16384` and `emulator-5554`), and must still count as
ONE candidate so the auto-suggestion fires; two real VMs must not."""

import paths as test_paths

import importlib.util
import socket
from pathlib import Path

import pytest

BACKEND = test_paths.BACKEND


class _Done:
    def __init__(self, stdout: str):
        self.stdout = stdout


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    path = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location("tp_dashboard_emu", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_WIZ_STATE", str(tmp_path / "wizard_state.json"))
    # the configured adb is the only one probed; "adb" skips the on-disk check
    monkeypatch.setattr(mod, "EMULATOR_ADB_CANDIDATES", [("MuMu Player", "adb")])
    monkeypatch.setattr(
        mod,
        "load_config",
        lambda: {
            "adb": {"exe": "adb"},
            "active_instance": "main",
            "instances": {"main": {"serial": "127.0.0.1:16480"}},
        },
    )
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))

    # the configured device is down: its port refuses
    def refuse(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    # ...and the adb server does not list it (no real server is contacted)
    from device import adbclient

    monkeypatch.setattr(adbclient, "get_state", lambda serial, timeout=1.0: "missing")
    return mod


def _configure(dash, monkeypatch, serial, state):
    """Point the fake config at `serial` and make the adb server report
    `state` for it ("missing" for any other serial)."""
    from device import adbclient

    monkeypatch.setattr(
        dash,
        "load_config",
        lambda: {
            "adb": {"exe": "adb"},
            "active_instance": "main",
            "instances": {"main": {"serial": serial}},
        },
    )
    monkeypatch.setattr(
        adbclient,
        "get_state",
        lambda s, timeout=1.0: state if s == serial else "missing",
    )


def _scan(dash, monkeypatch, devices, ports, no_server_first=False):
    """GET /api/wizard/emulators with the adb server listing `devices` (its
    socket host:devices answer, adbclient.devices) and the manager inventory
    running one VM per port in `ports`. Records every spawn in dash.spawned;
    `no_server_first`: the first devices() finds no server listening."""
    from device import adbclient

    asked = []

    def listed(timeout=5.0):
        asked.append(1)
        if no_server_first and len(asked) == 1:
            raise ConnectionRefusedError("no adb server")
        return list(devices)

    monkeypatch.setattr(adbclient, "devices", listed)
    dash.spawned = []
    monkeypatch.setattr(dash, "_run", lambda args, **k: dash.spawned.append(list(args)) or _Done(""))
    monkeypatch.setattr(
        dash,
        "_emulator_instances",
        lambda: [
            {
                "emulator": "MuMu",
                "index": i,
                "name": f"vm{i}",
                "running": True,
                "adb_port": p,
                "port_source": "manager",
            }
            for i, p in enumerate(ports)
        ],
    )
    with dash.app.test_client() as c:
        return c.get("/api/wizard/emulators").get_json()


def test_one_mumu_vm_listed_twice_by_adb_is_one_suggestion(dash, monkeypatch):
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384", "emulator-5554"], [16384])
    assert d["suggestion"] is not None
    assert d["suggestion"]["serial"] == "127.0.0.1:16384"
    assert d["suggestion"]["also_seen"] == ["emulator-5554"]
    # the daemon's own listing is reported untouched
    assert d["scanned"][0]["devices"] == ["127.0.0.1:16384", "emulator-5554"]
    # read over the server socket: no `adb devices` process (review R16)
    assert dash.spawned == []


def test_no_server_on_a_fresh_machine_is_started_once_with_the_configured_adb_then_listed(dash, monkeypatch):
    # no serial configured yet: nothing to detach, and the page needs the list
    _configure(dash, monkeypatch, "", "unconfigured")
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384"], [16384], no_server_first=True)
    assert dash.spawned == [["adb", "start-server"]]
    assert d["scanned"][0]["devices"] == ["127.0.0.1:16384"]


def test_no_server_with_a_configured_serial_is_not_started_by_the_scan(dash, monkeypatch):
    # live 2026-10-12 16:39:33: the scan's bare start-server left the configured
    # TCP serial 127.0.0.1:16384 detached - a cold server never re-attaches it.
    # The status poll's _reconnect_tick (adbclient.recover) starts the server
    # AND connects the device, so the scan leaves a configured machine to it
    _configure(dash, monkeypatch, "127.0.0.1:16384", "missing")
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384"], [16384], no_server_first=True)
    assert dash.spawned == []
    assert d["scanned"] == []


def test_a_listing_that_times_out_does_not_start_a_server(dash, monkeypatch):
    # a slow or wedged server is listening: start-server there can kill it
    # (an adb client of another version kills the server it finds). Only a
    # REFUSED connect means no server; the adb is skipped, nothing spawned.
    from device import adbclient

    def timed_out(timeout=5.0):
        raise TimeoutError("timed out")

    _scan(dash, monkeypatch, [], [])    # stubs the spawn recorder and the inventory
    monkeypatch.setattr(adbclient, "devices", timed_out)
    dash.spawned.clear()
    with dash.app.test_client() as c:
        d = c.get("/api/wizard/emulators").get_json()
    assert dash.spawned == []
    assert d["scanned"] == []


def test_fold_does_not_depend_on_the_probing_adb_binary(dash, monkeypatch):
    # the daemon is shared: the label of the adb that ran `devices` (here the
    # shipped PATH row) says nothing about which emulator owns a device
    monkeypatch.setattr(dash, "EMULATOR_ADB_CANDIDATES", [("PATH adb", "adb")])
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384", "emulator-5554"], [16384])
    assert d["suggestion"] is not None
    assert d["suggestion"]["serial"] == "127.0.0.1:16384"
    assert d["suggestion"]["also_seen"] == ["emulator-5554"]


def test_two_mumu_vms_get_no_suggestion(dash, monkeypatch):
    d = _scan(
        dash,
        monkeypatch,
        ["127.0.0.1:16384", "127.0.0.1:16416", "emulator-5554", "emulator-5556"],
        [16384, 16416],
    )
    assert d["suggestion"] is None


def test_more_emulator_tokens_than_inventory_vms_get_no_suggestion(dash, monkeypatch):
    # the manager knows one VM but the daemon holds two emulator transports:
    # the extra one cannot be accounted for, so nothing is auto-picked
    d = _scan(
        dash,
        monkeypatch,
        ["127.0.0.1:16384", "emulator-5554", "emulator-5556"],
        [16384],
    )
    assert d["suggestion"] is None


def test_lone_emulator_token_without_inventory_is_still_suggested(dash, monkeypatch):
    # an AVD-only machine: no manager inventory, the token is the one device
    d = _scan(dash, monkeypatch, ["emulator-5554"], [])
    assert d["suggestion"]["serial"] == "emulator-5554"
    assert "also_seen" not in d["suggestion"]


def test_live_configured_emulator_token_gets_no_switch_suggestion(dash, monkeypatch):
    # review R8 case C: a configured `emulator-5554` has no port to probe; the
    # adb server lists it online, so nothing may suggest re-keying the
    # instance to the same VM's `127.0.0.1:16384` (a different calibration_dir)
    _configure(dash, monkeypatch, "emulator-5554", "device")
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384", "emulator-5554"], [16384])
    assert d["suggestion"] is None


def test_live_configured_emulator_token_alone_gets_no_switch_suggestion(dash, monkeypatch):
    # review R8 case C in isolation: the daemon lists only the VM's
    # `127.0.0.1:16384` (the configured token is not listed, so it is not a
    # second candidate), and the configured `emulator-5554` reads `device` at
    # the adb server - the one-candidate rule alone would suggest the re-key.
    # Only the server's state for the configured serial (it has no port to
    # probe) holds the suggestion back.
    _configure(dash, monkeypatch, "emulator-5554", "device")
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384"], [16384])
    assert d["suggestion"] is None


def test_token_not_backed_by_a_listed_host_port_is_not_folded(dash, monkeypatch):
    # review R8 case A: the manager knows one running VM but the daemon lists
    # only a token, not that VM's host:port serial - the token cannot be
    # attributed to the VM, so both stay candidates and nothing is auto-picked
    d = _scan(dash, monkeypatch, ["emulator-5554"], [16384])
    assert d["suggestion"] is None


def test_offline_configured_vm_still_blocks_a_second_vm_suggestion(dash, monkeypatch):
    # the configured VM (16480) is up but offline at the adb server while a
    # second VM (another account) is online: the configured VM still counts
    # as a candidate, so the other account is never suggested
    _configure(dash, monkeypatch, "127.0.0.1:16480", "offline")
    d = _scan(dash, monkeypatch, ["127.0.0.1:16384"], [16384, 16480])
    assert d["suggestion"] is None
