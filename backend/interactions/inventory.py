"""Walk the module inventory and learn what we actually own.

Why this exists: loadout.apply_modules() refuses to run when a template is
missing, and four of the coin-farm modules had none. They could not be cut
blind - nothing in a screenshot says which tile is which. The game will tell
us, one tap at a time: tapping a tile opens a detail panel with the rarity and
the NAME, and that is ground truth.

Two crops come out of every tile, and the distinction is the whole point:

    NAME comes from the detail PANEL - the only place it is written.
    ICON comes from the GRID frame, at the tile we tapped.

Cutting the icon from the panel instead would be the obvious mistake. The
panel draws the art large and re-framed, while every template lookup in
tourney._find_in_band matches against GRID tiles - so a panel-cut template
would never match the thing it is supposed to find.

The grid is a 203px square lattice (measured, not assumed: lit-pixel column
and row projections put the five columns at 126..939 and the rows at 1082
with a 203px pitch). Tiles below the fold need one fling, and pages are
deduplicated by icon similarity rather than by counting - a fling that lands
short would otherwise silently rename every tile after it.
"""
import json
import os
import time

import cv2
import numpy as np

from device import act
from device import capture
from runtime import logger

COL_X = [126, 329, 533, 736, 939]
ROW_Y = [1082, 1285, 1488, 1691, 1894, 2097]
TILE = 150                       # icon box cut around a tile centre

# The close X is FOUND, not hardcoded. It rides the panel's top edge, which
# moves with the rarity line: y=578 for "ANCESTRAL **", y=616 for "EPIC+".
# A fixed (925,580) tap therefore closed some panels and missed others, and a
# miss left the panel up for the next tile tap. BACK is not an alternative -
# on this screen it does not dismiss the panel at all, it only dims it further.
# The strip has to be generous: the card is vertically centred and its height
# follows the number of effect rows, so a short panel (few effects) starts at
# y~755 where a tall one starts at y~526.
CLOSE_STRIP = (490, 950, 880, 975)     # y0,y1,x0,x1 - where the X can appear
CLOSE_FALLBACK = (928, 600)
# Rarity + name, cropped RELATIVE TO THE CLOSE X rather than at fixed y.
# Nothing about this panel is at a fixed height: the card is vertically centred
# and grows with its effect list, so its top ranges from y=526 (a long
# Ancestral) to y=755 (a short Rare), and the rarity line gains a stars row on
# top of that. The X tracks the card, so offsets from it hold for every variant.
HEAD_DY = (-30, 190)                   # above/below the X centre
HEAD_X = (395, 985)
# Detect the panel by the SCRIM, not by anything inside the panel.
#
# Two content-region probes were tried and both failed, for the same reason:
# THE PANEL LAYOUT MOVES. "ANCESTRAL **" carries a stars line that "EPIC+" does
# not, so the name, the multiplier line and even the close X sit ~38px apart
# between two rarities - a fixed flat-patch probe lands on empty card for one
# module and on body text for the next.
#
# The scrim does not move: opening any panel dims the whole screen behind it.
# Measured on the MODULES header, which no panel ever covers:
#     154.1 with no panel, 43.0 with either panel type.
# It also catches modals this routine did not open, which is what makes the
# "did it actually close?" check trustworthy.
SCRIM_PROBE = (110, 150, 20, 300)
SCRIM_DARK = 90.0

GRID_BAND = (1000, 2260)
PANEL_WAIT = 0.45
CLOSE_WAIT = 0.35


def _panel_open(frame=None) -> bool:
    """Is any modal up, by the dimming of the screen behind it?"""
    frame = frame if frame is not None else capture.grab()
    y0, y1, x0, x1 = SCRIM_PROBE
    return float(frame[y0:y1, x0:x1].mean()) < SCRIM_DARK


def _close_panel(tries: int = 4) -> bool:
    """Tap the X until the scrim clears. Returns False if it never does.

    Verifying this is not defensive padding - it is the bug that wrecked the
    first sweep. When a close silently failed, the NEXT tile tap landed inside
    the still-open panel, on effect rows and one row above the Level up and Max
    buttons. Nothing was spent that time. Continuing blind is not worth the
    second chance.
    """
    for _ in range(tries):
        frame = capture.grab()
        if not _panel_open(frame):
            return True
        act.tap(*(_find_close(frame) or CLOSE_FALLBACK),
                "close detail panel", instant=True)
        time.sleep(CLOSE_WAIT)
    return not _panel_open()


def _find_close(frame):
    """Centroid of the cyan X glyph, or None. Only meaningful with a panel up -
    the grid has plenty of cyan of its own."""
    y0, y1, x0, x1 = CLOSE_STRIP
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] > 80) & (hsv[..., 0] < 100) &
         (hsv[..., 1] > 90) & (hsv[..., 2] > 170))
    ys, xs = np.nonzero(m)
    if len(ys) < 50:
        return None
    return int(xs.mean()) + x0, int(ys.mean()) + y0


