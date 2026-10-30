"""Contract cases of the recognition images and screen analysis (README rows R-03, R-04) and the later developer tools (R-05).

Families: recognition_images (GET /api/frame.png, POST/GET /api/template/<rel>, GET /api/artwork/<rel>), screen_analysis (POST start, GET
status, GET frame.png of an analysis operation) and recognition_later (the clicker routes and the manifest export).

The device is the world's adb stand-in answering the transport + exec conversation with a synthetic frame: a gradient with a button-like
block and a flat block, drawn here (no screenshot of any game or machine). A route that answers pixels is recorded as metadata
({binary: {content_type, width, height, pixels_sha256}}): the PNG's bytes depend on the encoder, the decoded pixels do not.
"""
import copy
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from dashboard_contract_cases_recognition import calibration_folder, expected_context, write_report
from dashboard_contract_pages_world import raw_post
from test_dashboard_contract import put_image, raising

IMG = 'recognition_images'
FRAME, TEMPLATE, ARTWORK = '/api/frame.png', '/api/template/', '/api/artwork/'
NATIVE = (1080, 2560)
BUTTON = (100, 200, 300, 90)         # x, y, w, h of the button-like block (textured: the text is drawn on it)
FLAT = (600, 1000, 200, 200)         # a block of one grey (every channel equal: the crop's standard deviation is 0)


def synthetic_frame(width=NATIVE[0], height=NATIVE[1]):
    """A frame with edges (a gradient, a button with text) and a flat block; drawn, never captured."""
    frame = np.zeros((height, width, 3), np.uint8)
    ramp = np.linspace(20, 120, width).astype(np.uint8)
    frame[:, :, 0] = ramp
    frame[:, :, 1] = ramp[::-1]
    frame[:, :, 2] = 50
    x, y, w, h = BUTTON
    cv2.rectangle(frame, (x, y), (x + w, y + h), (230, 200, 60), -1)
    for number in range(5):      # letter-like bars (exact integer drawing: no anti-aliased text, so the pixels are the same on every build)
        cv2.rectangle(frame, (x + 20 + number * 50, y + 20), (x + 20 + number * 50 + 14 + 6 * number, y + h - 20), (255, 255, 255), -1)
    x, y, w, h = FLAT
    frame[y:y + h, x:x + w] = (128, 128, 128)
    return frame


def png_of(frame):
    ok, encoded = cv2.imencode('.png', frame)
    assert ok
    return encoded.tobytes()


def serve_frames(w, png=None, *, raw=None):
    """The adb stand-in answers the screencap exec with `png` (or the raw bytes a case gives); every other exec prints nothing
    (the display discovery sees a single display)."""
    original = w.adb.reply

    def reply(payload):
        if payload.startswith('host:transport:'):
            w.adb.requests.append(payload)
            return b'OKAY'
        if payload.startswith('exec:'):
            w.adb.requests.append(payload)
            return b'OKAY' + ((raw if raw is not None else png) if payload[5:].startswith('screencap') else b'')
        return original(payload)
    w.adb.reply = reply


def binary(response, *, cache_control=False, pixels=True):
    """What a route that answers pixels says: the content type and the decoded size and pixels (never the encoded bytes).
    pixels=False for an image the route draws with anti-aliased text: its pixels depend on the drawing library's build."""
    data = response.get_data()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert image is not None, 'the route answered something that is not an image'
    height, width = image.shape[:2]
    info = dict(content_type=response.mimetype, width=width, height=height)
    if pixels:
        info['pixels_sha256'] = hashlib.sha256(image.tobytes()).hexdigest().upper()
    if cache_control:
        info['cache_control'] = response.headers.get('Cache-Control')
    return dict(binary=info)


def image_case(w, family, case, path, *, cache_control=False, pixels=True):
    """GET `path` that must answer an image: recorded as its metadata (status 200)."""
    response = w.client.get(path)
    assert response.status_code == 200 and response.mimetype == 'image/png', (case, response.status_code, response.get_data(as_text=True)[:200])
    w.gen.cases.setdefault(family, {})[case] = dict(request=dict(method='GET', path=path), status=200, body=binary(response, cache_control=cache_control, pixels=pixels))
    return response


def pin_provenance(w, screen='home'):
    """The cropper records where a crop came from. Recognition of the screen (OCR) is a controlled dependency; the record itself
    (a report entry and a learned rectangle) is the real writer, on the world's clock."""
    from player import bootstrap, calibrate, learned
    w.mp.setattr(bootstrap, 'recognize_screen', (lambda frame, lines=None, *, read=None: screen) if screen != 'raise' else raising(RuntimeError('OCR unavailable')))
    for module in (calibrate, learned):
        w.mp.setattr(module, 'time', w.clock)


def is_coded(reply, status, code):
    """The refusal a coded route answers: {ok: false, code, error}, with the status the case expects."""
    got = reply.get_json()
    assert reply.status_code == status and got['ok'] is False and got['code'] == code and got['error'], (reply.status_code, got)
    return got


def fetch(w, token, case=None, **query):
    """Fetch the frame under a token (the page does this every few seconds); recorded only when a case name is given."""
    path = f'{FRAME}?ts={token}' + ''.join(f'&{k}={v}' for k, v in query.items())
    if case:
        return image_case(w, IMG, case, path)
    response = w.client.get(path)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def box(x, y, w, h, **extra):
    return dict(dict(x=x, y=y, w=w, h=h), **extra)

def cropper_world(gen, name, *, screen='home', taps=True):
    """A world whose device shows the synthetic frame, an empty frame cache, and the provenance writer on the world's clock."""
    return gen.world(name, taps=taps)


def prepare(w, *, screen='home', frame=None):
    """What every image case needs: the device answers the frame, the frame cache is empty, a dropped connection starts no
    recovery thread, and the screen recognition of the cropper is a controlled stand-in."""
    from test_dashboard_contract import freeze_reconnect
    png = png_of(synthetic_frame() if frame is None else frame)
    serve_frames(w, png)
    w.mp.setattr(w.dash, '_FRAME_CACHE', {})
    freeze_reconnect(w)
    pin_provenance(w, screen)
    return png


