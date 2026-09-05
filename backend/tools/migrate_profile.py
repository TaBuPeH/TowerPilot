"""Golden migration: TODAY's config.yaml -> profiles/default.yaml (schema v1).

DETERMINISTIC by construction: no timestamps, no set iteration, no dict
ordering that is not written down here. Two runs produce byte-identical
files - that is a test (tests/test_migrate_verify.py) and the reason this
tool exists at all: the P2 profile is a TRANSLATION of the live config, not
a rewrite of it, so it has to be reproducible and auditable key by key.

Every value below comes from one of four places, and nothing else:
  * config.yaml   - presets (with `base:` resolved), loadouts, rois
  * combo.py      - SHARD_HOUR / SHARD_RUNS / TOURNEY_HOUR / TOURNEY_DAYS
  * flows/shard.py      - TIER
  * flows/quest_sm.py / flows/quest_ilm.py / orchestrator.py - constants that were hardcoded in
    code and become explicit profile data (the rescue literals especially:
    falling_samples 2, deadband 0.01, collapse from_above 0.3, retaps 3)

Anything in config.yaml that is NOT consumed is printed as `DROPPED <name>`
so a human can check the list rather than diff two file formats by eye.

CLI:
    python tools/migrate_profile.py --dry-run
    python tools/migrate_profile.py --out profiles/default.yaml [--force]
"""
from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


# --------------------------------------------------------------- THE MERGE
# EXACT COPY of orchestrator.preset()'s single-level `base:` merge (orchestrator.py, the
# 12 lines after the `if "base" not in p` guard). It MUST STAY IDENTICAL to
# orchestrator.preset(): the whole point of the migration is that the compiled
# profile reproduces what orchestrator.preset() returns today, and tools/
# verify_profile.py proves it by comparing against THIS function. If
# orchestrator.preset() ever changes, change this together with it (the test
# tests/test_migrate_verify.py execs the real source out of orchestrator.py and
# compares, so the two cannot silently drift).
#
# NOT `import orchestrator`: orchestrator pulls in capture/adb/psutil/cv2 at import time,
# which means a migration tool could not run without a live emulator.
def merge_preset(presets: dict, name: str) -> dict:
    """Effective preset dict for `name`, resolving single-level `base:`."""
    p = presets[name]
    if "base" not in p:
        return p
    base = presets[p["base"]]
    merged = {}
    for k, v in base.items():
        if isinstance(v, dict):
            merged[k] = {**v, **p.get(k, {})}
        else:
            merged[k] = p.get(k, v)
    for k, v in p.items():
        if k not in merged and k != "base":
            merged[k] = v
    return merged
# ------------------------------------------------------------ end of copy


# Hardcoded literals in orchestrator.py's greedy wall watch, made explicit here.
# (orchestrator.py: `falling >= 2`, `ext < prev - 0.01`, `prev > 0.3`, the retap
# loop `for attempt in (1, 2, 3)`, and `rs.fleet_try_at = now_m + 5.0`.)
FALLING_SAMPLES = 2
DEADBAND = 0.01
COLLAPSE_FROM_ABOVE = 0.3
BURST_RETAPS = 3
NUKE_THROTTLE_SEC = 5
# The Tier A `fire` parameters, also literals today:
#   orchestrator.py can_fire(): `now - rs.last_fire[name] > 15` - the refire floor.
#   orchestrator.py fire_button(require_ready=False) on the RESCUE path, because the
#   ready test reads mean brightness of a band that is mostly battlefield and
#   refuses the tap exactly when a dark tournament field needs it.
# The fleet-mark nuke does NOT pass require_ready=False (orchestrator.py:455/737 call
# fire_button(frame, "nuke", f"fleet_{m}") plain, i.e. ready IS required at
# that site). require_ready is therefore PER ACTION in legacy, and the
# compiler splits it accordingly: burst_require_match and
# nuke_on_fleet.require_ready. A single flat key could only be right about one
# of the two sites (coordinator ruling 2026-08-18).
#
# THE BURST HAS TWO SITES and they gate different things (P3 finding,
# coordinator ruling 2026-08-18):
#   * the WALL burst reaches its Demon Mode by template match, falling back to
#     the fixed RESCUE_DM_PT when no glyph matches (orchestrator.py:500). That is a
#     MATCH requirement -> burst_require_match. False = keep the fallback.
#   * the HP-PATH rescue DM goes through fire_button, whose readiness test is
#     the thing being waived (orchestrator.py:800) -> burst_require_ready. False =
#     tap without trusting the ready read, which is mostly battlefield.
# Both are false in legacy, and both are false on golden - but they are two
# knobs over two mechanisms, so neither may stand in for the other.
REFIRE_GUARD_SEC = 15
BURST_REQUIRE_MATCH = False         # orchestrator.py:500, the WALL burst's DM point
BURST_REQUIRE_READY = False         # orchestrator.py:800, the HP-PATH rescue DM
FLEET_REQUIRE_READY = True          # orchestrator.py:455/737, the scheduled nuke
HP_NUKE_REQUIRE_READY = True        # orchestrator.py:806, the hp-path rescue nuke

