"""Synthetic recognition failures and optional ignored native-frame replay."""
import json
import math
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from player.bootstrap_layout import manifest
from player.geometry import Display
from vision import orbit_gems


def diamond():
    """A geometric five-facet fixture, generated without captured artwork."""
    glyph = np.zeros((50, 54), np.uint8)
    polygon = np.array([[3, 16], [14, 3], [39, 3], [50, 16], [27, 46]], np.int32)
    cv2.polylines(glyph, [polygon], True, 255, 2)
    for start, end in [((3, 16), (50, 16)), ((14, 3), (27, 16)),
                       ((39, 3), (27, 16)), ((27, 16), (27, 46))]:
        cv2.line(glyph, start, end, 255, 2)
    return glyph


def scene(angle=0., center=(786, 1038), kind='gem', debris=False, scale=1.):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    x, y, w, h = manifest()['interface']['screens']['battle']['orbiting_gem']['hud_icon_rect']
    glyph = diamond()
    frame[y:y+glyph.shape[0], x:x+glyph.shape[1]] = glyph[:, :, None]
    tile = np.zeros((150, 150, 3), np.uint8)
    color = (255, 220, 255) if kind != 'red' else (0, 0, 255)
    cv2.rectangle(tile, (37, 37), (113, 113), (255, 40, 255) if kind != 'red' else color, 6)
    cv2.rectangle(tile, (37, 37), (113, 113), color, 2)
    if kind in ('gem', 'red'):
        tile[50:100, 48:102] = np.maximum(tile[50:100, 48:102], glyph[:, :, None])
    elif kind == 'filled':
        cv2.rectangle(tile, (50, 50), (100, 100), (255, 255, 255), -1)
    elif kind == 'boss':
        cv2.rectangle(tile, (44, 44), (106, 106), color, 4)
    if debris:
        tile[48:50, 48:50] = 255
        tile[100:102, 100:102] = 255
    tile = cv2.warpAffine(tile, cv2.getRotationMatrix2D((75, 75), angle, scale), (150, 150))
    cx, cy = center
    frame[cy-75:cy+75, cx-75:cx+75] = tile
    return frame


@pytest.mark.parametrize('angle', [0, 20, 45, 90, 135, 225, 300])
def test_recognizes_rotated_gem_without_saved_templates(angle):
    gem = orbit_gems.find_orbit_gem(scene(angle))
    assert gem is not None
    assert math.dist(gem.center, (786, 1038)) <= 2
    assert gem.score >= .8


@pytest.mark.parametrize('kind', ['empty', 'filled', 'boss', 'red'])
def test_square_enemies_and_plain_shapes_are_unknown(kind):
    assert orbit_gems.find_orbit_gem(scene(kind=kind)) is None


@pytest.mark.parametrize('damage', ['missing', 'filled', 'clipped'])
def test_unverified_hud_icon_cannot_supply_a_template(damage):
    frame = scene()
    x, y, w, h = manifest()['interface']['screens']['battle']['orbiting_gem']['hud_icon_rect']
    if damage == 'missing':
        frame[y:y+h, x:x+w] = 0
    elif damage == 'filled':
        frame[y:y+h, x:x+w] = 255
    else:
        frame[y:y+8, x:x+w] = 0
    assert orbit_gems.find_orbit_gem(frame) is None


def test_multiple_candidates_and_wrong_resolution_remain_unknown():
    first = scene(center=(786, 1038))
    second = scene(center=(250, 600))
    assert orbit_gems.find_orbit_gem(np.maximum(first, second)) is None
    assert orbit_gems.find_orbit_gem(first[::2, ::2]) is None


def test_manifest_geometry_is_read_per_call_and_scales(monkeypatch):
    native = manifest()
    scaled = manifest(Display(540, 1280, 180))
    monkeypatch.setattr(orbit_gems, 'manifest', lambda: native)
    assert orbit_gems.reference_geometry() == ((510, 805), 170.)
    monkeypatch.setattr(orbit_gems, 'manifest', lambda: scaled)
    assert orbit_gems.reference_geometry() == ((255, 402), 85.)
    assert scaled['interface']['screens']['battle']['orbiting_gem']['hud_icon_rect'] == [12, 95, 28, 27]