def frame_cases(gen):
    with gen.world('img_frame') as w:
        prepare(w)
        got = image_case(w, IMG, 'frame_png_remembered_under_the_token', FRAME + '?ts=t1')
        assert binary(got)['binary']['width'] == 1080 and 't1' in w.dash._FRAME_CACHE
        image_case(w, IMG, 'frame_png_without_a_token_is_not_remembered', FRAME)
        assert list(w.dash._FRAME_CACHE) == ['t1']
        assert w.adb.requests.count('exec:screencap -p') == 2
        image_case(w, IMG, 'frame_png_of_a_display_the_page_names', FRAME + '?ts=t2&display=2')
        assert 'exec:screencap -p -d 2' in w.adb.requests
        image_case(w, IMG, 'frame_png_ignores_a_serial_the_page_names', FRAME + '?ts=t3&serial=emulator-5554')
        assert not any('emulator-5554' in request for request in w.adb.requests)
        refused_display = w.get(IMG, 'frame_png_display_that_is_not_a_number_400', FRAME + '?display=2;reboot')
        assert refused_display.status_code == 400
        assert not any('reboot' in request for request in w.adb.requests)
    with gen.world('img_frame_warning') as w:
        png = prepare(w)
        serve_frames(w, raw=b'[Warning] Multiple displays were found, using display 0\n' + png)
        image_case(w, IMG, 'frame_png_with_an_emulator_warning_before_the_pixels_is_stripped', FRAME + '?ts=t1')
    with gen.world('img_frame_garbage') as w:
        prepare(w)
        serve_frames(w, raw=b'the screen is off')
        got = w.get(IMG, 'frame_png_that_is_not_a_png_502', FRAME + '?ts=t1')
        assert got.status_code == 502 and not w.dash._FRAME_CACHE
    with gen.world('img_frame_down') as w:
        prepare(w)
        w.adb.server = ConnectionRefusedError
        got = w.get(IMG, 'frame_png_capture_failed_502', FRAME + '?ts=t1')
        assert got.status_code == 502 and not w.dash._FRAME_CACHE
    with gen.world('img_frame_offline') as w:
        prepare(w)
        w.adb.state = 'offline'
        original = w.adb.reply

        def reply(payload):
            if payload.startswith('host:transport:'):
                from dashboard_contract_world import fail
                return fail('device offline')
            return original(payload)
        w.adb.reply = reply
        got = w.get(IMG, 'frame_png_device_offline_502', FRAME + '?ts=t1')
        assert got.status_code == 502
    with gen.world('img_frame_cache') as w:
        prepare(w)
        for number in range(9):
            fetch(w, f'k{number}')
        assert sorted(w.dash._FRAME_CACHE) == [f'k{n}' for n in range(1, 9)]       # the oldest token was dropped (8 are kept)

def template_names_cases(gen):
    with gen.world('img_template_names') as w:
        prepare(w)
        fetch(w, 'f1')
        for case, rel, status in (('save_a_name_without_a_folder_400', 'retry.png', 400), ('save_a_name_that_is_not_a_png_400', 'buttons/retry.jpg', 400),
                                  ('save_a_name_with_a_drive_400', 'C:/retry.png', 400), ('save_a_name_that_climbs_out_of_templates_400', 'buttons/../../retry.png', 400),
                                  ('save_a_name_with_a_backslash_is_read_as_a_folder_separator', 'buttons\\retry.png', 200)):
            reply = w.post(IMG, case, TEMPLATE + rel, box(*BUTTON, ts='f1'))
            assert reply.status_code == status, (case, reply.status_code, reply.get_data(as_text=True)[:200])
        assert w.get(IMG, 'read_the_template_list_after_the_names', '/api/wizard/templates').get_json()['have'].count('buttons/retry.png') == 1


