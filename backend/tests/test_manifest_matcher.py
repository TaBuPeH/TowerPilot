import hashlib

import cv2
import numpy as np

from player.manifest_matcher import LocalMatcher


def make_matcher(**changes):
    art = np.random.default_rng(12).integers(40, 255, (40, 40, 3), dtype=np.uint8)
    raw = cv2.imencode('.png', art)[1].tobytes()
    row = dict(template='local.png', screen='battle_menu', verified=True,
               manifest_version=6, image_sha256=hashlib.sha256(raw).hexdigest(),
               rect=[850, 10, 40, 40], position='container', anchor='menu')
    row.update(changes)
    matcher = LocalMatcher({'open': row}, lambda _: raw,
                           lambda data: cv2.imdecode(np.frombuffer(data, np.uint8), 1),
                           version=6, locate_anchor=lambda *a: dict(
                               verified=True, rect=[840, 0, 240, 1100]))
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[650:690, 960:1000] = art
    return matcher, frame, dict(id='open', source='battle_menu'), art


def test_container_icon_can_move_several_rows():
    matcher, frame, edge, _ = make_matcher()
    assert matcher(frame, edge)['rect'] == [960, 650, 40, 40]


def test_duplicate_icon_is_not_a_location():
    matcher, frame, edge, art = make_matcher()
    frame[100:140, 850:890] = art
    assert matcher(frame, edge) is None


def test_changed_image_or_manifest_invalidates_mapping():
    for changes in ({'manifest_version': 5}, {'image_sha256': 'old'}, {'verified': False}):
        matcher, frame, edge, _ = make_matcher(**changes)
        assert matcher(frame, edge) is None


def test_relative_control_requires_live_anchor():
    matcher, frame, edge, _ = make_matcher(position='anchor_relative', rect_offset=[120,650,40,40])
    assert matcher(frame, edge)['rect'] == [960, 650, 40, 40]
    matcher.locate_anchor = lambda *a: None
    assert matcher(frame, edge) is None
