"""The RUN harness: one shape for every kind of farming run.

Your grouping, made executable. Everything the bot does is one of three
things, and they were previously tangled together inside each script:

    LOADOUT      what you equip          -> loadout.py (cards/guardians/modules)
    RUN          what you do with it     -> here
    CHORES       what you do in between  -> chores.py

Every run type differs in exactly five places and nothing else:

    loadout      which named bundle to equip first
    tier         which difficulty to enter on (None = leave it alone)
    stop         when this run is finished  (frame, wave) -> bool
    in_run       what to watch while it lasts (gems, abilities, shopping)
    teardown     what to put back afterwards

So a coin farm, a shard farm and a quest run are three sets of arguments, not
three programs. That matters most for the tournament, where three entries need
three module sets - previously impossible, because the plan was a constant.

What this deliberately does NOT own: the orchestrator's in-run policy. A coin farm is
hours of ability decisions and shopping, and that logic is hard-won and stays
where it is. The harness starts a run, watches for its stop condition, and
tears down - it does not try to be the orchestrator.
"""
import time

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python runtime/harness.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from device import capture
from runtime import logger
from interactions import loadout
from flows import shard
from interactions import tourney
from vision import wave_reader
from interactions.tourney import Abort

POLL = 0.3
OFF_BATTLE_FRAMES = 10          # ~3s of no battle before believing it, so a
                                # loop boundary is not mistaken for an exit


class Run:
    """One farming run: equip, enter, watch, tear down."""

    def __init__(self, name: str, loadout_name: str | None = None,
                 tier: int | None = None, stop=None, in_run=None,
                 teardown=None, timeout: float = 3600.0,
                 restore_cards: bool = False):
        self.name = name
        self.loadout = loadout_name
        self.tier = tier
        self.stop = stop or (lambda frame, wave: False)
        self.in_run = in_run
        self.teardown = teardown
        self.timeout = timeout
        self.restore_cards = restore_cards
        self.previous_cards: str | None = None

    # -- phases ----------------------------------------------------------
    def equip(self):
        if not self.loadout:
            return
        done = loadout.apply(self.loadout, restore_cards=self.restore_cards)
        self.previous_cards = done.get("previous_cards")

    def enter(self):
        tourney.ensure_home()
        if self.tier:
            shard.set_tier(self.tier)
        shard.start_battle()

    def watch(self):
        """Poll until stop() says we are done. Returns the last wave seen.

        Every harness run claims floating gems by default (user, 2026-08-28:
        circling gems went unclaimed through whole quest batches) - GemWatch
        is the same claim discipline as the shard loop, gated by the same
        gather.flying_gem policy via shard.gem_opts().
        """
        deadline = time.monotonic() + self.timeout
        off = 0
        wave = None
        gems = shard.GemWatch(**shard.gem_opts())
        while time.monotonic() < deadline:
            frame = capture.grab()
            w = wave_reader.read_wave(frame)
            if w is not None:
                wave = w
                off = 0
                gems.poll(frame)
                if self.in_run:
                    self.in_run(frame, w)
                if self.stop(frame, w):
                    return wave
            elif not shard._in_run(frame):
                # Do not trust a single odd frame: every run boundary crosses a
                # gap where the old run is gone and the new one has not drawn.
                off += 1
                if off >= OFF_BATTLE_FRAMES:
                    logger.shot(frame, f"harness_{self.name}_left_run")
                    raise Abort(f"{self.name}: left the battle screen")
            time.sleep(POLL)
        raise Abort(f"{self.name}: timed out after {self.timeout:.0f}s")

    def finish(self, wave):
        if self.teardown:
            self.teardown(self)
        # Hand the deck back. A quest run that swaps to the No Card preset and
        # does not restore it leaves the NEXT run cardless - the exact failure
        # seen live today. None means "nothing read as selected", which must be
        # treated as do-not-restore rather than a guess.
        if self.restore_cards and self.previous_cards:
            loadout.apply_cards(self.previous_cards)
        logger.event("run_done", run=self.name, wave=wave)

    def go(self):
        logger.event("run_begin", run=self.name, loadout=self.loadout,
                     tier=self.tier)
        self.equip()
        self.enter()
        wave = self.watch()
        self.finish(wave)
        return wave


# ----------------------------------------------------------- run definitions

def wave_at_least(target: int):
    """Stop once the wave counter reaches `target`."""
    return lambda frame, wave: wave is not None and wave >= target


def quest_nocard_run(target_wave: int = 61) -> Run:
    """"Reach wave 20/40/60 with no card equipped on tier 14 or above".

    Waits for target_wave (61 for the wave-60 level) so the qualifying wave is
    COMPLETED rather than merely reached, then surrenders. Restores whatever
    card preset it displaced - without that it hands the next run an empty deck.
    """
    return Run("quest_nocard", loadout_name="quest_nocard", tier=14,
               stop=wave_at_least(target_wave),
               # end_round, NOT shard.abandon_run: abandon_run finishes by
               # tapping RETRY, which would immediately start ANOTHER cardless
               # run instead of stopping. end_round surrenders and returns to
               # Home, which is where the card preset has to be restored from
               # and where the next thing is dispatched.
               teardown=lambda r: tourney.end_round(),
               restore_cards=True, timeout=900.0)


def shard_run() -> Run:
    """Kept as a thin wrapper: flows/shard.py owns its own tight loop, which is
    tuned to ~90s and has its own nuke timing. Re-implementing it here would
    trade working, measured code for symmetry."""
    return Run("shard_farm", loadout_name="shard_farm", tier=18,
               stop=wave_at_least(shard.SPRINT_WAVE),
               # end_round, not nothing: every flow exit must close its
               # live battle (2026-08-29) - a leftover run is no longer
               # ended by the next handoff, it gets ADOPTED or held on.
               teardown=lambda r: tourney.end_round(), timeout=600.0)


RUNS = {
    "quest_nocard": quest_nocard_run,
    "shard_farm": shard_run,
}


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="accepted for tray parity")
    ap.add_argument("--run", required=True, choices=sorted(RUNS),
                    help="which run definition to execute")
    ap.add_argument("--repeat", type=int, default=1,
                    help="how many times (0 = until stopped)")
    return ap.parse_args()


def main() -> None:
    import settings
    a = _cli()
    settings.select_instance(a.instance)
    print(f"harness: {a.run} on {a.instance} "
          f"(repeat={a.repeat or 'forever'})")
    n = 0
    while a.repeat == 0 or n < a.repeat:
        n += 1
        RUNS[a.run]().go()
    print(f"completed {n} run(s)")


if __name__ == "__main__":
    # run_main, NOT a print-and-exit except block: under pythonw the print
    # goes nowhere and the death is invisible - exactly how the 2026-08-28
    # quest_nocard watcher vanished (it aborted "left the battle screen"
    # when a user-opened overlay hid the battle, leaving only a screenshot).
    from flows import run_main
    run_main("harness", main)
