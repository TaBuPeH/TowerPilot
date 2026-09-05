"""Layout proof in vision.wave_reader (2026-09-04).

A readable wave number is the autopilot's "tower on screen" evidence, so the
reader must reject digits that merely happen to sit in the wave box: the
BATTLE HISTORY screen, scrolled so a row's date lands there, showed a lone
"9" from "9/3/2026" at x 139-145 and read as wave 9 four times - each one a
fake run boundary whose new-run normalization tapped into a human's menu.
The HUD counter is left-aligned after "Wave": first digit at x 16-17 on all
409 battle frames measured.
"""
import cv2
import numpy as np
import pytest

import device.capture as capture
import vision.wave_reader as wave_reader


def _frame_with_digits(digits: str, x0: int, y0: int = 6) -> np.ndarray:
    """A black 1080x2560 frame with white HUD digits pasted into the wave box
    starting at x0 (ROI-relative), spaced like the real counter.

    Use digits 1 2 4 5 6 9 only: their templates are the full 32 px tall.
    The 0 3 7 8 templates are cropped to 25-27 px and a pasted copy falls
    under the reader's 28 px glyph floor (real HUD digits render at 32)."""
    frame = np.zeros((2560, 1080, 3), np.uint8)
    rx, ry, rw, rh = capture.CONFIG["rois"]["wave_box"]
    x = x0
    for ch in digits:
        tpl = wave_reader._load_templates()[int(ch)]
        h, w = tpl.shape
        assert x + w <= rw + 2, "test digits must stay inside/at the ROI edge"
        mask = tpl > 180
        sub = frame[ry + y0:ry + y0 + h, rx + x:rx + x + w]
        vis = mask[:, :sub.shape[1]]
        sub[vis] = (255, 255, 255)
        x += w + 4
    return frame


@pytest.fixture(autouse=True)
def _closed_layout(monkeypatch):
    monkeypatch.setattr(capture, "layout_offset", 0)


def test_counter_at_hud_position_reads():
    assert wave_reader.read_wave(_frame_with_digits("95", 16)) == 95
    assert wave_reader.read_wave(_frame_with_digits("4595", 17)) == 4595


def test_lone_digit_away_from_hud_position_is_not_a_wave():
    # the BATTLE HISTORY date digit: right end of the box
    assert wave_reader.read_wave(_frame_with_digits("9", 139)) is None
    # anywhere else off the label position is not the counter either
    assert wave_reader.read_wave(_frame_with_digits("9", 60)) is None


def test_glyph_cut_by_roi_edge_is_distrusted():
    rx, ry, rw, rh = capture.CONFIG["rois"]["wave_box"]
    tpl_w = wave_reader._load_templates()[9].shape[1]
    # a digit at the HUD position followed by one run into the right edge
    frame = _frame_with_digits("9", 16)
    cut = _frame_with_digits("9", rw - tpl_w + 1)
    frame = np.maximum(frame, cut)
    assert wave_reader.read_wave(frame) is None


def test_first_digit_window_matches_measured_layout():
    lo, hi = wave_reader.FIRST_DIGIT_X
    assert lo <= 16 <= 17 <= hi
    assert hi < 139
