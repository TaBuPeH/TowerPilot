"""Regression tests for the page-contract defects F6-1..F6-8 (backend/logs/reviews/dashboard-contract-pages-report.json).

Every test drives the REAL routes of backend/dashboard.py over the isolated world of the page contract
(dashboard_contract_pages_world.PagesGenerator: a temporary root, ledger and lease folder, a fixed clock, an in-memory process
table, adb server and Popen); nothing touches the machine. Each test names the defect it pins and fails when its fix is reverted
(backend/logs/reviews/ui-backend-pages-defects-r9/mutation-results.json lists the mutants).
"""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from dashboard_contract_cases_calibrate import CAPTURE, apply_body, body_of, launched, seal
from dashboard_contract_cases_recognition import calibration_folder, expected_context, navigation_images, write_report
from dashboard_contract_cases_screens import BUTTON, DONE, analysis, box, fetch, png_of, prepare, step_body, synthetic_frame, worker
from dashboard_contract_pages_world import PagesGenerator, populate
from test_dashboard_contract import put_image

REL = 'buttons/retry.png'
TEMPLATE = '/api/template/' + REL
LIBRARY = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}}, 'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}


@pytest.fixture
def gen(dash, tmp_path, monkeypatch):
    return PagesGenerator(dash, tmp_path / 'world', monkeypatch)


def as_account(w, account):
    def change(cfg):
        cfg['accounts'] = copy.deepcopy(LIBRARY)
        cfg['instances']['main']['account'] = account
    w.edit_config(change)


def operations_recorded(w):
    with w.store._db() as db:
        return db.execute('SELECT count(*) FROM operations').fetchone()[0]


def nothing_started(w, before=0):
    return not w.spawn.calls and operations_recorded(w) == before


def post(w, path, body):
    return w.client.post(path, json=body)


def coded(reply, status, code):
    got = reply.get_json()
    assert reply.status_code == status and got['ok'] is False and got['code'] == code, (reply.status_code, got)
    assert isinstance(got['error'], str) and got['error']
    return got


# ------------------------------------------------------------------ F6-7: a frame token belongs to one page
def change_account(w):
    as_account(w, 'beta')


def change_instance(w):
    def change(cfg):
        cfg['instances']['second'] = dict(cfg['instances']['main'])
        cfg['active_instance'] = 'second'
    w.edit_config(change)


def change_profile(w):
    w.edit_config(lambda cfg: cfg['accounts']['alpha'].update(active_profile='other'))     # an account carries its own active profile


def change_serial(w):
    w.edit_config(lambda cfg: cfg['instances']['main'].update(serial='fake:2'))


@pytest.mark.parametrize('change', [change_account, change_instance, change_profile, change_serial],
                         ids=['account', 'instance', 'profile', 'serial'])
def test_a_crop_is_refused_from_a_frame_fetched_for_another_page(gen, change):
    with gen.world('frame_binding') as w:
        as_account(w, 'alpha')
        prepare(w)
        fetch(w, 'f1')
        config = (Path(w.root) / 'config.yaml').read_text(encoding='utf-8')
        change(w)
        got = coded(post(w, TEMPLATE, box(*BUTTON, ts='f1')), 409, 'frame_context_changed')
        assert 'refresh the live screen' in got['error']
        assert not list(Path(w.root).rglob('retry.png')), 'a crop of another page\'s frame was written'
        (Path(w.root) / 'config.yaml').write_text(config, encoding='utf-8')
        assert post(w, TEMPLATE, box(*BUTTON, ts='f1')).status_code == 200      # back on the page it was fetched for: the token still works


def test_a_crop_from_a_frame_that_is_gone_is_a_coded_409(gen):
    with gen.world('frame_expired') as w:
        prepare(w)
        got = coded(post(w, TEMPLATE, box(*BUTTON, ts='never')), 409, 'frame_expired')
        assert 'no longer cached' in got['error']
        coded(post(w, TEMPLATE, box(*BUTTON)), 409, 'frame_expired')


def test_a_frame_remembered_without_a_context_is_bound_to_the_current_page(gen):
    with gen.world('frame_default') as w:
        prepare(w)
        w.dash._remember_frame('d1', png_of(synthetic_frame()))
        assert post(w, TEMPLATE, box(*BUTTON, ts='d1')).status_code == 200
        w.dash._remember_frame('d2', png_of(synthetic_frame()))
        as_account(w, 'beta')
        coded(post(w, TEMPLATE, box(*BUTTON, ts='d2', overwrite=True)), 409, 'frame_context_changed')


