"""Synthetic Sprint identity and owned setup dispatch regressions; no game pixels."""
from copy import deepcopy
from types import SimpleNamespace
import subprocess

import cv2
import numpy as np
import pytest

from player import flow_capture as fc


def canvas():
    layout = fc.manifest()['layout']
    frame = np.zeros((layout['height'], layout['width'], 3), np.uint8)
    # Synthetic scene anchors distinguish a proven battle from changed UI.
    geometry = fc.manifest()
    rectangles = [target['rect'] for target in geometry['hud']['battle']['targets']]
    rectangles.append(geometry['interface']['screens']['battle']['controls']['attack']['reference_rect'])
    for x, y, w, h in rectangles:
        yy, xx = np.indices((h, w))
        frame[y:y+h, x:x+w] = np.where(((xx+yy) % 9 < 4)[...,None], 170, 40)
    return frame


def draw_clock(frame, *, centre=None, radius=24, red='cross', hands=True):
    x, y, w, h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cx, cy = centre or (x+w//2, y+h//2)
    cv2.circle(frame, (cx, cy), radius, (255, 255, 255), 3)
    if hands:
        cv2.line(frame, (cx, cy), (cx, cy-radius//2), (255, 255, 255), 3)
        cv2.line(frame, (cx, cy), (cx+radius//2, cy+radius//3), (255, 255, 255), 3)
    cv2.rectangle(frame, (cx-5, cy-radius-9), (cx+5, cy-radius-5), (255, 255, 255), -1)
    cv2.line(frame, (cx, cy-radius-6), (cx, cy-radius-1), (255, 255, 255), 3)
    mx, my = cx+radius+3, cy-radius
    if red == 'cross':
        cv2.line(frame, (mx-7, my-7), (mx+7, my+7), (0, 0, 255), 3)
        cv2.line(frame, (mx-7, my+7), (mx+7, my-7), (0, 0, 255), 3)
    elif red == 'digits':
        cv2.putText(frame, '99', (mx-8, my+8), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 255), 2)
    elif red == 'bar':
        cv2.rectangle(frame, (mx-8, my-7), (mx+8, my+7), (0, 0, 255), -1)
    return frame


@pytest.fixture(autouse=True)
def no_device_or_ocr(monkeypatch):
    from device import act, adbclient, capture
    from vision import textocr

    def forbidden(*args, **kwargs):
        pytest.fail('Sprint test reached real device/OCR')

    for module, name in ((act, '_input'), (adbclient, 'shell'), (adbclient, 'exec_out'),
                         (capture, 'grab'), (textocr, 'read_lines'), (textocr, 'read_texts')):
        monkeypatch.setattr(module, name, forbidden)


def test_clock_with_hands_and_upper_right_cross_is_identified():
    crop, point = fc._sprint_glyph(draw_clock(canvas()))
    assert point is not None and crop.size
    # Both the white clock and its red X must be retained for stable proof.
    assert np.any(crop.min(axis=2) > 185)
    assert np.any((crop[:, :, 2] > 140) & (crop[:, :, 0] < 60))


def test_hollow_clock_survives_softened_synthetic_edges():
    frame = cv2.GaussianBlur(draw_clock(canvas()), (3,3), .6)
    assert fc._sprint_glyph(frame)[1] is not None


@pytest.mark.parametrize('red,hands', [('digits', True), ('bar', True), (None, True), ('cross', False)])
def test_nearby_red_is_not_a_cross_and_empty_ring_is_not_a_clock(red, hands):
    assert fc._sprint_glyph(draw_clock(canvas(), red=red, hands=hands)) == (None, None)


@pytest.mark.parametrize('location', ['currency', 'effect'])
def test_even_clock_like_artwork_outside_control_slot_is_not_sprint(location):
    m = fc.manifest()
    x, y, w, h = m['flow_regions']['sprint']
    sx, sy, sw, sh = m['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cy = (y+sy)//2 if location == 'currency' else (sy+sh+y+h)//2
    assert fc._sprint_glyph(draw_clock(canvas(), centre=(sx+sw//2, cy))) == (None, None)


def test_white_faceted_currency_with_red_nearby_is_not_a_clock():
    frame = canvas()
    x, y, w, h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cx, cy = x+w//2, y+h//2
    diamond = np.array([(cx, cy-25), (cx+25, cy), (cx, cy+25), (cx-25, cy)])
    cv2.polylines(frame, [diamond], True, (255, 255, 255), 3)
    cv2.line(frame, (cx-25, cy), (cx+25, cy), (255, 255, 255), 3)
    cv2.line(frame, (cx, cy-25), (cx, cy+25), (255, 255, 255), 3)
    cv2.putText(frame, '99', (cx+18, cy-12), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 255), 2)
    assert fc._sprint_glyph(frame) == (None, None)


def test_effect_arcs_with_red_highlights_do_not_make_a_clock():
    frame = canvas()
    x, y, w, h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    for radius in (16, 23, 30):
        cv2.ellipse(frame, (x+w//2, y+h//2), (radius, radius), 0, 20, 150, (255,255,255), 3)
    cv2.line(frame, (x+w//2, y), (x+w, y+h), (0,0,255), 5)
    assert fc._sprint_glyph(frame) == (None, None)


def test_location_is_resolved_from_current_manifest(monkeypatch):
    original = draw_clock(canvas())
    moved = deepcopy(fc.manifest())
    moved['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect'][1] += 150
    monkeypatch.setattr(fc, 'manifest', lambda: moved)
    assert fc._sprint_glyph(original) == (None, None)
    assert fc._sprint_glyph(draw_clock(canvas()))[1] is not None


@pytest.mark.parametrize('factor', [.5, 1.5])
def test_structural_identity_tracks_scaled_native_geometry(monkeypatch, factor):
    from player.geometry import Display, scale_manifest
    original = draw_clock(canvas())
    reference = fc.reference_manifest()
    native = reference['layout']
    display = Display(round(native['width']*factor), round(native['height']*factor), native['dpi'])
    scaled = scale_manifest(reference, display, profile={})
    monkeypatch.setattr(fc, 'manifest', lambda: scaled)
    image = cv2.resize(original, (display.width, display.height), interpolation=cv2.INTER_NEAREST)
    assert fc._sprint_glyph(image)[1] is not None


def test_frame_geometry_mismatch_is_not_absence():
    with pytest.raises(RuntimeError, match='frame geometry changed'):
        fc._sprint_glyph(canvas()[:-1])


def test_stability_ignores_dark_background_but_not_added_glyph_pixels():
    first = fc._sprint_glyph(draw_clock(canvas()))
    crop, point = first
    background = crop.copy()
    background[np.max(background, axis=2) == 0] = (30, 15, 25)
    assert fc._same_sprint_control(first, (background, point))
    added = crop.copy()
    added[crop.max(axis=2) == 0] = (255,255,255)
    assert not fc._same_sprint_control(first, (added, point))


def test_multiple_complete_candidates_are_an_error(monkeypatch):
    m = deepcopy(fc.manifest())
    x, y, w, h = m['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    m['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect'][3] = 200
    # Slot expansion here tests ambiguity, not a display scaling change.
    monkeypatch.setattr(fc, 'manifest', lambda: m)
    reference = deepcopy(fc.reference_manifest())
    reference['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect'][3] = 200
    monkeypatch.setattr(fc, 'reference_manifest', lambda: reference)
    frame = draw_clock(canvas(), centre=(x+w//2, y+35))
    draw_clock(frame, centre=(x+w//2, y+130))
    with pytest.raises(RuntimeError, match='Ambiguous Intro Sprint controls'):
        fc._sprint_glyph(frame)


@pytest.fixture
def flow(monkeypatch):
    from runtime import operations
    monkeypatch.setattr(operations, 'environment_operation', lambda: (None, 'sprint-op'))
    empty, present, dialog = canvas(), draw_clock(canvas()), canvas()
    dialog[0,0,0] = 5
    state = {'setup_battle': {'id':'owned', 'operation_id':'sprint-op', 'status':'active'}}
    scanner = SimpleNamespace(state=state, cal=SimpleNamespace(), skipped=[], current=present,
                              frames=[], inputs=[], cuts=[], clock=0., stop_error=None)

    def check_stop():
        if scanner.stop_error:
            raise scanner.stop_error

    def grab(**kwargs):
        frame = scanner.frames.pop(0) if scanner.frames else scanner.current
        if int(frame[0,0,0]) in (1,2,4):
            frame = frame.copy()
            x,y,w,h = fc.manifest()['hud']['battle']['targets'][0]['rect']
            frame[y:y+h, x:x+w] = 0
        return frame

    def tap(x, y, **kwargs):
        scanner.inputs.append((x, y, kwargs))
        if kwargs['reason'] == 'flow: open intro sprint dialog':
            scanner.current = dialog
        elif kwargs['reason'] == 'flow: end intro sprint early':
            scanner.current = empty
        return {'dry_run':False}

    scanner.check_stop, scanner.grab, scanner.tap = check_stop, grab, tap
    scanner.pause = lambda seconds: setattr(scanner, 'clock', scanner.clock+seconds)
    scanner.progress = lambda *args, **kwargs: None
    monkeypatch.setattr(fc, 'time', SimpleNamespace(monotonic=lambda: scanner.clock))
    texts = {4:['ABORT'], 5:['END INTRO SPRINT EARLY'], 3:['GAME STATS'], 2:['TOURNAMENT']}
    from vision import textocr
    monkeypatch.setattr(textocr, 'read_lines', lambda frame, scale=2., **kwargs:
                        [(0,0,text) for text in texts.get(int(frame[0,0,0]), [])])
    monkeypatch.setattr(fc, '_row_buttons', lambda *args: ((200,1400,200,70), (600,1400,200,70)))
    f = fc._Flow(scanner)
    f._owned_battle_id = 'owned'
    f.wave = lambda frame: 1 if int(frame[0,0,0]) in (0,4) else None
    f.game_stats = lambda frame: int(frame[0,0,0]) == 3
    f.in_tournament = lambda frame: int(frame[0,0,0]) == 2
    f.cut_crop = lambda *args, **kwargs: scanner.cuts.append(args[0])
    f.capture = lambda *args, **kwargs: scanner.cuts.append(args[0])
    f.skip = lambda target, reason, **kwargs: scanner.skipped.append((target,reason,kwargs))
    monkeypatch.setattr(fc, '_have_templates', lambda rels: set())
    return f, scanner, empty, present


def test_stable_sprint_opens_confirms_and_proves_clear_on_two_frames(flow):
    f, scanner, _, _ = flow
    fc.capture_intro_sprint(f)
    assert [args[2]['reason'] for args in scanner.inputs] == [
        'flow: open intro sprint dialog', 'flow: end intro sprint early']
    assert scanner.inputs[0][2]['deadline'] > 0
    assert scanner.cuts == ['icons/intro_sprint.png', 'home/intro_sprint_dialog.png', 'home/intro_sprint_yes.png']
    assert scanner.clock == pytest.approx(.9)  # presence .5s, then two clear frames .4s apart
    assert f._sprint_proof is None  # consumed; never reusable by another dispatch


def test_genuinely_unobserved_sprint_remains_optional(flow):
    f, scanner, empty, _ = flow
    scanner.current = empty
    fc.capture_intro_sprint(f)
    assert scanner.inputs == []
    assert len(scanner.skipped) == 1 and scanner.skipped[0][2]['optional'] is True


@pytest.mark.parametrize('change', ['disappear', 'move', 'glyph'])
def test_control_change_during_observation_aborts_without_optional_skip(flow, change):
    f, scanner, empty, present = flow
    x, y, w, h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cx, cy = x+w//2, y+h//2
    changed = empty if change == 'disappear' else draw_clock(canvas(), centre=(cx+4,cy))
    if change == 'glyph':
        changed = present.copy()
        cv2.line(changed, (cx-10,cy+8), (cx+5,cy+8), (255,255,255), 3)
    scanner.frames = [present, changed]
    with pytest.raises(RuntimeError, match='identity changed'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == [] and scanner.skipped == []


def test_control_change_on_final_fresh_capture_never_dispatches(flow):
    f, scanner, empty, present = flow
    scanner.frames = [present, present, empty]
    with pytest.raises(RuntimeError, match='changed before dispatch'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == [] and scanner.cuts == []


@pytest.mark.parametrize('screen', [1,2,4])
def test_unknown_tournament_and_abort_frames_never_dispatch(flow, screen):
    f, scanner, _, present = flow
    unsafe = present.copy()
    unsafe[0,0,0] = screen
    scanner.frames = [present, present, unsafe]
    with pytest.raises(RuntimeError, match='scene changed'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_another_operations_battle_cannot_be_used(flow):
    f, scanner, _, _ = flow
    scanner.state['setup_battle']['operation_id'] = 'someone-else'
    with pytest.raises(RuntimeError, match='active setup battle'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_stop_before_fresh_dispatch_sends_no_input(flow):
    from player.calibrate import Stopped
    f, scanner, _, present = flow
    original = scanner.grab

    def stop_on_fresh(**kwargs):
        scanner.stop_error = Stopped('stop before Sprint input')
        return original(**kwargs)

    scanner.frames = [present, present]
    f.progress = lambda *a, **k: setattr(scanner, 'grab', stop_on_fresh)
    with pytest.raises(Stopped):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_expired_fresh_proof_never_dispatches(flow):
    f, scanner, _, _ = flow
    f.progress = lambda *args, **kwargs: setattr(scanner, 'clock', scanner.clock+11)
    with pytest.raises(TimeoutError, match='deadline expired'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_slow_capture_is_counted_in_fresh_proof_age(flow):
    f, scanner, _, _ = flow
    original = scanner.grab

    def slow_capture(**kwargs):
        scanner.clock += 3
        return original(**kwargs)

    f.progress = lambda *a, **k: setattr(scanner, 'grab', slow_capture)
    with pytest.raises(TimeoutError, match='deadline expired'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_open_intent_without_stable_observation_cannot_dispatch(flow):
    f, scanner, _, present = flow
    point = fc._sprint_glyph(present)[1]
    with pytest.raises(RuntimeError, match='No stable Intro Sprint'):
        f.tap(*point, 'open intro sprint dialog')
    assert scanner.inputs == []


def test_battle_identity_change_between_observation_and_dispatch_is_refused(flow):
    f, scanner, _, _ = flow
    f.progress = lambda *a, **k: scanner.state['setup_battle'].update(id='different')
    with pytest.raises(RuntimeError, match='active setup battle'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


@pytest.mark.parametrize('screen', [1,2,4])
def test_unrecognized_post_confirmation_screen_does_not_prove_sprint_clear(flow, screen):
    f, scanner, empty, _ = flow
    unsafe = empty.copy()
    unsafe[0,0,0] = screen
    original = scanner.tap

    def post_confirm_change(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs['reason'] == 'flow: end intro sprint early':
            scanner.current = unsafe
        return result

    scanner.tap = post_confirm_change
    with pytest.raises(RuntimeError):
        fc.capture_intro_sprint(f)
    assert len(scanner.inputs) == 2


@pytest.mark.parametrize('radius', [18, 24, 28])
def test_filled_white_disk_and_x_is_not_a_stopwatch(radius):
    frame = draw_clock(canvas(), radius=radius)
    x,y,w,h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cv2.circle(frame, (x+w//2,y+h//2), radius, (255,255,255), -1)
    assert fc._sprint_glyph(frame)[1] is None


def test_filled_disk_does_not_authorize_open_input(flow):
    f, scanner, _, present = flow
    x,y,w,h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cv2.circle(present, (x+w//2,y+h//2), 24, (255,255,255), -1)
    fc.capture_intro_sprint(f)
    assert scanner.inputs == [] and scanner.skipped[0][2]['optional']


def test_optional_absence_finishes_cleanly_if_owned_run_dies(flow):
    f, scanner, empty, _ = flow
    stats = empty.copy()
    stats[0,0,0] = 3
    scanner.frames = [empty, stats]
    scanner.current = stats
    fc.capture_intro_sprint(f)
    assert scanner.inputs == [] and scanner.skipped[0][2]['optional']
    assert 'GAME STATS' in scanner.skipped[0][1]


def test_observed_control_lost_to_results_still_aborts(flow):
    f, scanner, empty, present = flow
    stats = empty.copy()
    stats[0,0,0] = 3
    scanner.frames = [present, stats]
    with pytest.raises(RuntimeError, match='recognized normal battle'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == [] and scanner.skipped == []


def test_foreign_results_do_not_become_optional(flow):
    f, scanner, empty, _ = flow
    scanner.current = empty.copy()
    scanner.current[0,0,0] = 3
    scanner.state['setup_battle']['operation_id'] = 'someone-else'
    with pytest.raises(RuntimeError, match='active setup battle'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == [] and scanner.skipped == []


def test_optional_deadline_reaches_actual_ocr_boundary_and_timeout_is_not_absence(flow, monkeypatch):
    from vision import textocr
    f, scanner, empty, _ = flow
    scanner.current = empty
    calls = []

    def timeout_read(frame, scale, *, timeout, raise_on_error):
        calls.append((timeout,raise_on_error))
        scanner.clock += timeout
        raise subprocess.TimeoutExpired('simulated OCR', timeout)

    monkeypatch.setattr(textocr, 'read_lines', timeout_read)
    with pytest.raises(subprocess.TimeoutExpired):
        fc.capture_intro_sprint(f)
    assert calls == [(10.,True)]
    assert scanner.clock == 10 and scanner.inputs == [] and scanner.skipped == []


def test_partial_ocr_cost_reduces_next_call_budget(flow, monkeypatch):
    from vision import textocr
    f, scanner, empty, _ = flow
    scanner.current = empty
    calls = []

    def read(frame, scale, *, timeout, raise_on_error):
        calls.append(timeout)
        scanner.clock += min(3, timeout)
        if timeout <= 3:
            raise subprocess.TimeoutExpired('simulated OCR', timeout)
        return []

    monkeypatch.setattr(textocr, 'read_lines', read)
    with pytest.raises(subprocess.TimeoutExpired):
        fc.capture_intro_sprint(f)
    assert calls == [10.,6.5,3.]
    assert scanner.clock == 10 and scanner.skipped == []


def test_late_ocr_completion_cannot_return_usable_evidence(flow, monkeypatch):
    from vision import textocr
    f, scanner, empty, _ = flow

    def late(frame, scale, *, timeout, raise_on_error):
        scanner.clock += timeout+1
        return [(0,0,'GAME STATS')]

    monkeypatch.setattr(textocr, 'read_lines', late)
    with f.read_budget(2), pytest.raises(TimeoutError, match='deadline expired'):
        f.lines_text(empty)
    assert scanner.inputs == []


def test_slow_semantics_outside_fresh_fence_still_allow_positive_sprint(flow, monkeypatch):
    from vision import textocr
    f, scanner, _, _ = flow
    original_read, original_tap = textocr.read_lines, f.tap
    in_dispatch = [False]

    def read(frame, scale, *, timeout, raise_on_error):
        assert not in_dispatch[0], 'Fresh dispatch must not run OCR'
        assert raise_on_error and 0 < timeout <= 10
        scanner.clock += 2.5
        return original_read(frame, scale, timeout=timeout, raise_on_error=raise_on_error)

    def tap(x,y,reason):
        in_dispatch[0] = reason == 'open intro sprint dialog'
        try:
            return original_tap(x,y,reason)
        finally:
            in_dispatch[0] = False

    monkeypatch.setattr(textocr, 'read_lines', read)
    f.tap = tap
    fc.capture_intro_sprint(f)
    assert len(scanner.inputs) == 2


def test_changed_scene_anchor_refuses_unchanged_sprint(flow):
    f, scanner, _, present = flow
    changed = present.copy()
    x,y,w,h = fc.manifest()['hud']['battle']['targets'][0]['rect']
    changed[y:y+h,x:x+w] = 0
    scanner.frames = [present,present,changed]
    with pytest.raises(RuntimeError, match='scene changed'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_batch_read_caps_timeout_and_preserves_errors(flow, monkeypatch):
    from vision import textocr
    f, scanner, _, present = flow
    calls = []

    def read(crops, **kwargs):
        calls.append(kwargs)
        raise OSError('OCR engine failed')

    monkeypatch.setattr(textocr, 'read_texts', read)
    with f.read_budget(4), pytest.raises(OSError, match='OCR engine failed'):
        f.read_texts([present], timeout=25)
    assert calls == [{'scales':(2.,3.),'raise_on_error':True,'timeout':4.}]


def test_deadline_read_seam_reaches_locate_capture_and_wave_fallback(flow, monkeypatch):
    import settings
    from vision import textocr, wave_reader
    from player import battle_capture
    f, scanner, _, present = flow
    calls = []
    _,_,wave_width,wave_height = settings.CONFIG['rois']['wave_box']

    def read(frame, scale, *, timeout, raise_on_error):
        calls.append((scale,timeout,raise_on_error))
        text = '1' if frame.shape[:2] == (wave_height,wave_width) else 'BATTLE'
        return [(0,0,text)]

    monkeypatch.setattr(textocr, 'read_lines', read)
    monkeypatch.setattr(wave_reader, 'read_wave', lambda frame: None)
    monkeypatch.setattr(battle_capture, 'digit_glyphs', lambda *a: {'1':np.ones((1,1))})
    with f.read_budget(5):
        fc._Flow.locate(f,present,(0,0,200,200),'BATTLE')
        fc._Flow.capture(f,'test.png','ABSENT',(0,0,200,200),(20,20))
        fc._Flow.wave(f,present)
    assert len(calls) == 3 and all(timeout == 5 and strict for _,timeout,strict in calls)


def test_optional_capture_propagates_absolute_deadline(flow):
    f, scanner, empty, _ = flow
    deadlines=[]
    scanner.current = empty

    def capture(*, deadline):
        deadlines.append(deadline)
        assert deadline == 10
        return empty

    scanner.grab = capture
    fc.capture_intro_sprint(f)
    assert deadlines and all(deadline == 10 for deadline in deadlines)


def test_bounded_capture_refuses_a_reader_without_deadline_support(flow):
    f, scanner, empty, _ = flow
    calls=[]
    scanner.grab = lambda: calls.append(True) or empty
    with pytest.raises(RuntimeError, match='requires deadline support'):
        fc.capture_intro_sprint(f)
    assert calls == [] and scanner.inputs == [] and scanner.skipped == []


def test_deadline_override_applies_to_batched_and_line_reads(flow, monkeypatch):
    from vision import textocr
    f, scanner, _, present = flow
    original = f.ocr_deadline
    f.ocr_deadline = lambda deadline=None: min(3.,original(deadline) or 3.)
    calls=[]
    monkeypatch.setattr(textocr,'read_lines',lambda frame,scale,**kw: calls.append(kw) or [])
    monkeypatch.setattr(textocr,'read_texts',lambda crops,**kw: calls.append(kw) or [''])
    f.read_lines(present)
    f.read_texts([present],timeout=25)
    assert all(call['timeout']==3 and call['raise_on_error'] for call in calls)


def test_wrong_post_open_dialog_sends_no_confirmation(flow, monkeypatch):
    from vision import textocr
    f, scanner, _, _ = flow
    original = textocr.read_lines
    monkeypatch.setattr(textocr, 'read_lines', lambda frame,scale=2.,**kw:
                        [(0,0,'UNRELATED DIALOG')] if int(frame[0,0,0]) == 5
                        else original(frame,scale,**kw))
    with pytest.raises(RuntimeError, match='dialog was not verified'):
        fc.capture_intro_sprint(f)
    assert len(scanner.inputs) == 1 and scanner.cuts == []


def test_owned_results_after_yes_are_accepted(flow):
    f, scanner, empty, _ = flow
    stats = empty.copy()
    stats[0,0,0] = 3
    original = scanner.tap

    def tap(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs['reason'] == 'flow: end intro sprint early':
            scanner.current = stats
        return result

    scanner.tap = tap
    fc.capture_intro_sprint(f)
    assert len(scanner.inputs) == 2


def test_same_coordinates_changed_foreground_rejects_final_dispatch(flow):
    f, scanner, _, present = flow
    changed = present.copy()
    x,y,w,h = fc.manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    cv2.line(changed,(x+w//2-10,y+h//2+8),(x+w//2+8,y+h//2+8),(255,255,255),3)
    scanner.frames = [present,present,changed]
    with pytest.raises(RuntimeError, match='changed before dispatch'):
        fc.capture_intro_sprint(f)
    assert scanner.inputs == []


def test_saved_upgrade_verification_uses_per_flow_batch_reader(flow, monkeypatch, tmp_path):
    import settings
    from player import battle_capture
    f, scanner, _, _ = flow
    f.cal.entries = []
    f.cal.p = {}
    monkeypatch.setattr(settings,'template_path',lambda rel: tmp_path/rel)
    calls=[]
    f.read_texts = lambda crops, **kw: calls.append(kw) or ['label']

    def verify(crops, reader):
        assert reader([np.zeros((2,2,3),np.uint8)]) == ['label']
        return {}

    monkeypatch.setattr(battle_capture,'verify_stat_labels',verify)
    fc.verify_saved_upgrade_labels(f,{'damage'})
    assert calls == [{'scales':(2.,3.),'raise_on_error':True}]


def test_on_home_and_tier_use_flow_reader_under_deadline(flow, monkeypatch):
    from player import bootstrap
    from vision import textocr
    f, scanner, _, present = flow
    calls=[]
    monkeypatch.setattr(textocr,'read_lines',lambda frame,scale,**kw:
                        calls.append(kw) or [(0,0,'Tier 1')])
    monkeypatch.setattr(bootstrap,'screen_matches',lambda frame,lines,name: bool(lines))
    with f.read_budget(3):
        assert fc._Flow.on_home(f,present)
        assert fc._Flow.read_tier(f,present) == 1
    assert len(calls) == 2 and all(c['timeout']==3 and c['raise_on_error'] for c in calls)