GRANT_UWS = ("smart_missiles", "inner_land_mines", "chronofield")
# CANONICAL FULL NAMES, one spelling per day: playerprofile refuses the
# `mon`/`wed` aliases outright, so a profile cannot carry both and leave
# something downstream to pick a winner.
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")

# uw_setup() in flows/quest_sm.py, PARSED from its source (see quest_sm_uw_setup).
# Not transcribed: a transcription is a second place to be wrong, and the
# verifier's oracle for quest_sm compares the profile against this same
# reading of the runner. tests/ pins the parser against the literal.

# Which old preset becomes which blueprint, and the policy names its parts
# get. `cl_mode` is the SCHEMA's vocabulary word for the chain_lightning
# block the preset already carries - a naming decision made once, here,
# rather than re-derived per call site.
PRESET_MAP = {
    "normal_run": {"blueprint": "coin_default", "kind": "coin",
                   "uw_policy": "farm_cl_choreo", "cl_mode": "fleet_marks",
                   "rescue_policy": "high_tier_wall", "gather": "all_on",
                   "loadout": "coin_farm"},
    "t19_test": {"blueprint": "coin_t19", "kind": "coin",
                 "uw_policy": "farm_cl_choreo", "cl_mode": "fleet_marks",
                 "rescue_policy": "t19_fast_drain", "gather": "all_on",
                 "loadout": "coin_farm"},
    # gather: all_on, NOT gems_only (Codex audit #2 / #8). This table is the
    # ONE place the policy names live - build() reads them from here rather
    # than repeating them as literals, so a stale row cannot disagree with
    # the emitted blueprint the way this one silently did.
    "tournament": {"blueprint": "tourney_main", "kind": "tournament",
                   "uw_policy": "tourney_cl", "cl_mode": "off_until_wave",
                   "rescue_policy": "tournament_any_falling",
                   "gather": "all_on", "loadout": "tourney_1"},
    "shard_farm": {"blueprint": "shard_daily", "kind": "shard",
                   "gather": "gems_only", "loadout": "shard_farm"},
    "quest_smart_missiles": {"blueprint": "quest_sm", "kind": "uw_grant_quest",
                             "loadout": "coin_farm"},
    "quest_inner_land_mines": {"blueprint": "quest_ilm", "kind": "cycle_quest",
                               "loadout": "inner_land_mines_quest"},
}

# Preset keys consumed by the translation, per preset. Everything else is
# reported as DROPPED. `base` is consumed by merge_preset() itself.
CONSUMED = {
    "normal_run": {"shopping", "shop_interval_sec", "restart_via_home",
                   "tier", "uw_wanted", "chain_lightning", "gem_delay_sec",
                   "abilities"},
    "t19_test": {"base", "tier", "abilities", "shopping", "shop_interval_sec",
                 "restart_via_home", "uw_wanted", "chain_lightning",
                 "gem_delay_sec"},
    "tournament": {"base", "tournament_setup", "gem_entry_max",
                   "chain_lightning", "abilities", "shopping",
                   "shop_interval_sec", "restart_via_home", "tier",
                   "uw_wanted", "gem_delay_sec"},
    "shard_farm": {"runner", "runner_args"},
    "quest_smart_missiles": {"runner", "runner_args", "tier"},
    "quest_inner_land_mines": {"runner", "runner_args", "tier"},
}

