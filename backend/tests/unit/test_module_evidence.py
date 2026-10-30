"""Module evidence from synthetic pixels, isolated from devices and account files."""
import subprocess
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from device import act, adbclient, capture
from interactions import inventory, module_evidence as evidence, tourney
from player.bootstrap_layout import reference_manifest
from player.geometry import Display, scale_manifest
from vision import detect, pills


def _deny_io(*args, **kwargs):
    raise AssertionError('unexpected device or process I/O')


def _art(seed, height=24, width=28):
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def _paste(frame, image, centre):
    cx, cy = centre
    h, w = image.shape[:2]
    frame[cy-h//2:cy-h//2+h, cx-w//2:cx-w//2+w] = image


@pytest.fixture
def scene(monkeypatch, tmp_path):
    """Templates are an in-memory calibration boundary; geometry stays real."""
    spec = scale_manifest(reference_manifest(), Display(1080, 2560, 360))
    templates = {'modules/buy_module.png': _art(900)}

    def template(name):
        if name not in templates:
            raise detect.TemplateMissing(name)
        return templates[name]

    monkeypatch.setattr(detect, '_tpl', template)
    monkeypatch.setattr(evidence, 'manifest', lambda: spec)
    monkeypatch.setattr(inventory, 'manifest', lambda: spec)
    # configure_geometry assigns these module globals during settled(). Restore
    # them with the manifest, including when a scaled test raises midway.
    for name in ('COL_X', 'ROW_Y', 'TILE', 'CLOSE_STRIP', 'HEAD_DY', 'HEAD_X',
                 'SCRIM_PROBE', 'GRID_BAND', 'GRID_TOUCH', 'GRID_GAP_Y',
                 'PAGE_DRAG', 'SWIPE_X'):
        monkeypatch.setattr(inventory, name, getattr(inventory, name))
    monkeypatch.setattr(tourney, 'MODULE_CATEGORY', {'synthetic_module': 'cannon'})
    monkeypatch.setattr(capture, 'grab', _deny_io)
    monkeypatch.setattr(act, 'tap', _deny_io)
    monkeypatch.setattr(act, 'swipe', _deny_io)
    for name in ('exec_out', 'exec_to', 'shell', 'alive', 'devices', 'get_state', 'reconnect'):
        monkeypatch.setattr(adbclient, name, _deny_io)
    monkeypatch.setattr(subprocess, 'Popen', _deny_io)
    monkeypatch.setattr(inventory, 'settle_status', _deny_io)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(spec=spec, templates=templates)


def _frame(scene, shape=(2560, 1080), tabs=False, landmark=True, active=0):
    frame = np.full((*shape, 3), 12, np.uint8)
    y0, y1, x0, x1 = scene.spec['module_inventory']['scrim_probe']
    frame[y0:y1, x0:x1] = 150
    if landmark:
        _paste(frame, scene.templates['modules/buy_module.png'], (420, 75))
    if tabs:
        band0, _ = scene.spec['tab_bands']['modules']
        for i, text in enumerate(('My 42', 'Arbitrary', 'Other')):
            hue = 48 if i == active else 99
            color = tuple(int(v) for v in cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0])
            x, y = 70 + 270*i, band0 + 35
            cv2.rectangle(frame, (x, y), (x+210, y+80), color, 6)
            cv2.putText(frame, text, (x+14, y+50), cv2.FONT_HERSHEY_SIMPLEX, .6, (240, 240, 240), 1)
    return frame


def _slot(frame, scene, kind, category, art, occupied=True):
    categories = ('cannon', 'armor', 'generator', 'core')
    centre = tuple(scene.spec['module_slots'][kind][categories.index(category)])
    if occupied:
        cv2.circle(frame, centre, scene.spec['module_slots']['radius'][kind], (255, 0, 255), 10)
    _paste(frame, art, centre)


