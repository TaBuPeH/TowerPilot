"""Takeover server side (roadmap item 3, T3c): the preview, the admission order, the receipt and retry, the restricted envelope and the transfer from a
standalone collector. The real Flask app over a temporary root and ledger; only process creation, psutil, the process table and adb state are fake. No device."""
import copy
import hashlib
import json

import pytest

from control import operations
from harvest_http import _battle_for, collector_row, enable, ops, running_collector
from harvest_support import NOW
from player import attach_policy, readiness
from runtime.attach_worker import WAVE_MARGIN
from takeover_http import ATTESTATION, URL, body, install_readiness, make_app, missing_row, post, preview


@pytest.fixture
def app(dash, launches, monkeypatch):
    app = make_app(dash, launches, monkeypatch)
    app.readiness = install_readiness(monkeypatch)
    return app


def stored(app, operation_id):
    return operations.ledger(app.dash.ROOT).get(operation_id)


def envelope_of(app, operation_id):
    return stored(app, operation_id)['request']['battle_envelope']


def with_collector(app, wave=4200):
    """A running standalone collector that owns the emulator and has a battle on the ledger, at `wave`."""
    enable(app)
    collector = running_collector(app)
    store, battle = _battle_for(app, collector)
    if wave is not None:
        store.update_status(battle['id'], battle['generation'], {'observed_wave': wave})
    app.rows.append(collector_row(collector))
    return collector, store, store.get(battle['id'])


def nothing_launched_besides(app, *kinds):
    return all(op['kind'] in kinds for op in ops(app))


# ---------------------------------------------------------------- preview
def test_the_run_list_gives_every_runnable_run_a_verdict(app):
    shown = app.client.get(URL).get_json()
    assert shown['ok'] and shown['preset'] is None and shown['schema_version'] == 1
    [farm] = [run for run in shown['runs'] if run['preset'] == 'bp_farm']
    assert farm['verdict'] == 'compatible' and farm['reasons'] == []
    assert set(shown['context']) == {'instance', 'account', 'serial', 'rendering', 'profile'}


def test_the_preview_of_a_run_carries_plan_revision_readiness_owners_and_battle(app):
    shown = preview(app)
    assert shown['run']['verdict'] == 'compatible' and len(shown['run']['policy_revision']) == 64
    assert shown['plan']['stripped'] == ['tier'] and shown['plan']['forced_off'] == list(attach_policy.FORCED_OFF)
    assert shown['readiness'] == {'ready': True, 'missing': [], 'advisory': []} and app.readiness.calls[-1]['attached'] is True
    assert shown['device'] == {'state': 'free', 'owners': []} and shown['setup_guard']['blocks_launch'] is False
    assert shown['current_battle'] == {'source': 'none', 'battle_id': None, 'generation': None, 'observed_wave': None,
                                       'operation_id': None, 'status': None, 'wave_source': 'none'}
    assert shown['blockers'] == []


def test_the_preview_reads_the_waves_source_from_the_collectors_record(app):
    collector, _, battle = with_collector(app, wave=4200)
    shown = preview(app)
    assert shown['device']['state'] == 'collector'
    assert shown['current_battle'] == {'source': 'collector_record', 'battle_id': battle['id'], 'generation': 'g1', 'observed_wave': 4200,
                                       'operation_id': collector['id'], 'status': 'active', 'wave_source': 'collector_record'}
    assert shown['blockers'] == []


def test_a_collector_battle_with_no_published_wave_says_the_wave_is_unknown(app):
    collector, _, battle = with_collector(app, wave=None)
    shown = preview(app)['current_battle']
    assert shown['battle_id'] == battle['id'] and shown['observed_wave'] is None and shown['wave_source'] == 'none' and shown['source'] == 'collector_record'
    assert post(app).status_code == 200                                                          # null confirmed, null still read: the same battle


def test_a_preview_changes_nothing_and_creates_no_ledger(app):
    import os
    path = operations.ledger_path(app.dash.ROOT)
    assert not os.path.exists(path)
    preview(app)
    app.client.get(URL)
    assert not os.path.exists(path)


def test_the_preview_reports_why_a_takeover_would_be_refused(app):
    app.readiness.missing = [missing_row(readiness.IN_TOURNAMENT)]
    assert preview(app)['blockers'] == ['tournament_recognition_missing']
    app.readiness.missing = [missing_row('x.png')]
    assert preview(app)['blockers'] == ['calibration_needed']
    app.readiness.missing = []
    app.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    shown = preview(app)
    assert shown['device']['state'] == 'owner_other' and shown['blockers'] == ['owner_other']


