"""The Dissonance run type (2026-09-14): a farm-kind run entered through the
Dissonant Run dialog with one workshop tab disabled, its own loadout, no
shopping, every owned Ultimate Weapon on and its own perk bans.

Three things are pinned here:
* every entry point enters a run through shard.enter_run, so a dissonance
  blueprint never starts a NORMAL run (the day plan's handoff and a person's
  Start did exactly that before);
* the compiler trims what the disabled tab makes meaningless (shopping on
  that tab, the UW toggles when Ultimate Weapons are disabled) and carries
  `perk_bans` verbatim, validated;
* interactions/perks applies a ban list on the BAN PERKS tab by OCR'd row
  text, verifies every toggle, aborts on anything unexpected and never taps
  once the bans already match."""
import copy
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from player import playerprofile as pp, run_templates as rt  # noqa: E402
from interactions import perks  # noqa: E402


@pytest.fixture
def profile(monkeypatch):
    monkeypatch.setattr(pp, "CONFIG", yaml.safe_load((ROOT / "backend/config.example.yaml").read_text(encoding="utf8")))
    p = yaml.safe_load((ROOT / "backend/profiles/default.yaml").read_text(encoding="utf8"))
    p["blueprints"] = {}
    p.pop("plan", None)
    return p


# ------------------------------------------------------------ template
def test_dissonance_template_compiles_to_a_dissonant_no_shopping_coin_run(profile):
    before = copy.deepcopy(profile)
    result = rt.instantiate(profile, "dissonance", "disso",
                            {"dissonant_tab": "attack", "tier": 5,
                             "perk_bans": ["coins, but tower max health", "  "]})
    assert profile == before
    assert pp.validate(result) == []
    c = pp.compile_preset(result, "disso")
    assert c["kind"] == "coin" and c["tier"] == 5
    assert c["dissonant_tab"] == "attack"
    assert c["shopping"] == []                       # no_shopping, disabled
    assert c["perk_bans"] == ["coins, but tower max health"]
    assert c["restart_via_home"] is True
    assert c["loadout"] == "dissonance"


def test_dissonance_template_defaults_to_utility_and_refuses_a_bad_tab(profile):
    c = pp.compile_preset(rt.instantiate(profile, "dissonance", "d1"), "d1")
    assert c["dissonant_tab"] == "utility" and c["perk_bans"] is None   # no bans named: untouched
    assert pp.compile_preset(rt.instantiate(profile, "dissonance", "d0", {"perk_bans": [" "]}), "d0")["perk_bans"] is None
    with pytest.raises(ValueError, match="workshop tab"):
        rt.instantiate(profile, "dissonance", "d2", {"dissonant_tab": "cards"})
    with pytest.raises(ValueError, match="Perk bans"):
        rt.instantiate(profile, "dissonance", "d3", {"perk_bans": "coins"})
    # the farm template does not take dissonance choices
    with pytest.raises(ValueError, match="Unsupported"):
        rt.instantiate(profile, "farm", "f1", {"dissonant_tab": "attack"})


def test_all_uw_on_policy_needs_chain_lightning_like_every_uw_policy(profile):
    result = rt.instantiate(profile, "dissonance", "d", {"enable_uw": True})
    problems = pp.validate(result)
    assert problems and "Chain Lightning" in problems[0]
    owned = copy.deepcopy(result)
    owned["player"]["uws"] = {k: True for k in owned["player"]["uws"]}
    assert pp.validate(owned) == []
    c = pp.compile_preset(owned, "d")
    assert c["chain_lightning"] == {"always_on": True}
    assert c["uw_wanted"] and all(c["uw_wanted"].values())


