"""One-off tier-ladder campaign driver (user, 2026-08-29).

One max-push run per tier, descending, each guarded by the
bp_coin_t18_legend blueprint (tournament nets, as_is loadout - every run
enters with whatever presets the game has selected; the driver never
touches the picker, cards, or category screens). The runflag contract does
the one-run-per-tier part: the flag is set before each run, the
orchestrator leaves at its death handler after collecting the run log, and
this driver steps the tier down and re-enters via the same primitives the
death handler itself uses.

When the ladder ends it marks today's tournament flag (the user played the
tournament by hand - combo must not spend a second ticket) and hands the
account back to combo for normal farming.

NOTE ON FLEETS (user, 2026-08-29): higher tiers spawn fleets at different
waves and durations - NOT 2495 + i*1000. The ladder preset is deliberately
fleet-agnostic: no fleet-mark nuke, wave-latched CL - so nothing here
depends on the tier-14 fleet calendar.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import settings

PRESET = "bp_coin_t18_legend"
CREATE_NO_WINDOW = 0x08000000


def _orchestrator_running() -> bool:
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
        except Exception:                       # noqa: BLE001 - process died
            continue
        if "python" in name and "orchestrator.py" in cmd:
            return True
    return False


def _spawn(args: list) -> subprocess.Popen:
    root = Path(__file__).resolve().parents[1]
    return subprocess.Popen([sys.executable, *args], cwd=str(root),
                            creationflags=CREATE_NO_WINDOW)


def _enter(tier: int) -> bool:
    """Get a run going on `tier` from wherever the last one left the screen:
    the stats dialog (the flag-stop's parking spot) or Home. Anything else
    is an Abort-style stop - log and refuse, never blind-tap."""
    from device import capture
    from runtime import logger
    from interactions import tourney
    from flows import shard
    import orchestrator as orch
    frame = capture.grab()
    if tourney.on_home(frame):
        shard.set_tier(tier)
        shard.start_battle()
        return True
    if orch.restart_from_home(frame, tier):     # stats dialog -> HOME -> battle
        return True
    logger.event("ladder", stage="entry failed", tier=tier,
                 shot=logger.shot(frame, f"ladder_entry_t{tier}"))
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default=None,
                    help="instance key from config.yaml (default: active_instance)")
    ap.add_argument("--tiers", default="17,16,15",
                    help="tiers still to run AFTER the currently live run, "
                         "in order (default: 17,16,15)")
    a = ap.parse_args()
    settings.select_instance(a.instance or settings.CONFIG["active_instance"])
    from runtime import logger
    from scheduling import runflag, daystate
    tiers = [int(t) for t in a.tiers.split(",") if t.strip()]
    # The LIVE run (T18) stops at its own death via the flag; this driver
    # never kills anything.
    runflag.request("tier_ladder")
    logger.event("ladder", stage="armed", tiers_left=tiers)
    while _orchestrator_running():
        time.sleep(20)
    for tier in tiers:
        runflag.clear()
        if not _enter(tier):
            raise SystemExit(1)
        runflag.request("tier_ladder")          # one run, then leave
        p = _spawn(["orchestrator.py", "--instance", a.instance,
                    "--preset", PRESET])
        logger.event("ladder", stage="run", tier=tier, pid=p.pid)
        p.wait()
        logger.event("ladder", stage="run done", tier=tier,
                     code=p.returncode)
    runflag.clear()
    # The tournament was played BY HAND today - combo's shared flag must say
    # so or the resumed day plan would spend a second, pricier ticket.
    daystate.mark_today("combo_tournament")
    p = _spawn(["scheduling\\combo.py", "--instance", a.instance])
    logger.event("ladder", stage="done", combo_pid=p.pid)


if __name__ == "__main__":
    # Under pythonw a print dies silently - the event log IS the console
    # (same contract as flows.run_main).
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:                  # noqa: BLE001 - log, re-raise
        try:
            from runtime import logger
            logger.event("runner_crashed", flow="tier_ladder",
                         error=f"{type(e).__name__}: {e}")
        except Exception:                       # noqa: BLE001
            pass
        raise