def test_an_incompatible_run_is_listed_with_its_reasons_and_no_plan(app, monkeypatch):
    real = app.dash._compiled_runs

    def runs(cfg):
        found = real(cfg)
        found['bp_farm'] = dict(found['bp_farm'], max_wave=500)
        return found
    monkeypatch.setattr(app.dash, '_compiled_runs', runs)
    [listed] = [run for run in app.client.get(URL).get_json()['runs'] if run['preset'] == 'bp_farm']
    assert listed['verdict'] == 'incompatible' and [r['code'] for r in listed['reasons']] == ['ends_battle']
    shown = preview(app)
    assert shown['plan'] is None and shown['readiness'] is None and shown['blockers'] == ['not_compatible']
    refused = post(app, shown=shown)
    assert refused.status_code == 409 and refused.get_json()['code'] == 'takeover_not_compatible'
    assert refused.get_json()['reasons'][0]['field'] == 'max_wave' and ops(app) == []


def test_a_run_the_start_button_cannot_start_is_not_takeable(app, monkeypatch):
    shown = preview(app)
    monkeypatch.setattr(app.dash, 'runnable_presets', lambda cfg: [])
    assert app.client.get(URL, query_string={'preset': 'bp_farm'}).status_code == 400
    assert app.client.get(URL).get_json()['runs'] == []
    reply = post(app, shown=shown)
    assert reply.status_code == 400 and reply.get_json()['reason'] == 'unknown_preset' and ops(app) == []


def test_an_unknown_run_is_a_400_in_the_preview_and_the_action(app):
    assert app.client.get(URL, query_string={'preset': 'bp_nope'}).status_code == 400
    shown = preview(app)
    reply = post(app, shown=shown, preset='bp_nope')
    assert reply.status_code == 400 and reply.get_json()['reason'] == 'unknown_preset' and ops(app) == []


# ---------------------------------------------------------------- the request
@pytest.mark.parametrize('change,reason', [
    (dict(battle_envelope={'preset': {}}), 'reserved_field'), (dict(harvest=True), 'reserved_field'), (dict(desire_revision=1), 'reserved_field'),
    (dict(surprise=1), 'unknown_field')])
def test_a_body_with_a_reserved_or_unknown_key_is_refused(app, change, reason):
    reply = app.client.post('/api/control', json=body(app, **change))
    got = reply.get_json()
    assert reply.status_code == 400 and got['code'] == 'invalid_request' and got['reason'] == reason and ops(app) == []


@pytest.mark.parametrize('drop', ['attach', 'expected_context', 'idempotency_key', 'preset'])
def test_a_missing_part_of_the_request_is_a_400(app, drop):
    request = body(app)
    del request[drop]
    reply = app.client.post('/api/control', json=request)
    assert reply.status_code == 400 and reply.get_json()['reason'] == 'missing_field' and reply.get_json()['field'] == drop


@pytest.mark.parametrize('changes,field', [
    (dict(attestation='yes'), 'attach.attestation'), (dict(policy_revision='abc'), 'attach.policy_revision'),
    (dict(tier={'value': 3, 'provenance': 'unknown'}), 'attach.tier.value'), (dict(extra=1), 'attach.extra')])
def test_a_malformed_attach_object_names_the_offending_field(app, changes, field):
    reply = post(app, attach_changes=changes)
    assert reply.status_code == 400 and reply.get_json()['field'] == field and ops(app) == []


