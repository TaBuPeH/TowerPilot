"""Unit tests for playerprofile.py. NO emulator, no adb, no screenshots.

The whole value of profiles is that a bad one is caught at startup instead of
at wave 1, so most of these tests assert on REFUSALS: the exact conditions the
schema says the compiler must not let through. The rest pin the compiled shape,
because orchestrator.py reads it by key and a renamed key is a silently dead rescue.
"""
import copy
import datetime
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from player import playerprofile as profile_mod
from player.playerprofile import ProfileError
from settings import CONFIG


# ------------------------------------------------------------------ fixtures

def _base_profile() -> dict:
    """A valid profile exercising every kind, both execution tiers and a
    shopping list. Tests deep-copy it and break one thing at a time."""
    return {
        "player": {
            "scanned_at": "2026-08-18T12:00:00",
            "uws": {"chain_lightning": True, "death_wave": True,
                    "golden_tower": True, "poison_swamp": True,
                    "black_hole": True, "spotlight": True,
                    "smart_missiles": False, "inner_land_mines": False,
                    "chronofield": False},
            "abilities": {"nuke": True, "demon_mode": True},
            # NOT implied by the loadouts - the migrator's `abilities` block is
            # inferred from config.yaml and has never looked at the account, so
            # ownership only counts once something has actually checked.
            "abilities_verified": True,
            "card_presets": ["main_farm", "tourney_p1", "disco", "no_card",
                             "18v300"],
            "guardians": ["ally", "attack", "bounty", "fetch", "scout",
                          "summon"],
            "modules_equipped": ["amplifying_strike", "sharp_fortitude",
                                 "black_hole_digestor", "multiverse_nexus",
                                 "space_displacer"],
            "modules_in_grid": ["galaxy_compressor", "primordial_collapse",
                                "dimension_core", "pulsar_harvester"],
            "wall": True,
            "max_tier": 19,
            # v29: the shipped loadouts select presets by name, so the base
            # account must own them (same names as profiles/default.yaml).
            "global_presets": ["Farm Run", "Tournament"],
            "category_presets": {"workshop": ["Preset 1", "Preset 2"],
                                 "modules": ["Farm", "Tourney"],
                                 "guardians": ["Farm", "Tourney"],
                                 "bots": ["Farm", "Tourney"]},
        },
        "blueprints": {
            "coin_default": {
                "kind": "coin",
                "loadout": "coin_farm",
                "tier": 14,
                "restart_via_home": True,
                "shop_interval_sec": 90,
                "shopping": "default_sweep",
                "policies": {"uw": "farm_cl_choreo", "rescue": "high_tier_wall",
                             "gather": "all_on"},
            },
            "shard_daily": {
                "kind": "shard",
                "loadout": "shard_farm",
                "tier": 18,
                "count": 100,
                "policies": {"gather": "gems_only"},
            },
            "tourney_main": {
                "kind": "tournament",
                "loadout": "tourney_1",
                "gem_entry_max": 10,
                "tier": None,
                "policies": {"uw": "tourney_cl",
                             "rescue": "tournament_any_falling",
                             "gather": "gems_only"},
            },
            "quest_ilm": {
                "kind": "cycle_quest",
                "loadout": "inner_land_mines_quest",
                "tier": 1,
                "cycle_sec": 25,
                "cycles": 40,
            },
        },
        "policies": {
            "uw_policies": {
                "farm_cl_choreo": {
                    "baseline": {"death_wave": True, "golden_tower": True,
                                 "poison_swamp": True, "black_hole": True,
                                 "spotlight": True},
                    "chain_lightning": {
                        "mode": "fleet_marks",
                        "always_on_above": [4080, 4120],
                        "pre_mark_waves": [5, 25],
                        "off_after_waves": [53, 72],
                    },
                },
                "tourney_cl": {
                    "baseline": {},
                    "chain_lightning": {"mode": "off_until_wave",
                                        "on_above": [500, 550]},
                },
                "always_cl": {
                    "baseline": {"golden_tower": True},
                    "chain_lightning": {"mode": "always_on"},
                },
                "no_cl": {
                    "baseline": {"golden_tower": True},
                    "chain_lightning": {"mode": "off"},
                },
            },
            "rescue_policies": {
                "high_tier_wall": {
                    "arm": {"on": "second_wind", "immunity_sec": None,
                            "watch_sec": 30},
                    "end_sprint_after_sw": False,
                    "rules": [
                        {"when": {"bar": "wall", "below": 0.02,
                                  "falling_samples": 2, "deadband": 0.01},
                         "do": {"burst": {"cancel_sprint": True,
                                          "fire": "demon_mode", "retaps": 3}}},
                        {"when": {"wall_collapse": {"from_above": 0.3}},
                         "do": {"burst": {"cancel_sprint": True,
                                          "fire": "demon_mode", "retaps": 3}}},
                        {"when": {"fleet_mark": {"after_waves": 3,
                                                 "window_waves": 60}},
                         "do": {"fire": {"button": "nuke",
                                         "throttle_sec": 5}}},
                        # Tier B: no flat scalar holds this one, and it is one
                        # of the combinations the P3 evaluator can actually
                        # execute (wave_at_least -> stop_after_run).
                        {"when": {"wave_at_least": 6000},
                         "do": {"stop_after_run": True}},
                    ],
                },
                "tournament_any_falling": {
                    "arm": {"on": "second_wind", "immunity_sec": 8,
                            "watch_sec": None},
                    "end_sprint_after_sw": False,
                    "rules": [
                        {"when": {"bar": "wall", "below": 1.0,
                                  "falling_samples": 2, "deadband": 0.01},
                         "do": {"burst": {"cancel_sprint": True,
                                          "fire": "demon_mode",
                                          "retaps": 3}}},
                    ],
                },
            },
            "gather": {
                "all_on": {"flying_gem": True, "gem_delay_sec": [3, 10],
                           "ad_gems": True, "quests_8h": True,
                           "quest_rewards": True, "guild": True},
                "gems_only": {"flying_gem": True, "gem_delay_sec": [3, 10],
                              "ad_gems": False, "quests_8h": False,
                              "quest_rewards": False, "guild": False},
            },
            "shopping_lists": {
                "default_sweep": {
                    "enabled": True,
                    "directives": [
                        {"enabled": True, "tab": "utility",
                         "stats": ["enemy_attack_level_skip",
                                   "enemy_health_level_skip"],
                         "mode": "repeat"},
                        {"enabled": False, "tab": "defense",
                         "stats": ["death_defy"], "mode": "once"},
                        {"enabled": True, "tab": "attack",
                         "stats": ["damage"], "mode": "clicks", "clicks": 3},
                    ],
                },
            },
        },
        "plan": {
            "week": {"default": "farm_day"},
            "days": {
                "farm_day": [
                    {"block": "shards", "blueprint": "shard_daily",
                     "after": "08:00", "count": 100},
                    {"block": "coin", "blueprint": "coin_default"},
                ],
            },
        },
        "_name": "unittest",
    }


@pytest.fixture
def prof():
    return _base_profile()


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    """Point the loader at a scratch directory - tests must never write into
    the user's real profiles/."""
    monkeypatch.setattr(profile_mod, "PROFILES_DIR", tmp_path)
    return tmp_path


def _write(profile_dir, name, body):
    (profile_dir / f"{name}.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


# ----------------------------------------------------------------- load()

def test_load_valid(profile_dir, prof):
    _write(profile_dir, "main", prof)
    loaded = profile_mod.load("main")
    assert loaded["_name"] == "main"
    assert loaded["player"]["max_tier"] == 19
    assert set(loaded["blueprints"]) == set(prof["blueprints"])
    assert profile_mod.validate(loaded) == []


def test_load_missing_raises(profile_dir):
    with pytest.raises(ProfileError, match="no such profile"):
        profile_mod.load("nope")


def test_load_bad_yaml_raises(profile_dir):
    (profile_dir / "broken.yaml").write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="not valid YAML"):
        profile_mod.load("broken")


def test_load_non_mapping_raises(profile_dir):
    (profile_dir / "listy.yaml").write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="mapping at the top level"):
        profile_mod.load("listy")


def test_base_profile_is_valid(prof):
    assert profile_mod.validate(prof) == []


# ------------------------------------------------- chain_lightning compile

def test_cl_mode_fleet_marks(prof):
    cl = profile_mod.compile_preset(prof, "coin_default")["chain_lightning"]
    assert cl == {"always_on": False, "always_on_above": [4080, 4120],
                  "pre_mark_waves": [5, 25], "off_after_waves": [53, 72]}


def test_cl_mode_off_until_wave_emits_no_mark_ranges(prof):
    """Codex #12: injecting the farm offsets here lights CL around the fleet
    mark at 2495 for any policy whose latch sits above it - `on_above: 5000`
    turned CL on at 2470, which is not "off until wave 5000". The P3
    cl_window() guard is what makes the nulls safe."""
    cl = profile_mod.compile_preset(prof, "tourney_main")["chain_lightning"]
    assert cl == {"always_on": False, "always_on_above": [500, 550],
                  "pre_mark_waves": None, "off_after_waves": None}


def test_off_until_wave_ignores_stray_mark_ranges(prof):
    """Even when the policy carries them (the migrator emits them), the mode
    has no mark choreography and must not inherit one."""
    pol = prof["policies"]["uw_policies"]["tourney_cl"]["chain_lightning"]
    pol["pre_mark_waves"] = [5, 25]
    pol["off_after_waves"] = [53, 72]
    cl = profile_mod.compile_preset(prof, "tourney_main")["chain_lightning"]
    assert cl["pre_mark_waves"] is None
    assert cl["off_after_waves"] is None


def test_only_fleet_marks_mode_emits_mark_ranges(prof):
    """The mark offsets exist for exactly one mode. Every other mode compiles
    them to None, so a guarded cl_window() can tell "no choreography" from a
    range it must roll."""
    for policy, expect_ranges in (("farm_cl_choreo", True),
                                  ("tourney_cl", False),
                                  ("always_cl", False),
                                  ("no_cl", False)):
        prof["blueprints"]["coin_default"]["policies"]["uw"] = policy
        cl = profile_mod.compile_preset(prof, "coin_default")["chain_lightning"]
        if expect_ranges:
            assert cl["pre_mark_waves"] == [5, 25]
            assert cl["off_after_waves"] == [53, 72]
        else:
            assert cl.get("pre_mark_waves") is None
            assert cl.get("off_after_waves") is None


def test_cl_mode_always_on(prof):
    prof["blueprints"]["coin_default"]["policies"]["uw"] = "always_cl"
    cl = profile_mod.compile_preset(prof, "coin_default")["chain_lightning"]
    assert cl == {"always_on": True}


def test_cl_mode_off(prof):
    prof["blueprints"]["coin_default"]["policies"]["uw"] = "no_cl"
    cl = profile_mod.compile_preset(prof, "coin_default")["chain_lightning"]
    assert cl == {"enabled": False, "always_on": False,
                  "always_on_above": None, "pre_mark_waves": None,
                  "off_after_waves": None}


def test_cl_absent_policy_is_off(prof):
    """A shard blueprint names no uw policy at all - that must read as OFF,
    not as an inherited farm choreography."""
    cl = profile_mod.compile_preset(prof, "shard_daily")["chain_lightning"]
    assert cl["enabled"] is False
    assert cl["always_on"] is False
    assert cl["always_on_above"] is None


# ------------------------------------------------------- Tier A / Tier B

def test_tier_a_absorbs_wall_collapse_and_fleet(prof):
    p = profile_mod.compile_preset(prof, "coin_default")
    ab = p["abilities"]
    assert ab["hold_until_second_wind"] is True
    assert ab["post_sw_watch_sec"] == 30
    assert ab["sw_immunity_sec"] is None
    assert ab["end_sprint_after_sw"] is False
    assert ab["rescue_bar"] == "wall"
    assert ab["dm_below"] == 0.02
    assert ab["falling_samples"] == 2
    assert ab["deadband"] == 0.01
    assert ab["collapse_from"] == 0.3
    assert ab["burst_cancel_sprint"] is True
    assert ab["burst_retaps"] == 3
    assert ab["nuke_on_fleet"] == {"after_waves": 3, "window_waves": 60,
                                   "throttle_sec": 5, "require_ready": True}
    assert ab["nuke_below"] is None


def test_tier_b_gets_the_leftovers(prof):
    """P4: a Tier B rule is a NORMALIZED dict, not the raw schema block - see
    the compiled-shape contract in profiles/SCHEMA.md."""
    p = profile_mod.compile_preset(prof, "coin_default")
    assert len(p["rules"]) == 1
    assert p["rules"][0] == {
        "id": "high_tier_wall#3",
        "when": {"kind": "wave_at_least", "wave": 6000},
        "do": {"kind": "stop_after_run"},
        "repeat": False,
        "refire_sec": 5.0,
        "latency": "main_loop",
        "requires": {"abilities": [], "wall": False, "card_presets": [],
                     "uws": []},
    }


def test_second_rule_of_a_taken_slot_spills_to_tier_b(prof):
    """Tier A has exactly one bar/burst slot; a second such rule spills to
    main-loop latency (and is therefore refused until P4 can run it)."""
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules.insert(1, {"when": {"bar": "wall", "below": 0.5},
                     "do": {"burst": {"fire": "demon_mode"}}})
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["dm_below"] == 0.02      # the FIRST one won the slot
    assert len(p["rules"]) == 2
    assert p["rules"][0]["when"] == {"kind": "bar", "bar": "wall",
                                     "below": 0.5, "falling_samples": 0,
                                     "deadband": 0.0}
    # P3 REFUSED THE SPILL; P4 runs it. What survives from that refusal is the
    # honesty about latency: the spilled rule is a main-loop rule, and the
    # one that took the slot is the sub-second rescue.
    assert profile_mod.validate(prof) == []
    assert p["rules"][0]["latency"] == "main_loop"


def test_bar_nuke_rule_fills_nuke_below(prof):
    """`nuke_below` IS THE hp BRANCH'S KEY (orchestrator.py reads it there only), so
    the Tier A nuke slot takes `bar: hp` and nothing else."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"bar": "hp", "below": 0.05},
         "do": {"fire": {"button": "nuke"}}},
    ]
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["nuke_below"] == 0.05
    assert ab["dm_below"] == 0.2


def test_arm_always_pushes_everything_to_tier_b(prof):
    """The flat scalars ARE the post-Second-Wind watch. With no Second Wind
    gate there is nowhere to hoist them, so every rule is Tier B - and the
    compiled shape must be the SAFE one: no rescue_bar, no threshold."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["arm"] = "always"
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["hold_until_second_wind"] is False
    assert p["abilities"]["dm_below"] is None
    assert p["abilities"]["rescue_bar"] is None
    assert len(p["rules"]) == 4


def test_arm_always_is_accepted_at_p4_even_over_wall_rules(prof):
    """P3 refused `arm: always` outright: every rule fell to an evaluator that
    could not run them. P4's interpreter runs the whole vocabulary, wall bar
    included, so the shape is live - what changes is LATENCY, not legality.
    The same policy armed is a sub-second rescue; unarmed it is ~1s
    observation, and the compiled `latency` field is what says which."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["arm"] = "always"
    # `retaps` is a Tier A param (the fast watch fires blind off one frame and
    # confirms after); moving the policy to the main loop means dropping it,
    # and the compiler says so rather than compiling a number nothing reads.
    for rule in pol["rules"]:
        if "burst" in rule["do"]:
            rule["do"]["burst"].pop("retaps", None)
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert len(p["rules"]) == 4
    assert all(r["latency"] == "main_loop" for r in p["rules"])
    assert p["abilities"]["rescue_bar"] is None      # ...and NO Tier A rescue


def test_no_rescue_policy_yields_default_abilities(prof):
    ab = profile_mod.compile_preset(prof, "shard_daily")["abilities"]
    assert ab["dm_below"] is None
    assert ab["nuke_on_fleet"] is None
    assert ab["hold_until_second_wind"] is False
    # the NEW scalars are always present so the watch can hoist them blind
    for key in ("falling_samples", "deadband", "collapse_from",
                "burst_cancel_sprint", "burst_retaps"):
        assert key in ab


def test_safety_net_scalars_are_always_explicit(prof):
    """Never None: the watch uses them as bare values, and a safety net that
    is invisible in the compiled dict is one nobody remembers is there."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [pol["rules"][0]]          # drop the wall_collapse rule
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["collapse_from"] == 0.3
    assert ab["falling_samples"] == 2
    assert ab["deadband"] == 0.01
    assert ab["burst_retaps"] == 3
    assert ab["burst_cancel_sprint"] is True


def test_refuse_armed_watch_with_no_dm_below(prof):
    """`arm.on: second_wind` opens the sub-second watch, which reads dm_below
    as a bare value every sample. Only a bar+burst rule fills it."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [r for r in pol["rules"] if "bar" not in r["when"]]
    problems = profile_mod.validate(prof)
    assert any("policies.rescue_policies.high_tier_wall" in p
               and "dm_below" in p for p in problems)
    # ...and it is unconstructible even if validate() is skipped
    with pytest.raises(ProfileError, match="dm_below"):
        profile_mod.compile_preset(prof, "coin_default")


def test_a_bar_nuke_rule_alone_does_not_satisfy_the_armed_watch(prof):
    """`fire: nuke` sets nuke_below, not dm_below - the watch is still empty."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [{"when": {"bar": "wall", "below": 0.05},
                     "do": {"fire": {"button": "nuke"}}}]
    assert any("dm_below" in p for p in profile_mod.validate(prof))


def test_rescue_less_blueprint_compiles_to_no_rescue_at_all(prof):
    """Codex #3: `rescue_bar: "wall"` with `dm_below: None` put a valid profile
    into a permanent 5s crash loop (`extent < None` -> TypeError -> blanket
    handler -> retry). No rescue policy must compile to NO rescue."""
    del prof["blueprints"]["coin_default"]["policies"]["rescue"]
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["rescue_bar"] is None
    assert ab["dm_below"] is None
    assert ab["hold_until_second_wind"] is False


def test_no_compiled_preset_pairs_a_null_threshold_with_a_bar(prof):
    """The invariant behind #3, over every blueprint and both rescue shapes."""
    variants = [prof]
    bare = copy.deepcopy(prof)
    del bare["blueprints"]["coin_default"]["policies"]["rescue"]
    variants.append(bare)
    for variant in variants:
        for name in variant["blueprints"]:
            ab = profile_mod.compile_preset(variant, name)["abilities"]
            if ab["rescue_bar"] is not None:
                assert ab["dm_below"] is not None, name
            else:
                assert ab["dm_below"] is None, name


# -------------------------------------------------- arm: always (P4 opens it)

def _tier_b_only(prof, rules) -> dict:
    """Point coin_default at an UNARMED policy - no Tier A at all, every rule
    evaluated by the main loop."""
    prof["policies"]["rescue_policies"]["high_tier_wall"] = {
        "arm": "always", "rules": rules}
    return prof


def test_arm_always_is_accepted_when_no_rule_touches_the_wall(prof):
    """P3 refused `arm: always` outright because every rule fell to an
    evaluator that could not run them. P4 can, so the shape is live - the
    Second Wind gate is now about the WALL WATCH, not about rules in general."""
    _tier_b_only(prof, [
        {"when": {"wave_at_least": 4000}, "do": {"fire": {"button": "nuke"}}},
        {"when": {"bar": "hp", "below": 0.15},
         "do": {"burst": {"fire": "demon_mode"}}},
    ])
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert len(p["rules"]) == 2
    # NO Tier A at all: no watch is armed, so nothing may name a bar or a
    # threshold in abilities{} - that pair is the crash loop from Codex #3.
    assert p["abilities"]["hold_until_second_wind"] is False
    assert p["abilities"]["rescue_bar"] is None
    assert p["abilities"]["dm_below"] is None


def test_arm_always_over_a_wall_rule_is_an_observation_not_a_rescue(prof):
    """Legal, and the compiled artefact is honest about what it is: no Tier A
    scalars at all, one main-loop rule. The Second Wind gate is what buys
    sub-second latency; without it the wall rule is a 1Hz observation."""
    _tier_b_only(prof, [{"when": {"bar": "wall", "below": 0.02},
                         "do": {"burst": {"fire": "demon_mode"}}}])
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["rescue_bar"] is None and p["abilities"]["dm_below"] \
        is None
    assert [r["latency"] for r in p["rules"]] == ["main_loop"]


# ------------------------------------------- per-rule ownership requirements

def test_every_tier_b_rule_carries_what_it_needs(prof):
    """Requirements travel WITH the rule: a compiled preset is installed into
    CONFIG, listed in the tray and launched hours later, long after the profile
    that justified it was read."""
    _tier_b_only(prof, [
        {"when": {"wave_at_least": 100},
         "do": {"burst": {"fire": "demon_mode"}}},
        # (was `switch_cards`, which named a card preset - it is refused
        # everywhere now: no verified route from a battle to the cards screen.
        # `toggle_uw` carries the other non-ability requirement.)
        {"when": {"wave_at_least": 200},
         "do": {"toggle_uw": {"weapon": "black_hole", "want_on": False}}},
        {"when": {"wave_at_least": 300}, "do": {"stop_after_run": True}},
    ])
    rules = profile_mod.compile_preset(prof, "coin_default")["rules"]
    assert [r["requires"] for r in rules] == [
        {"abilities": ["demon_mode"], "wall": False, "card_presets": [],
         "uws": []},
        {"abilities": [], "wall": False, "card_presets": [],
         "uws": ["black_hole"]},
        {"abilities": [], "wall": False, "card_presets": [], "uws": []},
    ]