def template_save_cases(gen):
    rel = 'buttons/retry.png'
    with gen.world('img_template_save') as w:
        prepare(w)
        fetch(w, 'f1')
        saved = w.post(IMG, 'save_a_crop_of_the_cached_frame', TEMPLATE + rel, box(*BUTTON, ts='f1'))
        got = saved.get_json()
        assert saved.status_code == 200 and got['ok'] and got['rel'] == rel and (got['width'], got['height']) == BUTTON[2:] and got['screen'] == 'home', got
        read = image_case(w, IMG, 'read_the_saved_template', TEMPLATE + rel)
        assert (binary(read)['binary']['width'], binary(read)['binary']['height']) == BUTTON[2:]
        x, y, bw, bh = BUTTON      # the native crop, pixel for pixel: the saved file is exactly the box of the cached frame
        saved_pixels = cv2.imdecode(np.frombuffer(read.get_data(), np.uint8), cv2.IMREAD_UNCHANGED)
        assert np.array_equal(saved_pixels, synthetic_frame()[y:y + bh, x:x + bw])
        listed = w.get(IMG, 'read_the_template_list_after_the_save', '/api/wizard/templates').get_json()
        assert rel in listed['have']
        plan = w.get(IMG, 'read_the_scan_plan_after_the_save_shows_the_cut_screen_and_its_verification', '/api/wizard/scan-plan').get_json()
        target = next(t for step in plan['steps'] for t in step['targets'] if t['rel'] == rel)
        assert target['learned']['screen'] == 'home' and target['learned']['rect'] == list(BUTTON) and target['status'] == 'Verified on this emulator', target
        exists = w.post(IMG, 'save_over_an_existing_template_without_the_overwrite_flag_409', TEMPLATE + rel, box(*BUTTON, ts='f1'))
        assert exists.status_code == 409 and exists.get_json()['exists'] is True
        over = w.post(IMG, 'save_over_an_existing_template_with_overwrite', TEMPLATE + rel, box(*BUTTON, ts='f1', overwrite=True))
        assert over.status_code == 200
    with gen.world('img_template_fraction') as w:
        prepare(w)
        fetch(w, 'f1')
        got = w.post(IMG, 'save_a_box_with_fractions_is_truncated', TEMPLATE + rel, box(100.9, 200.2, 300.7, 90.4, ts='f1')).get_json()
        assert (got['width'], got['height']) == (300, 90)
    with gen.world('img_template_provenance') as w:
        prepare(w, screen='raise')
        fetch(w, 'f1')
        got = w.post(IMG, 'save_when_the_screen_cannot_be_recognised_still_saves_with_no_screen', TEMPLATE + rel, box(*BUTTON, ts='f1')).get_json()
        assert got['ok'] and got['screen'] is None
        plan = w.get(IMG, 'read_the_scan_plan_after_a_save_with_no_screen_learns_no_rectangle', '/api/wizard/scan-plan').get_json()
        target = next(t for step in plan['steps'] for t in step['targets'] if t['rel'] == rel)
        assert target['learned'] is None
    with gen.world('img_template_provenance_fails') as w:
        from player import calibrate
        prepare(w)
        fetch(w, 'f1')
        w.mp.setattr(calibrate, 'record_manual_cut', raising(OSError('the report cannot be written')))
        got = w.post(IMG, 'save_when_the_provenance_cannot_be_recorded_the_file_is_still_written_ok_true', TEMPLATE + rel, box(*BUTTON, ts='f1')).get_json()
        assert got['ok'] is True
        plan = w.get(IMG, 'read_the_scan_plan_after_the_provenance_failed_nothing_is_verified', '/api/wizard/scan-plan').get_json()
        target = next(t for step in plan['steps'] for t in step['targets'] if t['rel'] == rel)
        assert target['status'] == 'Captured; recognition not yet verified'
    with gen.world('img_template_refused') as w:
        prepare(w)
        fetch(w, 'f1')
        count = lambda: len(list(Path(w.root).rglob(rel.rsplit('/', 1)[1])))   # noqa: E731
        assert count() == 0
        for case, body, status in (
                ('save_without_a_box_400', {}, 400), ('save_a_box_that_is_text_400', dict(x='a', y=1, w=10, h=10, ts='f1'), 400),
                ('save_a_box_with_a_missing_side_400', dict(x=1, y=1, w=10, ts='f1'), 400),
                ('save_a_box_that_is_null_400', dict(x=None, y=1, w=10, h=10, ts='f1'), 400),
                ('save_a_box_outside_the_frame_400', box(1000, 100, 200, 100, ts='f1'), 400),
                ('save_a_box_below_the_frame_400', box(100, 2500, 100, 100, ts='f1'), 400),
                ('save_a_box_with_a_negative_corner_400', box(-5, 100, 100, 100, ts='f1'), 400),
                ('save_a_box_smaller_than_6_pixels_400', box(100, 200, 5, 90, ts='f1'), 400),
                ('save_a_box_with_no_area_400', box(100, 200, 0, 0, ts='f1'), 400),
                ('save_a_flat_colour_box_400', box(*FLAT, ts='f1'), 400),
                ('save_without_a_frame_token_409', box(*BUTTON), 409),
                ('save_from_a_frame_token_that_was_never_fetched_409', box(*BUTTON, ts='never'), 409)):
            reply = w.post(IMG, case, TEMPLATE + rel, body)
            assert reply.status_code == status, (case, reply.status_code, reply.get_data(as_text=True)[:200])
            assert count() == 0, f'{case} wrote a file'
        assert w.post(IMG, 'save_a_body_that_is_a_list_400', TEMPLATE + rel, [1, 2, 3, 4]).status_code == 400
        raw_post(w, IMG, 'save_a_body_that_is_not_json_400', TEMPLATE + rel, 'x=1', content_type='text/plain')
        assert count() == 0
    with gen.world('img_template_accounts') as w:
        from player import accounts
        library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}}, 'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

        def as_account(account):
            def change(cfg):
                cfg['accounts'] = copy.deepcopy(library)
                cfg['instances']['main']['account'] = account
            w.edit_config(change)
        as_account('alpha')
        prepare(w)
        fetch(w, 'a1')
        as_account('beta')      # the token was fetched for alpha: a crop of that screen must not become beta's template
        got = w.post(IMG, 'save_from_a_frame_fetched_for_another_account_409', TEMPLATE + rel, box(*BUTTON, ts='a1'))
        is_coded(got, 409, 'frame_context_changed')
        assert not accounts.template_path(w.root, w.cfg, rel).is_file()
        as_account('alpha')
        assert not accounts.template_path(w.root, w.cfg, rel).is_file()
        assert w.post(IMG, 'save_from_the_same_frame_back_on_the_account_it_was_fetched_for', TEMPLATE + rel, box(*BUTTON, ts='a1')).status_code == 200
        assert accounts.template_path(w.root, w.cfg, rel).is_file()
    with gen.world('img_template_serial') as w:
        prepare(w)
        fetch(w, 's1')
        w.edit_config(lambda cfg: cfg['instances']['main'].update(serial='fake:2'))
        is_coded(w.post(IMG, 'save_from_a_frame_fetched_for_another_emulator_409', TEMPLATE + rel, box(*BUTTON, ts='s1')), 409, 'frame_context_changed')
    with gen.world('img_template_expired') as w:
        prepare(w)
        for number in range(9):
            fetch(w, f'k{number}')
        got = w.post(IMG, 'save_from_a_frame_that_left_the_cache_409', TEMPLATE + rel, box(*BUTTON, ts='k0'))
        assert is_coded(got, 409, 'frame_expired') and 'no longer cached' in got.get_json()['error']
        assert w.post(IMG, 'save_from_the_newest_frame', TEMPLATE + rel, box(*BUTTON, ts='k8')).status_code == 200
    with gen.world('img_template_decode') as w:
        prepare(w)
        w.dash._remember_frame('broken', b'this is not an image')
        got = w.post(IMG, 'save_from_a_cached_frame_that_does_not_decode_500', TEMPLATE + rel, box(*BUTTON, ts='broken'))
        assert got.status_code == 500
    with gen.world('img_template_write') as w:
        prepare(w)
        fetch(w, 'f1')
        w.mp.setattr(cv2, 'imwrite', lambda *args, **kwargs: False)
        got = w.post(IMG, 'save_when_the_png_cannot_be_written_500', TEMPLATE + rel, box(*BUTTON, ts='f1'))
        assert got.status_code == 500 and 'could not write' in got.get_json()['error']
    with gen.world('img_template_lock') as w:
        prepare(w)
        fetch(w, 'f1')
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            got = w.post(IMG, 'save_action_lock_another_action_in_progress', TEMPLATE + rel, box(*BUTTON, ts='f1'))
            assert got.status_code == 409 and got.get_json()['code'] == 'action_lock'
        finally:
            w.dash._ACTION_LOCK.release()


def template_read_cases(gen):
    with gen.world('img_template_read') as w:
        w.get(IMG, 'read_a_template_that_was_not_captured_404', TEMPLATE + 'buttons/retry.png')
        w.get(IMG, 'read_a_template_name_that_is_not_allowed_403', TEMPLATE + 'retry.png')
        w.get(IMG, 'read_a_template_with_another_extension_403', TEMPLATE + 'buttons/retry.jpg')
        put_image(w, 'buttons/retry.png')
        got = image_case(w, IMG, 'read_a_captured_template', TEMPLATE + 'buttons/retry.png')
        assert (binary(got)['binary']['width'], binary(got)['binary']['height']) == (6, 8)

def library(w, images, *, mapping=None, name='lib1'):
    """An extracted asset library of the account (never shipped): index.json, an optional mapping.json and one PNG per image."""
    folder = calibration_folder(w) / 'asset_library' / name
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for image_name, file, kind, size in images:
        (folder / file).write_bytes(png_of(np.full((size, size, 3), 90 + size, np.uint8)))
        rows.append(dict(name=image_name, file=file, type=kind, width=size, height=size))
    (folder / 'index.json').write_text(json.dumps({'images': rows}), encoding='utf-8')
    if mapping is not None:
        (folder / 'mapping.json').write_text(json.dumps(mapping), encoding='utf-8')
    return folder


