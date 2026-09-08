"""Every image a run still needs is explained and shown, not just named.

`template_docs.describe` gives each target a plain-language card (what it
is, where it shows, how to get it); readiness rows carry those cards and the
scan-plan step; /api/runs lists one detail row per missing image for the
Control card; /api/artwork/<rel> serves the installed game's own artwork for
the target from the asset library Full setup extracted (user, 2026-09-08:
"we do not have a good description what this image is used for ... the
extracted-from-binary-apk image should be shown so we know what to find").
"""
import importlib.util
import json
import os

import pytest

from player import readiness, template_docs

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_every_requirable_image_has_a_card_and_unknown_names_fall_back():
    body = {"kind": "coin", "loadout": "lo", "shopping": [{"stats": ["damage"]}],
            "gather": {k: True for k in ("flying_gem", "ad_gems", "quests_8h", "quest_rewards", "guild")},
            "uw_wanted": {"chain_lightning": True}, "rules": [{"do": {"burst": {"fire": "nuke"}}}],
            "abilities": {"dm_below": 0.2, "rescue_bar": "wall"}, "max_wave": 100, "dissonant_tab": "utility"}
    cfg = {"active_instance": "main", "instances": {"main": {}},
           "loadouts": {"lo": {"global_preset": "Farm", "module_preset": "M", "cards": "main", "modules": ["mod_a"]}}}
    for row in readiness.requirements(cfg, body):
        for rel in row["alternatives"]:
            card = template_docs.describe(rel)
            assert card["label"] and card["what"] and card["where"] and card["how"], rel
    assert template_docs.describe("buttons/nuke.png")["art"] == ["protector-nuke"]   # the HUD button, not the auto-nuke perk
    fallback = template_docs.describe("modules/whatever_icon.png")
    assert fallback["label"].startswith("Module icon") and "whatever icon" in fallback["label"]
    assert template_docs.describe("config: rois.wall_bar")["where"] == "config.yaml"


def test_readiness_rows_carry_the_cards_and_the_scan_step(tmp_path):
    cfg = {"active_instance": "main", "instances": {"main": {}}, "loadouts": {}}
    body = {"kind": "coin", "gather": {}, "abilities": {"dm_below": 0.2}}
    rows = {r["alternatives"][0]: r for r in readiness.check(tmp_path, cfg, body)["missing"]}
    card = rows["buttons/demon_mode.png"]["docs"][0]
    assert card["label"] == "Demon Mode button" and card["step"]["id"] == "battle"
    assert card["step"]["title"].startswith("7.")


@pytest.fixture(scope="module")
def dash():
    path = os.path.join(os.path.dirname(BACKEND), "frontend", "dashboard.py")
    spec = importlib.util.spec_from_file_location("tp_dashboard_outstanding", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_api_runs_lists_one_detail_row_per_missing_image(dash, monkeypatch):
    runs = {"bp_coin": {"kind": "coin", "label": "Coin"}}
    monkeypatch.setattr(dash, "load_config", lambda: {"active_instance": "main", "active_profile": "me"})
    monkeypatch.setattr(dash, "_compiled_runs", lambda cfg: runs)
    fake = {"ready": False, "advisory": [],
            "missing": [{"alternatives": ["buttons/nuke.png"], "reasons": ["Rescue ability"],
                         "docs": [template_docs.describe("buttons/nuke.png")]}]}
    monkeypatch.setattr(readiness, "check", lambda root, cfg, body: fake)
    with dash.app.test_client() as c:
        r = c.get("/api/runs").json["readiness"]["bp_coin"]
    assert r["missing"] == ["buttons/nuke.png"]
    assert r["details"][0]["reasons"] == ["Rescue ability"]
    assert r["details"][0]["docs"][0]["label"] == "Nuke button"


def test_artwork_comes_from_the_mapping_then_from_the_documented_names(dash, tmp_path, monkeypatch):
    lib = tmp_path / "asset_library" / "abc"
    (lib / "images").mkdir(parents=True)
    for name in ("mapped", "sprite", "texture"):
        (lib / "images" / f"{name}.png").write_bytes(PNG)
    (lib / "index.json").write_text(json.dumps({"images": [
        {"name": "protector-nuke", "type": "Texture2D", "file": "images/texture.png", "width": 64, "height": 64},
        {"name": "Protector-Nuke", "type": "Sprite", "file": "images/sprite.png", "width": 32, "height": 32},
    ]}))
    (lib / "mapping.json").write_text(json.dumps({"targets": {
        "home/tile_guild.png": {"candidates": [{"file": "images/mapped.png"}]}}}))
    monkeypatch.setattr(dash, "load_config", lambda: {"active_instance": "main"})
    monkeypatch.setattr(dash, "_calibration_dir", lambda cfg: str(tmp_path))
    dash._ARTWORK_INDEX.clear()
    assert dash._artwork_path({}, "home/tile_guild.png") == str(lib / "images" / "mapped.png")
    assert dash._artwork_path({}, "buttons/nuke.png") == str(lib / "images" / "sprite.png")   # Sprite over Texture2D
    assert dash._artwork_path({}, "screens/hdr_cards.png") is None                            # rendered text: no sprite
    with dash.app.test_client() as c:
        assert c.get("/api/artwork/buttons/nuke.png").status_code == 200
        badge = c.get("/api/artwork/screens/hdr_cards.png")                                     # ...so a text badge stands in
        assert badge.status_code == 200 and badge.data[1:4] == b"PNG"
        assert c.get("/api/artwork/modules/some_module.png").status_code == 404               # neither sprite nor text
