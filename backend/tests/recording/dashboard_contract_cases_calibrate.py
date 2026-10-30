"""Contract cases of the calibration routes (README row R-02): start, observe, apply and stop, plus the status reads that settle them.

Family calibrate_writes. Every case is the REAL route over the world: the worker is the Spawner's recorded Popen (nothing starts, taps
or navigates), the operation is the ledger's own record, a calibration's state/report are written by the case exactly as the
calibrator seals them (test_dashboard_contract.record), and the dashboard's own process table answers "is a calibration running".
A command line the Spawner recorded is asserted (the flags the route builds) but is not part of the answer.
"""
import copy
import json
from pathlib import Path

from dashboard_contract_cases_recognition import CAL, REC, START, OBSERVE, APPLY, STOP, expected_context, navigation_images, calibration_folder
from dashboard_contract_cases_profile import write_profile
from dashboard_contract_pages_world import populate, raw_post
from test_dashboard_contract import put_image


def body_of(w, key, **extra):
    """A launch command as the page composes it: the context it was composed for and the key that names the attempt."""
    return dict(dict(expected_context=expected_context(w), idempotency_key=key), **extra)


def launched(w):
    """The command line of the worker the last accepted launch started (asserted, never recorded)."""
    return w.spawn.calls[-1][3:] if w.spawn.calls else None


def start_cases(gen):
    with gen.world('cal_start_basic') as w:
        navigation_images(w, ['c', 'm'])
        body = body_of(w, 'cal-start-1', phases='c,m')
        accepted = w.post(CAL, 'start_basic_phases_accepted', START, body)
        assert accepted.status_code == 200 and accepted.get_json()['phases'] == 'c,m'
        args = launched(w)
        assert args[1:3] == ['--instance', 'main'] and args[3:5] == ['--phases', 'c,m'] and not {'--bootstrap', '--overwrite', '--fresh'} & set(args), args
        retry = w.post(CAL, 'start_retry_with_the_same_key_answers_the_same_operation', START, body)
        assert retry.status_code == 200 and retry.get_json()['operation_id'] == accepted.get_json()['operation_id'] and len(w.spawn.calls) == 1
        assert 'phases' not in retry.get_json()
        refused(w, 'start_same_key_with_other_phases_is_refused', START, body_of(w, 'cal-start-1', phases='c'), 409, code='idempotency_key_reused')
        assert len(w.spawn.calls) == 1
        refused(w, 'start_while_a_calibration_is_running_is_refused', START, body_of(w, 'cal-start-2', phases='c,m'), 409, code='calibration_running')
        w.get(CAL, 'read_the_running_calibration_by_operation', '/api/calibrate/status?operation_id=' + accepted.get_json()['operation_id'])
    with gen.world('cal_start_resume') as w:
        folder = calibration_folder(w)
        (folder / 'calibrate_state.json').write_text(json.dumps({'phases': {'cards': {'status': 'done'}, 'modules': {'status': 'pending'}}}), encoding='utf-8')
        navigation_images(w, ['c', 'g', 'm', 'u', 'b', 'w'])
        accepted = w.post(CAL, 'start_without_phases_uses_the_six_default_steps_and_no_fresh_flag', START, body_of(w, 'cal-start-3'))
        assert accepted.get_json()['phases'] == 'c,g,m,u,b,w' and '--fresh' not in launched(w)
    with gen.world('cal_start_fresh') as w:
        navigation_images(w, ['c'])
        w.post(CAL, 'start_fresh_overwrite_and_navigation_consent_reach_the_worker', START,
               body_of(w, 'cal-start-4', phases='c', fresh=True, overwrite=True, allow_navigation=True))
        assert {'--fresh', '--overwrite', '--allow-navigation'} <= set(launched(w))
    with gen.world('cal_start_bootstrap') as w:
        accepted = w.post(CAL, 'start_bootstrap_needs_no_navigation_images', START, body_of(w, 'cal-start-5', bootstrap=True, phases='c,m'))
        assert accepted.status_code == 200 and '--bootstrap' in launched(w) and '--flows' not in launched(w)
    with gen.world('cal_start_bootstrap_flows') as w:
        w.post(CAL, 'start_bootstrap_with_the_action_capture_flows', START, body_of(w, 'cal-start-6', bootstrap=True, flows=True, phases='c,m'))
        assert '--flows' in launched(w) and '--battle-only' not in launched(w)
    with gen.world('cal_start_battle_only') as w:
        w.post(CAL, 'start_bootstrap_flows_battle_only', START, body_of(w, 'cal-start-7', bootstrap=True, flows=True, battle_only=True, phases='c'))
        assert {'--flows', '--battle-only'} <= set(launched(w))
    with gen.world('cal_start_battle_only_without_flows') as w:
        w.post(CAL, 'start_battle_only_without_flows_is_ignored', START, body_of(w, 'cal-start-8', bootstrap=True, battle_only=True, phases='c'))
        assert '--battle-only' not in launched(w) and '--flows' not in launched(w)
    with gen.world('cal_start_routes') as w:
        accepted = w.post(CAL, 'start_bootstrap_routes_home_to_cards_and_back', START,
                          body_of(w, 'cal-start-9', bootstrap=True, routes=['route_0', 'route_1'], phases='c'))
        assert accepted.status_code == 200 and launched(w)[launched(w).index('--routes') + 1] == 'route_0,route_1'
    with gen.world('cal_start_selected_run') as w:
        accepted = w.post(CAL, 'start_names_the_run_the_learning_is_for', START, body_of(w, 'cal-start-10', bootstrap=True, phases='c', selected_run='bp_farm'))
        assert accepted.get_json()['operation']['context']['selected_run'] == 'bp_farm'
    with gen.world('cal_start_equipped') as w:
        navigation_images(w, ['e'])
        refused(w, 'start_equipped_phase_before_modules_are_done_is_refused', START, body_of(w, 'cal-start-11', phases='e'), 409, code='modules_not_done')
        (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'phases': {'modules': {'status': 'done'}}}), encoding='utf-8')
        accepted = w.post(CAL, 'start_equipped_phase_after_modules_are_done', START, body_of(w, 'cal-start-12', phases='e'))
        assert accepted.status_code == 200 and launched(w)[3:5] == ['--phases', 'e']

