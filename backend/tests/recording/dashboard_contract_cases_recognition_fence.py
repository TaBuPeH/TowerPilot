"""Contract cases of the Recognition page's context fence (finding of frontend-f6-recognition.json, open item 1): POST /api/calibrate/apply.

Family recognition_context_fence. "Apply saved observations" ({saved_observations: true}) writes the PROFILE IN FORCE, and the page composes
the request from a config read made at click time, so a switch of account or profile between that read and the write put the observations on
the wrong profile. The route now takes the same optional `expected_context` {instance, account, profile, serial} as the Profiles writes (family
context_fence): refused 409 context_changed + current_context, writing nothing and recording no operation, first, under the config write lock;
a malformed field (an explicit null included) is 400 invalid_request; an absent field is the legacy request, answered exactly as before.

The other Recognition writes are classified here too, each through the real route:
  * calibrate start / observe and screen-analysis start already refuse a stale context: the operation they accept is FROZEN on the very
    configuration they verified it against (the five-field expected_context of operations.verify_expected: instance, account, serial, rendering,
    profile), so no later switch can move what the worker does;
  * calibrate stop is a revocation: it is never refused for a context (the case shows a stale expected_context is ignored and the stop is made);
  * the cropper's save is fenced by the context its frame token was captured under (family recognition_images, frame_context_changed).
Two accounts are used: Alpha (profile `mine`, saved observations with max_tier 19) and Beta (profile `theirs`, saved observations with max_tier 7).
"""
import copy
import json
import sqlite3

from dashboard_contract_cases_calibrate import CAPTURE, apply_body, seal
from dashboard_contract_cases_context import page_context, switch_to_beta, two_account_world
from dashboard_contract_cases_profile import two_accounts
from dashboard_contract_cases_recognition import APPLY, OBSERVE, START, STOP, calibration_folder, expected_context

RF = 'recognition_context_fence'
ANALYSIS = '/api/screen-analysis/start'
PROFILE_READ = '/api/profile/'      # the route takes the file name: /api/profile/<stem>.yaml
ALPHA_SEEN = dict(CAPTURE, max_tier=19)
BETA_SEEN = dict(CAPTURE, max_tier=7, card_presets=['no_card'], uws={'death_wave': True})
SAVED = dict(saved_observations=True)


def observations(w, player):
    """The saved observations of the account in force (calibrate_state.json of its own calibration folder, no operation)."""
    (calibration_folder(w) / 'calibrate_state.json').write_text(json.dumps({'player': copy.deepcopy(player)}), encoding='utf-8')


def observed_world(w):
    """Two accounts, each with saved observations of its own; Alpha is in force."""
    two_account_world(w)
    observations(w, ALPHA_SEEN)
    switch_to_beta(w)
    observations(w, BETA_SEEN)
    w.edit_config(lambda cfg: two_accounts(cfg, active='alpha'))


def profile_bytes(w, name):
    return (w.root / 'profiles' / f'{name}.yaml').read_bytes()


def count(w, kind):
    db = sqlite3.connect(w.store.path)
    try:
        return db.execute('SELECT COUNT(*) FROM operations WHERE kind=?', (kind,)).fetchone()[0]
    finally:
        db.close()


def post(w, case, path, body, status, *, code=None):
    reply = w.post(RF, case, path, body)
    assert reply.status_code == status, (case, reply.status_code, reply.get_json())
    got = reply.get_json()
    if code is not None:
        assert got['code'] == code, (case, got)
    return got


def read_profile(w, case, name):
    """GET /api/profile/<name>.yaml: the profile as the page reads it back (recorded), as a mapping."""
    reply = w.get(RF, case, f'{PROFILE_READ}{name}.yaml')
    assert reply.status_code == 200, (case, reply.status_code)
    return reply.get_json()


