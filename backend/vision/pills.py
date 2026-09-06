"""Structural detection of the game's outlined controls - no templates needed.

Every preset control in The Tower is drawn the same way: a rounded "pill"
with a CYAN outline (inactive) or a GREEN outline (active) and a white label
inside, on a dark fill. Card preset tabs, the per-category preset tab rows
(modules / guardians / workshop / bots) and the rows of the global preset
picker all share it. The labels are the player's own text, so their
templates can only come from the player's own screen - this module finds
the pills without knowing what they say, so the calibrator can cut and
name them (player/calibrate.py).

The trick that makes it robust (measured on BlueStacks frames 2026-09-06):
the outline is a closed bright loop, so the dark INTERIOR of every pill is
a connected component of its own, isolated from the dark background outside.
Looking for the interiors instead of the outlines is immune to the glow that
bridges neighbouring pills and fooled a contour-of-the-outline approach.

Also here: the fixed module header slot positions with a ring-occupancy
test, and the inventory grid's tile rows read from pixel projections (the
lattice shifts by a row when the "New" badges are shown).
"""
import cv2
import numpy as np

CYAN = (90, 108)        # OpenCV hue range of the inactive outline (peaks 99-101)
GREEN = (38, 60)        # active outline (peaks 47-48)

# Module header: four large (primary) and four small (assist) slots around
# the tower diagram, fixed positions at native 1080x2560 (2026-09-06).
HEADER_LARGE = ((307, 518), (307, 755), (768, 518), (768, 755))
HEADER_SMALL = ((115, 518), (115, 755), (954, 518), (954, 755))
HEADER_RADIUS = {"large": 86, "small": 64}
RING_OCCUPIED = 0.05    # lit fraction on the ring: empty grey slots read 0.00

# Where each preset tab row / the picker's rows sit at native 1080x2560 -
# generous bands, measured 2026-09-06 on BlueStacks; the pills are found
# inside them, nothing is assumed about their count or labels.
TAB_BANDS = {"cards": (330, 560), "modules": (150, 340), "guardians": (330, 520),
             "bots": (440, 640), "workshop": (140, 330), "picker": (1380, 1760)}

# The inventory grid is visible between the Inventory/Merge tab bar and the
# "All Types" filter bar (measured 2026-09-06). A tile row is only usable
# when its whole frame (TILE_HALF above and below the centre) lies between
# them: a row half behind either bar reads and cuts a truncated tile (the
# bottom-row cuts scored 0.73 against the same tile seen whole one page on).
GRID_CLEAR = (1080, 2262)
TILE_HALF = 90