def test_required_capabilities_covers_both_tiers(prof):
    """Tier A lives in abilities{} and Tier B in rules[] - the helper has to
    read both, because a runner only ever sees the compiled preset."""
    need = profile_mod.required_capabilities(
        profile_mod.compile_preset(prof, "coin_default"))
    assert need == {"abilities": ["demon_mode", "nuke"], "wall": True,
                    "card_presets": [], "uws": []}


def test_check_capabilities_catches_a_player_that_lost_a_capability(prof):
    """THE SPAWN-TIME RE-CHECK. validate() answered this when the profile was
    read; a scan can rewrite player.* between then and the launch."""
    compiled = profile_mod.compile_preset(prof, "coin_default")
    assert profile_mod.check_capabilities(compiled, prof["player"]) == []
    stripped = copy.deepcopy(prof["player"])
    stripped["abilities"]["demon_mode"] = False
    stripped["wall"] = False
    problems = profile_mod.check_capabilities(compiled, stripped)
    assert any("demon_mode" in p for p in problems)
    assert any("no wall" in p for p in problems)
    assert all("coin_default" in p for p in problems)   # names the blueprint


def test_check_capabilities_refuses_unverified_abilities(prof):
    compiled = profile_mod.compile_preset(prof, "coin_default")
    player = copy.deepcopy(prof["player"])
    player["abilities_verified"] = False
    assert any("unverified" in p
               for p in profile_mod.check_capabilities(compiled, player))


def test_check_capabilities_answers_for_a_preset_that_needs_nothing(prof):
    """A preset with no abilities and no rules requires nothing, so the honest
    answer is []. THE HELPER STILL ANSWERS THE QUESTION - it does not decide
    that this one is legacy and skip it; deciding what to ask about belongs to
    the caller."""
    assert profile_mod.check_capabilities({"label": "normal run"},
                                          prof["player"]) == []


def test_check_capabilities_never_decides_what_is_legacy(prof):
    """CODEX P4b (HIGH, BLOCKER). The runtime's "is this compiled" test is a
    `bp_` NAME or the `_source` stamp; this helper's was the stamp alone. A
    `bp_`-named body that had lost its stamp - a hand-edited CONFIG entry, a
    dashboard preview, a half-updated preset - therefore fell in the gap and
    returned [] with no profile bound: the one answer a spawn gate must never
    give. Two definitions of "legacy" is one too many, so this one has none."""
    stampless = profile_mod.compile_preset(prof, "coin_default")
    del stampless["_source"]              # ...but CONFIG would still name it
    assert stampless["abilities"]["dm_below"] is not None   # it DOES tap
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(profile_mod, "PROFILE", None)
        problems = profile_mod.check_capabilities(stampless)
        assert problems, "fail-open: a tapping preset passed unchecked"
        assert "no `player` section" in problems[0]
    # ...and the same body WITH an account to check against still answers []
    assert profile_mod.check_capabilities(stampless, prof["player"]) == []
    # ...and refuses when that account does not back it
    stripped = copy.deepcopy(prof["player"])
    stripped["abilities"]["demon_mode"] = False
    assert any("demon_mode" in p for p in
               profile_mod.check_capabilities(stampless, stripped))


def test_check_capabilities_fails_closed_with_no_player_to_check(prof,
                                                                 monkeypatch):
    """A GATE THAT CANNOT FIND THE EVIDENCE MUST REFUSE. It used to return []
    for a preset requiring an unowned Demon Mode simply because no profile was
    bound - which is the one answer a spawn gate must never give."""
    monkeypatch.setattr(profile_mod, "PROFILE", None)
    compiled = profile_mod.compile_preset(prof, "coin_default")
    problems = profile_mod.check_capabilities(compiled)
    assert len(problems) == 1
    assert "no `player` section" in problems[0]
    assert "coin_default" in problems[0]
    monkeypatch.setattr(profile_mod, "PROFILE", prof)
    assert profile_mod.check_capabilities(compiled) == []
    broken = copy.deepcopy(prof)
    broken["player"]["abilities"]["nuke"] = False
    monkeypatch.setattr(profile_mod, "PROFILE", broken)
    assert any("nuke" in p for p in profile_mod.check_capabilities(compiled))


def test_validate_refuses_what_check_capabilities_would_catch(prof):
    """The two must agree: anything the spawn-time check would report has to
    have been a validate() error first, or a profile could pass validation and
    then refuse to launch."""
    _tier_b_only(prof, [{"when": {"wave_at_least": 100},
                         "do": {"switch_cards": {"preset": "nope"}}}])
    problems = profile_mod.validate(prof)
    assert any("nope" in p and "card_presets" in p for p in problems)


def test_surrender_retry_is_refused_on_a_tournament_blueprint(prof):
    """HARD RULE: a tournament run is never cancelled - the entry is paid for
    and the next one costs more (10 -> 20 -> 30 gems)."""
    prof["policies"]["rescue_policies"]["tournament_any_falling"][
        "rules"].append({"when": {"wave_at_least": 100},
                         "do": {"surrender_retry": True}})
    problems = profile_mod.validate(prof)
    assert any("tourney_main" in p and "never cancelled" in p
               for p in problems)
    # ...and the same rule on a COIN blueprint is fine
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 100}, "do": {"surrender_retry": True}})
    assert not any("high_tier_wall" in p and "never cancelled" in p
                   for p in profile_mod.validate(prof))


def test_a_fully_composable_policy_compiles_end_to_end(prof):
    """The P4 headline: four different triggers, four different actions, both
    latencies, repeat and cooldowns - one policy, no Tier A at all, nothing
    refused and nothing dropped."""
    prof["policies"]["rescue_policies"]["composable"] = {
        "arm": "always",
        "rules": [
            {"when": {"bar": "hp", "below": 0.25, "falling_samples": 2,
                      "deadband": 0.02},
             "do": {"burst": {"fire": "demon_mode"}},
             "repeat": True, "refire_sec": 20},
            {"when": {"fleet_mark": {"after_waves": 2}},
             "do": {"fire": {"button": "nuke", "throttle_sec": 7,
                             "require_ready": True}},
             "repeat": True},
            {"when": {"wave_at_least": 4000},
             "do": {"toggle_uw": {"weapon": "black_hole", "want_on": False}}},
            {"when": {"death_screen": True}, "do": {"stop_after_run": True}},
        ]}
    prof["blueprints"]["coin_default"]["policies"]["rescue"] = "composable"
    assert profile_mod.validate(prof) == []
    rules = profile_mod.compile_preset(prof, "coin_default")["rules"]
    assert [r["id"] for r in rules] == [f"composable#{i}" for i in range(4)]
    assert [r["when"]["kind"] for r in rules] == [
        "bar", "fleet_mark", "wave_at_least", "death_screen"]
    assert [r["do"]["kind"] for r in rules] == [
        "burst", "fire", "toggle_uw", "stop_after_run"]
    assert [r["repeat"] for r in rules] == [True, True, False, False]
    assert [r["refire_sec"] for r in rules] == [20.0, 7.0, 5.0, 5.0]
    assert [r["latency"] for r in rules] == ["main_loop"] * 3 + \
        ["death_handler"]
    # EVERY key on EVERY rule, so the interpreter never calls .get(k, default)
    for rule in rules:
        assert set(rule) == {"id", "when", "do", "repeat", "refire_sec",
                             "latency", "requires"}
        assert "kind" in rule["when"] and "kind" in rule["do"]
        assert set(rule["requires"]) == {"abilities", "wall", "card_presets",
                                         "uws"}


# ------------------------------- the compiler is the only source of defaults

def _one_rule(prof, when, do, **extra) -> dict:
    """Compile a profile carrying exactly one Tier B rule and return it."""
    prof["policies"]["rescue_policies"]["high_tier_wall"] = dict(
        {"arm": "always", "rules": [dict({"when": when, "do": do}, **extra)]})
    return profile_mod.compile_preset(prof, "coin_default")["rules"][0]


def test_a_plain_threshold_compiles_zero_not_one(prof):
    """CODEX P4 REGRESSION (HIGH, defaults drift). The compiler said an
    unstated `falling_samples` was 1 while the runtime read a missing key as 0.
    A compiled `bar: hp, below: 0.3` rule therefore required a FALL, so a bar
    sitting still under its threshold never fired - reproduced as three passes
    below 0.3 with nothing happening, while the hand-written equivalent fired
    on the first. "hp is under 30%" is a level question; the direction question
    is what falling_samples asks, and nobody asked it here."""
    rule = _one_rule(prof, {"bar": "hp", "below": 0.3},
                     {"fire": {"button": "nuke"}})
    assert rule["when"]["falling_samples"] == 0
    assert rule["when"]["deadband"] == 0.0
    assert rule["when"]["below"] == 0.3


@pytest.mark.parametrize("when,do,keys", [
    ({"wave_at_least": 4000}, {"stop_after_run": True}, {"kind", "wave"}),
    ({"wave_between": [10, 20]}, {"cancel_sprint": True}, {"kind", "value"}),
    ({"bar": "hp", "below": 0.3}, {"cancel_sprint": True},
     {"kind", "bar", "below", "falling_samples", "deadband"}),
    ({"fleet_mark": {}}, {"fire": {"button": "nuke"}},
     {"kind", "after_waves", "window_waves"}),
    ({"second_wind": {"state": "open"}}, {"stop_after_run": True},
     {"kind", "state", "min_procs"}),
    ({"death_screen": True}, {"stop_after_run": True}, {"kind"}),
])
def test_every_compiled_trigger_param_is_explicit(prof, when, do, keys):
    """THE RUNTIME APPLIES NO DEFAULTS - absence is an admission error there -
    so every parameter has to be present with a real number, whether or not the
    profile stated it. One default in two places is a default that drifts."""
    rule = _one_rule(prof, when, do)
    assert set(rule["when"]) == keys
    for key, value in rule["when"].items():
        if key not in ("kind", "bar", "state"):
            assert isinstance(value, (int, float, list)), key
            assert not isinstance(value, bool), key


@pytest.mark.parametrize("do,keys", [
    ({"fire": {"button": "nuke"}}, {"kind", "button", "require_ready"}),
    ({"burst": {"fire": "demon_mode"}},
     {"kind", "button", "cancel_sprint", "require_ready"}),
    ({"toggle_uw": {"weapon": "black_hole"}}, {"kind", "weapon", "on"}),
    ({"cancel_sprint": True}, {"kind"}),
])
def test_every_compiled_action_param_is_explicit(prof, do, keys):
    rule = _one_rule(prof, {"wave_at_least": 100}, do)
    assert set(rule["do"]) == keys
    assert set(rule) == {"id", "when", "do", "repeat", "refire_sec",
                         "latency", "requires"}
    assert isinstance(rule["refire_sec"], float)


def test_refuse_retaps_on_a_tier_b_burst(prof):
    """Codex P4 (MEDIUM): the compiled `retaps` had no reader at this site -
    a main-loop burst fires through fire_button, which confirms its own tap -
    so a configured 5 silently became one fire."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 900},
         "do": {"burst": {"fire": "demon_mode", "retaps": 5}}})
    assert any("do.burst.retaps:" in p and "fire_button" in p
               for p in profile_mod.validate(prof))
    # ...and the Tier A burst keeps its retap loop
    assert profile_mod.compile_preset(
        prof, "coin_default")["abilities"]["burst_retaps"] == 3


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_refuse_non_finite_numbers(prof, bad):
    """Codex P4 (MEDIUM): `now < nan` is False forever (no cooldown at all)
    and `now < inf` is True forever (the rule never fires again). Neither
    raises, so neither is ever noticed."""
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules[-1]["refire_sec"] = bad
    assert any(".refire_sec:" in p for p in profile_mod.validate(prof))
    # ...and it is unconstructible even when validate() is skipped
    with pytest.raises(ProfileError, match="finite"):
        profile_mod.compile_preset(prof, "coin_default")


@pytest.mark.parametrize("when,do", [
    ({"bar": "hp", "below": float("nan")}, {"fire": {"button": "nuke"}}),
    ({"bar": "hp", "below": 0.3, "deadband": float("inf")},
     {"fire": {"button": "nuke"}}),
    ({"wave_between": [10, float("inf")]}, {"cancel_sprint": True}),
    ({"second_wind": {"state": "open", "min_procs": float("nan")}},
     {"stop_after_run": True}),
    ({"wave_at_least": 100},
     {"fire": {"button": "nuke", "throttle_sec": float("nan")}}),
])
def test_refuse_non_finite_trigger_and_action_params(prof, when, do):
    """Every numeric parameter, both tiers, at validation AND at compile."""
    prof["policies"]["rescue_policies"]["high_tier_wall"] = {
        "arm": "always", "rules": [{"when": when, "do": do}]}
    assert profile_mod.validate(prof) != []
    with pytest.raises(ProfileError, match="finite"):
        profile_mod.compile_preset(prof, "coin_default")


def test_refuse_sampling_scalars_on_a_threshold_nuke(prof):
    """Only the bar+`burst` rule fills the watch's falling_samples/deadband;
    on a bar+`fire: nuke` rule they would validate and then be dropped."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"bar": "hp", "below": 0.5, "deadband": 0.02},
         "do": {"fire": {"button": "nuke"}}},
    ]
    assert any("when.bar.deadband:" in p and "threshold-nuke" in p
               for p in profile_mod.validate(prof))


def test_a_non_finite_tier_a_threshold_is_unconstructible(prof):
    """Same lock on the fast path, where the number is compared 3 times a
    second: `ext < nan` is False on every sample - a rescue that never fires."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0][
        "when"]["below"] = float("nan")
    assert profile_mod.validate(prof) != []
    with pytest.raises(ProfileError, match="finite"):
        profile_mod.compile_preset(prof, "coin_default")


# ------------------------------------- the tournament lock, at compile time

def test_compile_refuses_a_tournament_surrender_without_validate(prof):
    """CODEX P4 (HIGH). The refusal lived only in validate(), and
    compile_preset() is reachable without it - the dashboard previews a
    blueprint, materialize() compiles every one. A forbidden rule sitting in
    CONFIG["presets"] is a forbidden rule the interpreter will be handed."""
    prof["policies"]["rescue_policies"]["tournament_any_falling"][
        "rules"].append({"when": {"wave_at_least": 100},
                         "do": {"surrender_retry": True}})
    with pytest.raises(ProfileError, match="TOURNAMENT"):
        profile_mod.compile_preset(prof, "tourney_main")
    # ...and the same policy on a coin blueprint still compiles
    prof["blueprints"]["coin_default"]["policies"]["rescue"] = \
        "tournament_any_falling"
    assert profile_mod.compile_preset(prof, "coin_default")["rules"]


def test_materialize_refuses_a_tournament_surrender(prof):
    """All-or-nothing: the whole install fails, so a forbidden rule cannot
    reach CONFIG even beside five innocent blueprints."""
    prof["policies"]["rescue_policies"]["tournament_any_falling"][
        "rules"].append({"when": {"wave_at_least": 100},
                         "do": {"surrender_retry": True}})
    before = dict(CONFIG["presets"])
    with pytest.raises(ProfileError, match="TOURNAMENT"):
        profile_mod.materialize(prof)
    assert set(CONFIG["presets"]) == set(before)


def test_rule_ids_are_stable_and_name_the_policy(prof):
    """The id is what the interpreter keys per-run state on and what the log
    shows. It must not move when an EARLIER rule is absorbed into Tier A."""
    a = profile_mod.compile_preset(prof, "coin_default")["rules"]
    assert [r["id"] for r in a] == ["high_tier_wall#3"]
    b = profile_mod.compile_preset(copy.deepcopy(prof), "coin_default")["rules"]
    assert [r["id"] for r in b] == [r["id"] for r in a]


# ------------------------------------------------------- other compile bits

def test_uw_wanted_filtered_to_owned(prof):
    prof["policies"]["uw_policies"]["farm_cl_choreo"]["baseline"][
        "smart_missiles"] = False
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["uw_wanted"] == {"death_wave": True, "golden_tower": True,
                              "poison_swamp": True, "black_hole": True,
                              "spotlight": True}
    assert "smart_missiles" not in p["uw_wanted"]
    assert "chain_lightning" not in p["uw_wanted"]   # driven dynamically


def test_gather_and_gem_delay(prof):
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["gem_delay_sec"] == [3, 10]
    assert p["gather"]["ad_gems"] is True
    q = profile_mod.compile_preset(prof, "tourney_main")
    assert q["gather"]["ad_gems"] is False


def test_tournament_keys(prof):
    p = profile_mod.compile_preset(prof, "tourney_main")
    assert p["tournament_setup"] is True
    assert p["gem_entry_max"] == 10
    assert p["runner"] is None and p["runner_args"] is None
    assert "tournament_setup" not in profile_mod.compile_preset(
        prof, "coin_default")


def test_runner_derivation(prof):
    assert profile_mod.compile_preset(prof, "coin_default")["runner"] is None
    shard = profile_mod.compile_preset(prof, "shard_daily")
    assert shard["runner"] == "flows/shard.py"
    assert shard["runner_args"] == ["--loops", "100", "--tier", "18"]
    ilm = profile_mod.compile_preset(prof, "quest_ilm")
    assert ilm["runner"] == "flows/quest_ilm.py"
    assert ilm["runner_args"] == ["--cycles", "40"]


def test_shard_count_none_means_forever(prof):
    prof["blueprints"]["shard_daily"]["count"] = None
    args = profile_mod.compile_preset(prof, "shard_daily")["runner_args"]
    assert args == ["--loops", "0", "--tier", "18"]


def test_shard_tier_flag_omitted_when_unset(prof):
    """`str(None)` would hand flows/shard.py the literal string 'None' to int()."""
    prof["blueprints"]["shard_daily"]["tier"] = None
    args = profile_mod.compile_preset(prof, "shard_daily")["runner_args"]
    assert args == ["--loops", "100"]
    assert "--tier" not in args


def test_quest_knobs_are_carried_into_the_body(prof):
    prof["blueprints"]["quest_sm"] = {
        "kind": "uw_grant_quest", "loadout": "coin_farm", "tier": 1,
        "grant_targets": ["smart_missiles"], "rides": 2,
        "reroll_at_wave": 1000, "ride_to_wave": 6500}
    sm = profile_mod.compile_preset(prof, "quest_sm")
    assert sm["rides"] == 2
    assert sm["runner_args"] == ["--rides", "2"]
    assert sm["ride_to_wave"] == 6500
    ilm = profile_mod.compile_preset(prof, "quest_ilm")
    assert ilm["cycles"] == 40
    assert ilm["cycle_sec"] == 25
    assert ilm["runner_args"] == ["--cycles", "40"]


def test_source_attestation(prof):
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["_source"] == {"profile": "unittest",
                            "blueprint": "coin_default"}


def test_compile_unknown_blueprint(prof):
    with pytest.raises(ProfileError, match="no such blueprint"):
        profile_mod.compile_preset(prof, "does_not_exist")


# --------------------------------------------------------------- shopping

def test_shopping_disabled_directive_dropped_and_flag_stripped(prof):
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["shopping"] == [
        {"tab": "utility",
         "stats": ["enemy_attack_level_skip", "enemy_health_level_skip"],
         "mode": "repeat"},
        {"tab": "attack", "stats": ["damage"], "mode": "clicks", "clicks": 3},
    ]
    assert all("enabled" not in d for d in p["shopping"])


def test_shopping_master_off_yields_empty_list(prof):
    prof["policies"]["shopping_lists"]["default_sweep"]["enabled"] = False
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["shopping"] == []
    assert profile_mod.validate(prof) == []


def test_shopping_legacy_bare_list_accepted(prof):
    prof["policies"]["shopping_lists"]["default_sweep"] = [
        {"tab": "utility", "stats": ["enemy_attack_level_skip"],
         "mode": "repeat"},
        {"tab": "defense", "stats": ["health", "health_regen"],
         "mode": "best_cost"},
    ]
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert len(p["shopping"]) == 2
    assert p["shopping"][0]["tab"] == "utility"


def test_shopping_unknown_stat_refused(prof):
    prof["policies"]["shopping_lists"]["default_sweep"]["directives"][0][
        "stats"] = ["enemy_attack_level_skip", "coin_bonus"]
    problems = profile_mod.validate(prof)
    assert any("coin_bonus" in p and "directives[0]" in p for p in problems)


def test_shopping_vocabulary_comes_from_templates():
    stats = profile_mod.shop_stats()
    assert "damage" in stats and "health_regen" in stats
    assert "max_label" not in stats          # a UI glyph, not a purchasable


def test_shopping_bad_tab_and_mode_refused(prof):
    d = prof["policies"]["shopping_lists"]["default_sweep"]["directives"][0]
    d["tab"] = "economy"
    d["mode"] = "spam"
    problems = profile_mod.validate(prof)
    assert any(".tab:" in p and "economy" in p for p in problems)
    assert any(".mode:" in p and "spam" in p for p in problems)


def test_shopping_clicks_required_for_clicks_mode(prof):
    d = prof["policies"]["shopping_lists"]["default_sweep"]["directives"][2]
    del d["clicks"]
    problems = profile_mod.validate(prof)
    assert any(".clicks: required" in p for p in problems)


def test_shopping_unknown_list_reference_refused(prof):
    prof["blueprints"]["coin_default"]["shopping"] = "nope_sweep"
    problems = profile_mod.validate(prof)
    assert any("blueprints.coin_default.shopping" in p and "nope_sweep" in p
               for p in problems)


# ------------------------------------------ Tier B vocabulary (P4 promotion)

@pytest.mark.parametrize("rule,when_kind,do_kind", [
    # THE THREE SHAPES P3 REFUSED WITH "not supported until P4". P4 is the
    # promotion, so each is now accepted, compiled and gated - never dropped.
    ({"when": {"death_screen": True}, "do": {"stop_after_run": True}},
     "death_screen", "stop_after_run"),
    ({"when": {"wave_at_least": 100}, "do": {"surrender_retry": True}},
     "wave_at_least", "surrender_retry"),
    ({"when": {"wave_at_least": 100},
      "do": {"burst": {"fire": "demon_mode"}}}, "wave_at_least", "burst"),
    # ...plus the rest of the vocabulary at main-loop latency.
    ({"when": {"fleet_mark": {"after_waves": 2, "window_waves": 30}},
      "do": {"fire": {"button": "nuke"}}}, "fleet_mark", "fire"),
    ({"when": {"wave_at_least": 900}, "do": {"cancel_sprint": True}},
     "wave_at_least", "cancel_sprint"),
    ({"when": {"wave_at_least": 900},
      "do": {"toggle_uw": {"weapon": "black_hole"}}},
     "wave_at_least", "toggle_uw"),
    # `switch_cards` is NOT here, and that is the P6 fix-round: it is the one
    # word in the vocabulary the runtime retires on sight, so the compiler
    # refuses it - see test_switch_cards_is_refused_in_every_phase.
])
def test_tier_b_vocabulary_is_accepted_and_compiled(prof, rule, when_kind,
                                                    do_kind):
    """Accepted AND compiled - the two halves are the same promise. A rule the
    validator blesses and the compiler drops is the accepted-but-ignored trap
    wearing the opposite hat."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"].append(rule)
    assert profile_mod.validate(prof) == []
    rules = profile_mod.compile_preset(prof, "coin_default")["rules"]
    compiled = rules[-1]
    assert compiled["when"]["kind"] == when_kind
    assert compiled["do"]["kind"] == do_kind
    assert compiled["id"] == f"high_tier_wall#{len(pol['rules']) - 1}"