# ------------------------------------------------------------------ F6-8: the provenance follows the dashboard's account
def test_the_cropper_provenance_is_read_from_the_dashboards_account_after_a_switch(gen):
    from player import accounts, calibrate
    with gen.world('provenance') as w:
        as_account(w, 'alpha')
        prepare(w)
        # calibrate.template_path resolves through settings.CONFIG, which a process reads once, when it imports it: a dashboard that
        # switched account since still holds the account it started with. (The stand-in resolves under that configuration; the
        # settings module itself is left alone, so nothing here can leak into an import-time binding of another test.)
        started_with = copy.deepcopy(w.cfg)
        w.mp.setattr(calibrate, 'template_path', lambda rel: accounts.template_path(w.root, started_with, rel, write=True))
        as_account(w, 'beta')
        fetch(w, 'f1')
        assert post(w, TEMPLATE, box(*BUTTON, ts='f1')).status_code == 200
        written = accounts.template_path(w.root, w.cfg, REL)
        assert written.is_file() and not accounts.template_path(w.root, started_with, REL).is_file()
        report = json.loads((calibration_folder(w) / 'calibrate_report.json').read_text(encoding='utf-8'))
        [entry] = [e for e in report['entries'] if e['rel'] == REL]
        assert entry['image_sha256'] == hashlib.sha256(written.read_bytes()).hexdigest()
        plan = w.client.get('/api/wizard/scan-plan').get_json()
        target = next(t for step in plan['steps'] for t in step['targets'] if t['rel'] == REL)
        assert target['status'] == 'Verified on this emulator'

# ------------------------------------------------------------------ F6-5 (manifest contribution): the analyzer's screen object
ANALYZED = dict(DONE, screen=dict(name='home', candidates=['home']))


def hit(**changes):
    return dict(dict(names=['SecondWind'], rect=[100, 200, 300, 90], inliers=48, ambiguous=False), **changes)


def test_discovery_candidates_read_the_screen_object_the_analyzer_writes():
    from player.manifest_contribution import discovery_candidates
    manifest = {'layout': {'width': 1080, 'height': 2560}, 'screens': {'home': {}}}
    known = {'SecondWind'}

    def screens_of(screen):
        return [c['screen'] for c in discovery_candidates(manifest, dict(status='done', screen=screen, matches=[hit()]), known)]
    assert screens_of(dict(name='home', candidates=['home'])) == ['home']
    assert screens_of(dict(name='unknown', candidates=['guild', 'events'])) == ['unclassified']
    assert screens_of(dict(candidates=['home'])) == ['unclassified']
    assert screens_of(['home']) == ['unclassified']
    assert screens_of(None) == ['unclassified']
    assert screens_of('home') == ['home']                      # the plain name the unit shape always allowed


@pytest.mark.parametrize('analysis', [[], 'text', 5, None, {'status': 'done', 'matches': 5}, {'status': 'done', 'matches': [1, None, 'x', {'names': 'SecondWind'}]},
                                     {'status': 'done', 'matches': [hit(names=[['x']]), hit(rect=5), hit(rect=[1, 2, 3]), hit(inliers='many')]}])
def test_discovery_candidates_skip_what_is_not_an_analysis(analysis):
    from player.manifest_contribution import discovery_candidates
    manifest = {'layout': {'width': 1080, 'height': 2560}, 'screens': {'home': {}}}
    assert discovery_candidates(manifest, analysis, {'SecondWind'}) == []


def test_the_manifest_contribution_route_answers_json_for_a_finished_analysis(gen):
    from dashboard_contract_cases_screens import library
    with gen.world('manifest') as w:
        library(w, [('SecondWind', 'sw.png', 'Sprite', 40)])
        (calibration_folder(w) / 'screen_analysis.json').write_text(json.dumps(dict(ANALYZED, matches=[hit()])), encoding='utf-8')
        reply = w.client.get('/api/manifest/contribution')
        assert reply.status_code == 200 and reply.mimetype == 'application/json'
        assert [(c['screen'], c['artwork_names']) for c in reply.get_json()['discovery_candidates']] == [('home', ['SecondWind'])]


