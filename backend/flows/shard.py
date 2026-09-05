"""Shard farming - the human session, replayed as a loop.

Recorded from the user on 2026-08-13
(recordings/main/20260813_111519_shard_farm, 31 gestures + 46 paired frames)
and narrated by them step by step.

The idea: on Tier 18 a FLEET spawns at WAVE 95 (then every 100 waves). Killing
it pays 1650 Reroll Shards 80% of the time, or 5 module shards of one random
type the other 20% - the official table, unchanged by the v28.3 fleet
rebalance. Everything after that kill is dead weight, so the run is abandoned
immediately and restarted. One loop is roughly two minutes, most of which is
the Intro Sprint racing to wave 100.

Wave 95 is why waiting until the end of wave 101 makes the kill unconditional:
the fleet has been on the field for six waves by then, and fleets spawn even
on waves the sprint skips. It also removes any need to tell the three fleet
types apart - whichever one rolled, it is already there and it drops the same.

    SETUP (once)   end any live run -> Tier 18 -> cards preset 18v300
                   -> Primordial Collapse to Primary -> BATTLE
    LOOP (forever) wave 100 -> cancel Intro Sprint
                   -> wave 101 nearly over -> NUKE
                   -> side menu -> EXIT BATTLE -> Surrender -> RETRY

RETRY restarts the same tier with the same cards and modules, which is why the
setup only runs once - the recorded session does the whole card/module dance
before the first battle and never again.

Two triggers make this work, and both are read, never timed:
  * the WAVE COUNTER for 100 - the sprint is cancelled the moment it lands
  * the WAVE PROGRESS bar (rois.wave_progress) for "101 is nearly over" -
    a clean sawtooth that ramps 0 -> ~0.85 and resets, so "almost over" is a
    threshold on it rather than a stopwatch

Shares tourney.py's navigation helpers rather than duplicating them: the
find/wait_for/require/tap_at/ensure_home/end_round machinery was built and
debugged for the tournament routine and the screens are the same.
"""
import random
import sys
import time
from pathlib import Path

# Flow files are runnable as scripts (`python flows/shard.py`) with the
# backend root as cwd - put that root on sys.path so sibling modules resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from device import act
from device import capture
from scheduling import daystate
from vision import detect
from runtime import logger
from scheduling import runflag
from vision import screen
from vision import wave_reader
from settings import CONFIG

from interactions import loadout
from interactions import tourney
from interactions.tourney import (Abort, find, require, tap_at, ensure_home, on_home,
                     open_nav, return_to_game, NAV)

# What this flow is, for the registry (flows/__init__.py). The scheduler,
# compiler, tray and dashboard all read THIS instead of hardcoded tables.
FLOW = {
    "kind": "shard",
    "label": "Shard farming",
    "runner": "flows/shard.py",
    # The scheduler equips the blueprint's loadout and sets its tier before
    # this script takes over the loop.
    "handoff": "loadout",
    # The scheduler's remaining daily quota arrives here; 0 = unbounded.
    "count_arg": "--loops",
    "blueprint_args": [
        {"flag": "--tier", "fields": ["tier"]},
    ],
    "legacy_preset": "shard_farm",
    # The scheduler's handoff ends with BATTLE on screen; without this the
    # runner's own setup() walked Home over that battle and ended it - every
    # "Tier 18 / Wave 1 / Coins 0" line in the battle history (2026-09-05).
    "adopt_arg": "--no-setup",
}

# --- the plan, as the user performed it --------------------------------
TIER = 18
CARD_PRESET = "cards/preset_18v300.png"
# ORDER MATTERS, and not for the reason it looks like. Primordial Collapse is
# normally already sitting in a slot. Equipping Dimension Core first DISPLACES
# it - the Transfer Level prompt moves the levels across - which frees
# Primordial Collapse to go into Primary clean. Doing these the other way
# round, or doing only the second one, does not produce the same loadout.
#
# Slots confirmed from the recording's own taps: op 1 landed at x=734 (the
# Assist button spans 555-790), op 2 at x=403 (Primary spans 285-530).
#
# Idempotent on a re-run: _equip_module reads "not in the inventory" as
# already-equipped, because equipping removes a module from the grid.
MODULE_PLAN = [("dimension_core", "assist"),
               ("primordial_collapse", "primary")]

# --- chrome measured off the recording ---------------------------------
TIER_LEFT = (390, 1410)         # "<" on the home Difficulty selector
TIER_RIGHT = (700, 1405)        # ">"
TIER_BAND = ((1382, 1425), (552, 655))   # the number, arrows excluded
TIER_DIGIT_H = (26, 40)
HOME_BATTLE = (621, 2057)       # the big BATTLE button on the home screen