def test_dissonance_rescue_fires_the_fleet_nuke(profile):
    """The optional rescue must carry the fleet-mark Nuke, like the farm's
    high_tier_wall: a dissonance run bound to wall rules only compiled
    `nuke_on_fleet: null` and let the Tier 14 fleet at 3495 through with Nuke
    ready (2026-09-14)."""
    result = rt.instantiate(profile, "dissonance", "d", {"enable_rescue": True})
    player = result["player"]
    player["abilities"] = {"nuke": True, "demon_mode": True}
    player["abilities_verified"] = True
    player["wall"] = True
    assert pp.validate(result) == []
    c = pp.compile_preset(result, "d")
    fleet = c["abilities"]["nuke_on_fleet"]
    assert fleet and fleet["after_waves"] == 3 and fleet["window_waves"] == 60
    assert c["abilities"]["dm_below"] == 0.5          # the tournament wall rule is kept


# ------------------------------------------------------------ compiler
def _coin(profile, **extra):
    profile["blueprints"]["x"] = {"kind": "coin", "label": "X", "loadout": "as_is", "tier": 1,
                                  "restart_via_home": True, "shopping": "default_sweep",
                                  "policies": {"gather": "all_on"}, **extra}
    return profile


def test_disabled_tab_directives_are_compiled_out_of_the_sweep(profile):
    _coin(profile, dissonant_tab="defense")
    assert pp.validate(profile) == []
    c = pp.compile_preset(profile, "x")
    tabs = {d["tab"] for d in c["shopping"]}
    assert "defense" not in tabs and tabs >= {"utility", "attack"}


def test_ultimate_weapons_dissonance_compiles_the_weapon_toggles_out(profile):
    profile["player"]["uws"] = {k: True for k in profile["player"]["uws"]}
    _coin(profile, dissonant_tab="ultimate_weapons",
          policies={"gather": "all_on", "uw": "farm_cl_choreo"})
    assert pp.validate(profile) == []
    c = pp.compile_preset(profile, "x")
    assert c["uw_wanted"] == {}
    assert c["chain_lightning"]["enabled"] is False
    assert c["chain_lightning"]["always_on"] is False


@pytest.mark.parametrize("bans,problem", [
    ("coins", "list of perk texts"),
    (["ok", ""], "list of perk texts"),
    ([3], "list of perk texts"),
    (["p"] * (pp.MAX_PERK_BANS + 1), "at most"),
])
def test_perk_bans_are_validated(profile, bans, problem):
    _coin(profile, perk_bans=bans)
    assert any(problem in p for p in pp.validate(profile))


def test_perk_bans_compile_on_coin_and_tournament_null_means_untouched(profile):
    _coin(profile)
    profile["blueprints"]["t"] = {"kind": "tournament", "label": "T", "loadout": "tourney_1",
                                  "gem_entry_max": 0, "tier": 1, "restart_via_home": True,
                                  "shopping": "default_sweep", "policies": {"gather": "all_on"},
                                  "perk_bans": []}
    assert pp.validate(profile) == []
    assert pp.compile_preset(profile, "x")["perk_bans"] is None
    assert pp.compile_preset(profile, "t")["perk_bans"] == []
    assert "perk_bans" not in pp.compile_preset(
        {**profile, "blueprints": {**profile["blueprints"],
                                   "s": {"kind": "shard", "label": "S", "loadout": "shard_farm",
                                         "tier": 1, "count": 1, "policies": {"gather": "gems_only"}}}}, "s")


# ---------------------------------------------------------- entry points
def test_enter_run_applies_bans_then_picks_the_dialog_or_battle(monkeypatch):
    from flows import shard
    calls = []
    monkeypatch.setattr(perks, "ensure_bans", lambda b: calls.append(("bans", b)))
    monkeypatch.setattr(shard, "start_dissonant", lambda tab: calls.append(("dissonant", tab)))
    monkeypatch.setattr(shard, "start_battle", lambda: calls.append(("battle",)))
    shard.enter_run({"dissonant_tab": "utility", "perk_bans": ["a"]})
    shard.enter_run({"dissonant_tab": None, "perk_bans": None})
    assert calls == [("bans", ["a"]), ("dissonant", "utility"),
                     ("bans", None), ("battle",)]