def _grid(scene, targets=(), row=1300, shape=(2560, 1080)):
    frame = _frame(scene, tabs=True, shape=shape)
    half = scene.spec['module_inventory']['tile']//2
    for cx in scene.spec['module_inventory']['columns']:
        frame[row-half:row+half, cx-half:cx+half] = (255, 0, 255)
    for image, column, dx in targets:
        _paste(frame, image, (scene.spec['module_inventory']['columns'][column]+dx, row))
    return frame


def _walk(monkeypatch, frames, *, top=True, distance=200, quiet=True, max_pages=4):
    """Fake capture settling/page boundaries, retain the real screen and art matchers."""
    remaining = iter(frames)
    observations, swipes = [], []

    def settle_status():
        frame = next(remaining)
        observations.append(frame)
        return frame, quiet

    monkeypatch.setattr(inventory, 'settle_status', settle_status)
    monkeypatch.setattr(evidence, '_at_top', lambda frame: top)
    monkeypatch.setattr(evidence, '_scroll_distance', lambda previous, current: distance)
    monkeypatch.setattr(inventory, 'MAX_PAGES', max_pages)
    monkeypatch.setattr(act, 'swipe', lambda *args, **kwargs: swipes.append((args, kwargs)))
    return observations, swipes


@pytest.mark.parametrize('active', [0, 1, 2])
def test_ui_version_uses_arbitrary_label_pills_with_modules_landmark(scene, active):
    frame = _frame(scene, tabs=True, active=active)
    found = pills.pills(frame, *scene.spec['tab_bands']['modules'])
    assert len(found) == 3
    assert evidence.ui_version(frame) == 'v29'


@pytest.mark.parametrize('tabs,landmark', [(False, True), (True, False), (False, False)])
def test_absent_tabs_or_modules_landmark_is_unknown_never_legacy(scene, tabs, landmark):
    assert evidence.ui_version(_frame(scene, tabs=tabs, landmark=landmark)) is None


@pytest.mark.parametrize('active', [-1, None])
def test_tabs_without_unique_selected_pill_do_not_prove_v29(scene, active):
    frame = _frame(scene, tabs=True, active=-1)
    if active is None:
        # Two positively green pills cannot identify a selected preset.
        y0, _ = scene.spec['tab_bands']['modules']
        for x in (70, 340):
            cv2.rectangle(frame, (x, y0+35), (x+210, y0+115), (0, 255, 102), 6)
    assert evidence.ui_version(frame) is None


@pytest.mark.parametrize('display', [Display(1080, 2560, 360), Display(1080, 1920, 280), Display(720, 1707, 240)])
@pytest.mark.parametrize('kind,expected', [('large', True), ('small', False)])
def test_header_identity_reads_active_manifest_large_and_small_slots(scene, display, kind, expected):
    scene.spec.clear()
    scene.spec.update(scale_manifest(reference_manifest(), display))
    art = _art(1, 20, 22)
    scene.templates['modules/equipped/synthetic_module.png'] = art
    frame = _frame(scene, shape=(display.height, display.width))
    _slot(frame, scene, kind, 'cannon', art)
    assert evidence.verify_v29('synthetic_module', frame) is expected


@pytest.mark.parametrize('occupied,category,expected', [(True, 'armor', False), (False, 'cannon', False), (True, 'cannon', True)])
def test_header_requires_occupied_expected_category(scene, occupied, category, expected):
    art = _art(2)
    scene.templates['modules/equipped/synthetic_module.png'] = art
    frame = _frame(scene)
    _slot(frame, scene, 'large', category, art, occupied=occupied)
    assert evidence.verify_v29('synthetic_module', frame) is expected


def test_duplicate_occupied_header_matches_are_unknown(scene):
    art = _art(3)
    scene.templates['modules/equipped/synthetic_module.png'] = art
    frame = _frame(scene)
    _slot(frame, scene, 'large', 'cannon', art)
    _slot(frame, scene, 'small', 'cannon', art)
    assert evidence.verify_v29('synthetic_module', frame) is None


