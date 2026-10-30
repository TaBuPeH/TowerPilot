"""Inner Land Mines event-quest runner (user method, 2026-08-16).

We do NOT own Inner Land Mines. Space Displacer's unique effect ("On Land
Mine Spawn: 30% chance to spawn an automatically organizing Inner Land Mine
instead, Max 20") spawns them anyway, and the event quest counts THEIR
kills. The farm is short Tier 1 cycles:

    loadout inner_land_mines_quest -> Tier 1 -> all 6 UWs OFF ->
    wait for the first SUMMON to pass -> surrender -> RETRY -> repeat

Setup requires clean Home. Only cycles this process starts and continuously
observes may be surrendered; a restarted process refuses existing battles,
menus and stats rather than inheriting authority.

The exit clock is REAL-LIFE SECONDS from the first readable wave (user):
at x5 game speed the first Summon guardian arrives ~70s in and lasts 32s,
and the mine kills it feeds are the whole point of the cycle - so the run
ends only after Summon has fully passed (~10 kills/cycle measured, 10/30
on the first live run). Restarting earlier farms nothing; staying longer
farms nothing extra until the NEXT Summon, which is not worth the wait.

UW toggles persist between runs, so cycle 1 pays the full toggle sweep and
later cycles just verify. Progress must be CLAIMED on the event screen
after each completed quest level or the next phase's progress stays
invisible - claiming is not automated here yet.
"""
import argparse
import sys
import time
from pathlib import Path

# Flow files are runnable as scripts (`python flows/quest_ilm.py`) with the
# backend root as cwd - put that root on sys.path so sibling modules resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import settings


# Every OWNED UW must be off for the cycle (mine kills are the whole
# point). chronofield added 2026-08-29 (user acquired it). An unowned name
# in this list would just log ilm_uw_fail and continue - uw_toggle degrades
# on a missing panel row - so err on the side of listing.
UWS = ("chain_lightning", "death_wave", "golden_tower",
       "poison_swamp", "black_hole", "spotlight", "chronofield")
# User-tuned, 2026-08-16: the Summon numbers (70s arrival + 32s duration)
# are GAME-time; at x5 they compress to ~20.4 real seconds, but a 22.4s
# exit proved "too soon" live - 25 flat is the user's call. Real-life
# seconds from the first readable wave, ~wave 40-45.
EXIT_AFTER_SEC = 25.0


# The one blueprint kind this script runs, and the preset it binds when
# nobody names a blueprint.
KIND = "cycle_quest"
LEGACY_PRESET = "quest_inner_land_mines"

# What this flow is, for the registry (flows/__init__.py).
FLOW = {
    "templates": ['buttons/retry.png',
     'home/game_stats_home.png',
     'home/battle_btn.png',
     'buttons/return_to_game.png',
     'uw/inner_land_mines.png',
     'uw/toggle_on.png',
     'uw/toggle_off.png'],
    "kind": "cycle_quest",
    "label": "Quest: short cycles (Inner Land Mines)",
    "runner": "flows/quest_ilm.py",
    # This runner owns its whole setup (loadout, tier, battle) end to end -
    # the scheduler must not walk the game Home underneath it.
    "handoff": "none",
    "blueprint_args": [
        {"flag": "--cycles", "fields": ["cycles", "count"], "default": 0},
    ],
    "legacy_preset": "quest_inner_land_mines",
}


def _preset() -> dict:
    """The ACTIVE preset, flat. Compiled blueprints are flat by contract; the
    legacy quest_inner_land_mines preset has none of these keys, so a missing
    key always falls back to the module constant."""
    from settings import CONFIG
    return CONFIG["presets"].get(CONFIG.get("preset")) or {}


def _bp_arg(value: str) -> str:
    """--preset accepts a compiled blueprint and NOTHING ELSE - a non-bp_
    value stays the argparse error it is today, because this script has never
    had the flag and a legacy tray launch must keep failing to parse rather
    than starting a surrender loop it never used to start."""
    if not value.startswith("bp_"):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a compiled blueprint - this runner takes "
            f"--preset bp_<name> (kind {KIND!r}) or no --preset at all")
    return value


def _bind_preset(instance: str, name: str | None) -> None:
    """Bind the process preset, REFUSING a blueprint of the wrong kind.

    No --preset binds the legacy preset, as before; a `bp_` preset of another
    kind is refused before the first capture. Selecting a blueprint never
    grants authority over a run; the process must start its own quest cycle.
    """
    from settings import CONFIG
    if name is None:
        settings.select_instance(instance, LEGACY_PRESET)   # legacy, verbatim
        return
    if not name.startswith("bp_"):
        raise SystemExit(f"REFUSED: --preset {name} is not a compiled "
                         f"blueprint (bp_<name>)")
    settings.select_instance(instance, name)
    kind = (CONFIG["presets"].get(name) or {}).get("kind")
    if kind != KIND:
        raise SystemExit(f"REFUSED: {name} is a {kind!r} blueprint - "
                         f"flows/quest_ilm.py runs {KIND!r} blueprints only "
                         f"(nothing was captured or tapped)")


