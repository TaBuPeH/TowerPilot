"""State detection: Intro Sprint, ability buttons, Second Wind floater, wall bar.

Icon templates (cropped from NATIVE captures) live under templates/:
  icons/intro_sprint.png      stopwatch-with-X indicator (top-left column)
  buttons/nuke.png            bomb glyph (box interior, state read from border)
  buttons/demon_mode.png      winged-hex glyph (boxed)
  floaters/second_wind.png    unboxed winged-hex (battlefield)
"""
from dataclasses import dataclass
import cv2
import numpy as np

from settings import ROOT
from device import capture

_TPL_CACHE: dict[str, np.ndarray] = {}
_MISSING: set[str] = set()


class TemplateMissing(RuntimeError):
    pass


def _tpl(rel: str) -> np.ndarray:
    if rel not in _TPL_CACHE:
        p = ROOT / "templates" / rel
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise TemplateMissing(f"missing template {p}")
        _TPL_CACHE[rel] = img
    return _TPL_CACHE[rel]


def _match(hay: np.ndarray, rel: str, threshold: float) -> tuple[bool, float, tuple[int, int]]:
    """Missing templates degrade to 'not found' (warned once) so the system
    can run partially calibrated during bring-up."""
    try:
        tpl = _tpl(rel)
    except TemplateMissing:
        if rel not in _MISSING:
            _MISSING.add(rel)
            from runtime import logger
            logger.event("template_missing", template=rel)
        return False, 0.0, (0, 0)
    res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return score >= threshold, float(score), loc


# Partial-occlusion matcher (2026-08-18). The whole-template CCOEFF score is
# an honest pixel comparator - and that is its weakness on this game: a
# floating bonus popup ("+9 Defense Ab...") crossing ONE tile drops a 0.997
# match to 0.937 and a strict-0.95 read fails. Measured on the Scout chip:
# grayscale / SQDIFF do nothing (the occluding pixels are real), edge
# matching is worse (0.77). Scoring the template as a 2x2 grid and averaging
# the best three cells recovers it (0.952) because three quarters of the tile
# were untouched. NOT a replacement for _match: locate with the fast whole-
# template pass, then re-score locally. Callers that read a small vocabulary
# of tiles under transient overlays (chips, cards, module icons) use this;
# HUD/dialog detectors keep the plain score - a dialog is never half-there.
def _match_robust(hay: np.ndarray, rel: str, threshold: float,
                  grid: int = 2, keep: int = 3
                  ) -> tuple[bool, float, tuple[int, int]]:
    hit, whole, loc = _match(hay, rel, 0.0)
    try:
        tpl = _tpl(rel)
    except TemplateMissing:
        return False, 0.0, (0, 0)
    if whole >= threshold:
        return True, whole, loc            # clean hit: no extra work
    th, tw = tpl.shape[:2]
    x0, y0 = loc
    if y0 + th > hay.shape[0] or x0 + tw > hay.shape[1]:
        return False, whole, loc
    cells = []
    for gy in range(grid):
        for gx in range(grid):
            ya, yb = gy * th // grid, (gy + 1) * th // grid
            xa, xb = gx * tw // grid, (gx + 1) * tw // grid
            cell = tpl[ya:yb, xa:xb]
            patch = hay[y0 + ya:y0 + yb, x0 + xa:x0 + xb]
            if cell.shape[:2] != patch.shape[:2] or min(cell.shape[:2]) < 8:
                continue
            r = cv2.matchTemplate(patch, cell, cv2.TM_CCOEFF_NORMED)
            cells.append(float(r.max()))
    if len(cells) < keep:
        return whole >= threshold, whole, loc
    cells.sort(reverse=True)
    partial = float(np.mean(cells[:keep]))
    score = max(whole, partial)
    return score >= threshold, score, loc


# The indicator lives in the left rail, which SLIDES as timed events start and
# end - so it is searched in a band, not a fixed ROI. The old tight ROI
# (10,240,110,110) sat ~90px above where the icon actually is on Main, so this
# returned False for every frame of every run: the autopilot could not tell it
# was in an Intro Sprint, never ended one, and therefore could never fire Nuke
# or Demon Mode (both are unusable while the sprint is running).
SPRINT_BAND = ((180, 760), (0, 150))