@pytest.mark.parametrize('rotation', [0, 1, 2, 3])
def test_glyph_alignment_ignores_separate_battle_pixels(rotation):
    frame = scene()
    region = manifest()['interface']['screens']['battle']['orbiting_gem']
    glyph = orbit_gems._hud_glyph(frame, region['hud_icon_rect'])
    rotated = np.rot90(glyph, rotation).copy()
    width = 61
    height = round(width*rotated.shape[0]/rotated.shape[1])
    inner = np.zeros((84, 84), np.uint8)
    # A complete faceted glyph plus separate bright debris. Its own scale and
    # alignment stay unchanged when unrelated pixels enter the square.
    inner[10:10+height, 7:7+width] = cv2.resize(rotated, (width, height))
    inner[2:4, 79:81] = 255
    scores = orbit_gems._glyph_scores(inner, glyph)
    assert scores is not None and scores[0] > .99
    assert scores[0]-scores[1] >= .1


@pytest.mark.parametrize('kind', ['empty', 'solid', 'triangle'])
def test_glyph_alignment_rejects_nonfaceted_interiors(kind):
    frame = scene()
    region = manifest()['interface']['screens']['battle']['orbiting_gem']
    glyph = orbit_gems._hud_glyph(frame, region['hud_icon_rect'])
    inner = np.zeros((84, 84), np.uint8)
    if kind == 'solid':
        inner[10:70, 10:70] = 255
    elif kind == 'triangle':
        cv2.polylines(inner, [np.array([[8, 70], [42, 8], [76, 70]])], True, 255, 2)
    scores = orbit_gems._glyph_scores(inner, glyph)
    assert scores is None or scores[0] < .8 or scores[0]-scores[1] < .1


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_full_frame_debris_keeps_the_verified_gem(angle):
    gem = orbit_gems.find_orbit_gem(scene(angle, debris=True))
    assert gem is not None
    assert math.dist(gem.center, (786, 1038)) <= 2
    assert gem.score >= .8


@pytest.mark.parametrize('angle', [0, 20, 45, 90, 135, 225, 300])
@pytest.mark.parametrize('scale', [.72, .76])
def test_smaller_collectible_keeps_its_verified_glyph(angle, scale):
    gem = orbit_gems.find_orbit_gem(scene(angle, scale=scale))
    assert gem is not None
    assert math.dist(gem.center, (786, 1038)) <= 2
    assert gem.score >= .8


@pytest.mark.parametrize('kind', ['empty', 'filled', 'boss', 'red'])
@pytest.mark.parametrize('angle', [0, 45, 90, 225])
def test_small_nonfaceted_shapes_remain_unknown(kind, angle):
    assert orbit_gems.find_orbit_gem(scene(angle, kind=kind, scale=.72)) is None


def test_two_small_collectibles_are_still_ambiguous():
    first = scene(center=(786, 1038), scale=.72)
    second = scene(center=(250, 600), scale=.72)
    assert orbit_gems.find_orbit_gem(first) is not None
    assert orbit_gems.find_orbit_gem(second) is not None
    assert orbit_gems.find_orbit_gem(np.maximum(first, second)) is None


@pytest.mark.parametrize('environment_variable,labels_filename', [
    ('TOWERPILOT_R14C_FIXTURES', 'replay-labels.json'),
    ('TOWERPILOT_R14D_FIXTURES', 'recognition-labels.json'),
])
def test_local_frame_replay(environment_variable, labels_filename):
    configured = os.environ.get(environment_variable)
    if not configured:
        pytest.skip(f'Captured game frames stay local; set {environment_variable} for incident replay')
    root = Path(configured)
    labels = json.loads((root / labels_filename).read_text(encoding='utf-8'))
    assert labels
    for row in labels:
        frame = cv2.imread(str(root / 'frames' / row['frame']))
        assert frame is not None, row['frame']
        gem = orbit_gems.find_orbit_gem(frame)
        if row['center'] is None:
            assert gem is None, row['frame']
        else:
            assert gem is not None, row['frame']
            assert math.dist(gem.center, row['center']) <= row.get('tolerance', 2), row['frame']
