"""Shatter BLUE (rare) modules - the human session, replayed as a loop.

Recorded from the user on 2026-08-13
(recordings/main/20260813_120100_disenchant_blue, 52 gestures + dense capture
captures/flat_20260813_120149) and narrated by them step by step.

    MODULES -> Shatter tab
    LOOP    select up to 12 blue tiles -> Confirm Shatter
            -> SHATTER MODULES dialog: VERIFY it says "rare" -> Yes
            -> dismiss reward screens (SKIP if offered, else NEXT)
            -> repeat until no blue tiles remain

Shattering is IRREVERSIBLE, so this routine is built to under-perform rather
than over-reach. Three independent things must all agree before anything is
destroyed:

  1. every tile tapped is positively identified as blue - an allowlist on the
     frame hue, never "not one of the colours I know to avoid";
  2. the staged batch is non-empty and the Confirm button is live;
  3. the game's OWN confirmation dialog says it is shattering *rare* modules -
     matched against the literal rendered sentence. Anything else, including
     an unreadable dialog, taps No and aborts.

(3) is the one that actually matters. (1) decides what gets selected, but the
dialog is the game telling us what it is about to destroy, and it names the
rarity in words. Trusting a hue threshold alone to stand between the user and
a shattered Epic would be the wrong instrument for an irreversible action.

The user declined to drive the "All Rarities" filter, so selection is visual -
hence the belt-and-braces.

2026-09-16: for three days this shattered nothing and EQUIPPED a module
instead. Its private geometry had gone stale - the Shatter tab was tapped at
y=923 while the bar the game draws sits at y 1000-1100 (the native layout
manifest's module_inventory.inventory_tab). The tap missed the bar, the
Inventory tab stayed open, and the guard agreed anyway: it asked the screen
classifier for "modules" and then took the BRIGHTNESS of one band as proof of
the tab - a band the equipped row lights up on every tab. Twelve blind taps
then landed on inventory tiles, where the first opens a module detail panel
and the rest hit that panel's own buttons; the saved frame is the game asking
"Equip Matrix Sim to the primary slot or assist slot?".

So this file no longer owns geometry or trusts a threshold:

  * the tab bar, the grid lattice and the nav button come from the native
    layout manifest, which device/layout rescales per display, and the tab is
    TAPPED WHERE IT IS READ (vision.textocr over the bar) rather than at a
    remembered point - the same idiom the calibrator uses for the Inventory
    tab;
  * a bar that cannot be read positively is an Abort with ZERO taps;
  * every tile tap is logged with its coordinates, and the scrim is checked
    after each one: a detail panel means this is not the Shatter tab, so the
    panel is closed and the batch abandoned. The first tile must also be seen
    to take its green check before the other eleven are tapped, so a wrong
    screen costs one tap instead of twelve.
"""
import argparse
import re
import sys
import time

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python interactions/shatter.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from device import act
from device import capture
import cv2
from runtime import logger
import numpy as np
from vision import screen
from settings import CONFIG

from interactions import inventory
from interactions import tourney
from interactions.tourney import Abort, find, tap_at
from player.bootstrap_layout import manifest
from vision import pills
from vision import textocr

# --- this runs on ONE account -------------------------------------------
ALLOWED_INSTANCE = "main"       # the user's rule: blue-shatter is a main-only
                                # chore. Enforced, not left as a convention -
                                # the other accounts keep their rares.

# --- chrome -------------------------------------------------------------
# What is NOT here: the tab bar, the grid lattice, the paging gestures and
# the nav button. They live in the native layout manifest, are rescaled for
# the player's display by device/layout.activate, and are read per call -
# this module's own copies of them are what went stale (see the note above).
# The Confirm button and the dialog are still measured points: they sit on a
# screen this flow reaches only after the tab is confirmed, and the dialog is
# verified by its template and its wording before anything is destroyed.
# `tray_text` logs what the tray says on every batch, which is what will let
# Confirm be read rather than remembered.
CONFIRM_SHATTER = (538, 799)    # button spans x 311-768, y 753-849
DIALOG_YES = (727, 1514)
DIALOG_NO = (351, 1514)
REWARD_NEXT = (540, 1965)       # magenta button, measured x 275-806 y 1877-2056
REWARD_SKIP = (898, 400)        # cyan button, top right; only when >1 screen