def refused(w, case, path, body, status, *, code=None, **fields):
    """A refusal: the answer, and proof that nothing was launched or recorded for it."""
    before = (len(w.spawn.calls), len(w.store.active()))
    reply = w.post(CAL, case, path, body)
    assert reply.status_code == status, (case, reply.status_code, reply.get_data(as_text=True)[:300])
    got = reply.get_json(silent=True) or {}
    if code is not None:
        assert got.get('code') == code, (case, got)
    for key, value in fields.items():
        assert got.get(key) == value, (case, key, got.get(key))
    assert (len(w.spawn.calls), len(w.store.active())) == before, f'{case} launched or recorded something'
    return got


def start_refusal_cases(gen):
    with gen.world('cal_start_routes_refused') as w:
        base = dict(bootstrap=True, phases='c', routes=['route_0', 'route_1'])
        refused(w, 'start_routes_without_bootstrap_400', START, body_of(w, 'k-r1', routes=['route_0', 'route_1'], phases='c'), 400, code='invalid_routes')
        refused(w, 'start_routes_with_the_flows_capture_400', START, body_of(w, 'k-r2', **dict(base, flows=True)), 400, code='invalid_routes')
        refused(w, 'start_routes_with_battle_only_400', START, body_of(w, 'k-r3', **dict(base, battle_only=True)), 400, code='invalid_routes')
        refused(w, 'start_routes_with_observe_400', START, body_of(w, 'k-r4', **dict(base, observe=True)), 400, code='invalid_routes')
        refused(w, 'start_routes_not_a_list_400', START, body_of(w, 'k-r5', **dict(base, routes='route_0')), 400, code='invalid_routes')
        refused(w, 'start_routes_empty_400', START, body_of(w, 'k-r6', **dict(base, routes=[])), 400, code='invalid_routes')
        refused(w, 'start_routes_unknown_id_400', START, body_of(w, 'k-r7', **dict(base, routes=['route_99'])), 400, code='invalid_routes')
        refused(w, 'start_routes_not_contiguous_400', START, body_of(w, 'k-r8', **dict(base, routes=['route_0', 'route_3'])), 400, code='invalid_routes')
        refused(w, 'start_routes_not_ending_at_home_400', START, body_of(w, 'k-r9', **dict(base, routes=['route_0'])), 400, code='invalid_routes')
        refused(w, 'start_routes_repeated_id_400', START, body_of(w, 'k-r10', **dict(base, routes=['route_0', 'route_1', 'route_0', 'route_1'])), 400, code='invalid_routes')
    with gen.world('cal_start_phases_refused') as w:
        refused(w, 'start_unknown_phase_400', START, body_of(w, 'k-p1', bootstrap=True, phases='c,z'), 400, code='invalid_phases')
        refused(w, 'start_phases_that_is_not_text_400', START, body_of(w, 'k-p2', bootstrap=True, phases=5), 400, code='invalid_phases')
        refused(w, 'start_phases_list_is_read_as_its_text_400', START, body_of(w, 'k-p3', bootstrap=True, phases=['c']), 400, code='invalid_phases')
        refused(w, 'start_only_separators_400', START, body_of(w, 'k-p4', bootstrap=True, phases=' , '), 400, code='invalid_phases')
    with gen.world('cal_start_images_missing') as w:
        got = refused(w, 'start_without_the_navigation_images_409', START, body_of(w, 'k-m1', phases='c,m'), 409, code='navigation_images_missing')
        assert 'home/battle_btn.png' in got['missing'] and 'screens/hdr_cards.png' in got['missing'] and got['missing'] == sorted(got['missing'])
        put_image(w, 'home/battle_btn.png')
        put_image(w, 'buttons/return_to_game.png')
        got = refused(w, 'start_with_only_the_base_images_409_lists_the_rest', START, body_of(w, 'k-m2', phases='c,m'), 409, code='navigation_images_missing')
        assert 'home/battle_btn.png' not in got['missing']
    with gen.world('cal_start_taps_off', taps=False) as w:
        navigation_images(w, ['c'])
        refused(w, 'start_taps_off_409', START, body_of(w, 'k-t1', phases='c'), 409, code='taps_disabled')
        accepted = w.post(CAL, 'start_taps_off_with_the_navigation_consent_is_accepted', START, body_of(w, 'k-t2', phases='c', allow_navigation=True))
        assert accepted.status_code == 200 and '--allow-navigation' in launched(w)
    with gen.world('cal_start_bootstrap_taps_off', taps=False) as w:
        refused(w, 'start_bootstrap_taps_off_409', START, body_of(w, 'k-t3', bootstrap=True, phases='c'), 409, code='taps_disabled')
    with gen.world('cal_start_context') as w:
        navigation_images(w, ['c'])
        wrong = dict(expected_context(w), serial='fake:other')
        refused(w, 'start_context_changed_409', START, dict(expected_context=wrong, idempotency_key='k-c1', phases='c'), 409, code='context_changed')
        wrong = dict(expected_context(w), profile='someone_else')
        refused(w, 'start_profile_changed_409', START, dict(expected_context=wrong, idempotency_key='k-c2', phases='c'), 409, code='context_changed')
        refused(w, 'start_expected_context_that_is_not_an_object_409', START, dict(expected_context='main', idempotency_key='k-c3', phases='c'), 409, code='context_changed')
        accepted = w.post(CAL, 'start_without_an_expected_context_is_accepted', START, dict(idempotency_key='k-c4', phases='c'))
        assert accepted.status_code == 200
    with gen.world('cal_start_owned_by_run') as w:
        navigation_images(w, ['c'])
        w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        refused(w, 'start_while_a_run_owns_the_device_409', START, body_of(w, 'k-o1', phases='c'), 409, code='device_owned')
    with gen.world('cal_start_owned_by_collector') as w:
        navigation_images(w, ['c'])
        w.running('gems', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        refused(w, 'start_while_the_collector_owns_the_device_409', START, body_of(w, 'k-o2', phases='c'), 409, code='device_owned')
    with gen.world('cal_start_owned_by_scan') as w:
        navigation_images(w, ['c'])
        w.running('scan', {}, args=['--phases', 'g,c,m'])
        w.dash._procs_refresh()
        refused(w, 'start_while_a_scan_owns_the_device_409', START, body_of(w, 'k-o3', phases='c'), 409, code='device_owned')
    with gen.world('cal_start_owned_by_observe') as w:
        navigation_images(w, ['c'])
        w.running('observe', {}, script='calibrate.py', args=['--phases', 'c', '--observe'])
        w.dash._procs_refresh()
        refused(w, 'start_while_an_observation_is_running_409', START, body_of(w, 'k-o4', phases='c'), 409)
    with gen.world('cal_start_lock') as w:
        navigation_images(w, ['c'])
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            refused(w, 'start_action_lock_another_action_in_progress', START, body_of(w, 'k-l1', phases='c'), 409, code='action_lock')
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('cal_start_malformed') as w:
        for case, data, content_type in (('start_body_not_json_400', 'phases=c', 'text/plain'), ('start_body_is_not_valid_json_400', '{"phases": ', 'application/json'),
                                         ('start_body_that_is_a_list_400', '["c"]', 'application/json'), ('start_body_that_is_a_number_400', '5', 'application/json'),
                                         ('start_body_that_is_null_400', 'null', 'application/json'), ('start_empty_body_400', '', 'application/json')):
            reply = raw_post(w, CAL, case, START, data, content_type=content_type)
            assert reply.status_code == 400 and reply.get_json()['code'] == 'invalid_request', (case, reply.get_json())
        assert not w.spawn.calls and not w.store.active()
    with gen.world('cal_start_retry_settled') as w:
        navigation_images(w, ['c'])
        body = body_of(w, 'k-s1', phases='c')
        first = w.post(CAL, 'start_for_a_later_retry', START, body).get_json()
        op = w.store.get(first['operation_id'])
        w.store.transition(op['id'], op['revision'], 'failed', error={'code': 'calibration_failed', 'message': 'The calibrator stopped before it finished'})
        w.dash._procs_refresh()
        retry = w.post(CAL, 'start_retry_after_the_calibration_failed_answers_the_failed_operation', START, body)
        assert retry.status_code == 200 and retry.get_json()['operation']['status'] == 'failed' and len(w.spawn.calls) == 1
        w.get(CAL, 'read_the_latest_calibration_of_the_context_after_a_lost_answer', '/api/calibrate/status')

def observe_cases(gen):
    with gen.world('cal_observe') as w:
        accepted = w.post(CAL, 'observe_accepted', OBSERVE, body_of(w, 'obs-1'))
        assert accepted.status_code == 200 and 'phases' not in accepted.get_json()
        args = launched(w)
        assert args[3:5] == ['--phases', 'c'] and args[5] == '--observe' and '--observe-watch' not in args, args
        retry = w.post(CAL, 'observe_retry_with_the_same_key_answers_the_same_operation', OBSERVE, body_of(w, 'obs-1'))
        assert retry.get_json()['operation_id'] == accepted.get_json()['operation_id'] and len(w.spawn.calls) == 1
        refused(w, 'observe_same_key_with_a_watch_is_refused', OBSERVE, body_of(w, 'obs-1', watch=30), 409, code='idempotency_key_reused')
        refused(w, 'observe_while_an_observation_is_running_is_refused', OBSERVE, body_of(w, 'obs-2'), 409, code='calibration_running')
        w.get(CAL, 'read_the_running_observation_by_operation', '/api/calibrate/status?operation_id=' + accepted.get_json()['operation_id'])
    with gen.world('cal_observe_watch') as w:
        accepted = w.post(CAL, 'observe_watch_30_seconds', OBSERVE, body_of(w, 'obs-3', watch=30))
        assert accepted.get_json()['operation']['request']['watch'] == 30 and launched(w)[-2:] == ['--observe-watch', '30']
    with gen.world('cal_observe_watch_refused') as w:
        # A watch is a whole number of seconds from 0 to 600; anything else is refused, never clamped, and nothing is recorded.
        for number, (case, watch) in enumerate((('observe_watch_above_600_is_refused_400', 9000), ('observe_watch_601_is_refused_400', 601),
                                                ('observe_negative_watch_is_refused_400', -5), ('observe_watch_that_is_text_is_refused_400', 'soon'),
                                                ('observe_watch_that_is_a_number_in_text_is_refused_400', '30'),
                                                ('observe_watch_that_is_a_fraction_is_refused_400', 2.9), ('observe_watch_that_is_a_boolean_is_refused_400', True))):
            refused(w, case, OBSERVE, body_of(w, f'obs-4-{number}', watch=watch), 400, code='invalid_watch')
        accepted = w.post(CAL, 'observe_watch_600_is_the_longest_accepted', OBSERVE, body_of(w, 'obs-4-ok', watch=600))
        assert accepted.status_code == 200 and launched(w)[-2:] == ['--observe-watch', '600']
    with gen.world('cal_observe_watch_zero') as w:
        accepted = w.post(CAL, 'observe_watch_0_is_no_watch', OBSERVE, body_of(w, 'obs-5', watch=0))
        assert accepted.status_code == 200 and '--observe-watch' not in launched(w)
    with gen.world('cal_observe_taps_off', taps=False) as w:
        accepted = w.post(CAL, 'observe_taps_off_is_accepted_it_never_taps', OBSERVE, body_of(w, 'obs-8'))
        assert accepted.status_code == 200 and '--allow-navigation' not in launched(w)
    with gen.world('cal_observe_selected_run') as w:
        accepted = w.post(CAL, 'observe_names_the_run_it_is_for', OBSERVE, body_of(w, 'obs-9', selected_run='bp_farm'))
        assert accepted.get_json()['operation']['context']['selected_run'] == 'bp_farm'
    with gen.world('cal_observe_context') as w:
        wrong = dict(expected_context(w), serial='fake:other')
        refused(w, 'observe_context_changed_409', OBSERVE, dict(expected_context=wrong, idempotency_key='obs-10'), 409, code='context_changed')
    with gen.world('cal_observe_owned_by_run') as w:
        w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        refused(w, 'observe_while_a_run_owns_the_device_409', OBSERVE, body_of(w, 'obs-11'), 409, code='device_owned')
    with gen.world('cal_observe_running_calibration') as w:
        w.running('calibrate', {'bootstrap': True, 'phases': 'c'}, script='calibrate.py', args=['--phases', 'c', '--bootstrap'])
        w.dash._procs_refresh()
        refused(w, 'observe_while_a_calibration_is_running_409', OBSERVE, body_of(w, 'obs-12'), 409, code='calibration_running')
    with gen.world('cal_observe_lock') as w:
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            refused(w, 'observe_action_lock_another_action_in_progress', OBSERVE, body_of(w, 'obs-13'), 409, code='action_lock')
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('cal_observe_malformed') as w:
        accepted = raw_post(w, CAL, 'observe_body_not_json_is_read_as_an_empty_command', OBSERVE, 'watch=30', content_type='text/plain')
        assert accepted.status_code == 200 and '--observe-watch' not in launched(w)
    with gen.world('cal_observe_bad_body') as w:
        for case, data in (('observe_body_that_is_a_list_400', '[30]'), ('observe_body_is_not_valid_json_400', '{"watch": 3')):
            reply = raw_post(w, CAL, case, OBSERVE, data)
            assert reply.status_code == 400 and reply.get_json()['code'] == 'invalid_request', (case, reply.get_json())
        assert not w.spawn.calls and not w.store.active()

# --------------------------------------------------------------------------- apply
CAPTURE = {'card_presets': ['main_farm', 'no_card'], 'uws': {'chain_lightning': True, 'death_wave': True}, 'abilities': {'nuke': True},
           'wall': True, 'max_tier': 19}
FAILED = {'code': 'calibration_failed', 'message': 'The calibrator stopped before the last step'}


def seal(w, *, kind='calibrate', status='awaiting_apply', player=None, state=None, error=None, request=None, files=None):
    """One calibration (or observation) of this device, finished as `status`: its state sealed the way the worker seals it
    (operations/<id>/calibrate_state.json, sha256 in result_refs), the way test_dashboard_contract.record does."""
    import hashlib
    context = w.context(request or {})
    request = request if request is not None else ({'bootstrap': True, 'phases': 'c,m'} if kind == 'calibrate' else {})
    op, _ = w.store.accept(kind, context, request)
    op = w.store.transition(op['id'], op['revision'], 'running')
    data = dict(operation_id=op['id'], player=copy.deepcopy(CAPTURE if player is None else player))
    data.update(state(op['id']) if callable(state) else (state or {}))     # a state may name its own operation
    raw = json.dumps(data).encode()
    path = Path(context['artifact_dir']) / 'operations' / op['id'] / 'calibrate_state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    refs = {'calibrate_state.json': {'path': str(path), 'revision': hashlib.sha256(raw).hexdigest()}}
    for name, content in (files or {}).items():     # more sealed artifacts: the report and the two manifests
        sealed = (json.dumps(dict(content, operation_id=op['id']) if name == 'calibrate_report.json' else content)).encode()
        other = path.with_name(name)
        other.write_bytes(sealed)
        refs[name] = {'path': str(other), 'revision': hashlib.sha256(sealed).hexdigest()}
    fields = dict(result_refs=refs)
    if error:
        fields['error'] = error
    if status == 'cancelled':
        op = w.store.transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
    return w.store.transition(op['id'], op['revision'], status, **fields)


def apply_body(op, **extra):
    return dict(dict(operation_id=op['id'], expected_revision=op['revision']), **extra)


def apply_cases(gen):
    from test_dashboard_contract import raising
    with gen.world('cal_apply') as w:
        populate(w)
        op = seal(w)
        refused(w, 'apply_without_naming_an_operation_409', APPLY, {}, 409, code='operation_required')
        refused(w, 'apply_unknown_operation_409', APPLY, dict(operation_id='no-such-operation', expected_revision=1), 409, code='operation_unknown')
        refused(w, 'apply_without_the_revision_409', APPLY, dict(operation_id=op['id']), 409, code='operation_required')
        refused(w, 'apply_stale_revision_409', APPLY, dict(operation_id=op['id'], expected_revision=op['revision'] - 1), 409, code='operation_changed')
        applied = w.post(CAL, 'apply_awaiting_apply_is_applied', APPLY, apply_body(op))
        got = applied.get_json()
        assert applied.status_code == 200 and got['ok'] and got['applied'] and got['operation']['status'] == 'succeeded' and got['profile'] == 'mine', got
        w.get(CAL, 'read_the_applied_calibration_by_operation', '/api/calibrate/status?operation_id=' + op['id'])
        again = w.post(CAL, 'apply_resend_after_a_lost_answer_is_answered_from_the_record', APPLY, apply_body(op))
        assert again.status_code == 200 and again.get_json()['applied'] is True and again.get_json()['operation']['revision'] == got['operation']['revision']
        refused(w, 'apply_other_revision_after_the_apply_is_refused', APPLY, dict(operation_id=op['id'], expected_revision=op['revision'] + 50), 409, code='operation_changed')
    with gen.world('cal_apply_observe') as w:
        populate(w)
        op = seal(w, kind='observe', request={})
        got = w.post(CAL, 'apply_an_observation_is_applied_like_a_calibration', APPLY, apply_body(op)).get_json()
        assert got['applied'] and got['operation']['kind'] == 'observe'
    with gen.world('cal_apply_wall_bar') as w:
        populate(w)
        op = seal(w, player=dict(CAPTURE, wall_bar=[40, 2100, 1000, 30]))
        got = w.post(CAL, 'apply_with_the_wall_bar_region_writes_it_to_the_configuration', APPLY, apply_body(op)).get_json()
        assert got['wall_bar'] == [40, 2100, 1000, 30]
        cfg = w.get(CAL, 'read_the_configuration_after_the_wall_bar_apply', '/api/config').get_json()
        assert cfg['instances']['main']['rois']['wall_bar'] == [40, 2100, 1000, 30]
    with gen.world('cal_apply_selected_run') as w:
        populate(w)
        op = seal(w, request={'bootstrap': True, 'phases': 'c,m', 'selected_run': 'bp_farm'})
        got = w.post(CAL, 'apply_with_a_selected_run_returns_its_readiness', APPLY, apply_body(op)).get_json()
        assert got['readiness'] == {'ready': True, 'missing': []} and got['operation']['readiness'] == got['readiness']
    with gen.world('cal_apply_failed_capture') as w:
        populate(w)
        op = seal(w, status='failed', error=FAILED)
        got = w.post(CAL, 'apply_after_a_failed_capture_keeps_the_failure_on_the_operation', APPLY, apply_body(op)).get_json()
        assert got['applied'] and got['operation']['status'] == 'failed' and got['operation']['error']['code'] == 'calibration_failed', got
        again = w.post(CAL, 'apply_resend_after_a_failed_capture_was_applied', APPLY, apply_body(op))
        assert again.status_code == 200 and again.get_json()['applied']
        w.get(CAL, 'read_the_failed_capture_after_the_apply', '/api/calibrate/status?operation_id=' + op['id'])
    with gen.world('cal_apply_interrupted') as w:
        populate(w)
        op = seal(w, status='interrupted', error={'code': 'worker_lost', 'message': 'The calibrator process ended without an outcome'})
        got = w.post(CAL, 'apply_after_an_interrupted_capture_keeps_the_interruption', APPLY, apply_body(op)).get_json()
        assert got['applied'] and got['operation']['status'] == 'interrupted'
    with gen.world('cal_apply_running') as w:
        populate(w)
        op = w.running('calibrate', {'bootstrap': True, 'phases': 'c'}, script='calibrate.py', args=['--phases', 'c', '--bootstrap'])
        w.dash._procs_refresh()
        refused(w, 'apply_while_the_calibration_runs_409', APPLY, apply_body(op), 409, code='device_owned')
    with gen.world('cal_apply_owned') as w:
        populate(w)
        op = seal(w)
        w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        refused(w, 'apply_while_a_run_owns_the_device_409', APPLY, apply_body(op), 409, code='device_owned')
    with gen.world('cal_apply_not_a_calibration') as w:
        populate(w)
        scan, _ = w.store.accept('scan', w.context(), {})
        scan = w.store.transition(scan['id'], scan['revision'], 'running')
        scan = w.store.transition(scan['id'], scan['revision'], 'succeeded')
        refused(w, 'apply_an_operation_that_is_not_a_calibration_409', APPLY, apply_body(scan), 409, code='not_a_calibration')
    with gen.world('cal_apply_not_awaiting') as w:
        populate(w)
        op = seal(w, status='cancelled')
        refused(w, 'apply_a_cancelled_calibration_409', APPLY, apply_body(op), 409, code='operation_changed')
    with gen.world('cal_apply_restoration_required') as w:
        populate(w)
        op = seal(w, status='restoration_required', error={'code': 'restoration_required', 'message': 'The original modules were not put back'})
        refused(w, 'apply_while_the_original_modules_are_not_restored_409', APPLY, apply_body(op), 409, code='operation_changed')
        w.get(CAL, 'read_the_calibration_that_needs_its_modules_restored', '/api/calibrate/status?operation_id=' + op['id'])
    with gen.world('cal_apply_context') as w:
        populate(w)
        op = seal(w)
        w.edit_config(lambda cfg: cfg['instances']['main'].update(serial='fake:other'))
        refused(w, 'apply_after_the_device_changed_409', APPLY, apply_body(op), 409, code='context_changed')
    with gen.world('cal_apply_context_profile') as w:
        populate(w)
        op = seal(w)
        write_profile(w, 'other', yaml_of(w, 'mine'))
        w.edit_config(lambda cfg: cfg.update(active_profile='other'))
        refused(w, 'apply_after_another_profile_became_active_409', APPLY, apply_body(op), 409, code='context_changed')
    with gen.world('cal_apply_context_config') as w:
        populate(w)
        op = seal(w)
        w.edit_config(lambda cfg: cfg.setdefault('loadouts', {}).update(spare={'cards': 'no_card'}))
        refused(w, 'apply_after_the_configuration_changed_409', APPLY, apply_body(op), 409, code='context_changed')
    with gen.world('cal_apply_starter') as w:
        w.edit_config(lambda cfg: cfg.update(active_profile='default'))
        op = seal(w)
        refused(w, 'apply_with_the_starter_profile_active_409', APPLY, apply_body(op), 409, code='starter_profile')
    with gen.world('cal_apply_no_profile') as w:
        w.edit_config(lambda cfg: cfg.update(active_profile='ghost'))
        op = seal(w)
        refused(w, 'apply_with_no_profile_file_400', APPLY, apply_body(op), 400, code='profile_missing')
    with gen.world('cal_apply_profile_edited') as w:
        populate(w)
        op = seal(w)
        w.edit_profile(lambda profile: profile['blueprints']['farm'].update(tier=2))
        refused(w, 'apply_after_the_profile_was_edited_409', APPLY, apply_body(op), 409, code='profile_changed')
        w.get(CAL, 'read_the_calibration_after_the_refused_apply', '/api/calibrate/status?operation_id=' + op['id'])
    with gen.world('cal_apply_restore_pending') as w:
        populate(w)
        op = seal(w)
        (calibration_folder(w) / 'module_restore.json').write_text('{}', encoding='utf-8')
        got = refused(w, 'apply_while_the_module_restoration_is_pending_400', APPLY, apply_body(op), 400, code='restoration_pending')
        assert 'Restore the saved module equipment' in got['error']
        current = w.store.get(op['id'])
        assert current['status'] == 'awaiting_apply' and current['revision'] == op['revision'] and current['error'] is None   # refused before the apply began
        w.get(CAL, 'read_the_calibration_after_the_refused_apply_nothing_changed', '/api/calibrate/status?operation_id=' + op['id'])
        (calibration_folder(w) / 'module_restore.json').unlink()
        retried = w.post(CAL, 'apply_again_after_the_restoration_is_done', APPLY, apply_body(op))
        assert retried.status_code == 200 and retried.get_json()['applied']
    with gen.world('cal_apply_no_player') as w:
        populate(w)
        op = seal(w, player={})
        refused(w, 'apply_state_without_account_observations_400', APPLY, apply_body(op), 400, code='no_observations')
    with gen.world('cal_apply_foreign_state') as w:
        populate(w)
        op = seal(w, state=dict(operation_id='another-operation'))
        refused(w, 'apply_state_of_another_operation_409', APPLY, apply_body(op), 409, code='artifact_invalid')
    with gen.world('cal_apply_artifact_changed') as w:
        populate(w)
        op = seal(w)
        Path(op['result_refs']['calibrate_state.json']['path']).write_bytes(b'{"operation_id": "x", "player": {"max_tier": 1}}')
        refused(w, 'apply_state_changed_after_it_was_sealed_409', APPLY, apply_body(op), 409, code='artifact_changed')
    with gen.world('cal_apply_artifact_missing') as w:
        populate(w)
        op = seal(w)
        Path(op['result_refs']['calibrate_state.json']['path']).unlink()
        refused(w, 'apply_state_missing_409', APPLY, apply_body(op), 409, code='artifact_unavailable')
    with gen.world('cal_apply_write_fails') as w:
        populate(w)
        op = seal(w)
        real_backup = w.dash._save_yaml_backup
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('disk full')))
        refused(w, 'apply_profile_write_fails_400', APPLY, apply_body(op), 400, code='apply_failed')
        current = w.store.get(op['id'])
        assert current['status'] == 'awaiting_apply' and current['result_refs']['apply_status'] == 'failed'
        w.get(CAL, 'read_the_calibration_after_the_write_failed', '/api/calibrate/status?operation_id=' + op['id'])
        w.mp.setattr(w.dash, '_save_yaml_backup', real_backup)
        retried = w.post(CAL, 'apply_again_after_the_write_failure', APPLY, apply_body(current))
        assert retried.status_code == 200 and retried.get_json()['applied']
    with gen.world('cal_apply_unexpected') as w:
        populate(w)
        op = seal(w)
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(RuntimeError('unexpected')))
        refused(w, 'apply_unexpected_failure_500', APPLY, apply_body(op), 500, code='apply_failed')
        assert w.store.get(op['id'])['status'] == 'awaiting_apply'
    with gen.world('cal_apply_lock') as w:
        populate(w)
        op = seal(w)
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            refused(w, 'apply_action_lock_another_action_in_progress', APPLY, apply_body(op), 409, code='action_lock')
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('cal_apply_saved') as w:
        populate(w)
        refused(w, 'apply_saved_observations_without_a_saved_file_400', APPLY, dict(saved_observations=True), 400, code='apply_failed')
        (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'player': CAPTURE}), encoding='utf-8')
        got = w.post(CAL, 'apply_saved_observations_of_no_operation', APPLY, dict(saved_observations=True)).get_json()
        assert got['applied'] and 'operation' not in got and got['profile'] == 'mine'
    with gen.world('cal_apply_saved_owned') as w:
        populate(w)
        (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'player': CAPTURE, 'operation_id': 'some-operation'}), encoding='utf-8')
        refused(w, 'apply_saved_observations_that_belong_to_an_operation_409', APPLY, dict(saved_observations=True), 409, code='observations_tracked')
    with gen.world('cal_apply_saved_tracked') as w:
        populate(w)
        seal(w)
        (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'player': CAPTURE}), encoding='utf-8')
        refused(w, 'apply_saved_observations_while_an_operation_is_tracked_409', APPLY, dict(saved_observations=True), 409, code='observations_tracked')
    with gen.world('cal_apply_saved_empty') as w:
        populate(w)
        (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'player': {}}), encoding='utf-8')
        refused(w, 'apply_saved_observations_without_account_observations_400', APPLY, dict(saved_observations=True), 400, code='no_observations')
    with gen.world('cal_apply_malformed') as w:
        populate(w)
        raw_post(w, CAL, 'apply_body_not_json_is_read_as_an_empty_command', APPLY, 'operation_id=x', content_type='text/plain')
        for case, data in (('apply_body_that_is_a_list_400', '["x"]'), ('apply_body_is_not_valid_json_400', '{"operation_id": ')):
            reply = raw_post(w, CAL, case, APPLY, data)
            assert reply.status_code == 400 and reply.get_json()['code'] == 'invalid_request', (case, reply.get_json())
        reply = w.post(CAL, 'apply_revision_that_is_text_409', APPLY, dict(operation_id='x', expected_revision='1'))
        assert reply.status_code == 409 and reply.get_json()['code'] == 'operation_unknown'