DROP_REASON = {
    "label": "tray menu text; the blueprint name is the identity now",
    "defined": "placeholder marker; a profile lists only real blueprints",
}


# ------------------------------------------------------------------ inputs
def _load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _int_const(module: str, name: str) -> int:
    """Read `NAME = <int>` out of a sibling module WITHOUT importing it.

    combo.py / flows/shard.py import daystate, logger and settings; importing them
    from a migration tool would create log dirs and bind adb config. The
    constants are plain top-level ints, so the source is the safer oracle.
    """
    src = (ROOT / module).read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=\s*(\d+)", src, re.M)
    if not m:
        raise SystemExit(f"{module}: constant {name} not found")
    return int(m.group(1))


def _int_set_const(module: str, name: str) -> list[int]:
    src = (ROOT / module).read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=\s*\{{([^}}]*)\}}", src, re.M)
    if not m:
        raise SystemExit(f"{module}: constant {name} not found")
    return sorted(int(x) for x in re.findall(r"\d+", m.group(1)))


def _float_const(module: str, name: str) -> float:
    """Read `NAME = <float>` (e.g. EXIT_AFTER_SEC = 25.0) from source."""
    src = (ROOT / module).read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=\s*(\d+(?:\.\d+)?)", src, re.M)
    if not m:
        raise SystemExit(f"{module}: constant {name} not found")
    return float(m.group(1))


def _pair_const(module: str, name: str) -> list[int]:
    """Read `NAME = (a, b)` / `[a, b]` out of a sibling module's source."""
    src = (ROOT / module).read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=\s*[\(\[]([^\)\]]*)[\)\]]", src, re.M)
    if not m:
        raise SystemExit(f"{module}: constant {name} not found")
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


def quest_sm_uw_setup() -> dict:
    """quest_sm.uw_setup() read out of its own source, as data.

    The function is a fixed list of shopper.uw_toggle() calls plus one loop
    of OFF weapons; nothing about it is dynamic, so the source IS the value.
    Parsing rather than transcribing keeps exactly one statement of what the
    quest run wants toggled - the one the runner itself executes.
    """
    src = (ROOT / "flows/quest_sm.py").read_text(encoding="utf-8")
    body = re.search(r"^def uw_setup\(\).*?\n(?=\n*^def |\n*^[A-Z_]+ =)",
                     src, re.M | re.S)
    if not body:
        raise SystemExit("flows/quest_sm.py: def uw_setup() not found")
    text = body.group(0)
    out: dict[str, bool] = {}
    for names, want in re.findall(
            r"for uw in \(([^)]*)\):\s*\n\s*shopper\.uw_toggle\("
            r"uw, want_on=(True|False)\)", text):
        for name in re.findall(r'"(\w+)"', names):
            out[name] = want == "True"
    for name, want in re.findall(
            r'shopper\.uw_toggle\("(\w+)", want_on=(True|False)\)', text):
        out[name] = want == "True"
    if not out:
        raise SystemExit("flows/quest_sm.py: uw_setup() parsed to nothing")
    return out


def _chore_names() -> list[str]:
    """The CHORES registry in chores.py, in its declared priority order."""
    src = (ROOT / "scheduling" / "chores.py").read_text(encoding="utf-8")
    block = re.search(r"^CHORES = \[(.*?)^\]", src, re.M | re.S)
    if not block:
        return []
    return re.findall(r'\(\s*"(\w+)"\s*,\s*lambda', block.group(1))


