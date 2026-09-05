"""Tournament START routine - the human session, replayed.

Recorded from the user on 2026-08-12 (recordings/main/20260812_181833_tournament,
88 touches + 146 paired screenshots) and narrated by them step by step. The
written-up decision rules live in that directory's NOTES.md; this module is the
executable half.

The routine is:

    home -> tournament (claim/skip the ticket) -> read the HEAT conditions
    -> guild GUARDIAN : farming chips out, tournament chips in
    -> CARDS          : load preset "Tourney P1", drop Cash, add Extra Orb
    -> MODULES        : equip the 4 tournament modules, transfer levels
    -> tournament -> BATTLE

Everything is located by TEMPLATE, never by fixed coordinate, because the two
things this screen is made of both move: the left rail slides up and down as
timed events start and end (measured: a 156px shift inside one recording), and
the module/card inventories REFLOW as items are equipped and leave the grid.
The only fixed coordinates here are the bottom nav row and the guardian slots,
which are chrome and do not move.

Each step verifies itself against the next screen and aborts the whole routine
rather than tapping blind - a mis-tap in a 203-module inventory equips the wrong
module, and that costs the transferred levels.
"""
import time

import cv2
import numpy as np

from device import act
from device import capture
from vision import detect
from runtime import logger
from vision import screen
from vision import wave_reader
from settings import CONFIG

# --- chrome that genuinely does not move -------------------------------
NAV = {"battle": (85, 2470), "workshop": (265, 2470), "cards": (448, 2470),
       "modules": (630, 2470), "lab": (812, 2470), "shop": (995, 2470)}
RETURN_STRIP = (538, 2455)          # "Tap To Return To Game"
GUARDIAN_SLOTS = [(170, 520), (909, 520), (170, 800)]   # 4th is locked
GUARDIAN_TAB = (472, 315)

# --- the plan, as the user performed it --------------------------------
CHIPS_OUT = ("fetch", "bounty", "summon")       # farming guardian set
CHIPS_IN = ("attack", "ally", "scout")          # tournament guardian set
# The tournament screen has TWO layouts and they do not share a BATTLE button:
#   pre-entry  - trophy, "First Prize", BATTLE at y2189
#   after your first run of the event - the LEADERBOARD, "Current/Next Prize",
#                BATTLE at y2177, and a different button treatment entirely
# Measured cross-scores: each template reads 1.000 on its own layout and
# 0.49-0.51 on the other, so one template can never serve both.
# battle_btn_preset.png (2026-09-02): the v29 client draws the button with a
# "Preset: <global preset>" subtitle under BATTLE; the two older cuts read
# 0.664 / 0.558 on it (threshold 0.90) and aborted a scheduled entry. The
# new cut reads 1.000 on that screen and <= 0.371 on 60 other screenshots.
BATTLE_BUTTONS = ("tourney/battle_btn.png", "tourney/battle_btn_rerun.png",
                  "tourney/battle_btn_preset.png",
                  # "Try again to improve your position" layout (an entry
                  # already on the board, 0 tickets): the same "BATTLE /
                  # Preset: ..." button drawn ~15% smaller above a "Time left
                  # to join" line - the first-entry cut scored 0.35 on it
                  # (2026-09-05 15:28, cut from that abort's screenshot).
                  "tourney/battle_btn_preset_tryagain.png")
# The BUY TICKET dialog ("Get another ticket to try again and improve your
# rank"), which is how every entry after the first is obtained. Cancel on the
# left, and on the right ONE of:
#     a green [>] button   - watch an ad, ticket is free
#     a gem price          - 10 gems, then 20, then 30, ... escalating per entry
# Only the video is ever taken. The price button is NOT templated on purpose:
# it changes every time, so recognising it would be a losing game. Instead the
# video glyph is an ALLOWLIST - no video match means the dialog gets cancelled,
# whatever is actually on that button. Spending the user's gems is not a
# decision this routine gets to make.
BUY_TICKET_VIDEO = "tourney/buy_ticket_video.png"
BUY_TICKET_CANCEL = "tourney/buy_ticket_cancel.png"
BUY_TICKET_BAND = ((1450, 1600), (600, 900))
# The gem price is READ, not templated. Earlier this was refused outright on
# the grounds that an escalating price cannot be recognised - but it can: the
# digits are the same HUD font as the wave counter, so the existing 0-9
# templates read it directly (measured 0.98/0.86 on "10", with the diamond
# glyph rejected on size). That turns "never spend gems" into an enforceable
# NUMERIC CAP, which is a far better rule than a blanket refusal.
#
# Fail-closed in every direction: an unreadable price, a price over the cap, or
# a cap of 0 all cancel the dialog. Nothing is ever bought on an assumption.
# y-band widened 2026-09-05: on the "Try again" layout the dialog sits lower
# (digits at y~1537-1570) and the 1470-1570 band clipped the diamond to 42px,
# INSIDE the digit height range - it was matched as a glyph, failed, and the
# 10-gem price read None (= "do not buy") while the dialog plainly said 10.
GEM_PRICE_BAND = ((1480, 1630), (560, 820))
GEM_DIGIT_H = (25, 45)          # digit glyph height; the diamond is ~51 tall
GEM_DIGIT_MIN_SCORE = 0.60
CARD_PRESET = "cards/preset_tourney_p1.png"
CARDS_DROP = ("cash",)                          # "we don't need it"
CARDS_ADD = ("extra_orb",)                      # unless Orb damage is resisted
MODULE_PLAN = [("dimension_core", "assist"),
               ("primordial_collapse", "primary"),
               ("pulsar_harvester", "assist"),
               ("galaxy_compressor", "primary")]

FIND_THRESH = 0.90
STRICT = 0.95           # inventory items: a wrong equip is expensive
CHECK_OFFSET = (87, 165)        # green "in deck" tick, from a card tile centre
CHIP_CHECK_OFFSET = (101, 100)  # ditto, from a chip tile centre
# the tick is a small glyph in a tile corner: measured 0.09-0.28 of the box when
# present and a flat 0.000 when absent, so anything above the noise floor is a
# yes. A "looks like half a tick" reading does not exist.
TICK_ON = 0.04
SLOT_EMPTY = 0.60       # padded-window score; measured empty>=0.92, full<=0.23
READABLE_BOTTOM = 2330  # below this the bottom bars occlude a tile's tick


class Abort(RuntimeError):
    """Raised when the screen is not what the routine expected."""


# ---------------------------------------------------------------- finding

def find_any(frame, rels, thresh: float = FIND_THRESH):
    """First template that matches, of several alternatives. Used where the
    game draws the same control differently depending on state."""
    for rel in rels:
        hit = find(frame, rel, thresh)
        if hit:
            return hit[0], rel
    return None, None