SPRINT_WAVE = 100               # cancel the Intro Sprint the moment this lands
NUKE_WAVE = 101                 # ...then nuke near the end of this one. The
                                # fleet actually spawned back on wave 95 and
                                # Commander/Overcharge move at 1/5 basic speed,
                                # so the wait is walk-in time, not spawn time.
NUKE_AT_PROGRESS = 0.10         # how far through wave 101 to fire.
                                # Fires almost as soon as 101 starts. The fleet
                                # spawned back on wave 95 and has had six waves
                                # to walk in, so by the time 101 begins it is
                                # already in position - the earlier 0.85 ("the
                                # last readable frame of the wave") was buying
                                # certainty that wave 95 had already given.
                                # If yields ever drop, this is the first knob to
                                # put back up: a Nuke fired before the fleet is
                                # in range still logs fired=True but kills
                                # nothing, and the loop cannot tell the
                                # difference.
EXIT_POLL = 0.10        # how often to look for the next exit screen
ADB_MAX_FAILS = 5       # consecutive daemon restarts before giving up: a
                        # dead emulator must not become an infinite retry loop
OFF_BATTLE_FRAMES = 10  # ~3s of no battle before it is treated as real
TIER_TRIES = 25    # was 12: Tier 14 -> Tier 1 (the ILM quest) needs 13 taps
                   # alone, and every miscounted arrow eats one more try
WAVE_TIMEOUT = 240.0            # generous: the sprint to wave 100 took ~77s


GEM_DELAY_SEC = (3, 10)         # human pause before claiming, as in orchestrator.py
GEM_STALE_SEC = 10.0            # give a drifting gem this long to reappear

# The game-speed widget ("- x5.0 +"), panel-open layout; rides the shifting
# bottom HUD, so capture.layout_offset applies to every y here.
SPEED_BAND = (1535, 1615)       # y-band of the label
SPEED_X = (730, 890)            # x-band of the label
SPEED_PLUS = (906, 1573)        # the '+' button
SPEED_TAPS = 6                  # x1 -> x5 is 4 presses; 6 bounds a bad read


def _in_ability_row(pt, margin: int = 25) -> bool:
    """Nothing speculative is ever tapped on the Nuke / Demon Mode buttons.

    Deliberately a copy of orchestrator._in_ability_row rather than an import: this
    module must not pull in the orchestrator's module-level state just to reuse five
    lines, and the rule is safety-critical enough to be visible where it is
    applied.
    """
    x, y, w, h = CONFIG["rois"]["ability_row"]
    return (x - margin <= pt[0] <= x + w + margin
            and y - margin <= pt[1] <= y + h + margin)


class GemWatch:
    """Claim floating gems while the loop is otherwise just waiting.

    Gems stay a priority during shard farming - both the diamond orbiting the
    tower and the settled 'CLAIM' box that lands bottom-left. detect.floating_
    gem finds either, anywhere in the field ROI, so there is nothing to aim.

    Same three rules as the orchestrator, for the same reasons:
      * a randomised 3-10s pause before tapping, so the claim is not instant
      * fire only on a FRESH detection - the orbiting gem moves, so a
        remembered point goes stale within a second
      * ...unless it has been missing for 10s, in which case tap where it was
        last seen (a settled box does not move) - but never if that point is
        over the ability row, where a stray tap would burn Nuke.
    """

    def __init__(self, enabled: bool = True,
                 delay: tuple[float, float] = GEM_DELAY_SEC):
        self.due: tuple[float, tuple[int, int]] | None = None
        # Defaults ARE the legacy behaviour: every existing caller does
        # GemWatch() and gets gem claiming with the 3-10s human pause.
        self.enabled = bool(enabled)
        self.delay = tuple(delay)

    def poll(self, frame) -> None:
        if not self.enabled:
            return
        gem = detect.floating_gem(frame)
        now = time.monotonic()
        if gem and self.due is None:
            delay = random.uniform(*self.delay)
            self.due = (now + delay, gem)
            logger.event("shard_gem_seen", delay=round(delay, 1),
                         x=gem[0], y=gem[1])
        if not self.due or now < self.due[0]:
            return
        if gem:
            try:
                logger.event("shard_gem", **act.tap(*gem, reason="gem_claim"))
            except act.TapRefused as e:
                logger.event("tap_refused", button="gem", error=str(e))
            self.due = None
        elif now - self.due[0] > GEM_STALE_SEC:
            pt = self.due[1]
            if _in_ability_row(pt):
                logger.event("shard_gem_lost", reason="over_ability_row",
                             x=pt[0], y=pt[1])
            else:
                try:
                    logger.event("shard_gem_stale",
                                 **act.tap(*pt, reason="gem_claim_stale"))
                except act.TapRefused as e:
                    logger.event("tap_refused", button="gem", error=str(e))
            self.due = None