# ------------------------------------------------------------- translation
def build_player(cfg: dict, presets: dict, verified: bool = False) -> dict:
    """Seeded from evidence in config.yaml - NOT a scan. Marked as such."""
    loadouts = cfg.get("loadouts") or {}
    cards, guardians, modules = set(), set(), set()
    for body in loadouts.values():
        body = body or {}
        if isinstance(body.get("cards"), str):
            cards.add(body["cards"])
        if isinstance(body.get("guardians"), list):
            guardians.update(g for g in body["guardians"] if isinstance(g, str))
        for entry in body.get("modules") or []:
            if isinstance(entry, (list, tuple)) and entry:
                modules.add(entry[0])

    # UWs: the ones normal_run enforces at wave 1, plus Chain Lightning
    # (deliberately absent from uw_wanted because the orchestrator drives it), minus
    # the three the quest presets exist to farm and the user does NOT own.
    wanted = merge_preset(presets, "normal_run").get("uw_wanted") or {}
    uws = {"chain_lightning": True}
    for name in sorted(wanted):
        uws[name] = True
    for name in sorted(GRANT_UWS):
        uws[name] = False

    # wall: an instance that calibrated a wall_bar ROI has a wall.
    wall = any((inst.get("rois") or {}).get("wall_bar")
               for inst in (cfg.get("instances") or {}).values())
    tiers = [b.get("tier") for b in presets.values()
             if isinstance(b, dict) and isinstance(b.get("tier"), int)]
    tiers.append(_int_const("flows/shard.py", "TIER"))

    out = {
        "seeded_from": "config.yaml (migrate)",
        "uws": uws,
        # Nuke and Demon Mode are both fired by orchestrator.py today (fire_button
        # + the rescue burst) on the live instance, so both are INFERRED as
        # owned - inferred from a config file that describes intent, which is
        # not the same thing as having watched the buttons work.
        "abilities": {"nuke": True, "demon_mode": True},
        # NEVER true from a migration (Codex round-2 NEW#1). A rescue burst
        # taps the fixed Demon Mode coordinate BLIND when no glyph matches,
        # so "the config implies we own DM" is not good enough to arm one:
        # an unowned ability means the burst taps whatever occupies that slot.
        # Only two things may set this true - a scan that saw the buttons, or
        # an operator asserting it with --assert-abilities-verified.
        "abilities_verified": bool(verified),
        "card_presets": sorted(cards),
        "guardians": sorted(guardians),
        # config.yaml cannot tell equipped from in-grid (loadout.py discovers
        # that at apply time); a scan fills these in properly. The union goes
        # in modules_equipped so ownership gating passes for every loadout
        # config.yaml already names.
        "modules_equipped": sorted(modules),
        "modules_in_grid": [],
        "wall": bool(wall),
        "max_tier": max(tiers),
    }
    if verified:
        # Provenance for the one field a human is allowed to raise by hand.
        out["abilities_verified_by"] = "operator flag"
    return out


def rescue_policy(ab: dict) -> dict:
    """abilities{} -> the SCHEMA RULES vocabulary.

    Tier A only: one bar rule, one wall_collapse, one fleet_mark, all under
    arm.on: second_wind. The three literals orchestrator.py hardcodes are emitted
    explicitly so nothing is implied by code any more.
    """
    burst = {"burst": {"cancel_sprint": True, "fire": "demon_mode",
                       "retaps": BURST_RETAPS}}
    bar = ab.get("rescue_bar", "wall")
    if ab.get("hold_until_second_wind", True):
        arm = {"on": "second_wind",
               "immunity_sec": ab.get("sw_immunity_sec"),
               "watch_sec": ab.get("post_sw_watch_sec")}
    else:
        arm = "always"

    rules = []
    if ab.get("dm_below") is not None:
        rules.append({"when": {"bar": bar, "below": ab["dm_below"],
                               "falling_samples": FALLING_SAMPLES,
                               "deadband": DEADBAND},
                      "do": copy.deepcopy(burst)})
    if bar == "wall":
        # orchestrator.py: `prev > 0.3`, FIXED, deliberately not dm_below.
        rules.append({"when": {"wall_collapse":
                               {"from_above": COLLAPSE_FROM_ABOVE}},
                      "do": copy.deepcopy(burst)})
    if ab.get("nuke_below") is not None:
        rules.append({"when": {"bar": bar, "below": ab["nuke_below"],
                               "falling_samples": FALLING_SAMPLES,
                               "deadband": DEADBAND},
                      "do": {"fire": {"button": "nuke",
                                      "throttle_sec": NUKE_THROTTLE_SEC}}})
    fleet = ab.get("nuke_on_fleet")
    if fleet:
        rules.append({"when": {"fleet_mark":
                               {"after_waves": fleet.get("after_waves", 1),
                                "window_waves": fleet.get("window_waves", 60)}},
                      "do": {"fire": {"button": "nuke",
                                      "throttle_sec": NUKE_THROTTLE_SEC}}})
    return {"arm": arm,
            "end_sprint_after_sw": bool(ab.get("end_sprint_after_sw", False)),
            "rules": rules}


