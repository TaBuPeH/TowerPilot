"""The shipped tree carries NO account data (user, 2026-09-06: "we need to be
able to scan any user's account, not just mine" / "do not assume the names of
the presets for the cards or any presets"). Everything account-specific is
produced per install - by the Calibrate cropper, the scan and the config
editor - and these tests fail the moment something slips back in."""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


@pytest.fixture()
def dash():
    path = REPO / "frontend" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("tp_dashboard_clean", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tracked(prefix: str) -> list[str] | None:
    try:
        r = subprocess.run(["git", "ls-files", prefix], cwd=str(REPO),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return [ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if ln.strip()]


# ------------------------------------------------------------- templates
def test_no_account_template_is_tracked():
    """Card preset tabs, preset picker rows and module icons are cut from
    ONE account at ONE rarity - they are never part of the repo."""
    from player import catalogue
    tracked = _tracked("backend/templates")
    if tracked is None:
        pytest.skip("not a git checkout")
    rels = [t[len("backend/templates/"):] for t in tracked]
    bad = []
    for rel in rels:
        folder, _, name = rel.partition("/")
        if folder == "cards" and name.startswith("preset_"):
            bad.append(rel)
        elif rel.startswith("modules/equipped/"):
            bad.append(rel)
        elif folder == "modules" and name[:-4] in catalogue.MODULES:
            bad.append(rel)
        elif folder == "presets" and name.startswith(
                ("gp_", "modules_", "guardians_", "workshop_", "bots_")) \
                and rel != "presets/gp_none.png":
            bad.append(rel)
    assert bad == [], bad


def test_gitignore_keeps_the_players_own_cuts_out_of_git():
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pat in ("backend/templates/cards/preset_*.png",
                "backend/templates/modules/equipped/",
                "backend/templates/modules/*.png",
                "backend/templates/presets/gp_*.png",
                "backend/templates/presets/modules_*.png",
                "backend/templates/presets/guardians_*.png",
                "backend/templates/presets/workshop_*.png",
                "backend/templates/presets/bots_*.png"):
        assert pat in text, pat
    # the generic buttons that live in the modules folder stay shipped
    for keep in ("assist_btn", "buy_module", "equip_btn", "primary_btn",
                 "shatter_dialog", "shatter_rare_text", "transfer_yes",
                 "v29_dialog_close", "v29_equip_btn"):
        assert f"!backend/templates/modules/{keep}.png" in text, keep
        assert (BACKEND / "templates" / "modules" / f"{keep}.png").exists(), keep


# ---------------------------------------------------- shipped config data
def test_example_config_loadouts_name_nothing():
    cfg = yaml.safe_load((BACKEND / "config.example.yaml").read_text(encoding="utf-8"))
    for name, body in cfg["loadouts"].items():
        assert body in ({}, {"defined": False}), (name, body)
    assert cfg["tourney_card_tweaks"] == {"drop": [], "add": []}
    for name, p in cfg["presets"].items():
        assert int(p.get("tier", 1)) == 1, (name, p.get("tier"))


def test_starter_profile_owns_nothing_and_binds_no_ownership_policy():
    from player import playerprofile as pp
    prof = pp.load("default")
    player = prof["player"]
    assert all(v is False for v in player["uws"].values())
    assert player["abilities"] == {"nuke": False, "demon_mode": False}
    assert player["abilities_verified"] is False
    for key in ("card_presets", "global_presets", "guardians",
                "modules_equipped", "modules_in_grid"):
        assert player[key] == [], key
    assert all(v == [] for v in player["category_presets"].values())
    assert player["wall"] is False and player["max_tier"] == 1
    assert pp.validate(prof) == []
    for name, bp in prof["blueprints"].items():
        refs = bp.get("policies") or {}
        assert "uw" not in refs and "rescue" not in refs, name
        assert bp["tier"] == 1, name
    # the library still ships in full, unbound
    assert set(prof["policies"]["uw_policies"]) >= {"farm_cl_choreo", "tourney_cl"}
    assert set(prof["policies"]["rescue_policies"]) >= {"high_tier_wall",
                                                        "tournament_wall_nuke"}


# ---------------------------------------------------------- code paths
def test_no_account_constants_survive_in_code():
    from interactions import loadout, tourney
    from player import catalogue
    from flows import shard
    for mod, attr in ((loadout, "CARD_TABS"), (tourney, "CARD_PRESET"),
                      (tourney, "MODULE_PLAN"), (tourney, "CARDS_DROP"),
                      (tourney, "CARDS_ADD"), (tourney, "card_swap"),
                      (tourney, "module_swap"), (catalogue, "INVENTORY"),
                      (catalogue, "EQUIPPED"), (catalogue, "duplicates_of"),
                      (shard, "CARD_PRESET"), (shard, "MODULE_PLAN")):
        assert not hasattr(mod, attr), f"{mod.__name__}.{attr}"
    assert shard.TIER == 1


def test_card_tabs_are_whatever_templates_the_player_cut(tmp_path, monkeypatch):
    from interactions import loadout
    root = tmp_path / "backend"
    (root / "templates" / "cards").mkdir(parents=True)
    monkeypatch.setattr(loadout, "ROOT", root)
    assert loadout.card_tabs() == ()
    for name in ("preset_deck_b.png", "preset_deck_a.png", "active_label.png"):
        (root / "templates" / "cards" / name).write_bytes(b"png")
    assert loadout.card_tabs() == ("deck_a", "deck_b")


def test_card_tweaks_come_from_config_and_skip_when_empty(monkeypatch):
    from interactions import tourney
    monkeypatch.setattr(tourney, "CONFIG", {})
    assert tourney._card_tweak_plan() == ([], [])
    monkeypatch.setattr(tourney, "CONFIG",
                        {"tourney_card_tweaks": {"drop": ["cash"], "add": ["extra_orb"]}})
    assert tourney._card_tweak_plan() == (["cash"], ["extra_orb"])
    # nothing configured: the cards screen is not even opened
    monkeypatch.setattr(tourney, "CONFIG", {"tourney_card_tweaks": {}})
    opened = []
    monkeypatch.setattr(tourney, "open_nav",
                        lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(AssertionError))
    events = []
    monkeypatch.setattr(tourney.logger, "event", lambda kind, **kw: events.append((kind, kw)))
    tourney.card_tweaks()
    assert opened == [] and events and events[0][0] == "card_tweaks_skipped"


# ------------------------------------------------------------- promote
@pytest.fixture()
def profiles_dir(dash, tmp_path, monkeypatch):
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    shutil.copyfile(BACKEND / "profiles" / "default.yaml", pdir / "default.yaml")
    monkeypatch.setattr(dash, "_profiles_dir", lambda: str(pdir))
    return pdir


def test_promote_takes_the_scanned_tier_and_assumes_no_wall(dash, profiles_dir):
    (profiles_dir / "seen.draft.yaml").write_text(
        yaml.safe_dump({"player": {"card_presets": [], "tier_current": 7}}),
        encoding="utf-8")
    (profiles_dir / "blind.draft.yaml").write_text(
        yaml.safe_dump({"player": {"card_presets": []}}), encoding="utf-8")
    with dash.app.test_client() as c:
        assert c.post("/api/profile-promote",
                      json={"draft": "seen", "name": "seen"}).status_code == 200
        assert c.post("/api/profile-promote",
                      json={"draft": "blind", "name": "blind"}).status_code == 200
    seen = yaml.safe_load((profiles_dir / "seen.yaml").read_text(encoding="utf-8"))
    blind = yaml.safe_load((profiles_dir / "blind.yaml").read_text(encoding="utf-8"))
    assert seen["player"]["max_tier"] == 7 and blind["player"]["max_tier"] == 1
    assert seen["player"]["wall"] is False and blind["player"]["wall"] is False
    assert seen["player"]["abilities_verified"] is False


def test_catalogue_endpoint_lists_the_module_slugs(dash):
    with dash.app.test_client() as c:
        mods = c.get("/api/catalogue/modules").get_json()["modules"]
    slugs = {m["slug"] for m in mods}
    assert {"space_displacer", "multiverse_nexus"} <= slugs
    assert all(m["name"] and isinstance(m["abbrevs"], list) for m in mods)


def test_calibrate_page_tells_a_fresh_account_what_to_cut():
    html = (REPO / "frontend" / "webui" / "index.html").read_text(encoding="utf-8")
    for needle in ("/api/catalogue/modules", "cards/preset_&lt;name&gt;.png",
                   "presets/gp_&lt;slug&gt;.png", "modules/equipped/&lt;slug&gt;.png"):
        assert needle in html, needle