@pytest.mark.parametrize('text', ['[]', '"text"', '5', '{"entries": 5}', '{"entries": [1, null, "x", {"rel": 5}, {"rel": "home/tile_guild.png", "verified": true, "rect": 5}]}'])
def test_the_manifest_contribution_route_survives_a_report_that_is_not_an_object_of_entries(gen, text):
    with gen.world('manifest_report') as w:
        (calibration_folder(w) / 'calibrate_report.json').write_text(text, encoding='utf-8')
        reply = w.client.get('/api/manifest/contribution')
        assert reply.status_code == 200 and reply.get_json()['targets'] == []


# ------------------------------------------------------------------ F6-2: a body that is not a JSON object
START, OBSERVE, APPLY = '/api/calibrate/start', '/api/calibrate/observe', '/api/calibrate/apply'
A_START, CLICK = '/api/screen-analysis/start', '/api/clicker/step'
NOT_OBJECTS = ['[1, 2]', '[]', '"text"', '5', '0', 'true', 'false', 'null', '{"a": ']


@pytest.mark.parametrize('route', [START, OBSERVE, APPLY, A_START, CLICK])
def test_a_body_that_claims_to_be_json_and_is_not_an_object_is_a_coded_400(gen, route):
    with gen.world('body_' + route.strip('/').replace('/', '_')) as w:
        calls = worker(w, {'ok': True})
        for raw in NOT_OBJECTS:
            reply = w.client.post(route, data=raw, content_type='application/json')
            got = coded(reply, 400, 'invalid_request')
            assert 'JSON' in got['error'], (raw, got)
            assert nothing_started(w) and not calls, f'{route} {raw!r} started something'


def test_a_start_body_must_be_json_even_when_it_does_not_say_so(gen):
    with gen.world('body_start_text') as w:
        coded(w.client.post(START, data='phases=c', content_type='text/plain'), 400, 'invalid_request')
        coded(w.client.post(START, data='[1]', content_type='text/plain'), 400, 'invalid_request')
        coded(w.client.post(START), 400, 'invalid_request')
        assert nothing_started(w)
        navigation_images(w, ['c'])
        assert w.client.post(START, data='{"phases": "c", "idempotency_key": "t1"}', content_type='text/plain').status_code == 200


def test_a_body_that_does_not_claim_to_be_json_is_still_an_empty_command_on_the_lenient_routes(gen):
    with gen.world('body_lenient') as w:
        assert w.client.post(OBSERVE, data='watch=30', content_type='text/plain').status_code == 200
        assert '--observe-watch' not in launched(w)
    with gen.world('body_lenient_analysis') as w:
        assert w.client.post(A_START, data='x=1', content_type='text/plain').status_code == 200
    with gen.world('body_lenient_apply') as w:
        coded(w.client.post(APPLY, data='x', content_type='text/plain'), 409, 'operation_required')


# ------------------------------------------------------------------ F6-3: observe watch and clicker execute
@pytest.mark.parametrize('watch', [-1, 601, 9000, 2.9, 30.0, '30', 'soon', True, False, [30], {'a': 1}])
def test_observe_refuses_a_watch_that_is_not_a_whole_number_of_seconds_from_0_to_600(gen, watch):
    with gen.world('watch_refused') as w:
        got = coded(post(w, OBSERVE, body_of(w, 'w1', watch=watch)), 400, 'invalid_watch')
        assert '0' in got['error'] and '600' in got['error']
        assert nothing_started(w)


def test_an_identical_retry_is_answered_from_its_receipt_before_the_watch_is_judged(gen):
    with gen.world('watch_replay') as w:
        stored = {'watch': 9000}      # a receipt from before watch was validated
        op, _ = w.store.accept('observe', w.context(stored), stored, 'old-key')
        reply = post(w, OBSERVE, dict(stored, idempotency_key='old-key'))
        assert reply.status_code == 200 and reply.get_json()['operation_id'] == op['id'] and not w.spawn.calls
        coded(post(w, OBSERVE, dict(stored, idempotency_key='new-key')), 400, 'invalid_watch')


