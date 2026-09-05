"""Read the current wave number from the wave_box ROI via digit templates.

Digit templates: templates/digits/0.png .. 9.png, cropped from NATIVE captures
of the same HUD font. Reader thresholds white text, segments glyphs by contour,
and matches each glyph against the ten templates.
"""
import cv2
import numpy as np
from pathlib import Path

from settings import ROOT
from device import capture

_TPL_DIR = ROOT / "templates" / "digits"
_templates: dict[int, np.ndarray] | None = None

MATCH_THRESHOLD = 0.60

# LAYOUT PROOF (2026-09-04). A readable wave number is the autopilot's
# "tower on screen" evidence - every tap rail rests on it - so the reader
# must not accept ANY digit that happens to sit in the wave box. The
# counter is left-aligned after the "Wave" label: its first digit starts at
# x 16-17 inside the ROI on all 409 battle frames measured (every wave from
# 1 to 6407, both panel layouts). The BATTLE HISTORY screen, scrolled so a
# row's date lands in the box, shows a lone "9" from "9/3/2026" at x 139-145
# with the slash cut by the ROI edge; four times (2026-09-02 08:33,
# 2026-09-04 00:06, 08:50, 08:53) that read as wave 9, faked a run boundary,
# and the UW-tab tap that normalizes a new run landed on a human's menu.
FIRST_DIGIT_X = (10, 24)      # inclusive window for the first glyph's left edge


def _load_templates() -> dict[int, np.ndarray]:
    global _templates
    if _templates is None:
        _templates = {}
        for d in range(10):
            p = _TPL_DIR / f"{d}.png"
            if p.exists():
                _templates[d] = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if len(_templates) < 10:
            raise RuntimeError(
                f"digit templates incomplete ({len(_templates)}/10) in {_TPL_DIR}")
    return _templates


def _binarize(gray: np.ndarray) -> np.ndarray:
    # HUD digits are near-white on a dark navy box.
    _, bw = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    return bw


def read_wave(frame: np.ndarray) -> int | None:
    """Return the wave number, or None if unreadable this frame.

    Auto-detects panel-open vs panel-closed layout: on a miss, retries with
    the other layout offset and keeps whichever works (all shifted ROIs -
    HP bar, ability row - follow via capture.layout_offset).
    """
    result = _read(frame)
    if result is None:
        capture.layout_offset = (capture.PANEL_SHIFT
                                 if capture.layout_offset == 0 else 0)
        result = _read(frame)
        if result is None:  # neither layout -> revert, likely dialog/death
            capture.layout_offset = (capture.PANEL_SHIFT
                                     if capture.layout_offset == 0 else 0)
    return result


def _read(frame: np.ndarray) -> int | None:
    tpls = _load_templates()
    gray = cv2.cvtColor(capture.roi(frame, "wave_box"), cv2.COLOR_BGR2GRAY)
    bw = _binarize(gray)

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    glyphs = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # wave digits measured h=32; ROI excludes the side column spatially
        if h < 28 or w < 6:
            continue
        glyphs.append((x, bw[y:y + h, x:x + w]))
    if not glyphs:
        return None
    glyphs.sort(key=lambda g: g[0])  # left-to-right
    # Layout proof (see FIRST_DIGIT_X): a counter that does not start where
    # the HUD draws it, or a glyph cut by the ROI's right edge, is not the
    # wave counter - whatever digits it contains.
    if not (FIRST_DIGIT_X[0] <= glyphs[0][0] <= FIRST_DIGIT_X[1]):
        return None
    if any(x + g.shape[1] >= bw.shape[1] - 1 for x, g in glyphs):
        return None

    digits = []
    for _, glyph in glyphs:
        best_d, best_score = None, MATCH_THRESHOLD
        for d, tpl in tpls.items():
            g = cv2.resize(glyph, (tpl.shape[1], tpl.shape[0]))
            score = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)[0][0]
            if score > best_score:
                best_d, best_score = d, score
        if best_d is None:
            return None              # one unreadable glyph -> distrust the frame
        digits.append(best_d)
    return int("".join(map(str, digits)))


class WaveTracker:
    """Sanity-checks raw reads: waves normally move forward in small steps.

    A rejection must never be permanent. The original version only re-synced
    when it saw a wave under 50 (a new run), so ONE oversized skip stranded it
    forever: it sat at 3643 while the game reached 4949, and every wave-driven
    rule (fleet nukes, the late-run Chain Lightning latch) silently stopped
    firing. Enemy level skips can jump far more than 20 waves at once, and the
    counter also resets to 1 on a new run.

    So an out-of-range read is not discarded, it is put on probation: a
    GENUINE jump keeps producing consistent readings frame after frame, while
    OCR garbage does not repeat. After `confirm` mutually-consistent readings
    the tracker re-syncs to the new value, in either direction.
    """

    def __init__(self, max_jump: int = 20, confirm: int = 3):
        self.last: int | None = None
        self.max_jump = max_jump
        self.confirm = confirm
        self.rejects = 0
        self.pending: int | None = None
        self.resyncs = 0

    def update(self, raw: int | None) -> int | None:
        if raw is None:
            return self.last
        if self.last is None or 0 <= raw - self.last <= self.max_jump:
            self.last = raw
            self.pending, self.rejects = None, 0
            return raw
        # ---- probation: consistent with the previous odd reading?
        if self.pending is not None and 0 <= raw - self.pending <= self.max_jump:
            self.rejects += 1
        else:
            self.rejects = 1
        self.pending = raw
        if self.rejects >= self.confirm:
            self.last = raw
            self.pending, self.rejects = None, 0
            self.resyncs += 1
        return self.last