def _kick_adb():
    """Restart the adb server after it drops its socket."""
    from settings import CONFIG as _C, run_hidden
    exe = _C["adb"]["exe"]
    serial = _C["instances"][_C["active_instance"]]["serial"]
    # RECONNECT is not optional. MuMu is reached over TCP, so kill-server drops
    # the device with it and every later screencap fails with "device not
    # found" - which is exactly what the first version did: it restarted the
    # daemon five times, each time into a session with no device attached, and
    # then gave up. Restarting the server without reconnecting is not recovery.
    # Daemon lifecycle is the ONE sanctioned adb.exe spawn (CLAUDE.md) - and
    # it must be window-suppressed: 3 consoles flashed per kick before.
    for args in (["kill-server"], ["start-server"], ["connect", serial]):
        try:
            run_hidden([exe, *args], capture_output=True, timeout=20)
        except Exception:                       # noqa: BLE001
            pass
    time.sleep(1.5)


def read_tier(frame) -> int | None:
    """The Difficulty number on the home screen, or None.

    Read rather than counted: the recorded session overshot to Tier 17 and
    corrected back, so replaying a fixed number of arrow taps would land
    somewhere different every time.
    """
    import cv2
    (y0, y1), (x0, x1) = TIER_BAND
    sub = frame[y0:y1, x0:x1]
    if sub.size == 0:
        return None
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = sorted(b for b in map(cv2.boundingRect, contours)
                   if TIER_DIGIT_H[0] <= b[3] <= TIER_DIGIT_H[1] and b[2] >= 5)
    if not boxes:
        return None
    tpls = wave_reader._load_templates()
    digits = []
    for x, y, w, h in boxes:
        glyph = bw[y:y + h, x:x + w]
        best, score = None, 0.55
        for d, tpl in tpls.items():
            g = cv2.resize(glyph, (tpl.shape[1], tpl.shape[0]))
            s = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)[0][0]
            if s > score:
                best, score = d, s
        if best is None:
            return None
        digits.append(best)
    return int("".join(map(str, digits)))


def set_tier(want: int = TIER):
    """Nudge the Difficulty selector until it reads `want`.

    BURST-THEN-VERIFY (user, 2026-08-28: "make the clicks between tiers
    way faster, like 140 msec"): the old loop paid a ~350ms frame grab per
    single step, ~1.5s per tier. Now the delta is read once and fired as a
    burst of instant taps - act's rate cap + jitter paces them at roughly
    the asked-for 140ms - then ONE grab verifies; any missed or extra step
    falls back to the original verified single-step loop.
    """
    for attempt in range(TIER_TRIES):
        frame = capture.grab()
        if not on_home(frame):
            raise Abort("not on the home screen while setting the tier")
        cur = read_tier(frame)
        if cur is None:
            logger.shot(frame, "shard_tier_unreadable")
            raise Abort("cannot read the Difficulty tier")
        if cur == want:
            logger.event("shard_tier", tier=cur, set=True)
            return frame
        steps = abs(want - cur)
        pt = TIER_RIGHT if cur < want else TIER_LEFT
        if attempt == 0 and steps > 1:
            # instant taps log no events (act contract) - one event for
            # the whole burst instead
            logger.event("shard_tier_burst", tier=cur, want=want,
                         steps=steps)
            for _ in range(steps):
                act.tap(*pt, reason=f"tier burst -> {want}", instant=True)
            continue
        tap_at(pt, f"tier {cur} -> {want}")
    logger.shot(capture.grab(), "shard_tier_stuck")
    raise Abort(f"could not reach Tier {want}")


def preset_active(frame, pt) -> bool:
    """Is the preset tab under `pt` the selected one?

    The selected tab is outlined in GREEN, the others in CYAN - and that is the
    only difference, the label art is identical either way. Measured across the
    five tabs of a recorded frame with 18v300 selected:

        Main Farm   green 0.000  cyan 0.077
        Tourney P1  green 0.000  cyan 0.056
        Disco       green 0.000  cyan 0.058
        Att Farm    green 0.000  cyan 0.055
        18v300      green 0.030  cyan 0.000      <- selected

    Cleanly separated, so it is decided on which hue is present rather than on
    a threshold either one has to clear.
    """
    import cv2
    x0, x1 = max(0, pt[0] - 100), pt[0] + 100
    y0, y1 = max(0, pt[1] - 44), pt[1] + 44
    box = frame[y0:y1, x0:x1]
    if box.size == 0:
        return False
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 110) & (v > 140)
    green = float(((h > 40) & (h < 80) & lit).mean())
    cyan = float(((h > 85) & (h < 105) & lit).mean())
    return green > cyan


