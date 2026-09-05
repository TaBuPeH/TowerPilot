"""A fresh checkout must bootstrap itself, and the two human-driven writers
the release adds (the template cropper, draft promotion) must stay inside
their folders and never replace a file unasked."""
import copy
import importlib.util
import os
import shutil
from pathlib import Path

import numpy as np
import cv2
import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture()
def dash():
    path = BACKEND.parent / "frontend" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("tp_dashboard_rb", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ config seeding
def test_settings_seeds_config_from_the_example(tmp_path):
    import settings
    example = tmp_path / "config.example.yaml"
    example.write_text("adb: {exe: adb}\ninstances: {main: {serial: ''}}\n",
                       encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    assert settings.seed_config(cfg, example) is True
    assert cfg.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")
    # a second call leaves the (possibly edited) file alone
    cfg.write_text("edited: true\n", encoding="utf-8")
    assert settings.seed_config(cfg, example) is False
    assert cfg.read_text(encoding="utf-8") == "edited: true\n"


def test_settings_refuses_when_both_files_are_missing(tmp_path):
    import settings
    with pytest.raises(FileNotFoundError):
        settings.seed_config(tmp_path / "config.yaml", tmp_path / "nope.yaml")


def test_shipped_example_config_is_generic_and_read_only():
    cfg = yaml.safe_load((BACKEND / "config.example.yaml").read_text(encoding="utf-8"))
    inst = cfg["instances"][cfg["active_instance"]]
    assert inst["serial"] == ""
    assert inst["allow_taps"] is False
    assert "display" not in inst and "input_display" not in inst
    assert cfg["screen"] == {"width": 1080, "height": 2560}
    assert cfg["active_profile"] == "default"


def test_dashboard_seeds_config_too(dash, tmp_path, monkeypatch):
    example = tmp_path / "config.example.yaml"
    example.write_text("adb: {exe: adb}\ninstances: {main: {serial: ''}}\n"
                       "active_instance: main\n", encoding="utf-8")
    monkeypatch.setattr(dash, "CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(dash, "CONFIG_EXAMPLE", str(example))
    assert dash.load_config()["active_instance"] == "main"
    assert (tmp_path / "config.yaml").exists()


def test_empty_serial_is_refused_before_touching_the_adb_server():
    from device import adbclient
    with pytest.raises(ConnectionError, match="Setup wizard"):
        adbclient.exec_out("", "wm size")


# ------------------------------------------------------- shipped starter
def test_shipped_starter_profile_validates_and_binds_no_rescue():
    from player import playerprofile as pp
    prof = pp.load("default")
    assert pp.validate(prof) == []
    assert prof["player"]["abilities_verified"] is False
    for name, bp in prof["blueprints"].items():
        assert "rescue" not in (bp.get("policies") or {}), name


# ------------------------------------------------------------- template path
@pytest.mark.parametrize("rel", ["x.png", "../x.png", "modules/../../x.png",
                                 "modules/a.txt", "/modules/a.png", "c:/a/b.png"])
def test_template_path_rejects_anything_outside_a_subfolder(dash, rel):
    assert dash._template_path(rel) is None


def test_template_path_accepts_a_plain_subfolder_name(dash):
    p = dash._template_path("modules/some_module.png")
    assert p and p.endswith(os.path.join("templates", "modules", "some_module.png"))


# ------------------------------------------------------- required templates
def test_required_templates_come_from_loadouts_and_the_scanned_player(dash):
    cfg = {"loadouts": {
        "coin": {"global_preset": "Farm Run"},
        "shard": {"cards": "18v300", "module_preset": "Tourney",
                  "cards_restore": "main_farm"},
        "ilm": {"modules": [["space_displacer", "primary"]],
                "modules_restore": [["sharp_fortitude", "primary"]]},
        "off": {"defined": False}}}
    prof = {"player": {"card_presets": ["disco"], "global_presets": ["Tournament"],
                       "category_presets": {"bots": ["Farm"]},
                       "modules_equipped": ["zz_not_cut"]}}
    rows = {r["rel"]: r for r in dash._required_templates(cfg, prof)}
    assert rows["presets/gp_farm_run.png"]["used_by"] == ["loadout coin"]
    assert rows["cards/preset_18v300.png"]["have"] is True
    assert rows["cards/preset_main_farm.png"]["feature"] == "card preset tab"
    assert rows["presets/modules_tourney.png"]["have"] is True
    assert rows["modules/space_displacer.png"]["have"] is True
    assert rows["modules/equipped/sharp_fortitude.png"]["have"] is True
    assert rows["presets/gp_tournament.png"]["used_by"] == ["scanned account"]
    assert rows["presets/bots_farm.png"]["have"] is True
    assert rows["modules/zz_not_cut.png"]["have"] is False
    assert rows["modules/equipped/zz_not_cut.png"]["have"] is False
    assert rows["presets/picker_icon.png"]["have"] is True
    assert not any(r.startswith("off") for r in rows)


# ---------------------------------------------------------------- cropper
def _frame_png(w=300, h=200) -> bytes:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (40, 40, 40)
    cv2.rectangle(img, (50, 60), (110, 100), (0, 200, 255), -1)   # one unique blob
    cv2.circle(img, (220, 140), 20, (255, 255, 255), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


@pytest.fixture()
def tpl_root(dash, tmp_path, monkeypatch):
    root = tmp_path / "backend"
    (root / "templates").mkdir(parents=True)
    monkeypatch.setattr(dash, "ROOT", str(root))
    return root


def test_cropper_cuts_from_the_cached_frame_and_never_overwrites_unasked(dash, tpl_root):
    dash._remember_frame("t1", _frame_png())
    body = {"ts": "t1", "x": 40, "y": 50, "w": 60, "h": 40}   # blob edge inside the box
    with dash.app.test_client() as c:
        r = c.post("/api/template/unit/blob.png", json=body)
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        assert (j["width"], j["height"]) == (60, 40)
        assert j["second_best"] < 0.9          # nothing else on the frame looks like it
        path = tpl_root / "templates" / "unit" / "blob.png"
        assert path.exists()
        crop = cv2.imread(str(path))
        assert crop.shape[:2] == (40, 60)
        assert tuple(int(v) for v in crop[20, 30]) == (0, 200, 255)
        # same name again: refused, file untouched
        r = c.post("/api/template/unit/blob.png", json=dict(body, x=200))
        assert r.status_code == 409 and r.get_json()["exists"] is True
        assert tuple(int(v) for v in cv2.imread(str(path))[20, 30]) == (0, 200, 255)
        # ...unless overwrite is explicit
        r = c.post("/api/template/unit/blob.png",
                   json=dict(body, x=200, y=120, overwrite=True))
        assert r.status_code == 200
        assert tuple(int(v) for v in cv2.imread(str(path))[20, 30]) == (255, 255, 255)


def test_cropper_refuses_bad_boxes_names_and_stale_frames(dash, tpl_root):
    dash._remember_frame("t2", _frame_png())
    with dash.app.test_client() as c:
        assert c.post("/api/template/unit/a.png",
                      json={"ts": "t2", "x": 280, "y": 0, "w": 60, "h": 40}).status_code == 400
        assert c.post("/api/template/unit/a.png",
                      json={"ts": "t2", "x": 0, "y": 0, "w": 3, "h": 3}).status_code == 400
        # a flat-colour box (pure background) has nothing to match
        assert c.post("/api/template/unit/a.png",
                      json={"ts": "t2", "x": 0, "y": 0, "w": 30, "h": 30}).status_code == 400
        assert c.post("/api/template/unit/a.png",
                      json={"ts": "gone", "x": 0, "y": 0, "w": 20, "h": 20}).status_code == 409
        assert c.post("/api/template/a.png",
                      json={"ts": "t2", "x": 0, "y": 0, "w": 20, "h": 20}).status_code == 400
        assert c.post("/api/template/unit/..%2F..%2Fa.png",
                      json={"ts": "t2", "x": 0, "y": 0, "w": 20, "h": 20}).status_code in (400, 404)
    assert not list((tpl_root / "templates").rglob("*.png"))


def test_frame_cache_keeps_only_the_newest_frames(dash):
    dash._FRAME_CACHE.clear()
    for i in range(dash._FRAME_CACHE_KEEP + 3):
        dash._remember_frame(f"k{i}", b"x")
    assert len(dash._FRAME_CACHE) == dash._FRAME_CACHE_KEEP
    assert "k0" not in dash._FRAME_CACHE and f"k{dash._FRAME_CACHE_KEEP + 2}" in dash._FRAME_CACHE


# --------------------------------------------------------------- promotion
@pytest.fixture()
def profiles_dir(dash, tmp_path, monkeypatch):
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    shutil.copyfile(BACKEND / "profiles" / "default.yaml", pdir / "default.yaml")
    from goldens import load_golden
    player = copy.deepcopy(load_golden()["player"])
    player.pop("abilities_verified", None)
    (pdir / "acct.draft.yaml").write_text(
        yaml.safe_dump({"player": player}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(dash, "_profiles_dir", lambda: str(pdir))
    return pdir


def test_promote_writes_the_scanned_player_over_the_starter(dash, profiles_dir):
    with dash.app.test_client() as c:
        r = c.post("/api/profile-promote", json={"draft": "acct.draft.yaml", "name": "mine"})
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        assert j["name"] == "mine" and isinstance(j["problems"], list)
        assert j["abilities_verified"] is False
        doc = yaml.safe_load((profiles_dir / "mine.yaml").read_text(encoding="utf-8"))
        starter = yaml.safe_load((profiles_dir / "default.yaml").read_text(encoding="utf-8"))
        assert doc["player"]["card_presets"] == ["18v300", "disco", "main_farm",
                                                 "no_card", "tourney_p1"]
        assert doc["player"]["abilities_verified"] is False
        assert doc["blueprints"] == starter["blueprints"]
        assert doc["policies"] == starter["policies"] and doc["plan"] == starter["plan"]
        assert "_name" not in doc and "_path" not in doc
        # a real account promoted over the starter validates clean
        assert j["problems"] == []
        # existing target: refused unless overwrite
        r = c.post("/api/profile-promote", json={"draft": "acct", "name": "mine"})
        assert r.status_code == 409 and r.get_json()["exists"] is True
        r = c.post("/api/profile-promote",
                   json={"draft": "acct", "name": "mine", "overwrite": True})
        assert r.status_code == 200


def test_promote_keeps_a_scanned_verification_and_refuses_bad_input(dash, profiles_dir):
    doc = yaml.safe_load((profiles_dir / "acct.draft.yaml").read_text(encoding="utf-8"))
    doc["player"]["abilities_verified"] = True
    (profiles_dir / "seen.draft.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with dash.app.test_client() as c:
        r = c.post("/api/profile-promote", json={"draft": "seen", "name": "verified"})
        assert r.status_code == 200 and r.get_json()["abilities_verified"] is True
        assert c.post("/api/profile-promote",
                      json={"draft": "nope", "name": "x"}).status_code == 400
        assert c.post("/api/profile-promote",
                      json={"draft": "acct", "name": "../x"}).status_code == 400
        assert c.post("/api/profile-promote",
                      json={"draft": "acct", "name": "x.draft"}).status_code == 400
        assert c.post("/api/profile-promote",
                      json={"draft": "acct", "name": "y", "base": "missing"}).status_code == 400
    assert not (profiles_dir / "x.yaml").exists()


# --------------------------------------------------------------------- UI
def test_ui_wires_the_cropper_and_the_promote_button():
    html = (BACKEND.parent / "frontend" / "webui" / "index.html").read_text(encoding="utf-8")
    for needle in ("/api/wizard/required", "function cropSave", "/api/template/",
                   "async function promoteDraft", "/api/profile-promote",
                   'id="crop-canvas"'):
        assert needle in html, needle