# ---------------------------------------------------------------- receipt and retry
def test_launch_stores_a_restricted_envelope(app, monkeypatch):
    real = app.dash._compiled_runs

    def rich(cfg):                                   # a run that carries Home-only entry and equipment
        found = real(cfg)
        found['bp_farm'] = dict(found['bp_farm'], perk_bans=['x'], restart_via_home=True, dissonant_tab='d', loadout='as_is')
        return found
    monkeypatch.setattr(app.dash, '_compiled_runs', rich)
    shown = preview(app)
    assert {'loadout', 'perk_bans', 'restart_via_home', 'dissonant_tab', 'tier'} <= set(shown['plan']['stripped'])
    reply = post(app, shown=shown, tier={'value': 15, 'provenance': 'declared'})
    got = reply.get_json()
    assert reply.status_code == 200 and got['ok'], got
    envelope = envelope_of(app, got['operation_id'])
    assert envelope['config']['preset'] == envelope['preset_key'] == 'bp_farm'                  # a KEY, not an object
    assert envelope['preset'] == envelope['config']['presets']['bp_farm']                      # two equal restricted objects
    assert attach_policy.unrestricted_keys(envelope['preset']) == []
    assert envelope['controls']['routines']['repeat']['enabled'] is False and envelope['controls']['routines']['rewards']['enabled'] is False
    assert envelope['attach'] == {'mode': 'current_battle', 'policy_revision': shown['run']['policy_revision'],
                                  'tier': {'value': 15, 'provenance': 'declared'}, 'stripped': shown['plan']['stripped'], 'predecessor': None}
    assert attach_policy.validate_envelope_attach(envelope['attach']) == envelope['attach'] and attach_policy.run_tier(envelope['attach']) == 15
    assert 'attach' in stored(app, got['operation_id'])['request'] and 'battle_envelope' not in got['operation']['request']
    assert len(app.spawned) == 1


def test_the_worker_restores_the_restricted_source_it_was_accepted_with(app, monkeypatch):
    from runtime import battle_source
    real = app.dash._compiled_runs
    monkeypatch.setattr(app.dash, '_compiled_runs', lambda cfg: {**real(cfg), 'bp_farm': dict(real(cfg)['bp_farm'], perk_bans=['x'], restart_via_home=True)})
    got = post(app).get_json()
    monkeypatch.setenv('TOWER_OPERATION_DB', str(operations.ledger_path(app.dash.ROOT)))
    monkeypatch.setenv('TOWER_OPERATION_ID', got['operation_id'])
    cfg, _ = battle_source.accepted_source('main')
    assert cfg['preset'] == 'bp_farm' and attach_policy.unrestricted_keys(cfg['presets']['bp_farm']) == []
    assert cfg['presets']['bp_farm'] == envelope_of(app, got['operation_id'])['preset']


def test_the_stored_request_is_exactly_the_one_received(app):
    request = body(app)
    got = app.client.post('/api/control', json=request).get_json()
    assert {k: v for k, v in stored(app, got['operation_id'])['request'].items() if k != 'battle_envelope'} == {
        k: v for k, v in request.items() if k != 'idempotency_key'}


def test_an_identical_retry_replays_the_receipt_after_the_worker_ended(app):
    request = body(app)
    first = app.client.post('/api/control', json=request).get_json()
    again = app.client.post('/api/control', json=request).get_json()
    assert again['operation_id'] == first['operation_id'] and len(app.spawned) == 1
    store = operations.ledger(app.dash.ROOT)
    op = store.get(first['operation_id'])
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    store.transition(op['id'], op['revision'], 'failed', error={'code': 'attach_no_battle', 'message': 'no battle'})
    after = app.client.post('/api/control', json=request)
    assert after.status_code == 200 and after.get_json()['operation_id'] == first['operation_id'] and len(app.spawned) == 1
    assert after.get_json()['operation']['status'] == 'failed' and after.get_json()['operation']['error']['code'] == 'attach_no_battle'


@pytest.mark.parametrize('change', [
    lambda b: b['attach'].update(tier={'value': 9, 'provenance': 'declared'}),
    lambda b: b['attach'].update(policy_revision='0' * 64),
    lambda b: b['attach']['expected_battle'].update(observed_wave=7),
    lambda b: b.update(preset='bp_other')])
def test_the_same_key_with_a_changed_request_is_a_409_and_launches_nothing(app, change):
    request = body(app)
    assert app.client.post('/api/control', json=request).status_code == 200
    changed = copy.deepcopy(request)
    change(changed)
    reply = app.client.post('/api/control', json=changed)
    assert reply.status_code == 409 and len(app.spawned) == 1 and len(ops(app)) == 1


def test_server_facts_in_the_envelope_do_not_break_an_identical_retry(app):
    collector, store, battle = with_collector(app)
    shown = preview(app)
    request = body(app, shown=shown)
    first = app.client.post('/api/control', json=request)
    assert first.status_code == 200, first.get_json()
    assert envelope_of(app, first.get_json()['operation_id'])['attach']['predecessor'] is not None      # a server-derived fact the request never carried
    again = app.client.post('/api/control', json=request)
    assert again.status_code == 200 and again.get_json()['operation_id'] == first.get_json()['operation_id'] and 'transfer' not in again.get_json()


