"""Fleet sighting by SHAPE + COLOUR (no templates, no shipped art).

A fleet is ONE big outlined pentagon of a random type (user, 2026-10-17/18):
saboteur red, commander orange, overcharge blue-violet. It arrives with a
swarm of pale Scatter triangles (same hue as overcharge, desaturated glow)
that do the killing - those are NOT the fleet and are not counted here. The
triangles that share a hue (vampire = saboteur red, scatter = overcharge) and
the red/orange/yellow squares (basic, tank, fast) are ordinary enemies; five
sides AND a large area is what makes a blob the fleet. Live T15 sightings
(2026-10-18) measured the fleet pentagon's outline at 1848-1882 px against
<= 582 for any ordinary pentagon.

Hues are the OpenCV hue (0-180) medians of the opaque saturated pixels of the
installed game's own sprites (measured 2026-10-17 from the local APK
extraction: saboteur 177, commander 15, overcharge 127). Nothing here is read
from, or written to, a template file.

`scan(frame)` -> {type: pentagon_count} over the field ROI, side menu
excluded. The Nuke gate in orchestrator.py decides what a count means.
"""
import cv2
import numpy as np

# type -> OpenCV hue of the sprite's coloured ring
HUES = {"saboteur": 177, "commander": 15, "overcharge": 127}
BLUE = "overcharge"

HUE_TOL = 6
MIN_SAT = 130
MIN_VAL = 200
# Mask area (px, native 1080x2560) of a fleet pentagon's coloured outline.
# Ordinary enemies measured 150-575 on a live T15 wave-1952 frame; fleet
# members are drawn bigger. Overridable per install: fleet.scan.min_area.
MIN_AREA = 700
# Contour must be this much of its convex hull (rejects a jagged glow blob).
MIN_SOLIDITY = 0.9
_EPS = 0.04


def _hue_distance(hue: np.ndarray, centre: int) -> np.ndarray:
    d = np.abs(hue.astype(np.int16) - centre)
    return np.minimum(d, 180 - d)


def is_pentagon(contour, min_area: float = MIN_AREA) -> bool:
    area = cv2.contourArea(contour)
    if area < min_area:
        return False
    approx = cv2.approxPolyDP(contour, _EPS * cv2.arcLength(contour, True), True)
    if len(approx) != 5 or not cv2.isContourConvex(approx):
        return False
    hull = cv2.contourArea(cv2.convexHull(contour))
    return hull > 0 and area / hull >= MIN_SOLIDITY


def scan(frame, field=None, exclude=None, min_area: float = MIN_AREA) -> dict[str, int]:
    """Count fleet-sized pentagons per enemy type.

    field   (x, y, w, h) playfield rect; default: the runtime layout's `field`.
    exclude (x0, y0, x1, y1) rect blanked before matching (the side menu's
            gold/white glyphs sit inside the field's top-right corner).
    """
    if field is None:
        from settings import CONFIG
        field = CONFIG["rois"]["field"]
    x, y, w, h = (int(v) for v in field)
    crop = frame[y:y + h, x:x + w]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    bright = (hsv[..., 1] > MIN_SAT) & (hsv[..., 2] > MIN_VAL)
    if exclude is not None:
        x0, y0, x1, y1 = exclude
        bright[max(0, y0 - y):max(0, y1 - y), max(0, x0 - x):max(0, x1 - x)] = False
    counts = {}
    for name, hue in HUES.items():
        mask = ((_hue_distance(hsv[..., 0], hue) <= HUE_TOL) & bright).astype(np.uint8) * 255
        found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        counts[name] = sum(1 for c in found if is_pentagon(c, min_area))
    return counts
