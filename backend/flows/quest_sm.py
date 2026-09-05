"""Smart Missiles event-quest runner (user method, 2026-08-16).

We do NOT own Smart Missiles. On Tier 1, the perk roll can randomly grant
one of the three unowned ultimate weapons - Chrono Field, Inner Land Mines
or Smart Missiles - for the duration of the run. The quest counts Smart
Missiles kills, so the farm is a lottery loop:

    coin_farm loadout -> Tier 1 -> only Spotlight ON (5 UWs OFF) ->
    run with the Intro Sprint (waves = perk rolls) ->
    every ~15s REAL time scan the UW panel for a granted 7th weapon:
        wrong grant  -> surrender -> RETRY (one grant per run, no rerolls)
        Smart Missiles -> cancel the Intro Sprint, ride to wave 4500
                          (SM one-shots everything up to ~4500-5000, then
                          stops killing), then exit to Home
        nothing yet  -> keep rolling

Grant identification bootstraps: the three grantable weapons have no name
template until first sighted. An UNKNOWN row is never auto-restarted - it
could be the jackpot - it is logged with a crop (sm_unknown_uw) and the
runner waits for logs/<instance>/sm_decision to contain 'proceed' or
'restart' (the supervising agent reads the crop, saves the labelled
template for next time, writes the decision). Timeout holds the run: a
Tier 1 farm run in progress costs nothing.

The claim flow on the event screen is deliberately NOT here yet.
"""
import argparse
import os
import sys
import time
from pathlib import Path

# Flow files are runnable as scripts (`python flows/quest_sm.py`) with the
# backend root as cwd - put that root on sys.path so sibling modules resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import settings


KNOWN_UWS = ("chain_lightning", "death_wave", "golden_tower",
             "poison_swamp", "black_hole", "spotlight")
GRANT_UWS = ("smart_missiles", "inner_land_mines", "chronofield")
SCAN_EVERY_SEC = 15.0
RIDE_TO_WAVE = 6500         # was 4500; tier 3 is 15000 CUMULATIVE kills and
                            # one ride at 2.62 kills/wave completes it near
                            # wave 6280. T1 HP scales gently so the ~4500
                            # falloff (higher-tier lore) likely never comes;
                            # the supervising agent watches kills/wave and
                            # bails to a second lottery ride only if the
                            # measured rate sags (user, 2026-08-16)
RESTART_AT_WAVE = 1000      # no grant by here -> the roll is spent, reroll
                            # (user, 2026-08-16)
DECISION_TIMEOUT_SEC = 300.0


# The one blueprint kind this script knows how to run, and the preset it
# binds when nobody names a blueprint. Legacy default UW set kept as a
# constant so the compiled and the hardcoded paths are the same six calls.
KIND = "uw_grant_quest"
LEGACY_PRESET = "quest_smart_missiles"

# What this flow is, for the registry (flows/__init__.py).
FLOW = {
    "kind": "uw_grant_quest",
    "label": "Quest: Ultimate Weapon grant (Smart Missiles)",
    "runner": "flows/quest_sm.py",
    # This runner owns its whole setup (loadout, tier, battle) end to end -
    # the scheduler must not walk the game Home underneath it.
    "handoff": "none",
    "blueprint_args": [
        {"flag": "--rides", "fields": ["rides", "count"], "default": 1},
    ],
    "legacy_preset": "quest_smart_missiles",
}
UW_SETUP = {"chain_lightning": False, "death_wave": False, "poison_swamp": False,
            "golden_tower": True, "black_hole": True, "spotlight": True,
            # owned since 2026-08-29 - its toggle exists now, keep it out of
            # the ride's kill mix like the other non-wanted weapons
            "chronofield": False}