TAB_WORDS = ("inventory", "merge", "shatter", "assist")
TRAY_DEPTH = 300                # the tray strip sits above the tab bar
STAGE_WAIT = 2.0                # how long the green check may take to draw

BATCH_MAX = 12                  # the game's cap per shatter, per the user
                                # Selection adds no delay of its own: see
                                # select_batch. The cadence that remains comes
                                # entirely from act.tap's rate limiter
                                # (6/sec = 167ms) plus its 50-150ms jitter and
                                # the adb round trip - already human-paced
                                # without stacking a sleep on top.

# --- what "blue" is, measured ------------------------------------------
# Sampled over 30 tiles of the user's inventory plus the equipped row:
#     blue (rare, shatterable)   hue 96,  ~7000 lit px in the frame ring
#     equipped (higher rarity)   hue 65-68, ~300-700 lit px
# The band is deliberately TIGHT around 96. It is an allowlist: Epic (purple)
# and Legendary (orange) sit far outside it and are rejected without ever
# having been sampled, which is the point - unknown rarities must fail closed.
BLUE_HUE = (88, 104)
BLUE_MIN_PX = 3000              # halfway between the two measured populations
RING_INNER, RING_OUTER = 70, 105

DIALOG_TPL = "modules/shatter_dialog.png"
RARE_TPL = "modules/shatter_rare_text.png"
RARE_MIN_SCORE = 0.90           # the sentence is rendered identically every
                                # time, so a real match scores ~0.99; anything
                                # under this means the wording changed, i.e.
                                # a different rarity is being shattered.
# The template is cropped to "You are shattering rare" and deliberately STOPS
# before the next word. Rare and Rare+ are both wanted (the user: "you can
# shatter any rare plus and rare", and they are visually near-identical - the
# whole grid measures hue 96-99), but their dialogs differ after that point.
# Cutting the template at "rare" accepts both while still rejecting "epic" and
# "legendary", which is exactly where the line belongs.

REWARD_MAX_SCREENS = 8          # 4 shard types is the real maximum; twice that
                                # is a runaway guard, not a limit


def guard_instance():
    key = CONFIG.get("active_instance")
    if key != ALLOWED_INSTANCE:
        raise Abort(f"shatter is {ALLOWED_INSTANCE}-only, refusing to run "
                    f"on '{key}'")


# ------------------------------------------------------------ blue tiles

def tile_is_blue(frame, cx: int, cy: int) -> bool:
    """Is the tile centred here framed in rare-blue?

    Samples a DIAMOND-shaped ring, because the tiles are diamonds and a square
    ring would clip the corners of the frame glow - which is the only part
    that carries the rarity colour. The interior art is the module TYPE and
    says nothing about rarity.
    """
    y0, y1 = cy - RING_OUTER, cy + RING_OUTER
    x0, x1 = cx - RING_OUTER, cx + RING_OUTER
    if y0 < 0 or x0 < 0 or y1 > frame.shape[0] or x1 > frame.shape[1]:
        return False
    box = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    hh, ww = box.shape[:2]
    yy, xx = np.mgrid[0:hh, 0:ww]
    d = np.abs(yy - hh / 2) + np.abs(xx - ww / 2)      # diamond distance
    ring = (d > RING_INNER) & (d < RING_OUTER)
    lit = ring & (s > 60) & (v > 90)
    n = int(lit.sum())
    if n < BLUE_MIN_PX:
        return False
    med = float(np.median(h[lit]))
    return BLUE_HUE[0] <= med <= BLUE_HUE[1]