def uw_policy(eff: dict, mode: str) -> dict:
    """uw_wanted + chain_lightning -> one uw policy.

    The baseline is the preset's OWN effective uw_wanted (tournament inherits
    normal_run's five via `base:`), so the compiled preset reproduces today's
    wave-1 normalization exactly. `pre_mark_waves`/`off_after_waves` are
    carried for both modes because both presets carry them today - dropping
    them for the tournament would change what orchestrator.preset() returns.
    """
    cl = dict(eff.get("chain_lightning") or {})
    out = {"mode": mode}
    above = cl.get("always_on_above")
    if cl.get("always_on"):
        out["mode"] = "always_on"
    if above is not None:
        # SCHEMA spells the tournament's threshold `on_above` and the farm's
        # `always_on_above`; both compile to the flat always_on_above.
        out["on_above" if mode == "off_until_wave" else "always_on_above"] = above
    if mode == "fleet_marks":
        # ONLY fleet_marks carries the mark offsets. off_until_wave used to
        # inherit normal_run's [5, 25]/[53, 72] here so the compiled dict
        # matched orchestrator.preset() byte for byte - and Codex audit #12 showed
        # what that bought: a tournament latch at 500 still switched CL back
        # ON around the 2495 fleet mark, which is the exact opposite of what
        # "off until wave" says. Ruled 2026-08-18: the offsets are dropped,
        # the behaviour change is intentional and the verifier carries it as
        # a documented path+value allowance.
        for k in ("pre_mark_waves", "off_after_waves"):
            if cl.get(k) is not None:
                out[k] = cl[k]
    return {"baseline": dict(eff.get("uw_wanted") or {}),
            "chain_lightning": out}