def ensure_uws_off(*, guard=None) -> list[str]:
    """All six UWs OFF, verified. Returns the ones that could not be set."""
    from runtime import logger
    from interactions import shopper
    fails = []
    for uw in UWS:
        if not shopper.uw_toggle(uw, want_on=False,
                                 **({"guard": guard} if guard is not None else {})):
            fails.append(uw)
    if fails:
        logger.event("ilm_uw_fail", weapons=fails)
    return fails


def one_cycle(n: int, last: bool, run) -> None:
    from runtime import logger
    from flows import shard
    from device import capture
    logger.event("ilm_cycle", n=n, stage="begin")
    shard.wait_for_wave(1, guard=run.guard, recover_home=False)
    t0 = time.monotonic()               # run clock starts at first wave
    shard.ensure_max_speed(guard=run.guard)
    # UW toggles PERSIST across runs, so only the first cycle pays the
    # panel sweep (user, 2026-08-16: "it was enough to just retry") - and
    # the sweep's scrolling could outlast the whole 22s window anyway.
    if n == 1:
        ensure_uws_off(guard=run.guard)
    # REAL seconds, not waves: waves fly at x5 on Tier 1 but the Summon
    # guardian runs on the wall clock, which is what the user's 70s+32s
    # numbers are measured in.
    # `or` not a dict default: compile_preset emits cycle_sec: None when the
    # blueprint omits it, and None would break the arithmetic below.
    cycle_sec = float(_preset().get("cycle_sec") or EXIT_AFTER_SEC)
    # GEMS STAY GATHERED (user, 2026-08-28: circling gems went unclaimed
    # through whole quest batches): the old plain sleep made this the only
    # battle loop that never looked at the screen. Poll frames through the
    # wait and let shard.GemWatch claim - same rules as every other loop.
    # The ~350ms grab paces the loop; overshooting the deadline by one grab
    # is fine (25s is the user's MINIMUM, "22.4s proved too soon").
    from runtime.action_session import ActionSession
    gems = shard.GemWatch(guard=run.guard, session=ActionSession.for_operation('quest_ilm'))
    while cycle_sec - (time.monotonic() - t0) > 0:
        frame = run.check(capture.grab())
        run.observe(gems.poll(frame))
    logger.event("ilm_cycle", n=n, stage="summon passed",
                 elapsed=round(time.monotonic() - t0, 1))
    if last:
        run.abandon(to_home=True)       # surrender -> HOME, no stats banked
    else:
        run.abandon()                   # surrender -> RETRY, next run starts
    logger.event("ilm_cycle", n=n, stage="done")


def _cli(argv=None):
    """The command line, lifted out of main() so the argument CONTRACT can be
    tested without running a quest."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--cycles", type=int, default=None,
                    help="cycles to run (default: the blueprint's `cycles`/"
                         "`count`, else 5)")
    ap.add_argument("--preset", type=_bp_arg, default=None,
                    help="a compiled blueprint preset (bp_<name>, kind "
                         "cycle_quest). Any other value is an argparse error, "
                         "exactly as --preset has always been here")
    return ap.parse_args(argv)


def main() -> None:
    a = _cli()
    from runtime.device_lease import prepare_instance
    prepare_instance(a.instance)
    _bind_preset(a.instance, a.preset)
    from player import readiness
    readiness.require(settings.ROOT, settings.CONFIG, _preset())
    # PRECEDENCE: explicit CLI > blueprint > module constant. The tray passes
    # --cycles 40 in runner_args, so the legacy launch is unchanged.
    cycles = (a.cycles if a.cycles is not None else
              int(_preset().get("cycles") or _preset().get("count") or 5))
    # The legacy preset already carries tier 1 and no loadout, so these read
    # back exactly the values they replace.
    lo_name = _preset().get("loadout") or "inner_land_mines_quest"
    tier = int(_preset().get("tier") or 1)
    from interactions import loadout
    from runtime import logger
    from flows import shard
    logger.event("ilm_quest", stage="begin", cycles=cycles)
    from flows._quest_run import QuestRun
    run = QuestRun()
    run.home()                         # foreign runs/menus/aftermath are untouched
    loadout.apply(lo_name)
    run.home()
    shard.set_tier(tier)
    run.start()
    for n in range(1, cycles + 1):
        one_cycle(n, last=(n == cycles), run=run)
    # v29: the quest's Space Displacer equip permanently displaced the farm
    # health module into the (auto-saving) Farm preset - put it back before
    # handing the account on. Declarative (`modules_restore` in the
    # loadout), so a restarted process still knows what to restore.
    restore = loadout.spec(lo_name).get("modules_restore")
    if restore:
        run.home()
        logger.event("ilm_quest", stage="restore modules",
                     plan=[list(m) for m in restore])
        loadout.apply_modules([tuple(m) for m in restore])
    logger.event("ilm_quest", stage="done", cycles=cycles)


if __name__ == "__main__":
    from flows import run_main
    run_main("quest_ilm", main)