@pytest.mark.parametrize('watch,flag', [(None, None), (0, None), (1, '1'), (30, '30'), (600, '600')])
def test_observe_accepts_a_watch_of_0_to_600_whole_seconds(gen, watch, flag):
    with gen.world('watch_accepted') as w:
        body = body_of(w, 'w2') if watch is None else body_of(w, 'w2', watch=watch)
        got = post(w, OBSERVE, body)
        assert got.status_code == 200
        assert (launched(w)[-2:] == ['--observe-watch', flag]) if flag else ('--observe-watch' not in launched(w))


@pytest.mark.parametrize('execute', ['true', 'false', 'yes', 1, 0, [], {}, None])
def test_the_clicker_step_needs_a_real_boolean_execute(gen, execute):
    for taps in (True, False):
        with gen.world(f'execute_{taps}', taps=taps) as w:
            calls = worker(w, {'ok': True})
            got = coded(post(w, CLICK, step_body(w, execute=execute)), 400, 'invalid_execute')
            assert 'true or false' in got['error'] and not calls


@pytest.mark.parametrize('action', [['guild'], {'a': 1}, 5, None, True])
def test_the_clicker_step_refuses_an_action_that_is_not_text_instead_of_failing(gen, action):
    with gen.world('action_not_text') as w:
        calls = worker(w, {'ok': True})
        coded(post(w, CLICK, dict(step_body(w), action=action)), 400, 'invalid_request')
        assert not calls


def test_the_clicker_step_clicks_only_for_a_boolean_true_and_only_with_taps_on(gen):
    with gen.world('execute_true') as w:
        calls = worker(w, {'ok': True, 'clicked': True})
        assert post(w, CLICK, step_body(w, execute=True)).status_code == 200 and '--execute' in calls[-1]
        assert post(w, CLICK, step_body(w, execute=False)).status_code == 200 and '--execute' not in calls[-1]
        assert post(w, CLICK, step_body(w)).status_code == 200 and '--execute' not in calls[-1]
    with gen.world('execute_taps_off', taps=False) as w:
        calls = worker(w, {'ok': True})
        coded(post(w, CLICK, step_body(w, execute=True)), 409, 'taps_disabled')
        assert not calls
        assert post(w, CLICK, step_body(w, execute=False)).status_code == 200 and post(w, CLICK, step_body(w)).status_code == 200
        assert all('--execute' not in call for call in calls)

# ------------------------------------------------------------------ F6-4: files that are not what the producer writes
GARBAGE = ['[]', '"text"', '5', 'null', '{"entries": 5}', '{"entries": [1, null, "x", {"rel": 5}, {"rel": "buttons/exit_battle.png", "verified": true}]}']


@pytest.mark.parametrize('text', GARBAGE)
def test_the_scan_plan_survives_a_report_that_is_not_an_object_of_entries(gen, text):
    with gen.world('plan_report') as w:
        put_image(w, 'buttons/exit_battle.png')
        write_report(w, text=text)
        reply = w.client.get('/api/wizard/scan-plan')
        assert reply.status_code == 200
        target = next(t for step in reply.get_json()['steps'] for t in step['targets'] if t['rel'] == 'buttons/exit_battle.png')
        assert target['status'] == 'Captured; recognition not yet verified'


@pytest.mark.parametrize('text', GARBAGE)
def test_the_scan_plan_function_survives_the_same_report(tmp_path, text):
    from player import accounts, scan_plan
    folder = accounts.calibration_dir(tmp_path, {})
    folder.mkdir(parents=True)
    (folder / 'calibrate_report.json').write_text(text, encoding='utf-8')
    assert scan_plan.plan(tmp_path, {})['total'] > 0


@pytest.mark.parametrize('name', ['calibrate_state.json', 'calibrate_report.json', 'module_manifest.json', 'card_manifest.json'])
@pytest.mark.parametrize('text', ['[]', '"text"', '5'])
def test_the_calibrate_status_survives_a_shared_file_that_is_not_an_object(gen, name, text):
    with gen.world('status_shared') as w:
        (calibration_folder(w) / name).write_text(text, encoding='utf-8')
        reply = w.client.get('/api/calibrate/status')
        assert reply.status_code == 200
        got = reply.get_json()
        assert got['state'] == {} or name != 'calibrate_state.json'
        assert got['report'] == {} and got['manifest'] == {} and got['card_manifest'] == {}