def artwork_cases(gen):
    with gen.world('img_artwork_none') as w:
        w.mp.setattr(w.dash, '_ARTWORK_INDEX', {})
        got = w.get(IMG, 'artwork_nothing_known_for_the_target_204', ARTWORK + 'cards/cash.png')
        assert got.status_code == 204
        got = image_case(w, IMG, 'artwork_a_text_badge_for_a_control_the_game_draws_as_text', ARTWORK + 'buttons/retry.png', cache_control=True, pixels=False)
        assert binary(got, pixels=False)['binary'] == dict(content_type='image/png', width=260, height=88)
        assert got.headers['Cache-Control'] == 'max-age=3600'
    with gen.world('img_artwork_library') as w:
        w.mp.setattr(w.dash, '_ARTWORK_INDEX', {})
        library(w, [('SecondWind', 'second_wind_big.png', 'Texture2D', 64), ('SecondWind', 'second_wind_sprite.png', 'Sprite', 40)])
        got = image_case(w, IMG, 'artwork_from_the_asset_library_prefers_the_sprite', ARTWORK + 'floaters/second_wind.png')
        assert (binary(got)['binary']['width']) == 40
        assert w.get(IMG, 'artwork_the_library_has_no_image_of_that_name_204', ARTWORK + 'cards/cash.png').status_code == 204
        badge = image_case(w, IMG, 'artwork_a_text_badge_even_when_a_library_exists', ARTWORK + 'buttons/retry.png', cache_control=True, pixels=False)
        assert badge.headers['Cache-Control'] == 'max-age=3600'
    with gen.world('img_artwork_mapping') as w:
        w.mp.setattr(w.dash, '_ARTWORK_INDEX', {})
        library(w, [('SecondWind', 'second_wind_sprite.png', 'Sprite', 40), ('Mapped', 'mapped.png', 'Sprite', 24)],
                mapping={'targets': {'floaters/second_wind.png': {'candidates': [{'file': 'missing.png'}, {'file': 'mapped.png'}]}}})
        got = image_case(w, IMG, 'artwork_from_the_manifest_mapping_wins_over_the_name', ARTWORK + 'floaters/second_wind.png')
        assert binary(got)['binary']['width'] == 24
    with gen.world('img_artwork_broken') as w:
        w.mp.setattr(w.dash, '_ARTWORK_INDEX', {})
        folder = library(w, [('SecondWind', 'second_wind_sprite.png', 'Sprite', 40)])
        (folder / 'index.json').write_text('{"images": [', encoding='utf-8')
        assert w.get(IMG, 'artwork_library_index_unreadable_204', ARTWORK + 'floaters/second_wind.png').status_code == 204
    with gen.world('img_artwork_file_missing') as w:
        w.mp.setattr(w.dash, '_ARTWORK_INDEX', {})
        folder = library(w, [('SecondWind', 'second_wind_sprite.png', 'Sprite', 40)])
        (folder / 'second_wind_sprite.png').unlink()
        assert w.get(IMG, 'artwork_indexed_file_missing_204', ARTWORK + 'floaters/second_wind.png').status_code == 204


def run_images_cases(gen):
    frame_cases(gen)
    template_names_cases(gen)
    template_save_cases(gen)
    template_read_cases(gen)
    artwork_cases(gen)

# --------------------------------------------------------------------------- screen analysis (R-04)
ANALYSIS = 'screen_analysis'
A_START, A_STATUS, A_FRAME = '/api/screen-analysis/start', '/api/screen-analysis/status', '/api/screen-analysis/frame.png'
MATCH = dict(rect=[100, 200, 300, 90], inliers=48, names=['SecondWind'], roles=['floaters/second_wind.png'], ambiguous=False,
             source='second_wind_sprite.png', sha256='b' * 64)
DONE = dict(status='done', screen=dict(name='home', candidates=['home']), width=1080, height=2560, tested=212, captured_at=1799999900.0,
            ocr=[dict(text='BATTLE', box=[400, 2200, 280, 80])],
            matches=[MATCH, dict(MATCH, rect=[110, 205, 296, 88], inliers=31, names=['SecondWindAlt'], roles=[], ambiguous=True, source='alt.png', sha256='c' * 64)])


def analysis(w, status='succeeded', result=None, *, png=True, error=None, key=None, sealed=True):
    """One screen analysis of this device, finished as `status`, with its result and screenshot sealed the way the worker
    seals them (operations/<id>/, sha256 in result_refs). `sealed=False` leaves the shared files only (what the worker is writing)."""
    context = w.context({})
    op, _ = w.store.accept('screen_analysis', context, {}, key)
    op = w.store.transition(op['id'], op['revision'], 'running')
    shared = Path(context['artifact_dir'])
    shared.mkdir(parents=True, exist_ok=True)
    folder = shared / 'operations' / op['id']
    folder.mkdir(parents=True, exist_ok=True)
    refs = {}
    if result is not None:
        raw = json.dumps(dict(result, operation_id=op['id'])).encode()
        (folder / 'screen_analysis.json' if sealed else shared / 'screen_analysis.json').write_bytes(raw)
        if sealed:
            refs['screen_analysis.json'] = dict(path=str(folder / 'screen_analysis.json'), revision=hashlib.sha256(raw).hexdigest())
    if png:
        raw = png_of(synthetic_frame())
        (folder / 'screen_analysis.png').write_bytes(raw)
        refs['screen_analysis.png'] = dict(path=str(folder / 'screen_analysis.png'), revision=hashlib.sha256(raw).hexdigest())
    if status == 'running':
        return w.store.get(op['id'])
    fields = dict(result_refs=refs)
    if error:
        fields['error'] = error
    if status == 'cancelled':
        op = w.store.transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
    return w.store.transition(op['id'], op['revision'], status, **fields)