def find_intro_sprint(frame: np.ndarray) -> tuple[int, int] | None:
    """Centre of the Intro Sprint indicator, or None. Tapping it opens the
    END INTRO SPRINT EARLY dialog."""
    (y0, y1), (x0, x1) = SPRINT_BAND
    sub = frame[y0:y1, x0:x1]
    hit, _, loc = _match(sub, "icons/intro_sprint.png", 0.85)
    if not hit:
        return None
    tpl = _tpl("icons/intro_sprint.png")
    return (x0 + loc[0] + tpl.shape[1] // 2, y0 + loc[1] + tpl.shape[0] // 2)


def intro_sprint_active(frame: np.ndarray) -> bool:
    return find_intro_sprint(frame) is not None


# The Second Wind badge: a winged hexagon in a CYAN RING, sitting just above
# the ability row. The ring is a radial countdown arc that erodes as the window
# runs out, so the template is the GLYPH ONLY - including the ring would drop
# the match score exactly when the state matters most.
SW_BAND = ((1300, 1440), (10, 180))
# 0.85 was too tight and made the badge FLICKER: on a live wave-1060 run it
# dropped below the line on alternating frames, so a single 10s window logged
# as open/closed/open/closed every ~0.7s. Each spurious close re-armed the
# post-window wall watch and each spurious open cancelled it, and the run died
# at wave 1120 with Demon Mode never fired.
#
# Measured separation over 355 frames: 0.910-1.000 present, <=0.341 absent.
# 0.60 sits almost exactly between them - nearly 2x the noise floor, and far
# enough under the present-floor to ride out combat effects drawn over the
# glyph, which is what the flicker actually was.
SW_THRESH = 0.60


def second_wind_badge(frame: np.ndarray) -> tuple[bool, float]:
    """Is the Second Wind badge up? Returns (active, score).

    The badge is the circular winged hexagon just above the ability row - NOT
    the pink countdown in the wall bar, which is the wall's immunity clock and
    a shorter, separate thing (measured: badge up for 147 frames of a capture
    where wall_state read 'immunity' for only 32). The ability row's own
    Demon Mode button is the same winged hexagon in a ROUNDED BOX ~120px
    lower; SW_BAND excludes it.
    """
    (y0, y1), (x0, x1) = SW_BAND
    hit, score, _ = _match(frame[y0:y1, x0:x1], "floaters/second_wind.png",
                           SW_THRESH)
    return hit, score


def second_wind_floater(frame: np.ndarray) -> tuple[bool, tuple[int, int] | None]:
    """The floater MOVES on the field; search the field ROI."""
    hit, _, loc = _match(capture.roi(frame, "field"),
                         "floaters/second_wind.png", 0.70)
    return hit, (loc if hit else None)


@dataclass
class ButtonState:
    present: bool
    ready: bool       # bright border, tappable
    active: bool      # ring overlay (duration ring depleting)
    score: float
    center: tuple[int, int] | None  # native coords for tapping


def _border_stats(row_bgr: np.ndarray, loc: tuple[int, int],
                  tpl_shape: tuple[int, int]) -> tuple[float, float]:
    """Mean saturation/brightness of a thin border band around the matched box."""
    th, tw = tpl_shape[:2]
    x, y = loc
    pad = 8
    y0, y1 = max(0, y - pad), min(row_bgr.shape[0], y + th + pad)
    x0, x1 = max(0, x - pad), min(row_bgr.shape[1], x + tw + pad)
    region = row_bgr[y0:y1, x0:x1].copy()
    region[pad:pad + th, pad:pad + tw] = 0     # keep only the border band
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = hsv[..., 2] > 40
    if not mask.any():
        return 0.0, 0.0
    return float(hsv[..., 1][mask].mean()), float(hsv[..., 2][mask].mean())


def button_state(frame: np.ndarray, name: str) -> ButtonState:
    """name: 'nuke' | 'demon_mode'. State from glyph match + border band color."""
    row_box = capture.CONFIG["rois"]["ability_row"]
    row = capture.roi(frame, "ability_row")
    hit, score, loc = _match(row, f"buttons/{name}.png", 0.60)
    if not hit:
        return ButtonState(False, False, False, score, None)
    tpl = _tpl(f"buttons/{name}.png")
    sat, val = _border_stats(row, loc, tpl.shape)
    # A READY button has a bright, vividly colored border (measured on Main:
    # sat 116-171, val 119-218). The old rule also demanded "not active",
    # where active meant a saturated border - which is exactly what a ready
    # button looks like, so ready was always False and abilities never fired.
    # Brightness alone decides tappability; the caller CONFIRMS the tap landed
    # by re-reading the border afterwards (see orchestrator.fire_button).
    ready = val > 115
    active = sat > 110 and val > 100
    cx = row_box[0] + loc[0] + tpl.shape[1] // 2
    cy = row_box[1] + loc[1] + tpl.shape[0] // 2
    return ButtonState(True, ready, active, score, (cx, cy))


def button_border_val(frame: np.ndarray, name: str) -> float | None:
    """Border brightness of an ability button, or None if not found.
    Used to confirm a fire landed: firing dims the button (cooldown)."""
    row = capture.roi(frame, "ability_row")
    hit, _, loc = _match(row, f"buttons/{name}.png", 0.60)
    if not hit:
        return None
    _, val = _border_stats(row, loc, _tpl(f"buttons/{name}.png").shape)
    return val


_GEM_SCALE = 0.5      # search at HALF resolution
# 0.65 -> 0.55 (user, 2026-08-28): the v29 orbiting gem sits under constant
# particle spray, so occluded frames score low - the old threshold made
# detection intermittent (gem_seen fired, the fresh-detection re-check
# failed, every gem ended gem_lost). Measured with the harvested
# floaters/gem_v29_orbit.png: 0.864 on-gem, 0.305 noise floor elsewhere
# on the same frame - 0.55 keeps ~0.25 of margin.
_GEM_THRESH = 0.55
_GEM_SMALL: dict[str, np.ndarray] = {}


def _gem_tpl(rel: str) -> np.ndarray | None:
    """Half-size template, resized once and cached."""
    if rel not in _GEM_SMALL:
        try:
            full = _tpl(rel)
        except TemplateMissing:
            if rel not in _MISSING:
                _MISSING.add(rel)
                from runtime import logger
                logger.event("template_missing", template=rel)
            _GEM_SMALL[rel] = None
        else:
            _GEM_SMALL[rel] = cv2.resize(full, None, fx=_GEM_SCALE,
                                         fy=_GEM_SCALE,
                                         interpolation=cv2.INTER_AREA)
    return _GEM_SMALL[rel]


def floating_gem(frame: np.ndarray) -> tuple[int, int] | None:
    """Collectible gem: in-flight diamond OR settled '5 CLAIM' box.

    Floaters drift and SETTLE anywhere in the viewport - including on top of
    the ability-button row - so this searches the full 'field' ROI, never a
    fixed spot. That also means a FALSE positive could tap Nuke, so the
    threshold sits far above the measured noise floor (~0.36 on gem-free
    frames vs ~0.86 with a gem).

    The search runs at half resolution: full-res matching over the whole
    field cost ~95ms PER TEMPLATE and dominated the whole loop, while halving
    it is ~5x faster and still localizes the box to within a pixel. Camera
    zoom differs slightly between accounts, so each account contributes its
    own gem_*.png rather than relying on one template matching everywhere.
    """
    field_box = capture.CONFIG["rois"]["field"]
    hay = capture.roi(frame, "field")
    small = cv2.resize(hay, None, fx=_GEM_SCALE, fy=_GEM_SCALE,
                       interpolation=cv2.INTER_AREA)
    from settings import ROOT
    rels = ["buttons/gem_claim.png"]
    rels += [f"floaters/{p.name}"
             for p in sorted((ROOT / "templates" / "floaters").glob("gem_*.png"))]
    best_score, best_loc, best_shape = 0.0, None, None
    for rel in rels:
        tpl = _gem_tpl(rel)
        if tpl is None:
            continue
        if tpl.shape[0] >= small.shape[0] or tpl.shape[1] >= small.shape[1]:
            continue
        res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best_score:
            best_score, best_loc, best_shape = score, loc, tpl.shape
    if best_score < _GEM_THRESH or best_loc is None:
        return None
    cx = field_box[0] + int((best_loc[0] + best_shape[1] / 2) / _GEM_SCALE)
    cy = field_box[1] + int((best_loc[1] + best_shape[0] / 2) / _GEM_SCALE)
    return (cx, cy)


def death_screen(frame: np.ndarray) -> tuple[bool, tuple[int, int] | None]:
    """GAME STATS dialog after death. Returns (present, retry_button_center).

    TWO independent signals must agree. The RETRY button alone is NOT enough:
    it is a big rounded plaque and scores ~0.76 against upgrade-panel price
    boxes mid-battle (measured false positive at wave 2757), which cost a
    stray tap and a full run-state reset. The GAME STATS dialog header is the
    discriminator (1.00 on a real death, 0.28 on that false frame), and the
    RETRY threshold is raised to 0.90 - a real death matches at 1.00.
    """
    hit_g, _, _ = _match(frame, "icons/game_stats.png", 0.80)
    if not hit_g:
        return False, None
    hit, _, loc = _match(frame, "buttons/retry.png", 0.90)
    if not hit:
        return False, None
    tpl = _tpl("buttons/retry.png")
    return True, (loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)


def panel_tab(frame: np.ndarray) -> str:
    """Which bottom-panel tab is open, from the header strip color.

    'attack' (cyan/blue) | 'defense' (red/pink) | 'utility' (yellow) |
    'uw' (green ULTIMATE WEAPONS) | 'unknown'.
    Calibrate upgrade_panel ROI to INCLUDE the colored header strip at its top.
    """
    panel = capture.roi(frame, "upgrade_panel")
    header = panel[: max(20, panel.shape[0] // 12)]
    hsv = cv2.cvtColor(header, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 90) & (v > 90)
    if lit.mean() < 0.2:
        return "unknown"
    hues = h[lit]
    bands = {
        "attack": ((hues > 90) & (hues < 110)).mean(),   # cyan-blue
        "defense": ((hues < 10) | (hues > 165)).mean(),  # red-pink
        "utility": ((hues > 20) & (hues < 35)).mean(),   # yellow
        "uw": ((hues > 45) & (hues < 75)).mean(),        # green
    }
    best = max(bands, key=bands.get)
    return best if bands[best] > 0.4 else "unknown"


def buyable_upgrades(frame: np.ndarray) -> list[tuple[int, int]]:
    """Native tap points for buyable workshop boxes visible in the panel.

    Buyable = bright blue price sub-box with white text; maxed boxes are grey.
    Heuristic: HSV mask for the saturated blue fill, contour-filter to
    button-sized rectangles.
    """
    box = capture.CONFIG["rois"]["upgrade_panel"]
    panel = capture.roi(frame, "upgrade_panel")
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    # saturated medium-blue fill of an affordable price button
    mask = cv2.inRange(hsv, (95, 120, 120), (115, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    points = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 90 <= w <= 320 and 35 <= h <= 130:      # price-button sized
            points.append((box[0] + x + w // 2, box[1] + y + h // 2))
    return points


_EDGE_ROWS = 2      # rows sampled at the top and bottom of the bar interior
_GAP = 3            # dark columns needed to call the fill ended


def bar_fill(frame: np.ndarray, roi_name: str) -> float:
    """Bar fill as fraction of width (0.0-1.0), column-based.

    CALLER'S CONTRACT: this measures a bar, it does not check that one is
    there. On a menu or the death screen the ROI holds whatever happens to be
    at those pixels and the number is meaningless - only call it on a frame
    already confirmed to be in battle (a readable wave counter).

    Two things make the reading correct:

    * Only the TOP AND BOTTOM 2 ROWS of the interior are sampled. The bar
      carries its value as large white overlay text ("113.47T / 190.68T")
      which is low-saturation and so reads as unlit, punching holes through
      the middle of the fill; the edge rows are the only text-free ones.
      Measured on the wall bar: the middle band drops to 0.10-0.51 lit inside
      the filled region, while the edge rows are a clean 1.0.
    * The fill is the LEADING RUN from the left edge, not the rightmost lit
      column anywhere. The bar's own right-hand border glows enough to count
      as lit, so the old rightmost-column rule pinned every reading at 1.0 -
      a bar showing 113.47T of 190.68T (59%) came back as 1.00, and the 5%
      Demon Mode rescue could therefore never trigger.
    """
    bar = capture.roi(frame, roi_name)
    inset = 6
    core = bar[inset:-inset, inset:-inset]
    if core.size == 0:
        return 0.0
    e = min(_EDGE_ROWS, max(1, core.shape[0] // 4))
    rows = np.vstack([core[:e], core[-e:]])
    hsv = cv2.cvtColor(rows, cv2.COLOR_BGR2HSV)
    lit = (hsv[..., 1] > 90) & (hsv[..., 2] > 80)
    col = lit.mean(axis=0) >= 0.5
    n = col.size
    if not col.any():
        return 0.0
    # first place where _GAP consecutive columns are all dark ends the fill;
    # the tolerance rides over single-column dropouts at segment joins
    padded = np.concatenate([col, np.ones(_GAP - 1, bool)])
    win = np.lib.stride_tricks.sliding_window_view(padded, _GAP).any(axis=1)
    idx = np.flatnonzero(~win)
    return float((int(idx[0]) if idx.size else n) / n)


def wall_fill(frame: np.ndarray) -> float:
    return bar_fill(frame, "wall_bar")


def hp_fill(frame: np.ndarray) -> float:
    return bar_fill(frame, "hp_bar")


def wall_overheal(frame: np.ndarray) -> tuple[float, str]:
    """(overheal extent 0..1, state) of the wall bar, by COLUMN COLOR.

    The user's model of the bar (2026-08-15), confirmed by measurement on
    every available state: OVERHEAL is painted as a PURPLE run (hue 110-140)
    growing from the left edge, base wall health TEAL (75-105) to its right,
    RED (<=12 / >=165) mixed in while the wall is immune after a Second Wind,
    and a mostly-dark 'Rebuilding' banner when the wall is broken. So
    "overheal under 20%" is a COLOR TRANSITION AT A POSITION - the column at
    20% of the width is teal instead of purple - not a brightness fill.

    No text is read and none can pollute this: only the top/bottom EDGE ROWS
    are sampled (the value overlay lives in the middle band) and the border
    is cropped away. Measured extents: fresh T18 run 0.23, mid-run 0.60,
    immune 0.29-with-red, rebuilding 0.00.

    States: 'normal' | 'immune' | 'rebuilding'.
    """
    bar = capture.roi(frame, "wall_bar")
    h, w = bar.shape[:2]
    rows = np.r_[5:9, h - 9:h - 5]
    inner = bar[rows][:, 6:w - 6]
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    hh, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 60) & (v > 60)
    purple = lit & (hh >= 110) & (hh <= 140)
    teal = lit & (hh >= 75) & (hh <= 105)
    red = lit & ((hh <= 12) | (hh >= 165))
    if red.mean() > 0.10:
        state = "immune"
    elif (purple | teal).mean() < 0.10:
        state = "rebuilding"
    else:
        state = "normal"
    colp = purple.mean(axis=0) > 0.5
    if colp.any():
        extent = float((int(np.max(np.nonzero(colp))) + 1) / colp.size)
    else:
        extent = 0.0
    return extent, state


def wall_state(frame: np.ndarray) -> str:
    """'immunity' (pink countdown) | 'rebuilding' | 'normal' from bar color mix."""
    bar = capture.roi(frame, "wall_bar")
    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 60) & (v > 80)
    if not lit.any():
        return "normal"
    hues = h[lit]
    pink = ((hues < 15) | (hues > 160)).mean()       # salmon/pink band
    teal = ((hues > 75) & (hues < 105)).mean()       # teal/green band
    if pink > 0.5:
        return "immunity"
    if teal > 0.5:
        # normal HP bar and the "Rebuilding" bar are both teal-outlined;
        # rebuilding shows mostly-empty interior -> low overall lit fraction
        return "normal" if lit.mean() > 0.25 else "rebuilding"
    return "normal"


def side_menu_open(frame) -> bool:
    """In-run side menu open = the exit button visible at the menu bottom
    (EXIT BATTLE on low tiers, END ROUND on high tiers - both emulators run
    the same client, only the label differs). The toggle slot itself is
    unreliable (green X or wave-badge tile)."""
    for rel in ("buttons/exit_battle.png", "buttons/end_round.png"):
        hit, _, _ = _match(frame, rel, 0.75)
        if hit:
            return True
    return False
