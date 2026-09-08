"""The wall is a per-run REQUIREMENT, not a compile refusal (2026-09-08).

A rescue plan that watches the wall reads config rois.wall_bar; capture.roi
raises without it. So: the compiler accepts the binding (the starter's
`wall: false` is no knowledge), readiness greys the run out until the region
is configured, Full setup detects the wall bar from the battle HUD (the bar
renders only for an account that has a wall) and Apply writes the region.
"""
import copy
import glob
import importlib.util
import json
import os

import cv2
import numpy as np
import pytest
import yaml

from player import accounts, readiness

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYAN = (255, 255, 0)          # BGR: OpenCV hue 90 - inside the bar's teal band


def _cfg(wall_bar=None):
    cfg = {"active_instance": "main", "instances": {"main": {}}, "loadouts": {}}
    if wall_bar:
        cfg["instances"]["main"]["rois"] = {"wall_bar": wall_bar}
    return cfg


def _wall_coin():
    return {"kind": "coin", "loadout": None, "shopping": [], "gather": {},
            "abilities": {"rescue_bar": "wall"}}


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.random.default_rng(7).integers(0, 256, (20, 30, 3), dtype=np.uint8))


def test_wall_rescue_run_needs_the_wall_bar_region_not_an_image(tmp_path):
    body = _wall_coin()
    rows = {r["alternatives"][0]: r for r in readiness.requirements(_cfg(), body)}
    assert rows[readiness.WALL_BAR_REQUIREMENT]["blocking"] is True
    assert readiness.WALL_BAR_REQUIREMENT not in {
        r["alternatives"][0] for r in readiness.requirements(_cfg(), {"kind": "coin", "gather": {}})}
    for row in readiness.requirements(_cfg(), body):
        if row["blocking"] and row["alternatives"][0] != readiness.WALL_BAR_REQUIREMENT:
            _image(accounts.template_path(tmp_path, _cfg(), row["alternatives"][0], write=True))
    result = readiness.check(tmp_path, _cfg(), body)
    assert not result["ready"]
    assert [r["alternatives"] for r in result["missing"]] == [[readiness.WALL_BAR_REQUIREMENT]]
    assert readiness.check(tmp_path, _cfg([84, 1559, 424, 44]), body)["ready"]
    # the global block counts too
    cfg = _cfg(); cfg["rois"] = {"wall_bar": [84, 1559, 424, 44]}
    assert readiness.check(tmp_path, cfg, body)["ready"]


def test_compiler_accepts_a_wall_rescue_without_a_confirmed_wall_and_advises():
    from goldens import load_golden
    from player import playerprofile as pp
    prof = copy.deepcopy(load_golden())
    prof["player"]["wall"] = False
    bound = [n for n, b in prof["blueprints"].items()
             if (b.get("policies") or {}).get("rescue") == "high_tier_wall"]
    assert bound, "golden binds high_tier_wall somewhere"
    assert not [p for p in pp.validate(prof) if "wall" in p.lower()]
    advice = [w for w in pp.warnings(prof) if "watches the wall" in w]
    assert advice and "rois.wall_bar" in advice[0]
    prof["player"]["wall"] = True
    assert not [w for w in pp.warnings(prof) if "watches the wall" in w]


def _hud(wall=True, hp=True):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    for on, (x, y, w, h) in ((hp, (34, 1696, 478, 64)), (wall, (84, 1559, 424, 44))):
        if on:
            frame[y:y + h, x:x + int(w * 0.7)] = CYAN      # 70% filled bar
    return frame


def test_hud_wall_detection_is_positive_only():
    from player import flow_capture as fc
    assert fc.wall_bar_present(_hud())
    assert not fc.wall_bar_present(_hud(wall=False))         # HUD without a wall
    assert not fc.wall_bar_present(_hud(hp=False))           # no HUD at all
    assert not fc.wall_bar_present(np.zeros((2560, 1080, 3), np.uint8))
    assert not fc.wall_bar_present(np.zeros((1080, 600, 3), np.uint8))   # wrong size

    class Cal:
        player = {}

    class Flow:
        cal = Cal()

    flow = Flow()
    assert fc._record_wall(flow, _hud(wall=False)) is False
    assert flow.cal.player == {}                             # nothing recorded
    assert fc._record_wall(flow, _hud()) is True
    assert flow.cal.player == {"wall": True, "wall_verified_by": "setup",
                               "wall_bar": list(fc.WALL_BAR_ROI)}
    assert fc._record_wall(object(), _hud()) is False        # no calibration: no-op


@pytest.fixture(scope="module")
def dash():
    path = os.path.join(os.path.dirname(BACKEND), "frontend", "dashboard.py")
    spec = importlib.util.spec_from_file_location("tp_dashboard_wall", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_apply_writes_the_wall_region_to_config_and_the_wall_to_the_profile(dash, tmp_path, monkeypatch):
    name = "zz_wallapply"
    src = os.path.join(BACKEND, "tests", "fixtures", "golden_profile.yaml")
    dst = os.path.join(BACKEND, "profiles", f"{name}.yaml")
    with open(src, encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    profile["player"]["wall"] = False
    with open(dst, "w", encoding="utf-8") as fh:
        yaml.safe_dump(profile, fh, sort_keys=False)
    state = {"phases": {}, "entries": [],
             "player": {"wall": True, "wall_verified_by": "setup", "wall_bar": [84, 1559, 424, 44]}}
    (tmp_path / "calibrate_state.json").write_text(json.dumps(state))
    cfg = {"active_profile": name, "active_instance": "main", "instances": {"main": {}},
           "loadouts": {k: {} for k in ("coin_farm", "tourney_1", "dissonance",
                                        "shard_farm", "inner_land_mines_quest")}}
    saved = []
    monkeypatch.setattr(dash, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(dash, "_procs", lambda: [])
    monkeypatch.setattr(dash, "_calibration_dir", lambda cfg: str(tmp_path))
    monkeypatch.setattr(dash, "save_config", lambda data: saved.append(copy.deepcopy(data)) or "bak")
    try:
        with dash.app.test_client() as c:
            r = c.post("/api/calibrate/apply", json={})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["wall_bar"] == [84, 1559, 424, 44]
        with open(dst, encoding="utf-8") as fh:
            after = yaml.safe_load(fh)["player"]
        assert after["wall"] is True and after["wall_verified_by"] == "setup"
        assert "wall_bar" not in after                       # config, not profile data
        assert saved and saved[0]["instances"]["main"]["rois"]["wall_bar"] == [84, 1559, 424, 44]
        # already configured: the config is left alone
        saved.clear()
        cfg["instances"]["main"]["rois"] = {"wall_bar": [1, 2, 3, 4]}
        with dash.app.test_client() as c:
            assert c.post("/api/calibrate/apply", json={}).status_code == 200
        assert saved == []
    finally:
        for f in glob.glob(os.path.join(BACKEND, "profiles", f"{name}.yaml*")):
            os.remove(f)
