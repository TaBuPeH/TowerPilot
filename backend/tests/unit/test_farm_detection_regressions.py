import cv2
import numpy as np
from vision import detect
from interactions import missions


def test_nuke_above_old_roi_and_with_closed_panel(monkeypatch):
    glyph = np.random.default_rng(31).integers(0, 255, (68, 66, 3), dtype=np.uint8)
    monkeypatch.setattr(detect, '_tpl', lambda name: glyph)
    for shift in (0, 679):
        f = np.zeros((2560, 1080, 3), dtype=np.uint8)
        cv2.rectangle(f, (18, 1445+shift), (151, 1540+shift), (0, 255, 255), 5)
        f[1457+shift:1525+shift, 55:121] = glyph
        state = detect.button_state(f, 'nuke')
        assert state.present and state.ready
        assert state.center == (88, 1491+shift)
        assert detect.button_border_val(f, 'nuke') > 200
        # Identical glyph over a dim edge must not count as ready.
        cv2.rectangle(f, (18, 1445+shift), (151, 1540+shift), (20, 20, 20), 5)
        assert not detect.button_state(f, 'nuke').ready


def test_missing_chest_lock_skips_chests_instead_of_crashing(monkeypatch):
    def missing(name):
        raise detect.TemplateMissing(name)
    monkeypatch.setattr(detect, '_tpl', missing)
    monkeypatch.setattr(missions.logger, 'event', lambda *a, **k: None)
    assert missions.claimable_chests(np.zeros((2560, 1080, 3), dtype=np.uint8)) == []
