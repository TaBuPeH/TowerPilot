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
"""
import argparse
import random
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

from interactions import tourney
from interactions.tourney import Abort, find, tap_at

# --- this runs on ONE account -------------------------------------------
ALLOWED_INSTANCE = "main"       # the user's rule: blue-shatter is a main-only
                                # chore. Enforced, not left as a convention -
                                # the other accounts keep their rares.

# --- chrome measured off the recording ----------------------------------
NAV_MODULES = (630, 2470)       # bottom nav, the diamond icon
SHATTER_TAB = (682, 923)        # Inventory | Merge | [Shatter] | Assist
CONFIRM_SHATTER = (538, 799)    # button spans x 311-768, y 753-849
DIALOG_YES = (727, 1514)
DIALOG_NO = (351, 1514)
REWARD_NEXT = (540, 1965)       # magenta button, measured x 275-806 y 1877-2056
REWARD_SKIP = (898, 400)        # cyan button, top right; only when >1 screen

# Grid geometry: 5 columns, ~200px row pitch, first row centred at y=1088.
GRID_COLS = (154, 346, 544, 740, 938)
GRID_TOP = 1088
GRID_PITCH = 200
GRID_BOTTOM = 2270              # below this the filter bar starts
SCROLL_BAND = (1200, 2200)      # swipe endpoints for paging the grid
# The user does not drag the list - they FLING it, and one fling crosses the
# whole grid either way. Measured off their own four drags in the recording:
#   to bottom  (703,2119)->(341,128)  2041px/196ms   10413 px/s
#              (434,1898)->(410, 28)  1888px/168ms   11268 px/s
#   to top     (398,1406)->(395,2752) 1384px/194ms    7138 px/s
#              (472,1431)->(257,2496) 1120px/138ms    8089 px/s
# so ~1900px in ~180ms. Their endpoints run off-screen (y=28, y=2752) because
# a real finger leaves the panel; ours stay just inside the bounds check.
FLING_MS = 180
FLING_TOP = (538, 700, 538, 2400)      # downward finger -> list goes to row 1
FLING_BOTTOM = (538, 2400, 538, 700)   # upward finger  -> list goes to the end

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

    Selected tiles carry a big green check (hue ~60) and so fail the blue
    test, which makes a re-scan naturally idempotent: already-staged tiles are
    not counted twice. Equipped tiles are locked by the game AND fail the test.
    """
    out = []
    y = GRID_TOP
    while y <= GRID_BOTTOM:
        for x in GRID_COLS:
            if tile_is_blue(frame, x, y):
                out.append((x, y))
        y += GRID_PITCH
    return out


_at_top = False                 # do we KNOW the grid is parked at row 1?


def fling(to_top: bool, reason: str):
    x0, y0, x1, y1 = FLING_TOP if to_top else FLING_BOTTOM
    act.swipe(x0, y0, x1, y1, FLING_MS, reason=reason)
    time.sleep(0.5)                 # let the momentum settle before reading


def scroll_grid(down: bool = True):
    global _at_top
    fling(to_top=not down, reason="page module grid")
    if down:
        _at_top = False


def ensure_top(max_swipes: int = 3):
    """Park the grid at row 1, WITHOUT re-dragging when it is already there.

    tourney._scroll_to_top swipes a fixed four times every call. That is right
    for a routine that visits a list once, but this one returns to the grid
    after every batch and is almost always still at the top - so it was paying
    four drags to discover nothing had moved.

    Two things stop that. The position is remembered across calls (only a
    downward page invalidates it), and even on a cold start it swipes until
    the grid STOPS CHANGING rather than a fixed count - so an already-parked
    grid costs one swipe to confirm, not four.
    """
    global _at_top
    if _at_top:
        logger.event("shatter_scroll", already_top=True, swipes=0)
        return capture.grab()
    before = capture.grab()
    for i in range(max_swipes):
        fling(to_top=True, reason="fling module grid to top")
        after = capture.grab()
        if np.array_equal(before[GRID_TOP:GRID_BOTTOM],
                          after[GRID_TOP:GRID_BOTTOM]):
            _at_top = True
            logger.event("shatter_scroll", already_top=False, swipes=i + 1)
            return after
        before = after
    _at_top = True
    logger.event("shatter_scroll", already_top=False, swipes=max_swipes)
    return before


# ------------------------------------------------------------ the screens

def on_shatter_tab(frame) -> bool:
    """Modules screen with the Shatter tab open.

    Checked by the screen classifier plus the Confirm button, rather than by
    remembering that we tapped the tab - a tap that silently missed would
    otherwise have us selecting tiles on the Inventory tab, where tapping a
    module opens it instead of staging it.
    """
    if screen.identify(frame).name != "modules":
        return False
    band = frame[753:849, 311:768]
    return band.size > 0 and band.mean() > 18


def open_shatter():
    frame = capture.grab()
    if not on_shatter_tab(frame):
        if screen.identify(frame).name != "modules":
            tap_at(NAV_MODULES, "nav modules")
            time.sleep(1.2)
        tap_at(SHATTER_TAB, "Shatter tab")
        time.sleep(1.0)
    frame = capture.grab()
    if not on_shatter_tab(frame):
        logger.shot(frame, "shatter_tab_missing")
        raise Abort("could not reach the Shatter tab")
    return frame


def staged_count_visible(frame) -> bool:
    """Is anything staged? The empty tray shows 'Select modules to shatter'
    and a dimmed Confirm button; a loaded one shows the shard totals."""
    band = frame[753:849, 311:768]
    return band.size > 0 and band.mean() > 30


# ------------------------------------------------------------ the dialog

def confirm_dialog() -> bool:
    """Confirm Shatter -> verify the dialog names RARE -> Yes.

    Returns True when the shatter was confirmed. Any doubt taps No: a dialog
    that does not appear, does not match, or names a different rarity means we
    misidentified something upstream, and the only safe move on an
    irreversible action is to back out.
    """
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
        staged += 1
    logger.event("shatter_select", staged=staged)
    return staged


def one_batch(n: int) -> int:
    frame = open_shatter()
    frame = ensure_top()
    staged = select_batch(frame)
    if staged == 0:
        logger.event("shatter_batch", n=n, staged=0, done=True)
        return 0
    frame = capture.grab()
    if not staged_count_visible(frame):
        logger.shot(frame, "shatter_nothing_staged")
        raise Abort(f"tapped {staged} tiles but nothing is staged")
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
        _f = open_shatter()
        _f = ensure_top()
        _blue = visible_blue(_f)
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