def analysis_start_cases(gen):
    with gen.world('an_start') as w:
        body = dict(idempotency_key='an-1', expected_context=expected(w))
        accepted = w.post(ANALYSIS, 'start_accepted', A_START, body)
        got = accepted.get_json()
        assert accepted.status_code == 200 and got['ok'] and got['operation']['kind'] == 'screen_analysis', got['operation']['status']
        assert w.spawn.calls[-1][3:] == ['-m', 'player.screen_analysis', '--instance', 'main'] and len(w.spawn.calls) == 1
        retry = w.post(ANALYSIS, 'start_retry_with_the_same_key_answers_the_same_operation', A_START, body)
        assert retry.get_json()['operation_id'] == got['operation_id'] and len(w.spawn.calls) == 1
        other = w.post(ANALYSIS, 'start_same_key_with_another_command_is_refused', A_START, dict(body, note='another'))
        is_coded(other, 409, 'idempotency_key_reused')
        again = w.post(ANALYSIS, 'start_while_an_analysis_is_running_is_refused', A_START, dict(body, idempotency_key='an-2'))
        is_coded(again, 409, 'operation_active')
        assert len(w.spawn.calls) == 1
    with gen.world('an_start_nokey') as w:
        accepted = w.post(ANALYSIS, 'start_without_a_key_or_a_context_is_accepted', A_START, {})
        assert accepted.status_code == 200
    with gen.world('an_start_context') as w:
        wrong = dict(expected(w), serial='fake:other')
        got = w.post(ANALYSIS, 'start_context_changed_409', A_START, dict(idempotency_key='an-3', expected_context=wrong))
        is_coded(got, 409, 'context_changed')
        assert not w.spawn.calls
    with gen.world('an_start_owned') as w:
        w.running('observe', {}, script='calibrate.py', args=['--phases', 'c', '--observe'])
        w.dash._procs_refresh()
        got = w.post(ANALYSIS, 'start_while_another_operation_owns_the_device_409', A_START, dict(idempotency_key='an-4'))
        is_coded(got, 409, 'operation_active')
        assert not w.spawn.calls
    with gen.world('an_start_lock') as w:
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            got = w.post(ANALYSIS, 'start_action_lock_another_action_in_progress', A_START, dict(idempotency_key='an-5'))
            assert got.status_code == 409 and got.get_json()['code'] == 'action_lock'
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('an_start_bad') as w:
        raw_post(w, ANALYSIS, 'start_body_not_json_is_read_as_an_empty_command', A_START, 'x=1', content_type='text/plain')
        for case, data in (('start_body_that_is_a_list_400', '["x"]'), ('start_body_is_not_valid_json_400', '{"idempotency_key": ')):
            is_coded(raw_post(w, ANALYSIS, case, A_START, data), 400, 'invalid_request')
        assert len(w.spawn.calls) == 1      # only the text body above started anything
    with gen.world('an_start_failed') as w:
        w.spawn.fail = OSError('the interpreter could not be started')
        got = w.post(ANALYSIS, 'start_when_the_worker_cannot_be_started_is_a_text_500', A_START, dict(idempotency_key='an-6'))
        assert got.status_code == 500
        read = w.get(ANALYSIS, 'read_the_operation_of_the_failed_start', A_STATUS)
        w.spawn.fail = None
        retry = w.post(ANALYSIS, 'start_retry_of_a_failed_start_answers_the_failed_operation', A_START, dict(idempotency_key='an-6'))
        assert retry.status_code == 200 and retry.get_json()['operation']['status'] == 'failed' and not w.spawn.calls


def expected(w):
    return expected_context(w)