def test_a_frame_with_nothing_to_show_is_a_coded_json_answer(gen):
    with gen.world('nothing_to_show') as w:
        got = coded(w.client.get('/api/screen-analysis/frame.png'), 404, 'no_screenshot')
        assert 'screenshot' in got['error']
        got = coded(w.client.get('/api/clicker/frame'), 404, 'no_preview')
        assert 'preview' in got['error']
        (calibration_folder(w) / 'screen_analysis.png').write_bytes(png_of(synthetic_frame()))
        (calibration_folder(w) / 'clicker_preview.png').write_bytes(png_of(synthetic_frame()))
        assert w.client.get('/api/screen-analysis/frame.png').status_code == 200 and w.client.get('/api/clicker/frame').status_code == 200


def test_the_frame_of_an_analysis_operation_without_a_screenshot_is_a_coded_409(gen):
    with gen.world('analysis_frame') as w:
        running = analysis(w, 'running', None, png=False)
        coded(w.client.get('/api/screen-analysis/frame.png', query_string={'operation_id': running['id']}), 409, 'analysis_not_finished')
    with gen.world('analysis_frame_unsealed') as w:
        done = analysis(w, 'succeeded', DONE, png=False)
        coded(w.client.get('/api/screen-analysis/frame.png', query_string={'operation_id': done['id']}), 409, 'artifact_unavailable')
    with gen.world('analysis_frame_changed') as w:
        done = analysis(w, 'succeeded', DONE)
        Path(done['result_refs']['screen_analysis.png']['path']).write_bytes(png_of(np.zeros((20, 20, 3), np.uint8)))
        coded(w.client.get('/api/screen-analysis/frame.png', query_string={'operation_id': done['id']}), 409, 'artifact_changed')


# ------------------------------------------------------------------ F6-1: every refusal of the Recognition routes has a stable code
ROWS = {}


def row(name, *, taps=True):
    def register(fn):
        ROWS[name] = (fn, taps)
        return fn
    return register


def wrong_serial(w):
    return dict(expected_context(w), serial='fake:other')


def running_calibration(w):
    w.running('calibrate', {'bootstrap': True, 'phases': 'c'}, script='calibrate.py', args=['--phases', 'c', '--bootstrap'])
    w.dash._procs_refresh()


@row('start_key_reused')
def _(w):
    navigation_images(w, ['c', 'm'])
    assert post(w, START, body_of(w, 'k1', phases='c')).status_code == 200
    return post(w, START, body_of(w, 'k1', phases='c,m')), 409, 'idempotency_key_reused'


@row('start_context_changed')
def _(w):
    navigation_images(w, ['c'])
    return post(w, START, dict(expected_context=wrong_serial(w), idempotency_key='k2', phases='c')), 409, 'context_changed'


@row('start_calibration_running')
def _(w):
    running_calibration(w)
    navigation_images(w, ['c'])
    return post(w, START, body_of(w, 'k3', phases='c')), 409, 'calibration_running'


@row('start_taps_disabled', taps=False)
def _(w):
    navigation_images(w, ['c'])
    return post(w, START, body_of(w, 'k4', phases='c')), 409, 'taps_disabled'


@row('start_navigation_images_missing')
def _(w):
    reply = post(w, START, body_of(w, 'k5', phases='c,m'))
    assert 'home/battle_btn.png' in reply.get_json()['missing']
    return reply, 409, 'navigation_images_missing'


@row('start_modules_not_done')
def _(w):
    navigation_images(w, ['e'])
    return post(w, START, body_of(w, 'k6', phases='e')), 409, 'modules_not_done'


@row('start_invalid_phases')
def _(w):
    return post(w, START, body_of(w, 'k7', bootstrap=True, phases='c,z')), 400, 'invalid_phases'


@row('start_invalid_routes')
def _(w):
    return post(w, START, body_of(w, 'k8', bootstrap=True, phases='c', routes=['route_99'])), 400, 'invalid_routes'


@row('start_routes_need_bootstrap')
def _(w):
    return post(w, START, body_of(w, 'k9', routes=['route_0', 'route_1'], phases='c')), 400, 'invalid_routes'


@row('start_operation_active')
def _(w):
    navigation_images(w, ['c'])
    w.store.accept('observe', w.context({}), {}, 'held')               # an observation is accepted and has no process yet: it owns the device
    return post(w, START, body_of(w, 'k10', phases='c')), 409, 'operation_active'


@row('observe_context_changed')
def _(w):
    return post(w, OBSERVE, dict(expected_context=wrong_serial(w), idempotency_key='o1')), 409, 'context_changed'