def gem_opts() -> dict:
    """GemWatch kwargs from the active blueprint's gather policy.

    Only a compiled `bp_` preset is consulted. Anything else - the legacy
    shard_farm tray entry, a bare `python flows/shard.py` - returns {} and keeps
    the module constants exactly.
    """
    name = CONFIG.get("preset") or ""
    if not name.startswith("bp_"):
        return {}
    g = (CONFIG["presets"].get(name) or {}).get("gather") or {}
    return {"enabled": bool(g.get("flying_gem", True)),
            "delay": tuple(g.get("gem_delay_sec", GEM_DELAY_SEC))}


def setup(tier: int | None = None):
    """Everything that happens once, before the first battle.

    The equipment goes through loadout.apply - ONE code path for the
    standalone launch and combo's handoff alike. The old per-key helpers
    read `spec("shard_farm")["modules"]` directly and crashed with a
    KeyError when the v29 body replaced that list with `module_preset:`
    (this silently killed the whole 78-run block on 2026-08-27; found via
    the runner_crashed event the next morning)."""
    tier = tier or TIER
    ensure_home()
    set_tier(tier)
    loadout.apply("shard_farm")
    frame = capture.grab()
    if not on_home(frame):
        ensure_home()
    start_battle()
    logger.event("shard_setup", stage="done", tier=tier)


def start_dissonant(tab: str = "utility", settle: float = 15.0):
    """Enter a DISSONANT run (event mode, user-taught 2026-08-31): home ->
    'Dissonant Run' button -> verify the wanted upgrade tab carries the
    red X (= disabled; the selection PERSISTS between opens, so the
    common path verifies and taps nothing) -> the dialog's own BATTLE.
    Only 'utility' has a harvested tile template today - any other tab
    fails closed via TemplateMissing, never a blind tap."""
    from vision import detect
    frame, pt = require("home/dissonant_run.png", "dissonant run button")
    tap_at(pt, "open dissonant run dialog")
    frame, hit = tourney.wait_for("dialogs/dissonant_header.png", 6.0)
    if not hit:
        raise Abort("dissonant run dialog did not open")
    tile = f"dialogs/dissonant_tile_{tab}.png"
    okx, _, locx = detect._match(frame, "dialogs/dissonant_x.png", 0.85)
    oku, _, locu = detect._match(frame, tile, 0.85)
    if not oku:
        raise Abort(f"dissonant tile {tab!r} not found in the dialog")
    on_tile = (okx and abs(locx[0] - locu[0]) < 120
               and abs(locx[1] - locu[1]) < 220)
    if not on_tile:
        # X sits elsewhere (or nowhere): select the wanted tile - its
        # center is just above the label the template matched
        tap_at((locu[0] + 60, locu[1] - 60), f"dissonant: disable {tab}")
        time.sleep(0.8)
        frame = capture.grab()
        okx, _, locx = detect._match(frame, "dialogs/dissonant_x.png", 0.85)
        oku, _, locu = detect._match(frame, tile, 0.85)
        if not (okx and oku and abs(locx[0] - locu[0]) < 120
                and abs(locx[1] - locu[1]) < 220):
            logger.shot(frame, "dissonant_tab_stuck")
            raise Abort(f"could not disable {tab!r} in the dissonant dialog")
    logger.event("dissonant_entry", tab=tab)
    frame, pt = require("dialogs/dissonant_battle.png", "dissonant BATTLE")
    tap_at(pt, "dissonant run: BATTLE")
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        frame = capture.grab()
        if _in_run(frame):
            logger.event("shard_battle", started=True, attempt=1,
                         dissonant=tab)
            return frame
        time.sleep(0.4)
    logger.shot(capture.grab(), "dissonant_start_stuck")
    raise Abort("dissonant run did not start")


def start_battle(tries: int = 2, settle: float = 15.0):
    """Tap BATTLE and WAIT for the run to actually exist.

    Returning the moment the tap lands is what killed the first forever-loop:
    the battle needs a second or two to load, and the loop's very next frame
    showed neither a wave counter nor a battle screen, so it aborted with
    'left the battle screen' before the run had even started. The tap is also
    retried once - a BATTLE tap that arrives while the nav is still settling
    is swallowed, and a swallowed tap leaves us on Home forever.
    """
    for i in range(tries):
        tap_at(HOME_BATTLE, "start battle")
        deadline = time.monotonic() + settle
        while time.monotonic() < deadline:
            frame = capture.grab()
            if _in_run(frame):
                logger.event("shard_battle", started=True, attempt=i + 1)
                return frame
            time.sleep(0.4)
        logger.event("shard_battle", started=False, attempt=i + 1)
    frame = capture.grab()
    logger.shot(frame, "shard_battle_never_started")
    raise Abort("BATTLE tapped but no run started")


# ----------------------------------------------------------------- the loop