def test_unknown_owned_slug_cannot_prove_expected_category(scene):
    art = _art(4)
    scene.templates['modules/equipped/arbitrary_owned_slug.png'] = art
    frame = _frame(scene)
    _slot(frame, scene, 'large', 'generator', art)
    assert evidence.verify_v29('arbitrary_owned_slug', frame) is None


@pytest.mark.parametrize('constant', [False, True])
def test_missing_or_nondiscriminating_header_templates_are_unknown(scene, constant):
    if constant:
        scene.templates['modules/equipped/synthetic_module.png'] = np.full((24, 28, 3), 150, np.uint8)
    assert evidence.verify_v29('synthetic_module', _frame(scene)) is None


def test_inventory_art_fallback_matches_scaled_header_without_dedicated_template(scene):
    tile = _art(5, 100, 100)
    scene.templates['modules/synthetic_module.png'] = tile
    icon = cv2.resize(tile[25:65, 25:75], None, fx=.6, fy=.6)
    frame = _frame(scene)
    _slot(frame, scene, 'large', 'cannon', icon)
    assert evidence.verify_v29('synthetic_module', frame) is True


def test_inventory_lookalike_does_not_prove_equipped_header(scene):
    tile = _art(6, 100, 100)
    scene.templates['modules/synthetic_module.png'] = tile
    frame = _grid(scene, [(tile, 0, 0)])
    assert evidence.verify_v29('synthetic_module', frame) is False


def test_grid_accepts_unique_complete_tile_and_rejects_clipped_row(scene):
    art = _art(7)
    frame = _grid(scene, [(art, 0, 0)])
    assert evidence._grid_hits(frame, art) == [(scene.spec['module_inventory']['columns'][0], 1300)]
    clipped = _grid(scene, [(art, 0, 0)], row=1100)
    assert evidence._grid_hits(clipped, art) == []


@pytest.mark.parametrize('display', [Display(1080, 1920, 280), Display(720, 1707, 240)])
def test_inventory_geometry_uses_current_scaled_manifest(scene, display):
    scene.spec.clear()
    scene.spec.update(scale_manifest(reference_manifest(), display))
    spec = scene.spec['module_inventory']
    row = sum(spec['grid_clear'])//2
    art = _art(70)
    frame = _grid(scene, [(art, 1, 0)], row=row, shape=(display.height, display.width))
    assert evidence._grid_hits(frame, art) == [(spec['columns'][1], row)]
    assert evidence._at_top(frame) is True
    clipped = _grid(scene, [(art, 1, 0)], row=spec['grid_gap_y'], shape=(display.height, display.width))
    assert evidence._grid_hits(clipped, art) == []
    assert evidence._at_top(clipped) is False


@pytest.mark.parametrize('boundary', ['frame_left', 'frame_right'])
def test_grid_rejects_horizontal_tile_clipping_even_when_icon_is_visible(scene, boundary):
    art = _art(71)
    frame = _grid(scene, [(art, 0, 0)])
    spec = scene.spec['module_inventory']
    cx = spec['columns'][0]
    if boundary == 'frame_left':
        frame = frame[:, 100:]
        spec['columns'] = [x-100 for x in spec['columns']]
    else:
        frame = frame[:, :cx+40]
    assert evidence._grid_hits(frame, art) == []


@pytest.mark.parametrize('boundary', ['search_left', 'search_right'])
def test_row_projection_band_does_not_hide_complete_tile_at_image_edge(scene, boundary):
    art = _art(72)
    frame = _grid(scene, [(art, 0, 0)])
    spec = scene.spec['module_inventory']
    cx = spec['columns'][0]
    spec['grid_search'][2 if boundary == 'search_left' else 3] = cx+(-20 if boundary == 'search_left' else 20)
    assert evidence._grid_hits(frame, art) == [(cx, 1300)]


