"""Dashboard routes for persistent harvesting: status, the choice, the lifecycle
host's tick, Stop, run transfer, the shared owner refusal and tap revocation.

The real Flask app over a temporary root and ledger. Process creation, psutil,
the process table and adb state are the only fake boundaries. No device and no
network."""
import hashlib
import os
import sqlite3

import pytest

from control import harvest, operations
from harvest_http import (
    _battle_for, body, collector_row, disable, enable, identity, make_app, ops, revision, revoke,
    running_collector, start_run, stub_status, taps_allowed,
)
from harvest_support import NOW, fake_inspect, schema
from towerpilot_workflows import harvest as ledger_h


@pytest.fixture
def app(dash, launches, monkeypatch):
    return make_app(dash, launches, monkeypatch)


# ---------------------------------------------------------------- status
def test_status_carries_the_versioned_harvest_projection(app):
    stub_status(app.dash, app.monkeypatch)
    payload = app.client.get('/api/status').get_json()
    shown = payload['harvest']
    assert (shown['version'], shown['state'], shown['code'], shown['desired']['choice']) == (1, 'off', 'never_enabled', 'unset')
    assert shown['host']['reconciler'] == 'inactive'          # no refresher thread in this process
    assert 'collection_guard' in payload and 'setup_battle_guard' in payload   # the M1 fields stay top level
    assert 'gem_harvesting' in app.client.get('/api/runtime').get_json()['capabilities']