def wait_for_wave(target: int, timeout: float = WAVE_TIMEOUT,
                  gems: "GemWatch | None" = None):
    """Block until the wave counter reaches `target`. Returns the frame.

    Waves SKIP during the Intro Sprint (observed 30 -> 40 -> 50), so this
    tests >= rather than ==; landing exactly on 100 is not guaranteed.
    """
    deadline = time.monotonic() + timeout
    off = 0
    while time.monotonic() < deadline:
        frame = capture.grab()
        w = wave_reader.read_wave(frame)
        if w is not None and w >= target:
            return frame, w
        if w is None and not _in_run(frame):
            # Do NOT abort on the first odd frame. Every loop boundary crosses
            # a gap where the old run is gone and the new one has not drawn
            # yet - RETRY, the death dialog fading, the battle loading - and
            # the old code raced that gap and lost on loop 10 of 100.
            off += 1
            if off >= OFF_BATTLE_FRAMES:
                if on_home(frame):
                    # RETRY did not take and we are parked on Home. This is a
                    # screen the routine navigated to itself, so starting the
                    # battle again is recovery, not a stray tap.
                    logger.event("shard_recover", where="home")
                    start_battle()
                    off = 0
                    continue
                logger.shot(frame, "shard_left_run")
                raise Abort("left the battle screen while waiting for a wave")
        else:
            off = 0
        if gems and w is not None:      # in-run only, never on a menu screen
            gems.poll(frame)
        time.sleep(0.3)
    raise Abort(f"wave {target} not reached within {timeout:.0f}s")


def _in_run(frame) -> bool:
    return (wave_reader.read_wave(frame) is not None
            or screen.identify(frame).name == "battle")


def cancel_sprint():
    """Tap the Intro Sprint indicator and confirm. Verified at every step."""
    frame = capture.grab()
    pt = detect.find_intro_sprint(frame)
    if pt is None:
        logger.event("shard_sprint", result="indicator not found")
        return False
    tap_at(pt, "intro sprint indicator")
    frame = capture.grab()
    sc = screen.identify(frame)
    if sc.name != "intro_sprint_end":
        logger.event("shard_sprint", result="no confirm dialog", screen=sc.name,
                     shot=logger.shot(frame, "shard_sprint_nodialog"))
        return False
    score, yes = screen._match(frame, "home/intro_sprint_yes.png",
                               ((1380, 1520), (560, 900)))
    if score < 0.90:
        logger.event("shard_sprint", result="Yes not found", score=round(score, 3))
        return False
    tap_at(yes, "intro sprint: yes")
    logger.event("shard_sprint", result="ended")
    return True


def wait_for_nuke_point(timeout: float = 120.0,
                        gems: "GemWatch | None" = None):
    """Wave NUKE_WAVE, nearly finished.

    The progress bar is the cue the user gave ("wait until 101 is almost
    over"), not a stopwatch - waves run ~7s under the sprint and much longer
    without it, so any fixed delay would be wrong in one regime or the other.
    """
    deadline = time.monotonic() + timeout
    best = 0.0
    while time.monotonic() < deadline:
        frame = capture.grab()
        w = wave_reader.read_wave(frame)
        p = detect.bar_fill(frame, "wave_progress")
        if w is not None and w > NUKE_WAVE:
            logger.event("shard_nuke_point", missed=True, wave=w)
            return frame            # overshot - fire immediately
        if gems and w is not None:
            gems.poll(frame)
        if w == NUKE_WAVE:
            best = max(best, p)
            if p >= NUKE_AT_PROGRESS:
                logger.event("shard_nuke_point", wave=w, progress=round(p, 3))
                return frame
        time.sleep(0.25)
    raise Abort(f"wave {NUKE_WAVE} end not detected (best progress {best:.2f})")


def fire_nuke(frame) -> bool:
    """Tap Nuke. require_ready is NOT consulted - see orchestrator.fire_button: the
    readiness test reads the battlefield behind the button, and a tap on a
    cooling ability is a harmless no-op while a refused tap loses the loop."""
    st = detect.button_state(frame, "nuke")
    if not (st.present and st.center) or st.score < 0.85:
        logger.event("shard_nuke", fired=False, score=round(st.score, 3),
                     shot=logger.shot(frame, "shard_no_nuke_btn"))
        return False
    before = detect.button_border_val(frame, "nuke")
    tap_at(st.center, "NUKE")
    after = detect.button_border_val(capture.grab(), "nuke")
    fired = after is None or (before is not None and after < before * 0.75)
    logger.event("shard_nuke", fired=fired,
                 border_before=round(before or 0, 1),
                 border_after=(round(after, 1) if after is not None else None))
    return fired