def build(cfg: dict, dropped: list[str], verified: bool = False) -> dict:
    presets = cfg["presets"]
    eff = {name: merge_preset(presets, name) for name in PRESET_MAP}
    normal = eff["normal_run"]
    tourney = eff["tournament"]
    t19 = eff["t19_test"]

    shard_hour = _int_const("scheduling/combo.py", "SHARD_HOUR")
    shard_runs = _int_const("scheduling/combo.py", "SHARD_RUNS")
    tourney_hour = _int_const("scheduling/combo.py", "TOURNEY_HOUR")
    tourney_days = _int_set_const("scheduling/combo.py", "TOURNEY_DAYS")
    shard_tier = _int_const("flows/shard.py", "TIER")

    # -------------------------------------------------------- blueprints
    # NEW#8: policy names come from PRESET_MAP, never from a literal here.
    # The stale `gather: gems_only` row that survived the #2 fix was harmless
    # only by luck - the emitted blueprint happened to be written out twice,
    # and one copy was right. One source, so there is nothing to disagree.
    def blueprint_policies(old_name: str) -> dict:
        m = PRESET_MAP[old_name]
        out = {}
        for key, field in (("uw", "uw_policy"), ("rescue", "rescue_policy"),
                           ("gather", "gather")):
            if m.get(field):
                out[key] = m[field]
        return out

    def coin(old_name: str, name_eff: dict) -> dict:
        return {
            "kind": PRESET_MAP[old_name]["kind"],
            "loadout": PRESET_MAP[old_name]["loadout"],
            "tier": name_eff.get("tier"),
            # NO cancel_sprint / max_wave / count. They are P6 fields with no
            # reader, and a null placeholder in the golden file is the audit
            # #5 trap in miniature: it advertises a knob, the dashboard will
            # happily show it, someone sets `max_wave: 3000` expecting a cap,
            # and nothing stops the run. An absent key promises nothing.
            # (Ruled 2026-08-18; the compiler refuses these on PRESENCE now.)
            "restart_via_home": bool(name_eff.get("restart_via_home")),
            "shop_interval_sec": name_eff.get("shop_interval_sec"),
            "shopping": "default_sweep",
            "policies": blueprint_policies(old_name),
        }

    blueprints = {
        "coin_default": coin("normal_run", normal),
        "coin_t19": coin("t19_test", t19),
        "tourney_main": {
            "kind": PRESET_MAP["tournament"]["kind"],   # tournament_setup
            "loadout": PRESET_MAP["tournament"]["loadout"],
            "gem_entry_max": tourney.get("gem_entry_max"),
            # 14, INHERITED from normal_run via `base:`. Not a typo and not a
            # judgment call: orchestrator.preset() returns tier 14 for the tournament
            # preset today, so the golden translation keeps it.
            "tier": tourney.get("tier"),
            # NO in_run_actions: same ruling. There is no evaluator for them
            # yet, and an empty list reads like "none configured" rather than
            # "not implemented".
            # Inherited from normal_run too - a tournament run shops and
            # restarts exactly like High Tier once the battle starts.
            "restart_via_home": bool(tourney.get("restart_via_home")),
            "shop_interval_sec": tourney.get("shop_interval_sec"),
            "shopping": "default_sweep",
            # gather: all_on, NOT gems_only (Codex audit #2, CRITICAL). A
            # tournament run is a orchestrator run: it inherits normal_run's full
            # reward collection today - ad gems, the 8h quests, quest
            # rewards, guild. gems_only would have silently switched all of
            # that off the day the profile was activated. Golden means
            # today's BEHAVIOUR, not the shape SCHEMA's example sketched.
            "policies": blueprint_policies("tournament"),
        },
        "shard_daily": {
            "kind": PRESET_MAP["shard_farm"]["kind"],   # runner: flows/shard.py
            "loadout": PRESET_MAP["shard_farm"]["loadout"],
            "tier": shard_tier,            # flows/shard.py TIER
            "count": shard_runs,           # combo.py SHARD_RUNS (was --loops 0)
            "policies": blueprint_policies("shard_farm"),
        },
        "quest_sm": {
            "kind": PRESET_MAP["quest_smart_missiles"]["kind"],
            "loadout": PRESET_MAP["quest_smart_missiles"]["loadout"],
            "tier": presets["quest_smart_missiles"].get("tier"),
            "grant_targets": ["smart_missiles"],
            "reroll_at_wave": 1000,        # quest_sm.RESTART_AT_WAVE
            "ride_to_wave": 6500,          # quest_sm.RIDE_TO_WAVE
            "rides": 1,                    # runner_args ['--rides', '1']
            "uw_setup": quest_sm_uw_setup(),       # parsed from flows/quest_sm.py
        },
        "quest_ilm": {
            "kind": PRESET_MAP["quest_inner_land_mines"]["kind"],
            "loadout": PRESET_MAP["quest_inner_land_mines"]["loadout"],
            "tier": presets["quest_inner_land_mines"].get("tier"),
            "cycle_sec": 25,               # quest_ilm.EXIT_AFTER_SEC
            "cycles": 40,                  # runner_args ['--cycles', '40']
        },
    }

    # ----------------------------------------------------------- policies
    gem_delay = normal.get("gem_delay_sec")
    policies = {
        "uw_policies": {
            "farm_cl_choreo": uw_policy(normal, "fleet_marks"),
            "tourney_cl": uw_policy(tourney, "off_until_wave"),
        },
        "rescue_policies": {
            "high_tier_wall": rescue_policy(normal.get("abilities") or {}),
            "t19_fast_drain": rescue_policy(t19.get("abilities") or {}),
            "tournament_any_falling": rescue_policy(
                tourney.get("abilities") or {}),
        },
        # all_on is what EVERY orchestrator-kind blueprint gets: orchestrator.py collects
        # all of it today and a translation may not quietly stop collecting.
        # gems_only is reachable by exactly one blueprint - shard_daily -
        # and only because flows/shard.py's GemWatch is genuinely flying-gems-only
        # (flows/shard.py:131; it has no ad, quest, reward or guild code at all,
        # and its loop leaves no gap to run one in). It is the legacy truth
        # for that runner, not a preference.
        "gather": {
            "all_on": {"flying_gem": True, "gem_delay_sec": gem_delay,
                       "ad_gems": True, "quests_8h": True,
                       "quest_rewards": True, "guild": True},
            "gems_only": {"flying_gem": True,
                          "gem_delay_sec": _pair_const("flows/shard.py",
                                                       "GEM_DELAY_SEC"),
                          "ad_gems": False, "quests_8h": False,
                          "quest_rewards": False, "guild": False},
        },
        # SCHEMA "### SHOPPING": a list is {enabled, directives:[...]} and
        # every directive carries its own `enabled`. Today's config has no
        # such keys, and a legacy list is all-enabled by definition, so the
        # migrator writes them in explicitly (order = priority, untouched).
        "shopping_lists": {
            "default_sweep": {
                "enabled": True,
                "directives": [{"enabled": True, **d}
                               for d in (normal.get("shopping") or [])],
            },
        },
        "chores": [{"name": n, "enabled": True} for n in _chore_names()],
    }

    # --------------------------------------------------------------- plan
    after_shards = _Time(f"{shard_hour:02d}:00")
    after_tourney = _Time(f"{tourney_hour:02d}:00")
    farm_day = [
        {"block": "shards", "blueprint": "shard_daily",
         "after": after_shards, "count": shard_runs},
        {"block": "coin", "blueprint": "coin_default"},
    ]
    week = {"default": "farm_day"}
    for d in tourney_days:
        week[WEEKDAYS[d]] = "tourney_day"
    plan = {
        "week": week,
        "days": {
            "farm_day": farm_day,
            "tourney_day": [
                {"block": "tournament", "blueprint": "tourney_main",
                 "after": after_tourney, "count": 1},
            ] + [dict(step) for step in farm_day],
        },
    }

    _guard_brain_gather(blueprints, policies["gather"])
    _report_dropped(presets, dropped)
    return {"player": build_player(cfg, presets, verified),
            "blueprints": blueprints,
            "policies": policies, "plan": plan}


