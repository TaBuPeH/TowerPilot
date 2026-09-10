"""Image-free setup: coverage, isolation and no-input startup guards."""
import cv2
import numpy as np
from player import accounts, scan_plan


def test_plan_covers_every_reference_once_with_acquisition_instructions(tmp_path):
    plan = scan_plan.plan(tmp_path, {})
    rows = [r for step in plan["steps"] for r in step["targets"]]
    assert {r["rel"] for r in rows} == accounts.generic_names()
    assert len(rows) == len({r["rel"] for r in rows})
    assert plan["captured"] == 0
    assert all(step["instructions"] and step["mode"] for step in plan["steps"])
    assert all(row["status"] == "Missing" for row in rows)
    assert scan_plan.missing_navigation(tmp_path, {}, ["c","m"])


def test_risky_targets_only_request_observation():
    for name in ("tourney/buy_ticket_title.png", "tourney/ticket_claim.png"):
        assert scan_plan.step_for(name) == "tournament"
    for name in ("modules/transfer_yes.png", "modules/shatter_dialog.png", "home/end_round_yes.png"):
        assert scan_plan.step_for(name) == "results"


def test_plan_distinguishes_saved_capture_from_verified_recognition(tmp_path):
    path = accounts.template_path(tmp_path, {}, "home/battle_btn.png", write=True)
    path.parent.mkdir(parents=True)
    cv2.imwrite(str(path), np.random.default_rng(9).integers(0,256,(20,40,3),dtype=np.uint8))
    plan = scan_plan.plan(tmp_path, {}, [{"alternatives":["home/battle_btn.png"]}])
    assert plan["captured"] == 1
    row = next(r for step in plan["steps"] for r in step["targets"] if r["rel"] == "home/battle_btn.png")
    assert row["required"] and "not yet verified" in row["status"]
    path.write_bytes(b"broken")
    assert scan_plan.plan(tmp_path,{})["captured"] == 0


def test_dashboard_plan_loads_without_images_and_automatic_scan_is_blocked(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    source = Path(__file__).resolve().parents[2] / "frontend/dashboard.py"
    spec = importlib.util.spec_from_file_location("image_free_dashboard", source)
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    monkeypatch.setattr(dash, "ROOT", str(tmp_path))
    monkeypatch.setattr(dash, "load_config", lambda: {"active_instance":"main", "instances":{"main":{}}, "presets":{}})
    monkeypatch.setattr(dash, "_procs", lambda: [])
    monkeypatch.setattr(dash.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Must not start a worker")))
    with dash.app.test_client() as client:
        response = client.get("/api/wizard/scan-plan")
        assert response.status_code == 200
        assert response.json["captured"] == 0 and response.json["total"] == len(accounts.generic_names())
        response = client.post("/api/calibrate/start", json={"phases":"c,m", "allow_navigation":True})
        assert response.status_code == 409 and response.json["missing"]


def test_control_requires_completed_scan_and_current_images(tmp_path):
    import json
    cfg = {"active_instance":"main"}
    assert not scan_plan.control_gate(tmp_path,cfg)["ready"]
    folder = accounts.calibration_dir(tmp_path,cfg)
    folder.mkdir(parents=True)
    state = folder / "calibrate_state.json"
    state.write_text(json.dumps({"phases":{"cards":{"status":"done"}}}))
    assert not scan_plan.control_gate(tmp_path,cfg)["ready"]
    for rel in scan_plan.missing_navigation(tmp_path,cfg,[],include_wave=False):
        path = accounts.template_path(tmp_path,cfg,rel,write=True)
        path.parent.mkdir(parents=True,exist_ok=True)
        cv2.imwrite(str(path),np.random.default_rng(32).integers(0,256,(20,30,3),dtype=np.uint8))
    assert scan_plan.control_gate(tmp_path,cfg)["ready"]
    assert "digits/0.png" in scan_plan.missing_navigation(tmp_path,cfg,[])
    state.write_text(json.dumps({"phases":{"cards":{"status":"running"}}}))
    assert not scan_plan.control_gate(tmp_path,cfg)["ready"]
    state.write_text(json.dumps({"phases":{"cards":{"status":"done"}}}))
    (folder / "module_restore.json").write_text("{}")
    assert not scan_plan.control_gate(tmp_path,cfg)["ready"]


def test_boot_finishes_without_waiting_for_missing_game_images(tmp_path, monkeypatch):
    import sys
    import settings
    from device import boot
    from runtime import logger
    events = []
    monkeypatch.setattr(sys,"argv",["boot.py","--instance","main"])
    monkeypatch.setattr(settings,"bind_device",lambda *a:None)
    monkeypatch.setattr(settings,"instance",lambda:{"serial":"test"})
    monkeypatch.setattr(settings,"ROOT",tmp_path)
    monkeypatch.setattr(settings,"CONFIG",{"active_instance":"main"})
    monkeypatch.setattr(boot.adbclient,"reconnect",lambda *a:True)
    monkeypatch.setattr(boot.adbclient,"alive",lambda *a:True)
    monkeypatch.setattr(boot.adbclient,"shell",lambda *a,**k:b"1")
    monkeypatch.setattr(boot.overlays,"clean",lambda:True)
    monkeypatch.setattr(boot.overlays,"windows",lambda *a:[boot.overlays.GAME_PKG])
    monkeypatch.setattr(boot,"_wait",lambda timeout,fn:fn())
    monkeypatch.setattr(logger,"event",lambda kind,**kw:events.append(dict(kind=kind,**kw)))
    assert boot.main() == 0
    assert events[-1]["kind"] == "boot_done"
    assert events[-1]["calibration_required"] and events[-1]["ok"]
    assert not any(e.get("stage") == "screen" for e in events)