def _confirm_exit(frame=None) -> bool:
    """Tap whichever exit confirm dialog is up, if any.

    The exit button's LABEL does not predict the DIALOG: tapping END ROUND on
    a plain Tier 18 run opened the Surrender/Go Home dialog (observed live,
    2026-08-14) even though tourney runs pair END ROUND with No/Yes. So the
    answer is chosen by what is on screen, never by what was tapped.
    Surrender over Go Home always: Go Home only hides a run that is still
    going, and the next RETRY would have nothing to retry.
    """
    frame = frame if frame is not None else capture.grab()
    for rel, reason in (("home/surrender.png", "surrender"),
                        ("home/end_round_yes.png", "end round yes")):
        hit = find(frame, rel, 0.90)
        if hit:
            # NOT instant, and SETTLE afterwards (acct2, 2026-08-19, proven
            # by an instrumented repro): at poll speed the dialog is re-
            # matched mid-fade right after being answered and the re-tap
            # lands on the TRANSITION - on this account that wedged the
            # emerging stats dialog and "GAME STATS / RETRY never appeared".
            # At a 1s cadence the same flow succeeds every time. So: full-
            # duration press, then give the transition its second before any
            # caller can re-poll.
            act.tap(*hit[0], reason=reason)
            time.sleep(1.2)
            return True
    return False


def abandon_run(to_home: bool = False):
    """Side menu -> EXIT BATTLE / END ROUND -> confirm -> RETRY, at speed.

    to_home=True taps HOME instead of RETRY at the stats dialog - the
    stats-free exit for quest cycles (no runlog.collect on either path).

    Modelled on tourney.end_round, replacing a broken version whose autopsy
    is worth keeping:
      * the toggle TOGGLES. A failed exit leaves the menu open for the next
        attempt, and blind-tapping the toggle then closes it - so the menu
        state is read first and the toggle only tapped when closed.
      * the menu SLIDES IN. A frame grabbed straight after the toggle tap
        caught the animation, missed the button template, and the fixed-point
        fallback fired at a button that was not active yet - the 09:11 abort
        shot shows the settled menu with the button untouched. So the button
        is POLLED for, and there is no blind fallback.
      * the slot carries TWO labels (END ROUND / EXIT BATTLE, both observed
        on Tier 18) and the label does not predict the confirm dialog -
        see _confirm_exit.
    """
    frame = capture.grab()
    # ABSOLUTE RULE (user, 2026-08-15): "you NEVER cancel a tournament run
    # EVER". tourney.end_round has carried this guard at its chokepoint ever
    # since a freshly bought 10-gem entry was surrendered at wave 1; THIS is
    # the other chokepoint. Every surrender in shard/quest land funnels
    # through here, including the quest runners' "adopt the running battle
    # via retry" path, which calls it on whatever happens to be on screen.
    if screen.in_tournament(frame):
        logger.shot(frame, "shard_abandon_refused")
        raise Abort("a TOURNAMENT run is on screen - automation never "
                    "cancels a tournament run")
    if not _confirm_exit(frame):        # leftover dialog from a failed exit
        if not detect.side_menu_open(frame):
            act.tap(*CONFIG["side_menu"]["toggle"], reason="open side menu",
                    instant=True)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            frame = capture.grab()
            hit = (find(frame, "buttons/end_round.png", 0.85)
                   or find(frame, "buttons/exit_battle.png", 0.85))
            if hit:
                act.tap(*hit[0], reason="exit battle", instant=True)
                break
            time.sleep(EXIT_POLL)
        else:
            logger.shot(capture.grab(), "shard_no_exit_button")
            raise Abort("neither END ROUND nor EXIT BATTLE appeared "
                        "on the side menu")
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if _confirm_exit():
                break
            time.sleep(EXIT_POLL)
        else:
            logger.shot(capture.grab(), "shard_no_exit_confirm")
            raise Abort("no exit confirm dialog after tapping the exit button")

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        frame = capture.grab()
        # A confirm tap can land during the dialog's fade-in and never
        # register (observed live on acct2, 2026-08-19: end_round_yes
        # matched at 0.99 on the timeout shot - found, tapped, swallowed).
        # The dialog still being up is proof the tap did not take; re-answer
        # it. _confirm_exit is template-gated, so this can never blind-tap.
        if _confirm_exit(frame):
            time.sleep(EXIT_POLL)
            continue
        dead, retry = detect.death_screen(frame)
        if dead and to_home:
            # Quest cycles (user, 2026-08-16): "just restart, don't store
            # the statistics" - so the home exit here deliberately skips
            # the runlog.collect that tourney.end_round would do.
            hit = find(frame, "home/game_stats_home.png")
            if hit:
                act.tap(*hit[0], reason="game stats: HOME", instant=True)
                logger.event("shard_exit_home", ok=True)
                return True
        elif dead and retry:
            act.tap(*retry, reason="RETRY", instant=True)
            logger.event("shard_retry", ok=True)
            return True
        time.sleep(EXIT_POLL)
    logger.shot(capture.grab(), "shard_no_retry")
    raise Abort("GAME STATS / RETRY never appeared after surrendering")


