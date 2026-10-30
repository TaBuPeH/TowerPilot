"""r15's neutral-gray recognition in interactions/ad_gems.py, through the r11d merge.

r15 (batched OCR, gray-border recognition) and r11d (the recorded action history of the Ad gems window) both changed ad_gems.py; the merged file
carries both. test_ad_gems.py asserts that a closed gray border is accepted and that it still needs CLAIM and 6; nothing there asserts the other half of
the rule the code states ("only a closed rectangular border is a neutral candidate"), so a merge that dropped it would have gone unnoticed.
"""
import cv2
import numpy as np

from interactions import ad_gems
from vision import textocr


def test_an_open_neutral_bar_is_not_an_offer_border_and_the_closed_border_is(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda _: tmp_path / 'missing.png')
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['CLAIM 6'] * len(crops))
    frame = np.zeros((2560, 1080, 3), np.uint8)
    # three sides of the border: HUD bars that run past the search rail look like this, and the text inside them can read CLAIM 6
    cv2.polylines(frame, [np.array([[20, 1348], [20, 1200], [268, 1200], [268, 1348]])], False, (208, 208, 208), 4)
    assert ad_gems.find_claim_rect(frame) is None
    cv2.rectangle(frame, (20, 1200), (268, 1348), (208, 208, 208), 4)           # the fourth side closes it
    assert ad_gems.find_claim_rect(frame) == (18, 1198, 253, 153)