def find(frame, rel: str, thresh: float = FIND_THRESH):
    """Best match for a template, or None. Returns (centre, score)."""
    try:
        tpl = detect._tpl(rel)
    except detect.TemplateMissing:
        return None
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < thresh:
        return None
    h, w = tpl.shape[:2]
    return (loc[0] + w // 2, loc[1] + h // 2), float(score)


def find_robust(frame, rel: str, thresh: float = FIND_THRESH):
    """find() that survives a transient overlay across part of the tile
    (see detect._match_robust). For inventory tiles - chips, cards, module
    icons - read under floating bonus text / sparkles. Same return shape."""
    hit, score, loc = detect._match_robust(frame, rel, thresh)
    if not hit:
        return None
    tpl = detect._tpl(rel)
    h, w = tpl.shape[:2]
    return (loc[0] + w // 2, loc[1] + h // 2), float(score)


def wait_for(rel: str, timeout: float = 6.0, thresh: float = FIND_THRESH):
    """Poll until a template appears. Returns (frame, centre) or (frame, None)."""
    deadline = time.monotonic() + timeout
    frame = None
    while time.monotonic() < deadline:
        frame = capture.grab()
        hit = find(frame, rel, thresh)
        if hit:
            return frame, hit[0]
        time.sleep(0.4)
    return frame, None


def require(rel: str, what: str, timeout: float = 6.0, thresh: float = FIND_THRESH):
    frame, pt = wait_for(rel, timeout, thresh)
    if pt is None:
        logger.shot(frame, f"tourney_missing_{what}")
        raise Abort(f"expected {what} ({rel}) on screen, not found")
    return frame, pt


def greenness(frame, cx: int, cy: int, w: int = 70, h: int = 60) -> float:
    """Fraction of a small box that reads as the game's confirm-green tick."""
    y0, x0 = max(0, cy - h // 2), max(0, cx - w // 2)
    box = frame[y0:y0 + h, x0:x0 + w]
    if box.size == 0:
        return 0.0
    b, g, r = box[:, :, 0].astype(int), box[:, :, 1].astype(int), box[:, :, 2].astype(int)
    tick = (g > 120) & (g - r > 40) & (g - b > 40)
    return float(tick.mean())


def _slot_empty(frame, slot) -> bool:
    """Guardian slot occupancy, read from a PADDED window: matching a 210x200
    template against an exactly-210x200 crop leaves the matcher one position to
    score and it misreads empty slots as full (seen on the top-right slot)."""
    sx, sy = slot
    sub = frame[max(0, sy - 130):sy + 130, max(0, sx - 140):min(1080, sx + 140)]
    hit = find(sub, "guardian/slot_empty.png", SLOT_EMPTY)
    return hit is not None


def tap_at(pt, reason: str):
    ev = act.tap(pt[0], pt[1], reason=reason)
    logger.event("tourney_tap", reason=reason, x=ev["x"], y=ev["y"])
    time.sleep(0.9)


# ------------------------------------------------------------ navigation

def on_home(frame) -> bool:
    return find(frame, "home/dissonant_run.png") is not None


def return_to_game(what: str = "") -> np.ndarray:
    """Exit a menu back to the home screen.

    There are two kinds of menu and they exit differently: overlays (guild,
    tournament) carry a "Tap To Return To Game" strip along the bottom, while
    the tabbed screens (cards, modules) carry the six-icon nav row in the same
    place - tapping the strip position there just lands on whichever nav icon
    is nearest. So try both, alternating.
    """
    frame = capture.grab()
    for i in range(4):
        if i % 2 == 0:
            tap_at(RETURN_STRIP, f"return_to_game {what}".strip())
        else:
            tap_at(NAV["battle"], f"nav battle from {what}".strip())
        frame = capture.grab()
        if on_home(frame):
            return frame
    logger.shot(frame, "tourney_stuck_return")
    raise Abort(f"could not get back to the home screen from {what or 'a menu'}")


def open_nav(name: str, marker: str, what: str):
    tap_at(NAV[name], f"nav {name}")
    return require(marker, what)


# -------------------------------------------------------------- the steps

def end_round():
    """End a farming run so the tournament can be entered.

    A tournament cannot start while a run is going, so this is part of the
    start routine, not a stray action - the user does exactly this by hand
    (side menu -> END ROUND -> Yes -> GAME STATS -> HOME).

    Note the HOME tap: the orchestrator's DEATH handler must never press HOME (it
    would drop out of the farm loop and idle forever), which is why that
    button is fenced off there. Here it is the point - it is the only way back
    to the screen the tournament is entered from, and the round is already
    over by then.
    """
    frame = capture.grab()
    # ABSOLUTE RULE (user, 2026-08-15): "you NEVER cancel a tournament run
    # EVER". This guard is at the chokepoint - every exit/surrender tap in
    # this module goes through here. It exists because the post-ticket
    # open_tournament() call walked through ensure_home(), took the freshly
    # bought 10-gem tournament run for a farm battle in the way, and
    # surrendered it at wave 1.
    if in_tournament(frame):
        logger.shot(frame, "tourney_end_round_refused")
        raise Abort("a TOURNAMENT run is on screen - automation never "
                    "cancels a tournament run")
    if detect.death_screen(frame)[0] or find(frame, "home/game_stats_home.png"):
        pass                                    # already at the stats screen
    elif (find(frame, "home/exit_battle_dialog.png")
          or find(frame, "home/end_round_dialog.png")):
        # a confirm left up by an interrupted exit - either variant
        hit = (find(frame, "home/surrender.png", 0.90)
               or find(frame, "home/end_round_yes.png", 0.90))
        if not hit:
            frame, pt = require("home/surrender.png", "exit battle dialog")
            hit = (pt,)
        tap_at(hit[0], "exit battle: confirm")
    else:
        if not detect.side_menu_open(frame):
            tap_at(tuple(CONFIG["side_menu"]["toggle"]), "open side menu")
        # POLL for the button rather than trusting the next frame - the menu
        # slides in, and a frame grabbed mid-animation misses the template.
        deadline = time.monotonic() + 6.0
        while True:
            frame = capture.grab()
            hit = (find(frame, "buttons/end_round.png", 0.85)
                   or find(frame, "buttons/exit_battle.png", 0.85))
            if hit:
                break
            if time.monotonic() > deadline:
                logger.shot(frame, "tourney_no_end_round")
                raise Abort("neither END ROUND nor EXIT BATTLE "
                            "on the side menu")
            time.sleep(0.3)
        # the same slot carries two labels (END ROUND / EXIT BATTLE), and the
        # LABEL DOES NOT PREDICT THE DIALOG: tapping END ROUND on a plain
        # Tier 18 run opened the Surrender/Go Home dialog (observed live,
        # 2026-08-14). So tap whichever button is there, then answer whichever
        # confirm appears. Surrender over Go Home always - "Go Home" only
        # hides the run, it does NOT end it, so a tournament started after it
        # would be refused; Yes/No has no such trap.
        tap_at(hit[0], "exit battle")
        deadline = time.monotonic() + 8.0
        while True:
            frame = capture.grab()
            yes = (find(frame, "home/end_round_yes.png", 0.90)
                   or find(frame, "home/surrender.png", 0.90))
            if yes:
                tap_at(yes[0], "exit battle: confirm")
                break
            if time.monotonic() > deadline:
                logger.shot(frame, "tourney_no_exit_confirm")
                raise Abort("no exit confirm dialog after tapping the "
                            "exit button")
            time.sleep(0.3)

    # bank the run in the same per-run log the farming deaths produce
    frame, home = require("home/game_stats_home.png", "GAME STATS screen", 12.0)
    if detect._match(frame, "icons/game_stats.png", 0.75)[0]:
        from runtime import runlog
        runlog.collect(CONFIG["active_instance"])
        frame = capture.grab()
        hit = find(frame, "home/game_stats_home.png")
        home = hit[0] if hit else home
    tap_at(home, "game stats: HOME")
    frame = capture.grab()
    if not on_home(frame):
        time.sleep(1.5)
        frame = capture.grab()
    logger.event("tourney_end_round", reached_home=on_home(frame))
    return frame


def _in_battle(frame) -> bool:
    """Is a run on screen? Re-checked every pass rather than once, because a
    dialog sitting over the wave counter makes the primary signal read False
    for exactly as long as the dialog is up."""
    return (wave_reader.read_wave(frame) is not None
            or detect.side_menu_open(frame)
            or find(frame, "home/exit_battle_dialog.png") is not None
            or find(frame, "buttons/exit_battle.png", 0.85) is not None
            or find(frame, "buttons/end_round.png", 0.85) is not None
            or find(frame, "home/game_stats_home.png") is not None)


def live_run(frame) -> bool:
    """A battle actually in progress - not the GAME STATS screen a finished
    run leaves behind (which _in_battle also counts, because the routines
    that walk Home must still handle it)."""
    if not _in_battle(frame):
        return False
    if detect.death_screen(frame)[0]:
        return False
    return find(frame, "home/game_stats_home.png") is None


# How long ensure_home() waits for a live run to end on its own before it
# gives up (raises). A coin run lasts ~6h; this is the same order as combo's
# boundary timeout. It is a runaway stop, not a design parameter.
HOLD_LIVE_RUN_SEC = 8 * 3600


def ensure_home():
    """Back out to the home screen from wherever the routine was started.

    A LIVE RUN IS NEVER ENDED HERE (user, 2026-09-05: "even if someone else
    started a run you do not interrupt"). Until then this walked a live run
    out through END ROUND - correct for the runs automation started itself,
    but the same code ran over a human's run whenever a handoff and a person
    met at the screen. Now a live run is HELD: this polls until the run ends
    on its own (death -> GAME STATS), then banks it and taps HOME as before.
    The callers that own a run they want gone use shard.abandon_run, which
    the shard loop applies only to runs it started itself.

    A tournament run still raises (never cancelled, and the caller spends the
    tournament day on the refusal - see combo._mark_block_done).
    """
    taps = 0
    held_at = None
    while True:
        frame = capture.grab()
        if on_home(frame):
            if held_at is not None:
                logger.event("tourney_home_hold_release",
                             held_sec=int(time.monotonic() - held_at))
            return frame
        if _in_battle(frame):
            if live_run(frame):
                if in_tournament(frame):
                    logger.shot(frame, "tourney_ensure_home_refused")
                    raise Abort("a TOURNAMENT run is in progress - "
                                "automation never cancels a tournament run")
                if held_at is None:
                    held_at = time.monotonic()
                    logger.event("tourney_home_hold", reason="run in progress",
                                 wave=wave_reader.read_wave(frame),
                                 shot=logger.shot(frame, "tourney_home_hold"))
                elif time.monotonic() - held_at > HOLD_LIVE_RUN_SEC:
                    logger.shot(frame, "tourney_home_hold_timeout")
                    raise Abort("a run has been in progress for over "
                                f"{HOLD_LIVE_RUN_SEC // 3600}h - still not "
                                "ending it; giving up on reaching Home")
                time.sleep(5.0)
                continue
            # the GAME STATS screen: the run is over - bank it, tap HOME
            logger.event("tourney_end_round", reason="stats screen",
                         wave=wave_reader.read_wave(frame))
            end_round()
            continue
        if held_at is not None:
            # Holding on somebody's run and the screen is neither the battle
            # nor Home: a person is in the menus. Touch NOTHING - keep
            # waiting (2026-09-05: three "back out to home"/"nav battle"
            # rounds went into a menu excursion during a hold).
            if time.monotonic() - held_at > HOLD_LIVE_RUN_SEC:
                logger.shot(frame, "tourney_home_hold_timeout")
                raise Abort("holding on a live run for over "
                            f"{HOLD_LIVE_RUN_SEC // 3600}h - giving up on "
                            "reaching Home")
            time.sleep(5.0)
            continue
        taps += 1
        if taps > 5:
            break
        tap_at(RETURN_STRIP, "back out to home")
        frame = capture.grab()
        if on_home(frame):
            return frame
        tap_at(NAV["battle"], "nav battle")
    frame = capture.grab()
    if on_home(frame):
        return frame
    logger.shot(frame, "tourney_not_home")
    raise Abort("cannot reach the home screen - is a run still going?")


def open_tournament():
    """Home -> tournament screen, dealing with the ticket reward popup."""
    frame = ensure_home()
    hit = find(frame, "tourney/trophy_tile.png")
    if not hit:
        logger.shot(frame, "tourney_no_trophy")
        raise Abort("tournament tile not on the left rail")
    tap_at(hit[0], "open tournament")

    # a reward animation can sit in front: SKIP fast-forwards it and claims
    for _ in range(5):
        frame = capture.grab()
        sc = screen.identify(frame)
        if sc.name == "tournament":
            return frame
        if sc.name == "ticket_reward":
            skip = find(frame, "tourney/ticket_skip.png")
            tap_at(skip[0] if skip else (538, 1966),
                   "ticket skip" if skip else "ticket claim")
            continue
        time.sleep(0.8)
    frame = capture.grab()
    sc = screen.identify(frame)
    if sc.name != "tournament":
        logger.shot(frame, "tourney_not_tournament")
        raise Abort(f"expected the tournament screen, on {sc}")
    return frame


def read_conditions(frame):
    """Open the HEAT dialog and keep a screenshot of it.

    The conditions are free text with levels, so there is nothing to template-
    match; they drive the card/module choices a human makes. Automating that
    judgement needs OCR, which is not built yet - so this records the evidence
    and logs it, and the fixed plan above is applied regardless. The one rule
    the user gave that this cannot yet check is: Tank Ultimate and NO Ranged
    Ultimate means swapping Sharp Fortitude for Orbital Augment.
    """
    hit = find(frame, "tourney/heat_icon.png")
    if not hit:
        logger.event("tourney_conditions", read=False, why="heat icon not found")
        return
    tap_at(hit[0], "open tournament heat")
    frame = capture.grab()
    # the list can open scrolled: drag it back to the top before the shot
    act.swipe(538, 1400, 538, 1900, 500, reason="heat list to top")
    time.sleep(0.8)
    frame = capture.grab()
    path = logger.shot(frame, "tourney_conditions")
    logger.event("tourney_conditions", read=True, shot=str(path))
    # close: the dialog X, else the return strip
    tap_at((1001, 477), "close heat dialog")


def guardian_swap(chips=CHIPS_IN):
    """Guild > Guardian: clear the three slots, then equip `chips`.

    Parameterised because the swap has to run BOTH WAYS. It was written for the
    one-way trip into a tournament, but the coin farm needs its own guardians
    (fetch / bounty / summon) put back afterwards - and a hardcoded CHIPS_IN
    meant the return leg would have re-equipped the tournament set instead.
    Defaults to CHIPS_IN so existing tournament callers are unchanged."""
    frame = capture.grab()
    hit = find(frame, "home/tile_guild.png")
    if not hit:
        logger.shot(frame, "tourney_no_guild_tile")
        raise Abort("guild tile not on the left rail")
    tap_at(hit[0], "open guild")
    frame, _ = require("guardian/tab_guardian.png", "guild screen")
    tap_at(GUARDIAN_TAB, "guardian tab")

    # v29 GUARD (2026-08-27): the redesigned guardian screen carries its own
    # loadout tabs (this account: "Farm"/"Tourney") and a new slot layout -
    # the legacy geometry below tapped the edge of a live slot and unequipped
    # a farm chip before the stuck-guard fired. Presets auto-save, so a
    # single stray tap here is a permanent build edit: refuse BEFORE the
    # first tap whenever the tab row is visible.
    frame = capture.grab()
    if (find(frame, "presets/guardians_farm.png", 0.85)
            or find(frame, "presets/guardians_tourney.png", 0.85)):
        logger.shot(frame, "guardian_v29_tabs")
        raise Abort("v29 guardian screen (Farm/Tourney preset tabs) - the "
                    "legacy chip swap would mutate the active preset; "
                    "select a guardian preset tab instead")

    # clear the three unlocked slots - tapping a slot returns whatever is in it.
    # The 4th slot is LOCKED and is deliberately not in GUARDIAN_SLOTS.
    for attempt in range(4):
        frame = capture.grab()
        occupied = [s for s in GUARDIAN_SLOTS if not _slot_empty(frame, s)]
        if not occupied:
            break
        tap_at(occupied[0], "unequip guardian chip")
    else:
        logger.shot(capture.grab(), "tourney_slots_stuck")
        raise Abort("could not clear the guardian slots")
    logger.event("tourney_guardian", stage="slots cleared")

    for chip in chips:
        frame = capture.grab()
        hit = find(frame, f"guardian/chip_{chip}.png", STRICT)
        if not hit:
            logger.shot(frame, f"tourney_chip_{chip}_missing")
            raise Abort(f"guardian chip {chip} not in the inventory")
        (cx, cy), _ = hit
        if greenness(frame, cx + CHIP_CHECK_OFFSET[0], cy + CHIP_CHECK_OFFSET[1]) > TICK_ON:
            logger.event("tourney_guardian", chip=chip, already_equipped=True)
            continue
        tap_at((cx, cy), f"equip chip {chip}")

    frame = capture.grab()
    got = [c for c in chips
           if (h := find(frame, f"guardian/chip_{c}.png", STRICT))
           and greenness(frame, h[0][0] + CHIP_CHECK_OFFSET[0],
                         h[0][1] + CHIP_CHECK_OFFSET[1]) > TICK_ON]
    logger.event("tourney_guardian", equipped=got)
    if len(got) != len(chips):
        logger.shot(frame, "tourney_guardian_incomplete")
        raise Abort(f"guardian chips equipped: {got}, wanted {list(chips)}")
    return_to_game("guild")


GRID_FLING_MS = 180     # the user flings these lists, they do not drag them:
                        # their own recorded gestures run ~1900px in ~180ms
                        # (7000-11000 px/s), and one fling crosses the whole
                        # grid. 450ms drags were both slower and less complete.


GRID_STILL = 1.0        # mean abs pixel diff below which the grid did not move
GRID_MARGIN = 150       # slack around the swipe band when searching it


def _find_in_band(frame, rel: str, top: int, bottom: int, thresh: float):
    """find() restricted to the scrolling grid, in full-frame coordinates.

    Two reasons, and the second is not about speed. Matching the whole 2560px
    frame costs several hundred ms per template, and this walks the grid with
    one template per item per page. But the header of the modules screen also
    DRAWS THE EQUIPPED MODULES - so a full-frame search can match a module up
    there and hand back a coordinate that is not an inventory tile at all.
    """
    lo = max(0, min(top, bottom) - GRID_MARGIN)
    hi = min(frame.shape[0], max(top, bottom) + GRID_MARGIN)
    hit = find(frame[lo:hi], rel, thresh)
    if hit is None:
        return None
    (cx, cy), score = hit
    return (cx, cy + lo), score


def _grid_same(a, b, top: int, bottom: int) -> bool:
    """Did a scroll actually move anything?

    Compared with a TOLERANCE, not exactly. The tiles glow and shimmer, so two
    frames of a motionless grid still differ slightly and `np.array_equal` is
    never true - which silently disabled every early-exit built on it and left
    the searches paying their full fixed swipe count anyway.

    Measured on a motionless grid: 0.01 idle, 0.23 across a scroll that had
    nowhere to go (bounce). A scroll that really pages the list moves most of
    the tiles and lands far above 1.0.
    """
    lo, hi = min(top, bottom), max(top, bottom)
    return float(cv2.absdiff(a[lo:hi], b[lo:hi]).mean()) < GRID_STILL


def _scroll_inventory(top: int, bottom: int):
    """One SHORT nudge down the list - deliberately not a page.

    CONSECUTIVE VIEWS MUST OVERLAP BY MORE THAN A TILE, or an item can
    straddle the view edge in every frame and never be seen whole. That is
    how Extra Orb went unfound twice on 2026-08-15: its tile sat cut in half
    at the band bottom, and the full-band fling (with glide on top) carried
    it clean over the top edge of the next view. A 260px drag is under one
    card tile (~290px), so even with fling glide the walk cannot step over
    anything. Short lists cost nothing extra - the bottom detector stops the
    walk the moment a nudge no longer changes the pixels.
    """
    act.swipe(538, top + 260, 538, top, 500, reason="scroll inventory")
    time.sleep(0.5)


def _scroll_to_top(top: int, bottom: int):
    """Park the grid at row 1 before scanning.

    Without this the search only ever looks DOWNWARD from wherever the last
    step left the list, so an item that ended up above the viewport is
    reported missing - which is exactly how a re-run of this routine failed to
    find a card it had itself scrolled past.

    Swipes until the grid STOPS MOVING rather than a fixed four times. The
    module inventory is about one screen (the user: "you will never have more
    than 1 screen of modules, maybe a bit more"), so four drags were three
    drags spent confirming nothing had happened - on every module, every run.
    An already-parked grid now costs one fling.
    """
    prev = capture.grab()
    for i in range(4):
        act.swipe(538, top, 538, bottom, GRID_FLING_MS,
                  reason="scroll inventory to top")
        time.sleep(0.5)
        cur = capture.grab()
        if _grid_same(prev, cur, top, bottom):
            logger.event("grid_scroll", to="top", swipes=i + 1)
            return
        prev = cur
    logger.event("grid_scroll", to="top", swipes=4)


def _scan_grid(rels, top: int, bottom: int, tries: int = 4) -> dict:
    """Locate SEVERAL inventory tiles in one pass over the grid.

    Searching them one at a time meant one scroll-to-top, one page walk and a
    fresh screen grab per item - and the grabs, not the swipes, are the
    expensive part (~300ms each). Here the grid is walked once and every
    template is tested against each frame, so N items cost one walk instead of
    N. Returns {rel: centre} for those found; anything missing from the dict
    is not in the inventory.

    Used for the PRESENCE question only. Positions go stale as soon as one
    item is equipped, because equipping removes it and the grid reflows - so
    callers that act on a tile re-locate it against a fresh frame. "Absent"
    does not go stale: equipping one module cannot make another appear.
    """
    _scroll_to_top(top, bottom)
    found, prev = {}, None
    for _ in range(tries):
        frame = capture.grab()
        for rel in rels:
            if rel not in found:
                hit = _find_in_band(frame, rel, top, bottom, STRICT)
                if hit:
                    found[rel] = hit[0]
        if len(found) == len(rels):
            break
        if prev is not None and _grid_same(prev, frame, top, bottom):
            break                       # bottom of the list
        prev = frame
        _scroll_inventory(top, bottom)
    logger.event("grid_scan", wanted=len(rels), found=len(found))
    return found


def _find_in_grid(rel: str, top: int, bottom: int, tries: int = 12,
                  from_top: bool = True):
    # tries=12 because scrolling is now by SHORT NUDGE (see _scroll_inventory)
    # rather than by page - the early bottom-stop keeps short lists cheap.
    """Look for an inventory tile, scanning the grid from the top down.

    Stops early once a scroll no longer changes the grid - that is the bottom
    of the list, and every further scan would re-read the same pixels. This is
    the common path, not an edge case: an EQUIPPED module is absent from the
    inventory, so "already equipped" is discovered by searching the whole grid
    and finding nothing, and it used to pay the full four scans to do it.
    """
    if from_top:
        _scroll_to_top(top, bottom)
    for i in range(tries):
        frame = capture.grab()
        hit = _find_in_band(frame, rel, top, bottom, STRICT)
        if hit:
            return frame, hit[0]
        if i < tries - 1:
            _scroll_inventory(top, bottom)
            if _grid_same(frame, capture.grab(), top, bottom):
                break               # bottom reached; nothing new to look at
    return capture.grab(), None


def _locate_card(name: str):
    """Find a card AND make sure its in-deck tick is actually visible.

    The tick sits 165px below the tile centre, so a card in the bottom row of
    the grid has it hidden behind the filter/nav bars and reads as 'not in the
    deck' - which would make the routine add a card that is already in. When
    that happens, scroll the grid up a notch and look again.
    """
    rel = f"cards/{name}.png"
    for i in range(3):
        frame, pt = _find_in_grid(rel, 1200, 2200, from_top=(i == 0))
        if pt is None:
            return frame, None, 0.0
        if pt[1] + CHECK_OFFSET[1] <= READABLE_BOTTOM:
            return frame, pt, greenness(frame, pt[0] + CHECK_OFFSET[0],
                                        pt[1] + CHECK_OFFSET[1])
        _scroll_inventory(1500, 1900)       # short nudge, keeps it on screen
    return capture.grab(), None, 0.0


TOURNEY_LOADOUT = "tourney_1"   # the config.yaml loadouts key setup() equips.
                                # cards/guardians/modules there are IDENTICAL
                                # to CARD_PRESET / CHIPS_IN / MODULE_PLAN - the
                                # constants are kept as the read-only checker's
                                # fallback and as this module's own record.


def card_tweaks():
    """The condition-driven deck tweaks, ON TOP of whatever preset is loaded.

    Split out of card_swap so the PRESET LOAD can go through loadout.apply()
    with every other equip while these stay here, where they belong: they are
    the user's standing plan (drop Cash, add Extra Orb), not part of any named
    loadout.

    The 2026-08-15 rule stands: a tweak that cannot be applied is logged with a
    screenshot and SKIPPED - entering with the plain preset always beats not
    entering. Only the preset load itself is fatal, and that is loadout's.
    """
    open_nav("cards", CARD_PRESET, "cards screen")
    for name, want_in in [(n, False) for n in CARDS_DROP] + \
                         [(n, True) for n in CARDS_ADD]:
        frame, pt, tick = _locate_card(name)
        if pt is None:
            logger.event("tourney_cards", card=name, skipped=True,
                         shot=logger.shot(frame,
                                          f"tourney_card_{name}_unreadable"))
            continue
        in_deck = tick > TICK_ON
        logger.event("tourney_cards", card=name, want_in=want_in,
                     in_deck=in_deck, tick=round(tick, 3))
        if in_deck != want_in:
            tap_at(pt, f"{'add' if want_in else 'remove'} card {name}")
    return_to_game("cards")


def verify_loadout(name: str = TOURNEY_LOADOUT) -> list[str]:
    """READ-ONLY parity check: is `name` already equipped? Returns mismatches.

    WHAT "READ ONLY" MEANS HERE, precisely: no tap that CHANGES STATE. It still
    navigates - you cannot read the cards screen without opening it, and the
    existing read-only pass already taps its way into the tournament and back
    out - but nothing is selected, equipped, unequipped or bought. Every check
    is the same reader the equip path verifies itself with:

      * cards    - loadout.current_cards(), the hue test on the tab row
      * guardians- the green in-deck tick beside each chip, the same test
                   guardian_swap asserts on before it returns
      * modules  - verify_slot(), the header match that is the LAST WORD on
                   whether a module is really in its slot (2026-08-15: a stale
                   grid scan said yes twice while the module lay in the grid)

    An empty list means the account is already in this loadout, which is what
    has to pass before P6 is allowed near a real tournament entry.
    """
    from interactions import loadout
    lo = loadout.spec(name)
    problems: list[str] = []

    if lo.get("global_preset"):
        # A global-preset loadout is applied by the game AT BATTLE ENTRY, so
        # the equipped screens prove nothing - what the ticket is spent on is
        # whatever the PICKER says. Re-read it, from scratch.
        from interactions import presets
        if not presets.verify_global(lo["global_preset"]):
            problems.append(f"global preset {lo['global_preset']!r} is not "
                            f"the picker's current selection")
        return problems

    if lo.get("cards"):
        open_nav("cards", loadout.CARD_TPL.format(lo["cards"]), "cards screen")
        got = loadout.current_cards()
        if got != lo["cards"]:
            problems.append(f"cards: {got!r} selected, want {lo['cards']!r}")
        return_to_game("cards")

    if lo.get("guardians"):
        chips = lo["guardians"]
        chips = tuple(chips) if isinstance(chips, (list, tuple)) else CHIPS_IN
        frame = capture.grab()
        hit = find(frame, "home/tile_guild.png")
        if not hit:
            problems.append("guardians: guild tile not on the left rail")
        else:
            tap_at(hit[0], "open guild")
            require("guardian/tab_guardian.png", "guild screen")
            tap_at(GUARDIAN_TAB, "guardian tab")
            frame = capture.grab()
            for c in chips:
                h = find(frame, f"guardian/chip_{c}.png", STRICT)
                on = bool(h) and greenness(
                    frame, h[0][0] + CHIP_CHECK_OFFSET[0],
                    h[0][1] + CHIP_CHECK_OFFSET[1]) > TICK_ON
                if not on:
                    problems.append(f"guardians: {c} "
                                    + ("not equipped" if h else "not found"))
            return_to_game("guild")

    if lo.get("modules"):
        open_nav("modules", "modules/buy_module.png", "modules screen")
        frame = capture.grab()
        for mod, slot in lo["modules"]:
            ok = verify_slot(mod, slot, frame)
            if ok is None:
                problems.append(f"modules: {mod} in {slot} NOT VERIFIABLE "
                                f"(no template / unknown module)")
            elif not ok:
                problems.append(f"modules: {mod} is not in the {slot} slot")
        return_to_game("modules")

    logger.event("tourney_verify_loadout", loadout=name, ok=not problems,
                 problems=problems)
    return problems


def card_swap():
    """Cards: load the tournament preset, then the condition-driven tweaks.

    SUPERSEDED by setup()'s loadout.apply() + card_tweaks() and kept as the
    one-call form (and for any caller that still wants the old sequence).

    The PRESET is the base deck; the tweaks (drop Cash, add Extra Orb) are
    the user's standing plan on top of it. Two rules learned 2026-08-15,
    when a failed Extra Orb lookup aborted a valid 22/22 deck and cost the
    Saturday tournament entry:
      * a tweak that cannot be applied is logged with a screenshot and
        SKIPPED - entering with the plain preset always beats not entering;
      * the preset load itself stays fatal on failure, because entering
        with the farm deck would waste the ticket.
    """
    open_nav("cards", CARD_PRESET, "cards screen")
    frame, pt = require(CARD_PRESET, "Tourney P1 preset tab")
    tap_at(pt, "load preset Tourney P1")
    logger.event("tourney_cards", stage="preset loaded")
    return_to_game("cards")
    # ONE COPY OF THE TWEAKS, shared with setup()'s loadout path - two copies
    # of "drop Cash, add Extra Orb" is two places to update when the plan does.
    card_tweaks()


# Which category's header slots a module occupies. The art gives it away
# (circle=cannon, octagon=armor, triangle=generator, diamond=core) but the
# code should not have to know that - it is data.
MODULE_CATEGORY = {
    "amplifying_strike": "cannon", "sharp_fortitude": "armor",
    "black_hole_digestor": "generator", "galaxy_compressor": "generator",
    "pulsar_harvester": "generator", "multiverse_nexus": "core",
    "primordial_collapse": "core", "dimension_core": "core",
    # Inner Land Mines quest (user, 2026-08-16): its unique effect spawns
    # Inner Land Mines without owning the weapon - the whole quest hinges
    # on this one health module being equipped.
    "space_displacer": "armor",
}
# The 8 equipped-module slots drawn on the modules-screen header (tower
# diagram): (centre x, centre y, half-size). Primaries are the larger inner
# icons, assists the smaller outer ones. Measured 2026-08-15.
HEADER_SLOTS = {
    ("cannon", "primary"): (307, 416, 120), ("cannon", "assist"): (115, 416, 90),
    ("armor", "primary"): (307, 672, 120), ("armor", "assist"): (115, 666, 90),
    ("generator", "primary"): (772, 431, 120), ("generator", "assist"): (942, 422, 90),
    ("core", "primary"): (772, 655, 120), ("core", "assist"): (942, 663, 90),
}
SLOT_VERIFY_THRESH = 0.65   # measured on a known header: the right module
                            # scores 0.87-0.96 in its slot, a wrong one
                            # 0.22-0.40 - a gap wide enough to bet a swap on


def verify_slot(name: str, slot: str, frame=None):
    """Is `name` VISIBLY sitting in its category's `slot` on the header?

    True / False, or None when it cannot be verified (unknown module, or no
    grid template to match with). The check is multi-scale because the header
    draws primaries and assists at different sizes than the grid tile the
    template was cut from, and it matches the tile's INTERIOR art only - the
    rarity frame and Lv. label do not survive the move to the header.

    This exists because of 2026-08-15: "already equipped" was twice inferred
    (from a stale grid scan) while the module in question was actually lying
    in the grid, and the tournament ran on the wrong primaries. The user's
    ruling: verify the slot after EVERY equip, because modules displace each
    other - so inference is never the last word, the header is.
    """
    cat = MODULE_CATEGORY.get(name)
    if cat is None:
        return None
    try:
        tpl = detect._tpl(f"modules/{name}.png")
    except detect.TemplateMissing:
        return None
    frame = frame if frame is not None else capture.grab()
    cx, cy, half = HEADER_SLOTS[(cat, slot)]
    crop = frame[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
    if crop.size == 0:
        return None
    h, w = tpl.shape[:2]
    t = tpl[int(h * 0.25):int(h * 0.65), int(w * 0.25):int(w * 0.75)]
    best = 0.0
    for s in np.arange(0.45, 1.15, 0.05):
        tt = cv2.resize(t, None, fx=float(s), fy=float(s))
        if tt.shape[0] >= crop.shape[0] or tt.shape[1] >= crop.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(crop, tt,
                                                 cv2.TM_CCOEFF_NORMED).max()))
    return best >= SLOT_VERIFY_THRESH


def _equip_module(name: str, slot: str, present: set | None = None) -> str:
    """One module: tile -> Equip -> Primary/Assist -> Transfer Level: Yes.

    Returns 'equipped', or 'already' when the module is not in the inventory -
    which is what an already-equipped module looks like, since equipping moves
    it out of the grid. That makes the whole routine safe to re-run.

    `present` is an optional result of _scan_grid: when given, a module absent
    from it is known to be equipped already and the grid walk is skipped
    entirely. But ABSENT-FROM-GRID IS ONLY EVER A HINT: every 'already' and
    every 'equipped' is confirmed against the header slot (verify_slot), and
    a failed confirmation falls through to a fresh grid walk rather than
    being believed - a displaced module lands back in the grid, and a scan
    from before the displacement knows nothing about it.
    """
    rel = f"modules/{name}.png"
    if present is not None and rel not in present:
        if verify_slot(name, slot) is not False:
            logger.event("tourney_modules", module=name, result="already",
                         via="scan")
            return "already"
        logger.event("tourney_modules", module=name, via="scan",
                     result="stale - not in its slot, walking the grid")
    frame, pt = _find_in_grid(rel, 1100, 2200)
    if pt is None:
        if verify_slot(name, slot) is False:
            logger.shot(capture.grab(), f"tourney_slot_{name}_missing")
            raise Abort(f"{name} is neither in the grid nor in the "
                        f"{MODULE_CATEGORY.get(name)} {slot} slot")
        logger.event("tourney_modules", module=name, result="already")
        return "already"
    tap_at(pt, f"open module {name}")

    frame, equip = require("modules/equip_btn.png", f"{name} Equip button")
    tap_at(equip, f"equip {name}")

    rel = f"modules/{slot}_btn.png"
    frame, btn = require(rel, f"{name} {slot} slot button")
    tap_at(btn, f"{name} -> {slot}")

    # only offered when the target slot already held a levelled module
    frame, yes = wait_for("modules/transfer_yes.png", 3.0)
    if yes:
        tap_at(yes, f"{name} transfer level yes")
    else:
        logger.event("tourney_modules", module=name, transfer="not offered")

    time.sleep(0.8)
    frame = capture.grab()
    if find(frame, f"modules/{name}.png", STRICT):
        logger.shot(frame, f"tourney_module_{name}_still_listed")
        raise Abort(f"{name} is still in the inventory - equip did not take")
    ok = verify_slot(name, slot, frame)
    if ok is False:
        logger.shot(frame, f"tourney_slot_{name}_wrong")
        raise Abort(f"{name} was equipped but is not showing in the "
                    f"{MODULE_CATEGORY.get(name)} {slot} slot")
    logger.event("tourney_modules", module=name, slot=slot, result="equipped",
                 slot_verified=(True if ok else "no-template"))
    return "equipped"


def module_swap():
    open_nav("modules", "modules/buy_module.png", "modules screen")
    present = set(_scan_grid([f"modules/{n}.png" for n, _ in MODULE_PLAN],
                             1100, 2200))
    results = {}
    # THE BATCH SCAN IS ONLY VALID UNTIL THE FIRST EQUIP - the same stale-scan
    # bug loadout.apply_modules fixed on 2026-08-13, still live here until the
    # 2026-08-15 tournament: equipping Dimension Core displaced Primordial
    # Collapse into the grid, but the pre-swap scan had already recorded PC
    # absent-therefore-equipped, so PC (and GComp, displaced by Pulsar
    # Harvester) were skipped as "already" and the tournament ran on the coin
    # primaries. After any equip, later lookups get a fresh frame instead.
    for name, slot in MODULE_PLAN:
        results[name] = _equip_module(name, slot, present)
        if results[name] == "equipped":
            present = None
    logger.event("tourney_modules", plan=results)
    return_to_game("modules")
    return results


def read_gem_price(frame) -> int | None:
    """The gem cost on the BUY TICKET dialog's right-hand button, or None.

    None means "could not read it", and every caller must treat that as
    "do not buy" - never as "free".
    """
    from vision import wave_reader
    (y0, y1), (x0, x1) = GEM_PRICE_BAND
    sub = frame[y0:y1, x0:x1]
    if sub.size == 0:
        return None
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    glyphs = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (GEM_DIGIT_H[0] <= h <= GEM_DIGIT_H[1]) or w < 5:
            continue                       # the diamond icon is taller
        glyphs.append((x, bw[y:y + h, x:x + w]))
    if not glyphs:
        return None
    glyphs.sort(key=lambda g: g[0])
    tpls = wave_reader._load_templates()
    digits = []
    for _, glyph in glyphs:
        best, best_score = None, GEM_DIGIT_MIN_SCORE
        for d, tpl in tpls.items():
            g = cv2.resize(glyph, (tpl.shape[1], tpl.shape[0]))
            s = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)[0][0]
            if s > best_score:
                best, best_score = d, s
        if best is None:
            return None                    # one bad glyph -> distrust the price
        digits.append(best)
    return int("".join(map(str, digits)))


def _handle_buy_ticket(frame, gem_max: int = 0):
    """On the BUY TICKET dialog: take the free ad; buy with gems only up to
    an explicit cap.

    Returns True when a ticket was obtained (so the caller can retry BATTLE),
    False when the dialog was cancelled.
    """
    hit = find(frame, BUY_TICKET_VIDEO, 0.90)
    if hit:
        logger.event("tourney_buy_ticket", offer="video", taken=True)
        tap_at(hit[0], "buy ticket: watch video")
        # the ad plays out, then the 1 TICKET reward lands
        for _ in range(40):
            time.sleep(3)
            sc = screen.identify(capture.grab())
            if sc.name == "ticket_reward":
                skip = find(capture.grab(), "tourney/ticket_skip.png")
                tap_at(skip[0] if skip else (538, 1966), "ticket skip")
                return True
            if sc.name == "tournament":
                return True
        logger.event("tourney_buy_ticket", offer="video", stuck=True)
        return False
    price = read_gem_price(frame)
    shot = logger.shot(frame, "tourney_buy_ticket_gems")
    if price is not None and 0 < price <= gem_max:
        gem_btn = find(frame, BUY_TICKET_CANCEL, 0.90)
        # the gem button is the RIGHT one; Cancel is the left. Mirror the
        # cancel position about the dialog centre rather than templating a
        # button whose face is a different number every time.
        pt = ((1080 - gem_btn[0][0], gem_btn[0][1]) if gem_btn
              else (720, 1520))
        logger.event("tourney_buy_ticket", offer="gems", price=price,
                     cap=gem_max, taken=True, shot=shot)
        tap_at(pt, f"buy ticket: {price} gems (cap {gem_max})")
        # A gem purchase does not hand back a ticket to spend - it drops
        # STRAIGHT INTO THE RUN. Leaving 'battle' out of this list cost a real
        # entry: the 10 gems were spent, the run started, and the routine
        # declared itself stuck and aborted, so the orchestrator never attached and a
        # live tournament played on unattended.
        for _ in range(10):
            time.sleep(1.0)
            sc = screen.identify(capture.grab())
            if sc.name in ("tournament", "ticket_reward", "battle"):
                if sc.name == "ticket_reward":
                    skip = find(capture.grab(), "tourney/ticket_skip.png")
                    tap_at(skip[0] if skip else (538, 1966), "ticket skip")
                logger.event("tourney_buy_ticket", offer="gems", price=price,
                             landed_on=sc.name)
                return True
        logger.event("tourney_buy_ticket", offer="gems", price=price,
                     stuck=True)
        return False

    cancel = find(frame, BUY_TICKET_CANCEL, 0.90)
    logger.event("tourney_buy_ticket", offer="gems", price=price, cap=gem_max,
                 taken=False,
                 why=("unreadable price" if price is None else "over cap"),
                 shot=shot)
    if cancel:
        tap_at(cancel[0], "buy ticket: cancel (costs gems)")
    return False


def start_battle():
    frame = open_tournament()
    pt, which = find_any(frame, BATTLE_BUTTONS)
    if pt is None:
        logger.shot(frame, "tourney_no_battle_btn")
        raise Abort("tournament BATTLE button not found on either layout")
    logger.event("tourney_battle", layout=which.split("/")[1])
    tap_at(pt, "tournament BATTLE")
    time.sleep(2.5)
    frame = capture.grab()

    # With no ticket in hand, BATTLE opens the BUY TICKET dialog instead of
    # starting the run. Take the free ad if that is the offer, then press
    # BATTLE again; otherwise stop - out of free entries is a normal end.
    if screen.identify(frame).name == "buy_ticket":
        gem_max = int((CONFIG["presets"][CONFIG["preset"]]
                       .get("gem_entry_max") or 0))
        if not _handle_buy_ticket(frame, gem_max):
            raise Abort("no tournament entry taken (no ad, and the gem price "
                        f"is unreadable or over the {gem_max}-gem cap)")
        # THE PURCHASE CAN DROP THE GAME STRAIGHT INTO THE RUN. Observed
        # 2026-08-15: buying the 10-gem ticket auto-started the tournament,
        # and the open_tournament() this branch used to make here walked
        # ensure_home() through the fresh run and SURRENDERED it at wave 1.
        # So: look for the run FIRST, and never navigate blind from here -
        # an unrecognized screen aborts rather than risking a tap that ends
        # a tournament run (user: "you NEVER cancel a tournament run EVER").
        time.sleep(2.0)
        frame = capture.grab()
        if in_tournament(frame) or wave_reader.read_wave(frame) is not None:
            logger.event("tourney_battle", started="via ticket purchase")
            logger.shot(frame, "tourney_started")
            return frame
        sc = screen.identify(frame)
        if sc.name != "tournament":
            logger.shot(frame, "tourney_post_ticket_unknown")
            raise Abort(f"after buying the ticket: unrecognized screen "
                        f"'{sc.name}' - stopping, a tournament run may be "
                        "starting behind it")
        pt, which = find_any(frame, BATTLE_BUTTONS)
        if pt is None:
            logger.shot(frame, "tourney_no_battle_btn")
            raise Abort("BATTLE button not found after buying a ticket")
        tap_at(pt, "tournament BATTLE (after ticket)")
        time.sleep(2.5)
        frame = capture.grab()

    logger.shot(frame, "tourney_started")
    return frame


def in_tournament(frame) -> bool:
    """A tournament run puts a trophy badge in front of the Tier readout.

    This is the guard that stops the orchestrator from re-running the setup on top of
    a tournament that is already going - the setup starts by ENDING whatever
    run it finds, so without it a restart would surrender the very run it was
    launched to play.
    """
    return find(frame, "tourney/in_tournament.png") is not None


def setup(read_only: bool = False, loadout_name: str | None = None) -> bool:
    """Run the whole pre-battle routine. Returns True when the battle started.

    THE EQUIPPING IS loadout.py's (P6). This used to carry its own guardian /
    card / module swap logic beside loadout.py's, which is two implementations
    of "put the tournament build on" drifting apart one fix at a time - the
    stale-grid-scan bug was found and fixed in loadout.apply_modules on
    2026-08-13 and was still live in module_swap at the 2026-08-15 tournament,
    which ran on the coin primaries because of it. Now there is one.

    Every verification survives the move, because they live in the functions
    being called, not in the sequence: guardian_swap still aborts unless every
    chip reads equipped, apply_modules still confirms an unharvestable module
    from the equipped HEADER rather than inferring it from the grid, and
    _equip_module still calls verify_slot after every equip.

    read_only walks the same flow and CHANGES NOTHING - see verify_loadout for
    exactly what that means. It returns False either way: no battle started.
    """
    frame = capture.grab()
    if in_tournament(frame):
        logger.event("tourney_setup", stage="skipped",
                     why="a tournament run is already in progress",
                     wave=wave_reader.read_wave(frame))
        return False
    name = loadout_name or TOURNEY_LOADOUT
    logger.event("tourney_setup", stage="begin", loadout=name,
                 dry_run=CONFIG["loop"]["dry_run"], read_only=read_only)
    if read_only:
        # READ-ONLY REFUSES A BUSY SCREEN (Codex P6 #4). open_tournament ->
        # ensure_home ends a live COIN run to clear the way (that is
        # ensure_home's job and it is right for a real entry) - so a read-only
        # validation started while the farm was mid-run would kill the run it
        # promised not to touch. "Taps nothing" has to mean it, including the
        # navigation: so this refuses unless the game is ALREADY parked
        # somewhere the check can start from.
        sc = screen.identify(frame)
        if not (sc.name in ("home", "tournament") or on_home(frame)):
            logger.event("tourney_setup", stage="read_only_refused",
                         loadout=name, on=sc.name,
                         why="read-only starts only from Home or the "
                             "tournament screen - it never ends a run to "
                             "make room for itself",
                         shot=logger.shot(frame, "tourney_read_only_refused"))
            return False
    try:
        frame = open_tournament()
        read_conditions(frame)
        return_to_game("tournament")
        if read_only:
            problems = verify_loadout(name)
            logger.event("tourney_setup", stage="read_only_done",
                         loadout=name, ok=not problems, problems=problems)
            return False
        from interactions import loadout
        # ONE CALL, three screens: cards, guardians, modules - in loadout.py's
        # order rather than this module's old guardians-first one. They are
        # three independent screens, each reached from Home by its own
        # open_nav, so nothing carries between them; the order that IS
        # load-bearing (modules displacing each other) is inside the plan and
        # apply_modules preserves it exactly.
        loadout.apply(name)
        # ...and then the deck tweaks the loadout does not own. NOT under a
        # global preset: the game re-applies the saved deck at battle entry,
        # which would silently wipe any tweak made here - fold the tweak into
        # the in-game card preset instead (v29).
        if loadout.spec(name).get("global_preset"):
            logger.event("card_tweaks_skipped", loadout=name,
                         why="global preset re-applies the saved deck at "
                             "battle entry - tweaks belong in the preset")
        else:
            card_tweaks()
        # THE LAST GATE BEFORE THE TICKET IS SPENT (Codex P6 #3). Everything
        # above reports its own success, but each reader only sees its own
        # screen at its own moment; this re-reads all three from scratch,
        # after the tweaks, and is the only check that sees the build the
        # entry will actually run. A ticket costs 10 -> 20 -> 30 gems and the
        # run auto-starts, so an unverified deck is not something to discover
        # from the leaderboard afterwards.
        problems = verify_loadout(name)
        if problems:
            logger.shot(capture.grab(), "tourney_loadout_unverified")
            raise Abort("refusing to enter the tournament: the equipped "
                        "loadout does not match "
                        f"{name!r} - {'; '.join(problems)}")
        start_battle()
    except (Abort, act.TapRefused) as e:
        logger.event("tourney_setup", stage="abort", error=str(e))
        raise
    logger.event("tourney_setup", stage="done", loadout=name)
    return True


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--read-only", action="store_true",
                    help="enter the tournament, record the conditions, come "
                         "back out - change nothing")
    return ap.parse_args()


if __name__ == "__main__":
    import settings
    _a = _cli()
    settings.select_instance(_a.instance, "tournament")
    print(f"tournament setup on {CONFIG['active_instance']} "
          f"(read_only={_a.read_only}, dry_run={CONFIG['loop']['dry_run']})")
    try:
        started = setup(read_only=_a.read_only)
        print("battle started" if started else "read-only pass complete")
    except (Abort, act.TapRefused) as e:
        print(f"ABORTED: {e}")
        raise SystemExit(1)
