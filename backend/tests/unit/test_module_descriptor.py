import numpy as np
import pytest

from player import asset_verify, module_descriptor
from interactions import inventory


@pytest.mark.parametrize('scale,accepted', [(1.5, True), (4, False)])
def test_inner_artwork_fallback_preserves_scale_guard(monkeypatch, scale, accepted):
    monkeypatch.setattr(inventory, '_find_close', lambda frame: (927, 878))
    monkeypatch.setattr(asset_verify, 'locate', lambda icon, frame, region:
        None if icon.shape[0] == 150 else
        {'rect': [140, 895, 200, 200], 'scale': scale, 'inliers': 38})
    icon = np.zeros((150, 150, 3), dtype=np.uint8)
    frame = np.zeros((2560, 1080, 3), dtype=np.uint8)
    if accepted:
        assert module_descriptor.locate_icon(icon, frame) == [132, 887, 216, 216]
    else:
        with pytest.raises(RuntimeError):
            module_descriptor.locate_icon(icon, frame)