@pytest.mark.parametrize('display', [Display(1080, 2560, 360), Display(720, 1707, 240)])
def test_real_page_overlap_distinguishes_motion_end_and_lost_overlap(scene, display):
    scene.spec.clear()
    scene.spec.update(scale_manifest(reference_manifest(), display))
    spec = scene.spec['module_inventory']
    first = _frame(scene, shape=(display.height, display.width))
    half = spec['tile']//2
    step = round(200 * display.width/1080)
    row0 = spec['grid_clear'][0]+2*half
    for row, cy in enumerate((row0, row0+step, row0+2*step)):
        for col, cx in enumerate(spec['columns']):
            tile = _art(300+row*5+col, 2*half, 2*half)
            tile[..., 0] = 255
            tile[..., 1] = 0
            _paste(first, tile, (cx, cy))
    after = first.copy()
    y0, y1, x0, x1 = spec['grid_search']
    after[y0:y1-step, x0:x1] = first[y0+step:y1, x0:x1]
    after[y1-step:y1, x0:x1] = 12
    assert evidence._scroll_distance(first, after) == step
    assert evidence._scroll_distance(first, first.copy()) == 0
    lost = _frame(scene, shape=(display.height, display.width))
    assert evidence._scroll_distance(first, lost) is None


def test_hidden_inventory_target_is_paged_then_reacquired_from_fresh_frame(scene, monkeypatch):
    art = _art(8)
    scene.templates['modules/synthetic_module.png'] = art
    first = _grid(scene)
    found = _grid(scene, [(art, 1, 0)])
    current = found.copy()
    observations, swipes = _walk(monkeypatch, [first, found, current])
    frame, point = evidence.find_inventory('synthetic_module', version='v29')
    assert frame is current
    assert point == (scene.spec['module_inventory']['columns'][1], 1300)
    assert len(observations) == 3
    assert len(swipes) == 1
    assert swipes[0][1]['reason'] == 'module inventory page'


def test_reacquisition_returns_current_small_shift_instead_of_stale_point(scene, monkeypatch):
    art = _art(80)
    scene.templates['modules/synthetic_module.png'] = art
    initial = _grid(scene, [(art, 1, 0)])
    current = _grid(scene, [(art, 1, 2)])
    _, swipes = _walk(monkeypatch, [initial, current])
    frame, point = evidence.find_inventory('synthetic_module', 'v29')
    assert frame is current
    assert point == (scene.spec['module_inventory']['columns'][1]+2, 1300)
    assert swipes == []


@pytest.mark.parametrize('constant', [False, True])
def test_inventory_template_must_exist_and_be_discriminating_before_input(scene, constant):
    if constant:
        scene.templates['modules/synthetic_module.png'] = np.full((24, 28, 3), 150, np.uint8)
    with pytest.raises(tourney.Abort, match='not discriminating|no inventory template'):
        evidence.find_inventory('synthetic_module', 'v29')


@pytest.mark.parametrize('interruption', ['missing_landmark', 'missing_tabs', 'modal'])
def test_interrupted_inventory_refuses_page_input(scene, monkeypatch, interruption):
    scene.templates['modules/synthetic_module.png'] = _art(81)
    frame = _frame(scene, tabs=interruption != 'missing_tabs', landmark=interruption != 'missing_landmark')
    if interruption == 'modal':
        y0, y1, x0, x1 = scene.spec['module_inventory']['scrim_probe']
        frame[y0:y1, x0:x1] = 10
    _, swipes = _walk(monkeypatch, [frame])
    with pytest.raises(tourney.Abort, match='unknown or interrupted'):
        evidence.find_inventory('synthetic_module', 'v29')
    assert swipes == []


def test_duplicate_visible_inventory_targets_abort_before_input(scene, monkeypatch):
    art = _art(9)
    scene.templates['modules/synthetic_module.png'] = art
    observations, swipes = _walk(monkeypatch, [_grid(scene, [(art, 0, 0), (art, 1, 0)])])
    with pytest.raises(tourney.Abort, match='ambiguous'):
        evidence.find_inventory('synthetic_module', 'v29')
    assert len(observations) == 1
    assert swipes == []