def yaml_of(w, name):
    import yaml
    return yaml.safe_load((w.root / 'profiles' / f'{name}.yaml').read_text(encoding='utf-8'))

# --------------------------------------------------------------------------- stop
def stop_cases(gen):
    with gen.world('cal_stop') as w:
        op = w.running('calibrate', {'bootstrap': True, 'phases': 'c,m'}, script='calibrate.py', args=['--phases', 'c,m', '--bootstrap'])
        w.dash._procs_refresh()
        refused(w, 'stop_without_naming_the_operation_409', STOP, {}, 409, code='operation_required')
        refused(w, 'stop_unknown_operation_409', STOP, dict(operation_id='no-such-operation', expected_revision=1), 409, code='operation_unknown')
        refused(w, 'stop_stale_revision_409', STOP, dict(operation_id=op['id'], expected_revision=op['revision'] - 1), 409, code='operation_changed')
        stopped = w.post(CAL, 'stop_running_calibration_asks_the_worker_to_stop', STOP, dict(operation_id=op['id'], expected_revision=op['revision']))
        got = stopped.get_json()
        assert stopped.status_code == 200 and got['operation']['status'] == 'cancelling' and got['stop']['state'] == 'stopping' and got['stop']['by_this_request']['requested'] is True, got
        assert (Path(op['context']['artifact_dir']) / 'calibrate_stop').read_text(encoding='utf-8') == op['id'] and not w.table.signalled
        w.get(CAL, 'read_the_calibration_that_was_asked_to_stop', '/api/calibrate/status?operation_id=' + op['id'])
        again = w.post(CAL, 'stop_again_while_it_is_stopping_signals_nothing', STOP, dict(operation_id=op['id'], expected_revision=got['operation']['revision']))
        assert again.status_code == 200 and again.get_json()['stop']['state'] == 'stopping' and not w.table.signalled
    with gen.world('cal_stop_gone') as w:
        op = w.running('calibrate', {'bootstrap': True, 'phases': 'c'}, pid=4500, script='calibrate.py', args=['--phases', 'c', '--bootstrap'])
        w.table.exit(4500)
        w.dash._procs_refresh()
        # The Stop reads the operation through the ledger's reconcile first: a worker proven gone retires the operation
        # (interrupted, worker_lost) before the revision is compared, so the Stop is refused with the operation as it now stands.
        reply = w.post(CAL, 'stop_after_the_worker_exited_the_operation_is_retired_and_the_stop_refused_409', STOP,
                       dict(operation_id=op['id'], expected_revision=op['revision']))
        got = reply.get_json()
        assert reply.status_code == 409 and got['code'] == 'operation_changed'
        assert got['operation']['status'] == 'interrupted' and got['operation']['error']['code'] == 'worker_lost' and not w.table.signalled
        w.get(CAL, 'read_the_calibration_whose_worker_exited', '/api/calibrate/status?operation_id=' + op['id'])
    with gen.world('cal_stop_unverified') as w:
        op = w.running('calibrate', {'bootstrap': True, 'phases': 'c'}, pid=4501, script='calibrate.py', args=['--phases', 'c', '--bootstrap'])
        w.table.denied.add(4501)
        w.dash._procs_refresh()
        got = w.post(CAL, 'stop_a_worker_that_cannot_be_verified_is_released_while_the_lease_is_free', STOP,
                     dict(operation_id=op['id'], expected_revision=op['revision'])).get_json()
        assert got['stop']['state'] == 'released' and got['operation']['status'] == 'interrupted', got
    with gen.world('cal_stop_observe') as w:
        op = w.running('observe', {}, script='calibrate.py', args=['--phases', 'c', '--observe'])
        w.dash._procs_refresh()
        got = w.post(CAL, 'stop_an_observation_like_a_calibration', STOP, dict(operation_id=op['id'], expected_revision=op['revision'])).get_json()
        assert got['operation']['kind'] == 'observe' and got['stop']['state'] == 'stopping'
    with gen.world('cal_stop_not_running') as w:
        awaiting = seal(w)
        refused(w, 'stop_a_calibration_that_finished_409', STOP, dict(operation_id=awaiting['id'], expected_revision=awaiting['revision']), 409, code='operation_changed')
    with gen.world('cal_stop_restoration') as w:
        op = seal(w, status='restoration_required', error={'code': 'restoration_required', 'message': 'The original modules were not put back'})
        got = refused(w, 'stop_while_the_modules_must_be_restored_409_nothing_is_signalled', STOP, dict(operation_id=op['id'], expected_revision=op['revision']), 409, code='operation_changed')
        assert got['operation']['status'] == 'restoration_required' and not w.table.signalled
    with gen.world('cal_stop_lock') as w:
        op = w.running('calibrate', {'bootstrap': True, 'phases': 'c'}, script='calibrate.py', args=['--phases', 'c', '--bootstrap'])
        w.dash._procs_refresh()
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            got = w.post(CAL, 'stop_is_not_held_back_by_another_action_in_progress', STOP, dict(operation_id=op['id'], expected_revision=op['revision']))
            assert got.status_code == 200 and got.get_json()['operation']['status'] == 'cancelling'
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('cal_stop_malformed') as w:
        raw_post(w, CAL, 'stop_body_not_json_is_read_as_an_empty_command', STOP, 'x', content_type='text/plain')
        assert w.post(CAL, 'stop_body_that_is_a_list_is_read_as_an_empty_command', STOP, ['x']).status_code == 409