BRAIN_KINDS = ("coin", "tournament")


def _guard_brain_gather(blueprints: dict, gather_policies: dict) -> None:
    """Refuse to emit a orchestrator-kind blueprint that gathers less than today.

    The last line of defence for Codex audit #2: not a comment, not a table
    row, but a check on the bytes about to be written. orchestrator.py collects
    flying gems, ad gems, the 8h quests, quest rewards and guild on every run
    it does today; a migration that quietly stops doing one of them is a
    behaviour change wearing a translation's clothes.
    """
    for name, bp in blueprints.items():
        if bp.get("kind") not in BRAIN_KINDS:
            continue
        policy_name = (bp.get("policies") or {}).get("gather")
        policy = gather_policies.get(policy_name) or {}
        off = sorted(k for k, v in policy.items()
                     if isinstance(v, bool) and not v)
        if not policy or off:
            raise SystemExit(
                f"REFUSED: blueprint {name} is a {bp['kind']} run but its "
                f"gather policy {policy_name!r} switches off {off or '(all)'} "
                f"- orchestrator.py collects every one of those today (audit #2)")


def _report_dropped(presets: dict, dropped: list[str]) -> None:
    """Name every config.yaml preset key the profile does NOT carry."""
    for name in sorted(presets):
        body = presets[name] or {}
        if name not in PRESET_MAP:
            keys = ", ".join(sorted(body))
            dropped.append(
                f"presets.{name} (whole preset: not in the P2 mapping; "
                f"keys: {keys})")
            continue
        for key in sorted(body):
            if key in CONSUMED.get(name, set()):
                continue
            reason = DROP_REASON.get(key, "not consumed by the schema")
            dropped.append(f"presets.{name}.{key} ({reason})")