def _hsv(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return hsv[..., 0], hsv[..., 1], hsv[..., 2]


def pills(frame, y0: int, y1: int, min_w: int = 90, max_w: int = 1000,
          min_h: int = 40, max_h: int = 160) -> list[dict]:
    """Outlined pills whose interior lies inside the band [y0, y1).

    Returns one dict per pill in reading order: `rect` (x, y, w, h) of the
    interior in FULL-FRAME coordinates, `state` ("green" = active, "cyan" =
    inactive) and the outline colour fractions that decided it.
    """
    band = frame[y0:y1]
    if band.size == 0:
        return []
    h, s, v = _hsv(band)
    bright = ((v > 110) & (s > 50)) | (v > 180)
    dark = (~bright).astype(np.uint8)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=4)
    lit = (s > 90) & (v > 120)
    cyan_m = (h > CYAN[0]) & (h < CYAN[1]) & lit
    green_m = (h > GREEN[0]) & (h < GREEN[1]) & lit
    out = []
    for i in range(1, n):
        x, y, w, hh, area = (int(t) for t in stats[i])
        if not (min_w <= w <= max_w and min_h <= hh <= max_h):
            continue
        if y == 0 or y + hh >= band.shape[0]:
            continue                        # touches the band edge: background
        if area < 0.55 * w * hh:
            continue                        # not a filled rectangle
        ring = np.zeros(band.shape[:2], np.uint8)
        cv2.rectangle(ring, (x - 12, y - 12), (x + w + 12, y + hh + 12), 255, -1)
        cv2.rectangle(ring, (x, y), (x + w, y + hh), 0, -1)
        rz = ring > 0
        cyan = float(cyan_m[rz].mean())
        green = float(green_m[rz].mean())
        if cyan + green < 0.06:
            continue                        # a dark box with no outline
        out.append({"rect": (x, y + y0, w, hh),
                    "state": "green" if green > cyan else "cyan",
                    "cyan": round(cyan, 3), "green": round(green, 3)})
    out.sort(key=lambda p: (p["rect"][1] // 40, p["rect"][0]))
    return out


def rows_of(ps: list[dict], tol: int = 40) -> list[list[dict]]:
    """Group pills into horizontal rows (top to bottom, left to right)."""
    rows: list[list[dict]] = []
    for p in sorted(ps, key=lambda p: (p["rect"][1], p["rect"][0])):
        for row in rows:
            if abs(row[0]["rect"][1] - p["rect"][1]) <= tol:
                row.append(p)
                break
        else:
            rows.append([p])
    for row in rows:
        row.sort(key=lambda p: p["rect"][0])
    return rows


def text_crop(frame, rect, margin: int = 8):
    """The white label inside a pill with a small margin - the template.

    Cut around the TEXT, not the pill: the fill differs between the active
    and inactive states (black vs navy) while the label does not, and a
    text-only cut matched its other state at 0.97-0.99 where a full-pill cut
    dropped to 0.75 (2026-09-06). Returns (crop, rect) or (None, None).
    """
    x, y, w, h = rect
    box = frame[y:y + h, x:x + w]
    if box.size == 0:
        return None, None
    _h, s, v = _hsv(box)
    white = (s < 70) & (v > 190)
    ys, xs = np.nonzero(white)
    if len(xs) < 20:
        return None, None
    x0, x1 = max(0, int(xs.min()) - margin), min(w, int(xs.max()) + margin + 1)
    y0, y1 = max(0, int(ys.min()) - margin), min(h, int(ys.max()) + margin + 1)
    return box[y0:y1, x0:x1].copy(), (x + x0, y + y0, x1 - x0, y1 - y0)


def match(frame, tpl):
    """(best score, centre, next-best score away from the best) of `tpl` in
    `frame` - the calibrator's self-match / distinctiveness check."""
    if tpl.shape[0] > frame.shape[0] or tpl.shape[1] > frame.shape[1]:
        return 0.0, (0, 0), 0.0
    r = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    _, best, _, loc = cv2.minMaxLoc(r)
    th, tw = tpl.shape[:2]
    r2 = r.copy()
    cv2.rectangle(r2, (max(0, loc[0] - tw // 2), max(0, loc[1] - th // 2)),
                  (loc[0] + tw // 2, loc[1] + th // 2), -1.0, -1)
    _, second, _, _ = cv2.minMaxLoc(r2)
    return round(float(best), 3), (loc[0] + tw // 2, loc[1] + th // 2), round(float(second), 3)


def header_slots(frame) -> list[dict]:
    """The eight module header slots with an occupancy reading each.

    Occupancy is the lit fraction on a thin ring at the slot's radius: an
    equipped module draws a rarity-coloured ring there (0.10-0.47 measured),
    an empty slot is flat grey (0.00). `half` is the icon crop half-size
    that stays inside the ring and above the level text.
    """
    _h, s, v = _hsv(frame)
    lit = (s > 90) & (v > 120)
    out = []
    for kind, pts in (("large", HEADER_LARGE), ("small", HEADER_SMALL)):
        rad = HEADER_RADIUS[kind]
        for (cx, cy) in pts:
            ring = np.zeros(frame.shape[:2], np.uint8)
            cv2.circle(ring, (cx, cy), rad, 255, 10)
            occ = float(lit[ring > 0].mean()) if (ring > 0).any() else 0.0
            out.append({"centre": (cx, cy), "kind": kind, "ring": round(occ, 3),
                        "occupied": occ >= RING_OCCUPIED, "half": int(rad * 0.62)})
    return out


def grid_row_spans(frame, y0: int = 1000, y1: int = 2300, x0: int = 40, x1: int = 1040,
                   min_lit: int = 40) -> list[tuple[int, int]]:
    """(start, end) of every run of rows with lit pixels in the grid band,
    full-frame y. The Inventory tab bar is the first run (it is lit); a
    tile row cut off by it fuses with it - that is what inventory.at_top
    reads (measured 2026-09-06: (1000,1080)+(1106,1290) parked at the top,
    one (1000,1205) run when scrolled)."""
    _h, s, v = _hsv(frame[y0:y1, x0:x1])
    lit = ((s > 90) & (v > 120)).sum(axis=1)
    spans, start = [], None
    for i, val in enumerate(lit):
        if val > min_lit and start is None:
            start = i
        elif val <= min_lit and start is not None:
            spans.append((start + y0, i + y0))
            start = None
    if start is not None:
        spans.append((start + y0, y1))
    return spans


def grid_rows(frame, y0: int = 1000, y1: int = 2300, x0: int = 40, x1: int = 1040,
              min_run: int = 100, min_lit: int = 40) -> list[int]:
    """Centres of the WHOLE tile rows: runs of rows with lit pixels whose
    tile frame clears both bars (GRID_CLEAR). Rows cut by a bar are left to
    the next page, which overlaps this one by two rows."""
    lo, hi = GRID_CLEAR
    return [(a + b) // 2 for a, b in grid_row_spans(frame, y0, y1, x0, x1, min_lit)
            if b - a > min_run and (a + b) // 2 - TILE_HALF >= lo
            and (a + b) // 2 + TILE_HALF <= hi]
