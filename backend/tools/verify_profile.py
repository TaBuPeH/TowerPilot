"""Static equivalence: profiles/default.yaml compiles back to today's behaviour.

For every (old preset -> blueprint) pair in MAPPING this computes

    old = migrate_profile.merge_preset(config["presets"], preset)   # orchestrator.preset()
    new = playerprofile.compile_preset(playerprofile.load(name), blueprint)

runs an ORACLE over `new`, then deep-diffs old against new. Exit 0 iff every
oracle passes and every difference is explained by a path-AND-value-specific
allowance. This is the safety argument for switching the runtime over to the
profile: not "the new file looks right", but "the dict each consumer reads is
the dict it reads today".

WHY AN ORACLE AND NOT AN ALLOWLIST (Codex audit #10, ruled 2026-08-18)
A leaf-name allowlist forgives a key whatever its value - `gather` was
allowed as one opaque dict and hid a CRITICAL change (the golden tournament
had stopped collecting ad gems, quests, rewards and guild: audit #2). So
nothing is forgiven by name any more. Every added or changed key must either

  * match an ORACLE entry - a path with an EXPECTED VALUE and a reason
    traceable to legacy source (orchestrator.py's literals, flows/shard.py/quest_*.py's
    constants, combo.py's schedule, or the legacy preset itself), or
  * match a per-pair documented allowance - again path AND value, or
  * be `label` (tray text) or `base` (resolved by the merge).

The shard/quest pairs get no blanket pass either: their old "preset" was a
runner stub, so their oracle is a BEHAVIOURAL one - every key those scripts
will read, checked against the constant it replaces, parsed out of the
runner's own source.

Usage:
    python tools/verify_profile.py [--profile default] [--all]
    python tools/verify_profile.py --hash          # attestation
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_profile as mig            # noqa: E402  (the ONE copy of the merge)
from migrate_profile import merge_preset  # noqa: E402

MODULE = "playerprofile"
BRAIN_KINDS = {"coin", "tournament"}
ADDED, REMOVED, CHANGED = "added", "removed", "changed"


class _Absent:
    """Sentinel: the compiler must NOT emit this key at all.

    A P6 field that is accepted in the blueprint but not yet wired (coin
    cancel_sprint / max_wave / count, tournament in_run_actions - audit #7)
    must stay absent from the compiled dict, not appear with a plausible
    default: absent is what today's orchestrator.preset() returns, and the day one
    of them starts being emitted is the day this check must fail loudly.
    """

    def __repr__(self):
        return "<absent>"


ABSENT = _Absent()


class Expect(NamedTuple):
    value: object
    why: str
    cmp: str | None = None      # None = equality; "flags" = argv flag dict


class Pair(NamedTuple):
    old: str
    bp: str
    kind: str
    loadout: str
    allow: dict                 # path -> (expected new value, reason)


# The tournament's CL mark offsets are the ONE intentional behaviour change in
# the golden profile, ruled 2026-08-18 after Codex audit #12: `off_until_wave`
# means CL is genuinely dark until the latch wave, and inheriting normal_run's
# fleet-mark offsets turned it back ON around wave 2495 whenever the latch sat
# above the first mark. Legacy [5, 25] / [53, 72] -> null, deliberately.
TOURNEY_ALLOW = {
    "chain_lightning.pre_mark_waves":
        (None, "INTENTIONAL (audit #12): off_until_wave means no fleet-mark "
               "choreography at all; legacy inherited [5, 25] from normal_run"),
    "chain_lightning.off_after_waves":
        (None, "INTENTIONAL (audit #12): as above; legacy inherited [53, 72]"),
}

MAPPING = [
    Pair("normal_run", "coin_default", "coin", "coin_farm", {}),
    Pair("t19_test", "coin_t19", "coin", "coin_farm", {}),
    Pair("tournament", "tourney_main", "tournament", "tourney_1",
         TOURNEY_ALLOW),
    Pair("shard_farm", "shard_daily", "shard", "shard_farm", {}),
    Pair("quest_smart_missiles", "quest_sm", "uw_grant_quest", "coin_farm", {}),
    Pair("quest_inner_land_mines", "quest_ilm", "cycle_quest",
         "inner_land_mines_quest", {}),
]

# Keys the runtime reads with a BARE SUBSCRIPT or arithmetic, so a null there
# is a crash, not a no-op: orchestrator.py does preset()["chain_lightning"],
# ab["dm_below"], `now >= rs.shop_at + shop_interval_sec`, and flows/shard.py does
# tuple(GEM_DELAY_SEC)/random.uniform(*...). The generic "a null addition is
# invisible under .get()" reasoning does NOT apply to these.
BARE_SUBSCRIPT = {"shopping", "shop_interval_sec", "gem_delay_sec",
                  "chain_lightning", "abilities", "abilities.dm_below"}

IGNORED_LEAF = {"label"}                 # tray text; the blueprint name is id
ALLOWED_DROPPED = {"base", "end_intro_sprint_to_fire"}


# ------------------------------------------------------------------ oracles
def _script_abilities() -> dict:
    """What a script-kind blueprint's compiled `abilities` MUST be.

    Not "whatever the compiler emits": rescue-DISABLED, explicitly. flows/shard.py
    and the quest runners never read preset abilities, but orchestrator.py might be
    handed one of these dicts by a future path, and a rescue_bar with a null
    dm_below is the audit #3 crash loop (extent < None) - and worse, a burst
    taps the fixed Demon Mode coordinate blind.
    """
    return {"hold_until_second_wind": False, "post_sw_watch_sec": None,
            "sw_immunity_sec": None, "end_sprint_after_sw": False,
            "rescue_bar": None, "dm_below": None, "nuke_below": None,
            "nuke_on_fleet": None,
            "falling_samples": mig.FALLING_SAMPLES, "deadband": mig.DEADBAND,
            "collapse_from": mig.COLLAPSE_FROM_ABOVE,
            "burst_cancel_sprint": True, "burst_retaps": mig.BURST_RETAPS,
            # PER SITE, not flat (coordinator ruling 2026-08-18). See
            # build_oracle() for the three orchestrator.py call sites this splits.
            "burst_require_match": mig.BURST_REQUIRE_MATCH,
            "burst_require_ready": mig.BURST_REQUIRE_READY,
            "hp_nuke_require_ready": mig.HP_NUKE_REQUIRE_READY,
            "refire_guard_sec": mig.REFIRE_GUARD_SEC}


def _legacy_flags(preset: dict) -> dict:
    """runner_args ['--rides', '1'] -> {'--rides': '1'}."""
    args = list(preset.get("runner_args") or [])
    out = {}
    for i, tok in enumerate(args):
        if str(tok).startswith("--"):
            nxt = args[i + 1] if i + 1 < len(args) else None
            out[str(tok)] = (None if nxt is None or str(nxt).startswith("--")
                             else str(nxt))
    return out


def build_oracle(pair: Pair, legacy: dict, profile_name: str) -> dict:
    """path -> Expect(value, why). Every expectation traces to legacy source."""
    o: dict[str, Expect] = {
        "kind": Expect(pair.kind, "the runner this blueprint selects"),
        "loadout": Expect(pair.loadout,
                          "config.yaml loadout this run type equips today"),
        "_source": Expect({"profile": profile_name, "blueprint": pair.bp},
                          "provenance stamp"),
        "rules": Expect([], "golden profile is Tier A only - a non-empty "
                            "Tier B list means a rule the P3 evaluator "
                            "cannot execute (audit #6)"),
    }

    if pair.kind in BRAIN_KINDS:
        ab = legacy.get("abilities") or {}
        # AUDIT #2: gather is checked flag by flag against what orchestrator.py
        # actually collects today. All of it. No opaque subtree.
        for flag in ("flying_gem", "ad_gems", "quests_8h", "quest_rewards",
                     "guild"):
            o[f"gather.{flag}"] = Expect(
                True, f"orchestrator.py collects {flag} on every run today; a "
                      f"translation may not silently stop (audit #2)")
        o["gather.gem_delay_sec"] = Expect(
            legacy.get("gem_delay_sec"), "the legacy preset's own gem delay")
        # orchestrator.py's hardcoded rescue literals, now explicit profile data.
        o["abilities.falling_samples"] = Expect(
            mig.FALLING_SAMPLES, "orchestrator.py wall watch: `falling >= 2`")
        o["abilities.deadband"] = Expect(
            mig.DEADBAND, "orchestrator.py wall watch: `ext < prev - 0.01`")
        o["abilities.collapse_from"] = Expect(
            mig.COLLAPSE_FROM_ABOVE, "orchestrator.py wall watch: `prev > 0.3`")
        o["abilities.burst_cancel_sprint"] = Expect(
            True, "orchestrator.py burst cancels the intro sprint as step one")
        o["abilities.burst_retaps"] = Expect(
            mig.BURST_RETAPS, "orchestrator.py retap loop: `for attempt in (1,2,3)`")
        if ab.get("nuke_on_fleet"):
            # The fleet-mark throttle used to be validated and then dropped
            # (audit #6); now it is carried, and 5s is what orchestrator.py has
            # always used: `rs.fleet_try_at = now_m + 5.0`.
            o["abilities.nuke_on_fleet.throttle_sec"] = Expect(
                mig.NUKE_THROTTLE_SEC,
                "orchestrator.py fleet nuke: `rs.fleet_try_at = now_m + 5.0`")
            # SITE 2 of 2 for require_ready.
            o["abilities.nuke_on_fleet.require_ready"] = Expect(
                mig.FLEET_REQUIRE_READY,
                "orchestrator.py:455/737 call `fire_button(frame, 'nuke', ...)` "
                "PLAIN, so the fleet nuke takes the require_ready=True "
                "default (orchestrator.py:167) - a scheduled tap can wait for a "
                "button it can actually see")
        o["abilities.refire_guard_sec"] = Expect(
            mig.REFIRE_GUARD_SEC,
            "orchestrator.py can_fire(): `now - rs.last_fire[name] > 15`")
        # require_ready IS PER SITE and the THREE sites do not agree, so a
        # single flat key could only ever be right about one of them
        # (coordinator ruling 2026-08-18, after this verifier's cross-check).
        # THE BURST IS TWO SITES over two different mechanisms (P3 finding,
        # ruled 2026-08-18). Not a rename of one another: both compile, both
        # are false on golden, and neither may stand in for the other.
        # SITE 1a - the WALL burst, reached by template match:
        o["abilities.burst_require_match"] = Expect(
            mig.BURST_REQUIRE_MATCH,
            "orchestrator.py:500 takes the matched Demon Mode centre when there is "
            "one and falls back to the fixed RESCUE_DM_PT when there is not. "
            "False keeps that fallback; true would refuse the blind tap")
        # SITE 1b - the HP-PATH rescue DM, reached through fire_button:
        o["abilities.burst_require_ready"] = Expect(
            mig.BURST_REQUIRE_READY,
            "orchestrator.py:800 fires the hp-path rescue DM with "
            "require_ready=False - the ready test is mean brightness of a "
            "band that is mostly battlefield, so a dark field must not veto "
            "the tap")
        # SITE 3 (site 2 is nuke_on_fleet.require_ready, above):
        o["abilities.hp_nuke_require_ready"] = Expect(
            mig.HP_NUKE_REQUIRE_READY,
            "orchestrator.py:806 fires the hp-path rescue nuke via a PLAIN "
            "fire_button(frame, 'nuke', ...), i.e. the require_ready=True "
            "default. `nuke_below` is read only on that branch - the wall "
            "branch hands the whole rescue to _fast_wall_watch")
        o["abilities.require_ready"] = Expect(
            ABSENT,
            "a flat require_ready would conflate four disagreeing call "
            "sites; it is split into burst_require_match, "
            "burst_require_ready, hp_nuke_require_ready and "
            "nuke_on_fleet.require_ready")
        o["abilities.sw_immunity_sec"] = Expect(
            ab.get("sw_immunity_sec"), "legacy value (absent -> null, and "
                                       "orchestrator reads `.get(...) or 0`)")
        o["abilities.end_sprint_after_sw"] = Expect(
            bool(ab.get("end_sprint_after_sw", False)),
            "legacy value; absent is falsy at the only read site")
        o["runner"] = Expect(None, "orchestrator-kind: orchestrator.py runs it in-process")
        o["runner_args"] = Expect(None, "orchestrator-kind: no external runner")
        if pair.kind == "coin":
            # P6 LANDED THE READERS, so these two are emitted now - and the
            # oracle is still an EQUIVALENCE oracle: the legacy preset had no
            # such keys and ran with no wave cap and no sprint handling, so
            # legacy behaviour is exactly the compiled DEFAULTS. A migrated
            # blueprint that states either one is asking for new behaviour and
            # this check is what says so.
            o["cancel_sprint"] = Expect(
                False, "P6 knob, default: legacy never touched the sprint")
            o["max_wave"] = Expect(
                None, "P6 knob, default: legacy ran with no wave cap")
            # `count` did NOT come back. There is one counting authority and
            # it is the plan block (P6 ruling) - a per-blueprint coin count
            # would be a cap the day counter cannot reconcile.
            o["count"] = Expect(ABSENT, "runs per day live on the PLAN BLOCK; "
                                        "legacy ran unbounded")
        if pair.kind == "tournament":
            o["in_run_actions"] = Expect(
                [], "P6 schedule, default: legacy swapped no cards mid-run")
        return o

    # ---- script kinds: a BEHAVIOURAL oracle, not a dict-shape one.
    o["abilities"] = Expect(
        _script_abilities(),
        "no script runner reads preset abilities; the compiled dict must be "
        "rescue-DISABLED (rescue_bar null AND dm_below null) so no consumer "
        "can enter the audit #3 crash loop or tap Demon Mode blind")
    o["uw_wanted"] = Expect({}, "no script normalizes UWs off the preset")
    # Per KEY, not as one dict: what matters is that CL is off AND that no
    # wave parameter can latch it back on (audit #12), and each of those is
    # its own claim with its own reason.
    o["chain_lightning.enabled"] = Expect(
        False, "no script drives Chain Lightning")
    o["chain_lightning.always_on"] = Expect(
        False, "...and it is not left on either")
    for key in ("always_on_above", "pre_mark_waves", "off_after_waves"):
        o[f"chain_lightning.{key}"] = Expect(
            None, f"no CL choreography for a script runner: {key} must be "
                  f"explicitly null, never an inherited farm range (#12)")
    o["shopping"] = Expect([], "no script visits the upgrade panel")
    o["restart_via_home"] = Expect(
        False, "each script owns its own restart (shard RETRY / quest cycle)")
    o["runner"] = Expect(legacy.get("runner"),
                         "the script config.yaml launches today")

    if pair.kind == "shard":
        tier = mig._int_const("flows/shard.py", "TIER")
        runs = mig._int_const("scheduling/combo.py", "SHARD_RUNS")
        delay = mig._pair_const("flows/shard.py", "GEM_DELAY_SEC")
        o["tier"] = Expect(tier, "flows/shard.py TIER")
        o["count"] = Expect(
            ABSENT, "the loop budget travels as --loops in runner_args, "
                    "which is what flows/shard.py actually parses; a second copy "
                    "in the preset would be a place to disagree with it")
        o["runner_args"] = Expect(
            {"--loops": str(runs), "--tier": str(tier)},
            "combo.SHARD_RUNS owns the loop count now; the tray's legacy "
            "--loops 0 meant 'until stopped'", cmp="flags")
        o["gather.flying_gem"] = Expect(
            True, "flows/shard.py GemWatch (flows/shard.py:131) claims flying gems")
        o["gather.gem_delay_sec"] = Expect(delay, "flows/shard.py GEM_DELAY_SEC")
        for flag in ("ad_gems", "quests_8h", "quest_rewards", "guild"):
            o[f"gather.{flag}"] = Expect(
                False, f"flows/shard.py has no {flag} code at all - its loop is "
                       f"tier/wave/nuke/surrender and leaves no gap for one")
        o["gem_delay_sec"] = Expect(delay, "flows/shard.py GEM_DELAY_SEC")
    elif pair.kind == "uw_grant_quest":
        o["tier"] = Expect(legacy.get("tier"), "config.yaml preset tier")
        o["rides"] = Expect(int(_legacy_flags(legacy)["--rides"]),
                            "legacy runner_args --rides")
        o["reroll_at_wave"] = Expect(
            mig._int_const("flows/quest_sm.py", "RESTART_AT_WAVE"),
            "flows/quest_sm.py RESTART_AT_WAVE")
        o["ride_to_wave"] = Expect(
            mig._int_const("flows/quest_sm.py", "RIDE_TO_WAVE"),
            "flows/quest_sm.py RIDE_TO_WAVE")
        o["uw_setup"] = Expect(mig.quest_sm_uw_setup(),
                               "flows/quest_sm.py uw_setup(), parsed from source")
        o["grant_targets"] = Expect(
            ["smart_missiles"], "quest_sm reports every grant as "
                                "smart_missiles; other targets refused until "
                                "the runner is generic (audit #7)")
        o["runner_args"] = Expect(_legacy_flags(legacy),
                                  "unchanged from config.yaml", cmp="flags")
        o["gather"] = Expect({}, "flows/quest_sm.py has no gem/reward code at all")
        o["gem_delay_sec"] = Expect(
            legacy_gem_delay(), "inert: gather is empty, nothing reads it")
    elif pair.kind == "cycle_quest":
        o["tier"] = Expect(legacy.get("tier"), "config.yaml preset tier")
        o["cycles"] = Expect(int(_legacy_flags(legacy)["--cycles"]),
                             "legacy runner_args --cycles")
        o["cycle_sec"] = Expect(
            mig._float_const("flows/quest_ilm.py", "EXIT_AFTER_SEC"),
            "flows/quest_ilm.py EXIT_AFTER_SEC")
        o["runner_args"] = Expect(_legacy_flags(legacy),
                                  "unchanged from config.yaml", cmp="flags")
        o["gather"] = Expect({}, "flows/quest_ilm.py has no gem/reward code at all")
        o["gem_delay_sec"] = Expect(
            legacy_gem_delay(), "inert: gather is empty, nothing reads it")
    return o


def legacy_gem_delay():
    """normal_run's gem delay - what the compiler carries as its default."""
    presets = load_presets()
    return merge_preset(presets, "normal_run").get("gem_delay_sec")


# Paths that carry no behaviour for a kind BECAUSE another oracled path is
# empty. Value-free, but conditional on a proof, not on a name.
INERT_IF = {
    "shop_interval_sec": ("shopping", [],
                          "inert: compiled shopping is empty, so the sweep "
                          "clock is never read"),
}


# ------------------------------------------------------------------ diffing
def deep_diff(old, new, path: str = "") -> list[tuple[str, str, object, object]]:
    """Recursive (kind, path, old, new). Lists recurse by index."""
    out: list[tuple[str, str, object, object]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in list(old) + [k for k in new if k not in old]:
            sub = f"{path}.{key}" if path else key
            if key not in new:
                out.append((REMOVED, sub, old[key], None))
            elif key not in old:
                out.append((ADDED, sub, None, new[key]))
            else:
                out += deep_diff(old[key], new[key], sub)
        return out
    if isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            sub = f"{path}[{i}]"
            if i >= len(new):
                out.append((REMOVED, sub, old[i], None))
            elif i >= len(old):
                out.append((ADDED, sub, None, new[i]))
            else:
                out += deep_diff(old[i], new[i], sub)
        return out
    if old != new:
        out.append((CHANGED, path, old, new))
    return out


def resolve(doc, path: str):
    """dotted path -> (found, value). No bracket syntax: oracles are whole
    values, so a list is compared as a list."""
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def check_oracle(oracle: dict, new: dict) -> tuple[dict, list[str]]:
    """Returns ({path: passed}, [failure lines])."""
    passed, failures = {}, []
    for path, exp in sorted(oracle.items()):
        found, got = resolve(new, path)
        if isinstance(exp.value, _Absent):
            passed[path] = not found
            if found:
                failures.append(f"{path}: present ({got!r}) but must be "
                                f"ABSENT ({exp.why})")
            continue
        if not found:
            passed[path] = False
            failures.append(f"{path}: MISSING (expected {exp.value!r} - "
                            f"{exp.why})")
            continue
        actual = _legacy_flags({"runner_args": got}) if exp.cmp == "flags" \
            else got
        ok = actual == exp.value
        passed[path] = ok
        if not ok:
            failures.append(f"{path}: {actual!r} != {exp.value!r} ({exp.why})")
    return passed, failures


def _leaf_paths(prefix: str, value):
    """Every leaf inside `value`, as dotted paths under `prefix`."""
    if isinstance(value, dict) and value:
        for key, sub in value.items():
            yield from _leaf_paths(f"{prefix}.{key}", sub)
    else:
        yield prefix                     # scalars, lists and {} are leaves


def _ancestors(path: str):
    """'gather.gem_delay_sec[0]' -> ['gather.gem_delay_sec', 'gather']."""
    bare = path.split("[", 1)[0]
    parts = bare.split(".")
    for i in range(len(parts), 0, -1):
        yield ".".join(parts[:i])


def classify(kind: str, path: str, old, new_val, pair: Pair, passed: dict,
             oracle: dict, compiled: dict) -> str | None:
    """None = unexplained. Otherwise the reason, for the table."""
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    bare = path.split("[", 1)[0]

    # 1. An oracle that PASSED is a proof about this path (or its parent):
    #    the value was checked against legacy source, so the diff is explained
    #    by that check and not by the key's name.
    for anc in _ancestors(path):
        if anc in oracle:
            if passed.get(anc):
                return f"oracle {anc}: {oracle[anc].why}"
            return None                  # oracle exists and FAILED: unexplained

    # 1b. A whole subtree added at once (old had no `gather` key at all) is
    #     explained only if EVERY leaf in it is separately oracled and every
    #     one of those checks passed. One un-oracled key inside and the whole
    #     addition stays unexplained - that is the audit #2 lesson: `gather`
    #     was forgiven as one opaque dict while a flag inside it had flipped.
    under = {p for p in oracle if p.startswith(f"{bare}.")}
    if under and kind == ADDED:
        leaves = set(_leaf_paths(bare, new_val))
        if leaves <= under and all(passed.get(p) for p in leaves):
            return (f"oracle: all {len(leaves)} leaves under {bare} checked "
                    f"against legacy source")
        missing = sorted(leaves - under)
        if missing:
            return None                  # something in the subtree is unchecked

    # 2. Per-pair documented allowance - path AND value.
    if bare in pair.allow:
        want, why = pair.allow[bare]
        return why if new_val == want else None

    if leaf in IGNORED_LEAF:
        return "ignored (tray label)"
    if kind == REMOVED:
        return ("dropped by design" if leaf in ALLOWED_DROPPED else None)

    # 3. Null additions are invisible under .get() - EXCEPT where the runtime
    #    subscripts or does arithmetic on the key.
    if new_val is None and kind in (ADDED, CHANGED):
        if bare in BARE_SUBSCRIPT or leaf in BARE_SUBSCRIPT:
            # abilities.dm_below null is LEGAL when the rescue is switched
            # off wholesale - that is the audit #3 fix, not the bug.
            if bare == "abilities.dm_below":
                found, bar = resolve(compiled, "abilities.rescue_bar")
                if found and bar is None:
                    return ("null dm_below with rescue_bar null: rescue "
                            "disabled wholesale (audit #3 fix)")
            return None
        return "null addition (== absent under .get())"

    # 4. Inert because another, oracled, path is empty.
    if bare in INERT_IF:
        other, want, why = INERT_IF[bare]
        found, got = resolve(compiled, other)
        if found and got == want and passed.get(other, other in oracle):
            return why
    return None


# ------------------------------------------------------------------ loading
def _by_path(path: Path):
    spec = importlib.util.spec_from_file_location("autopilot_playerprofile",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["autopilot_playerprofile"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_profile_module():
    """Import playerprofile.py - the compiler.

    It is `playerprofile`, not `profile`, precisely BECAUSE `profile` is a
    Python stdlib module (the profiler): on any path where the repo is not
    first, `import profile` succeeds, returns the profiler, and fails with
    AttributeError three frames later. The rename makes a plain import safe.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        mod = importlib.import_module(MODULE)
    except ImportError:
        path = ROOT / f"{MODULE}.py"
        if not path.exists():
            raise SystemExit(
                f"{MODULE}.py not found at {path} - nothing to verify")
        mod = _by_path(path)
    for fn in ("load", "compile_preset"):
        if not hasattr(mod, fn):
            raise SystemExit(f"{MODULE}.py has no {fn}() - API mismatch")
    return mod


def load_presets() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return cfg["presets"]


# -------------------------------------------------------------------- main
def run(profile_name: str, show_all: bool = False) -> int:
    presets = load_presets()
    mod = load_profile_module()
    prof = mod.load(profile_name)
    bad = 0

    if hasattr(mod, "validate"):
        problems = mod.validate(prof)
        if problems:
            # AUDIT #10: a validation error FAILS the run. Printing it and
            # exiting 0 is how a broken profile reaches a farm boundary.
            print(f"{MODULE}.validate() rejected the profile:")
            for p in problems:
                print(f"  ! {p}")
            bad += 1

    rows, details, oracle_lines = [], [], []
    for pair in MAPPING:
        if pair.old not in presets:
            rows.append((pair.old, pair.bp, "MISSING", "no such preset"))
            bad += 1
            continue
        legacy = merge_preset(presets, pair.old)
        try:
            new = mod.compile_preset(prof, pair.bp)
        except Exception as e:                       # noqa: BLE001
            rows.append((pair.old, pair.bp, "ERROR", repr(e)))
            bad += 1
            continue
        oracle = build_oracle(pair, legacy, profile_name)
        passed, failures = check_oracle(oracle, new)
        for line in failures:
            oracle_lines.append((pair.bp, line))

        unexplained = 0
        for kind, path, o, n in deep_diff(legacy, new):
            why = classify(kind, path, o, n, pair, passed, oracle, new)
            if not why:
                unexplained += 1
            details.append(("ok" if why else "UNEXPLAINED", pair.old, kind,
                            path, o, n, why or "not a documented difference"))
        n_oracle = len(oracle)
        n_fail = len(failures)
        ok = not unexplained and not n_fail
        if not ok:
            bad += 1
        rows.append((pair.old, pair.bp, "OK" if ok else "FAIL",
                     f"{n_oracle - n_fail}/{n_oracle} oracle, "
                     f"{unexplained} unexplained"))

    w = max(len(r[0]) for r in rows) + 2
    print(f"\n{'OLD PRESET'.ljust(w)}{'BLUEPRINT'.ljust(16)}"
          f"{'STATUS'.ljust(8)}NOTE")
    print("-" * (w + 52))
    for name, bp, status, note in rows:
        print(f"{name.ljust(w)}{bp.ljust(16)}{status.ljust(8)}{note}")

    if oracle_lines:
        print("\nORACLE FAILURES")
        for bp, line in oracle_lines:
            print(f"  [{bp}] {line}")

    shown = details if show_all else [d for d in details if d[0] == "UNEXPLAINED"]
    if shown:
        print("\nDIFFERENCES" if show_all else "\nUNEXPLAINED DIFFERENCES")
        for tag, name, kind, path, o, n, why in shown:
            print(f"  [{tag}] {name}: {kind} {path}\n"
                  f"        old={o!r}\n        new={n!r}\n        why={why}")
    unexplained = [d for d in details if d[0] == "UNEXPLAINED"]
    if unexplained:
        groups: dict[str, list[str]] = {}
        for _, name, kind, path, _o, _n, _w in unexplained:
            groups.setdefault(f"{kind} {path}", []).append(name)
        print("\nBY PATH")
        for key in sorted(groups):
            print(f"  {key.ljust(34)} {', '.join(groups[key])}")
    if not show_all:
        print(f"\n({len(details) - len(unexplained)} explained difference(s) "
              f"hidden; --all shows them)")
    print(f"\n{len(unexplained)} unexplained difference(s), "
          f"{len(oracle_lines)} oracle failure(s); {bad} failing item(s).")
    return 1 if bad else 0


def run_hash(profile_name: str) -> int:
    mod = load_profile_module()
    if not hasattr(mod, "compiled_hash"):
        raise SystemExit(f"{MODULE}.py has no compiled_hash() - API mismatch")
    prof = mod.load(profile_name)
    print(f"{'BLUEPRINT'.ljust(16)}COMPILED HASH")
    for pair in MAPPING:
        print(f"{pair.bp.ljust(16)}"
              f"{mod.compiled_hash(mod.compile_preset(prof, pair.bp))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="default")
    ap.add_argument("--all", action="store_true",
                    help="print the explained differences too")
    ap.add_argument("--hash", action="store_true",
                    help="print compiled_hash per blueprint (attestation)")
    args = ap.parse_args(argv)
    return run_hash(args.profile) if args.hash else run(args.profile, args.all)


if __name__ == "__main__":
    raise SystemExit(main())