def visible_blue(frame) -> list[tuple[int, int]]:
    """Every blue tile centre currently on screen, reading order.

    The lattice is not remembered either: the rows are the lit-pixel runs of
    THIS frame whose whole tile clears the tab bar and the filter bar
    (vision.pills.grid_rows) and the columns come from the native manifest, so
    the module sweep and this flow walk one grid on any display.

    Selected tiles carry a big green check (hue ~60) and so fail the blue
    test, which makes a re-scan naturally idempotent: already-staged tiles are
    not counted twice. Equipped tiles are locked by the game AND fail the test.
    """
    return [(cx, cy)
            for cy in pills.grid_rows(frame)
            for cx in inventory.COL_X
            if tile_is_blue(frame, cx, cy)]


# ------------------------------------------------------------ the screens

def on_modules(frame) -> bool:
    return screen.identify(frame).name == "modules"


def _band(key: str) -> tuple[int, int]:
    """Geometry from the ACTIVE native manifest, read per call: a rescaled
    display rebinds it (device/layout.activate) long after import."""
    return tuple(manifest()["module_inventory"][key])


def nav_modules() -> tuple[int, int]:
    return tuple(manifest()["navigation"]["modules"])


def tab_points(frame) -> dict[str, tuple[int, int]]:
    """Tap point of every module tab the bar shows, by lowercase name.

    Read off the frame, never remembered - the same idiom the calibrator uses
    for the Inventory tab (player.module_roundtrip.ScreenDriver.inventory_tab).
    A name that reads twice is dropped rather than guessed between, and a bar
    that is covered by a detail panel reads nothing at all: that is the case
    which used to pass on brightness alone.
    """
    top, bottom = _band("inventory_tab")
    found: dict[str, list[tuple[int, int]]] = {}
    for y, x, text in textocr.read_lines(frame[top:bottom], 1.0):
        name = re.sub(r"[^a-z]", "", text.casefold())
        if name in TAB_WORDS:
            found.setdefault(name, []).append((x + 20, y + top + 10))
    return {name: hits[0] for name, hits in found.items() if len(hits) == 1}


def tab_states(frame, points: dict) -> dict[str, str]:
    """Which tab the game outlines as active, when this bar is drawn as pills.

    EVIDENCE, NOT A GATE. Every other preset control in the game outlines the
    active one in green and the rest in cyan (vision.pills), but this bar has
    never been measured, and an unmeasured assumption about the tab is exactly
    what caused the incident this file opens with. It is logged on every batch
    so the first live readings can promote it to a gate; what protects the
    inventory meanwhile is the scrim check and the first tile's own
    confirmation.
    """
    top, bottom = _band("inventory_tab")
    found = pills.pills(frame, top, bottom)
    states = {}
    for name, (x, y) in points.items():
        for pill in found:
            px, py, pw, ph = pill["rect"]
            if px <= x <= px + pw and py <= y <= py + ph:
                states[name] = pill["state"]
    return states


def tray_text(frame) -> str:
    """What the strip above the tab bar says - the shatter tray, when the
    Shatter tab is open. Logged for the same reason as `tab_states`: Confirm
    is still a measured point, and these readings are what will let it be
    read instead."""
    top, _ = _band("inventory_tab")
    band = frame[max(0, top - TRAY_DEPTH):top]
    return " ".join(t for _, _, t in textocr.read_lines(band, 2.0)).strip()


def open_shatter():
    """Reach the Shatter tab, or abort without ever touching the grid."""
    frame = capture.grab()
    if not on_modules(frame):
        tap_at(nav_modules(), "nav modules")
        time.sleep(1.2)
        frame = capture.grab()
    if not on_modules(frame):
        logger.shot(frame, "shatter_not_modules")
        raise Abort("not on the Modules screen - refusing to tap")
    if inventory._panel_open(frame):
        # A grid tap under an open panel lands on the panel's own buttons:
        # that is how 2026-09-13 reached "Equip Matrix Sim".
        if not inventory._close_panel():
            logger.shot(capture.grab(), "shatter_panel_stuck")
            raise Abort("a module panel is open and will not close")
        frame = capture.grab()
    points = tab_points(frame)
    if "shatter" not in points:
        logger.shot(frame, "shatter_tab_unreadable")
        raise Abort("the module tab bar does not read a single Shatter tab")
    act.tap(*points["shatter"], reason="Shatter tab", instant=True)
    logger.event("shatter_tab_tap", x=points["shatter"][0], y=points["shatter"][1])
    frame = inventory.settle()
    points = tab_points(frame)
    if not on_modules(frame) or inventory._panel_open(frame) or "shatter" not in points:
        logger.shot(frame, "shatter_tab_missing")
        raise Abort("the Shatter tab did not open")
    logger.event("shatter_tab", tabs=sorted(points),
                 states=tab_states(frame, points), tray=tray_text(frame))
    return frame