def test_every_entry_point_goes_through_enter_run():
    """The three places a coin-kind run is entered from Home. A grep, not a
    live test: the regression was a call to start_battle where enter_run
    belongs."""
    orch = (ROOT / "backend/orchestrator.py").read_text(encoding="utf-8")
    combo = (ROOT / "backend/scheduling/combo.py").read_text(encoding="utf-8")
    assert orch.count("shard.enter_run(") == 2
    assert "shard.start_battle()" not in orch
    block = combo[combo.index("def _block_handoff"):combo.index("def _handoff")]
    assert block.count("shard.enter_run(body)") == 1
    assert "shard.start_battle()" not in block      # the legacy _handoff keeps its constants


# ------------------------------------------------------------ readiness
def test_readiness_names_the_dialog_tab_image_and_the_perk_routes():
    from player import readiness
    body = {"kind": "coin", "dissonant_tab": "defense", "perk_bans": ["x"],
            "gather": {}, "loadout": None}
    rows = readiness.requirements({"loadouts": {}}, body)
    needed = {r for row in rows for r in row["alternatives"]}
    assert "dialogs/dissonant_tile_defense.png" in needed
    assert set(perks.templates()) <= needed
    # the dialog images BLOCK only when the runner must enter runs itself
    # (restart via Home); a hand-started run it adopts plays without them
    blocking = {r for row in rows if row["blocking"] for r in row["alternatives"]}
    assert "dialogs/dissonant_tile_defense.png" not in blocking
    rows = readiness.requirements({"loadouts": {}}, {**body, "restart_via_home": True})
    blocking = {r for row in rows if row["blocking"] for r in row["alternatives"]}
    assert "dialogs/dissonant_tile_defense.png" in blocking
    quiet = readiness.requirements({"loadouts": {}}, {**body, "dissonant_tab": None, "perk_bans": None})
    untouched = {r for row in quiet for r in row["alternatives"]}
    assert not (set(perks.templates()) & untouched)
    assert "dialogs/dissonant_tile_defense.png" not in untouched


# ------------------------------------------------------------ perk flow
def test_route_templates_resolve_by_manifest_route_name():
    rels = perks.templates()
    assert len(rels) == 3 and all(r.startswith("mapping/v") and r.endswith(".png") for r in rels)
    assert len(set(rels)) == 3


def test_normalize_drops_numbers_and_punctuation():
    assert perks.normalize("x1.98 coins, but tower max health -70.0%") == "coinsbuttowermaxhealth"
    assert perks.wanted_set(["Coins, but tower", "coins but tower", " ", None]) == ["coinsbuttower"]


def _lines(rows, header_after=None):
    """Fake OCR: rows are (y_native, text); the header sits after row index
    header_after (None = off screen). Returns upscaled crop coordinates."""
    out = []
    for i, (y, text) in enumerate(rows):
        out.append(((y - perks.LIST_TOP) * perks.OCR_SCALE, 100 * perks.OCR_SCALE, text))
        if header_after is not None and i == header_after:
            out.append(((y + 120 - perks.LIST_TOP) * perks.OCR_SCALE, 300 * perks.OCR_SCALE, "Available Perks"))
    return out


def test_read_rows_groups_wrapped_lines_and_splits_at_the_header():
    frame = np.zeros((2560, 1080, 3), np.uint8)
    lines = _lines([(760, "x1.98 coins, but tower max health"), (1000, "Enemies speed -44.0%, but")], header_after=1)
    lines.append(((1050 - perks.LIST_TOP) * 2, 200, "enemies damage x2.5"))      # wrapped 2nd line
    lines.append(((1300 - perks.LIST_TOP) * 2, 200, "x1.50 max health"))
    rows, header_y = perks.read_rows(frame, read=lambda crop: lines)
    assert [r["norm"] for r in rows] == ["coinsbuttowermaxhealth", "enemiesspeedbutenemiesdamage", "maxhealth"]
    assert header_y == 1120
    perks._banned(rows, header_y, True)
    assert [r["banned"] for r in rows] == [True, True, False]