def test_a_harvest_status_failure_never_fails_the_status_endpoint(app):
    stub_status(app.dash, app.monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError('ledger exploded')
    app.monkeypatch.setattr(harvest, 'status', broken)
    payload = app.client.get('/api/status').get_json()
    assert payload['harvest']['state'] == 'unavailable' and payload['harvest']['code'] == 'status_failed'
    assert 'setup_battle_guard' in payload


def test_harvest_status_on_a_cold_checkout_creates_nothing(app):
    ledger = operations.ledger_path(app.dash.ROOT)
    assert not ledger.exists()
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert shown['state'] == 'off' and not ledger.exists()
    operations.ledger(app.dash.ROOT)
    before = schema(ledger)
    app.dash._harvest_status(app.dash.load_config(), [])
    assert schema(ledger) == before and 'harvest_choices' not in before


# ----------------------------------------------------------- the choice
def test_enabling_binds_the_run_and_a_get_never_launches(app):
    stub_status(app.dash, app.monkeypatch)
    result = enable(app)
    assert result['replayed'] is False and result['desired']['choice'] == 'on' and result['desired']['revision'] == 1
    shown = result['harvest']
    assert shown['source']['valid'] is True and shown['source']['edit'] == dict(page='runs', blueprint='farm')
    assert shown['source']['policy']['ad_gems']['enabled'] is True
    assert (shown['state'], shown['code']) == ('starting', 'launch_due')
    for _ in range(3):
        app.client.get('/api/status')
        app.client.get('/api/harvest')
    assert app.spawned == [] and ops(app) == []


def test_the_refresher_tick_launches_one_fenced_collector(app):
    enable(app)
    app.dash._harvest_tick()
    assert len(app.spawned) == 1
    [op] = ops(app)
    assert (op['kind'], op['request']['harvest'], op['request']['desire_revision'], op['request']['preset']) == (
        'gems', True, 1, 'bp_farm')
    assert 'battle_envelope' in op['request'] and op['request']['policy_revision']
    app.dash._harvest_tick()
    assert len(app.spawned) == 1 and app.dash._HARVEST_HOST['last_tick_at'] == NOW
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert shown['operation']['harvest_managed'] is True and shown['operation']['id'] == op['id']


def test_the_process_refresher_loop_calls_the_tick_after_the_process_scan(app):
    order = []
    app.monkeypatch.setattr(app.dash, '_procs_refresh', lambda: order.append('procs'))
    app.monkeypatch.setattr(app.dash, '_harvest_tick', lambda: order.append('tick'))

    class Stop(Exception):
        pass

    class Wake:
        def clear(self):
            pass

        def wait(self, seconds):
            raise Stop

    class Inline:
        def __init__(self, target, **kwargs):
            self.target = target

        def is_alive(self):
            return False

        def start(self):
            with pytest.raises(Stop):
                self.target()
    app.monkeypatch.setattr(app.dash, '_PROCS_WAKE', Wake())
    app.monkeypatch.setattr(app.dash, '_PROCS_THREAD', None)
    app.monkeypatch.setattr(app.dash.threading, 'Thread', Inline)
    app.dash._start_procs_refresher()
    assert order == ['procs', 'tick']


def test_the_tick_yields_to_a_person_action_and_survives_its_own_errors(app):
    enable(app)
    assert app.dash._ACTION_LOCK.acquire(blocking=False)
    try:
        app.dash._harvest_tick()                      # a person's action holds the lock
    finally:
        app.dash._ACTION_LOCK.release()
    assert app.spawned == [] and app.dash._HARVEST_HOST['last_tick_at'] is None

    def boom(*args, **kwargs):
        raise RuntimeError('adb server vanished')
    app.monkeypatch.setattr(harvest, 'tick', boom)
    app.dash._harvest_tick()
    assert app.dash._HARVEST_HOST['last_error']['type'] == 'RuntimeError' and not app.dash._ACTION_LOCK.locked()
    assert app.dash._harvest_host()['last_error']['message'] == 'adb server vanished'


@pytest.mark.parametrize('mutate,status,code', [
    (lambda b: b.pop('idempotency_key'), 400, 'invalid_request'),
    (lambda b: b.update(enabled='yes'), 400, 'invalid_request'),
    (lambda b: b.update(expected_revision=7), 409, 'harvest_revision_changed'),
    (lambda b: b['expected_context'].update(serial='fake:9'), 409, 'context_changed'),
    (lambda b: b['expected_context'].update(profile='someone-else'), 409, 'context_changed'),
    (lambda b: b.update(preset='bp_missing'), 409, 'policy_invalid')])
def test_refusals_carry_stable_codes_and_record_nothing(app, mutate, status, code):
    request = body(app)
    mutate(request)
    response = app.client.post('/api/harvest', json=request)
    assert response.status_code == status and response.get_json()['code'] == code and response.get_json()['ok'] is False
    assert revision(app) == 0


def test_a_refusal_for_a_stale_revision_carries_the_current_choice(app):
    enable(app)
    response = app.client.post('/api/harvest', json=body(app, expected=0))
    assert response.status_code == 409 and response.get_json()['code'] == 'harvest_revision_changed'
    assert response.get_json()['desired']['revision'] == 1 and response.get_json()['desired']['choice'] == 'on'


def test_an_unusable_run_is_refused_with_its_source(app):
    import yaml
    path = os.path.join(app.dash.ROOT, 'profiles', 'mine.yaml')
    profile = yaml.safe_load(open(path, encoding='utf-8'))
    profile['policies']['gather']['shared']['ad_gems'] = False
    open(path, 'w', encoding='utf-8').write(yaml.safe_dump(profile))
    response = app.client.post('/api/harvest', json=body(app))
    payload = response.get_json()
    assert response.status_code == 409 and payload['code'] == 'policy_invalid'
    assert payload['source']['code'] == 'gathering_disabled' and payload['source']['valid'] is False
    assert revision(app) == 0


def test_a_lost_reply_is_read_back_by_sending_the_same_request(app):
    request = body(app, key='lost-reply-http')
    real = harvest.apply_choice

    def commit_then_lose_the_reply(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError('connection reset after the commit')
    with app.monkeypatch.context() as patched:
        patched.setattr(harvest, 'apply_choice', commit_then_lose_the_reply)
        lost = app.client.post('/api/harvest', json=request)
    assert lost.status_code == 500
    assert lost.get_json() == dict(ok=False, code='harvest_outcome_unknown', outcome='unknown',
                                   request_key='lost-reply-http', error=lost.get_json()['error'])
    disable(app)                                                           # the choice moves on meanwhile
    again = app.client.post('/api/harvest', json=request)
    assert again.status_code == 200 and again.get_json()['replayed'] is True
    assert again.get_json()['desired']['revision'] == 1 and revision(app) == 2


def test_off_reports_what_was_stopped_and_what_is_still_stopping(app):
    enable(app)
    op = running_collector(app)
    result = disable(app)
    assert result['stop']['stopped'] == [op['id']] and result['stop']['stopping'] == []
    assert app.psutil.terminated == [os.getpid()] and result['desired']['choice'] == 'off'
    enable(app)
    running_collector(app)
    app.psutil.lingering = True
    pending = disable(app)
    assert len(pending['stop']['stopping']) == 1 and pending['harvest']['state'] == 'stopping'


def test_off_and_tap_revocation_stay_reachable_during_the_global_action_lock(app):
    enable(app)
    assert app.dash._ACTION_LOCK.acquire(blocking=False)
    try:
        locked = app.client.post('/api/harvest', json=body(app, expected=1))
        assert (locked.status_code, locked.get_json()['code']) == (409, 'action_lock')
        assert revision(app) == 1                                           # an enable lost to the lock, recorded nothing
        off = app.client.post('/api/harvest', json=body(app, enabled=False))
        assert off.status_code == 200 and off.get_json()['desired']['choice'] == 'off'
        taps = app.client.post('/api/automation-access', json={'enabled': False})
        assert taps.status_code == 200 and taps.get_json()['revocation']['latched'] is True
        enabling = app.client.post('/api/automation-access', json={'enabled': True})
        assert (enabling.status_code, enabling.get_json()['code']) == (409, 'action_lock')
        junk = app.client.post('/api/harvest', data='not json', content_type='application/json')
        assert (junk.status_code, junk.get_json()['code']) == (409, 'action_lock')   # malformed bodies get no exemption
    finally:
        app.dash._ACTION_LOCK.release()
    assert revision(app) == 2


def test_automatic_harvest_fields_are_reserved_for_the_lifecycle_host(app):
    for extra in ({'harvest': True}, {'desire_revision': 1}):
        response = app.client.post('/api/control', json=dict(action='start_gems', preset='bp_farm',
                                                            idempotency_key='sneaky-key-1', **extra))
        assert response.status_code == 400 and response.get_json()['code'] == 'invalid_request'
    assert app.spawned == []


# ------------------------------------------------------------------- Stop
def test_a_persons_stop_pauses_harvesting_before_cancelling(app):
    enable(app)
    op = running_collector(app)
    order = []
    real_pause, real_cancel = harvest.pause, operations.cancel
    app.monkeypatch.setattr(harvest, 'pause', lambda *a, **k: order.append('pause') or real_pause(*a, **k))
    app.monkeypatch.setattr(operations, 'cancel', lambda *a, **k: order.append('cancel') or real_cancel(*a, **k))
    response = app.client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': op['revision']})
    assert response.status_code == 200 and order == ['pause', 'cancel']
    assert app.dash._harvest_status(app.dash.load_config(), [])['desired']['reason'] == 'stopped_by_person'
    app.dash._harvest_tick()
    assert len(app.spawned) == 1 and revision(app) == 2        # the Stop is not undone


def test_a_stale_stop_pauses_nothing(app):
    enable(app)
    op = running_collector(app)
    response = app.client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': op['revision'] - 1})
    assert response.status_code == 409 and revision(app) == 1 and ops(app)[0]['status'] == 'running'


def test_stopping_a_run_pauses_harvesting_too(app):
    enable(app)
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    run, _ = store.accept('run', context, {}, 'a-run-key')
    run = store.transition(run['id'], run['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    assert app.client.post(f"/api/operations/{run['id']}/cancel", json={'expected_revision': run['revision']}).status_code == 200
    assert revision(app) == 2


def test_stopping_a_non_driver_operation_leaves_harvesting_alone(app):
    enable(app)
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    scan, _ = store.accept('scan', context, {}, 'a-scan-key')
    app.client.post(f"/api/operations/{scan['id']}/cancel", json={'expected_revision': scan['revision']})
    assert revision(app) == 1


# ---------------------------------------------------------- run transfer
def test_a_run_takes_over_from_the_standalone_collector_and_the_choice_survives(app):
    enable(app)
    collector = running_collector(app)
    store, battle = _battle_for(app, collector)
    app.rows.append(collector_row(collector))
    response = start_run(app)
    payload = response.get_json()
    assert response.status_code == 200, payload
    assert payload['transfer']['stopped'] == [collector['id']] and payload['transfer']['stopping'] == []
    assert operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'cancelled'
    [run] = ops(app)
    assert run['kind'] == 'run' and run['id'] == payload['operation_id']
    assert store.get(battle['id'])['status'] == 'active'                    # the game battle is never ended
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert shown['desired']['choice'] == 'on' and shown['desired']['revision'] == 1
    assert shown['transfer'] is None and shown['state'] == 'owned_by_driver'
    app.dash._harvest_tick()
    assert len(app.spawned) == 2 and [o['kind'] for o in ops(app)] == ['run']      # collector + run only: no relaunch
    store_ops = operations.ledger(app.dash.ROOT)                                    # the run ends by itself: harvesting resumes
    [run] = ops(app)
    store_ops.transition(run['id'], run['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    store_ops.transition(run['id'], run['revision'] + 1, 'succeeded')
    app.rows.clear()
    app.clock.now = NOW + 60
    app.dash._harvest_tick()
    [again] = ops(app)
    assert (again['kind'], again['request']['harvest'], again['request']['desire_revision']) == ('gems', True, 1)


def test_a_run_whose_checks_fail_leaves_the_collector_untouched(app):
    from player import readiness
    enable(app)
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    app.monkeypatch.setattr(readiness, 'check', lambda root, cfg, preset: {
        'ready': False, 'missing': [{'runs': ['farm'], 'alternatives': ['x.png'], 'reasons': ['needed']}]})
    refused = start_run(app)
    assert refused.status_code == 409 and refused.get_json()['error'].startswith('Calibration is needed')
    stale_context = start_run(app, expected_context=dict(instance='main', account='someone', serial='fake:1',
                                                         rendering=None, profile='mine'))
    assert stale_context.status_code == 409
    assert app.psutil.terminated == [] and ops(app)[0]['id'] == collector['id'] and ops(app)[0]['status'] == 'running'


def test_a_run_over_an_unreleased_collector_is_refused_with_the_shared_body(app):
    enable(app)
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    app.psutil.lingering = True
    response = start_run(app)
    payload = response.get_json()
    assert response.status_code == 409 and payload['code'] == 'collector_stopping'
    assert payload['owner']['operation_id'] == collector['id'] and payload['retry'] == 'after_confirmed_stop'
    assert payload['recovery']['action'] == 'pause_harvesting'
    assert all(o['kind'] == 'gems' for o in ops(app))                       # nothing else was launched
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert shown['transfer'] is not None and shown['desired']['choice'] == 'on'   # the hold keeps a lagging collector out


def test_a_foreign_process_blocks_a_run_instead_of_being_taken_over(app):
    enable(app)
    app.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    response = start_run(app)
    assert response.status_code == 409 and response.get_json()['code'] == 'device_owned'
    assert response.get_json()['owner']['foreign'] is True and app.psutil.terminated == []


def test_the_run_scope_guard_is_checked_before_the_collector_is_stopped(app):
    from pathlib import Path
    from test_dashboard_admission import calibrate, home
    sealed = calibrate(app.dash, home)                  # releasing evidence: the collector may launch
    enable(app)
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    path = Path(sealed['result_refs']['calibrate_state.json']['path'])
    path.write_bytes(path.read_bytes() + b' ')            # the evidence now reads as changed: unreadable blocks a run
    response = start_run(app)
    assert response.status_code == 409 and response.get_json()['code'] == 'setup_evidence_unreadable'
    assert app.psutil.terminated == [] and operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'running'


# ------------------------------------------------------ shared owner refusal
OWNED = [
    ('post', '/api/accounts', dict(id='someone')),
    ('post', '/api/account-tier', dict(max_tier=3)),
    ('post', '/api/automation-access', dict(enabled=True)),
    ('post', '/api/control', dict(action='start_gems', preset='bp_farm', idempotency_key='owned-gems-1')),
    ('post', '/api/control', dict(action='start', preset='bp_farm', idempotency_key='owned-run-1')),
    ('post', '/api/scan/start', {}),
    ('post', '/api/calibrate/start', {}),
    ('post', '/api/calibrate/observe', {}),
    ('post', '/api/profile-activate', dict(name='mine')),
    ('post', '/api/wizard/tools/install', dict(accept_license=True)),
    ('post', '/api/wizard/launch', dict(emulator='MuMu')),
    ('post', '/api/wizard/reconnect', {}),
    ('post', '/api/wizard/adopt', dict(serial='127.0.0.1:5555')),
]


@pytest.mark.parametrize('method,url,payload', OWNED, ids=[url.rsplit('/', 1)[-1] + str(i) for i, (_, url, _) in enumerate(OWNED)])
def test_every_route_that_needs_an_idle_emulator_refuses_with_one_body(app, method, url, payload):
    app.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    response = getattr(app.client, method)(url, json=payload)
    refusal = response.get_json()
    assert response.status_code == 409, refusal
    assert refusal['ok'] is False and refusal['code'] == 'device_owned' and refusal['retry'] == 'after_confirmed_stop'
    assert refusal['owner']['kind'] == 'process' and refusal['owner']['foreign'] is True and refusal['recovery'] is None
    assert isinstance(refusal['error'], str) and refusal['error']


def test_the_clicker_step_and_profile_routes_use_the_same_refusal(app):
    from player.clicker import ACTIONS
    ident = identity(app)
    app.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    action = next(iter(ACTIONS))
    response = app.client.post('/api/clicker/step', json=dict(action=action, instance=ident['instance'], account=None))
    assert response.status_code == 409 and response.get_json()['code'] == 'device_owned'


def test_no_route_still_answers_a_runner_with_a_bare_409(app):
    import re
    source = open(app.dash.__file__, encoding='utf-8').read()
    assert not re.search(r'if _procs\(\):', source), 'a route bypasses the shared owner refusal'
    assert source.count('_require_idle(') >= 15


def test_a_harvest_collector_owner_offers_pause_harvesting_with_the_choice_revision(app):
    enable(app)
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    gems = app.client.post('/api/control', json=dict(action='start_gems', preset='bp_farm', idempotency_key='second-gems-1'))
    refusal = gems.get_json()
    assert gems.status_code == 409 and refusal['code'] == 'collector_running'
    assert refusal['owner']['harvest_managed'] is True and refusal['owner']['operation_id'] == collector['id']
    assert refusal['recovery'] == dict(action='pause_harvesting', operation_id=collector['id'], expected_revision=1)
    accounts = app.client.post('/api/accounts', json=dict(id='someone'))
    assert accounts.get_json()['code'] == 'device_owned' and accounts.get_json()['recovery']['action'] == 'pause_harvesting'


def test_a_run_owner_offers_stop_operation(app):
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    run, _ = store.accept('run', context, {}, 'owner-run-key')
    run = store.transition(run['id'], run['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})
    app.rows.append({'pid': os.getpid(), 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py', 'created': 1.0})
    refusal = app.client.post('/api/accounts', json=dict(id='someone')).get_json()
    assert refusal['recovery'] == dict(action='stop_operation', operation_id=run['id'], expected_revision=run['revision'])
    assert refusal['owner']['kind'] == 'operation' and refusal['owner']['operation_kind'] == 'run'


# ---------------------------------------------------------- tap revocation
def test_revoking_with_nothing_running_latches_the_permission_and_marks_the_device(app):
    from runtime import device_lease
    revocation = revoke(app)
    assert (revocation['state'], revocation['latched'], revocation['fenced']) == ('nothing_running', True, True)
    assert revocation['harvest'] == dict(paused=False, revision=None) and revocation['errors'] == []
    ident = identity(app)
    assert taps_allowed(app) is False and device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port'])


def test_revoking_with_a_collector_pauses_the_choice_stops_its_input_then_latches(app):
    enable(app)
    collector = running_collector(app)
    order = []
    from runtime import device_lease
    real = dict(revoke=device_lease.revoke_input, pause=harvest.pause, stop=harvest.stop_collectors,
                fence=device_lease.fence_input, save=app.dash.save_config)
    app.monkeypatch.setattr(device_lease, 'revoke_input', lambda *a, **k: order.append('revoke') or real['revoke'](*a, **k))
    app.monkeypatch.setattr(harvest, 'pause', lambda *a, **k: order.append('pause') or real['pause'](*a, **k))
    app.monkeypatch.setattr(harvest, 'stop_collectors', lambda *a, **k: order.append('stop') or real['stop'](*a, **k))
    app.monkeypatch.setattr(device_lease, 'fence_input', lambda *a, **k: order.append('fence') or real['fence'](*a, **k))
    app.monkeypatch.setattr(app.dash, 'save_config', lambda cfg: order.append('persist') or real['save'](cfg))
    revocation = revoke(app)
    assert order == ['revoke', 'pause', 'stop', 'persist', 'fence']
    assert revocation['state'] == 'input_stopped' and revocation['stopped'] == [collector['id']]
    assert revocation['harvest'] == dict(paused=True, revision=2) and revocation['still_owned'] == []
    assert operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'cancelled'
    assert taps_allowed(app) is False
    app.dash._harvest_tick()
    assert len(app.spawned) == 1                      # revoked and paused: nothing relaunches


def test_a_collector_that_is_still_exiting_is_stopping_not_stopped(app):
    enable(app)
    running_collector(app)
    app.psutil.lingering = True
    revocation = revoke(app)
    assert revocation['state'] == 'stopping' and len(revocation['stopping']) == 1 and revocation['stopped'] == []
    assert revocation['latched'] is True


def test_a_running_full_driver_is_never_killed_and_is_reported_truthfully(app):
    from runtime import device_lease
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    run, _ = store.accept('run', context, {}, 'revoke-run-key')
    run = store.transition(run['id'], run['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    unfenced = revoke(app)
    assert unfenced['state'] == 'not_enforced'
    assert [(o['operation_id'], o['input']) for o in unfenced['still_owned']] == [(run['id'], 'unfenced')]
    assert unfenced['still_owned'][0]['recovery']['action'] == 'stop_operation'
    assert store.get(run['id'])['status'] == 'running' and app.psutil.terminated == []
    app.monkeypatch.setattr(device_lease, 'lease_state',
                            lambda *a, **k: dict(owned=True, delegated=False, input_gate='enforced'))
    enforced = revoke(app)
    assert enforced['state'] == 'input_stopped'
    assert [(o['operation_id'], o['input']) for o in enforced['still_owned']] == [(run['id'], 'refused')]


def test_a_failing_marker_write_is_a_failure_and_the_rest_still_runs(app):
    # H2INPUT-01: without a durable refusal nothing may read as revoked, fenced or latched.
    from runtime import device_lease
    ident = identity(app)
    enable(app)
    real = device_lease.revoke_input
    app.monkeypatch.setattr(device_lease, 'revoke_input', lambda *a, **k: (_ for _ in ()).throw(OSError('lease directory')))
    response = app.client.post('/api/automation-access', json={'enabled': False})
    assert response.status_code == 500
    body = response.get_json()
    assert body['ok'] is False and body['code'] == 'input_revocation_failed'
    revocation = body['revocation']
    assert [e['step'] for e in revocation['errors']] == ['revoke_input']
    assert revocation['state'] == 'failed' and revocation['revoked_at'] is None
    assert revocation['fenced'] is False and revocation['latched'] is False
    assert revocation['config_persisted'] is True and taps_allowed(app) is False   # configuration is not enforcement
    assert revocation['harvest']['paused'] is True
    assert device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port']) is False
    app.monkeypatch.setattr(device_lease, 'revoke_input', real)                     # a retry publishes the marker
    retried = revoke(app)
    assert retried['state'] == 'nothing_running' and retried['errors'] == [] and retried['latched'] is True
    assert device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port']) is True


def test_enabling_clears_the_revocation_and_persists_the_permission(app):
    from runtime import device_lease
    ident = identity(app)
    revoke(app)
    assert device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port']) is True
    response = app.client.post('/api/automation-access', json={'enabled': True})
    assert response.status_code == 200 and response.get_json() == {'ok': True, 'enabled': True}
    assert device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port']) is False
    assert taps_allowed(app) is True


def test_a_revoked_device_is_blocked_visibly_even_when_the_config_says_taps_are_on(app):
    from runtime import device_lease
    enable(app)
    ident = identity(app)
    device_lease.revoke_input(ident['serial'], ident['adb_host'], ident['adb_port'])
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert (shown['state'], shown['code'], shown['input_revoked']) == ('blocked', 'input_revoked', True)
    app.dash._harvest_tick()
    assert app.spawned == []


# ------------------------------------- the worker re-reads the choice itself
@pytest.fixture
def collector_call(app, worker_env):
    """The command and environment of the collector the lifecycle host launched."""
    from harvest_support import EnvSpawn
    spawn = EnvSpawn()
    app.monkeypatch.setattr(app.dash.subprocess, 'Popen', spawn)
    enable(app)
    app.dash._harvest_tick()
    assert len(spawn.calls) == 1
    return spawn.calls[0]


def record_off(app):
    """An Off recorded by another host that never got to stop anything."""
    ledger_h.Harvest(operations.ledger_path(app.dash.ROOT)).set_choice(
        identity(app), enabled=False, preset=None, label=None, expected_revision=None, bound=None,
        reason='paused_by_person', changed_by='person', request_key='off-by-another-host', fingerprint='x')


def test_a_launched_collector_reaches_its_first_frame_while_the_choice_stands(app, collector_call):
    from harvest_support import Reached, run_worker
    order, events, error = run_worker(app.dash.ROOT, app.monkeypatch, collector_call)
    assert isinstance(error, Reached) and order == ['runners', 'frame']
    assert 'gem_collector_started' in [kind for kind, _, _ in events]


def test_an_off_recorded_before_the_first_input_stops_the_worker_before_any_frame(app, collector_call):
    from harvest_support import run_worker
    record_off(app)
    order, events, error = run_worker(app.dash.ROOT, app.monkeypatch, collector_call)
    assert type(error).__name__ == 'CollectorRefused' and 'harvest_off' in str(error)
    assert order == ['runners']                                     # no frame, no session, no input
    [(_, fields, _)] = [e for e in events if e[0] == 'gem_collector_refused']
    assert fields['reason'] == 'harvest_off'
    [final] = [op for op in [operations.ledger(app.dash.ROOT).get(collector_call[1]['TOWER_OPERATION_ID'])]]
    assert (final['status'], final['error']['code']) == ('failed', 'CollectorRefused')
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    assert shown['failures'] == 0 and shown['circuit_open'] is False   # a fence refusal never opens the circuit


def test_a_new_revision_before_the_first_input_stops_the_stale_worker(app, collector_call):
    from harvest_support import run_worker
    disable(app)
    enable(app)                                                        # revision 3: the launch was for revision 1
    order, events, error = run_worker(app.dash.ROOT, app.monkeypatch, collector_call)
    assert type(error).__name__ in ('CollectorRefused', 'Conflict') and 'frame' not in order


def test_the_running_workers_fence_notices_an_off_without_the_dashboard(app, collector_call, monkeypatch):
    import gem_collector
    for name in ('TOWER_OPERATION_DB', 'TOWER_OPERATION_ID'):
        monkeypatch.setenv(name, collector_call[1][name])
    check = gem_collector.automatic_fence()
    check()                                                            # the choice still names this launch
    record_off(app)
    with pytest.raises(ledger_h.CollectorRefused, match='harvest_off'):
        check()


def test_the_running_workers_loop_rechecks_the_choice_and_sends_nothing_more_after_an_off(app, collector_call, monkeypatch):
    import time as real_time
    from types import SimpleNamespace

    import numpy as np

    from harvest_support import run_worker
    from vision import screen, wave_reader
    offset = {'seconds': 0.0}
    real_monotonic = real_time.monotonic
    monkeypatch.setattr(real_time, 'monotonic', lambda: real_monotonic() + offset['seconds'])
    monkeypatch.setattr(real_time, 'sleep', lambda seconds: offset.update(seconds=offset['seconds'] + 6))   # a slow iteration
    monkeypatch.setattr(screen, 'identify', lambda frame: SimpleNamespace(name='home'))
    monkeypatch.setattr(wave_reader, 'read_wave', lambda frame: None)
    frames = []

    def frame():
        frames.append(1)
        if len(frames) == 1:
            record_off(app)                         # recorded while the worker is running; nobody cancels it
        if len(frames) > 4:
            raise AssertionError('the worker kept grabbing frames after its choice was gone')
        return np.zeros((8, 8, 3), np.uint8)
    order, events, error = run_worker(app.dash.ROOT, app.monkeypatch, collector_call, frame=frame)
    assert type(error).__name__ == 'CollectorRefused' and 'harvest_off' in str(error)
    assert len(frames) == 1, 'a frame was grabbed after the choice was gone'


def test_a_transfer_hold_stops_a_worker_that_has_not_sent_input_yet(app, collector_call, monkeypatch):
    import gem_collector
    for name in ('TOWER_OPERATION_DB', 'TOWER_OPERATION_ID'):
        monkeypatch.setenv(name, collector_call[1][name])
    check = gem_collector.automatic_fence()
    harvest.begin_transfer(app.dash.ROOT, identity(app), 'run_start', now=NOW)
    with pytest.raises(ledger_h.CollectorRefused, match='transfer_pending'):
        check()


def test_a_collector_a_person_started_has_no_fence(app, monkeypatch):
    import gem_collector
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {'preset': 'bp_farm'}, None)
    op, _ = store.accept('gems', context, {'preset': 'bp_farm'}, 'manual-collector-key')
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    assert gem_collector.automatic_fence() is None
    monkeypatch.delenv('TOWER_OPERATION_ID')
    monkeypatch.delenv('TOWER_OPERATION_DB')
    assert gem_collector.automatic_fence() is None


def test_two_dashboards_ticking_at_once_start_one_collector(app, dash, monkeypatch):
    import importlib.util
    import threading
    from pathlib import Path
    enable(app)
    spec = importlib.util.spec_from_file_location('admission_dashboard_second', Path(dash.__file__))
    second = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(second)
    for name in ('ROOT', 'CONFIG_PATH'):
        monkeypatch.setattr(second, name, getattr(dash, name))
    monkeypatch.setattr(second, '_procs_cached', lambda: [])
    monkeypatch.setattr(second, '_device_state', lambda cfg: {'device_state': 'device', 'port_open': True, 'serial': 'fake:1'})
    barrier = threading.Barrier(2)

    def tick(module):
        barrier.wait()
        module._harvest_tick()
    threads = [threading.Thread(target=tick, args=(module,)) for module in (dash, second)]
    [thread.start() for thread in threads]
    [thread.join(60) for thread in threads]
    assert len(app.spawned) == 1 and len(ops(app)) == 1
    assert dash._HARVEST_HOST['last_error'] is None and second._HARVEST_HOST['last_error'] is None


# ======================================================== review round r1 fixes
def pre_spawn(app, kind='run', **request):
    """An operation the ledger has accepted whose worker process does not exist yet (the pre-spawn window)."""
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    op, _ = store.accept(kind, context, dict(request), f'pre-spawn-{next(KEY_COUNTER)}')
    assert op['worker'] is None and op['status'] == 'accepted'
    return op


KEY_COUNTER = iter(range(1, 10_000))


@pytest.mark.parametrize('method,url,payload', OWNED, ids=[url.rsplit('/', 1)[-1] + str(i) for i, (_, url, _) in enumerate(OWNED)])
def test_an_accepted_operation_without_a_process_owns_the_emulator_for_every_idle_only_route(app, method, url, payload):
    op = pre_spawn(app)
    before = open(os.path.join(app.dash.ROOT, 'config.yaml'), 'rb').read()
    response = getattr(app.client, method)(url, json=payload)
    refusal = response.get_json()
    assert response.status_code == 409, refusal
    assert (refusal['ok'], refusal['code'], refusal['retry']) == (False, 'device_owned', 'after_confirmed_stop')
    assert refusal['owner']['operation_id'] == op['id'] and refusal['owner']['kind'] == 'operation'
    assert refusal['recovery'] == dict(action='stop_operation', operation_id=op['id'], expected_revision=1)
    assert open(os.path.join(app.dash.ROOT, 'config.yaml'), 'rb').read() == before      # nothing was changed
    assert operations.ledger(app.dash.ROOT).get(op['id'])['status'] == 'accepted'


def test_the_recovery_for_a_pre_spawn_owner_works_and_the_route_then_proceeds(app):
    op = pre_spawn(app)
    assert app.client.post('/api/profile-activate', json=dict(name=None)).status_code == 409
    stopped = app.client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': op['revision']})
    assert stopped.status_code == 200 and stopped.get_json()['operation']['status'] == 'cancelled'
    assert app.client.post('/api/profile-activate', json=dict(name=None)).status_code != 409


def test_a_pre_spawn_operation_whose_worker_never_came_is_retired_not_an_owner_forever(app):
    op = pre_spawn(app)
    app.clock.now = NOW + 120                                # past the launch grace
    response = app.client.post('/api/profile-activate', json=dict(name=None))
    assert response.status_code != 409
    assert operations.ledger(app.dash.ROOT).get(op['id'])['status'] == 'interrupted'


def test_a_pre_spawn_collector_is_reported_as_the_collector_and_a_run_takes_it_over(app):
    enable(app)
    app.dash._harvest_tick()                                  # accepted, not spawned into a process yet
    [collector] = ops(app)
    refusal = app.client.post('/api/control', json=dict(action='start_gems', preset='bp_farm', idempotency_key='second-gems-2')).get_json()
    assert refusal['code'] == 'collector_running' and refusal['recovery']['action'] == 'pause_harvesting'
    response = start_run(app)
    assert response.status_code == 200, response.get_json()
    assert operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'cancelled'
    assert [o['kind'] for o in ops(app)] == ['run']


def test_a_scan_or_calibration_the_route_judges_itself_is_not_double_refused(app):
    pre_spawn(app, kind='calibrate')
    response = app.client.post('/api/calibrate/start', json={})
    assert response.status_code == 409 and response.get_json().get('code') != 'device_owned'


# ---- GET paths never create schema and never change the ledger
GET_ROUTES = ['/api/status', '/api/harvest', '/api/runtime', '/api/schedule/status']


def get_all(app):
    return [(url, app.client.get(url).status_code) for url in GET_ROUTES]


def test_every_ledger_reading_get_route_is_read_only_on_a_cold_checkout(app):
    stub_status(app.dash, app.monkeypatch)
    ledger = operations.ledger_path(app.dash.ROOT)
    assert not ledger.exists()
    assert all(code == 200 for _, code in get_all(app))
    assert not ledger.exists(), 'a GET initialized the ledger'


def test_the_status_route_with_a_ledger_that_predates_harvesting_creates_no_tables(app):
    stub_status(app.dash, app.monkeypatch)
    operations.ledger(app.dash.ROOT)
    ledger = operations.ledger_path(app.dash.ROOT)
    before = (schema(ledger), hashlib.sha256(ledger.read_bytes()).hexdigest())
    assert all(code == 200 for _, code in get_all(app))
    assert (schema(ledger), hashlib.sha256(ledger.read_bytes()).hexdigest()) == before
    assert 'harvest_choices' not in before[0]


def test_get_routes_never_retire_a_dead_worker_but_the_refresher_does(app):
    stub_status(app.dash, app.monkeypatch)
    enable(app)
    collector = running_collector(app)
    app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))   # the worker died without an outcome
    ledger = operations.ledger_path(app.dash.ROOT)
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert all(code == 200 for _, code in get_all(app))
    shown = app.client.get('/api/harvest').get_json()['harvest']
    assert shown['state'] == 'unavailable' and shown['code'] == 'worker_not_running'
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before
    assert operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'running'
    app.dash._harvest_tick()                                                  # the existing refresher retires it
    assert operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'interrupted'


def test_the_refresher_does_not_create_a_ledger_when_nothing_was_ever_chosen(app):
    ledger = operations.ledger_path(app.dash.ROOT)
    app.dash._harvest_tick()
    assert not ledger.exists()


# ----- the GET routes that open the ledger never move recorded lifecycle state
def recorded(app):
    """Every recorded operation and battle row, exactly as stored."""
    db = sqlite3.connect(operations.ledger_path(app.dash.ROOT))
    try:
        present = set(schema(operations.ledger_path(app.dash.ROOT)))
        return {table: sorted(db.execute(f'SELECT * FROM {table}').fetchall())
                for table in ('operations', 'battles', 'battle_commands', 'battle_events') if table in present}
    finally:
        db.close()


def running_run(app):
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    op, _ = store.accept('run', context, {}, 'orphan-run')
    return store.transition(op['id'], op['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})


@pytest.mark.parametrize('kind,url', [
    pytest.param('calibrate', '/api/calibrate/status', id='calibrate-status'),
    pytest.param('scan', '/api/scan/status', id='scan-status'),
    pytest.param('screen_analysis', '/api/screen-analysis/status', id='screen-analysis-status'),
    pytest.param('screen_analysis', '/api/screen-analysis/frame.png', id='screen-analysis-frame'),
    pytest.param('scan', '/api/operations/{id}', id='operation'),
    pytest.param('gems', '/api/operations/{id}', id='collector-operation'),
])
def test_a_get_never_retires_a_worker_that_died_but_the_refresher_does(app, kind, url):
    stub_status(app.dash, app.monkeypatch)
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    op, _ = store.accept(kind, context, {}, f'dead-{kind}')
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 4242, 'creation_time': 1.0})
    app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))   # it died without an outcome
    before = recorded(app)
    reply = app.client.get(url.format(id=op['id']))
    assert reply.status_code in (200, 409), reply.get_data(as_text=True)   # 409: frame of an unfinished analysis
    assert recorded(app) == before and store.get(op['id'])['status'] == 'running'
    app.dash._harvest_tick()
    assert store.get(op['id'])['status'] == 'interrupted'
    assert app.client.get(f"/api/operations/{op['id']}").get_json()['operation']['status'] == 'interrupted'


def test_a_get_never_finishes_a_battle_but_the_refresher_does(app):
    stub_status(app.dash, app.monkeypatch)
    store_ops = operations.ledger(app.dash.ROOT)
    run = running_run(app)
    battles_store, battle = _battle_for(app, run)
    store_ops.transition(run['id'], run['revision'], 'failed')                    # the driver ended; its battle is orphaned
    before = recorded(app)
    active = app.client.get('/api/battles/active').get_json()['battle']
    assert active is not None and active['id'] == battle['id'] and active['status'] == 'active'
    history = app.client.get('/api/battles').get_json()['battles']
    assert [row['status'] for row in history] == ['active']
    assert app.client.get(f"/api/battles/{battle['id']}").status_code == 200
    assert recorded(app) == before                                                  # nothing moved, nothing appended
    app.dash._harvest_tick()                                                        # the one place that finishes it
    assert battles_store.get(battle['id'])['status'] == 'interrupted'
    assert app.client.get('/api/battles/active').get_json()['battle'] is None
    assert [row['status'] for row in app.client.get('/api/battles').get_json()['battles']] == ['interrupted']


def test_the_refresher_finishes_orphan_battles_of_every_emulator_and_creates_nothing_cold(app):
    ledger = operations.ledger_path(app.dash.ROOT)
    app.dash._harvest_tick()
    assert not ledger.exists()                                                      # no ledger: nothing to finish
    stub_status(app.dash, app.monkeypatch)
    run = running_run(app)
    battles_store, battle = _battle_for(app, run)
    operations.ledger(app.dash.ROOT).transition(run['id'], run['revision'], 'failed')
    cfg = app.dash.load_config()
    cfg['active_instance'] = 'other'                                                # the person is looking at another emulator
    app.dash.save_config(cfg)
    app.dash._harvest_tick()
    assert battles_store.get(battle['id'])['status'] == 'interrupted'


# ----- the window between a worker's death and the refresher retiring it
def dead_collector(app):
    """An enabled harvest whose collector's worker died without an outcome. Nothing has retired it yet."""
    enable(app)
    collector = running_collector(app)
    app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))
    return collector


def test_a_dead_worker_is_an_owner_to_every_read_and_to_the_ledger_until_it_is_retired(app):
    stub_status(app.dash, app.monkeypatch)
    collector = dead_collector(app)
    store = operations.ledger(app.dash.ROOT)
    assert [o['operation_id'] for o in harvest.owners(app.dash.ROOT, app.dash.load_config(), [])] == [collector['id']]
    with pytest.raises(operations.Conflict, match='already owns'):
        store.accept('run', operations.context(app.dash.ROOT, app.dash.load_config(), {}, None), {}, 'second-owner')
    shown = app.client.get('/api/harvest').get_json()['harvest']
    assert shown['state'] == 'unavailable' and shown['code'] == 'worker_not_running'   # never 'ready' or 'off'
    assert store.get(collector['id'])['status'] == 'running'                              # a GET did not retire it


def test_a_launch_retires_the_dead_worker_itself_and_admits_exactly_one_owner(app):
    collector = dead_collector(app)
    store = operations.ledger(app.dash.ROOT)
    launched = len(app.spawned)
    assert app.client.post('/api/control', json=dict(action='start', preset='bp_farm', idempotency_key='after-death')
                           ).status_code == 200
    assert store.get(collector['id'])['status'] == 'interrupted'
    assert len(app.spawned) == launched + 1 and [op['kind'] for op in ops(app)] == ['run']   # one new owner, the dead one is gone


def test_a_live_worker_is_still_refused_the_moment_it_is_a_second_owner(app):
    enable(app)
    running_collector(app)                                                                # alive: worker_alive says so
    app.rows.append({'pid': os.getpid(), 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py', 'created': 1.0})
    refused = app.client.post('/api/accounts', json={'id': 'someone'})
    assert refused.status_code == 409 and refused.get_json()['code'] == 'device_owned'


def test_off_against_a_worker_that_died_unretired_reports_nothing_still_running(app):
    collector = dead_collector(app)
    off = disable(app)
    assert off['desired']['enabled'] is False
    assert off['stop']['stopped'] == [] and off['stop']['stopping'] == []                 # nothing was running to stop
    assert operations.ledger(app.dash.ROOT).get(collector['id'])['status'] == 'interrupted'
    assert ops(app) == []


def test_tap_disable_against_a_run_whose_worker_died_unretired_still_publishes_the_marker(app):
    from runtime import device_lease
    ident = identity(app)
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    run, _ = store.accept('run', context, {}, 'dies-first')
    store.transition(run['id'], run['revision'], 'running', worker={'pid': 4242, 'creation_time': 1.0})
    app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))
    revocation = revoke(app)
    assert device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port']) is True
    assert revocation['state'] == 'nothing_running' and revocation['errors'] == [] and revocation['still_owned'] == []
    assert taps_allowed(app) is False and store.get(run['id'])['status'] == 'interrupted'


# ----- finishing an orphan battle record: ledger only, idempotent, safe across dashboards
def test_finishing_an_orphan_battle_record_never_touches_the_device_or_a_live_battle(app):
    from device import act, adbclient, capture

    def forbidden(*args, **kwargs):
        raise AssertionError('the reconciler reached the device')
    for module, name in ((adbclient, 'shell'), (adbclient, 'exec_out'), (act, 'tap'), (act, 'swipe'), (capture, 'grab')):
        app.monkeypatch.setattr(module, name, forbidden)
    live = running_run(app)                                                             # its driver is alive
    live_store, live_battle = _battle_for(app, live)
    app.dash._harvest_tick()
    assert live_store.get(live_battle['id'])['status'] == 'active'                      # a live driver's record stays open
    operations.ledger(app.dash.ROOT).transition(live['id'], live['revision'], 'failed')  # the driver ended
    app.dash._harvest_tick()
    assert live_store.get(live_battle['id'])['status'] == 'interrupted'
    assert live_store.get(live_battle['id'])['outcome'] == 'unknown'                    # never a claim about how it went


def test_two_dashboards_finishing_the_same_orphan_battle_write_one_ending(app):
    import threading
    run = running_run(app)
    battles_store, battle = _battle_for(app, run)
    operations.ledger(app.dash.ROOT).transition(run['id'], run['revision'], 'failed')
    from control import battles as control_battles
    gate, errors = threading.Barrier(2, timeout=5), []
    real = control_battles.operations.resolve

    def meet(root, operation_id):
        gate.wait()                                                                        # both have listed the battle
        return real(root, operation_id)
    app.monkeypatch.setattr(control_battles.operations, 'resolve', meet)

    def host():
        try:
            control_battles.reconcile(app.dash.ROOT, control_battles.ledger(app.dash.ROOT))
        except Exception as exc:                                                           # noqa: BLE001 - reported below
            errors.append(exc)
    threads = [threading.Thread(target=host) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert battles_store.get(battle['id'])['status'] == 'interrupted'
    assert [event['kind'] for event in battles_store.events(battle['id'])].count('interrupted') == 1


def test_a_driver_that_ended_its_own_battle_first_is_not_overwritten_or_an_error(app):
    from control import battles as control_battles
    run = running_run(app)
    battles_store, battle = _battle_for(app, run)
    operations.ledger(app.dash.ROOT).transition(run['id'], run['revision'], 'failed')
    real = battles_store.finish.__func__
    ended = []

    def driver_finishes_first(self, battle_id, generation, **kwargs):
        if not ended:
            ended.append(real(self, battle_id, generation, reason='death', outcome='death', status='ended'))
        return real(self, battle_id, generation, **kwargs)
    app.monkeypatch.setattr(type(battles_store), 'finish', driver_finishes_first)
    control_battles.reconcile(app.dash.ROOT, control_battles.ledger(app.dash.ROOT))     # does not raise
    assert battles_store.get(battle['id'])['status'] == 'ended' and battles_store.get(battle['id'])['outcome'] == 'death'


def test_two_dashboards_retiring_the_same_dead_worker_both_see_it_interrupted(app):
    import threading
    run = running_run(app)
    gate, seen, errors = threading.Barrier(2, timeout=5), [], []

    def meet(worker):
        gate.wait()                                                                        # both read it 'running'
        return False                                                                       # and both find it proven gone
    app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(meet))

    def host():
        try:
            seen.append(operations.resolve(app.dash.ROOT, run['id'])['status'])
        except Exception as exc:                                                           # noqa: BLE001 - reported below
            errors.append(exc)
    threads = [threading.Thread(target=host) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == [] and seen == ['interrupted', 'interrupted']


def test_who_holds_the_emulator_does_not_depend_on_the_selected_account(app):
    cfg = app.dash.load_config()
    store = operations.ledger(app.dash.ROOT)
    run, _ = store.accept('run', operations.context(app.dash.ROOT, cfg, {}, None), {}, 'owner-before-rename')
    cfg['instances']['main']['account'] = 'nobody-registered'
    assert [o['operation_id'] for o in harvest.owners(app.dash.ROOT, cfg, [])] == [run['id']]


def test_a_failed_owner_scan_never_reads_as_nothing_running(app):
    from runtime import device_lease
    ident = identity(app)
    enable(app)
    app.monkeypatch.setattr(operations.Operations, 'active_readonly', staticmethod(
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked'))))
    revocation = revoke(app)
    assert revocation['state'] == 'stopping' and [e['step'] for e in revocation['errors']] == ['owners']
    # who holds the emulator is unknown, so nothing is claimed about covering them: the marker itself is still real
    assert revocation['latched'] is False and revocation['fenced'] is False and revocation['revoked_at'] is not None
    assert device_lease.is_revoked(ident['serial'], ident['adb_host'], ident['adb_port']) is True


@pytest.mark.parametrize('url,expected', [
    pytest.param('/api/operations/op-x', 404, id='operation'),
    pytest.param('/api/calibrate/status', 200, id='calibrate-status'),
    pytest.param('/api/scan/status', 200, id='scan-status'),
    pytest.param('/api/screen-analysis/status', 200, id='screen-analysis-status'),
])
def test_the_operation_status_routes_create_nothing_on_a_cold_checkout(app, url, expected):
    ledger = operations.ledger_path(app.dash.ROOT)
    assert not ledger.exists()
    assert app.client.get(url).status_code == expected
    assert not ledger.exists(), 'a GET initialized the ledger'


# ----- the device lease is the final owner: idle-only routes refuse while ANY process holds it
def hold_lease(app):
    """A real OS lease on this emulator held by this process, with nothing in the ledger or the process table."""
    from runtime import device_lease
    ident = identity(app)
    return device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port'])


@pytest.mark.parametrize('method,url,payload', OWNED, ids=[url.rsplit('/', 1)[-1] + str(i) for i, (_, url, _) in enumerate(OWNED)])
def test_a_held_device_lease_refuses_every_idle_only_route_when_the_ledger_reads_empty(app, method, url, payload):
    hold_lease(app)
    before = open(os.path.join(app.dash.ROOT, 'config.yaml'), 'rb').read()
    launched = len(app.spawned)
    response = getattr(app.client, method)(url, json=payload)
    refusal = response.get_json()
    assert response.status_code == 409, refusal
    assert (refusal['ok'], refusal['code'], refusal['retry']) == (False, 'device_owned', 'after_confirmed_stop')
    owner = refusal['owner']
    assert (owner['kind'], owner['foreign'], owner['pid'], owner['operation_id']) == ('process', True, None, None)
    assert owner['lease']['held'] is True and refusal['recovery'] is None
    assert open(os.path.join(app.dash.ROOT, 'config.yaml'), 'rb').read() == before      # nothing was changed
    assert len(app.spawned) == launched and operations.ledger(app.dash.ROOT).active() == []


def test_a_free_device_lease_lets_the_same_route_proceed(app):
    assert app.client.post('/api/profile-activate', json=dict(name=None)).status_code == 200


@pytest.mark.parametrize('method,url,payload', OWNED, ids=[url.rsplit('/', 1)[-1] + str(i) for i, (_, url, _) in enumerate(OWNED)])
def test_an_unreadable_device_lease_is_never_read_as_a_free_one(app, method, url, payload):
    from runtime import device_lease
    app.monkeypatch.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    before = open(os.path.join(app.dash.ROOT, 'config.yaml'), 'rb').read()
    response = getattr(app.client, method)(url, json=payload)
    refusal = response.get_json()
    assert response.status_code == 409 and refusal['code'] == 'device_owner_unreadable', refusal
    assert open(os.path.join(app.dash.ROOT, 'config.yaml'), 'rb').read() == before and ops(app) == []


def test_the_lease_of_the_operation_a_route_judges_itself_is_not_a_second_reason_to_refuse(app):
    pre_spawn(app, kind='calibrate')
    hold_lease(app)                                           # that calibration's worker would hold it
    response = app.client.post('/api/calibrate/start', json={})
    assert response.status_code == 409 and response.get_json().get('code') != 'device_owned'


def test_a_lease_held_for_another_emulator_does_not_refuse_this_one(app):
    from runtime import device_lease
    ident = identity(app)
    device_lease.acquire('fake:2', ident['adb_host'], ident['adb_port'])
    assert app.client.post('/api/profile-activate', json=dict(name=None)).status_code == 200


def test_an_emulator_without_an_adb_serial_has_no_lease_to_ask(app):
    cfg = app.dash.load_config()
    cfg['instances']['main'].pop('serial')
    app.dash.save_config(cfg)
    assert harvest.lease_owner(app.dash.load_config()) is None


@pytest.mark.parametrize('action', ['start', 'start_gems'])
def test_an_identical_retry_is_answered_from_its_receipt_before_the_lease_is_asked(app, action):
    body = dict(action=action, preset='bp_farm', idempotency_key=f'retry-{action}-0001')
    first = app.client.post('/api/control', json=body)
    assert first.status_code == 200, first.get_json()
    launched = len(app.spawned)
    store = operations.ledger(app.dash.ROOT)
    [op] = store.active()
    store.transition(op['id'], op['revision'], 'interrupted')                 # it is over, but something else holds the lease now
    hold_lease(app)
    retry = app.client.post('/api/control', json=body)
    assert retry.status_code == 200 and retry.get_json()['operation_id'] == first.get_json()['operation_id']
    assert len(app.spawned) == launched                                       # a retry never launches
    fresh = app.client.post('/api/control', json=dict(body, idempotency_key=f'new-{action}-0002'))
    assert fresh.status_code == 409 and fresh.get_json()['code'] == 'device_owned'   # a new request is refused by the lease