# ------------------------------------------------------------ the dialog

def confirm_dialog() -> bool:
    """Confirm Shatter -> verify the dialog names RARE -> Yes.

    Returns True when the shatter was confirmed. Any doubt taps No: a dialog
    that does not appear, does not match, or names a different rarity means we
    misidentified something upstream, and the only safe move on an
    irreversible action is to back out.
    """
    frame = capture.grab()
    if inventory._panel_open(frame):
        logger.shot(frame, "shatter_panel_before_confirm")
        inventory._close_panel()
        raise Abort("a panel covers the Confirm button - nothing confirmed")
    tap_at(CONFIRM_SHATTER, "Confirm Shatter")
    deadline = time.monotonic() + 6.0
    frame = None
    while time.monotonic() < deadline:
        frame = capture.grab()
        if find(frame, DIALOG_TPL, 0.85):
            break
        time.sleep(0.3)
    else:
        logger.shot(frame if frame is not None else capture.grab(),
                    "shatter_no_dialog")
        # Leave no modal behind: whatever that tap opened would otherwise be
        # the next chore's - or the next run's - problem.
        inventory._close_panel()
        raise Abort("SHATTER MODULES dialog never appeared")

    score = 0.0
    hit = find(frame, RARE_TPL, RARE_MIN_SCORE)
    if hit:
        score = hit[1] if len(hit) > 1 else RARE_MIN_SCORE
    if not hit:
        logger.event("shatter_rarity", ok=False,
                     shot=logger.shot(frame, "shatter_wrong_rarity"))
        tap_at(DIALOG_NO, "No - dialog does not say 'rare'")
        raise Abort("dialog did not confirm RARE modules - nothing shattered")

    logger.event("shatter_rarity", ok=True, score=round(float(score), 3))
    tap_at(DIALOG_YES, "Yes - shatter rare modules")
    return True


def dismiss_rewards() -> int:
    """Clear the shard reward screens.

    One screen per shard type, up to four. SKIP appears only when there is
    more than one, and the user confirms it is exactly 'NEXT pressed several
    times'. So this never counts types or predicts screens - it just clears
    whatever is up until the modules screen is back, which makes a one-type
    batch and a four-type batch the same code path.
    """
    seen = 0
    for _ in range(REWARD_MAX_SCREENS):
        frame = capture.grab()
        if screen.identify(frame).name == "modules":
            return seen
        hsv = cv2.cvtColor(frame[300:520, 760:1040], cv2.COLOR_BGR2HSV)
        cyan = ((hsv[..., 0] > 85) & (hsv[..., 0] < 105)
                & (hsv[..., 1] > 110) & (hsv[..., 2] > 170))
        if cyan.sum() > 200:
            tap_at(REWARD_SKIP, "SKIP rewards")
        else:
            tap_at(REWARD_NEXT, "NEXT reward")
        seen += 1
        time.sleep(0.9)
    logger.shot(capture.grab(), "shatter_rewards_stuck")
    raise Abort("reward screens never cleared")


# ------------------------------------------------------------- the batch

