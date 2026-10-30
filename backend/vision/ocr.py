"""Value OCR for HUD amounts ("$ 2.30M" cash, "$ 45K" price boxes).

Glyph templates in templates/valuefont/ (harvested auto-labeled from known
amounts); digits missing there fall back to templates/digits/ (same font
family; classifier resizes per comparison, so size differences are fine).
"""
import cv2
import numpy as np
from pathlib import Path

import settings
from settings import ROOT

# The Tower's measure units, in game order (Settings > notation):
# K, M, B, T, q, Q, s, S, O, N, d, D  (case-sensitive: q=quadrillion, Q=quintillion)
_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "q": 1e15, "Q": 1e18,
           "s": 1e21, "S": 1e24, "O": 1e27, "N": 1e30, "d": 1e33, "D": 1e36}
# font -> template dirs, searched in order (first hit per glyph wins)
_FONT_DIRS = {
    "value": ["valuefont", "digits"],
    "store": ["storefont", "valuefont", "digits"],   # guild store UI font
}
_glyph_cache: dict[str, dict[str, np.ndarray]] = {}

# Native 1080x2560 battle HUD: excludes the diamond icon and neighboring rows.
GEM_BALANCE_REGION = (90, 190, 240, 60)
# Native 1080x2560 Home top bar, measured on captured Home frames (966, 972,
# 993, 2522): the digits start 13 px inside the region, right of the diamond
# icon (x 382-428), and stay left of the stones icon (x >= 668).
HOME_GEM_BALANCE_REGION = (434, 18, 226, 60)


def gem_balance_crop(frame: np.ndarray, region=None) -> np.ndarray | None:
    """The exact counter pixels used by the reader and failure diagnostics."""
    if getattr(frame, 'shape', None) != (2560, 1080, 3):
        return None
    x, y, w, h = GEM_BALANCE_REGION if region is None else region
    return frame[y:y+h, x:x+w]


def read_gem_balance(frame: np.ndarray, region=None) -> int | None:
    """Strict integer-only counter read; unknown/cropped glyphs fail closed.

    Uses existing account-local digit templates, never captures new templates.
    Callers must independently establish the screen `region` belongs to: the
    battle HUD by default, `HOME_GEM_BALANCE_REGION` on the exact Home screen.
    Only a contiguous, baseline-aligned number belongs to the HUD. Detached
    battle art clipped at the lower crop edge may follow a clear gutter;
    overlapping components must also be too wide for a digit in this font.
    """
    crop = gem_balance_crop(frame, region)
    if crop is None:
        return None
    h, w = crop.shape[:2]
    glyphs = {ch: tpl for ch, tpl in _load().items() if len(ch) == 1 and ch.isdigit()}
    if len(glyphs) != 10:
        return None
    bw = cv2.inRange(crop, (180, 180, 180), (255, 255, 255))
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = sorted((cv2.boundingRect(c) for c in contours), key=lambda b: b[0])
    if not boxes or not 0 < boxes[0][0] <= 25:
        return None
    _, top, _, height = boxes[0]
    bottom = top + height
    baseline_tolerance = 3
    max_digit_width = (height+baseline_tolerance)*max(
        tpl.shape[1]/tpl.shape[0] for tpl in glyphs.values())+2*baseline_tolerance
    digits = []
    right = None
    for index, (gx, gy, gw, gh) in enumerate(boxes):
        if right is not None and gx-right >= height/2:
            # Prove a full blank gutter, rather than merely a gap between
            # bounding boxes that might contain smaller glyph fragments.
            if np.any(bw[:, right:gx]):
                return None
            # Do not silently truncate a separated digit or an unknown mark
            # on the counter line. Every remaining component must reach the
            # bottom edge. Anything overlapping the digit band must exceed
            # the widest glyph in the loaded font, including alignment slack.
            if any(sy <= top+baseline_tolerance or sy+sh != h
                   or (sy < bottom-baseline_tolerance and sw <= max_digit_width)
                   for _, sy, sw, sh in boxes[index:]):
                return None
            break
        if gx <= 0 or gy <= 0 or gx+gw >= w-1 or gy+gh >= h-1 or not 28 <= gh <= 48:
            return None
        if abs(gy-top) > baseline_tolerance or abs(gy+gh-bottom) > baseline_tolerance:
            return None
        patch = bw[gy:gy+gh, gx:gx+gw]
        scores = sorted((float(cv2.matchTemplate(
            cv2.resize(patch, (tpl.shape[1], tpl.shape[0])), tpl,
            cv2.TM_CCOEFF_NORMED)[0, 0]), ch) for ch, tpl in glyphs.items())
        score, ch = scores[-1]
        if score < .8 or score-scores[-2][0] < .1:
            return None
        digits.append(ch)
        right = gx+gw
    return int(''.join(digits))


def _load(font: str = "value") -> dict[str, np.ndarray]:
    if font not in _glyph_cache:
        glyphs: dict[str, np.ndarray] = {}
        names = {"dot": ".", "dollar": "$"}
        for d in _FONT_DIRS[font]:
            for p in settings.template_files(f"{d}/*.png"):
                ch = names.get(p.stem, p.stem)
                if ch not in glyphs:
                    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        glyphs[ch] = img
        _glyph_cache[font] = glyphs
    return _glyph_cache[font]


def read_amount(bgr: np.ndarray, thresh: int = 160,
                font: str = "value") -> float | None:
    """Parse a numeric amount from a cropped region. None if unreadable."""
    glyphs = _load(font)
    # white-only mask: value digits are white; drops green $ icon, green
    # interest line, and colored panel art that grayscale would let through
    bw = cv2.inRange(bgr, (thresh, thresh, thresh), (255, 255, 255))
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = sorted([cv2.boundingRect(c) for c in contours
                    if cv2.boundingRect(c)[3] >= 5 and cv2.boundingRect(c)[2] >= 3],
                   key=lambda b: b[0])
    if not boxes:
        return None
    heights = [b[3] for b in boxes if b[3] >= 15]
    if not heights:
        return None
    hmax = max(heights)
    chars = []
    H = bw.shape[0]
    for (x, y, w, h) in boxes:
        if h < 5 or (h < hmax * 0.5 and not (w <= 12 and h <= 12)):
            continue                       # noise; small squares may be '.'
        if h >= H - 3 or (w / h) < 0.22:
            continue                       # border/edge artifact, not a glyph
        if w <= 12 and h <= 12:
            chars.append(".")          # decimal point: size, not template
            continue                   # (tiny templates degenerate in matching)
        glyph = bw[y:y + h, x:x + w]
        best_ch, best_score = None, 0.5
        for ch, tpl in glyphs.items():
            if ch == ".":
                continue
            g = cv2.resize(glyph, (tpl.shape[1], tpl.shape[0]))
            score = float(cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)[0][0])
            if score > best_score:
                best_ch, best_score = ch, score
        if best_ch is None:
            return None                    # unknown glyph -> distrust everything
        chars.append(best_ch)
    s = "".join(c for c in chars if c != "$")
    mult = 1.0
    if s and s[-1] in _SUFFIX:
        mult = _SUFFIX[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None
