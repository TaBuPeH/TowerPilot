"""Read an orbit gem using its square and the same frame's HUD diamond.

No stored artwork, template writes, device I/O or absent/reward inference.
The caller establishes battle ownership and decides whether input is permitted.
"""
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from player.bootstrap_layout import manifest


@dataclass(frozen=True)
class OrbitGem:
    center: tuple[float, float]
    rect: tuple[int, int, int, int]
    score: float


# Frames the sensing tally remembers one by one. Old frames fall out, so one recognition ends a suspicion
# and a quiet stretch does not keep an old one alive for ever.
RECENT_FRAMES = 120


@dataclass
class Glance:
    """What one search saw, for a caller that counts searches. find_orbit_gem fills it; its answer is unchanged.

    reason: found, ambiguous (two or more squares passed), glyph_rejected (a square with the gem's pink border
    passed every shape check but not the glyph check), no_candidate, hud_glyph (the HUD diamond could not be
    read, so no square was judged) or frame_shape (not the layout's size). near_misses counts the squares that
    reached the glyph check and failed it; best_near_miss is the best glyph score among them (None when none
    could be scored)."""
    reason: str = 'not_searched'
    near_misses: int = 0
    best_near_miss: float | None = None


class OrbitSensing:
    """The tally of the idle searches one tapper ran: how many frames, how many recognized, how many were near misses.

    A frame is a near miss when nothing was recognized but a square got as far as the glyph check. A recognizer
    that stopped matching shows as frames and near misses that rise while recognized stays 0; a clean empty
    battle shows frames only. `recent` is the same count over the last RECENT_FRAMES frames."""

    def __init__(self, recent=RECENT_FRAMES):
        self.frames = self.recognized = self.near_miss_frames = 0
        self.reasons: dict[str, int] = {}
        self.best_near_miss: float | None = None
        self._recent = deque(maxlen=recent)

    def record(self, glance: Glance, recognized: bool) -> None:
        reason = 'found' if recognized else glance.reason
        near_miss = not recognized and glance.near_misses > 0
        self.frames += 1
        self.recognized += recognized
        self.near_miss_frames += near_miss
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        if near_miss and glance.best_near_miss is not None:
            self.best_near_miss = glance.best_near_miss if self.best_near_miss is None else max(self.best_near_miss, glance.best_near_miss)
        self._recent.append('recognized' if recognized else 'near_miss' if near_miss else 'quiet')

    def snapshot(self) -> dict | None:
        """The counts as published, or None before any frame (nothing sensed is unknown, never zero)."""
        if not self.frames:
            return None
        return dict(frames=self.frames, recognized=self.recognized, near_miss_frames=self.near_miss_frames,
                    reasons=dict(sorted(self.reasons.items())), best_near_miss=self.best_near_miss,
                    recent=dict(window=self._recent.maxlen, frames=len(self._recent),
                                recognized=self._recent.count('recognized'), near_miss_frames=self._recent.count('near_miss')))


def reference_geometry() -> tuple[tuple[float, float], float]:
    """Read the current display's reference geometry, without caching it."""
    region = manifest()['interface']['screens']['battle']['orbiting_gem']
    return tuple(region['reference_center']), float(region['reference_radius_pixels'])


def field_region(layout_offset: int = 0) -> tuple[int, int, int, int]:
    """The manifest's battlefield, extended when the upgrade panel closes."""
    geometry = manifest()
    x, y, w, h = geometry['runtime_layout']['rois']['field']
    return x, y, w, min(geometry['layout']['height']-y, h+layout_offset)


def _hud_glyph(frame: np.ndarray, rect: list[int]) -> np.ndarray | None:
    x, y, w, h = rect
    crop = frame[y:y+h, x:x+w]
    mask = cv2.inRange(crop, (200, 200, 200), (255, 255, 255))
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return None
    outer = [i for i, row in enumerate(hierarchy[0]) if row[3] == -1]
    if len(outer) != 1:
        return None
    index = outer[0]
    contour = contours[index]
    gx, gy, gw, gh = cv2.boundingRect(contour)
    if not (0 < gx and 0 < gy and gx+gw < w and gy+gh < h
            and .6*w <= gw and .55*h <= gh):
        return None
    polygon = cv2.approxPolyDP(contour, .025*cv2.arcLength(contour, True), True)
    # A plain triangle, filled diamond or clipped icon cannot teach a matcher.
    holes = [i for i, row in enumerate(hierarchy[0]) if row[3] == index
             and cv2.contourArea(contours[i]) >= .03*gw*gh]
    if len(polygon) != 5 or len(holes) != 5:
        return None
    return mask[gy:gy+gh, gx:gx+gw]


