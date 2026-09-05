"""Walk a compiled plan against the clock, and diff it against the constants.

    python tools/plan_sim.py --profile default --week 2026-08-17
    python tools/plan_sim.py --profile default --week 2026-08-17 --diff

THE SAFETY ARGUMENT FOR P5, and it is the same one P3 made for the presets:
not "the plan looks right", but "the decision the scheduler makes at every
minute of the week is the decision it makes today". `combo.due()` is four
constants and a three-line ladder; a plan is data. The two must agree on
`profiles/default.yaml` before the runtime is switched over, so this walks a
15-minute grid across seven days, asks BOTH, and prints every disagreement.

An empty diff is the whole point. Anything else is a plan that would schedule
the farm differently than the constants it claims to reproduce.

WHAT IS SIMULATED, AND WHAT IS NOT. Nothing runs, so nothing finishes: the
walk is a FRESH DAY at every step unless `--done` says otherwise. That is
deliberate - the completion path (shards exhausted -> coin) is a counter
question, not a clock question, and `--done shards` asks it directly rather
than pretending to know how long 100 shard runs take.

No adb, no capture, no runner: this imports playerprofile (which imports only
settings) and reads combo.py's constants without importing it, because
importing combo drags in the whole runner stack.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from player import playerprofile                                            # noqa: E402

GRID_MINUTES = 15


# ------------------------------------------------------------ the constants
#
# READ OUT OF combo.py'S SOURCE, never re-typed here. A copy of a constant is a
# constant that drifts, and the one thing this tool must not do is compare the
# plan against a stale idea of what the constants say. Same trick
# tools/migrate_profile.py uses on the runner scripts.

def constants(path: Path | None = None) -> dict:
    src = (path or ROOT / "scheduling" / "combo.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    want = {"SHARD_HOUR", "SHARD_RUNS", "TOURNEY_HOUR", "TOURNEY_DAYS"}
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in want:
                found[target.id] = ast.literal_eval(node.value)
    missing = want - set(found)
    if missing:
        raise SystemExit(f"combo.py no longer defines {', '.join(sorted(missing))} "
                         f"- this tool compares against constants that are gone")
    return found


def constant_due(now: datetime.datetime, done: set[str], const: dict) -> str:
    """combo.due(), restated over an explicit `done` set.

    Line for line the ladder in combo.py: the tournament outranks the shard
    block because it is the one thing with a closing window.
    """
    if (now.hour >= const["TOURNEY_HOUR"]
            and now.weekday() in const["TOURNEY_DAYS"]
            and "tournament" not in done):
        return "tournament"
    if (const["SHARD_RUNS"] > 0 and now.hour >= const["SHARD_HOUR"]
            and "shards" not in done):
        return "shards"
    return "coin"


# ------------------------------------------------------------------ the plan

def plan_due(plan: dict, now: datetime.datetime,
             done: set[str]) -> tuple[str, str, str]:
    """(block name, block id, preset) for the first eligible block.

    The reference implementation of the compiled contract, and the thing
    combo.due() is meant to become. Order is priority; `until` is exclusive; a
    block whose runs are spent is skipped.

    THE PROBE IS ALL THREE, not just the name (Codex P5, MED). Two plans can
    agree on "coin" at every minute of the week and still disagree about WHICH
    coin block that is - and the block id is the daystate counter key, so an id
    that changes under a steady block name is a counter that silently restarts.
    The name catches a schedule change; the id and the preset catch an identity
    one.
    """
    day = playerprofile.WEEKDAYS[now.weekday()]
    minute = now.hour * 60 + now.minute
    for block in plan["week"][day]:
        if not block["after_min"] <= minute < block["until_min"]:
            continue
        if block["count"] is not None and block["block"] in done:
            continue                    # its runs for today are spent
        return block["block"], block["id"], block["preset"] or ""
    return "idle", "", ""


# ------------------------------------------------------------------ the walk

def _fills(spec: str) -> dict:
    """`--fill shards=14:30` -> {"shards": 870}: the minute a block's counter
    fills, every day.

    The clock path is a window question and the counter path is a quota one,
    and the SWITCHOVER between them is where a scheduler rewrite actually goes
    wrong - so it is simulated rather than assumed (Codex P5, MED).
    """
    out = {}
    for item in (part.strip() for part in spec.split(",") if part.strip()):
        name, _, clock = item.partition("=")
        hh, _, mm = clock.partition(":")
        if not hh.isdigit() or not mm.isdigit():
            raise SystemExit(f"--fill wants block=HH:MM, got {item!r}")
        out[name.strip()] = int(hh) * 60 + int(mm)
    return out


def walk(profile_name: str, monday: datetime.date, done: set,
         show_diff: bool, combo_path: Path | None = None,
         step_minutes: int = GRID_MINUTES,
         fill: dict | None = None) -> int:
    prof = playerprofile.load(profile_name)
    problems = playerprofile.validate(prof)
    if problems:
        print(f"REFUSED: {profile_name} has {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 2
    plan = playerprofile.compile_plan(prof)
    if plan is None:
        # A RULES-ONLY PROFILE. compile_plan answers absence with absence, so
        # there is no schedule to walk and nothing to disagree with: the
        # scheduler runs its constants, which is what this tool compares
        # AGAINST. Saying so beats printing a week of "coin" that came from
        # nowhere.
        print(f"{profile_name}: no `plan` section - the scheduler runs the "
              f"combo constants, so there is no compiled plan to walk")
        return 0
    const = constants(combo_path)
    fill = fill or {}

    samples = diffs = jumps = 0
    previous = None
    rows = []
    midnight = datetime.datetime.combine(monday, datetime.time())
    for step in range(7 * 24 * 60 // step_minutes):
        now = midnight + datetime.timedelta(minutes=step * step_minutes)
        minute = now.hour * 60 + now.minute
        # `done` is per DAY: a counter that filled at 14:30 is spent for the
        # rest of that day and empty again at midnight.
        today = set(done) | {b for b, m in fill.items() if minute >= m}
        got, bid, preset = plan_due(plan, now, today)
        want = constant_due(now, today, const)
        samples += 1
        if got != want:
            diffs += 1
            if show_diff:
                rows.append(f"  {now:%a %H:%M}  plan={got:<10} "
                            f"constants={want:<10} ({bid or 'no block'})")
        elif previous and previous[0] == got and previous[1:] != (bid, preset):
            # IDENTITY CONTINUITY: the block name held, but the id or the
            # preset under it did not. The constant era has neither, so this
            # is reported on its own rather than diffed against it.
            #
            # AN ID CHANGE AT MIDNIGHT IS NOT DRIFT. Ids are weekday-prefixed
            # BY DESIGN - they are the per-day counter keys, and Tuesday's
            # count is not Wednesday's - so a run in flight across the
            # boundary keeps its identity through (preset, kind, count-bounds)
            # rather than through the id (SCHEMA.md, "identity for
            # continuity"). What is drift is the PRESET moving underneath a
            # steady block name: that is a different tower doing the work.
            boundary = (minute == 0 and preset == previous[2])
            rows.append(f"  {now:%a %H:%M}  {got}: "
                        f"{'day boundary, same preset' if boundary else 'IDENTITY DRIFT'} "
                        f"{previous[1]}/{previous[2] or '-'} -> "
                        f"{bid}/{preset or '-'}")
            if not boundary:
                jumps += 1
        elif not show_diff and (got, bid, preset) != previous:
            rows.append(f"  {now:%a %H:%M}  {got:<10} {bid:<12} {preset}")
        previous = (got, bid, preset)

    label = "DIFFS" if show_diff else "TRANSITIONS"
    grid = "1-min" if step_minutes == 1 else f"{step_minutes}-min"
    print(f"{profile_name}: week of {monday:%Y-%m-%d}, {grid} grid, "
          f"{samples} samples, done={sorted(done) or 'nothing'}"
          + (f", fill={fill}" if fill else ""))
    print(f"{label}:")
    print("\n".join(rows) if rows else "  (none)")
    print(f"diff vs combo constants: {diffs}/{samples} samples differ, "
          f"{jumps} identity jump(s)")
    return 1 if (diffs or jumps) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="default")
    ap.add_argument("--week", default=None,
                    help="any date in the week to walk (default: this Monday)")
    ap.add_argument("--diff", action="store_true",
                    help="print the disagreements instead of the transitions")
    ap.add_argument("--done", default="",
                    help="comma-separated blocks already finished today "
                         "(e.g. shards) - the counter path, not the clock one")
    ap.add_argument("--minutes", action="store_true",
                    help="walk EVERY minute (10080 samples) instead of the "
                         "15-minute grid - a grid cannot see a 07:59 drift")
    ap.add_argument("--fill", default="",
                    help="block=HH:MM[,...]: the minute a block's counter "
                         "fills, so the switchover minute is compared too "
                         "(e.g. shards=14:30)")
    args = ap.parse_args(argv)

    day = (datetime.date.fromisoformat(args.week) if args.week
           else datetime.date.today())
    monday = day - datetime.timedelta(days=day.weekday())
    done = {p.strip() for p in args.done.split(",") if p.strip()}
    return walk(args.profile, monday, done, args.diff,
                step_minutes=1 if args.minutes else GRID_MINUTES,
                fill=_fills(args.fill))


if __name__ == "__main__":
    raise SystemExit(main())