class _Screen:
    """A scripted Perks dialog: taps toggle rows between the sections."""

    def __init__(self, banned, available):
        self.banned, self.available = list(banned), list(available)
        self.taps, self.swipes = [], []

    def lines(self, crop):
        rows = [(760 + 240 * i, t) for i, t in enumerate(self.banned)]
        header_after = len(self.banned) - 1 if self.banned else None
        out = _lines(rows, header_after)
        if not self.banned:
            out.append(((700 - perks.LIST_TOP) * 2, 600, "Available Perks"))
        base = 760 + 240 * len(self.banned) + 200
        for i, t in enumerate(self.available):
            out.append(((base + 240 * i - perks.LIST_TOP) * 2, 200, t))
        return out

    def tap(self, x, y, reason="", instant=True):
        self.taps.append((x, y, reason))
        rows = [(760 + 240 * i, t, True) for i, t in enumerate(self.banned)]
        base = 760 + 240 * len(self.banned) + 200
        rows += [(base + 240 * i, t, False) for i, t in enumerate(self.available)]
        hit = min(rows, key=lambda r: abs(r[0] - y))
        if hit[2]:
            self.banned.remove(hit[1]); self.available.insert(0, hit[1])
        else:
            self.available.remove(hit[1]); self.banned.append(hit[1])


@pytest.fixture
def dialog(monkeypatch):
    from device import act, capture
    from interactions import tourney
    from player import bootstrap
    from vision import detect
    from runtime import logger
    events = []
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(logger, "shot", lambda frame, tag: tag + ".png")
    monkeypatch.setattr(capture, "grab", lambda: np.zeros((2560, 1080, 3), np.uint8))
    monkeypatch.setattr(perks.time, "sleep", lambda s: None)
    state = {"screen": "home", "taps": []}
    monkeypatch.setattr(tourney, "on_home", lambda frame: state["screen"] == "home")
    monkeypatch.setattr(bootstrap, "recognize_screen", lambda frame: state["screen"])
    tpl = np.zeros((40, 80, 3), np.uint8)
    monkeypatch.setattr(detect, "_tpl", lambda rel: tpl)
    monkeypatch.setattr(detect, "_match", lambda frame, rel, thr: (True, 0.99, (500, 500)))

    def tap(x, y, reason="", instant=True):
        state["taps"].append(reason)
        if reason == "perks_open":
            state["screen"] = "perks_first"
        elif reason == "perks_ban_tab":
            state["screen"] = "perks_ban"
        elif reason.startswith("perks_close"):
            state["screen"] = "home"
        elif reason.startswith("perk_"):
            state["dialog"].tap(x, y, reason)
    monkeypatch.setattr(act, "tap", tap)
    monkeypatch.setattr(act, "swipe", lambda *a, **k: state["dialog"].swipes.append(a))
    return state, events


def test_apply_bans_toggles_only_what_differs_and_verifies_the_result(dialog):
    state, events = dialog
    d = _Screen(banned=["x1.98 coins, but tower max health", "Boss health -73.5%, but boss speed +50%"],
                available=["x1.50 max health", "Lifesteal x2.75, but knockback force -70%"])
    state["dialog"] = d
    out = perks.apply_bans(["coins, but tower max health", "lifesteal"], read=d.lines)
    assert d.banned == ["x1.98 coins, but tower max health", "Lifesteal x2.75, but knockback force -70%"]
    assert d.available[0] == "Boss health -73.5%, but boss speed +50%"
    assert out["toggled"] == 2 and len(out["banned"]) == 2
    assert state["taps"][:2] == ["perks_open", "perks_ban_tab"] and state["taps"][-1] == "perks_close"
    assert [k for k, _ in events] == ["perk_ban_toggled", "perk_ban_toggled"]
    assert state["screen"] == "home"