def _speed_maxed(frame) -> bool:
    """Is the game-speed label reading x5.0?

    The label sits directly on the ANIMATED battlefield, so raw template
    matching is unusable - the background differs every frame. Both sides are
    binarized to the white glyphs instead (min channel > 190, which also
    drops the yellow/pink effects), and the stored template is the same mask.
    Measured: x5.0 scores 1.0, x1.0 scores 0.664 - threshold 0.85.
    """
    import cv2
    off = capture.layout_offset
    crop = frame[SPEED_BAND[0] + off:SPEED_BAND[1] + off,
                 SPEED_X[0]:SPEED_X[1]]
    if crop.size == 0:
        return False
    live = (crop.min(axis=2) > 190).astype("uint8") * 255
    from settings import ROOT
    tpl = cv2.imread(str(ROOT / "templates" / "home/speed_x5_mask.png"),
                     cv2.IMREAD_GRAYSCALE)
    if tpl is None or live.shape[0] < tpl.shape[0] or live.shape[1] < tpl.shape[1]:
        return False
    return float(cv2.matchTemplate(live, tpl, cv2.TM_CCORR_NORMED).max()) > 0.85


def ensure_max_speed():
    """Press the speed '+' until the label reads x5.0.

    Added 2026-08-14: a run came back from RETRY at x1.0 - no tap of ours
    anywhere near the widget, cause unknown - and the Intro Sprint at
    one-fifth speed blew the 240s wave-100 timeout. One press when already
    maxed is a no-op for the game, but the normal case exits without
    tapping at all.
    """
    for i in range(SPEED_TAPS + 1):
        frame = capture.grab()
        if _speed_maxed(frame):
            if i:
                logger.event("shard_speed", presses=i, fixed=True)
            return True
        if i == SPEED_TAPS:
            break
        try:
            act.tap(SPEED_PLUS[0], SPEED_PLUS[1] + capture.layout_offset,
                    reason="speed_plus", instant=True)
        except act.TapRefused as e:
            logger.event("tap_refused", button="speed_plus", error=str(e))
            return False
        time.sleep(0.4)
    logger.event("shard_speed", presses=SPEED_TAPS, fixed=False,
                 shot=logger.shot(capture.grab(), "shard_speed_stuck"))
    return False


def one_loop(n: int, gems: "GemWatch | None" = None, last: bool = False):
    logger.event("shard_loop", n=n, stage="begin")
    # any readable wave = the battle HUD is up AND layout_offset is current -
    # both are what ensure_max_speed needs before it looks at the widget
    wait_for_wave(1, gems=gems)
    ensure_max_speed()
    frame, w = wait_for_wave(SPRINT_WAVE, gems=gems)
    logger.event("shard_loop", n=n, stage="wave", wave=w)
    cancel_sprint()
    frame = wait_for_nuke_point(gems=gems)
    fire_nuke(frame)
    time.sleep(0.4)                 # let the kill and the shard drop resolve
    # The LAST loop exits to HOME, not RETRY (2026-08-29): the final RETRY
    # used to chain a 101st run that the next handoff walked over - since
    # the orphan-adoption fix that leftover gets ADOPTED as a coin run
    # (T18 + 18v300 farming as coin until it dies) or HELD ON by a
    # tournament block, and it also parked cards_restore off the nav row.
    if last:
        abandon_run(to_home=True)
    else:
        abandon_run()
    logger.event("shard_loop", n=n, stage="done")


