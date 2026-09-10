import numpy as np
from interactions import shopper


def test_toggle_below_long_label_is_fully_visible(monkeypatch):
    # The full 51px pill extends beyond the former 90px search strip.
    frame = np.zeros((400, 300, 3), dtype=np.uint8)
    pill = np.random.default_rng(4).integers(0, 255, (51, 91, 3), dtype=np.uint8)
    frame[230:281, 40:131] = pill
    monkeypatch.setattr(shopper, 'CONFIG', {'rois': {'upgrade_panel': [0, 100, 300, 260]}})
    monkeypatch.setattr(shopper, '_uw_box', lambda f, w: (f[100:360], (30, 40)))
    templates = {'uw/chain_lightning.png': np.zeros((55, 200, 3), dtype=np.uint8),
                 'uw/toggle_off.png': pill, 'uw/toggle_on.png': 255 - pill}
    monkeypatch.setattr(shopper.detect, '_tpl', lambda rel: templates[rel])
    assert shopper._uw_state(frame, 'chain_lightning') is False
    assert shopper._uw_toggle_center(frame, 'chain_lightning') == (85, 255)