# --------------------------------------------------------------------------- status reads
REPORT = {'entries': [
    {'rel': 'buttons/exit_battle.png', 'verified': True, 'screen': 'results', 'image_sha256': 'a' * 64, 'status': 'verified'},
    {'rel': 'home/end_round_yes.png', 'verified': False, 'status': 'stale', 'reason': 'The saved image no longer matches the screen'},
    {'rel': 'modules/black_hole_digestor.png', 'verified': False, 'status': 'stale', 'rarity': 'epic', 'reason': 'Did not match'},
    {'rel': 'modules/equipped/black_hole_digestor.png', 'verified': True, 'status': 'verified', 'rarity': 'legendary'},
    {'rel': 'uw/chain_lightning.png', 'verified': True, 'status': 'verified'}],
    'player': {'modules_equipped': ['black_hole_digestor']}}
STATE = {'phases': {'cards': {'status': 'done'}, 'modules': {'status': 'done'}, 'guild': {'status': 'failed', 'error': 'The guild tile was not found'}},
         'activity': {'kind': 'calibrate_progress', 'phase': 'guild', 't': 1799999990.0}}
MANIFEST = {'modules': {'black_hole_digestor': {'slot': 3, 'rarity': 'epic'}}}
CARD_MANIFEST = {'cards': {'main_farm': {'slot': 1}}}