def run(loops: int | None = None, do_setup: bool = True,
        tier: int | None = None):
    """`loops=None` runs until stopped.

    An Abort still ends the whole thing rather than being retried. That is
    deliberate and matches the rest of the autopilot: every Abort means the
    screen was not what the routine expected, and the one thing that must
    never happen is blind tapping into an unknown screen. Stopping leaves a
    logged reason and a screenshot; guessing does not.
    """
    if do_setup:
        setup(tier)
    adb_fails = 0
    gems = GemWatch(**gem_opts())   # one watcher across all loops: a gem seen
                                    # at the end of one run is gone by the next
    n = 0
    while loops is None or n < loops:
        # Honour the scheduler's stop flag at the loop boundary (2026-08-17).
        # Before this, combo's _stop_and_wait sat out its full 8h timeout
        # against a shard block - flows/shard.py never looked at the flag. Same
        # contract as orchestrator.py: leave only where stopping is free. A run is
        # already live here (chained by the previous RETRY) - close it to
        # HOME before leaving (2026-08-29): the next handoff no longer ends
        # a leftover battle, it ADOPTS it (coin) or HOLDS on it
        # (tournament - which would stall the block for hours), and the
        # cards restore below needs the nav row. Degrades loudly: a failed
        # close must not lose the stop itself.
        why = runflag.requested()
        if why is not None:
            logger.event("shard_stop_flag", after_loop=n, reason=why)
            try:
                abandon_run(to_home=True)
            except (Abort, act.TapRefused) as e:
                logger.event("shard_stop_close_fail", error=str(e))
            break
        n += 1
        try:
            one_loop(n, gems=gems,
                     last=(loops is not None and n == loops))
        except capture.CaptureError as e:
            # NOT a logic failure: adb itself died. On Windows a long run
            # spawns tens of thousands of `adb screencap`/`input` processes
            # and the daemon eventually loses its socket ("the system lacked
            # sufficient buffer space"). Killed the first infinite run at loop
            # 12. Restart the daemon and retry the same loop rather than
            # ending a farm session over an infrastructure hiccup.
            n -= 1
            adb_fails += 1
            logger.event("shard_adb", fails=adb_fails, error=str(e)[:120])
            if adb_fails > ADB_MAX_FAILS:
                raise
            _kick_adb()
            time.sleep(5.0)
            continue
        except (Abort, act.TapRefused) as e:
            logger.event("shard_loop", n=n, stage="abort", error=str(e))
            raise
        adb_fails = 0
        # Persist today's completed-run total after EVERY loop, not at exit:
        # an abort (a human taking the screen counts) must not lose the
        # count - combo resumes the remainder from this number.
        daystate.set_today("shard_runs", daystate.get_today("shard_runs") + 1)
    # v29 (user, 2026-08-28): put the farming deck back on the cards screen
    # before leaving. The coin block's global preset only SELECTS presets at
    # battle entry - nothing else moves the cards screen off this block's
    # 18v300, and whichever preset stays selected is where later card
    # mutations land. Degrades loudly: a failed restore must not turn a
    # completed block into a crash.
    restore = loadout.spec("shard_farm").get("cards_restore")
    if restore:
        try:
            logger.event("shard_cards_restore", preset=restore,
                         result=loadout.apply_cards(restore))
        except (Abort, act.TapRefused) as e:
            logger.event("shard_cards_restore", preset=restore,
                         error=str(e))
    return n


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None,
                    help="accepted so the tray can launch this the same way "
                         "it launches orchestrator.py; the shard loop has no preset "
                         "settings of its own, the plan is in this file")
    ap.add_argument("--loops", type=int, default=1,
                    help="number of shard runs (default 1; 0 = forever)")
    ap.add_argument("--tier", type=int, default=None,
                    help=f"tier to farm on (default {TIER}); a profile block "
                         f"passes its blueprint's tier here")
    ap.add_argument("--no-setup", action="store_true",
                    help="skip tier/cards/modules - a run is already going")
    return ap.parse_args()


if __name__ == "__main__":
    import settings
    _a = _cli()
    # ONLY a compiled blueprint is bound as the process preset. The tray
    # still passes --preset shard_farm (a runner-only entry with no settings);
    # binding that would change nothing here and could raise on a placeholder,
    # so the legacy path keeps ignoring it exactly as it always has.
    if (_a.preset or "").startswith("bp_"):
        settings.select_instance(_a.instance, _a.preset)
        # KIND BEFORE ANY CAPTURE OR TAP. This loop surrenders runs; pointed
        # at a tournament blueprint it would surrender a tournament.
        _kind = (CONFIG["presets"].get(_a.preset) or {}).get("kind")
        if _kind != "shard":
            raise SystemExit(f"REFUSED: {_a.preset} is a {_kind!r} blueprint "
                             f"- flows/shard.py runs 'shard' blueprints only "
                             f"(nothing was captured or tapped)")
    else:
        settings.select_instance(_a.instance)
    print(f"shard farming on {CONFIG['active_instance']} "
          f"(loops={_a.loops or 'forever'}, dry_run={CONFIG['loop']['dry_run']})")
    try:
        done = run(loops=(None if _a.loops == 0 else _a.loops),
                   do_setup=not _a.no_setup, tier=_a.tier)
        print(f"completed {done} shard loop(s)")
    except (Abort, act.TapRefused) as e:
        # The print is invisible under pythonw - the event log IS the
        # console. A silent exit 1 closed the whole 78-run shard block on
        # 2026-08-27 with no recorded reason.
        logger.event("runner_crashed", flow="shard",
                     error=f"{type(e).__name__}: {e}")
        print(f"ABORTED: {e}")
        raise SystemExit(1)
    except SystemExit:
        raise
    except BaseException as e:      # noqa: BLE001 - logged, then re-raised
        try:
            logger.event("runner_crashed", flow="shard",
                         error=f"{type(e).__name__}: {e}")
        except Exception:           # noqa: BLE001 - never mask the original
            pass
        raise
