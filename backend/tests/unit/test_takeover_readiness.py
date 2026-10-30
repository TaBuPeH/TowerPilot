"""Readiness of a takeover (roadmap item 3, T3a): `requirements(cfg, body, attached=True)`, `check(..., attached=True)` and `require(..., attached=True)`.

An attached run starts on a battle already on screen: the Home-side images are not asked for, the in-battle ones still block, and the tournament badge becomes
BLOCKING (a missing badge reads as "not a tournament"). `attached=False` is the output the module always had, proven here against a pinned copy of it."""
import copy
import hashlib
import itertools
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

import paths as test_paths
from goldens import load_golden
from player import accounts, attach_policy, battle_end, playerprofile, readiness
from vision import screen

PINNED = test_paths.FIXTURES / "takeover_t3a" / "readiness_f047c55.py.txt"
PINNED_SHA256 = "18B8906CDA9A8F6587C1022543B90B21F2E08FE5FD6D4262C664A02FDAC6683A"        # backend/player/readiness.py at f047c55
BADGE = "tourney/in_tournament.png"
EQUIPMENT = {"global_preset": "Farm Run", "module_preset": "Mods", "guardian_preset": "Guard", "workshop_preset": "Shop", "bot_preset": "Bots",
             "cards": "main_farm", "cards_restore": "no_card", "modules": [["black_hole_digestor", "primary"], "amplifying_strike"], "modules_restore": ["amplifying_strike"]}


def cfg(wall_bar=True, loadouts=None):
    instance = {"rois": {"wall_bar": [84, 1559, 424, 44]}} if wall_bar else {}
    return {"active_instance": "main", "instances": {"main": instance}, "loadouts": {"farm": EQUIPMENT} if loadouts is None else loadouts}


def coin(**more):
    body = {"kind": "coin", "loadout": "farm", "shopping": [{"stats": ["health", "damage"]}], "uw_wanted": {"black_hole": True},
            "abilities": {"dm_below": 0.02, "nuke_on_fleet": {"after_waves": 3}, "rescue_bar": "wall", "hold_until_second_wind": True},
            "gather": {"ad_gems": True, "quests_8h": True, "quest_rewards": True, "guild": True, "free_store_gems": True},
            "dissonant_tab": "utility", "restart_via_home": True, "perk_bans": ["x"], "max_wave": None, "rules": []}
    body.update(more)
    return body


def paths(rows):
    return {rel for row in rows for rel in row["alternatives"]}


def by_first(rows):
    return {row["alternatives"][0]: row for row in rows}


def write_image(root, config, rel):
    path = accounts.template_path(root, config, rel, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.random.default_rng(3).integers(0, 256, (20, 30, 3), dtype=np.uint8))


def write_blocking(root, config, body, *, attached, skip=()):
    for row in readiness.requirements(config, body, attached=attached):
        if row["blocking"]:
            for rel in row["alternatives"][:1]:
                if rel not in skip and not rel.startswith("config:"):
                    write_image(root, config, rel)


def unchanged_module():
    """The readiness module as it was at f047c55, loaded from its pinned text."""
    text = PINNED.read_bytes()
    assert hashlib.sha256(text).hexdigest().upper() == PINNED_SHA256
    module = types.ModuleType("readiness_unchanged_f047c55")
    exec(compile(text.decode("utf-8"), str(PINNED), "exec"), module.__dict__)
    return module


# ------------------------------------------------------------------ what an attached run asks for
def test_attached_requirements_drop_home_only_templates():
    from interactions import perks
    config, body = cfg(), coin()
    home, attached = paths(readiness.requirements(config, body)), paths(readiness.requirements(config, body, attached=True))
    equipment = {"presets/gp_farm_run.png", "presets/modules_mods.png", "presets/guardians_guard.png", "presets/workshop_shop.png", "presets/bots_bots.png",
                 "presets/picker_icon.png", "presets/picker_icon_large.png", "presets/close_x.png", "presets/select_header.png", "presets/gp_none.png",
                 "cards/preset_main_farm.png", "cards/preset_no_card.png", "cards/active_label.png",
                 "modules/black_hole_digestor.png", "modules/amplifying_strike.png", "modules/buy_module.png", "modules/v29_dialog_close.png",
                 "modules/v29_equip_btn.png", "modules/transfer_yes.png"}
    dissonant = {"dialogs/dissonant_header.png", "dialogs/dissonant_battle.png", "dialogs/dissonant_x.png", "dialogs/dissonant_tile_utility.png"}
    start_only = {"home/battle_btn.png", "home/game_stats_home.png"}
    assert (home - attached) == equipment | dissonant | set(perks.templates()) | start_only
    assert attached <= home                                    # nothing is asked for that an ordinary start does not ask for