@pytest.mark.parametrize("when", [
    {"wave_at_least": 900},
    {"second_wind": {"state": "closed"}},
    {"bar": "hp", "below": 0.25},
])
def test_switch_cards_is_refused_in_every_phase(prof, when):
    """P6 FIX-ROUND, THE OTHER HALF. `switch_cards` was the one word in the
    Tier B vocabulary the runtime retires ON SIGHT, in every phase, because
    loadout.apply_cards opens with a fixed, unconfirmable tap on the in-battle
    nav row and returns by polling for HOME - there is no verified route from a
    live battle to the cards screen. A compiler that kept accepting it would
    ship a rule that renders in the dashboard, reads as configured and never
    runs: the accepted-but-ignored shape this module exists to abolish.

    NO TIER A SLOT TAKES IT (they take `burst` and `fire`), so every
    switch_cards rule is a main-loop rule and this is the whole feature."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": when, "do": {"switch_cards": {"preset": "disco"}}})
    problems = profile_mod.validate(prof)
    assert any("do.switch_cards:" in p and "no verified route" in p
               and "orchestrator.run_in_run_actions" in p for p in problems), problems
    # ...and UNCONSTRUCTIBLE, not merely refused: compile_preset() is reached
    # without validate() by the dashboard's preview and by materialize().
    with pytest.raises(ProfileError) as e:
        profile_mod.compile_preset(prof, "coin_default")
    assert "switch_cards" in str(e.value) and "no verified route" in str(e.value)


def test_the_death_screen_refusal_is_the_one_that_speaks_first(prof):
    """A death-phase switch_cards rule keeps its OWN message - the death
    screen is a different obstacle one screen later, and it was refused first.
    Unchanged by the fix-round, and pinned so it stays that way."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"death_screen": True},
         "do": {"switch_cards": {"preset": "disco"}}})
    problems = profile_mod.validate(prof)
    assert any("cannot run on the death screen" in p for p in problems)
    # ...and not the main-loop one, which is what naming orchestrator.run_in_run_actions
    # identifies (both messages happen to contain the words "verified route" -
    # they are two statements of one obstacle, one screen apart).
    assert not any("orchestrator.run_in_run_actions" in p for p in problems), problems


def test_the_compiler_and_the_runtime_refuse_for_the_same_reason():
    """ONE REASON, WRITTEN DOWN ONCE. The refusal message points at
    orchestrator.run_in_run_actions rather than restating the evidence, so the two
    sides cannot drift into two different explanations of one obstacle."""
    assert "orchestrator.run_in_run_actions" in profile_mod.NO_CARDS_ROUTE
    assert "no verified route" in profile_mod.NO_CARDS_ROUTE
    # ...and the vocabulary still OFFERS both, marked - a dashboard that
    # hid them would be lying about what the schema supports.
    v = profile_mod.vocab()
    assert profile_mod.PENDING_ROUTE in \
        v["rule_actions"]["fields"]["switch_cards"]["doc"]
    assert profile_mod.PENDING_ROUTE in v["in_run_action_kinds"]["doc"]
    assert profile_mod.PENDING_ROUTE in \
        v["blueprint_fields"]["fields"]["tournament"]["fields"][
            "in_run_actions"]["doc"]


def test_wave_between_is_a_window_not_a_threshold(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_between": [1000, 2000]},
         "do": {"cancel_sprint": True}})
    assert profile_mod.validate(prof) == []
    rule = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert rule["when"] == {"kind": "wave_between", "value": [1000, 2000]}


@pytest.mark.parametrize("value", [1000, [1000], [1000, 2000, 3000],
                                   [2000, 1000], [1.5, 2]])
def test_refuse_a_window_that_is_not_two_ordered_integers(prof, value):
    """One number is a threshold and belongs to wave_at_least; three is a typo
    that would silently window the first two; a float crashes randint-shaped
    consumers and an inverted pair matches nothing."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_between": value}, "do": {"cancel_sprint": True}})
    assert any("wave_between" in p for p in profile_mod.validate(prof))


@pytest.mark.parametrize("state", ["open", "closed", "after_immunity", "any"])
def test_second_wind_states_compile(prof, state):
    """No new detection: each state is a question the RunState already
    answers, which is why there are exactly four."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"second_wind": {"state": state, "min_procs": 2}},
         "do": {"cancel_sprint": True}})
    assert profile_mod.validate(prof) == []
    rule = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert rule["when"] == {"kind": "second_wind", "state": state,
                            "min_procs": 2}


def test_second_wind_defaults_to_one_proc(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"second_wind": {"state": "closed"}},
         "do": {"stop_after_run": True}})
    rule = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert rule["when"]["min_procs"] == 1


@pytest.mark.parametrize("when,fragment", [
    ({"second_wind": {"state": "sideways"}}, "unknown Second Wind state"),
    ({"second_wind": {}}, "missing required parameter 'state'"),
    ({"second_wind": {"state": "open", "procs": 2}}, "unknown parameter"),
])
def test_refuse_bad_second_wind(prof, when, fragment):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": when, "do": {"stop_after_run": True}})
    assert any(fragment in p for p in profile_mod.validate(prof))


def test_toggle_uw_compiles_and_defaults_to_on(prof):
    """The UW panel is driven through shopper.uw_toggle, which verifies the
    pill after the tap - so the compiled action carries the weapon and the
    wanted state, never a coordinate."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    pol.append({"when": {"wave_at_least": 3000},
                "do": {"toggle_uw": {"weapon": "chain_lightning",
                                     "want_on": False}}})
    pol.append({"when": {"wave_at_least": 3500},
                "do": {"toggle_uw": {"weapon": "black_hole"}}})
    assert profile_mod.validate(prof) == []
    rules = profile_mod.compile_preset(prof, "coin_default")["rules"]
    assert rules[-2]["do"] == {"kind": "toggle_uw",
                               "weapon": "chain_lightning", "on": False}
    assert rules[-1]["do"] == {"kind": "toggle_uw", "weapon": "black_hole",
                               "on": True}
    assert rules[-1]["requires"]["uws"] == ["black_hole"]


def test_refuse_toggle_of_an_unowned_weapon(prof):
    """The panel has no row for a weapon the account does not own, so the
    sweep would scroll, find nothing and report success."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 3000},
         "do": {"toggle_uw": {"weapon": "smart_missiles"}}})
    assert any("smart_missiles" in p and "does not own" in p
               for p in profile_mod.validate(prof))
    # ...and the spawn-time re-check says the same thing about the compiled
    # artefact, which is what a runner actually holds.
    prof["player"]["uws"]["smart_missiles"] = True
    compiled = profile_mod.compile_preset(prof, "coin_default")
    prof["player"]["uws"]["smart_missiles"] = False
    assert any("smart_missiles" in p for p in
               profile_mod.check_capabilities(compiled, prof["player"]))


def test_the_yaml_on_key_trap_is_named_not_shrugged_at(prof):
    """YAML 1.1 reads a bare `on:` key as the BOOLEAN True, so `{weapon: x,
    on: false}` loads as `{weapon: x, True: False}` - the parameter vanishes
    and the toggle switches the weapon ON. The source key is `want_on` for
    that reason, and writing the trap gets a message that says so instead of
    'unknown parameter True', which sends the author hunting for a typo that
    is not there. (config.yaml quotes `'on':` in every arm block already.)"""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 3000},
         "do": {"toggle_uw": {"weapon": "black_hole", True: False}}})
    assert any("YAML read the key" in p and "want_on" in p
               for p in profile_mod.validate(prof))


def _schema_blocks() -> list[tuple[int, object]]:
    """(fence line number, parsed body) for EVERY ```yaml block in SCHEMA.md.

    Parsed from the file, never retyped here: a doc example that is copied
    into a test is a doc example that can drift from the file the player
    actually reads. Both of the ones that drifted were real traps, not typos.
    """
    path = Path(__file__).resolve().parents[1] / "profiles" / "SCHEMA.md"
    text = path.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"^```yaml\n(.*?)^```", text, re.S | re.M):
        line = text.count("\n", 0, m.start()) + 1
        out.append((line, yaml.safe_load(m.group(1))))
    return out


def _bool_keys(node, path="") -> list[str]:
    """Every mapping key YAML turned into a bool - the `on:`/`off:` trap."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, bool):
                found.append(f"{path} -> {key!r}")
            found += _bool_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _bool_keys(value, f"{path}[{i}]")
    return found


def test_schema_examples_parse_and_carry_no_yaml_traps():
    """EVERY yaml block in SCHEMA.md, parsed as a player would paste it.

    Two of them were carrying the trap this schema documents: the Tier B
    example wrote `on: false` (which the validator refuses) and the Tier A
    example wrote `arm: {on: second_wind}`, where YAML 1.1 turns the key into
    the boolean true - arm.on is then absent and the policy is SILENTLY
    UNARMED. A documented example is a thing people copy; it has to survive
    the same validator everything else does."""
    blocks = _schema_blocks()
    assert len(blocks) >= 8, "SCHEMA.md lost its examples"
    for line, body in blocks:
        assert not _bool_keys(body), f"SCHEMA.md:{line} has a YAML on/off trap"


def test_every_documented_rule_example_validates_and_compiles(prof):
    """...and the rule blocks go through the real validator and compiler.

    A block is either a bare list of rules (the composable half) or a
    `rescue_policies:` mapping; both are spliced into a real profile. Stub
    bodies with no `rules:` key are placeholders, not examples, and are
    skipped rather than guessed at."""
    seen = 0
    for line, body in _schema_blocks():
        policies = {}
        if isinstance(body, list) and body and isinstance(body[0], dict) \
                and "when" in body[0]:
            # a bare rule list: Tier B by construction, so `arm: always`
            policies[f"doc_{line}"] = {"arm": "always", "rules": body}
        elif isinstance(body, dict) and isinstance(
                body.get("rescue_policies"), dict):
            for name, pol in body["rescue_policies"].items():
                if isinstance(pol, dict) and pol.get("rules"):
                    policies[f"doc_{line}_{name}"] = pol
        for name, pol in policies.items():
            fresh = copy.deepcopy(prof)
            fresh["policies"]["rescue_policies"][name] = pol
            fresh["blueprints"]["coin_default"]["policies"]["rescue"] = name
            assert profile_mod.validate(fresh) == [], f"SCHEMA.md:{line}"
            compiled = profile_mod.compile_preset(fresh, "coin_default")
            assert compiled["abilities"] or compiled["rules"], f"line {line}"
            seen += 1
    assert seen >= 2, f"only {seen} documented rule example(s) exercised"


def test_the_documented_composable_example_compiles_as_written(prof):
    """The shape assertions the generic sweep cannot make: it is THIS block
    that pins `want_on` -> compiled `on`, the rule-level cooldown and repeat."""
    block = next(body for _, body in _schema_blocks()
                 if isinstance(body, list) and body
                 and "wave_between" in (body[0].get("when") or {}))
    prof["policies"]["rescue_policies"]["doc_example"] = {
        "arm": "always", "rules": block}
    prof["blueprints"]["coin_default"]["policies"]["rescue"] = "doc_example"
    assert profile_mod.validate(prof) == []
    rules = profile_mod.compile_preset(prof, "coin_default")["rules"]
    assert [r["do"]["kind"] for r in rules] == [
        "toggle_uw", "cancel_sprint", "burst", "stop_after_run"]
    assert rules[0]["do"]["on"] is False        # source `want_on`, compiled `on`
    assert rules[2]["refire_sec"] == 20.0 and rules[2]["repeat"] is True
    assert rules[3]["latency"] == "death_handler"


def test_the_yaml_arm_trap_is_named_not_shrugged_at(prof):
    """The trap that made the Tier A example wrong: an unquoted `on:` in `arm`
    leaves the policy unarmed, and "unknown key True" would send the author
    hunting for a typo that is not there."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["arm"] = {
        True: "second_wind", "watch_sec": 30}
    assert any("YAML read the key" in p and "'on'" in p
               for p in profile_mod.validate(prof))


def test_refuse_unknown_weapon_name(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 3000},
         "do": {"toggle_uw": {"weapon": "chain_lightening"}}})
    assert any("unknown ultimate weapon" in p
               for p in profile_mod.validate(prof))


def test_bar_hp_spills_to_tier_b_and_is_compiled(prof):
    """The hp bar has a main-loop reader (detect.hp_fill), so a SECOND hp rule
    - the Tier A slot already taken - runs at main-loop latency rather than
    being refused. Its thresholds are normalized, defaults included."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},          # Tier A bar_burst
        {"when": {"bar": "hp", "below": 0.35, "falling_samples": 3},
         "do": {"fire": {"button": "demon_mode"}}},         # ...spills to B
    ]
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["rescue_bar"] == "hp"
    # Unstated deadband is 0 at Tier B - a plain threshold, which is exactly
    # what the shipped main-loop evaluator did. Tier A's 0.01 belongs to the
    # 3Hz wall watch, not here.
    assert p["rules"][0]["when"] == {"kind": "bar", "bar": "hp",
                                     "below": 0.35, "falling_samples": 3,
                                     "deadband": 0.0}


@pytest.mark.parametrize("rule,kind", [
    ({"when": {"bar": "wall", "below": 0.3},
      "do": {"burst": {"fire": "demon_mode"}}}, "bar"),
    ({"when": {"wall_collapse": {"from_above": 0.5}},
      "do": {"burst": {"fire": "demon_mode"}}}, "wall_collapse"),
])
def test_wall_shapes_spill_to_tier_b_as_observations(prof, rule, kind):
    """Appended AFTER the golden rules, so both Tier A wall slots are taken and
    these spill to the main loop. THE SPILL IS NOT A DEMOTION TO NOTHING: the
    interpreter reads the wall for itself at ~1s. It is a demotion in LATENCY,
    which is exactly what `latency: main_loop` on the compiled rule records -
    a 1Hz wall rule cannot save a collapsing wall, and must not be mistaken
    for the Tier A rescue that can."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(rule)
    assert profile_mod.validate(prof) == []
    compiled = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert compiled["when"]["kind"] == kind
    assert compiled["latency"] == "main_loop"
    assert compiled["requires"]["wall"] is True


@pytest.mark.parametrize("do,why", [
    # No battlefield: no ability row, no sprint, no wall.
    ({"fire": {"button": "nuke"}}, "no ability row"),
    ({"burst": {"fire": "demon_mode"}}, "no ability row"),
    ({"cancel_sprint": True}, "no sprint button"),
    # ...and not Home either. These two USED to be accepted here, until the
    # runtime worker showed both are refused at the death phase: apply_cards
    # navigates from Home (which the stats dialog has no verified route to)
    # and abandon_run surrenders a LIVE battle (there is none left).
    ({"switch_cards": {"preset": "disco"}}, "navigates from HOME"),
    ({"surrender_retry": True}, "surrenders a LIVE battle"),
])
def test_refuse_every_death_screen_action_but_stop_after_run(prof, do, why):
    """The refusal names the OBSTACLE, not just the whitelist: an author who
    is told "only stop_after_run" learns nothing about where the card swap
    belongs (the between-run chores path)."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"death_screen": True}, "do": do})
    problems = profile_mod.validate(prof)
    assert any("cannot run on the death screen" in p and why in p
               for p in problems)


def test_the_one_death_screen_action_that_runs(prof):
    """`stop_after_run` writes the run flag and touches no screen at all -
    which is exactly why it is the only one left."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"death_screen": True}, "do": {"stop_after_run": True}})
    assert profile_mod.validate(prof) == []
    rule = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert rule["do"] == {"kind": "stop_after_run"}
    # WHERE it runs is part of the contract: the observe loop has already
    # exited by the time the death screen exists.
    assert rule["latency"] == "death_handler"


@pytest.mark.parametrize("value", [False, None, {}, 0])
def test_flag_trigger_must_be_exactly_true(prof, value):
    """The mirror of the flag-ACTION rule, and live now that death_screen is:
    `{death_screen: false}` reads as 'switched off' and compiles to a rule that
    fires on every death, because the trigger is recognised by its NAME."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"death_screen": value}, "do": {"stop_after_run": True}})
    problems = profile_mod.validate(prof)
    assert any("death_screen" in p and "exactly" in p for p in problems)
    with pytest.raises(ProfileError, match="exactly"):
        profile_mod.compile_preset(prof, "coin_default")


def test_refuse_require_match_on_a_tier_b_burst(prof):
    """`require_match` gates the WALL watch's fixed-coordinate fallback. A
    main-loop burst goes through fire_button, which has no fallback - so the
    flag would compile into a rule nothing consults."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 900},
         "do": {"burst": {"fire": "demon_mode", "require_match": True}}})
    assert any("do.burst.require_match:" in p
               and "not read by a main-loop (Tier B) burst" in p
               for p in profile_mod.validate(prof))


def test_tier_b_burst_takes_require_ready(prof):
    """...and the gate that IS live at this site is compiled."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 900},
         "do": {"burst": {"fire": "demon_mode", "require_ready": True,
                          "cancel_sprint": False}}})
    assert profile_mod.validate(prof) == []
    do = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]["do"]
    assert do == {"kind": "burst", "button": "demon_mode",
                  "cancel_sprint": False, "require_ready": True}


def test_tier_a_rules_are_not_refused(prof):
    """The same triggers are fine in Tier A - it is the main-loop evaluator
    that cannot run them, not the compiler."""
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["collapse_from"] == 0.3           # wall_collapse ran in Tier A
    assert ab["nuke_on_fleet"]["after_waves"] == 3


def test_supported_tier_b_combination_is_accepted(prof):
    """wave_at_least -> stop_after_run is what P3 actually ships."""
    assert profile_mod.validate(prof) == []
    assert profile_mod.compile_preset(prof, "coin_default")["rules"]


def test_bar_hp_is_the_one_supported_tier_b_bar(prof):
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["arm"] = {"on": "second_wind", "watch_sec": 30}
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"bar": "hp", "below": 0.5},
         "do": {"fire": {"button": "nuke"}}},
    ]
    # first rule takes the Tier A bar slot; the second is a Tier A nuke slot
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["rescue_bar"] == "hp"


def test_repeat_is_compiled_at_p4(prof):
    """P3 refused `repeat` because compilation dropped it and a repeating rule
    would silently become a one-shot. P4 compiles it, so it is accepted."""
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules[-1]["repeat"] = True
    assert profile_mod.validate(prof) == []
    compiled = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert compiled["repeat"] is True
    assert compiled["refire_sec"] == 5.0        # ...with a floor, always


def test_repeat_must_be_a_bool(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][-1][
        "repeat"] = "yes"
    assert any(".repeat:" in p and "true or false" in p
               for p in profile_mod.validate(prof))


def test_refire_sec_overrides_the_default(prof):
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules[-1].update({"repeat": True, "refire_sec": 45})
    assert profile_mod.validate(prof) == []
    compiled = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert compiled["refire_sec"] == 45.0


def test_tier_b_fire_timing_params_become_the_refire_floor(prof):
    """They are not dropped and they are not a second clock: at Tier B the
    rule's refire floor IS the throttle, exactly as the P3 evaluator read it."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 900},
         "do": {"fire": {"button": "nuke", "throttle_sec": 30}}})
    assert profile_mod.validate(prof) == []
    compiled = profile_mod.compile_preset(prof, "coin_default")["rules"][-1]
    assert compiled["refire_sec"] == 30.0
    assert compiled["do"] == {"kind": "fire", "button": "nuke",
                              "require_ready": False}