@pytest.mark.parametrize('fresh', ['moved', 'missing', 'duplicate'])
def test_inventory_target_must_remain_unique_and_stationary_at_reacquisition(scene, monkeypatch, fresh):
    art = _art(10)
    scene.templates['modules/synthetic_module.png'] = art
    targets = {'moved': [(art, 0, 8)], 'missing': [], 'duplicate': [(art, 0, 0), (art, 1, 0)]}
    _, swipes = _walk(monkeypatch, [_grid(scene, [(art, 0, 0)]), _grid(scene, targets[fresh])])
    with pytest.raises(tourney.Abort, match='moved or changed'):
        evidence.find_inventory('synthetic_module', 'v29')
    assert swipes == []


def test_unsettled_inventory_aborts_before_input(scene, monkeypatch):
    scene.templates['modules/synthetic_module.png'] = _art(11)
    _, swipes = _walk(monkeypatch, [_grid(scene)], quiet=False)
    with pytest.raises(tourney.Abort, match='did not settle'):
        evidence.find_inventory('synthetic_module', 'v29')
    assert swipes == []


@pytest.mark.parametrize('distance,pattern', [(None, 'overlap was lost'), (200, 'scan bound')])
def test_inventory_overlap_loss_and_search_exhaustion_abort(scene, monkeypatch, distance, pattern):
    scene.templates['modules/synthetic_module.png'] = _art(12)
    _, swipes = _walk(monkeypatch, [_grid(scene) for _ in range(3)], distance=distance, max_pages=2)
    with pytest.raises(tourney.Abort, match=pattern):
        evidence.find_inventory('synthetic_module', 'v29')
    assert len(swipes) == (1 if distance is None else 2)


def test_inventory_absence_requires_observed_end(scene, monkeypatch):
    scene.templates['modules/synthetic_module.png'] = _art(13)
    first, end = _grid(scene), _grid(scene)
    _, swipes = _walk(monkeypatch, [first, end], distance=0)
    frame, point = evidence.find_inventory('synthetic_module', 'v29')
    assert frame is end and point is None
    assert len(swipes) == 1


def test_unproven_inventory_top_has_bounded_reset_and_aborts(scene, monkeypatch):
    scene.templates['modules/synthetic_module.png'] = _art(14)
    _, swipes = _walk(monkeypatch, [_grid(scene) for _ in range(6)], top=False)
    with pytest.raises(tourney.Abort, match='top not proven'):
        evidence.find_inventory('synthetic_module', 'v29')
    assert len(swipes) == 5
    assert all(kwargs['reason'] == 'module inventory to top' for _, kwargs in swipes)


@pytest.mark.parametrize('scrim,legacy,v29,accepted', [(True, True, False, True), (False, True, False, False), (True, False, False, False), (True, True, True, False)])
def test_legacy_dialog_requires_own_scrim_and_equip_and_excludes_v29(scene, scrim, legacy, v29, accepted):
    frame = _frame(scene)
    if scrim:
        y0, y1, x0, x1 = scene.spec['module_inventory']['scrim_probe']
        frame[y0:y1, x0:x1] = 10
    inventory.configure_geometry()
    for present, path, centre, seed in [(legacy, 'modules/equip_btn.png', (450, 1200), 100), (v29, 'modules/v29_equip_btn.png', (700, 1500), 101)]:
        scene.templates[path] = _art(seed)
        if present:
            _paste(frame, scene.templates[path], centre)
    if accepted:
        assert evidence.require_legacy_dialog(frame) is None
    else:
        with pytest.raises(tourney.Abort, match='not positively recognized'):
            evidence.require_legacy_dialog(frame)


def test_legacy_equip_lookalike_cannot_override_positive_v29_tabs(scene):
    frame = _frame(scene, tabs=True)
    y0, y1, x0, x1 = scene.spec['module_inventory']['scrim_probe']
    frame[y0:y1, x0:x1] = 10
    inventory.configure_geometry()
    art = _art(102)
    scene.templates['modules/equip_btn.png'] = art
    _paste(frame, art, (450, 1200))
    assert evidence.ui_version(frame) == 'v29'
    with pytest.raises(tourney.Abort, match='not positively recognized'):
        evidence.require_legacy_dialog(frame)