def test_attached_requirements_drop_the_surrender_and_end_round_rows():
    config, body = cfg(), coin(max_wave=700)
    home, attached = paths(readiness.requirements(config, body)), paths(readiness.requirements(config, body, attached=True))
    assert {"home/surrender.png", "home/end_round_yes.png", "buttons/end_round.png"} <= home
    assert not {"home/surrender.png", "home/end_round_yes.png", "buttons/end_round.png"} & attached


def test_attached_requirements_keep_every_in_battle_row_exactly():
    config, body = cfg(), coin()
    home, attached = by_first(readiness.requirements(config, body)), by_first(readiness.requirements(config, body, attached=True))
    for rel in readiness.CORE:
        assert attached[rel] == home[rel] and attached[rel]["blocking"] is True
    in_battle = {"buttons/retry.png", "buttons/return_to_game.png", "buttons/nuke.png", "buttons/demon_mode.png", "floaters/second_wind.png",
                 "uw/black_hole.png", "uw/toggle_on.png", "uw/toggle_off.png", "stats/health.png", "stats/damage.png", "stats/max_label.png",
                 "icons/intro_sprint.png", readiness.WALL_BAR_REQUIREMENT, "icons/daily_missions.png", "home/tile_guild.png"}
    for rel in in_battle:
        assert attached[rel] == home[rel], rel
    changed = {rel for rel in attached if attached[rel] != home[rel]}
    assert changed == {BADGE}


def test_the_dropped_flow_images_are_exactly_the_home_side_ones():
    """buttons/retry.png stays (detect.death_screen needs it to see the death the takeover ends on); the BATTLE and HOME buttons go."""
    assert readiness.HOME_ONLY_FLOW_TEMPLATES == ("home/battle_btn.png", "home/game_stats_home.png")
    rows = paths(readiness.requirements(cfg(), coin(), attached=True))
    assert {"buttons/retry.png", "buttons/return_to_game.png"} <= rows and not set(readiness.HOME_ONLY_FLOW_TEMPLATES) & rows


def test_missing_in_battle_template_blocks(tmp_path):
    config, body = cfg(), coin()
    write_blocking(tmp_path, config, body, attached=True)
    write_image(tmp_path, config, BADGE)
    assert readiness.check(tmp_path, config, body, attached=True)["ready"] is True
    readiness.require(tmp_path, config, body, attached=True)
    for rel in ("screens/hdr_battle.png", "digits/4.png", "buttons/retry.png", "buttons/nuke.png", "floaters/second_wind.png", "uw/toggle_on.png",
                "stats/max_label.png", "buttons/return_to_game.png", "icons/daily_missions.png"):
        path = accounts.template_path(tmp_path, config, rel)
        saved = path.read_bytes()
        path.unlink()
        result = readiness.check(tmp_path, config, body, attached=True)
        assert result["ready"] is False and [r["alternatives"] for r in result["missing"]] == [[rel]], rel
        with pytest.raises(RuntimeError, match=rel):
            readiness.require(tmp_path, config, body, attached=True)
        path.write_bytes(saved)
    assert readiness.check(tmp_path, config, body, attached=True)["ready"] is True


def test_a_missing_home_only_image_does_not_block_an_attached_run(tmp_path):
    config, body = cfg(), coin()
    write_blocking(tmp_path, config, body, attached=True)
    write_image(tmp_path, config, BADGE)
    assert not accounts.template_path(tmp_path, config, "home/battle_btn.png").exists()
    assert readiness.check(tmp_path, config, body, attached=True)["ready"] is True
    assert readiness.check(tmp_path, config, body)["ready"] is False                 # an ordinary start still needs them