@row('observe_key_reused')
def _(w):
    assert post(w, OBSERVE, body_of(w, 'o2')).status_code == 200
    return post(w, OBSERVE, body_of(w, 'o2', watch=30)), 409, 'idempotency_key_reused'


@row('observe_calibration_running')
def _(w):
    running_calibration(w)
    return post(w, OBSERVE, body_of(w, 'o3')), 409, 'calibration_running'


@row('apply_operation_required')
def _(w):
    return post(w, APPLY, {}), 409, 'operation_required'


@row('apply_revision_required')
def _(w):
    populate(w)
    return post(w, APPLY, dict(operation_id=seal(w)['id'])), 409, 'operation_required'


@row('apply_operation_unknown')
def _(w):
    return post(w, APPLY, dict(operation_id='no-such-operation', expected_revision=1)), 409, 'operation_unknown'


@row('apply_operation_changed')
def _(w):
    populate(w)
    op = seal(w)
    reply = post(w, APPLY, dict(operation_id=op['id'], expected_revision=op['revision'] - 1))
    assert reply.get_json()['operation']['id'] == op['id'] and reply.get_json()['operation']['status'] == 'awaiting_apply'
    return reply, 409, 'operation_changed'


@row('apply_not_a_calibration')
def _(w):
    scan, _ = w.store.accept('scan', w.context(), {})
    scan = w.store.transition(scan['id'], scan['revision'], 'running')
    scan = w.store.transition(scan['id'], scan['revision'], 'succeeded')
    return post(w, APPLY, apply_body(scan)), 409, 'not_a_calibration'


@row('apply_context_changed')
def _(w):
    populate(w)
    op = seal(w)
    w.edit_config(lambda cfg: cfg['instances']['main'].update(serial='fake:other'))
    return post(w, APPLY, apply_body(op)), 409, 'context_changed'


@row('apply_starter_profile')
def _(w):
    w.edit_config(lambda cfg: cfg.update(active_profile='default'))
    return post(w, APPLY, apply_body(seal(w))), 409, 'starter_profile'


@row('apply_profile_missing')
def _(w):
    w.edit_config(lambda cfg: cfg.update(active_profile='ghost'))
    return post(w, APPLY, apply_body(seal(w))), 400, 'profile_missing'


@row('apply_profile_changed')
def _(w):
    populate(w)
    op = seal(w)
    w.edit_profile(lambda profile: profile['blueprints']['farm'].update(tier=2))
    return post(w, APPLY, apply_body(op)), 409, 'profile_changed'


@row('apply_restoration_pending')
def _(w):
    populate(w)
    op = seal(w)
    (calibration_folder(w) / 'module_restore.json').write_text('{}', encoding='utf-8')
    return post(w, APPLY, apply_body(op)), 400, 'restoration_pending'


@row('apply_no_observations')
def _(w):
    populate(w)
    return post(w, APPLY, apply_body(seal(w, player={}))), 400, 'no_observations'


@row('apply_artifact_changed')
def _(w):
    populate(w)
    op = seal(w)
    Path(op['result_refs']['calibrate_state.json']['path']).write_bytes(b'{"operation_id": "x", "player": {"max_tier": 1}}')
    return post(w, APPLY, apply_body(op)), 409, 'artifact_changed'


@row('apply_artifact_unavailable')
def _(w):
    populate(w)
    op = seal(w)
    Path(op['result_refs']['calibrate_state.json']['path']).unlink()
    return post(w, APPLY, apply_body(op)), 409, 'artifact_unavailable'


@row('apply_artifact_invalid')
def _(w):
    populate(w)
    return post(w, APPLY, apply_body(seal(w, state=dict(operation_id='another-operation')))), 409, 'artifact_invalid'


@row('apply_observations_tracked')
def _(w):
    populate(w)
    seal(w)
    (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'player': CAPTURE}), encoding='utf-8')
    return post(w, APPLY, dict(saved_observations=True)), 409, 'observations_tracked'


@row('apply_write_failed')
def _(w):
    from test_dashboard_contract import raising
    populate(w)
    op = seal(w)
    w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('disk full')))
    reply = post(w, APPLY, apply_body(op))
    assert w.store.get(op['id'])['status'] == 'awaiting_apply'
    return reply, 400, 'apply_failed'