# ---------------------------------------------------------------- the fences of the admission order
def test_policy_revision_fence_refuses_a_run_that_changed_since_the_preview(app, monkeypatch):
    shown = preview(app)
    real = app.dash._compiled_runs
    monkeypatch.setattr(app.dash, '_compiled_runs', lambda cfg: {**real(cfg), 'bp_farm': dict(real(cfg)['bp_farm'], label='edited')})
    reply = post(app, shown=shown)
    got = reply.get_json()
    assert reply.status_code == 409 and got['code'] == 'policy_changed' and got['current_policy_revision'] != shown['run']['policy_revision']
    assert ops(app) == [] and app.spawned == []


def test_a_fence_refusal_kills_the_old_request_and_a_fresh_confirmation_is_a_new_command(app, monkeypatch):
    shown = preview(app)
    request = body(app, shown=shown)
    real = app.dash._compiled_runs
    monkeypatch.setattr(app.dash, '_compiled_runs', lambda cfg: {**real(cfg), 'bp_farm': dict(real(cfg)['bp_farm'], label='edited')})
    for _ in range(2):                                                                               # a manual resend of the same body and key is refused again, never updated
        refused = app.client.post('/api/control', json=request)
        assert refused.status_code == 409 and refused.get_json()['code'] == 'policy_changed'
    assert ops(app) == [] and app.spawned == []
    fresh = body(app)                                                                                # a fresh preview and a new confirmation (new key, new revision)
    assert fresh['idempotency_key'] != request['idempotency_key'] and fresh['attach']['policy_revision'] != request['attach']['policy_revision']
    assert app.client.post('/api/control', json=fresh).status_code == 200 and len(app.spawned) == 1


def test_a_battle_fence_refusal_is_not_a_recorded_command_and_does_not_fix_itself(app):
    shown = preview(app)
    request = body(app, shown=shown)
    collector, store, battle = with_collector(app)
    for _ in range(2):
        refused = app.client.post('/api/control', json=request)
        assert refused.status_code == 409 and refused.get_json()['code'] == 'attach_battle_changed'
    assert app.psutil.terminated == [] and [op['kind'] for op in ops(app)] == ['gems']
    assert app.client.post('/api/control', json=body(app)).status_code == 200                       # the dialog read the new battle: a new request


def test_context_fence_refuses_before_anything_else(app):
    request = body(app)
    request['expected_context']['account'] = 'someone-else'
    reply = app.client.post('/api/control', json=request)
    got = reply.get_json()
    assert reply.status_code == 409 and got['code'] == 'context_changed' and got['current_context']['instance'] == 'main' and app.spawned == []


def test_expected_battle_fence_runs_before_the_collector_stops(app):
    collector, store, battle = with_collector(app)
    shown = preview(app)
    for changes in (dict(expected_battle={'battle_id': None, 'generation': None, 'observed_wave': None}),
                    dict(expected_battle={'battle_id': battle['id'], 'generation': 'other', 'observed_wave': 4200}),
                    dict(expected_battle={'battle_id': battle['id'], 'generation': 'g1', 'observed_wave': 9000})):    # the record reads far below the wave seen
        reply = post(app, shown=shown, attach_changes=changes)
        got = reply.get_json()
        assert reply.status_code == 409 and got['code'] == 'attach_battle_changed', got
        assert got['current_battle']['battle_id'] == battle['id']
    assert app.psutil.terminated == [] and ops(app)[0]['id'] == collector['id'] and ops(app)[0]['status'] == 'running'


def test_a_battle_that_moved_forward_a_little_is_still_the_same_battle(app):
    collector, store, battle = with_collector(app, wave=4200)
    shown = preview(app)
    store.update_status(battle['id'], 'g1', {'observed_wave': 4200 + WAVE_MARGIN})
    assert post(app, shown=shown).status_code == 200


def test_a_page_too_old_to_vouch_for_the_battle_is_refused_before_the_collector_stops(app):
    """The worker's entry gate measures against the confirmed wave only: a record far ahead of it would leave a window for a replacement battle."""
    collector, store, battle = with_collector(app, wave=120)
    shown = preview(app)
    store.update_status(battle['id'], 'g1', {'observed_wave': 4200})
    refused = post(app, shown=shown)
    assert refused.status_code == 409 and refused.get_json()['code'] == 'attach_battle_changed'
    assert refused.get_json()['current_battle']['observed_wave'] == 4200 and app.psutil.terminated == []
    store.update_status(battle['id'], 'g1', {'observed_wave': 120 + WAVE_MARGIN + 1})
    assert post(app, shown=shown).status_code == 409