# ------------------------------------------------------------------ the tournament badge
def test_tournament_recognition_is_blocking(tmp_path):
    config, body = cfg(), coin()
    attached_row = by_first(readiness.requirements(config, body, attached=True))[BADGE]
    assert attached_row["blocking"] is True and attached_row["reasons"] == [readiness.TAKEOVER_TOURNAMENT_REASON]
    ordinary_row = by_first(readiness.requirements(config, body))[BADGE]
    assert ordinary_row["blocking"] is False and ordinary_row["reasons"] == [readiness.ADVISORY_REASON]
    write_blocking(tmp_path, config, body, attached=False)               # every image an ordinary start needs, which the badge is not
    result = readiness.check(tmp_path, config, body, attached=True)
    assert result["ready"] is False and [r["alternatives"] for r in result["missing"]] == [[BADGE]]
    assert result["missing"][0]["reasons"] == ["Takeover refuses to guess whether this is a tournament"]
    with pytest.raises(RuntimeError, match=BADGE):
        readiness.require(tmp_path, config, body, attached=True)
    assert readiness.check(tmp_path, config, body)["ready"] is True                  # an ordinary start never needed it
    assert BADGE in {rel for r in readiness.check(tmp_path, config, body)["advisory"] for rel in r["alternatives"]}
    write_image(tmp_path, config, BADGE)
    assert readiness.check(tmp_path, config, body, attached=True)["ready"] is True


def test_the_badge_stays_blocking_for_a_tournament_blueprint_too():
    row = by_first(readiness.requirements(cfg(), {"kind": "tournament", "gather": {}}, attached=True))[BADGE]
    assert row["blocking"] is True and readiness.TAKEOVER_TOURNAMENT_REASON in row["reasons"]


def test_the_badge_path_is_one_fact_between_the_readiness_row_and_the_screen_reader():
    assert readiness.IN_TOURNAMENT == screen.IN_TOURNAMENT_TEMPLATE == BADGE
    assert readiness.TAKEOVER_TOURNAMENT_REASON == "Takeover refuses to guess whether this is a tournament"


# ------------------------------------------------------------------ attached=False is the output it always was
SURRENDER_RULE = {"id": "p#0", "when": {"kind": "wave_at_least", "wave": 5000}, "do": {"kind": "surrender_retry"}}
RULE_VARIANTS = [
    [], [SURRENDER_RULE], [{"when": {"second_wind": {"state": "after_immunity"}}, "do": {"kind": "fire", "button": "nuke"}}],
    [{"when": {"wave": 1000}, "fire": "nuke"}], [{"outer": [{"inner": {"kind": "surrender_retry"}}]}],
    [{"when": {"kind": "fleet_mark"}, "do": {"kind": "burst", "button": "demon_mode"}}],
]


def matrix_bodies():
    """Bodies in every shape the function reads: each kind, every way to end a run, the Home entry options and the in-battle features."""
    out = []
    for kind, max_wave, rules in itertools.product((None, "coin", "tournament", "shard", "cycle_quest", "uw_grant_quest"), (None, 0, 700), RULE_VARIANTS):
        body = {"loadout": None, "gather": {}, "max_wave": max_wave, "rules": copy.deepcopy(rules)}
        if kind:
            body["kind"] = kind
        out.append(body)
    out += [
        coin(), coin(dissonant_tab=None), coin(restart_via_home=False), coin(dissonant_tab="utility", restart_via_home=False), coin(perk_bans=None),
        coin(perk_bans=[]), coin(loadout=None), coin(loadout="missing"), coin(abilities={}), coin(abilities={"nuke_below": 0.1}),
        coin(abilities={"dm_below": 0.1, "hold_until_second_wind": False}), coin(cancel_sprint=True), coin(uw_wanted={}), coin(shopping=[]),
        coin(shopping=[{"enabled": False, "stats": ["health"]}]), coin(gather={}), coin(gather={"ad_gems": False, "guild": False, "guild_store": True}),
        coin(global_behaviors={"demon_mode_always": True, "event_missions": True, "free_store_gems": True, "daily_missions": False, "guild_progress": True}),
        {"kind": "tournament", "loadout": "farm", "tournament_setup": True, "gather": {}}, {"tournament_setup": True}, {}, {"runner": "flows/shard.py"},
        {"kind": "coin", "gather": None, "shopping": None, "uw_wanted": None, "rules": None, "abilities": None},
    ]
    profile = load_golden()
    out += [playerprofile.compile_preset(profile, name) for name in profile["blueprints"]]
    return out


