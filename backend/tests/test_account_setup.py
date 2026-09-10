"""Account isolation, pre-run refusals and recoverable calibration, offline."""
import copy
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from player import accounts, readiness

ROOT = Path(__file__).resolve().parents[1]


def config():
    return {"active_instance": "main", "instances": {"main": {"account": "alice"}},
            "loadouts": {"legacy": {}}, "active_profile": "default",
            "accounts": {"alice": {"active_profile": "alice", "loadouts": {"farm": {"cards": "Alice"}}},
                         "bob": {"active_profile": "bob", "loadouts": {"farm": {"cards": "Bob"}}}}}


def image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), np.random.default_rng(4).integers(0, 255, (30, 50, 3), dtype=np.uint8))


def test_account_config_roundtrip_preserves_other_accounts_and_machine_defaults():
    original = config()
    effective = accounts.effective(original)
    assert effective["loadouts"]["farm"]["cards"] == "Alice"
    effective["loadouts"]["farm"]["cards"] = "New deck"
    saved = accounts.persist(effective, original)
    assert saved["loadouts"] == original["loadouts"]
    assert saved["accounts"]["alice"]["loadouts"]["farm"]["cards"] == "New deck"
    assert saved["accounts"]["bob"] == original["accounts"]["bob"]
    assert original["accounts"]["alice"]["loadouts"]["farm"]["cards"] == "Alice"


def test_no_account_or_emulator_can_borrow_another_accounts_crop(tmp_path):
    cfg = config()
    legacy = tmp_path / "templates/cards/preset_farm.png"
    image(legacy)
    alice = accounts.template_path(tmp_path, cfg, "cards/preset_farm.png", write=True)
    assert alice != legacy
    image(alice)
    cfg["instances"]["main"]["account"] = "bob"
    assert not accounts.template_path(tmp_path, cfg, "cards/preset_farm.png").exists()
    assert accounts.template_files(tmp_path, cfg, "cards/*.png") == []
    cfg["instances"]["main"]["account"] = "alice"
    cfg["instances"]["second"] = {"account": "alice"}
    cfg["active_instance"] = "second"
    assert not accounts.template_path(tmp_path, cfg, "cards/preset_farm.png").exists()


def test_shared_ui_images_never_fall_back_to_another_install(tmp_path):
    cfg = config()
    legacy = tmp_path / "templates/buttons/retry.png"
    image(legacy)
    assert not accounts.template_path(tmp_path, cfg, "buttons/retry.png").exists()
    assert accounts.template_files(tmp_path, cfg, "buttons/*.png") == []
    target = accounts.template_path(tmp_path, cfg, "buttons/retry.png", write=True)
    assert target != legacy
    image(target)
    assert accounts.template_path(tmp_path, cfg, "buttons/retry.png") == target


@pytest.mark.parametrize("bad", ["../bob", "A/B", "a:b", "NUL", "CON", "", "/tmp"])
def test_account_names_cannot_escape_storage(bad):
    with pytest.raises(ValueError):
        accounts.valid_id(bad)


@pytest.mark.parametrize("bad", ["../x.png", "cards/../../x.png", "C:/x.png", "cards//x.png"])
def test_template_names_cannot_escape_storage(tmp_path, bad):
    with pytest.raises(ValueError):
        accounts.template_path(tmp_path, config(), bad)


def coin(**extra):
    return dict({"kind": "coin", "loadout": None, "shopping": [], "gather": {
        "flying_gem": False, "ad_gems": False, "quests_8h": False,
        "quest_rewards": False, "guild": False}}, **extra)


def names(cfg, body):
    return {rel for row in readiness.requirements(cfg, body) for rel in row["alternatives"]}


def test_requirements_ignore_unused_equipment_and_policy_features():
    cfg = accounts.effective(config())
    basic = names(cfg, coin())
    assert "cards/preset_Alice.png" not in basic
    assert "buttons/nuke.png" not in basic
    assert "stats/damage.png" not in basic
    assert "tourney/tournament_stats.png" in basic  # overlay guard, not paid entry
    assert "tourney/trophy_tile.png" not in basic
    enabled = names(cfg, coin(loadout="farm", shopping=[{"stats": ["damage"]}],
                              rules=[{"do": {"fire": {"button": "nuke"}}}]))
    assert {"cards/preset_Alice.png", "stats/damage.png", "buttons/nuke.png"} <= enabled