def test_a_collector_battle_that_appeared_after_the_preview_changes_the_answer(app):
    shown = preview(app)                     # nothing owns the emulator: both ids null
    with_collector(app)
    reply = post(app, shown=shown)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'attach_battle_changed' and app.psutil.terminated == []


# ---------------------------------------------------------------- readiness
def test_the_setup_guard_answers_before_readiness_and_before_the_battle_fence(app):
    from pathlib import Path
    from test_dashboard_admission import calibrate, home
    sealed = calibrate(app.dash, home)
    shown = preview(app)
    path = Path(sealed['result_refs']['calibrate_state.json']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    app.readiness.missing = [missing_row(readiness.IN_TOURNAMENT)]
    changed = dict(expected_battle={'battle_id': 'gone', 'generation': 'g', 'observed_wave': 5})
    reply = post(app, shown=shown, attach_changes=changed)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'setup_evidence_unreadable' and ops(app) == []


def test_missing_recognition_refuses_with_its_missing_list_and_no_collector_stop(app):
    collector, _, _ = with_collector(app)
    shown = preview(app)
    app.readiness.missing = [missing_row(readiness.IN_TOURNAMENT)]
    reply = post(app, shown=shown)
    got = reply.get_json()
    assert reply.status_code == 409 and got['code'] == 'tournament_recognition_missing' and got['missing'][0]['alternatives'] == [readiness.IN_TOURNAMENT]
    app.readiness.missing = [missing_row('home/x.png')]
    assert post(app, shown=shown).get_json()['code'] == 'calibration_needed'
    assert app.psutil.terminated == [] and ops(app)[0]['id'] == collector['id']


# ---------------------------------------------------------------- transfer
def test_collector_is_stopped_after_all_checks_and_the_choice_survives(app):
    collector, store, battle = with_collector(app)
    reply = post(app)
    got = reply.get_json()
    assert reply.status_code == 200, got
    assert got['transfer']['stopped'] == [collector['id']] and operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'cancelled'
    [run] = ops(app)
    assert run['kind'] == 'run' and run['id'] == got['operation_id']
    assert envelope_of(app, run['id'])['attach']['predecessor'] == {'battle_id': battle['id'], 'generation': 'g1', 'operation_id': collector['id']}
    assert store.get(battle['id'])['status'] == 'active'                                        # the game battle is never ended by the dashboard
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert shown['desired']['choice'] == 'on' and shown['transfer'] is None


def test_no_owner_launches_without_transfer_and_without_predecessor(app):
    got = post(app).get_json()
    assert 'transfer' not in got and envelope_of(app, got['operation_id'])['attach']['predecessor'] is None


def test_the_run_scope_guard_blocks_before_the_collector_stops(app):
    from pathlib import Path
    from test_dashboard_admission import calibrate, home
    sealed = calibrate(app.dash, home)
    collector, _, _ = with_collector(app)
    shown = preview(app)
    path = Path(sealed['result_refs']['calibrate_state.json']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    assert preview(app)['blockers'] == ['guard_blocks']
    reply = post(app, shown=shown)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'setup_evidence_unreadable'
    assert app.psutil.terminated == [] and ops(app)[0]['id'] == collector['id']


@pytest.mark.parametrize('row,code', [
    ({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'}, 'device_owned'),
    ({'pid': 4243, 'runner': 'combo', 'cmdline': 'python combo.py'}, 'device_owned')])
def test_other_owners_are_refused_with_the_shared_body(app, row, code):
    app.rows.append(row)
    shown = preview(app)
    reply = post(app, shown=shown)
    assert reply.status_code == 409 and reply.get_json()['code'] == code and app.psutil.terminated == [] and app.spawned == []


def test_a_run_operation_owner_is_refused_and_classified(app):
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {'preset': 'bp_farm'}, None)
    op, _ = store.accept('run', context, {}, 'other-run-1')
    store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    shown = preview(app)
    assert shown['device']['state'] == 'owner_run' and 'owner_run' in shown['blockers']
    reply = post(app, shown=shown)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'device_owned'


def test_the_day_plan_run_is_classified_as_the_scheduler(app):
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {'preset': 'combo'}, None)
    op, _ = store.accept('run', context, {'preset': 'combo'}, 'plan-run-1')
    store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    assert preview(app)['device']['state'] == 'owner_scheduler'


def test_a_collector_that_is_still_stopping_is_refused_with_the_shared_body(app):
    collector, _, _ = with_collector(app)
    shown = preview(app)
    app.psutil.lingering = True
    reply = post(app, shown=shown)
    got = reply.get_json()
    assert reply.status_code == 409 and got['code'] == 'collector_stopping' and got['owner']['operation_id'] == collector['id']
    assert all(op['kind'] == 'gems' for op in ops(app))
    assert app.dash._harvest_status()['transfer']['reason'] == 'run_start'          # kept, as `start` keeps it: a lagging collector is not relaunched under the run


def test_action_lock(app):
    shown = preview(app)
    assert app.dash._ACTION_LOCK.acquire(blocking=False)
    try:
        reply = post(app, shown=shown)
    finally:
        app.dash._ACTION_LOCK.release()
    assert reply.status_code == 409 and reply.get_json()['code'] == 'action_lock' and app.spawned == []


# ---------------------------------------------------------------- start is unchanged
def test_start_still_launches_the_unrestricted_envelope(app):
    reply = app.client.post('/api/control', json=dict(action='start', preset='bp_farm', idempotency_key='plain-1'))
    assert reply.status_code == 200
    envelope = envelope_of(app, reply.get_json()['operation_id'])
    assert 'attach' not in envelope and envelope['controls']['routines']['repeat']['enabled'] is True
    assert 'tier' in envelope['preset'] and envelope['config']['presets']['bp_farm']['tier'] == envelope['preset']['tier']


def test_a_takeover_key_cannot_be_replayed_as_a_plain_start(app):
    request = body(app)
    assert app.client.post('/api/control', json=request).status_code == 200
    plain = dict(action='start', preset='bp_farm', idempotency_key=request['idempotency_key'])
    assert app.client.post('/api/control', json=plain).status_code == 409 and len(app.spawned) == 1


# ---------------------------------------------------------------- review findings: the runner, reads that write nothing, cleanup
def test_a_run_whose_command_would_start_another_script_is_not_takeable(app, monkeypatch):
    """A coin run with a script of its own would be launched without the worker's entry gate and its restricted controls."""
    real = app.dash._compiled_runs
    monkeypatch.setattr(app.dash, '_compiled_runs', lambda cfg: {**real(cfg), 'bp_farm': dict(real(cfg)['bp_farm'], runner='interactions/shatter.py')})
    shown = preview(app)
    assert shown['run']['verdict'] == 'incompatible' and [(r['code'], r['field']) for r in shown['run']['reasons']] == [('unsupported_kind', 'runner')]
    refused = post(app, shown=shown)
    assert refused.status_code == 409 and refused.get_json()['code'] == 'takeover_not_compatible' and ops(app) == []


def test_a_refused_takeover_creates_no_ledger(app, tmp_path):
    from control import operations as ledgers
    shown = preview(app)
    path = ledgers.ledger_path(app.dash.ROOT)
    if path.exists():
        path.unlink()
    refused = post(app, shown=shown, attach_changes=dict(policy_revision='0' * 64))
    assert refused.status_code == 409 and refused.get_json()['code'] == 'policy_changed' and not path.exists()


def test_the_preview_creates_no_battle_tables(app):
    import sqlite3
    from control import operations as ledgers
    enable(app)
    running_collector(app)                       # a ledger with an operation but no battle schema yet
    path = ledgers.ledger_path(app.dash.ROOT)

    def tables():
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            return sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master"))
        finally:
            db.close()
    db = sqlite3.connect(path)                   # a ledger written before battles existed
    for table in ('battle_commands', 'battle_events', 'battles'):
        db.execute(f'DROP TABLE IF EXISTS {table}')
    db.commit()
    db.close()
    before = tables()
    assert 'battles' not in before
    shown = preview(app)
    assert shown['current_battle']['source'] == 'none' and tables() == before


def test_a_failure_while_the_collector_is_stopped_ends_the_transfer(app, monkeypatch):
    from control import harvest as harvest_api
    collector, store, battle = with_collector(app)
    request = body(app, shown=preview(app))
    monkeypatch.setattr(app.dash, '_transfer_from_collector', lambda cfg, body_, owners: (_ for _ in ()).throw(OSError('stop failed')))
    ended = []
    monkeypatch.setattr(harvest_api, 'end_transfer', lambda root, identity: ended.append(identity))
    assert app.client.post('/api/control', json=request).status_code == 500
    assert len(ended) == 1