# ------------------------------------------------------------------ output
_SECTION_DOC = {
    "player": ("player - SEEDED FROM config.yaml, NOT SCANNED. Every list\n"
               "# here is the union of what the loadouts already name, which\n"
               "# is evidence of INTENT, not proof of ownership: config.yaml\n"
               "# says what a run tries to equip, never what the account has.\n"
               "# scan.py overwrites this section with real evidence.\n"
               "#\n"
               "# abilities_verified is therefore false out of the migrator,\n"
               "# always. It gates the rescue: a burst taps the fixed Demon\n"
               "# Mode coordinate blind when no glyph matches, so an ability\n"
               "# that is merely IMPLIED by a config file must not arm one.\n"
               "# Raise it only with a scan, or with the operator assertion\n"
               "# `tools/migrate_profile.py --assert-abilities-verified`,\n"
               "# which stamps abilities_verified_by: operator flag."),
    "blueprints": "blueprints - one per thing the bot can be told to do.",
    "policies": "policies - the shared, reusable halves of a blueprint.",
    "plan": "plan - the day schedule, from combo.py's constants.",
}

class _Time(str):
    """A "HH:MM" that must stay quoted. Unquoted, YAML 1.1 reads 19:00 as the
    sexagesimal integer 1140 (08:00 survives only by accident of the leading
    zero) - a golden file cannot depend on that."""


class _Dumper(yaml.SafeDumper):
    """No anchors/aliases: two policies sharing a value must not turn into
    `&id001`/`*id001` in a file humans are expected to read and edit."""

    def ignore_aliases(self, data):
        return True


_Dumper.add_representer(
    _Time, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", str(v),
                                           style="'"))


HEADER = """\
# Tower Autopilot player profile (schema v1) - see profiles/SCHEMA.md
#
# GENERATED by tools/migrate_profile.py from config.yaml, combo.py, flows/shard.py,
# flows/quest_sm.py, flows/quest_ilm.py and orchestrator.py's hardcoded rescue literals. It is a
# TRANSLATION, not a redesign: tools/verify_profile.py proves every blueprint
# compiles back to the dict orchestrator.preset() returns today.
#
# Regenerating is safe and byte-stable; hand edits are NOT preserved.
"""


def render(doc: dict) -> str:
    """YAML text. Sections dumped one at a time so each keeps its comment."""
    out = [HEADER]
    for key, value in doc.items():
        doc_line = _SECTION_DOC.get(key)
        if doc_line:
            out.append(f"\n# {doc_line}\n")
        out.append(yaml.dump({key: value}, Dumper=_Dumper, sort_keys=False,
                             default_flow_style=False, allow_unicode=True,
                             width=88))
    return "".join(out)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".migrate_",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="profiles/default.yaml",
                    help="destination profile (default: profiles/default.yaml)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the YAML, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing profile")
    ap.add_argument("--config", default=str(CONFIG_PATH),
                    help="source config.yaml (default: the repo's)")
    ap.add_argument("--assert-abilities-verified", action="store_true",
                    help="OPERATOR ASSERTION: you have watched Nuke and "
                         "Demon Mode actually fire on this account. Sets "
                         "player.abilities_verified true and records "
                         "abilities_verified_by: operator flag. Without it "
                         "the migrator always emits false - config evidence "
                         "is intent, not proof, and a rescue burst taps the "
                         "Demon Mode coordinate blind.")
    args = ap.parse_args(argv)

    dropped: list[str] = []
    text = render(build(_load_config(Path(args.config)), dropped,
                        args.assert_abilities_verified))

    for line in dropped:
        print(f"DROPPED {line}")
    print(f"DROPPED {len(dropped)} config key(s)/preset(s) in total")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if args.dry_run:
        print(f"--- dry run, nothing written to {out} ---")
        sys.stdout.write(text)
        return 0
    if out.exists() and not args.force:
        print(f"REFUSED: {out} exists (pass --force to overwrite)",
              file=sys.stderr)
        return 1
    write_atomic(out, text)
    print(f"wrote {out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
