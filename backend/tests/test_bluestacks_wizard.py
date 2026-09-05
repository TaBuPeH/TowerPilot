"""The BlueStacks side of the Setup wizard: preparing bluestacks.conf (ADB
switch + display) only while the player is closed, with a backup, and the
instance rows reporting what the conf says."""
import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

CONF = '''bst.enable_adb_access="0"
bst.enable_adb_remote_access="0"
bst.instance.Pie64.adb_port="5555"
bst.instance.Pie64.custom_resolution_selected="0"
bst.instance.Pie64.display_name="BlueStacks App Player"
bst.instance.Pie64.dpi="320"
bst.instance.Pie64.fb_height="1440"
bst.instance.Pie64.fb_width="3440"
bst.instance.Other.adb_port="5565"
bst.instance.Other.display_name="Second"
bst.instance.Other.dpi="240"
bst.instance.Other.fb_height="1080"
bst.instance.Other.fb_width="1920"
'''


@pytest.fixture()
def dash():
    path = BACKEND.parent / "frontend" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("tp_dashboard_bs", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conf(dash, tmp_path, monkeypatch):
    path = tmp_path / "bluestacks.conf"
    path.write_text(CONF, encoding="utf-8")
    monkeypatch.setattr(dash, "_bluestacks_conf_path", lambda: str(path))
    # no HD-Player is running in the test process' view
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    return path


def _keys(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_prepare_sets_adb_and_display_for_one_instance_with_a_backup(dash, conf):
    r = dash._prepare_bluestacks("Pie64")
    assert set(r["changed"]) == {"bst.enable_adb_access",
                                 "bst.instance.Pie64.custom_resolution_selected",
                                 "bst.instance.Pie64.dpi",
                                 "bst.instance.Pie64.fb_height",
                                 "bst.instance.Pie64.fb_width"}
    assert r["added"] == []
    k = _keys(conf.read_text(encoding="utf-8"))
    assert k["bst.enable_adb_access"] == '"1"'
    assert k["bst.instance.Pie64.fb_width"] == '"2560"'
    assert k["bst.instance.Pie64.fb_height"] == '"1080"'
    assert k["bst.instance.Pie64.dpi"] == '"360"'
    assert k["bst.instance.Pie64.custom_resolution_selected"] == '"1"'
    # the other instance and unrelated keys are untouched
    assert k["bst.instance.Other.fb_width"] == '"1920"'
    assert k["bst.enable_adb_remote_access"] == '"0"'
    assert Path(r["backup"]).read_text(encoding="utf-8") == CONF
    # idempotent: a second call changes nothing and writes no backup
    r2 = dash._prepare_bluestacks("Pie64")
    assert r2["changed"] == {} and r2["backup"] is None


def test_prepare_appends_a_missing_key(dash, conf):
    text = conf.read_text(encoding="utf-8").replace(
        'bst.instance.Pie64.custom_resolution_selected="0"\n', "")
    conf.write_text(text, encoding="utf-8")
    r = dash._prepare_bluestacks("Pie64")
    assert r["added"] == ["bst.instance.Pie64.custom_resolution_selected"]
    assert 'bst.instance.Pie64.custom_resolution_selected="1"' in conf.read_text(encoding="utf-8")


def test_prepare_refuses_while_a_player_runs_and_unknown_instances(dash, conf, monkeypatch):
    import psutil

    class _P:
        info = {"name": "HD-Player.exe"}

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter((_P(),)))
    with pytest.raises(RuntimeError, match="running"):
        dash._prepare_bluestacks("Pie64")
    assert _keys(conf.read_text(encoding="utf-8"))["bst.enable_adb_access"] == '"0"'
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    with pytest.raises(ValueError):
        dash._prepare_bluestacks("Nope")
    with pytest.raises(ValueError):
        dash._prepare_bluestacks("../x")


def test_prepare_endpoint_maps_errors(dash, conf, monkeypatch):
    with dash.app.test_client() as c:
        r = c.post("/api/wizard/bluestacks/prepare", json={"index": "Pie64"})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        assert c.post("/api/wizard/bluestacks/prepare",
                      json={"index": "Nope"}).status_code == 400
    import psutil

    class _P:
        info = {"name": "HD-Player.exe"}

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter((_P(),)))
    with dash.app.test_client() as c:
        assert c.post("/api/wizard/bluestacks/prepare",
                      json={"index": "Pie64"}).status_code == 409


def test_resolution_check_reads_the_captured_frame_not_the_panel(dash, tmp_path, monkeypatch):
    """BlueStacks keeps a 2560x1080 landscape panel and rotates it for The
    Tower: `wm size` says 2560x1080 while screencap delivers 1080x2560. The
    check must judge the frame the vision layer gets (seen 2026-09-05)."""
    import struct
    from device import adbclient

    monkeypatch.setattr(dash, "_WIZ_STATE", str(tmp_path / "wizard_state.json"))
    monkeypatch.setattr(dash, "_SETUP_FLAG", str(tmp_path / "setup_done"))
    monkeypatch.setitem(dash._SETUP_CACHE, "done", False)
    frames = {"rotated": struct.pack("<III", 1080, 2560, 1) + b"\0" * 16,
              "panel": struct.pack("<III", 2560, 1080, 1) + b"\0" * 16}
    current, calls = ["rotated"], []

    def fake_exec_out(serial, command, timeout=None):
        calls.append(command)
        return frames[current[0]]

    monkeypatch.setattr(adbclient, "exec_out", fake_exec_out)
    with dash.app.test_client() as c:
        r = c.get("/api/wizard/resolution?serial=127.0.0.1:5555").get_json()
        assert (r["width"], r["height"], r["expected"]) == (1080, 2560, True)
        assert os.path.exists(tmp_path / "setup_done")
        current[0] = "panel"
        r = c.get("/api/wizard/resolution?serial=127.0.0.1:5555").get_json()
        assert (r["width"], r["height"], r["expected"]) == (2560, 1080, False)
        assert "2560x1080 landscape" in r["note"]
    assert calls and all(cmd == "screencap" for cmd in calls)


def test_ui_offers_prepare_for_bluestacks():
    html = (BACKEND.parent / "frontend" / "webui" / "index.html").read_text(encoding="utf-8")
    for needle in ("/api/wizard/bluestacks/prepare", "async function prepareBS",
                   "ADB switch off in BlueStacks", "needs 2560x1080@360"):
        assert needle in html, needle