def _preset() -> dict:
    """The ACTIVE preset, flat. Blueprints compiled by playerprofile.py are
    flat by contract (no `base:`), and the legacy quest preset carries none of
    the keys read through here - so a missing key always means "use the module
    constant", which is exactly the pre-profile behaviour."""
    from settings import CONFIG
    return CONFIG["presets"].get(CONFIG.get("preset")) or {}


def _bp_arg(value: str) -> str:
    """--preset accepts a compiled blueprint and NOTHING ELSE.

    This script has never had a --preset flag, so the tray's launch (which
    passes a preset name to every runner) dies in argparse today. That must
    not change: quietly ACCEPTING a legacy name would turn a launch that
    fails to parse into a live routine that surrenders battles. So a non-bp_
    value stays an argparse error, and only a compiled blueprint gets through.
    """
    if not value.startswith("bp_"):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a compiled blueprint - this runner takes "
            f"--preset bp_<name> (kind {KIND!r}) or no --preset at all")
    return value


def _bind_preset(instance: str, name: str | None) -> None:
    """Bind the process preset, REFUSING a blueprint of the wrong kind.

    No --preset at all binds the legacy preset, exactly as before. A `bp_`
    preset of another kind is REFUSED before the first capture and long
    before the first tap: `flows/quest_sm.py --preset bp_tourney_main` would
    otherwise reach "adopt running battle via retry" and SURRENDER A
    TOURNAMENT. bp_ presets exist only after select_instance materializes
    them, hence bind-then-check-then-exit.
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
                         f"flows/quest_sm.py runs {KIND!r} blueprints only "
                         f"(nothing was captured or tapped)")


def _decision_path() -> str:
    from settings import CONFIG
    return os.path.join("logs", CONFIG.get("active_instance", "main"),
                        "sm_decision")


def uw_setup() -> None:
    """Spotlight, Black Hole and Golden Tower ON; Chain Lightning, Death
    Wave and Poison Swamp OFF (user, 2026-08-16).

    ORDER IS PRESERVED: UW_SETUP is written in the original call order and
    dicts keep insertion order, so the legacy path makes the same six calls
    in the same sequence it always did."""
    from interactions import shopper
    wanted = _preset().get("uw_setup") or UW_SETUP
    for uw, on in wanted.items():
        shopper.uw_toggle(uw, want_on=bool(on))


def _grant_templates() -> list[str]:
    return [g for g in GRANT_UWS
            if os.path.exists(os.path.join("templates", "uw", f"{g}.png"))]


def scan_grant(targets: tuple[str, ...] = ("smart_missiles",)
               ) -> tuple[str, object]:
    """One UW-panel sweep. Returns (verdict, extra):
    ('none', None) | ('smart_missiles', None) | ('wrong', name) |
    ('unknown', shot_path).

    `targets` are the grants that count as a JACKPOT. The verdict string
    stays 'smart_missiles' whichever target hit, so every caller-side
    comparison downstream is unchanged."""
    from device import capture
    import cv2
    from vision import detect
    from runtime import logger
    import numpy as np
    from interactions import shopper
    if not shopper._tap_tab("uw"):
        return "none", None
    shopper._scroll_to_top()
    frames = []
    for _ in range(3):
        frames.append(capture.grab())
        shopper._swipe_panel_down()
        time.sleep(0.5)
    frames.append(capture.grab())
    known = [f"uw/{k}.png" for k in KNOWN_UWS]
    grants = _grant_templates()
    for f in frames:
        for g in grants:
            hit, _, _ = detect._match(f, f"uw/{g}.png", 0.80)
            if hit:
                return (("smart_missiles", None) if g in targets
                        else ("wrong", g))
        # FULL-FRAME known-name locations first (2026-08-16: the old
        # per-anchor crop cut Golden Tower's name at its edge and flagged
        # a row we own - row heights shift with progress bars, the crop
        # window does not). A pill anchor is "known" if a known name sits
        # near where its row's name belongs; only unvouched rows with
        # actual text are unknown.
        known_at = []
        for k in known:
            hit, _, loc = detect._match(f, k, 0.70)
            if hit:
                known_at.append(loc)
        for pill_rel in ("uw/toggle_on.png", "uw/toggle_off.png"):
            tpl = detect._tpl(pill_rel)
            res = cv2.matchTemplate(f, tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= 0.75)
            for y, x in zip(ys, xs):
                if y < 2000:                    # pills live BELOW the green
                    continue                    # ULTIMATE WEAPONS header
                                                # (a 1946 anchor put the name
                                                # window inside the banner,
                                                # 2026-08-16)
                if any(abs(kx - x) < 120 and abs(ky - (y - 115)) < 80
                       for kx, ky in known_at):
                    continue                    # a row we own
                name = f[max(0, y - 135):y - 25, x:x + 400]
                if name.size == 0:
                    continue
                white = float((name.min(axis=2) > 190).mean())
                if white < 0.02:
                    continue                    # empty strip, no text
                shot = logger.shot(f, "sm_unknown_uw")
                crop = logger.shot(name, "sm_unknown_uw_name")
                logger.event("sm_unknown_uw", shot=shot, crop=crop,
                             at=[int(x), int(y)])
                return "unknown", shot
    return "none", None


def await_decision() -> str:
    """Block until the supervising agent labels the unknown row."""
    from runtime import logger
    path = _decision_path()
    deadline = time.monotonic() + DECISION_TIMEOUT_SEC
    while time.monotonic() < deadline:
        try:
            with open(path, encoding="utf-8") as fh:
                d = fh.read().strip()
            os.remove(path)
            logger.event("sm_decision", decision=d)
            if d in ("proceed", "restart"):
                return d
        except FileNotFoundError:
            pass
        time.sleep(3)
    # RESTART on timeout (changed 2026-08-16 after the first live run): an
    # unanswered unknown used to keep rolling, and a granted Inner Land
    # Mines run then farmed nothing for over an hour. A restart risks
    # discarding an unlabelled jackpot only while smart_missiles.png does
    # not exist yet; a wrong grant kept alive wastes the machine forever.
    logger.event("sm_decision", decision="timeout -> restart")
    return "restart"


def ride_to(target: int) -> None:
    """Sprint cancelled; just wait for the wave counter to reach target."""
    from device import capture
    from runtime import logger
    from flows import shard
    from vision import wave_reader
    shard.cancel_sprint()
    last_log = 0
    # Gems are claimed through the whole ride (user, 2026-08-28: circling
    # gems went unclaimed through whole quest batches). The old 10s sleep
    # would lose the orbiting gem between polls - 1s keeps GemWatch's
    # fresh-detection rule workable and costs one template match a second.
    gems = shard.GemWatch(**shard.gem_opts())
    while True:
        frame = capture.grab()
        w = wave_reader.read_wave(frame)
        gems.poll(frame)
        if w is not None and w >= target:
            logger.event("sm_ride", wave=w, stage="target reached")
            return
        if w is not None and w - last_log >= 500:
            logger.event("sm_ride", wave=w)
            last_log = w
        time.sleep(1)


def _cli(argv=None):
    """The command line, lifted out of main() so the argument CONTRACT can be
    tested without running a quest."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--rides", type=int, default=None,
                    help="how many successful Smart-Missiles runs to farm "
                         "(default: the blueprint's `rides`/`count`, else 1)")
    ap.add_argument("--adopt-ride", action="store_true",
                    help="the live battle already HAS Smart Missiles granted:"
                         " skip setup and lottery, ride it out as ride 1")
    ap.add_argument("--preset", type=_bp_arg, default=None,
                    help="a compiled blueprint preset (bp_<name>, kind "
                         "uw_grant_quest). Any other value is an argparse "
                         "error, exactly as --preset has always been here")
    return ap.parse_args(argv)