def test_unattached_output_is_the_output_it_always_was():
    """`requirements(cfg, body)` and `requirements(cfg, body, attached=False)` equal the pinned f047c55 function on every body of a wide synthetic matrix."""
    old = unchanged_module()
    bodies = matrix_bodies()
    assert len(bodies) > 120
    for config in (cfg(), cfg(wall_bar=False, loadouts={})):
        for body in bodies:
            want = old.requirements(config, copy.deepcopy(body))
            assert readiness.requirements(config, copy.deepcopy(body)) == want
            assert readiness.requirements(config, copy.deepcopy(body), attached=False) == want


def test_unattached_check_and_require_are_what_they_always_were(tmp_path):
    old = unchanged_module()
    config = cfg()
    bodies = [coin(), coin(max_wave=700), coin(dissonant_tab=None, perk_bans=None, loadout=None), {"kind": "tournament", "gather": {}},
              {"kind": "shard", "gather": {}}, {}]
    for number, body in enumerate(bodies):
        folder = tmp_path / f"case{number}"
        rows = old.requirements(config, body)
        for index, row in enumerate(rows):                  # about half the images exist, so `ready`, `missing` and `advisory` all vary
            if index % 2 == 0 and not row["alternatives"][0].startswith("config:"):
                write_image(folder, config, row["alternatives"][0])
        want = old.check(folder, config, copy.deepcopy(body))
        assert readiness.check(folder, config, copy.deepcopy(body)) == want
        assert readiness.check(folder, config, copy.deepcopy(body), attached=False) == want
        try:
            old.require(folder, config, copy.deepcopy(body))
            expected = None
        except RuntimeError as exc:
            expected = str(exc)
        for call in (lambda: readiness.require(folder, config, copy.deepcopy(body)), lambda: readiness.require(folder, config, copy.deepcopy(body), attached=False)):
            try:
                call()
                got = None
            except RuntimeError as exc:
                got = str(exc)
            assert got == expected


def test_the_pinned_copy_is_the_module_as_it_was():
    assert hashlib.sha256(PINNED.read_bytes()).hexdigest().upper() == PINNED_SHA256


def test_check_and_require_pass_attached_through_and_keep_the_old_call_shape(tmp_path, monkeypatch):
    """Callers and tests replace `requirements` and `check` with two- and three-argument functions: the unattached path must still call them that way."""
    calls = []
    monkeypatch.setattr(readiness, "requirements", lambda config, body: calls.append("unattached") or [])
    assert readiness.check(tmp_path, cfg(), {})["ready"] is True
    monkeypatch.setattr(readiness, "requirements", lambda config, body, attached=False: calls.append(("attached", attached)) or [])
    assert readiness.check(tmp_path, cfg(), {}, attached=True)["ready"] is True
    assert readiness.check(tmp_path, cfg(), {})["ready"] is True
    assert calls == ["unattached", ("attached", True), ("attached", False)]
    seen = []
    monkeypatch.setattr(readiness, "check", lambda root, config, body: seen.append("plain") or {"ready": True, "missing": []})
    readiness.require(tmp_path, cfg(), {})
    monkeypatch.setattr(readiness, "check", lambda root, config, body, attached=False: seen.append(("attached", attached)) or {"ready": True, "missing": []})
    readiness.require(tmp_path, cfg(), {}, attached=True)
    assert seen == ["plain", ("attached", True)]


def test_attached_is_keyword_only_for_check_and_require(tmp_path):
    with pytest.raises(TypeError):
        readiness.check(tmp_path, cfg(), {}, True)
    with pytest.raises(TypeError):
        readiness.require(tmp_path, cfg(), {}, True)


# ------------------------------------------------------------------ one answer to "does this end a battle"
def test_readiness_and_the_policy_agree_on_what_ends_a_battle():
    """Both read the walk and the kinds of player/battle_end.py: the surrender images are listed exactly for the presets the policy calls battle-ending."""
    for body in matrix_bodies():
        if body.get("max_wave") == 0 or (body.get("runner") and not body.get("kind")):
            continue                           # a cap of wave 0 (readiness: truthy, policy: not None) and a bare runner (readiness looks the kind up, the policy refuses) differ on purpose
        policy_says = any(r["code"] == "ends_battle" for r in attach_policy.compatibility(body))
        row_listed = "home/surrender.png" in paths(readiness.requirements(cfg(), body))
        kind = body.get("kind")
        if kind in (None, "coin", "tournament", *battle_end.SURRENDER_KINDS):
            assert policy_says == row_listed, body