def test_apply_bans_taps_nothing_when_the_list_already_matches(dialog):
    state, events = dialog
    d = _Screen(banned=["x1.98 coins, but tower max health"], available=["x1.50 max health"])
    state["dialog"] = d
    out = perks.apply_bans(["coins but tower max health"], read=d.lines)
    assert out["toggled"] == 0 and d.taps == []
    assert state["taps"] == ["perks_open", "perks_ban_tab", "perks_close"]


def test_apply_bans_empty_list_clears_every_ban(dialog):
    state, _ = dialog
    d = _Screen(banned=["a perk", "another perk"], available=["x1.50 max health"])
    state["dialog"] = d
    out = perks.apply_bans([], read=d.lines)
    assert d.banned == [] and out["toggled"] == 2


def test_apply_bans_aborts_when_a_wanted_perk_is_not_in_the_list(dialog):
    state, _ = dialog
    d = _Screen(banned=[], available=["x1.50 max health"])
    state["dialog"] = d
    with pytest.raises(perks.PerkAbort, match="not found"):
        perks.apply_bans(["chain lightning damage"], read=d.lines)
    assert d.taps == []                       # nothing toggled on the way


def test_apply_bans_aborts_when_a_tap_does_not_toggle(dialog):
    state, _ = dialog
    d = _Screen(banned=[], available=["x1.50 max health"])
    d.tap = lambda x, y, reason="", instant=True: d.taps.append(reason)   # the game ignores the tap
    state["dialog"] = d
    with pytest.raises(perks.PerkAbort, match="did not toggle"):
        perks.apply_bans(["max health"], read=d.lines)
    assert len(d.taps) == 1


def test_apply_bans_refuses_off_home_and_unknown_controls(dialog, monkeypatch):
    state, _ = dialog
    state["screen"] = "cards"
    with pytest.raises(perks.PerkAbort, match="Home only"):
        perks.apply_bans(["x"])
    state["screen"] = "home"
    from vision import detect
    monkeypatch.setattr(detect, "_match", lambda frame, rel, thr: (False, 0.3, (0, 0)))
    with pytest.raises(perks.PerkAbort, match="not recognised"):
        perks.apply_bans(["x"])
    assert state["taps"] == []


def test_ensure_bans_remembers_the_applied_set_and_degrades_on_failure(dialog, monkeypatch):
    state, events = dialog
    from scheduling import daystate
    store = {}
    monkeypatch.setattr(daystate, "get_raw", lambda k, default=None: store.get(k, default))
    monkeypatch.setattr(daystate, "set_raw", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(perks, "_key", lambda: "perk_bans:test")
    applied = []
    monkeypatch.setattr(perks, "apply_bans", lambda w, **k: applied.append(w) or {"banned": w, "toggled": 1})
    assert perks.ensure_bans(None) is False and applied == []
    assert perks.ensure_bans(["Coins, but tower"]) is True
    assert perks.ensure_bans(["coins but tower"]) is False        # same set: no dialog
    assert applied == [["coinsbuttower"]]
    assert events[-1][0] == "perk_bans_applied"

    def boom(w, **k):
        raise perks.PerkAbort("banned section does not match")
    monkeypatch.setattr(perks, "apply_bans", boom)
    assert perks.ensure_bans(["other"]) is False
    assert events[-1][0] == "perk_bans_failed" and "does not match" in events[-1][1]["error"]
    assert store["perk_bans:test"] == '["coinsbuttower"]'         # the failed set is NOT remembered
    assert "perks_close_after_failure" in state["taps"]


def test_ensure_bans_is_wired_into_enter_run_only():
    src = (ROOT / "backend").rglob("*.py")
    users = [p for p in src if "ensure_bans(" in p.read_text(encoding="utf-8") and "tests" not in p.parts]
    assert sorted(p.name for p in users) == ["perks.py", "shard.py"]