def status_cases(gen):
    with gen.world('cal_status_sealed') as w:
        populate(w)
        op = seal(w, state=STATE, files={'calibrate_report.json': REPORT, 'module_manifest.json': MANIFEST, 'card_manifest.json': CARD_MANIFEST})
        got = w.get(REC, 'calibrate_status_awaiting_apply_with_the_sealed_evidence', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['running'] is False and got['manifest'] == MANIFEST and got['card_manifest'] == CARD_MANIFEST and got['operation']['status'] == 'awaiting_apply'
        copies = {e['rel']: e['status'] for e in got['report']['entries']}
        assert copies['modules/black_hole_digestor.png'] == 'unverified_copy' and copies['home/end_round_yes.png'] == 'stale', copies
        latest = w.get(REC, 'calibrate_status_without_an_operation_reads_the_latest_of_the_context', '/api/calibrate/status').get_json()
        assert latest['operation']['id'] == op['id']
    with gen.world('cal_status_aborted') as w:
        op = seal(w, status='cancelled', state=lambda op_id: dict(STATE, final_screen=dict(name='home', verified=True, observed_at=1799999801.0, operation_id=op_id)))
        got = w.get(REC, 'calibrate_status_aborted_by_a_person_keeps_the_partial_evidence', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['operation']['status'] == 'cancelled' and got['running'] is False and got['state']['phases']['guild']['status'] == 'failed'
    with gen.world('cal_status_failed') as w:
        op = seal(w, status='failed', error=FAILED, state=STATE, files={'calibrate_report.json': REPORT})
        got = w.get(REC, 'calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['operation']['status'] == 'failed' and got['report']['entries'] and got['running'] is False
    with gen.world('cal_status_foreign_state') as w:
        op = seal(w, state=dict(operation_id='another-operation'), files={'calibrate_report.json': REPORT})
        got = w.get(REC, 'calibrate_status_state_of_another_operation_empties_the_read_and_names_the_error', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['state'] == {} and got['report'] == {} and 'state' in got['artifact_errors']
    with gen.world('cal_status_report_changed') as w:
        op = seal(w, state=STATE, files={'calibrate_report.json': REPORT})
        Path(op['result_refs']['calibrate_report.json']['path']).write_text('{"entries": []}', encoding='utf-8')
        got = w.get(REC, 'calibrate_status_report_changed_after_it_was_sealed_is_unavailable', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert 'report' in got['artifact_errors']
    with gen.world('cal_status_other_kind') as w:
        reply = w.get(REC, 'calibrate_status_unknown_operation_409', '/api/calibrate/status?operation_id=no-such-operation')
        assert reply.status_code == 409 and reply.get_json()['code'] == 'operation_unknown'
        run = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        reply = w.get(REC, 'calibrate_status_of_another_workflow_409', '/api/calibrate/status?operation_id=' + run['id'])
        assert reply.status_code == 409 and reply.get_json()['code'] == 'operation_kind_mismatch'
    with gen.world('cal_status_shared_not_objects') as w:
        folder = calibration_folder(w)
        for name in ('calibrate_state.json', 'calibrate_report.json', 'module_manifest.json', 'card_manifest.json'):
            (folder / name).write_text('[]', encoding='utf-8')
        got = w.get(REC, 'calibrate_status_shared_files_that_are_json_but_not_objects_read_as_nothing', '/api/calibrate/status').get_json()
        assert got['state'] == {} and got['report'] == {} and got['manifest'] == {} and got['card_manifest'] == {} and got['running'] is False
    with gen.world('cal_status_shared_corrupt') as w:
        folder = calibration_folder(w)
        (folder / 'calibrate_state.json').write_text('{"phases": ', encoding='utf-8')
        (folder / 'calibrate_report.json').write_text('{"entries": [', encoding='utf-8')
        got = w.get(REC, 'calibrate_status_corrupt_shared_files_read_as_nothing', '/api/calibrate/status').get_json()
        assert got['state'] == {} and got['report'] == {} and got['running'] is False
    with gen.world('cal_status_shared_report') as w:
        write_report = REPORT
        (calibration_folder(w) / 'calibrate_report.json').write_text(json.dumps(write_report), encoding='utf-8')
        (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps(STATE), encoding='utf-8')
        got = w.get(REC, 'calibrate_status_shared_files_of_an_untracked_calibration', '/api/calibrate/status').get_json()
        assert got['report']['entries'] and got['state']['phases'] and 'operation' not in got
    with gen.world('cal_status_accounts') as w:
        library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}}, 'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

        def as_account(account):
            def change(cfg):
                cfg['accounts'] = copy.deepcopy(library)
                cfg['instances']['main']['account'] = account
            w.edit_config(change)
        as_account('alpha')
        (calibration_folder(w) / 'calibrate_report.json').write_text(json.dumps(REPORT), encoding='utf-8')
        assert w.get(REC, 'calibrate_status_of_the_first_account', '/api/calibrate/status').get_json()['report']['entries']
        as_account('beta')
        second = w.get(REC, 'calibrate_status_of_the_second_account_shows_nothing_of_the_first', '/api/calibrate/status').get_json()
        assert second['report'] == {} and second['state'] == {}
    with gen.world('cal_status_untracked_running') as w:
        w.worker(4520, 'calibrate.py', args=['--phases', 'c,m', '--bootstrap', '--allow-navigation'])
        w.mp.setattr(w.dash, '_newest_events', lambda inst='main', n=2000: [{'kind': 'calibrate_progress', 'phase': 'modules', 't': 1799999999.0}])
        got = w.get(REC, 'calibrate_status_running_worker_that_belongs_to_no_operation', '/api/calibrate/status').get_json()
        assert got['running'] is True and got['selection'] == dict(mode='bootstrap', phases=['c', 'm'], taps=True, overwrite=False) and got['activity']['phase'] == 'modules'
    with gen.world('cal_status_observe_running') as w:
        op = w.running('observe', {'watch': 30}, script='calibrate.py', args=['--phases', 'c', '--observe'])
        got = w.get(REC, 'calibrate_status_running_observation_names_its_mode_and_no_taps', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['selection']['mode'] == 'observe' and got['selection']['taps'] is False and got['running'] is True
    with gen.world('cal_status_taps_off', taps=False) as w:
        got = w.get(REC, 'calibrate_status_taps_not_allowed', '/api/calibrate/status').get_json()
        assert got['taps_allowed'] is False


def evidence_cases(gen):
    """R-06: the retained setup evidence as the status read echoes it. The producers are the calibrator's own records: the setup battle
    (player.flow_capture._save_setup_battle: starting, active, finished, unknown), the final screen (calibrate.record_final_screen:
    name, verified, observed_at, operation_id) and the report entries of each target. No route summarises a target's gap or compares
    scans (see NOT_PRODUCIBLE in the contract test)."""
    targets = ['stats/bounce_shot_range.png', 'buttons/perks.png', 'modules/shatter_rare_text.png', 'dialogs/dissonant_header.png']

    def finished(op_id):
        return dict(setup_battle=dict(id='5e7a0b1c2d3e4f5a6b7c8d9e0f1a2b3c', status='finished', started_at=1799999000.0, finished_at=1799999800.0,
                                      operation_id=op_id, reason='consented setup battle'),
                    final_screen=dict(name='home', verified=True, observed_at=1799999801.0, operation_id=op_id), **STATE)
    report = {'entries': [
        {'rel': targets[0], 'verified': True, 'status': 'verified', 'screen': 'battle', 'phase': 'flows', 'self': 1.0, 'next': 0.41},
        {'rel': targets[1], 'verified': False, 'status': 'written', 'screen': 'home', 'phase': 'flows', 'reason': 'Another spot on the source frame looks the same'},
        {'rel': targets[3], 'verified': False, 'status': 'stale', 'phase': 'flows', 'reason': 'The saved image no longer matches the screen'}]}
    with gen.world('cal_evidence_finished') as w:
        op = seal(w, state=finished, files={'calibrate_report.json': report}, request={'bootstrap': True, 'flows': True, 'phases': 'c,m'})
        got = w.get(REC, 'calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['state']['setup_battle']['status'] == 'finished' and got['state']['final_screen']['verified'] is True
        assert [e['rel'] for e in got['report']['entries']] == [targets[0], targets[1], targets[3]]       # the target with no entry is simply absent
    with gen.world('cal_evidence_active') as w:
        op = w.running('calibrate', {'bootstrap': True, 'flows': True, 'phases': 'c'}, script='calibrate.py', args=['--phases', 'c', '--bootstrap', '--flows'])
        folder = calibration_folder(w)
        (folder / 'calibrate_state.json').write_text(json.dumps(dict(operation_id=op['id'], setup_battle=dict(
            id='5e7a0b1c2d3e4f5a6b7c8d9e0f1a2b3c', status='active', started_at=1799999000.0, operation_id=op['id'], reason='consented setup battle'))), encoding='utf-8')
        got = w.get(REC, 'calibrate_status_setup_battle_active_no_final_screen_yet', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['running'] is True and got['state']['setup_battle']['status'] == 'active' and 'final_screen' not in got['state']
    with gen.world('cal_evidence_restoration') as w:
        op = seal(w, status='restoration_required', error={'code': 'restoration_required', 'message': 'The original modules were not put back'},
                  state=lambda op_id: dict(final_screen=dict(name='unknown', verified=False, observed_at=1799999801.0, operation_id=op_id, error='Stopped before phase')))
        (calibration_folder(w) / 'module_restore.json').write_text('{}', encoding='utf-8')
        got = w.get(REC, 'calibrate_status_restoration_required_with_the_restore_file_present', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert got['restore_pending'] is True and got['operation']['status'] == 'restoration_required' and got['state']['final_screen']['verified'] is False


def run_calibrate_cases(gen):
    start_cases(gen)
    start_refusal_cases(gen)
    observe_cases(gen)
    apply_cases(gen)
    stop_cases(gen)
    status_cases(gen)
    evidence_cases(gen)