def recognition_context_fence_cases(gen):
    with gen.world('rf_match') as w:
        observed_world(w)
        alpha = page_context(w)
        assert alpha['account'] == 'alpha' and alpha['profile'] == 'mine'
        w.get(RF, 'read_config_the_page_context_comes_from', '/api/config')
        got = post(w, 'apply_saved_with_the_matching_context_is_applied', APPLY, dict(SAVED, expected_context=alpha), 200)
        assert got['ok'] and got['applied'] and got['profile'] == 'mine' and 'operation' not in got, got
        assert read_profile(w, 'read_the_profile_after_the_matching_apply', 'mine')['player']['max_tier'] == 19
        assert count(w, 'profile_write') == 1
    with gen.world('rf_switch') as w:
        observed_world(w)
        alpha = page_context(w)
        switch_to_beta(w)
        beta = page_context(w)
        assert beta['account'] == 'beta' and beta['profile'] == 'theirs'
        w.get(RF, 'read_config_after_another_client_switched_the_account', '/api/config')
        before = dict(mine=profile_bytes(w, 'mine'), theirs=profile_bytes(w, 'theirs'))
        refused = post(w, 'apply_saved_composed_for_alpha_is_refused_after_the_switch', APPLY, dict(SAVED, expected_context=alpha), 409, code='context_changed')
        assert refused['ok'] is False and refused['current_context'] == beta and 'operation' not in refused, refused
        assert dict(mine=profile_bytes(w, 'mine'), theirs=profile_bytes(w, 'theirs')) == before and count(w, 'profile_write') == 0
        assert read_profile(w, 'read_the_profile_the_refused_apply_would_have_changed', 'theirs')['player'].get('max_tier') is None
        legacy = post(w, 'apply_saved_without_the_field_lands_on_the_current_profile_the_legacy_page', APPLY, dict(SAVED), 200)
        assert legacy['applied'] and legacy['profile'] == 'theirs', legacy
        assert profile_bytes(w, 'theirs') != before['theirs'] and profile_bytes(w, 'mine') == before['mine'] and count(w, 'profile_write') == 1
        assert read_profile(w, 'read_the_profile_after_the_legacy_apply', 'theirs')['player']['max_tier'] == 7
    with gen.world('rf_malformed') as w:
        observed_world(w)
        alpha = page_context(w)
        before = profile_bytes(w, 'mine')
        for case, bad in (('apply_expected_context_null_is_refused_not_read_as_absent', None),
                          ('apply_expected_context_not_an_object', 'alpha'),
                          ('apply_expected_context_missing_the_serial', {k: v for k, v in alpha.items() if k != 'serial'}),
                          ('apply_expected_context_with_an_unknown_key_the_five_field_launch_shape', expected_context(w)),
                          ('apply_expected_context_serial_not_text', dict(alpha, serial=5))):
            refused = post(w, case, APPLY, dict(SAVED, expected_context=bad), 400, code='invalid_request')
            assert refused['ok'] is False, case
        assert profile_bytes(w, 'mine') == before and count(w, 'profile_write') == 0
        assert read_profile(w, 'read_the_profile_after_the_malformed_requests_nothing_was_applied', 'mine')['player'].get('max_tier') != 19
    with gen.world('rf_resend') as w:
        observed_world(w)
        alpha = page_context(w)
        post(w, 'apply_saved_composed_for_alpha_is_applied_and_the_answer_is_lost', APPLY, dict(SAVED, expected_context=alpha), 200)
        switch_to_beta(w)
        theirs = profile_bytes(w, 'theirs')
        refused = post(w, 'the_same_request_resent_after_the_switch_is_refused_not_applied_twice', APPLY, dict(SAVED, expected_context=alpha), 409, code='context_changed')
        assert refused['current_context'] == page_context(w)
        assert count(w, 'profile_write') == 1 and profile_bytes(w, 'theirs') == theirs
    with gen.world('rf_owner') as w:
        observed_world(w)
        alpha = page_context(w)
        switch_to_beta(w)
        beta = page_context(w)
        w.worker(4702, 'orchestrator.py', args=['--preset', 'bp_farm'])
        stale = post(w, 'stale_apply_while_a_runner_owns_the_device_is_told_the_context_changed', APPLY, dict(SAVED, expected_context=alpha), 409, code='context_changed')
        assert stale['current_context'] == beta
        current = post(w, 'current_apply_while_a_runner_owns_the_device_is_refused_by_the_owner', APPLY, dict(SAVED, expected_context=beta), 409, code='device_owned')
        assert current['ok'] is False
    with gen.world('rf_operation') as w:
        observed_world(w)
        alpha = page_context(w)
        op = seal(w, player=ALPHA_SEEN)
        got = post(w, 'apply_by_operation_with_the_matching_context_is_applied', APPLY, apply_body(op, expected_context=alpha), 200)
        assert got['applied'] and got['operation']['status'] == 'succeeded', got
    with gen.world('rf_operation_switch') as w:
        observed_world(w)
        alpha = page_context(w)
        op = seal(w, player=ALPHA_SEEN)
        switch_to_beta(w)
        beta = page_context(w)
        fence = post(w, 'apply_by_operation_composed_for_alpha_is_refused_by_the_fence_after_the_switch', APPLY, apply_body(op, expected_context=alpha), 409, code='context_changed')
        assert fence['current_context'] == beta and 'operation' not in fence, fence
        awaiting = w.get(RF, 'read_the_calibration_after_the_fence_refusal_it_still_awaits_apply', '/api/calibrate/status?operation_id=' + op['id']).get_json()
        assert awaiting['operation']['status'] == 'awaiting_apply', awaiting
        own = post(w, 'apply_by_operation_without_the_field_after_the_switch_is_refused_by_the_operation_check', APPLY, apply_body(op), 409, code='context_changed')
        assert 'current_context' not in own and own['operation']['id'] == op['id'], own
    with gen.world('rf_operation_replay') as w:
        observed_world(w)
        alpha = page_context(w)
        op = seal(w, player=ALPHA_SEEN)
        applied = post(w, 'apply_by_operation_is_applied_and_the_answer_is_lost', APPLY, apply_body(op, expected_context=alpha), 200)
        switch_to_beta(w)
        refused = post(w, 'the_resend_of_an_applied_operation_after_the_switch_is_refused_by_the_fence', APPLY, apply_body(op, expected_context=alpha), 409, code='context_changed')
        assert 'operation' not in refused and applied['operation']['status'] == 'succeeded'
        w.get(RF, 'read_the_calibration_whose_apply_the_page_must_settle_by_reading', '/api/calibrate/status?operation_id=' + op['id'])
    with gen.world('rf_no_config') as w:
        observed_world(w)
        alpha = page_context(w)
        before = profile_bytes(w, 'mine')
        (w.root / 'config.yaml').unlink()
        refused = post(w, 'apply_saved_when_config_yaml_is_missing_is_refused_and_creates_nothing', APPLY, dict(SAVED, expected_context=alpha), 409, code='context_changed')
        assert refused['current_context'] is None and not (w.root / 'config.yaml').exists() and profile_bytes(w, 'mine') == before and count(w, 'profile_write') == 0
    with gen.world('rf_unknown_account') as w:
        observed_world(w)
        alpha = page_context(w)
        w.edit_config(lambda cfg: cfg['instances']['main'].update(account='ghost'))
        refused = post(w, 'apply_saved_when_the_configuration_names_an_unknown_account_is_refused', APPLY, dict(SAVED, expected_context=alpha), 409, code='context_changed')
        assert refused['current_context'] is None and count(w, 'profile_write') == 0
    with gen.world('rf_launches') as w:
        observed_world(w)
        launch = expected_context(w)
        switch_to_beta(w)
        spawned, accepted = len(w.spawn.calls), {kind: count(w, kind) for kind in ('calibrate', 'observe', 'screen_analysis')}
        for case, path, body in (('calibrate_start_composed_for_alpha_is_refused_after_the_switch', START, dict(phases='c', bootstrap=True, idempotency_key='rf-start')),
                                 ('calibrate_observe_composed_for_alpha_is_refused_after_the_switch', OBSERVE, dict(idempotency_key='rf-observe')),
                                 ('screen_analysis_start_composed_for_alpha_is_refused_after_the_switch', ANALYSIS, dict(idempotency_key='rf-analysis'))):
            refused = post(w, case, path, dict(body, expected_context=launch), 409, code='context_changed')
            assert 'current_context' not in refused, case       # these refuse through the operation's own check: no current_context
        assert len(w.spawn.calls) == spawned and {kind: count(w, kind) for kind in accepted} == accepted
    with gen.world('rf_stop') as w:
        observed_world(w)
        alpha = page_context(w)
        op = w.running('calibrate', {'bootstrap': True, 'phases': 'c,m'}, script='calibrate.py', args=['--phases', 'c,m', '--bootstrap'])
        w.dash._procs_refresh()
        switch_to_beta(w)
        stopped = post(w, 'stop_composed_for_alpha_after_the_switch_is_not_refused_a_stop_is_a_revocation', STOP,
                       dict(operation_id=op['id'], expected_revision=op['revision'], expected_context=alpha), 200)
        assert stopped['operation']['status'] == 'cancelling' and stopped['stop']['state'] == 'stopping', stopped