def test_refuse_two_spellings_of_one_cooldown(prof):
    """`refire_sec` and `do.fire.throttle_sec` compile to the same floor, so
    ranking them silently would drop whichever lost."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 900}, "refire_sec": 10,
         "do": {"fire": {"button": "nuke", "throttle_sec": 30}}})
    assert any(".refire_sec:" in p and "Keep one" in p
               for p in profile_mod.validate(prof))


@pytest.mark.parametrize("field,value", [("repeat", True), ("refire_sec", 30)])
def test_refuse_tier_b_rule_fields_on_a_tier_a_rule(prof, field, value):
    """The fast watch has no per-rule bookkeeping: it re-decides from hoisted
    scalars every sample. Both fields would compile to nothing there."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0][
        field] = value
    assert any(f".rules[0].{field}:" in p and "Tier A" in p
               for p in profile_mod.validate(prof))


def test_refuse_bad_refire_sec(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][-1][
        "refire_sec"] = -1
    assert any(".refire_sec:" in p and "positive number" in p
               for p in profile_mod.validate(prof))


@pytest.mark.parametrize("value", [False, None, {}, 0])
def test_flag_action_must_be_exactly_true(prof, value):
    """`{stop_after_run: false}` reads as 'this rule does nothing', but an
    evaluator dispatching on key presence stops the run anyway. `null` is the
    same hazard in disguise: YAML writes a valueless key as None, which looks
    deliberate and means nothing."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][-1][
        "do"] = {"stop_after_run": value}
    assert any("exactly" in p and "true" in p
               for p in profile_mod.validate(prof))
    with pytest.raises(ProfileError, match="exactly"):
        profile_mod.compile_preset(prof, "coin_default")


def test_flag_action_true_is_accepted(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][-1][
        "do"] = {"stop_after_run": True}
    assert profile_mod.validate(prof) == []


# ------------------------------- Tier A fire params are compiled, not dropped

def test_gate_defaults_match_all_four_legacy_call_sites(prof):
    """FOUR fire sites, not three - the burst has two. In _fast_wall_watch it
    is raw act.tap (orchestrator.py:500, no readiness exists, an unmatched glyph
    falls back to RESCUE_DM_PT); on the hp path the same rule becomes a real
    fire_button with require_ready=False (:800). Both nukes take the True
    default. One flat key would have to answer four different questions."""
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["burst_require_match"] is False      # orchestrator.py:500, wall burst
    assert ab["burst_require_ready"] is False      # orchestrator.py:800, hp burst
    assert ab["hp_nuke_require_ready"] is True     # orchestrator.py:806
    assert ab["nuke_on_fleet"]["require_ready"] is True   # orchestrator.py:455/737
    assert "require_ready" not in ab               # no flat key to conflate


def test_burst_gates_are_both_legal_and_independent(prof):
    """`require_ready` is back on a burst - it has a real home on the hp
    path - and it must not move the wall gate."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode", "require_ready": True}}},
    ]
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["burst_require_ready"] is True
    assert ab["burst_require_match"] is False


@pytest.mark.parametrize("bar,gate,live", [
    ("wall", "require_ready", "require_match"),
    ("hp", "require_match", "require_ready"),
])
def test_refuse_the_burst_gate_the_rescue_bar_does_not_use(prof, bar, gate,
                                                           live):
    """A wall rescue runs the burst as raw taps (no readiness exists); an hp
    rescue runs it through fire_button (no fallback coordinate exists). The
    gate your bar does not use compiles a flag nothing reads."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [{"when": {"bar": bar, "below": 0.2},
                     "do": {"burst": {"fire": "demon_mode", gate: True}}}]
    problems = profile_mod.validate(prof)
    assert any(f"do.burst.{gate}:" in p and "not read by a" in p
               and "require_ready gates the hp-path Demon Mode" in p
               for p in problems)
    # ...and the gate that IS live on that bar is accepted
    pol["rules"][0]["do"]["burst"] = {"fire": "demon_mode", live: True}
    assert profile_mod.validate(prof) == []


def test_per_site_gates_are_settable(prof):
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules[0]["do"]["burst"]["require_match"] = True
    rules[2]["do"]["fire"]["require_ready"] = False
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["burst_require_match"] is True
    assert ab["nuke_on_fleet"]["require_ready"] is False
    assert ab["hp_nuke_require_ready"] is True      # untouched by the others


def test_require_match_true_refuses_the_blind_fallback_tap(prof):
    """`burst_require_match: False` is today's behaviour - an unmatched icon
    falls back to the fixed Demon Mode coordinate. True is the knob to reach
    for on an account whose abilities are not confirmed."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0]["do"][
        "burst"]["require_match"] = True
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["burst_require_match"] is True


def test_require_match_must_be_bool(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0]["do"][
        "burst"]["require_match"] = "yes"
    assert any("require_match" in p for p in profile_mod.validate(prof))


def test_a_wall_bar_nuke_is_a_tier_b_rule_not_a_tier_a_one(prof):
    """`nuke_below` is read on the hp branch ONLY: a wall-bar rescue is handed
    wholesale to _fast_wall_watch, which fires Demon Mode via the burst and
    never looks at a nuke threshold. P3 refused the rule for that reason; P4
    gives it the reader it was missing - the main loop, which reads the wall
    itself - so it compiles to Tier B and the Tier A slot stays hp-only."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"bar": "wall", "below": 0.05},
         "do": {"fire": {"button": "nuke"}}})
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["nuke_below"] is None       # NOT the Tier A slot
    assert p["rules"][-1]["when"]["bar"] == "wall"
    assert p["rules"][-1]["do"] == {"kind": "fire", "button": "nuke",
                                    "require_ready": False}
    assert p["rules"][-1]["latency"] == "main_loop"


def test_wall_collapse_outside_a_wall_rescue_is_tier_b(prof):
    """`collapse_from` is hoisted by _fast_wall_watch, and only a WALL rescue
    enters that watch - so the Tier A collapse slot belongs to a wall policy
    and an hp policy's collapse rule is a main-loop rule instead of a refusal.
    It is a real reader either way, which is all the compiler promises."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"wall_collapse": {"from_above": 0.3}},
         "do": {"burst": {"fire": "demon_mode"}}},
    ]
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["rescue_bar"] == "hp"
    assert p["abilities"]["collapse_from"] == 0.3    # the untouched default
    assert p["rules"][0]["when"] == {"kind": "wall_collapse",
                                     "from_above": 0.3}
    assert p["rules"][0]["requires"]["wall"] is True


def test_a_wall_rule_still_needs_a_wall(prof):
    """The gate that does NOT move with the tier: no wall on the account means
    the ROI holds something else entirely, at 3Hz or at 1Hz."""
    prof["player"]["wall"] = False
    prof["policies"]["rescue_policies"]["high_tier_wall"]["arm"] = "always"
    assert any("no wall" in p for p in profile_mod.validate(prof))


def test_wall_collapse_is_accepted_on_a_wall_policy(prof):
    """The golden farm policy - unchanged."""
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["rescue_bar"] == "wall"
    assert ab["collapse_from"] == 0.3


def test_wall_collapse_with_no_bar_rule_at_all_is_tier_b(prof):
    """No bar rule means no Tier A rescue at all (rescue_bar null), so the
    collapse rule is a main-loop rule - and the armed-but-empty watch refusal
    is what keeps `arm` honest about it."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["arm"] = "always"
    pol["rules"] = [{"when": {"wall_collapse": {"from_above": 0.3}},
                     "do": {"burst": {"fire": "demon_mode"}}}]
    assert profile_mod.validate(prof) == []
    p = profile_mod.compile_preset(prof, "coin_default")
    assert p["abilities"]["rescue_bar"] is None
    assert len(p["rules"]) == 1


def test_hp_bar_nuke_is_still_accepted(prof):
    """The refusal is about the WALL bar, not about threshold nukes."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"bar": "hp", "below": 0.5},
         "do": {"fire": {"button": "nuke"}}},
    ]
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["nuke_below"] == 0.5


def test_fleet_mark_nuke_is_still_accepted(prof):
    """The other escape hatch the message points at."""
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["nuke_on_fleet"]["after_waves"] == 3


def test_hp_nuke_require_ready_is_settable(prof):
    """`nuke_below` is read only on the hp branch, so the key is named for the
    site that consumes it."""
    pol = prof["policies"]["rescue_policies"]["high_tier_wall"]
    pol["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"bar": "hp", "below": 0.5},
         "do": {"fire": {"button": "nuke", "require_ready": False}}},
    ]
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["rescue_bar"] == "hp"
    assert ab["nuke_below"] == 0.5
    assert ab["hp_nuke_require_ready"] is False
    assert ab["burst_require_match"] is False      # its own site, own default


def test_all_three_fire_sites_are_independent(prof):
    """Setting one must not move the other two - that is the whole reason the
    flat key was split."""
    base = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0]["do"][
        "burst"]["require_match"] = True
    moved = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert moved["burst_require_match"] != base["burst_require_match"]
    assert moved["hp_nuke_require_ready"] == base["hp_nuke_require_ready"]
    assert (moved["nuke_on_fleet"]["require_ready"]
            == base["nuke_on_fleet"]["require_ready"])


def test_a_later_tier_a_rule_cannot_clobber_an_earlier_one(prof):
    """The bar/burst and wall_collapse rules both carry burst params. Last-wins
    let a collapse rule silently rewrite the RESCUE's cancel/retap/ready
    behaviour, which is exactly the shared-global-scalars problem Codex
    flagged. First statement wins, so the compile is order-deterministic."""
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules[0]["do"]["burst"].update({"retaps": 7, "cancel_sprint": False,
                                    "require_match": True})
    rules[1]["do"]["burst"].update({"retaps": 1, "cancel_sprint": True,
                                    "require_match": False})
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["burst_retaps"] == 7            # the bar rule came first
    assert ab["burst_cancel_sprint"] is False
    assert ab["burst_require_match"] is True


def test_fleet_throttle_and_refire_guard_are_compiled(prof):
    rules = prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"]
    rules[2]["do"]["fire"]["throttle_sec"] = 12
    rules[2]["do"]["fire"]["refire_guard_sec"] = 40
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["nuke_on_fleet"]["throttle_sec"] == 12
    assert ab["refire_guard_sec"] == 40


def test_two_policies_that_differ_compile_differently(prof):
    """The whole point of #4: identical compiled output for different safety
    settings meant the setting was being dropped."""
    a = profile_mod.compile_preset(prof, "coin_default")
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][2]["do"][
        "fire"]["throttle_sec"] = 30
    b = profile_mod.compile_preset(prof, "coin_default")
    assert a["abilities"] != b["abilities"]
    assert profile_mod.compiled_hash(a) != profile_mod.compiled_hash(b)