def test_missing_and_corrupt_files_refuse_then_valid_images_pass(tmp_path):
    cfg = config()
    body = coin()
    assert not readiness.check(tmp_path, cfg, body)["ready"]
    for row in readiness.requirements(cfg, body):
        image(accounts.template_path(tmp_path, cfg, row["alternatives"][0], write=True))
    assert readiness.check(tmp_path, cfg, body)["ready"]
    path = accounts.template_path(tmp_path, cfg, "buttons/retry.png", write=True)
    path.write_bytes(b"not a PNG")
    with pytest.raises(RuntimeError, match="buttons/retry.png"):
        readiness.require(tmp_path, cfg, body)


def test_known_stale_calibration_is_not_ready(tmp_path):
    cfg = config()
    for row in readiness.requirements(cfg, coin()):
        image(accounts.template_path(tmp_path, cfg, row["alternatives"][0], write=True))
    report = accounts.calibration_dir(tmp_path, cfg) / "calibrate_report.json"
    report.write_text(json.dumps({"entries": [{"rel": "buttons/retry.png", "status": "stale"}]}))
    assert not readiness.check(tmp_path, cfg, coin())["ready"]


def test_display_requires_both_native_pixels_and_density():
    from device import layout
    assert layout.density("Physical density: 240\nOverride density: 360") == 360
    layout.require_native(1080, 2560, 360)
    for dims in [(1080, 2560, 240), (2560, 1080, 360), (1080, 2560, None)]:
        with pytest.raises(ValueError, match="Setup"):
            layout.require_native(*dims)


