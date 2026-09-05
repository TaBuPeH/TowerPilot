"""Value OCR for HUD amounts ("$ 2.30M" cash, "$ 45K" price boxes).

Glyph templates in templates/valuefont/ (harvested auto-labeled from known
amounts); digits missing there fall back to templates/digits/ (same font
family; classifier resizes per comparison, so size differences are fine).
"""
import cv2
import numpy as np
from pathlib import Path

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


def _load(font: str = "value") -> dict[str, np.ndarray]:
    if font not in _glyph_cache:
        glyphs: dict[str, np.ndarray] = {}
        names = {"dot": ".", "dollar": "$"}
        for d in _FONT_DIRS[font]:
            for p in (ROOT / "templates" / d).glob("*.png"):
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