def _hp_rescue(prof, nuke_fire: dict) -> None:
    """Point coin_default at an hp-only rescue whose nuke rule carries
    `nuke_fire`. The bar_nuke slot is only reachable on `bar: hp` now, and a
    wall rule cannot be mixed in beside it."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"] = [
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}},
        {"when": {"bar": "hp", "below": 0.5},
         "do": {"fire": dict({"button": "nuke"}, **nuke_fire)}},
    ]


def test_refuse_throttle_on_a_rescue_nuke(prof):
    """That site is rate-limited by can_fire()'s refire guard, not a throttle
    of its own, so there is nowhere to put one and accepting it would drop
    it."""
    _hp_rescue(prof, {"throttle_sec": 3})
    assert any("do.fire.throttle_sec:" in p and "dropped silently" in p
               for p in profile_mod.validate(prof))


def test_refire_guard_sec_is_homed_on_a_rescue_nuke(prof):
    """It has a flat home, so it is compiled rather than refused."""
    _hp_rescue(prof, {"refire_guard_sec": 22})
    assert profile_mod.validate(prof) == []
    ab = profile_mod.compile_preset(prof, "coin_default")["abilities"]
    assert ab["refire_guard_sec"] == 22


# ------------------------------------------- ability / inventory gating (#4)

def test_refuse_fire_of_an_unowned_ability(prof):
    """act.py taps Demon Mode at a FIXED COORDINATE when no glyph is found, so
    an unowned ability is a blind tap during a rescue, not a no-op."""
    prof["player"]["abilities"]["demon_mode"] = False
    problems = profile_mod.validate(prof)
    assert any("demon_mode" in p and "does not have" in p for p in problems)


def test_refuse_nuke_when_unowned(prof):
    prof["player"]["abilities"]["nuke"] = False
    assert any("'nuke'" in p and "does not have" in p
               for p in profile_mod.validate(prof))


def test_missing_player_abilities_section_is_a_refusal_not_a_permit(prof):
    del prof["player"]["abilities"]
    problems = profile_mod.validate(prof)
    assert any("player.abilities" in p and "cannot be verified" in p
               for p in problems)


def test_refuse_unverified_ability_ownership(prof):
    """Codex round 2 #1: the migrator writes `abilities: {nuke: true,
    demon_mode: true}` because every loadout in config.yaml implies them - it
    has never looked at the account. A fabricated `true` is indistinguishable
    from a scanned one, so ownership only counts once something has checked."""
    prof["player"]["abilities_verified"] = False
    problems = profile_mod.validate(prof)
    assert any("unverified" in p and "scan.py --battle" in p
               for p in problems)


def test_missing_abilities_verified_is_unverified(prof):
    """Absent must read as false, not as consent."""
    del prof["player"]["abilities_verified"]
    assert any("unverified" in p for p in profile_mod.validate(prof))


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_abilities_verified_must_be_the_boolean_true(prof, value):
    """A truthy string is how a hand-edited YAML quietly disarms the gate."""
    prof["player"]["abilities_verified"] = value
    assert any("unverified" in p for p in profile_mod.validate(prof))


def test_unverified_abilities_only_gate_rescue_blueprints(prof):
    """A shard or quest blueprint taps no rescue ability, so it must stay
    runnable on an unscanned account."""
    prof["player"]["abilities_verified"] = False
    for name in ("coin_default", "tourney_main"):
        del prof["blueprints"][name]
    prof["plan"]["days"]["farm_day"] = [
        {"block": "shards", "blueprint": "shard_daily"}]
    assert profile_mod.validate(prof) == []


def test_unreferenced_policy_abilities_are_not_gated(prof):
    """Only the abilities a blueprint's OWN rescue taps are required."""
    prof["policies"]["rescue_policies"]["unused"] = {
        "arm": {"on": "second_wind", "watch_sec": 30},
        "rules": [{"when": {"bar": "wall", "below": 0.1},
                   "do": {"burst": {"fire": "demon_mode"}}}]}
    assert profile_mod.validate(prof) == []


@pytest.mark.parametrize("key", ["card_presets", "guardians",
                                 "modules_equipped"])
def test_refuse_missing_ownership_inventory(prof, key, monkeypatch):
    """"Unknown, so permit" is backwards - an unscanned account is exactly
    where a wrong tap is most likely.

    v29 note: the shipped loadouts are preset-based, so the guardians and
    modules cases inject a manual body - the ownership check itself must
    keep firing for any future manual list."""
    manual = {"card_presets": {"cards": "farm_deck"},
              "guardians": {"guardians": ["fetch"]},
              "modules_equipped": {"modules": [["space_displacer", "primary"]],
                                   "module_preset": "Farm"}}
    if key in manual:
        patched = dict(CONFIG.get("loadouts") or {})
        patched["zz_manual"] = manual[key]
        monkeypatch.setitem(CONFIG, "loadouts", patched)
        prof["blueprints"]["quest_ilm"]["loadout"] = "zz_manual"
    del prof["player"][key]
    problems = profile_mod.validate(prof)
    assert any("scan.py" in p and key in p for p in problems)


# ------------------------------------------------- strict value shapes (#9)

@pytest.mark.parametrize("value", [None, 5, "3,10", [3], [3, 10, 20],
                                   ["3", "10"], [10, 3], [5.5, 25], [-5, 25],
                                   [True, 25]])
def test_refuse_malformed_cl_range(prof, value):
    """cl_window() does `random.randint(*range)` - every one of these raises
    inside the run instead of at startup. `[5.5, 25]` is the one Codex round 2
    caught: randint does not round, it raises 'non-integer arg 1'."""
    prof["policies"]["uw_policies"]["farm_cl_choreo"]["chain_lightning"][
        "pre_mark_waves"] = value
    assert any("pre_mark_waves" in p for p in profile_mod.validate(prof))


def test_float_cl_range_is_refused_before_randint_sees_it(prof):
    import random
    prof["policies"]["uw_policies"]["farm_cl_choreo"]["chain_lightning"][
        "always_on_above"] = [4080.5, 4120]
    assert any("always_on_above" in p and "integers" in p
               for p in profile_mod.validate(prof))
    with pytest.raises((ValueError, TypeError)):   # what the run would have hit
        random.randint(4080.5, 4120)


@pytest.mark.parametrize("value", [None, "3,10", [3], [1, 2, 3], [10, 3],
                                   [3.5, 10], [-1, 10], [False, 10]])
def test_refuse_malformed_gem_delay(prof, value):
    """Consumers take this off the `gather` dict and splat it the same way."""
    prof["policies"]["gather"]["all_on"]["gem_delay_sec"] = value
    assert any("gem_delay_sec" in p for p in profile_mod.validate(prof))


def test_every_compiled_cl_range_survives_randint(prof):
    """The invariant, end to end: whatever a compiled preset offers cl_window()
    must be something randint can actually splat."""
    import random
    for policy in ("farm_cl_choreo", "tourney_cl", "always_cl", "no_cl"):
        prof["blueprints"]["coin_default"]["policies"]["uw"] = policy
        cl = profile_mod.compile_preset(prof, "coin_default")["chain_lightning"]
        for key in ("always_on_above", "pre_mark_waves", "off_after_waves"):
            if cl.get(key) is not None:
                assert isinstance(random.randint(*cl[key]), int)


@pytest.mark.parametrize("field,value", [
    ("shop_interval_sec", 0), ("shop_interval_sec", "90"),
    ("shop_interval_sec", True), ("count", -1), ("max_wave", 0),
    ("tier", True), ("tier", 14.5), ("restart_via_home", "yes"),
])
def test_refuse_bad_blueprint_scalars(prof, field, value):
    prof["blueprints"]["coin_default"][field] = value
    assert any(f".{field}:" in p for p in profile_mod.validate(prof))


def test_bool_is_not_an_integer(prof):
    """`True == 1` in Python, so a naive int check would run tier 1."""
    prof["blueprints"]["coin_default"]["tier"] = True
    assert any(".tier:" in p for p in profile_mod.validate(prof))


@pytest.mark.parametrize("value", [40, -0.1, 1.5, "0.02"])
def test_refuse_threshold_outside_zero_to_one(prof, value):
    """40 is not 40% - it would fire the rescue on the first sample of the
    run, every run."""
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0][
        "when"]["below"] = value
    assert any("below" in p and "fraction" in p
               for p in profile_mod.validate(prof))


def test_refuse_bad_clicks_and_directive_keys(prof):
    d = prof["policies"]["shopping_lists"]["default_sweep"]["directives"][2]
    d["clicks"] = 0
    d["prioirty"] = 1
    problems = profile_mod.validate(prof)
    assert any(".clicks:" in p for p in problems)
    assert any("prioirty" in p and "unknown key" in p for p in problems)


@pytest.mark.parametrize("section", ["player", "blueprints", "policies"])
def test_refuse_missing_top_level_section(prof, section):
    del prof[section]
    assert any(p.startswith(f"{section}: required")
               for p in profile_mod.validate(prof))


def test_a_profile_without_a_plan_is_legal_and_installs_none(prof):
    """THE LEGACY LEG OF THE TRI-STATE. No plan section is a DECISION, not an
    omission: no compiled artefact, so the scheduler keeps its own constants.
    It is also what makes "remove the plan section" an answer a player can
    act on when the empty-plan refusal tells them to."""
    del prof["plan"]
    assert profile_mod.validate(prof) == []
    # NONE, not an empty week: an all-empty week is indistinguishable from a
    # plan that WAS authored and came out empty, which the runtime is right to
    # treat as a defect and hold on - so a rules-only profile idled the farm
    # instead of running the constants it never meant to replace (P5c row A).
    assert profile_mod.compile_plan(prof) is None
    before = dict(CONFIG["presets"])
    had_plan = "plan" in CONFIG
    CONFIG["plan"] = {"week": {"monday": [{"id": "stale#0"}]}}
    try:
        profile_mod.materialize(prof)
        # ...and a re-materialize that dropped the section drops the artefact,
        # so a scheduler can read `"plan" in CONFIG` as a complete answer.
        assert "plan" not in CONFIG
    finally:
        for key in list(CONFIG["presets"]):
            if key not in before:
                del CONFIG["presets"][key]
        if not had_plan:
            CONFIG.pop("plan", None)


def test_refuse_unknown_policy_section(prof):
    prof["policies"]["rescue_policy"] = {}
    assert any("policies.rescue_policy" in p and "unknown key" in p
               for p in profile_mod.validate(prof))


def test_refuse_unknown_gather_key(prof):
    prof["policies"]["gather"]["all_on"]["ad_gemz"] = True
    assert any("ad_gemz" in p for p in profile_mod.validate(prof))


def test_refuse_unknown_chain_lightning_key(prof):
    prof["policies"]["uw_policies"]["farm_cl_choreo"]["chain_lightning"][
        "always_on_bellow"] = 10
    assert any("always_on_bellow" in p for p in profile_mod.validate(prof))


def test_chores_are_a_known_policy_section(prof):
    prof["policies"]["chores"] = [{"name": "quest_scan", "enabled": True}]
    assert profile_mod.validate(prof) == []
    prof["policies"]["chores"] = [{"name": "quest_scan", "enabled": "yes"}]
    assert any("chores[0].enabled" in p for p in profile_mod.validate(prof))


# ------------------------------------------------ scope guards + warnings (#7)

def test_refuse_grant_target_other_than_smart_missiles(prof):
    """flows/quest_sm.py follows Smart-Missiles choreography and logs every grant as
    smart_missiles, so any other target reports success for a weapon it never
    farmed."""
    prof["blueprints"]["quest_sm"] = {
        "kind": "uw_grant_quest", "loadout": "coin_farm", "tier": 1,
        "grant_targets": ["inner_land_mines"], "rides": 1}
    problems = profile_mod.validate(prof)
    assert any("grant_targets" in p and "not supported until P4" in p
               for p in problems)


def test_smart_missiles_grant_target_is_accepted(prof):
    prof["blueprints"]["quest_sm"] = {
        "kind": "uw_grant_quest", "loadout": "coin_farm", "tier": 1,
        "grant_targets": ["smart_missiles"], "rides": 1}
    assert profile_mod.validate(prof) == []


@pytest.mark.parametrize("field,value", [
    ("cancel_sprint", True), ("cancel_sprint", False),
    ("max_wave", 5000), ("max_wave", None)])
def test_coin_p6_fields_are_accepted_now_that_they_have_readers(prof, field,
                                                                value):
    """WAS A REFUSAL until P6 (round 2 #5: accepted-but-inert is a trap for
    whoever trusts the dashboard). orchestrator.apply_cancel_sprint and
    orchestrator.max_wave_reached are the readers that made them ordinary fields, so
    the same four cases that used to be refused now validate - INCLUDING the
    null, which no longer means "unwired" but "no cap"."""
    prof["blueprints"]["coin_default"][field] = value
    assert profile_mod.validate(prof) == []


def test_coin_p6_fields_compile_explicitly_even_when_unstated(prof):
    """THE COMPILER IS THE ONLY SOURCE OF DEFAULTS. orchestrator reads both keys off
    the preset, so both are emitted on every coin preset whether the blueprint
    stated them or not - a `.get(key, <default>)` in the reader is the drift
    that put a compiled `bar` rule three passes under its threshold."""
    body = profile_mod.compile_preset(prof, "coin_default")
    assert body["cancel_sprint"] is False and body["max_wave"] is None
    prof["blueprints"]["coin_default"].update({"cancel_sprint": True,
                                               "max_wave": 5000})
    body = profile_mod.compile_preset(prof, "coin_default")
    assert body["cancel_sprint"] is True and body["max_wave"] == 5000
    # ...and NOWHERE ELSE. A shard or quest preset carrying a false
    # `cancel_sprint` would read as "considered and declined" for a runner
    # that never asks the question.
    for name in ("shard_daily", "quest_ilm", "tourney_main"):
        body = profile_mod.compile_preset(prof, name)
        assert "cancel_sprint" not in body and "max_wave" not in body


@pytest.mark.parametrize("field,value", [("max_wave", 0), ("max_wave", -1),
                                         ("max_wave", 3.5),
                                         ("max_wave", "3000"),
                                         ("cancel_sprint", "yes"),
                                         ("cancel_sprint", 1)])
def test_coin_p6_fields_still_have_shapes(prof, field, value):
    prof["blueprints"]["coin_default"][field] = value
    assert any(f".{field}:" in p for p in profile_mod.validate(prof))


@pytest.mark.parametrize("field", ["cancel_sprint", "max_wave"])
def test_coin_p6_fields_are_coin_only(prof, field):
    """The runner is what makes a field mean something: flows/shard.py has its own
    sprint handling and a tournament run is never surrendered at a wave cap."""
    prof["blueprints"]["tourney_main"][field] = 1000 if field == "max_wave" \
        else True
    assert any(f"tourney_main.{field}:" in p and "not a legal field" in p
               for p in profile_mod.validate(prof))


def test_shard_count_is_consumed_and_not_refused(prof):
    """`count` is real on a shard blueprint - it is what becomes `--loops`.
    The refusal is per KIND, not per field name."""
    assert prof["blueprints"]["shard_daily"]["count"] == 100
    assert profile_mod.validate(prof) == []
    assert profile_mod.compile_preset(
        prof, "shard_daily")["runner_args"] == ["--loops", "100",
                                                "--tier", "18"]


@pytest.mark.parametrize("bp_name,alternative", [
    ("coin_default", "PLAN BLOCK"),
    ("tourney_main", "one entry"),
    ("quest_sm", "`rides`"),
    ("quest_ilm", "`cycles`"),
])
def test_refuse_count_outside_shard(prof, bp_name, alternative):
    """`count` reaches only flows/shard.py's --loops. Everywhere else it sits in the
    profile looking like it bounds the run, and compiles to nothing."""
    prof["blueprints"]["quest_sm"] = {
        "kind": "uw_grant_quest", "loadout": "coin_farm", "tier": 1,
        "grant_targets": ["smart_missiles"], "rides": 1}
    prof["blueprints"][bp_name]["count"] = 5
    problems = profile_mod.validate(prof)
    assert any(f"blueprints.{bp_name}.count:" in p
               and "only consumed by shard blueprints" in p
               and alternative in p for p in problems)


def test_count_refusal_fires_on_presence_not_value(prof):
    prof["blueprints"]["coin_default"]["count"] = None
    assert any(".count:" in p and "only consumed by shard" in p
               for p in profile_mod.validate(prof))


def test_count_never_reaches_a_non_shard_compiled_preset(prof):
    """The invariant behind the refusal."""
    for name in prof["blueprints"]:
        body = profile_mod.compile_preset(prof, name)
        assert "count" not in body
        if body["runner"] == "flows/shard.py":
            assert "--loops" in body["runner_args"]


def test_burst_cancel_sprint_is_not_the_coin_blueprint_field(prof):
    """SAME WORD, THREE DIFFERENT THINGS, and they must never be wired to each
    other: `cancel_sprint` inside a burst action is a Tier A param (compiled
    to `abilities.burst_cancel_sprint`), `cancel_sprint` as a rule ACTION is a
    Tier B action, and `cancel_sprint` on a coin blueprint is the P6 run-start
    knob (compiled flat). The golden profile carries the first on every burst
    rule and does not state the third."""
    assert profile_mod.validate(prof) == []
    body = profile_mod.compile_preset(prof, "coin_default")
    assert body["abilities"]["burst_cancel_sprint"] is True
    assert body["cancel_sprint"] is False
    prof["blueprints"]["coin_default"]["cancel_sprint"] = True
    body = profile_mod.compile_preset(prof, "coin_default")
    assert body["abilities"]["burst_cancel_sprint"] is True
    assert body["cancel_sprint"] is True


def test_warnings_are_empty(prof):
    """Empty by construction now - the P5/P6 fields it used to carry are
    refusals, and a warning nobody must act on is one people scroll past.

    v29 postscript: empty again since 2026-08-27 - every shipped loadout is
    now preset-based (the ILM quest was the last manual body; Space
    Displacer turned out to live permanently in the Farm module preset), so
    the corruption advisory has nothing to flag. A future manual list
    without its `<cat>_preset` declaration would bring one back."""
    assert profile_mod.warnings(prof) == []


# --------------------------------------------------------- blueprint_kind()

def test_blueprint_kind(prof):
    """Runners call this to refuse work that is not theirs BEFORE they capture
    a frame - `flows/quest_sm.py --preset bp_tourney_main` would otherwise find a
    readable battle and surrender a live tournament."""
    assert profile_mod.blueprint_kind(prof, "tourney_main") == "tournament"
    assert profile_mod.blueprint_kind(prof, "bp_tourney_main") == "tournament"
    assert profile_mod.blueprint_kind(prof, "shard_daily") == "shard"
    assert profile_mod.blueprint_kind(prof, "nope") is None
    assert profile_mod.blueprint_kind(prof, "bp_nope") is None


# --------------------------------------------------------------- refusals

def test_unowned_uw_in_baseline_is_advisory_not_refusal(prof):
    """2026-08-29 (user ruling): presets name ANY ultimate weapon - they
    travel between accounts, and ownership only decides what applies. A
    `true` for an unowned weapon validates clean, compiles to nothing
    (_compile_uw_wanted drops it), and surfaces as a warnings() advisory."""
    prof["policies"]["uw_policies"]["farm_cl_choreo"]["baseline"][
        "smart_missiles"] = True
    assert not any("smart_missiles" in p for p in profile_mod.validate(prof))
    warns = profile_mod.warnings(prof)
    assert any("smart_missiles" in w and "does not own" in w for w in warns)
    compiled = profile_mod.compile_preset(prof, "coin_default")
    assert "smart_missiles" not in compiled["uw_wanted"]


def test_refuse_cl_policy_when_cl_unowned(prof):
    prof["player"]["uws"]["chain_lightning"] = False
    problems = profile_mod.validate(prof)
    assert any("chain_lightning" in p and "does not own" in p
               for p in problems)


def test_refuse_bar_wall_when_player_has_no_wall(prof):
    prof["player"]["wall"] = False
    problems = profile_mod.validate(prof)
    assert any("bar: wall" in p and "no wall" in p for p in problems)
    assert any("policies.rescue_policies.high_tier_wall" in p
               for p in problems)


def test_refuse_wall_and_hp_in_one_policy(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"bar": "hp", "below": 0.2},
         "do": {"burst": {"fire": "demon_mode"}}})
    problems = profile_mod.validate(prof)
    assert any("mixes" in p and "bar: hp" in p for p in problems)


def test_refuse_unknown_trigger(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0] = {
        "when": {"wall_wobble": {"below": 0.2}},
        "do": {"burst": {"fire": "demon_mode"}}}
    problems = profile_mod.validate(prof)
    assert any("no known trigger" in p and "rules[0]" in p for p in problems)


def test_refuse_unknown_action(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0]["do"] = {
        "panic": True}
    problems = profile_mod.validate(prof)
    assert any("no known action" in p for p in problems)


def test_refuse_missing_required_trigger_param(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][1] = {
        "when": {"wall_collapse": {}},
        "do": {"burst": {"fire": "demon_mode"}}}
    problems = profile_mod.validate(prof)
    assert any("missing required parameter 'from_above'" in p
               for p in problems)


def test_refuse_unknown_trigger_param(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0][
        "when"]["wobble"] = 3
    problems = profile_mod.validate(prof)
    assert any("unknown parameter 'wobble'" in p for p in problems)


def test_refuse_unknown_button(prof):
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][2]["do"] = {
        "fire": {"button": "ultimate_hug"}}
    problems = profile_mod.validate(prof)
    assert any("ultimate_hug" in p for p in problems)


def test_refuse_missing_loadout(prof):
    prof["blueprints"]["coin_default"]["loadout"] = "no_such_loadout"
    problems = profile_mod.validate(prof)
    assert any("blueprints.coin_default.loadout" in p
               and "no_such_loadout" in p for p in problems)


def test_loadout_as_is_equips_nothing(prof):
    """`as_is` is the one loadout value that is not a config.yaml key. It
    exists because `loadout` is required and every loadout in config.yaml
    names cards, guardians or modules - so a fresh or sandbox account cannot
    name one without a profile that lies about what it owns. It compiles to
    NULL, never to the word: the compiled key is a loadouts key, and a
    sentinel string there would be looked up by the first consumer that
    trusts the field."""
    prof["blueprints"]["coin_default"]["loadout"] = "as_is"
    assert profile_mod.validate(prof) == []
    assert profile_mod.compile_preset(prof, "coin_default")["loadout"] is None


@pytest.mark.parametrize("bp_name", ["shard_daily", "tourney_main",
                                     "quest_ilm"])
def test_refuse_as_is_where_the_runner_equips_something(prof, bp_name):
    """Coin only: orchestrator.py equips nothing on its own (the scheduler does it
    before handing over). Every other runner applies something - the
    tournament's three pre-battle swaps, the quest scripts' `.get("loadout")
    or <own default>` - so `as_is` there would be overridden rather than
    honoured, which is the opposite of what it says."""
    prof["blueprints"][bp_name]["loadout"] = "as_is"
    assert any(".loadout:" in p and "only legal on a coin blueprint" in p
               for p in profile_mod.validate(prof))


def test_missing_loadout_names_the_alternative(prof):
    """...and the required-key message points at it, so "I want to equip
    nothing" does not look like "the schema will not let me"."""
    del prof["blueprints"]["coin_default"]["loadout"]
    assert any(".loadout: required" in p and "as_is" in p
               for p in profile_mod.validate(prof))


def test_refuse_placeholder_loadout(prof):
    """config.yaml marks the unwritten quest loadouts `defined: false` - they
    equip nothing, so a blueprint pointing at one must not run."""
    assert CONFIG["loadouts"]["smart_missiles_quest"].get("defined") is False
    prof["blueprints"]["coin_default"]["loadout"] = "smart_missiles_quest"
    problems = profile_mod.validate(prof)
    assert any("placeholder" in p for p in problems)


def test_refuse_loadout_with_unowned_module(prof, monkeypatch):
    # v29: no shipped loadout hand-equips modules anymore - inject one so
    # the ownership check itself stays pinned for future manual bodies.
    patched = dict(CONFIG.get("loadouts") or {})
    patched["zz_manual_mod"] = {"modules": [["space_displacer", "primary"]]}
    monkeypatch.setitem(CONFIG, "loadouts", patched)
    prof["blueprints"]["quest_ilm"]["loadout"] = "zz_manual_mod"
    prof["player"]["modules_equipped"] = ["amplifying_strike"]
    prof["player"]["modules_in_grid"] = []
    problems = profile_mod.validate(prof)
    assert any("wants module" in p and "space_displacer" in p
               for p in problems)


def test_refuse_loadout_with_unowned_cards(prof, monkeypatch):
    patched = dict(CONFIG.get("loadouts") or {})
    patched["zz_cards"] = {"cards": "farm_deck"}
    monkeypatch.setitem(CONFIG, "loadouts", patched)
    prof["blueprints"]["quest_ilm"]["loadout"] = "zz_cards"
    prof["player"]["card_presets"] = ["other_deck"]
    problems = profile_mod.validate(prof)
    assert any("wants card preset" in p and "farm_deck" in p
               for p in problems)


def test_refuse_tier_above_max(prof):
    prof["blueprints"]["coin_default"]["tier"] = 25
    problems = profile_mod.validate(prof)
    assert any("above the player's unlocked maximum" in p for p in problems)


def test_refuse_unknown_kind(prof):
    prof["blueprints"]["coin_default"]["kind"] = "vibes"
    problems = profile_mod.validate(prof)
    assert any("blueprints.coin_default.kind" in p and "vibes" in p
               for p in problems)


def test_refuse_out_of_kind_field(prof):
    prof["blueprints"]["coin_default"]["cycle_sec"] = 25
    problems = profile_mod.validate(prof)
    assert any("cycle_sec" in p and "not a legal field" in p
               for p in problems)


def test_refuse_unknown_policy_reference(prof):
    prof["blueprints"]["coin_default"]["policies"]["rescue"] = "ghost"
    problems = profile_mod.validate(prof)
    assert any("policies.rescue" in p and "ghost" in p for p in problems)


def test_refuse_plan_block_of_the_wrong_kind(prof):
    """Codex round 2 #5: existence is not enough. A `tournament` block pointing
    at a shard blueprint validates, then farms shards at 19:00 on a
    Wednesday instead of entering the tournament."""
    prof["plan"]["days"]["farm_day"][0]["block"] = "tournament"
    problems = profile_mod.validate(prof)
    assert any("runs tournament blueprints" in p and "'shard'" in p
               for p in problems)


def test_quest_blocks_accept_either_quest_kind(prof):
    prof["blueprints"]["quest_ilm"]["cycles"] = 40
    prof["plan"]["days"]["farm_day"].insert(
        0, {"block": "quest_ilm", "blueprint": "quest_ilm"})
    assert profile_mod.validate(prof) == []


def test_refuse_unknown_plan_block_name(prof):
    prof["plan"]["days"]["farm_day"][0]["block"] = "gems"
    assert any("unknown block" in p for p in profile_mod.validate(prof))


def test_refuse_plan_block_without_a_name(prof):
    del prof["plan"]["days"]["farm_day"][0]["block"]
    assert any(".block: required" in p for p in profile_mod.validate(prof))


def test_refuse_plan_pointing_at_missing_blueprint(prof):
    prof["plan"]["days"]["farm_day"][0]["blueprint"] = "ghost_run"
    problems = profile_mod.validate(prof)
    assert any("plan.days.farm_day[0].blueprint" in p and "ghost_run" in p
               for p in problems)


# --------------------------------------- tournament in_run_actions (P6, v1)

def _with_in_run(prof, actions):
    prof["blueprints"]["tourney_main"]["in_run_actions"] = actions
    return prof


def test_a_non_empty_in_run_schedule_is_refused_at_both_gates(prof):
    """P6 FIX-ROUND. There is no verified route from a live battle to the cards
    screen: loadout.apply_cards opens with a fixed, unconfirmable tap on the
    in-battle nav row and returns by polling for HOME. The runtime refuses the
    schedule outright, so a compiler that still accepted one would ship the
    accepted-but-ignored shape - it would render in the dashboard, read as
    configured, and do nothing but write a refusal to the log once a run.

    BOTH GATES, because compile_preset() is reachable without validate(): the
    dashboard previews a blueprint and materialize() compiles every one."""
    _with_in_run(prof, [{"at_wave": 400, "switch_cards": "tourney_p1"},
                        {"at_wave": 1500, "switch_cards": "main_farm"}])
    problems = profile_mod.validate(prof)
    assert any("tourney_main.in_run_actions:" in p and "no verified route" in p
               and "orchestrator.run_in_run_actions" in p for p in problems), problems
    with pytest.raises(ProfileError) as e:
        profile_mod.compile_preset(prof, "tourney_main")
    assert "no verified route" in str(e.value)


def test_the_empty_in_run_schedule_stays_legal_and_still_compiles(prof):
    """THE KEY STAYS WIRED. `[]` is what "supported, schedules nothing" looks
    like and what every tournament preset compiles to today - keeping it
    accepted is what makes turning the feature on a change to one function
    rather than a format to re-add."""
    _with_in_run(prof, [])
    assert profile_mod.validate(prof) == []
    assert profile_mod.compile_preset(prof, "tourney_main")[
        "in_run_actions"] == []


def test_the_in_run_shape_is_still_diagnosed_under_the_refusal(prof):
    """An author who wrote a schedule gets it fully diagnosed alongside the
    refusal, so the list is correct on the day the route exists - the same
    ruling the old "not consumed until P6" refusal carried."""
    _with_in_run(prof, [{"at_wave": 400, "switch_cards": "ghost_deck"}])
    problems = profile_mod.validate(prof)
    assert any("no verified route" in p for p in problems)
    assert any("not on the account" in p for p in problems), problems


def test_the_compiled_in_run_shape_is_unchanged(prof):
    """THE CONTRACT WITH orchestrator.run_in_run_actions, still pinned while the
    feature is off: an ordered list of `{id, at_wave, switch_cards}`, where
    switch_cards is the preset NAME as a bare string - the runtime hands it
    straight to loadout.apply_cards, so a nested action dict there would be
    read as no preset at all. Built through the translator, which stays a pure
    translator: the REFUSAL lives at compile_preset, one level up, so the shape
    both sides agreed on can still be tested by both sides."""
    assert profile_mod._compile_in_run_actions(
        {"in_run_actions": [{"at_wave": 400, "switch_cards": "tourney_p1"},
                            {"at_wave": 1500, "switch_cards": "main_farm"}]}
    ) == [
        {"id": "in_run#0", "at_wave": 400, "switch_cards": "tourney_p1",
         "requires": {"abilities": [], "wall": False,
                      "card_presets": ["tourney_p1"], "uws": []}},
        {"id": "in_run#1", "at_wave": 1500, "switch_cards": "main_farm",
         "requires": {"abilities": [], "wall": False,
                      "card_presets": ["main_farm"], "uws": []}}]


def test_in_run_actions_compile_empty_on_every_tournament_preset(prof):
    """Explicit like every other compiled key: absent on the blueprint means
    `[]`, not a missing key the runtime has to have an opinion about."""
    assert profile_mod.compile_preset(prof, "tourney_main")["in_run_actions"] \
        == []
    # ...and NOWHERE ELSE: orchestrator refuses in_run_actions on a non-tournament
    # preset at runtime, and the compiler must not hand it one to refuse.
    for name in ("coin_default", "shard_daily", "quest_ilm"):
        assert "in_run_actions" not in profile_mod.compile_preset(prof, name)


def test_in_run_actions_are_tournament_only(prof):
    prof["blueprints"]["coin_default"]["in_run_actions"] = [
        {"at_wave": 400, "switch_cards": "tourney_p1"}]
    assert any("coin_default.in_run_actions:" in p and "not a legal field" in p
               for p in profile_mod.validate(prof))


def test_in_run_actions_feed_the_capability_gate(prof):
    """THE WHOLE REASON `requires` TRAVELS WITH THE ACTION. required_capabilities
    reads the COMPILED preset, and this preset has no rules at all - a gate
    that walked only `rules[]` would wave through the one card preset whose
    swap happens mid-run, on a paid ticket.

    Built through the translator now that a schedule cannot be compiled from a
    profile: the gate must still cover the shape, because the shape is what a
    compiled preset will carry the day the route is verified."""
    body = profile_mod.compile_preset(prof, "tourney_main")
    body["in_run_actions"] = profile_mod._compile_in_run_actions(
        {"in_run_actions": [{"at_wave": 400, "switch_cards": "tourney_p1"}]})
    assert body["rules"] == []
    assert profile_mod.required_capabilities(body)["card_presets"] == [
        "tourney_p1"]
    assert profile_mod.check_capabilities(body, prof["player"]) == []
    # ...and it refuses when the account stops backing it - the spawn-time
    # re-check, hours after validate() said yes.
    stripped = copy.deepcopy(prof["player"])
    stripped["card_presets"] = ["main_farm"]
    assert any("tourney_p1" in p for p in
               profile_mod.check_capabilities(body, stripped))
    del stripped["card_presets"]
    assert any("no `player.card_presets`" in p for p in
               profile_mod.check_capabilities(body, stripped))


@pytest.mark.parametrize("actions,needle", [
    ({"at_wave": 400}, "must be a LIST"),
    (["at_wave: 400"], "must be a mapping"),
    ([{"switch_cards": "tourney_p1"}], "at_wave: required"),
    ([{"at_wave": 400}], "switch_cards: required"),
    ([{"at_wave": 0, "switch_cards": "tourney_p1"}], "at_wave: must be an"),
    ([{"at_wave": 4.5, "switch_cards": "tourney_p1"}], "at_wave: must be an"),
    ([{"at_wave": 400, "switch_cards": "ghost_deck"}], "not on the account"),
    ([{"at_wave": 400, "switch_cards": 7}], "must be the NAME"),
    ([{"at_wave": 400, "switch_cards": "tourney_p1", "once": True}],
     "unknown key"),
])
def test_refuse_malformed_in_run_actions(prof, actions, needle):
    """Refuse everything else, LOUDLY: every message names the entry and the
    field, because this list is written by hand against a tournament that runs
    once a week."""
    _with_in_run(prof, actions)
    problems = profile_mod.validate(prof)
    assert any("in_run_actions" in p and needle in p for p in problems), \
        problems


def test_refuse_the_rules_spelling_by_name(prof):
    """`do:` is the RULES spelling one section up in the schema, so it is the
    mistake the schema itself sets up. Name it rather than reporting an
    unknown key and leaving the author to guess the shape."""
    _with_in_run(prof, [{"at_wave": 400,
                         "do": {"switch_cards": {"preset": "tourney_p1"}}}])
    problems = profile_mod.validate(prof)
    assert any("in_run_actions[0].do:" in p and "not rescue rules" in p
               and "switch_cards: <preset name>" in p for p in problems)


def test_refuse_out_of_order_in_run_actions(prof):
    """The runtime walks the list in order and fires one per pass, so a later
    action with an earlier wave is either dead or fires at the wrong wave."""
    for second in (400, 200):
        _with_in_run(prof, [{"at_wave": 400, "switch_cards": "tourney_p1"},
                            {"at_wave": second, "switch_cards": "main_farm"}])
        assert any("in_run_actions[1].at_wave" in p and "not above" in p
                   for p in profile_mod.validate(prof)), second


def test_refuse_more_in_run_actions_than_v1_takes(prof):
    _with_in_run(prof, [{"at_wave": 100 * i, "switch_cards": "tourney_p1"}
                        for i in range(1, profile_mod.IN_RUN_ACTIONS_MAX + 2)])
    assert any("in_run_actions:" in p and "at most" in p
               for p in profile_mod.validate(prof))


def test_in_run_action_ids_are_stable_across_compiles(prof):
    """The runtime keys "already fired" and "gave up on" on the id for the life
    of the run, and a restart mid-tournament recompiles the preset - an id that
    moved would replay a swap that already happened."""
    schedule = {"in_run_actions": [
        {"at_wave": 400, "switch_cards": "tourney_p1"},
        {"at_wave": 1500, "switch_cards": "main_farm"}]}
    first = profile_mod._compile_in_run_actions(schedule)
    again = profile_mod._compile_in_run_actions(schedule)
    assert [a["id"] for a in first] == ["in_run#0", "in_run#1"] == \
        [a["id"] for a in again]


def test_refuse_grant_target_already_owned(prof):
    prof["blueprints"]["quest_sm"] = {
        "kind": "uw_grant_quest", "loadout": "coin_farm", "tier": 1,
        "grant_targets": ["golden_tower"], "rides": 1}
    problems = profile_mod.validate(prof)
    assert any("already owns" in p for p in problems)


def test_validate_collects_every_problem_not_just_the_first(prof):
    prof["blueprints"]["coin_default"]["loadout"] = "ghost"
    prof["blueprints"]["coin_default"]["tier"] = 99
    prof["player"]["wall"] = False
    problems = profile_mod.validate(prof)
    assert len(problems) >= 3


# ------------------------------------------------------------- plan (P5)

def _plan(prof, days, week=None) -> dict:
    prof["plan"] = {"week": week or {"default": list(days)[0]}, "days": days}
    return prof


def test_plan_compiles_one_ordered_list_per_weekday(prof):
    """RESOLVED PER WEEKDAY: `week`/`days` is how a human avoids writing the
    same day seven times, and neither is something a scheduler should have to
    dereference on a poll."""
    plan = profile_mod.compile_plan(prof)
    assert list(plan["week"]) == list(profile_mod.WEEKDAYS)
    monday = plan["week"]["monday"]
    assert [b["id"] for b in monday] == ["monday#0", "monday#1"]
    assert [b["block"] for b in monday] == ["shards", "coin"]
    assert monday[0] == {
        "id": "monday#0", "day_plan": "farm_day", "block": "shards",
        "blueprint": "shard_daily", "preset": "bp_shard_daily",
        "kind": "shard", "after_min": 480, "until_min": 1440,
        "after": "08:00", "until": None, "count": 100,
    }


def test_plan_block_keys_are_all_explicit(prof):
    """THE RUNTIME APPLIES NO DEFAULTS - the same contract as the Tier B
    rules. An unstated `after` is 0 and an unstated `until` is end-of-day, in
    the compiled artefact, not in the reader."""
    filler = profile_mod.compile_plan(prof)["week"]["monday"][1]
    assert filler == {
        "id": "monday#1", "day_plan": "farm_day", "block": "coin",
        "blueprint": "coin_default", "preset": "bp_coin_default",
        "kind": "coin", "after_min": 0, "until_min": 1440,
        "after": None, "until": None, "count": None,
    }
    for day in profile_mod.compile_plan(prof)["week"].values():
        for block in day:
            assert set(block) == {"id", "day_plan", "block", "blueprint",
                                  "preset", "kind", "after_min", "until_min",
                                  "after", "until", "count"}
            assert isinstance(block["after_min"], int)
            assert isinstance(block["until_min"], int)


def test_the_clock_echo_can_never_disagree_with_the_minutes(prof):
    """`after`/`until` are the SOURCE ECHO - for logs, the dashboard, and a
    reader that would rather see a clock. The minutes are authoritative, and
    the two are compiled from one field in one place, so this pins them equal
    rather than trusting that they stay so."""
    _plan(prof, {"d": [{"block": "shards", "blueprint": "shard_daily",
                        "after": "08:30", "until": "19:45", "count": 5},
                       {"block": "coin", "blueprint": "coin_default"}]})
    for day in profile_mod.compile_plan(prof)["week"].values():
        for b in day:
            after = b["after"]
            until = b["until"]
            assert b["after_min"] == (0 if after is None else
                                      int(after[:2]) * 60 + int(after[3:]))
            assert b["until_min"] == (1440 if until is None else
                                      int(until[:2]) * 60 + int(until[3:]))


def test_week_default_fills_every_unnamed_day(prof):
    _plan(prof, {"farm": [{"block": "coin", "blueprint": "coin_default"}],
                 "tourney": [{"block": "tournament",
                              "blueprint": "tourney_main", "after": "19:00"},
                             {"block": "coin", "blueprint": "coin_default"}]},
          week={"default": "farm", "wednesday": "tourney"})
    assert profile_mod.validate(prof) == []
    plan = profile_mod.compile_plan(prof)
    assert [b["day_plan"] for b in plan["week"]["wednesday"]] == \
        ["tourney", "tourney"]
    assert [b["day_plan"] for b in plan["week"]["monday"]] == ["farm"]
    # the id names the WEEKDAY, not the day plan: it is the daystate key, and
    # two weekdays sharing a plan still count their runs separately.
    assert plan["week"]["thursday"][0]["id"] == "thursday#0"


def test_hhmm_compiles_to_minutes(prof):
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default",
                        "after": "08:30", "until": "23:45"}]})
    block = profile_mod.compile_plan(prof)["week"]["monday"][0]
    assert (block["after_min"], block["until_min"]) == (510, 1425)


def test_midnight_after_is_a_value_not_a_gap(prof):
    """`is None`, never `or`: 00:00 is a legitimate `after`."""
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default",
                        "after": "00:00", "until": "06:00"},
                       {"block": "coin", "blueprint": "coin_default"}]})
    assert profile_mod.validate(prof) == []
    first = profile_mod.compile_plan(prof)["week"]["monday"][0]
    assert first["after_min"] == 0 and first["until_min"] == 360


@pytest.mark.parametrize("value", ["8am", "08", "8:0", "25:00", "08:60",
                                   830, None])
def test_refuse_a_clock_that_is_not_a_clock(prof, value):
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default",
                        "after": value}]})
    if value is None:                   # unset is legal - it means midnight
        assert profile_mod.validate(prof) == []
        return
    assert any(".after:" in p for p in profile_mod.validate(prof))


def test_refuse_a_window_that_never_opens(prof):
    """A block that runs past midnight belongs to BOTH days, so it is written
    in both - a wrapping window would otherwise silently own no minute."""
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default",
                        "after": "19:00", "until": "08:00"}]})
    assert any("never opens" in p for p in profile_mod.validate(prof))


def test_refuse_unknown_week_and_block_keys(prof):
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default",
                        "at": "08:00"}]}, week={"default": "d",
                                                "caturday": "d"})
    problems = profile_mod.validate(prof)
    assert any(".at:" in p and "unknown key" in p for p in problems)
    assert any("plan.week.caturday" in p for p in problems)


def test_refuse_a_week_with_no_default_and_unnamed_days(prof):
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default"}]},
          week={"monday": "d"})
    assert any("no `default` day plan" in p
               for p in profile_mod.validate(prof))


def test_a_fully_named_week_needs_no_default(prof):
    days = {"d": [{"block": "coin", "blueprint": "coin_default"}]}
    _plan(prof, days, week={a: "d" for a in profile_mod.WEEK_KEYS[1:]})
    assert profile_mod.validate(prof) == []
    assert len(profile_mod.compile_plan(prof)["week"]["sunday"]) == 1


def test_refuse_an_empty_day_plan(prof):
    """One empty day among others is a per-day fault: the week still has
    blocks, so the message points at the day rather than at the plan."""
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default"}],
                 "e": []},
          week={"default": "d", "sunday": "e"})
    assert any("plan.days.e: empty" in p for p in profile_mod.validate(prof))


@pytest.mark.parametrize("plan", [
    {},                                             # `plan:` with nothing in it
    {"week": {"default": "d"}, "days": {}},         # no day plans
    {"week": {"default": "d"}, "days": {"d": []}},  # every day plan empty
    {"week": {}, "days": {"d": [{"block": "coin",
                                 "blueprint": "coin_default"}]}},
    {"week": {"monday": "nope"},                    # a reference to nothing
     "days": {"d": [{"block": "coin", "blueprint": "coin_default"}]}},
])
def test_refuse_a_plan_that_resolves_to_no_blocks(prof, plan):
    """THE THIRD STATE, and the only one nobody can act on: a missing plan
    means "use the constants" and a plan with blocks means "use the plan",
    while a plan that resolves to nothing means neither - a scheduler handed
    it either idles a tower all day or quietly falls back to constants the
    player thought they had replaced.

    Every spelling of nothing is the same nothing, so the check is the
    RESOLUTION rather than the shape - which is the only way to catch the
    week whose references point at day plans that are not there."""
    prof["plan"] = plan
    problems = profile_mod.validate(prof)
    assert any("plan: a plan with no blocks schedules nothing" in p
               and "remove the plan section" in p for p in problems)
    # ...and it is unconstructible even when validate() is skipped
    with pytest.raises(ProfileError, match="schedules nothing"):
        profile_mod.compile_plan(prof)


def test_compile_plan_returns_one_value_per_state_and_no_fourth(prof):
    """THE RETURN IS THE TRI-STATE. One value per state, and an empty week is
    not one of them:

        no plan section        -> None
        plan resolving to none -> ProfileError
        plan with blocks       -> the populated week

    That is the whole invariant the runtime leans on. A caller holding a dict
    knows every day was resolved; a caller holding None knows there was
    nothing to resolve; and neither has to guess which of the two an empty
    week meant."""
    # (1) absence
    absent = copy.deepcopy(prof)
    del absent["plan"]
    assert profile_mod.validate(absent) == []
    assert profile_mod.compile_plan(absent) is None
    # (2) authored, but empty
    empty = copy.deepcopy(prof)
    empty["plan"] = {"week": {"default": "d"}, "days": {"d": []}}
    with pytest.raises(ProfileError, match="schedules nothing"):
        profile_mod.compile_plan(empty)
    # (3) populated - and NEVER an empty week, on any validating profile
    variants = [prof]
    one_day = copy.deepcopy(prof)
    _plan(one_day, {"d": [{"block": "coin", "blueprint": "coin_default"}],
                    "e": [{"block": "coin", "blueprint": "coin_default"}]},
          week={"default": "d", "wednesday": "e"})
    variants.append(one_day)
    for variant in variants:
        assert profile_mod.validate(variant) == []
        compiled = profile_mod.compile_plan(variant)
        assert compiled is not None
        week = compiled["week"]
        assert any(week.values()), "a validating plan compiled to nothing"
        assert all(isinstance(blocks, list) for blocks in week.values())


def test_plan_sim_says_so_for_a_rules_only_profile(prof, profile_dir, capsys):
    """The other caller of compile_plan. It assumed a dict, and a rules-only
    profile would have crashed it on `plan["week"]` - so the tri-state is
    exercised through the tool, not only through the function."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import plan_sim
    del prof["plan"]
    _write(profile_dir, "rules_only", prof)
    rc = plan_sim.walk("rules_only", datetime.date(2026, 8, 17), set(), False)
    assert rc == 0
    assert "no `plan` section" in capsys.readouterr().out


def test_plan_count_is_legal_on_every_kind(prof):
    """The PLAN-level `count` is a different field from the blueprint-level
    one (that is flows/shard.py's --loops). Here it means "runs of this block per
    day", persisted per block id so an aborted day resumes - which is what
    lets a plan say "100 shard runs, then coin for the rest of the day"."""
    _plan(prof, {"d": [{"block": "shards", "blueprint": "shard_daily",
                        "after": "08:00", "count": 100},
                       {"block": "coin", "blueprint": "coin_default",
                        "count": 3},
                       {"block": "coin", "blueprint": "coin_default"}]})
    assert profile_mod.validate(prof) == []
    counts = [b["count"] for b in profile_mod.compile_plan(prof)["week"]["monday"]]
    assert counts == [100, 3, None]


def test_a_tournament_block_is_one_entry_a_day(prof):
    """Not a default anyone may raise: the ticket purchase auto-starts the run
    and the gem cost escalates 10 -> 20 -> 30."""
    _plan(prof, {"d": [{"block": "tournament", "blueprint": "tourney_main",
                        "after": "19:00", "count": 2},
                       {"block": "coin", "blueprint": "coin_default"}]})
    assert any(".count:" in p and "10 -> 20 -> 30" in p
               for p in profile_mod.validate(prof))
    # ...and an unstated count compiles to 1 rather than to "unbounded"
    prof["plan"]["days"]["d"][0].pop("count")
    assert profile_mod.validate(prof) == []
    assert profile_mod.compile_plan(prof)["week"]["monday"][0]["count"] == 1


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "100"])
def test_refuse_a_count_that_is_not_a_count(prof, value):
    _plan(prof, {"d": [{"block": "shards", "blueprint": "shard_daily",
                        "count": value},
                       {"block": "coin", "blueprint": "coin_default"}]})
    assert any(".count:" in p for p in profile_mod.validate(prof))


def test_refuse_two_tournament_blocks_in_one_day(prof):
    """CODEX P5 (CRITICAL). Capping `count` at 1 caps each BLOCK on its own -
    two tournament blocks in a day are two paid entries, and the second costs
    more than the first (10 -> 20 -> 30). The cap has to be counted over the
    day, and it is unconstructible rather than merely invalid: compile_plan()
    is reachable without validate()."""
    _plan(prof, {"d": [{"block": "tournament", "blueprint": "tourney_main",
                        "after": "19:00"},
                       {"block": "tournament", "blueprint": "tourney_main",
                        "after": "21:00"},
                       {"block": "coin", "blueprint": "coin_default"}]})
    assert any("2 tournament blocks in one day" in p and "10 -> 20 -> 30" in p
               for p in profile_mod.validate(prof))
    with pytest.raises(ProfileError, match="tournament blocks"):
        profile_mod.compile_plan(prof)


def test_the_tournament_cap_is_per_day_not_per_week(prof):
    """One a day on every day of the week is fine - it is one PER DAY that is
    capped, and Wednesday's entry is not Saturday's."""
    _plan(prof, {"d": [{"block": "tournament", "blueprint": "tourney_main",
                        "after": "19:00"},
                       {"block": "coin", "blueprint": "coin_default"}]})
    assert profile_mod.validate(prof) == []
    week = profile_mod.compile_plan(prof)["week"]
    assert all(w[0]["kind"] == "tournament" for w in week.values())
    assert len({w[0]["id"] for w in week.values()}) == 7


def test_two_tournament_blocks_are_refused_even_on_one_weekday(prof):
    """The day that carries them is named explicitly rather than defaulted -
    the compile-time refusal names the WEEKDAY ids, which is what a reader
    needs to find them."""
    _plan(prof, {"quiet": [{"block": "coin", "blueprint": "coin_default"}],
                 "double": [{"block": "tournament",
                             "blueprint": "tourney_main", "after": "19:00"},
                            {"block": "tournament",
                             "blueprint": "tourney_main", "after": "21:00"},
                            {"block": "coin", "blueprint": "coin_default"}]},
          week={"default": "quiet", "saturday": "double"})
    with pytest.raises(ProfileError, match="saturday#0, saturday#1"):
        profile_mod.compile_plan(prof)


@pytest.mark.parametrize("alias", ["mon", "wed", "sat", 0])
def test_refuse_weekday_aliases_in_the_source(prof, alias):
    """ONE SPELLING PER DAY (Codex P5, LOW - ruled). A profile that could
    carry both `wednesday:` and `wed:` needs a precedence rule nobody would
    ever read, guarding a collision with no right answer. Refusing the alias
    makes the collision unconstructible."""
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default"}]},
          week={"default": "d", alias: "d"})
    assert any(f"plan.week.{alias}" in p and "unknown key" in p
               for p in profile_mod.validate(prof))


def test_canonical_weekday_names_are_the_spelling(prof):
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default"}],
                 "e": [{"block": "coin", "blueprint": "coin_default"}]},
          week={"default": "d", "wednesday": "e"})
    assert profile_mod.validate(prof) == []
    assert profile_mod.compile_plan(prof)["week"]["wednesday"][0]["day_plan"] \
        == "e"


def test_warn_about_a_block_that_can_never_run(prof):
    """A dead BLOCK is advisory, not a refusal: nothing fires late and nothing
    is missed - the day just runs the block above it. That is the opposite of
    a dead rescue rule, which costs the run it was written to save."""
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default"},
                       {"block": "shards", "blueprint": "shard_daily",
                        "after": "08:00", "count": 100}]})
    assert profile_mod.validate(prof) == []          # legal...
    warnings = profile_mod.warnings(prof)
    assert any("can never run" in w and "[0]" in w for w in warnings)


def test_warn_about_a_day_plan_nobody_runs(prof):
    _plan(prof, {"d": [{"block": "coin", "blueprint": "coin_default"}],
                 "orphan": [{"block": "coin", "blueprint": "coin_default"}]},
          week={"default": "d"})
    assert profile_mod.validate(prof) == []
    assert any("orphan" in w and "never referenced" in w
               for w in profile_mod.warnings(prof))


def test_materialize_installs_the_plan_into_config(prof):
    """combo.py holds a reference to CONFIG from import time and must read the
    schedule without holding a profile. No profile -> no `plan` key -> the
    constants, which is the legacy path unchanged."""
    before_plan = copy.deepcopy(CONFIG.get("plan"))
    before_presets = dict(CONFIG["presets"])
    try:
        profile_mod.materialize(prof)
        assert list(CONFIG["plan"]["week"]) == list(profile_mod.WEEKDAYS)
        assert CONFIG["plan"]["week"]["monday"][0]["id"] == "monday#0"
        installed = CONFIG["plan"]
        profile_mod.materialize(prof)                # ...in place, idempotent
        assert CONFIG["plan"] is installed
    finally:
        for key in list(CONFIG["presets"]):
            if key not in before_presets:
                del CONFIG["presets"][key]
        if before_plan is None:
            CONFIG.pop("plan", None)
        else:
            CONFIG["plan"] = before_plan


# ------------------------------------------------------------ materialize

def test_materialize_installs_and_is_idempotent(prof):
    before = dict(CONFIG["presets"])
    presets_obj = CONFIG["presets"]
    try:
        names = profile_mod.materialize(prof)
        assert sorted(names) == ["bp_coin_default", "bp_quest_ilm",
                                 "bp_shard_daily", "bp_tourney_main"]
        assert CONFIG["presets"] is presets_obj      # never rebound
        body = CONFIG["presets"]["bp_coin_default"]
        assert body["tier"] == 14
        first = copy.deepcopy(body)

        names2 = profile_mod.materialize(prof)
        assert names2 == names
        # same dict OBJECT refilled, so anything holding a reference is still
        # looking at live data
        assert CONFIG["presets"]["bp_coin_default"] is body
        assert CONFIG["presets"]["bp_coin_default"] == first
        assert len(CONFIG["presets"]) == len(before) + 4
    finally:
        for key in list(CONFIG["presets"]):
            if key not in before:
                del CONFIG["presets"][key]


def test_materialize_is_all_or_nothing(prof):
    """A blueprint that fails to compile must leave CONFIG exactly as it was -
    installing as it goes leaves the process on a half-updated profile, some
    blueprints new, some old, nothing in the log saying which."""
    before = copy.deepcopy(CONFIG["presets"])
    prof["blueprints"]["zz_broken"] = {
        "kind": "coin", "loadout": "coin_farm", "tier": 14,
        "policies": {"rescue": "broken"}}
    prof["policies"]["rescue_policies"]["broken"] = {
        "arm": {"on": "second_wind", "watch_sec": 30},
        "rules": [{"when": {"bar": "wall", "below": 0.1},
                   "do": {"nonsense": True}}]}
    try:
        with pytest.raises(ProfileError):
            profile_mod.materialize(prof)
        assert CONFIG["presets"] == before
        assert not [k for k in CONFIG["presets"] if k.startswith("bp_")]
    finally:
        for key in [k for k in CONFIG["presets"] if k not in before]:
            del CONFIG["presets"][key]


def test_rematerialize_never_empties_a_live_body(prof):
    """`.clear()` then `.update()` opens a window where another thread holding
    that dict sees an EMPTY preset and dies on a bare subscript."""
    before = set(CONFIG["presets"])
    try:
        profile_mod.materialize(prof)
        body = CONFIG["presets"]["bp_coin_default"]
        seen: list[int] = []

        class Watcher(dict):
            """Records the size at every mutation - a 0 means an observer
            could have seen an empty preset."""
            def update(self, *a, **kw):
                super().update(*a, **kw)
                seen.append(len(self))

            def __delitem__(self, key):
                super().__delitem__(key)
                seen.append(len(self))

            def clear(self):
                super().clear()
                seen.append(len(self))

        CONFIG["presets"]["bp_coin_default"] = Watcher(body)
        prof["blueprints"]["coin_default"]["tier"] = 15
        profile_mod.materialize(prof)
        assert seen and 0 not in seen
        assert CONFIG["presets"]["bp_coin_default"]["tier"] == 15
    finally:
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


def test_rematerialize_drops_stale_keys_from_a_reused_body(prof):
    """The body is updated in place, so keys the new compile does not produce
    have to be deleted explicitly or they linger forever."""
    before = set(CONFIG["presets"])
    try:
        profile_mod.materialize(prof)
        body = CONFIG["presets"]["bp_tourney_main"]
        assert "gem_entry_max" in body
        prof["blueprints"]["tourney_main"]["kind"] = "coin"
        prof["blueprints"]["tourney_main"].pop("gem_entry_max")
        prof["blueprints"]["tourney_main"].pop("in_run_actions", None)
        profile_mod.materialize(prof)
        assert CONFIG["presets"]["bp_tourney_main"] is body   # same object
        assert "gem_entry_max" not in body
        assert "tournament_setup" not in body
    finally:
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


def test_materialize_retires_deleted_blueprints(prof):
    """A renamed blueprint must not leave a stale runnable entry in the tray."""
    before = set(CONFIG["presets"])
    try:
        profile_mod.materialize(prof)
        assert "bp_shard_daily" in CONFIG["presets"]
        del prof["blueprints"]["shard_daily"]
        prof["plan"]["days"]["farm_day"] = [
            b for b in prof["plan"]["days"]["farm_day"]
            if b["blueprint"] != "shard_daily"]
        profile_mod.materialize(prof)
        assert "bp_shard_daily" not in CONFIG["presets"]
        assert "bp_coin_default" in CONFIG["presets"]
    finally:
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


def test_materialize_leaves_legacy_presets_alone(prof):
    """It owns the `bp_` namespace and nothing else."""
    before = set(CONFIG["presets"])
    legacy_tier = CONFIG["presets"]["normal_run"]["tier"]
    try:
        profile_mod.materialize(prof)
        assert "normal_run" in CONFIG["presets"]
        assert CONFIG["presets"]["normal_run"]["tier"] == legacy_tier
    finally:
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


def test_materialized_preset_is_selectable_by_settings(prof):
    """settings.select_instance() refuses placeholders - a compiled blueprint
    must not look like one."""
    before = set(CONFIG["presets"])
    try:
        profile_mod.materialize(prof)
        body = CONFIG["presets"]["bp_coin_default"]
        assert body.get("defined", True) is True
        assert "base" not in body                   # already merged
    finally:
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


# ------------------------------------------------------- select_profile()

def test_select_profile_none_is_a_noop(profile_dir):
    before = dict(CONFIG["presets"])
    assert profile_mod.select_profile(None) is None
    assert CONFIG["presets"] == before
    assert profile_mod.PROFILE is None


def test_select_profile_missing_file_is_a_noop(profile_dir):
    """The legacy door: a name with no file behind it must change nothing, so
    a half-migrated machine still farms."""
    before = dict(CONFIG["presets"])
    assert profile_mod.select_profile("not_here") is None
    assert CONFIG["presets"] == before


def test_select_profile_binds_and_materializes(profile_dir, prof, monkeypatch):
    monkeypatch.setattr(profile_mod, "PROFILE", None)
    before = set(CONFIG["presets"])
    _write(profile_dir, "main", prof)
    try:
        assert profile_mod.select_profile("main") == "main"
        assert profile_mod.PROFILE["_name"] == "main"
        assert "bp_coin_default" in CONFIG["presets"]
    finally:
        monkeypatch.setattr(profile_mod, "PROFILE", None)
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


def test_select_profile_raises_listing_every_problem(profile_dir, prof,
                                                     monkeypatch):
    monkeypatch.setattr(profile_mod, "PROFILE", None)
    prof["player"]["wall"] = False
    prof["blueprints"]["coin_default"]["loadout"] = "ghost"
    _write(profile_dir, "broken", prof)
    before = set(CONFIG["presets"])
    with pytest.raises(ProfileError) as e:
        profile_mod.select_profile("broken")
    msg = str(e.value)
    assert "no wall" in msg and "ghost" in msg
    assert set(CONFIG["presets"]) == before      # nothing installed on refusal


def test_select_profile_one_per_process(profile_dir, prof, monkeypatch):
    monkeypatch.setattr(profile_mod, "PROFILE", None)
    before = set(CONFIG["presets"])
    _write(profile_dir, "main", prof)
    _write(profile_dir, "alt", prof)
    try:
        profile_mod.select_profile("main")
        assert profile_mod.select_profile("main") == "main"   # re-select is ok
        with pytest.raises(ProfileError, match="one profile per process"):
            profile_mod.select_profile("alt")
    finally:
        monkeypatch.setattr(profile_mod, "PROFILE", None)
        for key in set(CONFIG["presets"]) - before:
            del CONFIG["presets"][key]


# -------------------------------------------- the REAL profile on disk

def _golden():
    """tests/fixtures/golden_profile.yaml - a real, fully populated account
    profile (the migrated equivalent of that account's config.yaml presets),
    frozen as the regression fixture. The shipped profiles/default.yaml is a
    generic starter and is NOT what these locks describe - see goldens.py."""
    from goldens import load_golden
    return load_golden()


def test_golden_profile_validates_clean():
    assert profile_mod.validate(_golden()) == []


def test_tier_a_only_presets_still_report_their_capabilities():
    """CODEX P4 (CRITICAL, runtime half): every ability-using golden preset has
    `rules: []`, so a gate that only looked at Tier B rules would wave through
    exactly the presets that tap Demon Mode and the Nuke. required_capabilities
    reads `abilities{}` too, which is what makes gating every compiled preset
    (not just the ones with rules) meaningful."""
    golden = _golden()
    # tourney_main's policy has no fleet_mark rule, so it needs Demon Mode and
    # not the Nuke - the requirement is READ OFF the compiled dict, never
    # assumed from the kind.
    for name, want in (("coin_default", ["demon_mode", "nuke"]),
                       ("coin_t19", ["demon_mode", "nuke"]),
                       ("tourney_main", ["demon_mode"])):
        compiled = profile_mod.compile_preset(golden, name)
        assert compiled["rules"] == []
        need = profile_mod.required_capabilities(compiled)
        assert need["abilities"] == want, name
        assert need["wall"] is True, name
        # ...and the gate refuses when the account stops backing them
        stripped = copy.deepcopy(golden["player"])
        stripped["abilities"]["demon_mode"] = False
        problems = profile_mod.check_capabilities(compiled, stripped)
        assert any("demon_mode" in p for p in problems), name


def test_a_rescue_less_preset_needs_nothing_but_is_still_checked():
    """The other half: shard_daily taps no ability, so the gate has nothing to
    refuse - and it must not invent something, or the shard block never runs."""
    golden = _golden()
    compiled = profile_mod.compile_preset(golden, "shard_daily")
    assert profile_mod.required_capabilities(compiled) == {
        "abilities": [], "wall": False, "card_presets": [], "uws": []}
    assert profile_mod.check_capabilities(compiled, golden["player"]) == []


# ------------------------------------------ the live-test profile on disk

def _acct2(name: str = "acct2") -> dict:
    path = Path(__file__).resolve().parents[1] / "profiles" / f"{name}.yaml"
    if not path.exists():
        pytest.skip(f"profiles/{name}.yaml not present")
    return profile_mod.load(name)


def test_acct2_live_test_profile_is_runnable():
    """The P4 live-test profile for the second account. It has to keep
    validating: it is the thing a runner is pointed at on a real device, and
    an account with no abilities, no wall, no cards and one module is the
    strictest reading of the ownership gates this schema has."""
    prof = _acct2()
    assert profile_mod.validate(prof) == []
    body = profile_mod.compile_preset(prof, "live_rules_test")
    assert body["loadout"] is None and body["tier"] == 1
    assert body["shopping"] == []                     # workshop never opened
    assert body["uw_wanted"] == {}                    # no normalization sweep
    assert body["chain_lightning"]["enabled"] is False   # ...and CL is unowned
    assert body["abilities"]["rescue_bar"] is None    # no rescue: no abilities
    assert [r["id"] for r in body["rules"]] == [
        "rule_walk#0", "rule_walk#1", "rule_walk#2"]
    assert [r["do"]["kind"] for r in body["rules"]] == [
        "toggle_uw", "toggle_uw", "stop_after_run"]
    # the gate must PASS for this one, against its own player section
    assert profile_mod.check_capabilities(body, prof["player"]) == []
    assert profile_mod.required_capabilities(body)["uws"] == ["golden_tower"]
    # ...and the clone's plan is the trivial one: the same coin block every
    # day, all day, so the scheduler fixture has no clock or counter in it.
    week = profile_mod.compile_plan(prof)["week"]
    assert profile_mod.warnings(prof) == []
    for day, blocks in week.items():
        assert [b["blueprint"] for b in blocks] == ["live_rules_test"], day
        assert (blocks[0]["after_min"], blocks[0]["until_min"],
                blocks[0]["count"]) == (0, 1440, None)


def test_acct2_refusal_fixture_is_refused_by_name():
    """The other half of the live test: a rule firing an ability the ability
    row scan did not find must be refused at PROFILE LOAD - before the first
    screencap, let alone the first tap - and the message must name the
    ability, not merely say no."""
    prof = _acct2("acct2_refused")
    problems = profile_mod.validate(prof)
    assert len(problems) == 1, problems
    assert "gate_refusal_test" in problems[0]
    assert "demon_mode" in problems[0] and "does not have" in problems[0]
    # ...and the spawn-time gate says the same thing about the compiled body,
    # which is the second lock: same answer, later moment.
    body = profile_mod.compile_preset(prof, "gate_refusal_test")
    assert body["rules"][0]["requires"]["abilities"] == ["demon_mode"]
    assert any("demon_mode" in p for p in
               profile_mod.check_capabilities(body, prof["player"]))


def test_golden_plan_compiles_bit_for_bit():
    """P5'S GOLDEN. `tests/golden_default_plan.json` is the compiled schedule
    of profiles/default.yaml - the constant era written as data. It is frozen
    for the same reason the preset snapshot is: the day a block moves, an hour
    changes or an id shifts, some counter in daystate is keyed on the old name
    and a day silently reruns or silently skips."""
    path = Path(__file__).resolve().parent / "golden_default_plan.json"
    if not path.exists():
        pytest.skip("plan snapshot not generated")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert profile_mod.compile_plan(_golden()) == frozen


def test_golden_plan_reproduces_the_constant_era():
    """The four constants combo.py has scheduled by since the beginning, read
    back out of the compiled plan: shards from 08:00 for 100 runs, tournament
    from 19:00 on Wednesday and Saturday for exactly one entry, coin as the
    filler on every day of the week."""
    week = profile_mod.compile_plan(_golden())["week"]
    for day, blocks in week.items():
        assert blocks[-1]["block"] == "coin"
        assert (blocks[-1]["after_min"], blocks[-1]["until_min"],
                blocks[-1]["count"]) == (0, 1440, None)
        shards = [b for b in blocks if b["block"] == "shards"]
        assert [(b["after_min"], b["count"]) for b in shards] == [(480, 100)]
        tourney = [b for b in blocks if b["block"] == "tournament"]
        expect = [(1140, 1)] if day in ("wednesday", "saturday") else []
        assert [(b["after_min"], b["count"]) for b in tourney] == expect
        # ORDER IS PRIORITY, and the tournament outranks the shard block
        # because it is the one thing with a closing window.
        assert [b["block"] for b in blocks] == \
            (["tournament"] if expect else []) + ["shards", "coin"]


def test_golden_profile_compiles_bit_for_bit(prof):
    """THE P4 REGRESSION LOCK. `tests/golden_default_compiled.json` is the
    output of the PRE-P4 compiler over profiles/default.yaml, captured before
    the vocabulary was promoted. Every one of that profile's rescue rules is a
    Tier A fast-path shape, so P4 must not have moved a single byte: the farm
    that is running today compiles to the same preset it compiled to yesterday.

    A deliberate change to the compiled shape means regenerating this file AND
    saying so - which is the point of freezing it rather than recomputing it.

    SAID SO, ONCE, IN P6: the snapshot was regenerated for five new key/value
    pairs and NOTHING ELSE - `cancel_sprint: false` and `max_wave: null` on
    both coin presets, `in_run_actions: []` on the tournament one. All three
    are the P6 knobs' unstated defaults, all three are exactly what the runtime
    did before it could read them, and no existing key moved. The test below
    pins that: every OTHER preset is byte-identical to the pre-P6 compiler.

    SAID SO AGAIN, 2026-08-29: regenerated for exactly one cause - the
    account acquired Chrono Field. quest_sm's `uw_setup` gained
    `chronofield: false` (the ride keeps it off like every other non-wanted
    weapon), and `uw_wanted` on coin_default / coin_t19 / tourney_main
    gained `chronofield: true` (user: forced ON for farm and tournament).
    No other key moved.

    AND ONCE MORE, 2026-08-29 evening: tourney_main `abilities.dm_below`
    1.0 -> 0.5 (user: the any-falling burst fired at a 96.6% wall wobble -
    "reduce the % margin ... to half"). One value, nothing else. Then the
    `coin_t18_legend` blueprint was ADDED (as_is loadout + tournament nets
    on a farming tier) - a new snapshot entry, no existing key moved.

    2026-08-31: `coin_dissonance` ADDED (Dissonance event: tournament
    loadout + nets, no-utility sweep) - again purely a new entry.
    """
    path = Path(__file__).resolve().parent / "golden_default_compiled.json"
    if not path.exists():
        pytest.skip("golden snapshot not generated")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    golden = _golden()
    now = {name: profile_mod.compile_preset(golden, name)
           for name in golden["blueprints"]}
    assert set(now) == set(frozen)
    for name in sorted(frozen):
        assert now[name] == frozen[name], name
    # ...and no golden blueprint has a Tier B rule at all - the whole profile
    # is fast-path, which is why the snapshot can be exact.
    assert all(body["rules"] == [] for body in now.values())


@pytest.mark.parametrize("name,added", [
    ("coin_default", {"cancel_sprint": False, "max_wave": None}),
    ("coin_t19", {"cancel_sprint": False, "max_wave": None}),
    ("tourney_main", {"in_run_actions": []}),
    ("shard_daily", {}), ("quest_sm", {}), ("quest_ilm", {}),
])
def test_p6_added_exactly_these_keys_to_the_golden(name, added):
    """WHAT THE REGENERATED SNAPSHOT COST, stated as an assertion rather than
    a commit message. The P6 keys are the ONLY difference from the pre-P6
    compiler, they are all defaults, and every default is what the runtime did
    when it could not read the key at all. Strip them and the preset is the
    one the farm has been running."""
    body = profile_mod.compile_preset(_golden(), name)
    pre_p6 = {k: v for k, v in body.items() if k not in added}
    assert {k: body[k] for k in added} == added
    for key in ("cancel_sprint", "max_wave", "in_run_actions"):
        assert key not in pre_p6, f"{name} carries an unaccounted P6 key"


@pytest.mark.parametrize("key", ["abilities", "chain_lightning", "shopping",
                                 "shop_interval_sec", "gem_delay_sec",
                                 "uw_wanted", "rules", "tier", "runner",
                                 "runner_args", "_source"])
def test_every_golden_blueprint_has_the_keys_brain_subscripts(key):
    """orchestrator.py reads these as BARE SUBSCRIPTS (`preset()["chain_lightning"]`,
    `preset()["shop_interval_sec"]`), so an absent one is a KeyError mid-run,
    not a graceful default. Every blueprint, every key, no exceptions."""
    prof = _golden()
    for name in prof["blueprints"]:
        assert key in profile_mod.compile_preset(prof, name), \
            f"{name} is missing {key}"


@pytest.mark.parametrize("key", ["dm_below", "rescue_bar",
                                 "hold_until_second_wind", "nuke_below",
                                 "nuke_on_fleet", "falling_samples",
                                 "deadband", "collapse_from",
                                 "burst_cancel_sprint", "burst_retaps"])
def test_every_golden_blueprint_has_the_ability_keys(key):
    prof = _golden()
    for name in prof["blueprints"]:
        ab = profile_mod.compile_preset(prof, name)["abilities"]
        assert key in ab, f"{name}.abilities is missing {key}"


# ------------------------------------------------- the plan simulator

def test_plan_sim_reports_no_drift_from_the_constants():
    """THE P5 SAFETY ARGUMENT, run as a test rather than only by hand: at
    every minute of a 15-minute grid across seven days, the compiled plan and
    combo.py's constant ladder make the SAME decision on the golden profile.
    Zero disagreements is the whole claim; anything else is a plan that would
    schedule the farm differently than the constants it replaces."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import plan_sim
    const = plan_sim.constants()
    # ...read out of combo.py's SOURCE, so a constant that moves is caught
    # here rather than silently forgiven.
    assert const == {"SHARD_HOUR": 8, "SHARD_RUNS": 100,
                     "TOURNEY_HOUR": 19, "TOURNEY_DAYS": {2, 5}}
    plan = profile_mod.compile_plan(_golden())
    monday = datetime.date(2026, 8, 17)
    midnight = datetime.datetime.combine(monday, datetime.time())
    samples = diffs = 0
    # EVERY MINUTE, not the 15-minute grid: a block that opened at 07:59
    # instead of 08:00 is invisible on a grid, and an hour boundary is exactly
    # where a rewrite of a clock comparison goes wrong (Codex P5, MED).
    for done in ({}, {"shards"}, {"tournament"}, {"shards", "tournament"}):
        done = set(done)
        for step in range(7 * 24 * 60):
            now = midnight + datetime.timedelta(minutes=step)
            got, _, _ = plan_sim.plan_due(plan, now, done)
            samples += 1
            diffs += got != plan_sim.constant_due(now, done, const)
    assert samples == 4 * 10080
    assert diffs == 0


def test_plan_sim_probes_the_block_id_and_preset_too():
    """A schedule diff catches a block that runs at the wrong hour; only the
    id and the preset catch the RIGHT block name being backed by the wrong
    thing - and the id is the daystate counter key, so an id that moves under
    a steady name is a counter that silently restarts."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import plan_sim
    plan = profile_mod.compile_plan(_golden())
    at = datetime.datetime(2026, 8, 19, 20, 0)          # Wednesday, 20:00
    assert plan_sim.plan_due(plan, at, set()) == (
        "tournament", "wednesday#0", "bp_tourney_main")
    # ...and with the entry spent, the same minute belongs to the next block
    assert plan_sim.plan_due(plan, at, {"tournament"}) == (
        "shards", "wednesday#1", "bp_shard_daily")


def test_plan_sim_walks_the_counter_switchover(capsys):
    """The counter path has a minute too: --fill says when the quota fills,
    and the switchover is diffed against the constant era's own."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import plan_sim
    assert plan_sim._fills("shards=14:30") == {"shards": 870}
    rc = plan_sim.walk("default", datetime.date(2026, 8, 17), set(), False,
                       step_minutes=1, fill={"shards": 870})
    out = capsys.readouterr().out
    assert rc == 0
    assert "0/10080 samples differ, 0 identity jump(s)" in out
    # the switchover itself is visible, on the minute
    assert "Mon 14:30  coin" in out
    # ...and the midnight id change is reported and NOT counted: ids are
    # weekday-prefixed by design, so continuity is (preset, kind, bounds).
    assert "day boundary, same preset" in out


def test_plan_sim_refuses_a_profile_that_does_not_validate(capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import plan_sim
    _acct2("acct2_refused")     # skips when the fixture profile is absent
    rc = plan_sim.walk("acct2_refused", datetime.date(2026, 8, 17), set(),
                       False)
    assert rc == 2 and "REFUSED" in capsys.readouterr().out


# ------------------------------------------------------------ compiled_hash

def test_hash_is_stable_across_recompiles(prof):
    a = profile_mod.compile_preset(prof, "coin_default")
    b = profile_mod.compile_preset(copy.deepcopy(prof), "coin_default")
    assert profile_mod.compiled_hash(a) == profile_mod.compiled_hash(b)
    assert len(profile_mod.compiled_hash(a)) == 64


def test_hash_is_key_order_independent(prof):
    a = profile_mod.compile_preset(prof, "coin_default")
    b = {k: a[k] for k in reversed(list(a))}
    assert profile_mod.compiled_hash(a) == profile_mod.compiled_hash(b)


def test_hash_changes_when_a_rule_changes(prof):
    a = profile_mod.compile_preset(prof, "coin_default")
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"][0][
        "when"]["below"] = 0.03
    b = profile_mod.compile_preset(prof, "coin_default")
    assert profile_mod.compiled_hash(a) != profile_mod.compiled_hash(b)


def test_hash_differs_per_blueprint(prof):
    a = profile_mod.compile_preset(prof, "coin_default")
    b = profile_mod.compile_preset(prof, "tourney_main")
    assert profile_mod.compiled_hash(a) != profile_mod.compiled_hash(b)


# ------------------------------------------- the vocabulary export (P6, #2)

def _walk_specs(node, path="", pending=False) -> list[tuple[str, dict]]:
    """(path, spec) for every node in vocab(), leaves and objects alike.

    `spec["pending"]` is not a vocab key - the walker adds it, INHERITED from
    any ancestor whose doc carries the PENDING VERIFIED ROUTE marker, because
    a field nested under an offered-but-unrunnable feature is unrunnable too.
    """
    pending = pending or profile_mod.PENDING_ROUTE in node["doc"]
    node = {**node, "pending": pending}
    out = [(path, node)]
    for name, child in (node.get("fields") or {}).items():
        out += _walk_specs(child, f"{path}.{name}", pending)
    return out


def _all_specs() -> list[tuple[str, dict]]:
    out = []
    for section, spec in profile_mod.vocab().items():
        out += _walk_specs(spec, section)
    return out


def test_vocab_top_level_keys_are_the_documented_set():
    """The dashboard consumes these generically, so the set of sections IS the
    interface. VOCAB_SECTIONS is the one list of them and SCHEMA.md quotes it -
    a section added without being documented fails here."""
    v = profile_mod.vocab()
    assert tuple(v) == profile_mod.VOCAB_SECTIONS
    schema = (Path(__file__).resolve().parents[1] / "profiles" / "SCHEMA.md"
              ).read_text(encoding="utf-8")
    for name in profile_mod.VOCAB_SECTIONS:
        assert f"`{name}`" in schema, f"SCHEMA.md does not document {name}"


def test_every_vocab_node_is_a_well_formed_spec():
    """THE SHAPE IS THE PROMISE: every node answers to type/values/range/doc,
    always all four, `None` where the constraint does not apply - so a generic
    renderer can subscript them instead of guessing which keys this particular
    field brought."""
    leaf = {"enum", "int", "float", "bool", "str", "list"}
    for path, spec in _all_specs():
        spec = {k: v for k, v in spec.items() if k != "pending"}  # walker's
        assert set(spec) >= {"type", "values", "range", "doc"}, path
        assert spec["type"] in leaf | {"object"}, path
        assert isinstance(spec["doc"], str) and spec["doc"], path
        assert "\n" not in spec["doc"], path        # one line, by contract
        if spec["type"] == "object":
            assert set(spec) == {"type", "values", "range", "doc", "fields",
                                 "required"}, path
            assert spec["fields"], path
            assert set(spec["required"]) <= set(spec["fields"]), path
        else:
            assert set(spec) == {"type", "values", "range", "doc"}, path
            assert spec["values"] is None or isinstance(spec["values"], list)
            assert spec["range"] is None or len(spec["range"]) == 2, path
        if spec["type"] == "enum":
            assert spec["values"], path


def test_vocab_is_json_able_and_a_fresh_object_each_call():
    """The dashboard jsonifies it; a shared mutable would let one request's
    edit poison every later one."""
    v = profile_mod.vocab()
    assert json.loads(json.dumps(v)) == v
    v["kinds"]["values"].append("nonsense")
    assert "nonsense" not in profile_mod.vocab()["kinds"]["values"]


@pytest.mark.parametrize("section,source", [
    ("kinds", "KINDS"), ("bar_names", "BAR_NAMES"), ("buttons", "BUTTONS"),
    ("sw_states", "SW_STATES"), ("cl_modes", "CL_MODES"),
    ("weekdays", "WEEKDAYS"), ("shop_tabs", "SHOP_TABS"),
    ("shop_modes", "SHOP_MODES"),
    ("death_screen_actions", "DEATH_SCREEN_ACTIONS"),
    ("in_run_action_kinds", "IN_RUN_ACTIONS"),
])
def test_vocab_enums_are_derived_not_retyped(section, source):
    """A dropdown that lists four actions after the compiler learned eight is
    the failure this replaces, so every enum IS the validator's own tuple."""
    assert profile_mod.vocab()[section]["values"] == \
        list(getattr(profile_mod, source))


def test_vocab_covers_the_whole_rule_vocabulary():
    v = profile_mod.vocab()
    assert set(v["rule_triggers"]["fields"]) == set(profile_mod.TRIGGERS)
    assert set(v["rule_actions"]["fields"]) == set(profile_mod.ACTIONS)
    for name, (required, optional) in profile_mod.TRIGGERS.items():
        spec = v["rule_triggers"]["fields"][name]
        if spec["type"] != "object":
            continue                    # scalar/flag triggers have no params
        assert set(required) <= set(spec["required"]), name
        assert set(required) | set(optional) <= set(spec["fields"]), name
    for name, (required, optional) in profile_mod.ACTIONS.items():
        spec = v["rule_actions"]["fields"][name]
        if spec["type"] != "object":
            assert name in profile_mod.FLAG_ACTIONS, name
            assert spec["values"] == [True], name    # `false` is refused
            continue
        assert set(spec["required"]) == set(required), name
        assert set(required) | set(optional) == set(spec["fields"]), name


def _sample(spec):
    """A value the validator accepts, derived FROM THE SPEC - so filling in a
    field's required siblings never needs a table of its own."""
    if spec["type"] == "enum":
        return spec["values"][0]
    if spec["type"] == "bool":
        return True
    if spec["type"] == "int":
        return (spec["range"] or [1])[0] or 1
    if spec["type"] == "float":
        return 0.5
    if spec["type"] == "list":
        return [1, 2]
    return "main_farm"                  # the only str field is a card preset


# One VALIDATING blueprint per kind, so a bound can be written into a real
# blueprint of the right kind and put through validate(). Four come from the
# fixture; the grant quest is the one the fixture does not carry.
_BASE_BLUEPRINT = {"coin": "coin_default", "shard": "shard_daily",
                   "tournament": "tourney_main", "cycle_quest": "quest_ilm",
                   "uw_grant_quest": "quest_sm"}


def _blueprint_of_kind(prof, kind):
    name = _BASE_BLUEPRINT[kind]
    prof["blueprints"].setdefault(name, {
        "kind": "uw_grant_quest", "loadout": "coin_farm", "tier": 1,
        "grant_targets": ["smart_missiles"], "rides": 1})
    return name, prof["blueprints"][name]


# Legal values for every blueprint field, used to probe that a field the
# vocabulary offers is really accepted. Keyed by field NAME - the same spec
# name vocab() uses - and the coverage assertion below makes a new field
# without a probe value fail rather than go untested.
_LEGAL_BLUEPRINT_VALUE = {
    "kind": None,                       # filled per kind: it IS the kind
    "label": "probe",
    "loadout": "coin_farm",
    "tier": 1,
    "policies": {"gather": "gems_only"},
    "shopping": "default_sweep",
    "restart_via_home": True,
    "shop_interval_sec": 90,
    "cancel_sprint": True,
    "max_wave": 5000,
    "dissonant_tab": "utility",
    "count": 5,
    "gem_entry_max": 10,
    # EMPTY, because that is the only value validate() accepts today.
    "in_run_actions": [],
    "grant_targets": ["smart_missiles"],
    "reroll_at_wave": 1000,
    "ride_to_wave": 6500,
    "rides": 1,
    "uw_setup": {"golden_tower": True},
    "cycle_sec": 25,
    "cycles": 40,
}


def _profile_with(prof, path, spec, value):
    """`prof`, edited so that the vocab node at `path` really carries `value`.

    THIS IS WHAT MAKES THE BOUND TEST REAL: the value goes through validate()
    at the site the section describes, not through a checker the test picked.
    """
    section, _, rest = path.partition(".")
    name, _, param = rest.partition(".")
    if section == "blueprint_fields":
        field, _, sub = param.partition(".")
        _, bp = _blueprint_of_kind(prof, name)
        if not sub:
            bp[field] = value
        elif field == "in_run_actions":
            # A LIST of mappings - and a non-empty one is refused outright
            # today (no verified route to the cards screen), which is why the
            # bound sweep reads this subtree's bounds off the MESSAGES.
            bp[field] = [{"at_wave": 400, "switch_cards": "tourney_p1",
                          sub: value}]
        else:
            bp[field] = {sub: value}
        return prof
    if section in ("rule_triggers", "rule_actions"):
        vocab = profile_mod.vocab()[section]["fields"][name]
        body = {}
        if param:                       # a nested param: fill the siblings
            for req in vocab["required"]:
                body[req] = _sample(vocab["fields"][req])
            body[param] = value
        when = {name: body or value} if section == "rule_triggers" \
            else {"wave_at_least": 100}
        do = {"stop_after_run": True} if section == "rule_triggers" \
            else {name: body or value}
        if name == "bar":               # its params are SIBLINGS of the key
            when = {"bar": body.pop("bar", "hp"), **body}
        # A TIER A PARAM NEEDS A TIER A RULE. `retaps` and `require_match` are
        # refused on a main-loop rule (they have no reader there), so the two
        # the vocab marks TIER A ONLY go into an armed wall rule instead.
        tier_a = "TIER A ONLY" in spec["doc"]
        policy = {"arm": {"on": "second_wind", "watch_sec": 30},
                  "rules": [{"when": {"bar": "wall", "below": 0.02},
                             "do": {"burst": {"fire": "demon_mode",
                                              **body}}}]} if tier_a else \
                 {"arm": "always", "rules": [{"when": when, "do": do}]}
        prof["policies"]["rescue_policies"]["bound_probe"] = policy
        prof["blueprints"]["coin_default"]["policies"]["rescue"] = "bound_probe"
    elif section == "gather_keys":
        prof["policies"]["gather"]["all_on"][name] = value
    elif section == "block_fields":
        prof["plan"]["days"]["farm_day"][0][name] = value
    else:
        pytest.skip(f"no bound site wired for {path}")
    return prof


def _bounded_specs():
    return [(path, spec) for path, spec in _all_specs()
            if spec["range"] and spec["range"][0] is not None]


def test_vocab_ranges_are_the_ones_the_validator_enforces(prof):
    """TEETH, AND THEY BITE ON validate(). A declared bound the validator does
    not actually accept is worse than no bound: the editor offers the value,
    the player saves it and the profile is refused at load. So every declared
    low bound is written into a REAL profile at the site the section describes
    and put through the real validator - accepted AT the bound, refused one
    step under it."""
    checked = 0
    for path, spec in _bounded_specs():
        lo = spec["range"][0]
        field = path.rsplit(".", 1)[-1]
        under = [lo - 1, 0] if spec["type"] == "list" else lo - 1
        at = [lo, lo + 1] if spec["type"] == "list" else lo
        problems = profile_mod.validate(
            _profile_with(copy.deepcopy(prof), path, spec, at))
        if spec["pending"]:
            # An offered-but-unrunnable field is refused for its ROUTE at every
            # value, so its bound is read off the messages instead: AT the
            # bound, nothing complains about the field itself.
            assert problems and not any(field in p for p in problems), path
        else:
            assert problems == [], \
                f"{path}: {at!r} is offered by vocab() and refused by validate()"
        problems = profile_mod.validate(
            _profile_with(copy.deepcopy(prof), path, spec, under))
        assert problems, \
            f"{path}: vocab() claims a floor of {lo} that validate() ignores"
        if spec["pending"]:
            assert any(field in p for p in problems), \
                f"{path}: under the floor and only the route was reported"
        checked += 1
    assert checked == 27, f"{checked} bounded specs exercised, expected 27"


def test_vocab_unit_floats_are_really_0_to_1(prof):
    """The other bound: a bar threshold is a SHARE of the bar, and 40 is not
    40% - it would fire on the first sample of every run."""
    checked = 0
    for path, spec in _all_specs():
        if spec["type"] != "float" or spec["range"] != [0, 1]:
            continue
        for good in (0, 1, 0.5):
            assert profile_mod.validate(
                _profile_with(copy.deepcopy(prof), path, spec, good)) == [], \
                f"{path}={good}"
        for bad in (-0.1, 1.5, 40):
            assert profile_mod.validate(
                _profile_with(copy.deepcopy(prof), path, spec, bad)), \
                f"{path}={bad} is out of 0..1 and validate() took it"
        checked += 1
    assert checked == 3, f"{checked} unit floats exercised, expected 3"


def test_vocab_required_lists_are_complete(prof):
    """A rule built from ONLY the fields vocab() marks required must validate -
    otherwise the editor's "you may stop here" is a lie, and the profile it
    writes is refused at load."""
    v = profile_mod.vocab()
    checked = 0
    for section in ("rule_triggers", "rule_actions"):
        for name, spec in v[section]["fields"].items():
            if spec["type"] != "object" or "TIER A ONLY" in spec["doc"]:
                continue
            if profile_mod.PENDING_ROUTE in spec["doc"]:
                continue        # offered, and refused at load - by design
            body = {r: _sample(spec["fields"][r]) for r in spec["required"]}
            fresh = _profile_with(copy.deepcopy(prof), f"{section}.{name}",
                                  spec, body)
            # the sample body IS the required set; put it in unchanged
            pol = fresh["policies"]["rescue_policies"]["bound_probe"]
            key = "when" if section == "rule_triggers" else "do"
            if name == "bar":
                pol["rules"][0]["when"] = {"bar": body["bar"],
                                           "below": body["below"]}
            else:
                pol["rules"][0][key] = {name: body}
            assert profile_mod.validate(fresh) == [], f"{section}.{name}"
            checked += 1
    assert checked >= 5, f"only {checked} required-lists exercised"


@pytest.mark.parametrize("kind", list(profile_mod.KINDS))
def test_vocab_blueprint_fields_are_exactly_what_the_validator_accepts(prof,
                                                                       kind):
    """SECTION 18, AND ITS TEETH ARE A VALIDATION PROBE PER KIND. An editor
    that infers a field's type from its current value renders `rides` as a
    bare number box on a coin blueprint just as happily as on a quest one, so
    the vocabulary has to be exact in BOTH directions:

      * every field it lists for a kind is fed a legal value -> validate clean
      * a field it does not list is fed one           -> validate refuses it
    """
    listed = profile_mod.vocab()["blueprint_fields"]["fields"][kind]["fields"]
    assert set(listed) <= set(_LEGAL_BLUEPRINT_VALUE), \
        "a blueprint field with no probe value - add one to the table"
    for field in listed:
        fresh = copy.deepcopy(prof)
        name, bp = _blueprint_of_kind(fresh, kind)
        bp[field] = kind if field == "kind" \
            else copy.deepcopy(_LEGAL_BLUEPRINT_VALUE[field])
        assert profile_mod.validate(fresh) == [], \
            f"vocab offers {kind}.{field} and validate() refuses it"
    # ...and the other direction, over EVERY field this kind does not list -
    # which is what catches `count` drifting back onto a coin blueprint.
    unlisted = (set(_LEGAL_BLUEPRINT_VALUE) - set(listed)) | {"nonsense"}
    for field in unlisted:
        fresh = copy.deepcopy(prof)
        name, bp = _blueprint_of_kind(fresh, kind)
        bp[field] = copy.deepcopy(_LEGAL_BLUEPRINT_VALUE.get(field, 1))
        assert any(f"{name}.{field}" in p
                   for p in profile_mod.validate(fresh)), \
            f"vocab hides {kind}.{field} and validate() accepts it"


def test_vocab_blueprint_field_placement_is_derived_from_the_tables():
    """Placement comes off `_COMMON_FIELDS` / `_KIND_FIELDS` - the same two
    tables _validate_blueprint refuses against - with ONE subtraction: `count`
    is listed on every kind so that writing it gets the specific message, but
    shard is the only kind that consumes it."""
    fields = profile_mod.vocab()["blueprint_fields"]["fields"]
    assert set(fields) == set(profile_mod.KINDS)
    for kind, spec in fields.items():
        legal = set(profile_mod._COMMON_FIELDS) | \
            set(profile_mod._KIND_FIELDS[kind])
        if kind != "shard":
            legal.discard("count")
        assert set(spec["fields"]) == legal, kind
        assert set(spec["required"]) <= set(spec["fields"]), kind
    assert "count" in fields["shard"]["fields"]
    assert all("count" not in fields[k]["fields"]
               for k in profile_mod.KINDS if k != "shard")


def test_vocab_blueprint_required_lists_are_what_omission_is_refused_for(prof):
    """`required` is not decoration: dropping any one of them must be refused,
    or the editor's "you may stop here" writes a profile that will not load."""
    fields = profile_mod.vocab()["blueprint_fields"]["fields"]
    checked = 0
    for kind, spec in fields.items():
        for field in spec["required"]:
            if field == "kind":
                continue            # dropping it is "no kind", tested already
            fresh = copy.deepcopy(prof)
            name, bp = _blueprint_of_kind(fresh, kind)
            bp.pop(field, None)
            assert any(f"{name}.{field}" in p
                       for p in profile_mod.validate(fresh)), (kind, field)
            checked += 1
    assert checked == 9, f"{checked} required blueprint fields exercised"


def test_vocab_shop_stats_are_the_template_library():
    """Not a hardcoded list: a stat with no template compiles fine and is then
    silently unbuyable for the whole run, so the editor offers exactly what
    the sweep can find on screen."""
    assert profile_mod.vocab()["shop_stats"]["values"] == \
        sorted(profile_mod.shop_stats())


def test_vocab_carries_no_account_data(prof):
    """It is the VOCABULARY, identical on every account. Card presets, owned
    weapons and loadout names are player data the editor reads from the
    profile it is editing - baking them in here is how a dropdown starts
    offering another account's deck."""
    blob = json.dumps(profile_mod.vocab())
    for name in prof["player"]["card_presets"]:
        assert f'"{name}"' not in blob, name
    assert "coin_farm" not in blob                  # a config.yaml loadout
    # (guardian names are not swept: `attack` is also a workshop TAB, which is
    # vocabulary - the collision is in the game's words, not in this dict.)


def test_vocab_needs_no_config_and_no_bound_profile():
    """No Flask, no CONFIG, no I/O beyond the cached template listing: the
    dashboard imports it and jsonifies it, and it must not depend on a profile
    having been bound to the process first."""
    before = profile_mod.PROFILE
    profile_mod.PROFILE = None
    try:
        assert tuple(profile_mod.vocab()) == profile_mod.VOCAB_SECTIONS
    finally:
        profile_mod.PROFILE = before


def test_the_documented_p6_blueprints_validate_and_compile(prof):
    """The P6 examples in SCHEMA.md, spliced into a real profile and put
    through the real validator - a documented blueprint is a thing people
    copy, and both of these carry fields that were REFUSED one phase ago."""
    block = next(body for _, body in _schema_blocks()
                 if isinstance(body, dict) and "blueprints" in body
                 and "coin_capped" in body["blueprints"])
    prof["blueprints"].update(copy.deepcopy(block["blueprints"]))
    assert profile_mod.validate(prof) == []
    coin = profile_mod.compile_preset(prof, "coin_capped")
    assert coin["cancel_sprint"] is True and coin["max_wave"] == 5000
    # ...and the documented tournament example carries the EMPTY schedule,
    # which is the only value accepted while the cards route is unverified.
    assert prof["blueprints"]["tourney_swap"]["in_run_actions"] == []
    assert profile_mod.compile_preset(
        prof, "tourney_swap")["in_run_actions"] == []