def _tile_icon(frame, cx, cy):
    h = TILE // 2
    return frame[cy - h:cy + h, cx - h:cx + h].copy()


def _blank_tile(icon) -> bool:
    """An empty slot past the end of the inventory: near-uniform dark."""
    return float(icon.std()) < 18.0


def _same_icon(a, b) -> bool:
    if a.shape != b.shape:
        return False
    return float(cv2.absdiff(a, b).mean()) < 6.0


def _fling(y0, y1, ms=180):
    act.swipe(538, y0, 538, y1, ms, reason="inventory page")
    time.sleep(0.7)


def sweep(out_dir: str) -> list:
    """Tap every inventory tile, record name + rarity + grid icon."""
    os.makedirs(out_dir, exist_ok=True)
    top, bottom = GRID_BAND
    # Never fling with a panel up: the drag goes to the panel's effect list
    # instead of the grid, so the grid does not move while the code believes it
    # has paged - which is how the last sweep ended up reading a bottom page
    # while labelling it page 0.
    if not _close_panel():
        raise RuntimeError("a modal is open and will not close - not sweeping")
    # park at the top: the sweep is positional, so it must know where row 0 is
    for _ in range(3):
        prev = capture.grab()
        _fling(top, bottom)
        if float(cv2.absdiff(prev[top:bottom],
                             capture.grab()[top:bottom]).mean()) < 1.0:
            break

    seen, records = [], []
    for page in range(3):
        grid = capture.grab()
        cv2.imwrite(f"{out_dir}/page{page}_grid.png", grid)
        new_on_page = 0
        for r, cy in enumerate(ROW_Y):
            for c, cx in enumerate(COL_X):
                icon = _tile_icon(grid, cx, cy)
                if _blank_tile(icon):
                    continue
                if any(_same_icon(icon, s) for s in seen):
                    continue
                seen.append(icon)
                new_on_page += 1
                idx = len(records)

                act.tap(cx, cy, f"inspect tile p{page}r{r}c{c}")
                panel = None
                for _ in range(8):                 # up to ~1.6s for the panel
                    time.sleep(PANEL_WAIT / 2)
                    frame = capture.grab()
                    if _panel_open(frame):
                        panel = frame
                        break
                if panel is None:
                    # Do not guess. A missed panel means the next crop would be
                    # grid pixels labelled as a name - worse than a gap.
                    logger.event("inventory_no_panel", page=page, r=r, c=c)
                    seen.pop()
                    continue

                xpt = _find_close(panel)
                if xpt is None:
                    logger.event("inventory_no_close", page=page, r=r, c=c)
                    seen.pop()
                    _close_panel()
                    continue
                head = panel[xpt[1] + HEAD_DY[0]:xpt[1] + HEAD_DY[1],
                             HEAD_X[0]:HEAD_X[1]]
                cv2.imwrite(f"{out_dir}/{idx:02d}_name.png", head)
                cv2.imwrite(f"{out_dir}/{idx:02d}_icon.png", icon)
                records.append({"idx": idx, "page": page, "row": r, "col": c,
                                "x": cx, "y": cy})

                if not _close_panel():
                    raise RuntimeError(
                        f"panel stuck open after tile p{page}r{r}c{c}")

        logger.event("inventory_page", page=page, new=new_on_page,
                     total=len(records))
        if new_on_page == 0:
            break
        _fling(bottom, top)

    with open(f"{out_dir}/index.json", "w") as fh:
        json.dump(records, fh, indent=1)
    return records


def contact_sheet(out_dir: str, records: list, per_row: int = 4):
    """One image pairing each icon with its name, for reading in bulk."""
    cells = []
    for rec in records:
        n = cv2.imread(f"{out_dir}/{rec['idx']:02d}_name.png")
        ic = cv2.imread(f"{out_dir}/{rec['idx']:02d}_icon.png")
        if n is None or ic is None:
            continue
        ic = cv2.resize(ic, (70, 70))
        n = cv2.resize(n, (int(n.shape[1] * 70 / n.shape[0]), 70))
        cells.append(np.hstack([ic, n]))
    width = max(c.shape[1] for c in cells)
    strips = []
    for i in range(0, len(cells), per_row):
        grp = cells[i:i + per_row]
        grp = [np.pad(c, ((0, 6), (0, width - c.shape[1]), (0, 0))) for c in grp]
        strips.append(np.hstack(grp) if False else np.vstack(grp))
    h = max(s.shape[0] for s in strips)
    strips = [np.pad(s, ((0, h - s.shape[0]), (0, 10), (0, 0))) for s in strips]
    sheet = np.hstack(strips)
    path = f"{out_dir}/sheet.png"
    cv2.imwrite(path, sheet)
    return path


if __name__ == "__main__":
    import argparse
    import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="main")
    ap.add_argument("--out", default="captures/inventory")
    a = ap.parse_args()
    settings.select_instance(a.instance)
    recs = sweep(a.out)
    print(f"{len(recs)} modules inspected")
    print(contact_sheet(a.out, recs))