def analysis_status_cases(gen):
    sealed_done = dict(DONE, screenshot=dict(operation_id='x', path='screen_analysis.png', revision='d' * 64))
    with gen.world('an_status_shared') as w:
        got = w.get(ANALYSIS, 'status_idle_nothing_was_ever_analysed', A_STATUS).get_json()
        assert got == {'status': 'idle'}
        folder = calibration_folder(w)
        (folder / 'screen_analysis.json').write_text(json.dumps(dict(DONE)), encoding='utf-8')
        got = w.get(ANALYSIS, 'status_done_from_the_shared_file_of_an_untracked_analysis', A_STATUS).get_json()
        assert got['status'] == 'done' and len(got['matches']) == 2 and 'operation' not in got
        (folder / 'screen_analysis.json').write_text('{"status": "done", "matches": [', encoding='utf-8')
        assert w.get(ANALYSIS, 'status_corrupt_shared_file_reads_as_idle', A_STATUS).get_json() == {'status': 'idle'}
        (folder / 'screen_analysis.json').write_text(json.dumps(dict(status='error', message='Extract artwork first; the local library is missing or damaged')), encoding='utf-8')
        assert w.get(ANALYSIS, 'status_error_written_by_the_worker_in_the_shared_file', A_STATUS).get_json()['status'] == 'error'
    with gen.world('an_status_accounts') as w:
        library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}}, 'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

        def as_account(account):
            def change(cfg):
                cfg['accounts'] = copy.deepcopy(library)
                cfg['instances']['main']['account'] = account
            w.edit_config(change)
        as_account('alpha')
        (calibration_folder(w) / 'screen_analysis.json').write_text(json.dumps(dict(DONE)), encoding='utf-8')
        assert w.get(ANALYSIS, 'status_of_the_first_account', A_STATUS).get_json()['status'] == 'done'
        as_account('beta')
        assert w.get(ANALYSIS, 'status_of_the_second_account_shows_nothing_of_the_first', A_STATUS).get_json() == {'status': 'idle'}
    with gen.world('an_status_running') as w:
        op = analysis(w, 'running', None, png=False)
        got = w.get(ANALYSIS, 'status_running_before_the_worker_wrote_anything', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'running' and got['operation']['id'] == op['id']
        (calibration_folder(w) / 'screen_analysis.json').write_text(json.dumps(dict(status='running', completed=40, total=212, operation_id=op['id'],
                                                                                    message='Matching installed artwork against the captured screen')), encoding='utf-8')
        got = w.get(ANALYSIS, 'status_running_with_the_progress_of_the_matching', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'running' and (got['completed'], got['total']) == (40, 212)
    with gen.world('an_status_retained') as w:
        old = analysis(w, 'succeeded', DONE, sealed=True)
        (calibration_folder(w) / 'screen_analysis.json').write_text(json.dumps(dict(DONE, operation_id=old['id'])), encoding='utf-8')
        new = analysis(w, 'running', None, png=False)
        got = w.get(ANALYSIS, 'status_new_operation_running_does_not_show_the_previous_result', A_STATUS + '?operation_id=' + new['id']).get_json()
        assert got['status'] == 'running' and 'matches' not in got
        got = w.get(ANALYSIS, 'status_without_an_operation_reads_the_latest_of_the_context', A_STATUS).get_json()
        assert got['operation']['id'] == new['id'] and got['status'] == 'running'
    with gen.world('an_status_done') as w:
        op = analysis(w, 'succeeded', DONE)
        got = w.get(ANALYSIS, 'status_done_with_matches_and_an_ambiguous_one', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'done' and [m['ambiguous'] for m in got['matches']] == [False, True] and got['operation']['status'] == 'succeeded'
    with gen.world('an_status_nomatch') as w:
        op = analysis(w, 'succeeded', dict(DONE, matches=[], tested=212))
        got = w.get(ANALYSIS, 'status_done_without_any_match', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'done' and got['matches'] == []
    with gen.world('an_status_unknown_screen') as w:
        op = analysis(w, 'succeeded', dict(DONE, screen=dict(name='unknown', candidates=['guild', 'events']), matches=[]))
        got = w.get(ANALYSIS, 'status_done_on_a_screen_that_could_not_be_told_apart', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['screen']['name'] == 'unknown' and len(got['screen']['candidates']) == 2
    with gen.world('an_status_failed') as w:
        op = analysis(w, 'failed', dict(status='error', message='Extract artwork first; the local library is missing or damaged'), png=False,
                      error=dict(code='analysis_failed', message='Extract artwork first; the local library is missing or damaged'))
        got = w.get(ANALYSIS, 'status_failed_operation_is_an_error_with_its_message', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'error' and 'Extract artwork' in got['message']
    with gen.world('an_status_failed_silent') as w:
        op = analysis(w, 'failed', None, png=False)
        got = w.get(ANALYSIS, 'status_failed_without_a_message_says_it_did_not_complete', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'error' and got['message'] == 'Analysis did not complete'
    with gen.world('an_status_interrupted') as w:
        op = analysis(w, 'interrupted', dict(DONE), png=True, error=dict(code='worker_lost', message='Worker exited without a confirmed outcome'))
        got = w.get(ANALYSIS, 'status_interrupted_operation_is_an_error_even_with_a_result_in_the_file', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'error' and 'Worker exited' in got['message']
    with gen.world('an_status_unsealed') as w:
        op = analysis(w, 'succeeded', None, png=False)
        got = w.get(ANALYSIS, 'status_done_operation_whose_result_was_never_written_is_unavailable', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'unavailable'
    with gen.world('an_status_foreign') as w:
        op = analysis(w, 'succeeded', DONE)
        Path(op['result_refs']['screen_analysis.json']['path']).write_text(json.dumps(dict(DONE, operation_id=op['id'], tested=1)), encoding='utf-8')
        got = w.get(ANALYSIS, 'status_result_changed_after_it_was_sealed_is_unavailable', A_STATUS + '?operation_id=' + op['id']).get_json()
        assert got['status'] == 'unavailable'
    with gen.world('an_status_other') as w:
        is_coded(w.get(ANALYSIS, 'status_unknown_operation_409', A_STATUS + '?operation_id=no-such-operation'), 409, 'operation_unknown')
        run = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        is_coded(w.get(ANALYSIS, 'status_of_another_workflow_409', A_STATUS + '?operation_id=' + run['id']), 409, 'operation_kind_mismatch')


def analysis_frame_cases(gen):
    with gen.world('an_frame') as w:
        op = analysis(w, 'succeeded', DONE)
        got = image_case(w, ANALYSIS, 'frame_of_a_finished_analysis_by_its_operation', A_FRAME + '?operation_id=' + op['id'] + '&revision=' + str(op['revision']))
        assert binary(got)['binary']['height'] == 2560
        again = image_case(w, ANALYSIS, 'frame_ignores_a_revision_the_page_holds_that_is_not_the_operations', A_FRAME + '?operation_id=' + op['id'] + '&revision=1')
        assert again.get_data() == got.get_data()
        latest = image_case(w, ANALYSIS, 'frame_without_an_operation_is_the_latest_of_the_context', A_FRAME)
        assert latest.get_data() == got.get_data()
    with gen.world('an_frame_running') as w:
        op = analysis(w, 'running', None, png=False)
        got = w.get(ANALYSIS, 'frame_of_an_unfinished_analysis_409', A_FRAME + '?operation_id=' + op['id'])
        assert is_coded(got, 409, 'analysis_not_finished')['operation']['id'] == op['id']
    with gen.world('an_frame_unsealed') as w:
        op = analysis(w, 'succeeded', DONE, png=False)
        got = w.get(ANALYSIS, 'frame_of_an_analysis_with_no_saved_screenshot_409', A_FRAME + '?operation_id=' + op['id'])
        is_coded(got, 409, 'artifact_unavailable')
    with gen.world('an_frame_changed') as w:
        op = analysis(w, 'succeeded', DONE)
        Path(op['result_refs']['screen_analysis.png']['path']).write_bytes(png_of(np.zeros((20, 20, 3), np.uint8)))
        got = w.get(ANALYSIS, 'frame_changed_after_it_was_sealed_409', A_FRAME + '?operation_id=' + op['id'])
        is_coded(got, 409, 'artifact_changed')
    with gen.world('an_frame_other') as w:
        is_coded(w.get(ANALYSIS, 'frame_of_an_unknown_operation_409', A_FRAME + '?operation_id=no-such-operation'), 409, 'operation_unknown')
        run = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        is_coded(w.get(ANALYSIS, 'frame_of_another_workflow_409', A_FRAME + '?operation_id=' + run['id']), 409, 'operation_kind_mismatch')
    with gen.world('an_frame_shared') as w:
        (calibration_folder(w) / 'screen_analysis.png').write_bytes(png_of(synthetic_frame()))
        image_case(w, ANALYSIS, 'frame_of_the_shared_file_when_no_operation_exists', A_FRAME)
    with gen.world('an_frame_none') as w:
        is_coded(w.get(ANALYSIS, 'frame_with_no_operation_and_no_file_404', A_FRAME), 404, 'no_screenshot')


def run_screens_cases(gen):
    run_images_cases(gen)
    analysis_start_cases(gen)
    analysis_status_cases(gen)
    analysis_frame_cases(gen)

# --------------------------------------------------------------------------- the later developer tools (R-05)
LATER = 'recognition_later'
CLICK_ACTIONS, CLICK_STEP, CLICK_FRAME, CONTRIBUTION = '/api/clicker/actions', '/api/clicker/step', '/api/clicker/frame', '/api/manifest/contribution'
VERIFIED = dict(ok=True, screen='home', target='home/tile_guild.png', rect=[40, 880, 160, 150], score=0.981, runner_up=0.412, search=[16, 856, 208, 198],
                position_source='shipped', reason='Target verified near its manifest position')


def worker(w, result=None, *, text=None, error=None):
    """The clicker worker (python player/clicker.py): its last stdout line is the JSON result. The dashboard runs it with
    subprocess.run; here the call is recorded and answers what the case gives (never started)."""
    from test_dashboard_contract import completed
    w.clicker_calls = []

    def run(args, **kwargs):
        w.clicker_calls.append(list(args))
        if error is not None:
            raise error
        return completed(stdout=text if text is not None else '\n'.join(['starting', json.dumps(result)]) + '\n')
    w.mp.setattr(w.dash, '_run', run)
    return w.clicker_calls


def step_body(w, action='guild', **extra):
    context = expected_context(w)
    return dict(dict(action=action, instance=context['instance'], account=context['account']), **extra)


def clicker_cases(gen):
    with gen.world('later_clicker_actions') as w:
        got = w.get(LATER, 'actions_listed', CLICK_ACTIONS).get_json()
        assert [a['id'] for a in got['actions']] == ['guild', 'events', 'presets', 'close_presets', 'cards_home', 'guild_home', 'reward_continue']
    with gen.world('later_clicker_step') as w:
        calls = worker(w, dict(VERIFIED, clicked=False, reason='Dry run: target verified, no click sent'))
        got = w.post(LATER, 'step_dry_run_verifies_the_target_and_clicks_nothing', CLICK_STEP, step_body(w)).get_json()
        assert got['ok'] is True and got['result']['clicked'] is False and '--execute' not in calls[-1] and calls[-1][-2:] == ['--action', 'guild']
        calls = worker(w, dict(ok=False, screen='cards', reason='Open home; current screen is cards'))
        got = w.post(LATER, 'step_the_check_refused_is_ok_true_outside_and_ok_false_inside', CLICK_STEP, step_body(w)).get_json()
        assert got['ok'] is True and got['result']['ok'] is False
        calls = worker(w, dict(ok=False, reason='Scan this control to learn its position first', screen='home'))
        w.post(LATER, 'step_position_not_learned_yet_nothing_to_click', CLICK_STEP, step_body(w, 'events'))
        calls = worker(w, text='')
        got = w.post(LATER, 'step_the_worker_printed_nothing', CLICK_STEP, step_body(w)).get_json()
        assert got == dict(ok=True, result=dict(ok=False, reason='Clicker worker returned no result'))
        calls = worker(w, text='not json at all\n')
        got = w.post(LATER, 'step_the_worker_printed_something_that_is_not_json_is_an_unknown_outcome', CLICK_STEP, step_body(w)).get_json()
        assert got['ok'] is False and 'Check did not finish' in got['error']
        import subprocess
        calls = worker(w, error=subprocess.TimeoutExpired(['clicker'], 45))
        got = w.post(LATER, 'step_the_worker_timed_out_is_an_unknown_outcome', CLICK_STEP, step_body(w)).get_json()
        assert got['ok'] is False and 'Inspect the game' in got['error']
        calls = worker(w, dict(VERIFIED, clicked=False, reason='Dry run: target verified, no click sent'))
        before = len(calls)
        for case, execute in (('step_execute_given_as_text_is_refused_400', 'true'), ('step_execute_given_as_a_number_is_refused_400', 1),
                              ('step_execute_given_as_null_is_refused_400', None)):
            is_coded(w.post(LATER, case, CLICK_STEP, step_body(w, execute=execute)), 400, 'invalid_execute')
        assert len(calls) == before
    with gen.world('later_clicker_execute') as w:
        calls = worker(w, dict(VERIFIED, clicked=True, destination='guild', reason='Click confirmed: guild'))
        got = w.post(LATER, 'step_execute_clicks_and_confirms_the_destination', CLICK_STEP, step_body(w, execute=True)).get_json()
        assert got['result']['clicked'] is True and got['result']['ok'] is True and '--execute' in calls[-1]
        calls = worker(w, dict(VERIFIED, ok=False, clicked=True, destination='unknown', reason='Click sent; destination not confirmed. Stopped for review.'))
        got = w.post(LATER, 'step_execute_clicked_but_the_destination_was_not_confirmed', CLICK_STEP, step_body(w, execute=True)).get_json()
        assert got['ok'] is True and got['result']['ok'] is False and got['result']['clicked'] is True
        calls = worker(w, dict(VERIFIED, ok=False, reason='Screen changed before the click; nothing clicked'))
        w.post(LATER, 'step_execute_the_screen_changed_before_the_click', CLICK_STEP, step_body(w, execute=True))
    with gen.world('later_clicker_taps_off', taps=False) as w:
        worker(w, dict(VERIFIED, clicked=False, reason='Dry run: target verified, no click sent'))
        assert w.post(LATER, 'step_dry_run_needs_no_tap_permission', CLICK_STEP, step_body(w)).status_code == 200
        calls = worker(w, dict(VERIFIED))
        got = w.post(LATER, 'step_execute_with_taps_off_409', CLICK_STEP, step_body(w, execute=True))
        is_coded(got, 409, 'taps_disabled')
        assert not calls
        is_coded(w.post(LATER, 'step_execute_given_as_text_with_taps_off_is_refused_400_before_the_taps_check', CLICK_STEP, step_body(w, execute='true')), 400, 'invalid_execute')
        assert not calls
    with gen.world('later_clicker_refused') as w:
        calls = worker(w, dict(VERIFIED))
        for case, body in (('step_unknown_action_400', step_body(w, 'teleport')), ('step_another_instance_400', dict(step_body(w), instance='other')),
                           ('step_another_account_400', dict(step_body(w), account='someone')), ('step_without_an_action_400', dict(instance='main', account=None)),
                           ('step_empty_body_400', {})):
            is_coded(w.post(LATER, case, CLICK_STEP, body), 400, 'invalid_request')
        raw_post(w, LATER, 'step_body_not_json_is_read_as_an_empty_command_400', CLICK_STEP, 'action=guild', content_type='text/plain')
        for case, data in (('step_body_that_is_a_list_400', '["guild"]'), ('step_body_is_not_valid_json_400', '{"action": ')):
            is_coded(raw_post(w, LATER, case, CLICK_STEP, data), 400, 'invalid_request')
        is_coded(w.post(LATER, 'step_action_that_is_not_text_400', CLICK_STEP, dict(step_body(w), action=['guild'])), 400, 'invalid_request')
        assert not calls
    with gen.world('later_clicker_owned') as w:
        calls = worker(w, dict(VERIFIED))
        w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        got = w.post(LATER, 'step_while_a_run_owns_the_device_409', CLICK_STEP, step_body(w))
        assert got.status_code == 409 and got.get_json()['code'] == 'device_owned' and not calls
    with gen.world('later_clicker_lock') as w:
        calls = worker(w, dict(VERIFIED))
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            got = w.post(LATER, 'step_action_lock_another_action_in_progress', CLICK_STEP, step_body(w))
            assert got.status_code == 409 and got.get_json()['code'] == 'action_lock' and not calls
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('later_clicker_frame') as w:
        is_coded(w.get(LATER, 'frame_preview_that_was_never_written_404', CLICK_FRAME), 404, 'no_preview')
        (calibration_folder(w) / 'clicker_preview.png').write_bytes(png_of(synthetic_frame()))
        got = image_case(w, LATER, 'frame_preview_written_by_the_clicker', CLICK_FRAME)
        assert binary(got)['binary']['width'] == 1080

def contribution(w, case):
    response = w.get(LATER, case, CONTRIBUTION)
    assert response.status_code == 200 and response.mimetype == 'application/json'
    assert response.headers['Content-Disposition'] == 'attachment; filename="manifest-contribution.json"'
    return response.get_json()


def contribution_cases(gen):
    entry = lambda rel, screen, rect, **extra: dict(dict(rel=rel, screen=screen, rect=rect, verified=True), **extra)    # noqa: E731
    with gen.world('later_manifest_empty') as w:
        got = contribution(w, 'manifest_contribution_without_a_report_has_no_targets')
        assert got['targets'] == [] and got['excluded_entries'] == 0 and got['requires_review'] is True and got['format'] == 'tower-pilot-manifest-contribution'
        assert got['layout'] == dict(width=1080, height=2560, dpi=360) and got['discovery_candidates'] == []
    with gen.world('later_manifest_report') as w:
        write_report(w, [
            entry('home/tile_guild.png', 'home', [40, 880, 160, 150], source_kind='installed_asset', confirmation=0.97),
            entry('home/battle_btn.png', 'home', [435, 2015, 235, 65], confirmation=0.5),
            entry('cards/active_label.png', 'cards', [422, 480, 240, 65]),
            dict(entry('modules/buy_module.png', 'modules', [420, 337, 290, 65]), verified=False),
            entry('invented/not_in_the_manifest.png', 'home', [10, 10, 50, 50]),
            entry('home/dissonant_run.png', 'cards', [402, 2175, 290, 65]),
            entry('home/tile_event.png', 'home', [10.5, 10, 50, 50]),
            entry('home/tile_event.png', 'home', [1000, 2500, 200, 200]),
            entry('home/tile_event.png', 'home', [0, 0, 0, 10]),
            dict(rel='home/tile_event.png', screen='home', verified=True)])
        got = contribution(w, 'manifest_contribution_exports_verified_geometry_and_counts_what_it_excluded')
        assert [(t['screen'], t['target'], t['verification']) for t in got['targets']] == [
            ('cards', 'cards/active_label.png', 'local_template_check'), ('home', 'home/battle_btn.png', 'local_template_check'),
            ('home', 'home/tile_guild.png', 'two_frame_capture')], got['targets']
        assert got['targets'][2]['artwork_names'] == ['Guild Icon'] and got['excluded_entries'] == 7
        text = json.dumps(got)
        assert 'image_sha256' not in text and 'png"' not in text.replace('.png', '') and 'operation' not in text.lower()
    with gen.world('later_manifest_corrupt') as w:
        (calibration_folder(w) / 'calibrate_report.json').write_text('{"entries": [', encoding='utf-8')
        got = contribution(w, 'manifest_contribution_with_a_corrupt_report_has_no_targets')
        assert got['targets'] == [] and got['excluded_entries'] == 0
        (calibration_folder(w) / 'calibrate_report.json').write_text('[]', encoding='utf-8')
        got = contribution(w, 'manifest_contribution_with_a_report_that_is_not_an_object_has_no_targets')
        assert got['targets'] == [] and got['excluded_entries'] == 0
        (calibration_folder(w) / 'calibrate_report.json').write_text(json.dumps({'entries': [1, None, 'x', {'rel': 'home/tile_guild.png', 'verified': True, 'screen': 'home', 'rect': 5}]}), encoding='utf-8')
        got = contribution(w, 'manifest_contribution_skips_report_entries_that_are_not_what_the_calibrator_writes')
        assert got['targets'] == [] and got['excluded_entries'] == 4
    with gen.world('later_manifest_candidates') as w:
        write_report(w, [])
        library(w, [('Guild Icon', 'guild.png', 'Sprite', 40), ('Hammer', 'hammer.png', 'Sprite', 30)])
        analysis_file = calibration_folder(w) / 'screen_analysis.json'
        matches = [dict(MATCH, names=['Guild Icon'], rect=[40, 880, 160, 150], inliers=48),
                   dict(MATCH, names=['Guild Icon'], rect=[40, 880, 160, 150], inliers=48, ambiguous=True),
                   dict(MATCH, names=['Hammer'], rect=[500, 500, 100, 100], inliers=5),
                   dict(MATCH, names=['Not In The Index'], rect=[500, 500, 100, 100], inliers=40),
                   dict(MATCH, names=['Hammer'], rect=[1000, 2500, 200, 200], inliers=40),
                   dict(MATCH, names=['Hammer'], rect=[500.5, 500, 100, 100], inliers=40),
                   dict(MATCH, names=['Hammer'], rect=[500, 500, 100, 100], inliers=40)]
        analysis_file.write_text(json.dumps(dict(DONE, screen='home', matches=matches)), encoding='utf-8')
        got = contribution(w, 'manifest_contribution_lists_discovery_candidates_from_a_done_analysis_of_a_known_screen')
        assert [c['artwork_names'] for c in got['discovery_candidates']] == [['Guild Icon'], ['Hammer']] and {c['screen'] for c in got['discovery_candidates']} == {'home'}
        analysis_file.write_text(json.dumps(dict(DONE, screen='some_new_dialog', matches=matches[:1])), encoding='utf-8')
        got = contribution(w, 'manifest_contribution_names_an_unknown_screen_unclassified')
        assert got['discovery_candidates'][0]['screen'] == 'unclassified'
        analysis_file.write_text(json.dumps(dict(DONE, status='running', screen='home', matches=matches[:1])), encoding='utf-8')
        assert contribution(w, 'manifest_contribution_ignores_an_analysis_that_is_not_done')['discovery_candidates'] == []
        analysis_file.write_text(json.dumps(dict(DONE, matches=matches[:1])), encoding='utf-8')     # the shape the analyzer really writes: screen is {name, candidates}
        got = contribution(w, 'manifest_contribution_with_the_screen_object_the_analyzer_writes_lists_candidates')
        assert [(c['screen'], c['artwork_names']) for c in got['discovery_candidates']] == [('home', ['Guild Icon'])]
        analysis_file.write_text(json.dumps(dict(DONE, screen=dict(name='unknown', candidates=['guild', 'events']), matches=matches[:1])), encoding='utf-8')
        got = contribution(w, 'manifest_contribution_with_a_screen_the_analyzer_could_not_tell_apart_is_unclassified')
        assert [c['screen'] for c in got['discovery_candidates']] == ['unclassified']
        analysis_file.write_text('[]', encoding='utf-8')
        assert contribution(w, 'manifest_contribution_with_an_analysis_that_is_not_an_object_has_no_candidates')['discovery_candidates'] == []
        analysis_file.write_text('{"status": "done", "matches": [', encoding='utf-8')
        assert contribution(w, 'manifest_contribution_with_a_corrupt_analysis_has_no_candidates')['discovery_candidates'] == []


def run_later_cases(gen):
    clicker_cases(gen)
    contribution_cases(gen)