@row('apply_unexpected_failure')
def _(w):
    from test_dashboard_contract import raising
    populate(w)
    w.mp.setattr(w.dash, '_save_yaml_backup', raising(RuntimeError('unexpected')))
    return post(w, APPLY, apply_body(seal(w))), 500, 'apply_failed'


@row('analysis_key_reused')
def _(w):
    assert post(w, A_START, dict(idempotency_key='a1')).status_code == 200
    return post(w, A_START, dict(idempotency_key='a1', note='another')), 409, 'idempotency_key_reused'


@row('analysis_context_changed')
def _(w):
    return post(w, A_START, dict(idempotency_key='a2', expected_context=wrong_serial(w))), 409, 'context_changed'


@row('analysis_operation_active')
def _(w):
    assert post(w, A_START, dict(idempotency_key='a3')).status_code == 200
    return post(w, A_START, dict(idempotency_key='a4')), 409, 'operation_active'


@row('analysis_status_operation_unknown')
def _(w):
    return w.client.get('/api/screen-analysis/status', query_string={'operation_id': 'nope'}), 409, 'operation_unknown'


@row('analysis_status_other_workflow')
def _(w):
    run = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
    return w.client.get('/api/screen-analysis/status', query_string={'operation_id': run['id']}), 409, 'operation_kind_mismatch'


@row('analysis_frame_operation_unknown')
def _(w):
    return w.client.get('/api/screen-analysis/frame.png', query_string={'operation_id': 'nope'}), 409, 'operation_unknown'


@row('calibrate_status_operation_unknown')
def _(w):
    return w.client.get('/api/calibrate/status', query_string={'operation_id': 'nope'}), 409, 'operation_unknown'


@row('calibrate_status_other_workflow')
def _(w):
    run = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
    return w.client.get('/api/calibrate/status', query_string={'operation_id': run['id']}), 409, 'operation_kind_mismatch'


@pytest.mark.parametrize('name', sorted(ROWS))
def test_each_refusal_of_the_recognition_routes_has_a_stable_code(gen, name):
    fn, taps = ROWS[name]
    with gen.world('refusal', taps=taps) as w:
        reply, status, code = fn(w)
        got = coded(reply, status, code)
        assert set(got) <= {'ok', 'code', 'error', 'operation', 'missing'}, got


def test_the_refusal_messages_are_the_ones_the_routes_always_gave(gen):
    expected = {
        'start_context_changed': 'Selected account, device or profile changed before acceptance',
        'start_key_reused': 'Idempotency key already belongs to another command or target',
        'start_operation_active': 'An operation already owns this device',
        'start_calibration_running': 'a calibration is already running',
        'start_modules_not_done': 'Finish phase 1 Modules first.',
        'apply_operation_unknown': 'Unknown operation',
        'apply_operation_changed': 'Operation changed; refresh its status before trying again',
        'apply_profile_changed': 'Profile changed after preparation; discoveries remain saved for review',
        'apply_observations_tracked': 'These observations belong to an operation; apply its ID and revision',
        'apply_starter_profile': 'Create your account or copy the starter before applying calibration.',
        'apply_artifact_invalid': 'Saved calibrate_state.json belongs to another operation',
        'analysis_status_operation_unknown': 'Unknown operation',
        'analysis_status_other_workflow': 'Operation belongs to another workflow'}
    for name, message in expected.items():
        fn, taps = ROWS[name]
        with gen.world('message_' + name, taps=taps) as w:
            assert fn(w)[0].get_json()['error'] == message, name


def test_the_other_routes_keep_the_refusals_they_always_gave(gen):
    """Codes are the Recognition routes' contract; dashboard_contract_v1.json (POST /api/control and the rest) is unchanged."""
    from test_dashboard_contract import start_body
    with gen.world('plain') as w:
        got = post(w, '/api/control', start_body('p1', expected_context=wrong_serial(w))).get_json()
        assert got == {'ok': False, 'error': 'Selected account, device or profile changed before acceptance'}
        op = w.running('scan', {}, args=['--phases', 'g,c,m'])
        got = post(w, '/api/control', dict(action='clear_flag', operation_id=op['id'], expected_revision=op['revision'] + 5)).get_json()
        assert got == {'ok': False, 'error': 'Operation changed; refresh its status before trying again'}