def _glyph_scores(inner: np.ndarray, glyph: np.ndarray) -> list[float] | None:
    """Fit the validated glyph inside the square without resizing battle art.

    Taking the bounding box of every bright pixel stretches unrelated debris
    into the glyph. Search position and scale within the bounded interior;
    retain the caller's score and orientation-margin requirements.
    """
    if min(inner.shape) < 35 or not np.any(inner):
        return None
    scores = []
    for rotation in range(4):
        template = np.rot90(glyph, rotation).copy()
        best = -1.
        for width in range(35, inner.shape[1]+1, 2):
            height = round(width*template.shape[0]/template.shape[1])
            if not 35 <= height <= inner.shape[0]:
                continue
            scaled = cv2.resize(template, (width, height))
            score = float(cv2.matchTemplate(inner, scaled, cv2.TM_CCOEFF_NORMED).max())
            best = max(best, score)
        scores.append(best)
    return sorted(scores, reverse=True)


def find_orbit_gem(frame: np.ndarray, *, layout_offset: int = 0, glance: Glance | None = None) -> OrbitGem | None:
    """Return one verified visible gem; None means not established, not absent.

    Require a square white border with pink pixels and a faceted inner glyph
    matching the independently validated HUD diamond. Rectification handles
    rotation; duplicate inner/outer border contours count as one object.
    `glance`, when given, is filled with why the answer is what it is (see Glance); it never changes the answer.
    """
    seen = glance if glance is not None else Glance()
    seen.reason, seen.near_misses, seen.best_near_miss = 'not_searched', 0, None
    geometry = manifest()
    layout = geometry['layout']
    if getattr(frame, 'shape', None) != (layout['height'], layout['width'], 3):
        seen.reason = 'frame_shape'
        return None
    region = geometry['interface']['screens']['battle']['orbiting_gem']
    glyph = _hud_glyph(frame, region['hud_icon_rect'])
    if glyph is None:
        seen.reason = 'hud_glyph'
        return None
    fx, fy, fw, fh = field_region(layout_offset)
    field = frame[fy:fy+fh, fx:fx+fw]
    white = cv2.inRange(field, (180, 180, 180), (255, 255, 255))
    contours, _ = cv2.findContours(white, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    glyph_width = glyph.shape[1]
    same_square = .3*glyph_width        # centres closer than this are the inner and outer border of one square
    found: list[OrbitGem] = []
    missed: list[np.ndarray] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (1.1*glyph_width <= w <= 3.3*glyph_width
                and 1.1*glyph_width <= h <= 3.3*glyph_width and .65 <= w/h <= 1.55):
            continue
        quad = cv2.approxPolyDP(contour, .025*cv2.arcLength(contour, True), True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        points = quad.reshape(4, 2).astype(np.float32)
        vectors = np.roll(points, -1, axis=0)-points
        sides = np.linalg.norm(vectors, axis=1)
        if sides.min() == 0 or sides.max()/sides.min() > 1.15:
            continue
        # The bounded box above already rejects tiny candidates. Requiring
        # another minimum edge/HUD ratio rejected smaller field gems whose
        # pink square and faceted glyph pass the unchanged checks below.
        if sides.mean() > 2.4*glyph_width:
            continue
        cosines = np.sum(vectors*np.roll(vectors, -1, axis=0), axis=1)/(sides*np.roll(sides, -1))
        if np.max(np.abs(cosines)) > .18:
            continue
        transform = cv2.getPerspectiveTransform(points, np.float32([[0, 0], [99, 0], [99, 99], [0, 99]]))
        warped = cv2.warpPerspective(white, transform, (100, 100), flags=cv2.INTER_NEAREST)
        color = cv2.warpPerspective(field, transform, (100, 100)).astype(np.int16)
        b, g, r = cv2.split(color)
        pink = (b-g > 15) & (r-g > 15) & (r > 180) & (b > 180)
        interior = slice(8, 92)
        border = np.ones((100, 100), bool)
        border[interior, interior] = False
        if np.count_nonzero(pink & border) < 40:
            continue
        scores = _glyph_scores(warped[interior, interior], glyph)
        center = points.mean(axis=0)+np.array([fx, fy])
        if scores is None or scores[0] < .8 or scores[0]-scores[1] < .1:
            # Shaped and bordered like a gem; only the glyph said no. The inner and outer border contours of one
            # square count once, as they do for a gem that is found.
            if all(np.linalg.norm(center-old) >= same_square for old in missed):
                missed.append(center)
                seen.near_misses += 1
            if scores is not None:
                seen.best_near_miss = round(scores[0], 3) if seen.best_near_miss is None else max(seen.best_near_miss, round(scores[0], 3))
            continue
        gem = OrbitGem((float(center[0]), float(center[1])), (x+fx, y+fy, w, h), scores[0])
        duplicate = next((old for old in found if np.linalg.norm(center-np.array(old.center)) < same_square), None)
        if duplicate is None:
            found.append(gem)
        elif gem.score > duplicate.score:
            found[found.index(duplicate)] = gem
    seen.reason = ('found' if len(found) == 1 else 'ambiguous' if found
                   else 'glyph_rejected' if seen.near_misses else 'no_candidate')
    return found[0] if len(found) == 1 else None