def main() -> None:
    a = _cli()
    _bind_preset(a.instance, a.preset)
    # PRECEDENCE: explicit CLI > blueprint > module constant. The tray passes
    # --rides 1 in runner_args, so the legacy launch is bit-for-bit unchanged.
    # `or` rather than a dict default throughout: compile_preset emits these
    # keys with a None value when the blueprint leaves them out.
    rides = (a.rides if a.rides is not None else
             int(_preset().get("rides") or _preset().get("count") or 1))
    targets = tuple(_preset().get("grant_targets") or ("smart_missiles",))
    reroll_at = int(_preset().get("reroll_at_wave") or RESTART_AT_WAVE)
    ride_to_wave = int(_preset().get("ride_to_wave") or RIDE_TO_WAVE)
    # The legacy preset already carries tier 1 and no loadout, so these read
    # back the hardcoded values they replace.
    lo_name = _preset().get("loadout") or "coin_farm"
    tier = int(_preset().get("tier") or 1)
    from device import capture
    from vision import detect
    from device import act
    from runtime import logger
    from interactions import loadout
    from flows import shard
    from interactions import tourney
    from vision import wave_reader
    logger.event("sm_quest", stage="begin", rides=rides, targets=targets,
                 reroll_at_wave=reroll_at, ride_to_wave=ride_to_wave)
    frame = capture.grab()
    dead, retry = detect.death_screen(frame)
    if a.adopt_ride:
        # NEVER route through the adopt-via-retry paths here: they surrender
        # the battle, and this battle is the jackpot.
        w = wave_reader.read_wave(frame)
        if w is None:
            raise tourney.Abort("--adopt-ride but no readable wave on screen")
        logger.event("sm_quest", stage="adopt live SM ride", wave=w)
    elif dead and retry and wave_reader.read_wave(frame) is None:
        logger.event("sm_quest", stage="adopt stats dialog via retry")
        act.tap(*retry, reason="RETRY", instant=True)
    elif wave_reader.read_wave(frame) is not None:
        logger.event("sm_quest", stage="adopt running battle via retry")
        shard.abandon_run()
    else:
        tourney.ensure_home()
        loadout.apply(lo_name)              # blueprint's loadout, else the
        shard.set_tier(tier)                # standard farming one at tier 1
        shard.start_battle()
    rides_done = 0
    first = not a.adopt_ride    # adopting means toggles are already set
    adopting = a.adopt_ride
    while rides_done < rides:
        if adopting:            # granted run is live - straight to the ride
            adopting = False
            granted = True
        else:
            shard.wait_for_wave(1)
            shard.ensure_max_speed()
            if first:
                uw_setup()
                first = False
            granted = False
        while not granted:
            time.sleep(SCAN_EVERY_SEC)
            w = wave_reader.read_wave(capture.grab())
            if w is not None and w >= reroll_at:
                logger.event("sm_reroll", wave=w)
                shard.abandon_run()
                shard.wait_for_wave(1)
                shard.ensure_max_speed()
                continue
            verdict, extra = scan_grant(targets)
            if verdict == "unknown":
                verdict = await_decision()
                if verdict == "proceed":
                    verdict = "smart_missiles"
            if verdict == "smart_missiles":
                logger.event("sm_grant", weapon="smart_missiles")
                granted = True
            elif verdict in ("wrong", "restart"):
                logger.event("sm_grant", weapon=str(extra), action="restart")
                shard.abandon_run()
                shard.wait_for_wave(1)
                shard.ensure_max_speed()
    # jackpot: Smart Missiles are ON by default when granted; Spotlight is
    # already ON; everything else already OFF. Cancel the sprint and ride.
        ride_to(ride_to_wave)
        rides_done += 1
        last = rides_done >= rides
        shard.abandon_run(to_home=last)
        logger.event("sm_quest", stage="ride done", rides_done=rides_done)
    logger.event("sm_quest", stage="done", rides=rides_done)


if __name__ == "__main__":
    from flows import run_main
    run_main("quest_sm", main)