@pytest.fixture
def dash(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("account_setup_dashboard", ROOT.parent / "frontend/dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(mod, "_procs", lambda: [])
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles/default.yaml").write_text((ROOT / "profiles/default.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return mod


def test_new_account_has_all_shipped_run_recipes(dash):
    with dash.app.test_client() as client:
        reply = client.post("/api/accounts", json={"id": "alice", "create": True})
        assert reply.status_code == 200, reply.json
        cfg = dash.load_config()
        assert cfg["active_profile"] == "account_alice"
        profile = yaml.safe_load((Path(dash.ROOT) / "profiles/account_alice.yaml").read_text())
        assert list(profile["blueprints"]) == ["coin_default", "tourney_main", "shard_run"]
        assert profile["blueprints"]["coin_default"]["loadout"] == "as_is"
        assert profile["blueprints"]["tourney_main"]["loadout"] == "my_equipment"
        assert "plan" not in profile
        assert client.get("/api/runs").status_code == 200


def test_unready_profile_remains_visible_but_cannot_compile_for_launch(dash):
    with dash.app.test_client() as client:
        assert client.post("/api/accounts", json={"id": "alice", "create": True}).status_code == 200
        path = Path(dash.ROOT) / "profiles/account_alice.yaml"
        profile = yaml.safe_load(path.read_text())
        profile["blueprints"]["coin_default"]["loadout"] = "unknown_equipment"
        path.write_text(yaml.safe_dump(profile))
        result = client.get("/api/runs")
        assert result.status_code == 200
        assert "bp_coin_default" in result.json["runs"]
        assert not result.json["readiness"]["bp_coin_default"]["ready"]
        ready = client.get("/api/readiness?preset=bp_coin_default")
        assert ready.status_code == 200 and not ready.json["ready"]
        assert ready.json["diagnostics"]
        with pytest.raises(ValueError):
            dash._compiled_runs(dash.load_config())


def test_apply_discoveries_does_not_require_unrelated_run_readiness(dash):
    with dash.app.test_client() as client:
        client.post("/api/accounts", json={"id": "alice", "create": True})
        path = Path(dash.ROOT) / "profiles/account_alice.yaml"
        profile = yaml.safe_load(path.read_text())
        profile["blueprints"]["coin_default"]["loadout"] = "not_configured"
        path.write_text(yaml.safe_dump(profile))
        folder = Path(dash._calibration_dir(dash.load_config()))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "calibrate_state.json").write_text(json.dumps({
            "player": {"card_presets": ["my_cards"]}, "phases": {"cards": {"status": "done"}}}))
        result = client.post("/api/calibrate/apply", json={})
        assert result.status_code == 200, result.json
        saved = yaml.safe_load(path.read_text())
        assert saved["player"]["card_presets"] == ["my_cards"]
        assert saved["blueprints"] == profile["blueprints"]
        assert result.json["remaining_run_checks"]


def test_import_copies_profile_and_templates_without_changing_original(dash):
    source = Path(dash.ROOT) / "templates/cards/preset_farm.png"
    image(source)
    original_profile = (Path(dash.ROOT) / "profiles/default.yaml").read_bytes()
    with dash.app.test_client() as client:
        reply = client.post("/api/accounts", json={"id": "alice", "create": True, "import_current": True})
        assert reply.status_code == 200, reply.json
    cfg = dash.load_config()
    assert accounts.template_path(dash.ROOT, cfg, "cards/preset_farm.png").read_bytes() == source.read_bytes()
    assert (Path(dash.ROOT) / "profiles/default.yaml").read_bytes() == original_profile
    assert cfg["active_profile"] != "default"


def test_start_refuses_before_spawn_when_selected_run_has_missing_images(dash, monkeypatch):
    monkeypatch.setattr(dash, "_compiled_runs", lambda cfg: {"bp_coin_default": coin()})
    monkeypatch.setattr(dash.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    with dash.app.test_client() as client:
        reply = client.post("/api/control", json={"action": "start", "preset": "bp_coin_default"})
    assert reply.status_code == 409
    assert reply.json["missing"] and reply.json["calibration_url"].endswith("#calibrate")


def test_switch_is_refused_while_automation_is_running(dash, monkeypatch):
    monkeypatch.setattr(dash, "_procs", lambda: [{"runner": "calibrate"}])
    with dash.app.test_client() as client:
        assert client.post("/api/accounts", json={"id": "bob", "create": True}).status_code == 409


def test_profile_vocabulary_survives_no_shipped_images(tmp_path, monkeypatch):
    from player import playerprofile as pp
    monkeypatch.setattr(pp, "_STAT_TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(pp, "_STATS_CACHE", None)
    assert "damage" in pp.shop_stats(refresh=True)


def test_changing_emulator_under_same_instance_invalidates_personal_crops(tmp_path):
    cfg = config()
    cfg["instances"]["main"]["serial"] = "127.0.0.1:5555"
    first = accounts.template_path(tmp_path, cfg, "cards/preset_farm.png", write=True)
    image(first)
    cfg["instances"]["main"]["serial"] = "127.0.0.1:16384"
    assert not accounts.template_path(tmp_path, cfg, "cards/preset_farm.png").exists()


def test_account_switch_does_not_change_another_accounts_profile_or_loadout(dash):
    with dash.app.test_client() as client:
        assert client.post("/api/accounts", json={"id": "alice", "create": True}).status_code == 200
        assert client.post("/api/account-tier", json={"max_tier": 7}).status_code == 200
        assert client.post("/api/accounts", json={"id": "bob", "create": True}).status_code == 200
        assert client.get("/api/accounts").json["max_tier"] == 1
        assert client.post("/api/profile-activate", json={"name": "account_alice"}).status_code == 409
        assert client.post("/api/accounts", json={"id": "alice"}).status_code == 200
        assert client.get("/api/accounts").json["max_tier"] == 7


def test_navigation_permission_is_only_passed_to_calibration(dash, monkeypatch):
    from player import scan_plan
    monkeypatch.setattr(scan_plan, "missing_navigation", lambda *a, **k: [])
    commands = []
    monkeypatch.setattr(dash.subprocess, "Popen", lambda args, **kwargs: commands.append(args))
    with dash.app.test_client() as client:
        result = client.post("/api/calibrate/start", json={"phases": "c", "allow_navigation": True})
        assert result.status_code == 200
        assert "--allow-navigation" in commands[0]
        assert not dash.load_config()["instances"]["main"]["allow_taps"]
        assert client.post("/api/calibrate/start", json={"phases": "invalid", "allow_navigation": True}).status_code == 400
        assert len(commands) == 1