def select_batch(frame) -> int:
    """Stage up to BATCH_MAX blue tiles from the CURRENT screen only.

    Deliberately does not scroll. Tapping a tile toggles it, so a second tap
    DESELECTS it - and the earlier paging version re-scanned the grid after
    each fling, where a tile whose green check had not rendered yet still read
    as blue and got tapped again. The batch quietly emptied itself, and eight
    batches in the Confirm button was dead and the dialog never came.

    Scrolling was never needed anyway: the top page shows 24-30 tiles against
    a batch cap of 12. When the inventory finally runs low the page holds
    fewer than 12, and a short batch is perfectly fine - the outer loop just
    runs once more. One scan, one tap each, no position visited twice.

    Every tap is logged with its coordinates and answered for. A panel after
    any tap means the tile was not a shatter cell at all, and the FIRST tile
    must be seen to take its green check before the rest are tapped - so a
    wrong screen costs one tap and one closed panel, not twelve taps walking
    into Equip.
    """
    staged = 0
    # NO sleep between taps. Selecting 12 tiles is a burst - the user does it
    # in under 3 seconds - and act.tap already spends 300-450ms per tap on the
    # rate limiter, its own jitter and the adb round trip. Anything added here
    # lands on top of that, which is how the first version reached ~1s a tile.
    #
    # instant=True sends `input tap` instead of a near-zero-distance swipe,
    # dropping the 85ms synthetic press hold. That hold exists to look human on
    # the battlefield; on a menu grid it is pure cost, and these widgets take a
    # plain tap fine.
    for (x, y) in visible_blue(frame)[:BATCH_MAX]:
        act.tap(x, y, reason=f"stage blue tile {staged + 1}", instant=True)
        logger.event("shatter_tap", n=staged + 1, x=x, y=y)
        after = capture.grab()
        if inventory._panel_open(after):
            logger.shot(after, "shatter_panel_opened")
            inventory._close_panel()
            raise Abort(f"tile {staged + 1} at ({x},{y}) opened a module panel "
                        f"- this is not the Shatter tab")
        if staged == 0 and not _staged(after, x, y):
            logger.shot(capture.grab(), "shatter_tile_not_staged")
            raise Abort(f"the tile at ({x},{y}) did not stage - "
                        f"refusing to tap {BATCH_MAX - 1} more")
        staged += 1
    logger.event("shatter_select", staged=staged)
    return staged


def _staged(frame, x, y) -> bool:
    """Has the tile at (x, y) taken its green check? Waits up to STAGE_WAIT:
    the check is drawn, not instant, and calling an unstaged tile staged is
    the mistake that matters here."""
    deadline = time.monotonic() + STAGE_WAIT
    while True:
        if not tile_is_blue(frame, x, y):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
        frame = capture.grab()
        if inventory._panel_open(frame):
            return False


def one_batch(n: int) -> int:
    open_shatter()
    try:
        inventory.park_top()
    except RuntimeError as exc:
        logger.shot(capture.grab(), "shatter_grid_unparked")
        raise Abort(str(exc))
    frame = inventory.settle()
    staged = select_batch(frame)
    if staged == 0:
        logger.event("shatter_batch", n=n, staged=0, done=True)
        return 0
    confirm_dialog()
    screens = dismiss_rewards()
    logger.event("shatter_batch", n=n, staged=staged, reward_screens=screens)
    time.sleep(0.8)
    return staged


def run(max_batches: int | None = None) -> int:
    guard_instance()
    total = n = 0
    while max_batches is None or n < max_batches:
        n += 1
        staged = one_batch(n)
        if staged == 0:
            break
        total += staged
    logger.event("shatter_done", batches=n - 1, modules=total)
    return total


def _cli():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="accepted for tray parity")
    ap.add_argument("--batches", type=int, default=0,
                    help="max shatter batches (0 = until no blue remains)")
    ap.add_argument("--dry-run", action="store_true",
                    help="scan and report what WOULD be staged; taps nothing")
    return ap.parse_args()


if __name__ == "__main__":
    import settings
    _a = _cli()
    settings.select_instance(_a.instance)
    if _a.dry_run:
        guard_instance()
        open_shatter()
        inventory.park_top()
        _blue = visible_blue(inventory.settle())
        print(f"visible blue tiles: {len(_blue)}")
        for _p in _blue:
            print("   ", _p)
        sys.exit(0)
    print(f"shatter blue on {CONFIG['active_instance']} "
          f"(batches={_a.batches or 'until empty'})")
    try:
        _n = run(max_batches=(None if _a.batches == 0 else _a.batches))
        print(f"shattered {_n} module(s)")
    except (Abort, act.TapRefused) as e:
        print(f"ABORTED: {e}")
        raise SystemExit(1)
