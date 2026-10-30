"""Offline operation contract tests: temporary SQLite/artifacts, no devices."""

import paths as test_paths
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from runtime.operations import Conflict, Operations, file_revision, worker_operation
from control import operations as service
from harvest_support import fake_inspect


@pytest.mark.parametrize('module_name', ['calibrate', 'scan'])
def test_tracked_worker_ignores_late_stop_marker_from_old_operation(tmp_path, monkeypatch, module_name):
    from player import calibrate, scan
    module = {'calibrate': calibrate, 'scan': scan}[module_name]
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    old, _ = store.accept('calibrate', ctx, {})
    old = store.transition(old['id'], old['revision'], 'cancelling')
    store.transition(old['id'], old['revision'], 'cancelled')
    newer, _ = store.accept('calibrate', ctx, {})
    monkeypatch.setenv('TOWER_OPERATION_ID', newer['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    marker = tmp_path / 'stop'
    paths = {'stop': str(marker)}
    assert module._stop_requested(paths) is False
    marker.write_text(old['id'], encoding='utf-8')
    assert module._stop_requested(paths) is False
    marker.write_text(newer['id'], encoding='utf-8')
    assert module._stop_requested(paths) is True
    marker.unlink()
    store.transition(newer['id'], newer['revision'], 'cancelling')
    assert module._stop_requested(paths) is True


@pytest.mark.parametrize('module_name', ['calibrate', 'scan'])
def test_stop_marker_read_errors_fail_loud_and_legacy_keeps_existence(tmp_path, monkeypatch, module_name):
    from player import calibrate, scan
    module = {'calibrate': calibrate, 'scan': scan}[module_name]
    marker = tmp_path / 'stop'
    marker.write_text('legacy-dashboard', encoding='utf-8')
    monkeypatch.delenv('TOWER_OPERATION_ID', raising=False)
    monkeypatch.delenv('TOWER_OPERATION_DB', raising=False)
    assert module._stop_requested({'stop': str(marker)}) is True
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    def denied(*args, **kwargs):
        raise PermissionError('marker is unreadable')
    monkeypatch.setattr(module, 'open', denied, raising=False)
    with pytest.raises(PermissionError, match='unreadable'):
        module._stop_requested({'stop': str(marker)})


def target(tmp_path, serial='fake:1'):
    profile = tmp_path / 'profiles' / 'mine.yaml'
    profile.parent.mkdir(exist_ok=True)
    profile.write_text('player: {}\n')
    cfg = dict(active_instance='main', active_profile='mine', instances={'main': {'serial': serial, 'rendering': {'width': 1080, 'height': 2560, 'dpi': 360}}})
    return cfg, service.context(tmp_path, cfg, {}, str(profile))


def test_concurrent_admission_and_retry_are_transactional(tmp_path):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    def accept(key):
        try:
            return store.accept('calibrate', ctx, {}, key)
        except Conflict:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(accept, ['one', 'two']))
    accepted = [row for row in results if row]
    assert len(accepted) == 1
    op = accepted[0][0]
    again, created = store.accept('calibrate', ctx, {}, op['idempotency_key'])
    assert not created and again == op
    independent = dict(ctx, device_key='second-device', serial='fake:2')
    assert store.accept('calibrate', independent, {}, 'other')[1]


def test_retry_does_not_reassign_context_or_request(tmp_path):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {'flows': False}, 'key')
    ctx['profile'] = 'someone_else'
    with pytest.raises(Conflict):
        store.accept('calibrate', ctx, {'flows': False}, 'key')
    assert store.get(op['id'])['context']['profile'] == 'mine'
    with pytest.raises(Conflict):
        store.accept('calibrate', op['context'], {'flows': True}, 'key')


def test_revision_and_lifecycle_edges_fence_stale_commands(tmp_path):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    with pytest.raises(Conflict):
        store.transition(op['id'], 1, 'succeeded')
    running = store.transition(op['id'], 1, 'running')
    with pytest.raises(Conflict):
        store.transition(op['id'], 1, 'cancelling')
    with pytest.raises(ValueError):
        store.transition(op['id'], running['revision'], 'running', context={})
    finished = store.transition(op['id'], running['revision'], 'awaiting_apply')
    restarted = Operations(tmp_path / 'ops.db')
    assert restarted.get(op['id']) == finished


def test_reconciliation_never_infers_success_and_checks_creation_time(tmp_path, monkeypatch):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 999, 'creation_time': 10})
    assert store.reconcile(op['id'], lambda worker: True)['status'] == 'running'
    assert store.reconcile(op['id'], lambda worker: False)['status'] == 'interrupted'
    fake = types.SimpleNamespace(Process=lambda pid: types.SimpleNamespace(create_time=lambda: 11, is_running=lambda: True), NoSuchProcess=ProcessLookupError, AccessDenied=PermissionError)
    monkeypatch.setitem(sys.modules, 'psutil', fake)
    assert not service.worker_alive(op['worker'])


def test_reconcile_judges_the_snapshot_it_read_and_retires_only_that_revision(tmp_path):
    """H2R7-02. policy(op) is handed the operation THIS call read (status, worker, revision); a judgement made
    on an earlier read of the caller's own is exactly the stale-status defect, so the API gives it no other way."""
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 999, 'creation_time': 10})
    seen = []

    def gone(snapshot):
        seen.append((snapshot['status'], snapshot['revision'], snapshot['worker']))
        return False
    retired = store.reconcile(op['id'], policy=gone)
    assert seen == [('running', op['revision'], {'pid': 999, 'creation_time': 10})]
    assert retired['status'] == 'interrupted' and retired['revision'] == op['revision'] + 1


def test_reconcile_policy_that_cannot_prove_the_worker_gone_keeps_the_operation(tmp_path):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 999, 'creation_time': 10})
    for answer in (True, None, 0, ''):                    # only an explicit False retires: unknown and falsy values do not
        assert store.reconcile(op['id'], policy=lambda snapshot, answer=answer: answer) == op


def test_a_policy_that_judged_an_older_state_loses_the_compare_and_set_to_a_newer_one(tmp_path):
    """The operation moves between the policy's snapshot and the write: the write is refused by the revision and
    the newer state is the answer. (Before, a caller-side judgement of an earlier read could retire it.)"""
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 999, 'creation_time': 10})

    def gone_and_the_operation_moves(snapshot):
        store.transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
        return False
    answer = store.reconcile(op['id'], policy=gone_and_the_operation_moves)
    assert answer['status'] == 'cancelling' and answer['revision'] == op['revision'] + 1 and answer['error'] is None


@pytest.mark.parametrize('arguments', [(), ('both',)], ids=['neither', 'both'])
def test_reconcile_needs_exactly_one_way_to_judge(tmp_path, arguments):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    with pytest.raises(TypeError):
        if arguments:
            store.reconcile(op['id'], lambda worker: False, policy=lambda snapshot: False)
        else:
            store.reconcile(op['id'])


def test_cancel_old_operation_cannot_cancel_new_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'inspect_worker', fake_inspect(lambda worker: True))
    _, ctx = target(tmp_path)
    store = service.ledger(tmp_path)
    a, _ = store.accept('calibrate', ctx, {})
    a = store.transition(a['id'], a['revision'], 'running')
    a = service.cancel(tmp_path, {'operation_id': a['id'], 'expected_revision': a['revision']})
    store.transition(a['id'], a['revision'], 'cancelled')
    b, _ = store.accept('calibrate', ctx, {})
    flag = Path(ctx['artifact_dir']) / 'calibrate_stop'
    flag.unlink()
    with pytest.raises(Conflict):
        service.cancel(tmp_path, {'operation_id': a['id'], 'expected_revision': a['revision']})
    assert not flag.exists()
    assert store.get(b['id'])['status'] == 'accepted'


@pytest.mark.parametrize('restore,failed,expected', [(False, False, 'awaiting_apply'), (True, False, 'restoration_required'), (False, True, 'failed')])
def test_capture_outcome_preserves_artifact_reference(tmp_path, monkeypatch, restore, failed, expected):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {'phases': 'c'})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    path = directory / 'calibrate_state.json'
    with worker_operation():
        path.write_text(json.dumps({'operation_id': op['id'], 'player': {'card_presets': ['test']}, 'phases': {'cards': {'status': 'error' if failed else 'done'}},
                                    'final_screen': {'name': 'home', 'verified': True, 'operation_id': op['id']}}))
        if restore:
            (directory / 'module_restore.json').write_text('{}')
    saved = store.get(op['id'])
    assert saved['status'] == expected
    assert saved['result_refs']['calibrate_state.json']['revision'] == file_revision(path)
    assert 'player' not in saved


@pytest.mark.parametrize('proof', [None, {},
    {'name': 'home', 'verified': True, 'operation_id': 'previous-operation'},
    {'name': 'unknown', 'verified': False},
    {'name': 'home', 'verified': False},
    {'name': 'home', 'verified': 'true'},
    {'name': 'battle', 'verified': True},
])
def test_calibration_clean_exit_requires_current_verified_home(tmp_path, monkeypatch, proof):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {'phases': 'c'})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    state = {'operation_id': op['id'], 'phases': {'cards': {'status': 'done'}}}
    if proof is not None:
        state['final_screen'] = {'operation_id': op['id'], **proof}
    with worker_operation():
        (directory / 'calibrate_state.json').write_text(json.dumps(state))
    saved = store.get(op['id'])
    assert saved['status'] == 'failed'
    assert saved['error']['code'] == 'final_screen_unverified'


@pytest.mark.parametrize('kind,requested,phases', [
    ('calibrate', {'phases': 'c,m'}, {'cards': {'status': 'done'}}),
    ('calibrate', {'bootstrap': True}, {}),
    ('scan', {'phases': 'c,g'}, {'cards': {'status': 'done'}}),
    ('scan', {'phases': 'c', 'battle': True}, {'cards': {'status': 'done'}}),
])
def test_missing_requested_phase_cannot_succeed(tmp_path, monkeypatch, kind, requested, phases):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept(kind, ctx, requested)
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    with worker_operation():
        (directory / f'{kind}_state.json').write_text(json.dumps({
            'operation_id': op['id'], 'phases': phases,
            'final_screen': {'name': 'home', 'verified': True, 'operation_id': op['id']},
        }))
    saved = store.get(op['id'])
    assert saved['status'] == 'failed'
    assert saved['error']['code'] == 'capture_incomplete'


@pytest.mark.parametrize('requested', [{}, {'phases': 'g, c, m'}])
def test_scan_default_selection_matches_worker_without_home_requirement(tmp_path, monkeypatch, requested):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('scan', ctx, requested)
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    with worker_operation():
        (directory / 'scan_state.json').write_text(json.dumps({
            'operation_id': op['id'],
            'phases': {name: {'status': 'done'} for name in ('guardians', 'cards', 'modules')},
        }))
    assert store.get(op['id'])['status'] == 'succeeded'
    assert store.get(op['id'])['error'] is None


@pytest.mark.parametrize('during', ['artifact_read', 'terminal_cas'])
def test_cancellation_during_finalization_reaches_cancelled(tmp_path, monkeypatch, during):
    from runtime import operations as runtime_ops
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {'phases': 'c'})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    original_revision = runtime_ops.file_revision
    original_transition = Operations.transition
    cancelled = []
    def request_cancel():
        current = store.get(op['id'])
        original_transition(store, op['id'], current['revision'], 'cancelling')
        cancelled.append(True)
    def revision(path):
        if not cancelled:
            request_cancel()
        return original_revision(path)
    def transition(self, operation_id, expected_revision, status, **kwargs):
        if status == 'awaiting_apply' and not cancelled:
            request_cancel()
        return original_transition(self, operation_id, expected_revision, status, **kwargs)
    if during == 'artifact_read':
        monkeypatch.setattr(runtime_ops, 'file_revision', revision)
    else:
        monkeypatch.setattr(Operations, 'transition', transition)
    with worker_operation():
        (directory / 'calibrate_state.json').write_text(json.dumps({
            'operation_id': op['id'], 'phases': {'cards': {'status': 'done'}},
            'final_screen': {'name': 'home', 'verified': True, 'operation_id': op['id']},
        }))
    saved = store.get(op['id'])
    assert cancelled == [True]
    assert saved['status'] == 'cancelled'
    assert saved['error'] is None
    assert saved['result_refs']['calibrate_state.json']['revision'] == original_revision(directory / 'calibrate_state.json')


def test_clean_driver_exit_ignores_old_setup_errors(tmp_path, monkeypatch):
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('run', ctx, {})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    (directory / 'module_restore.json').write_text('{}')
    (directory / 'calibrate_state.json').write_text('{"phases":{"cards":{"status":"error"}}}')
    with pytest.raises(SystemExit):
        with worker_operation():
            raise SystemExit(0)
    assert store.get(op['id'])['status'] == 'succeeded'


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    path = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location('tp_operation_contract', path)
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    cfg, ctx = target(tmp_path)
    monkeypatch.setattr(dash, 'ROOT', str(tmp_path))
    monkeypatch.setattr(dash, 'load_config', lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    return dash, cfg, ctx


def test_status_uses_original_context_after_switch(dashboard):
    dash, cfg, ctx = dashboard
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', ctx, {})
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    (directory / 'calibrate_state.json').write_text(json.dumps({'operation_id': op['id'], 'player': {'marker': 'original'}}))
    cfg['active_profile'] = 'another'
    cfg['instances']['main']['serial'] = 'fake:other'
    with dash.app.test_client() as client:
        response = client.get('/api/calibrate/status?operation_id=' + op['id'])
    assert response.status_code == 200
    result = response.get_json()
    assert result['state']['player']['marker'] == 'original'
    assert result['operation']['context']['profile'] == 'mine'
    assert 'config' not in result['operation']['context']


@pytest.mark.parametrize('activity_operation', ['current', 'previous', None])
def test_calibration_status_reads_only_matching_persisted_activity(dashboard, monkeypatch, activity_operation):
    dash, _, ctx = dashboard
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', ctx, {'phases': 'c'})
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    activity = {'operation_id': op['id'] if activity_operation == 'current' else activity_operation,
                'phase': 'cards', 'kind': 'calibrate_progress', 'message': 'Cards: reading page 3', 't': 123}
    (directory / 'calibrate_state.json').write_text(json.dumps({'operation_id': op['id'], 'activity': activity}))
    monkeypatch.setattr(dash, '_newest_events', lambda *args, **kwargs: pytest.fail('Tracked progress must not scan event logs'))
    with dash.app.test_client() as client:
        for _ in range(2):
            result = client.get('/api/calibrate/status?operation_id=' + op['id']).get_json()
            assert result['activity'] == (activity if activity_operation == 'current' else None)


def test_basic_progress_roundtrips_from_worker_state_to_status_api(dashboard, monkeypatch):
    from player import calibrate
    dash, _, ctx = dashboard
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', ctx, {'phases': 'm'})
    op = store.transition(op['id'], op['revision'], 'running')
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    monkeypatch.setattr(service, 'inspect_worker', fake_inspect(lambda worker: True))
    monkeypatch.setattr(dash, '_newest_events', lambda *args, **kwargs: pytest.fail('No event-log scan'))
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    paths = {'state': str(directory / 'calibrate_state.json')}
    state = {'phases': {'modules': {'status': 'running', 'started_at': 10}}}
    calibrate._record_activity(paths, state, 'modules', 'Modules: identifying tile 8 on page 2')
    persisted = json.loads(Path(paths['state']).read_text(encoding='utf-8'))
    assert persisted['operation_id'] == op['id']
    assert persisted['operation_revision'] > op['revision']
    with dash.app.test_client() as client:
        response = client.get('/api/calibrate/status?operation_id=' + op['id'])
    assert response.status_code == 200
    result = response.get_json()
    assert result['activity'] == persisted['activity']
    assert result['activity']['message'] == 'Modules: identifying tile 8 on page 2'
    assert result['state']['phases']['modules']['status'] == 'running'
    assert result['operation']['status'] == 'running'


@pytest.mark.parametrize('kind,route,filename', [('scan', '/api/scan/status', 'scan_state.json'), ('screen_analysis', '/api/screen-analysis/status', 'screen_analysis.json')])
def test_status_does_not_combine_old_operation_with_new_shared_artifact(dashboard, kind, route, filename):
    dash, _, ctx = dashboard
    store = service.ledger(dash.ROOT)
    old, _ = store.accept(kind, ctx, {})
    old = store.transition(old['id'], old['revision'], 'running')
    old = store.transition(old['id'], old['revision'], 'succeeded')
    newer, _ = store.accept(kind, ctx, {})
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    (directory / filename).write_text(json.dumps({'operation_id': newer['id'], 'player': {'marker': 'newer'}, 'status': 'done', 'matches': [{'marker': 'newer'}]}))
    with dash.app.test_client() as client:
        response = client.get(route + '?operation_id=' + old['id'])
    assert response.status_code == 200
    result = response.get_json()
    assert result['operation']['id'] == old['id']
    if kind == 'scan':
        assert result['state'] == {}
    else:
        assert result['status'] == 'unavailable'
        assert 'matches' not in result and 'player' not in result


@pytest.mark.parametrize('domain_status,expected', [('done', 'succeeded'), ('error', 'failed'), ('running', 'failed')])
def test_screen_analysis_clean_exit_uses_tagged_domain_outcome(tmp_path, monkeypatch, domain_status, expected):
    import numpy as np
    from player import screen_analysis
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('screen_analysis', ctx, {})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    path = directory / 'screen_analysis.json'
    with worker_operation():
        screen_analysis._write_state(path, {'status': 'running', 'message': 'progress'})
        assert json.loads(path.read_text())['operation_id'] == op['id']
        screenshot = screen_analysis._save_screenshot(
            directory / 'screen_analysis.png', np.zeros((24, 24, 3), dtype=np.uint8))
        screen_analysis._write_state(path, {
            'status': domain_status, 'message': 'domain outcome', 'screenshot': screenshot})
    final = store.get(op['id'])
    assert final['status'] == expected
    assert final['result_refs']['screen_analysis.json']['revision'] == file_revision(path)
    assert final['result_refs']['screen_analysis.png']['revision'] == screenshot['revision']
    if expected == 'failed':
        assert final['error']['code'] == 'analysis_incomplete'


def test_apply_profile_conflict_preserves_discoveries(dashboard):
    dash, _, ctx = dashboard
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', ctx, {})
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    state = directory / 'calibrate_state.json'
    state.write_text(json.dumps({'operation_id': op['id'], 'player': {'card_presets': ['found']}}))
    op = store.transition(op['id'], op['revision'], 'running')
    op = store.transition(op['id'], op['revision'], 'awaiting_apply', result_refs={'calibrate_state.json': {'path': str(state), 'revision': file_revision(state)}})
    Path(ctx['profile_path']).write_text('player: {wall: true}\n')
    with dash.app.test_client() as client:
        response = client.post('/api/calibrate/apply', json={'operation_id': op['id'], 'expected_revision': op['revision']})
    assert response.status_code == 409
    assert 'Profile changed' in response.get_json()['error']
    assert json.loads(state.read_text())['player']['card_presets'] == ['found']
    assert store.get(op['id'])['status'] == 'awaiting_apply'


def test_apply_is_revision_fenced_and_retry_returns_same_outcome(dashboard, monkeypatch):
    dash, _, ctx = dashboard
    from player import playerprofile
    monkeypatch.setattr(playerprofile, 'validate', lambda profile: [])
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', ctx, {})
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    state = directory / 'calibrate_state.json'
    state.write_text(json.dumps({'operation_id': op['id'], 'player': {'card_presets': ['found']}}))
    op = store.transition(op['id'], op['revision'], 'running')
    op = store.transition(op['id'], op['revision'], 'awaiting_apply', result_refs={'calibrate_state.json': {'path': str(state), 'revision': file_revision(state)}})
    body = {'operation_id': op['id'], 'expected_revision': op['revision']}
    with dash.app.test_client() as client:
        applied = client.post('/api/calibrate/apply', json=body)
        retried = client.post('/api/calibrate/apply', json=body)
    assert applied.status_code == retried.status_code == 200
    assert applied.get_json()['operation'] == retried.get_json()['operation']
    assert retried.get_json()['operation']['status'] == 'succeeded'
    assert store.get(op['id'])['revision'] == op['revision'] + 3


def test_apply_retries_saved_profile_after_config_failure_without_capture(dashboard, monkeypatch):
    dash, cfg, ctx = dashboard
    from player import playerprofile
    monkeypatch.setattr(playerprofile, 'validate', lambda profile: [])
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', ctx, {})
    directory = Path(ctx['artifact_dir'])
    directory.mkdir(parents=True)
    state = directory / 'calibrate_state.json'
    state.write_text(json.dumps({'operation_id': op['id'], 'player': {'wall': True, 'wall_bar': [1, 2, 3, 4]}}))
    op = store.transition(op['id'], op['revision'], 'running')
    op = store.transition(op['id'], op['revision'], 'awaiting_apply', result_refs={'calibrate_state.json': {'path': str(state), 'revision': file_revision(state)}})
    attempts = []
    def save_config(data):
        attempts.append(copy.deepcopy(data))
        if len(attempts) == 1:
            raise OSError('simulated config write failure')
        cfg.clear()
        cfg.update(copy.deepcopy(attempts[-1]))
    monkeypatch.setattr(dash, 'save_config', save_config)
    with dash.app.test_client() as client:
        failed = client.post('/api/calibrate/apply', json={'operation_id': op['id'], 'expected_revision': op['revision']})
        assert failed.status_code == 400
        retry = store.get(op['id'])
        assert retry['status'] == 'awaiting_apply'
        assert retry['result_refs']['apply_profile_digest']
        succeeded = client.post('/api/calibrate/apply', json={'operation_id': op['id'], 'expected_revision': retry['revision']})
    assert succeeded.status_code == 200, succeeded.get_json()
    assert succeeded.get_json()['operation']['status'] == 'succeeded'
    assert len(attempts) == 2
    assert file_revision(state) == op['result_refs']['calibrate_state.json']['revision']


@pytest.mark.parametrize('kind', ['run', 'calibrate'])
def test_expected_context_prevents_launch_retargeting(tmp_path, kind):
    cfg, ctx = target(tmp_path)
    command = {'expected_context': {key: ctx[key] for key in ('instance', 'account', 'serial', 'rendering', 'profile')}}
    cfg['instances']['main']['serial'] = 'fake:changed'
    flag = tmp_path / 'logs' / 'main' / 'stop_after_run'
    flag.parent.mkdir(parents=True)
    flag.write_text('active scheduler transition', encoding='utf-8')
    launches = []
    with pytest.raises(Conflict, match='before acceptance'):
        service.launch(tmp_path, cfg, kind, command, ['python', 'fake.py'], lambda *args, **kwargs: launches.append(args), ctx['profile_path'])
    assert not launches
    assert flag.read_text() == 'active scheduler transition'


@pytest.mark.parametrize('busy', [False, True])
@pytest.mark.parametrize('kind', ['run', 'gems'])
def test_bootstrap_acquires_lease_before_running_or_target_import(tmp_path, monkeypatch, busy, kind):
    from runtime import device_lease, operation_worker
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept(kind, ctx, {})
    flag = Path(store.path).parent / ctx['instance'] / 'stop_after_run'
    flag.parent.mkdir()
    flag.write_text('old stop', encoding='utf-8')
    other = flag.parent.parent / 'another' / 'stop_after_run'
    other.parent.mkdir()
    other.write_text('other scheduler transition', encoding='utf-8')
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    order = []
    def acquire(serial, host, port):
        assert store.get(op['id'])['status'] == 'accepted'
        assert flag.read_text() == 'old stop'
        order.append('lease')
        if busy:
            raise device_lease.DeviceBusy('fake occupied transport')
    def execute(path, run_name):
        assert store.get(op['id'])['status'] == 'running'
        assert flag.exists() is (kind != 'run')
        order.append('target')
    monkeypatch.setattr(device_lease, 'acquire', acquire)
    monkeypatch.setattr(operation_worker, 'restore_worker_context', lambda settings: order.append('context'))
    monkeypatch.setattr(operation_worker.runpy, 'run_path', execute)
    monkeypatch.setattr(sys, 'argv', ['operation_worker', 'fake_target.py'])
    if busy:
        with pytest.raises(device_lease.DeviceBusy):
            operation_worker.main()
        assert order == ['context', 'lease']
        assert store.get(op['id'])['status'] == 'failed'
        assert flag.read_text() == 'old stop'
    else:
        operation_worker.main()
        assert order == ['context', 'lease', 'target']
        assert store.get(op['id'])['status'] == 'succeeded'
    assert other.read_text() == 'other scheduler transition'


def test_retry_resolves_original_operation_after_mutable_revisions_change(tmp_path):
    cfg, ctx = target(tmp_path)
    store = service.ledger(tmp_path)
    op, _ = store.accept('calibrate', ctx, {'phases': 'c'}, 'accepted-key')
    cfg['unrelated_setting'] = 'changed'
    Path(ctx['profile_path']).write_text('player: {card_presets: [discovered]}\n')
    retried = service.retry(tmp_path, cfg, 'calibrate', {'phases': 'c', 'idempotency_key': 'accepted-key'}, ctx['profile_path'])
    assert retried == op
    cfg['active_profile'] = 'different-profile'
    with pytest.raises(Conflict):
        service.retry(tmp_path, cfg, 'calibrate', {'phases': 'c', 'idempotency_key': 'accepted-key'}, ctx['profile_path'])


def test_new_start_reconciles_dead_worker_without_prior_poll(tmp_path, monkeypatch):
    cfg, ctx = target(tmp_path)
    store = service.ledger(tmp_path)
    old, _ = store.accept('run', ctx, {})
    old = store.transition(old['id'], old['revision'], 'running', worker={'pid': 99999999, 'creation_time': 1})
    launches = []
    def spawn(command, **kwargs):
        launches.append(command)
        return types.SimpleNamespace(pid=99999999)
    newer = service.launch(tmp_path, cfg, 'run', {'idempotency_key': 'new-key'}, ['python', 'fake.py'], spawn, ctx['profile_path'])
    assert store.get(old['id'])['status'] == 'interrupted'
    assert newer['id'] != old['id'] and newer['status'] == 'accepted'
    assert len(launches) == 1
    assert launches[0][1:3] == ['-m', 'runtime.operation_worker']


def test_launch_propagates_correlation_without_changing_idempotent_payload(tmp_path, monkeypatch):
    from runtime import diagnostics
    cfg, ctx = target(tmp_path)
    flag = tmp_path / 'logs' / 'main' / 'stop_after_run'
    flag.parent.mkdir(parents=True)
    flag.write_text('old stop', encoding='utf-8')
    launches = []
    def spawn(command, **kwargs):
        assert flag.read_text() == 'old stop'
        launches.append(kwargs['env'])
        return types.SimpleNamespace(pid=99999999)
    command = {'idempotency_key': 'same-business-command', 'preset': 'test'}
    with diagnostics.context(trace_id='original-trace', request_id='original-request', span_id='request-span'):
        first = service.launch(tmp_path, cfg, 'run', command, ['python', 'fake.py'], spawn, ctx['profile_path'])
    assert flag.read_text() == 'old stop'
    flag.write_text('new scheduler transition', encoding='utf-8')
    with diagnostics.context(trace_id='retried-trace', request_id='retried-request'):
        second = service.launch(tmp_path, cfg, 'run', command, ['python', 'fake.py'], spawn, ctx['profile_path'])
    assert first == second
    assert flag.read_text() == 'new scheduler transition'
    assert first['request'] == {'preset': 'test'}
    assert 'diagnostic' not in first['context']
    assert len(launches) == 1
    assert launches[0]['TOWER_TRACE_ID'] == 'original-trace'
    assert launches[0]['TOWER_REQUEST_ID'] == 'original-request'
    assert launches[0]['TOWER_OPERATION_ID'] == first['id']
    rows = [json.loads(line) for path in (tmp_path / 'logs' / '_dashboard').glob('events_*.jsonl')
            for line in path.read_text().splitlines()]
    start = next(row for row in rows if row['kind'] == 'attempt_start')
    assert launches[0]['TOWER_PARENT_SPAN_ID'] == start['span_id']
    assert start['parent_span_id'] == 'request-span'


def test_worker_exception_persists_failure_and_correlated_raw_outcome(tmp_path, monkeypatch):
    from runtime import logger
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('run', ctx, {})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    monkeypatch.setenv('TOWER_TRACE_ID', 'worker-trace')
    with pytest.raises(RuntimeError, match='worker crashed'):
        with worker_operation():
            raise RuntimeError('worker crashed')
    final = store.get(op['id'])
    assert final['status'] == 'failed'
    assert final['error']['code'] == 'RuntimeError'
    rows = [json.loads(line) for path in logger._LOG_DIR.rglob('events_*.jsonl')
            for line in path.read_text().splitlines()]
    assert {row['operation_id'] for row in rows} == {op['id']}
    assert {row['trace_id'] for row in rows} == {'worker-trace'}
    assert next(row for row in rows if row['kind'] == 'operation_worker_started')['revision'] == 2
    assert next(row for row in rows if row['kind'] == 'operation_worker_finished')['outcome'] == 'failed'
    end = next(row for row in rows if row['kind'] == 'attempt_end')
    assert end['outcome'] == 'failed'
    assert end['exception_type'] == 'RuntimeError'
    assert 'worker crashed' in end['traceback']


def test_process_projection_requires_creation_identity(tmp_path, monkeypatch):
    _, ctx = target(tmp_path)
    store = service.ledger(tmp_path)
    op, _ = store.accept('run', ctx, {})
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 123, 'creation_time': 10})
    fake = types.SimpleNamespace(Process=lambda pid: types.SimpleNamespace(pid=123, create_time=lambda: 10, is_running=lambda: True, parents=lambda: [types.SimpleNamespace(pid=99, create_time=lambda: 5, is_running=lambda: True)]), NoSuchProcess=ProcessLookupError, AccessDenied=PermissionError)
    monkeypatch.setitem(sys.modules, 'psutil', fake)
    rows = service.project_processes(tmp_path, [{'pid': 99, 'runner': 'orchestrator', 'cmdline': 'fake', 'created': 5}])   # the launcher the scan pinned
    assert rows[0]['operation']['id'] == op['id']
    assert rows[0]['operation']['revision'] == op['revision']
    fake.Process = lambda pid: types.SimpleNamespace(create_time=lambda: 11, is_running=lambda: True)
    rows = service.project_processes(tmp_path, [{'pid': 99, 'runner': 'orchestrator'}])
    assert 'operation' not in rows[0]
    assert store.get(op['id'])['status'] == 'interrupted'


def test_clear_flag_uses_operation_original_instance_and_revision(dashboard):
    dash, cfg, ctx = dashboard
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('run', ctx, {})
    flag = Path(dash.ROOT) / 'logs' / 'main' / 'stop_after_run'
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text('scheduler transition', encoding='utf-8')
    cfg['active_instance'] = 'different'
    cfg['instances']['different'] = {'serial': 'fake:other'}
    other = Path(dash.ROOT) / 'logs' / 'different' / 'stop_after_run'
    other.parent.mkdir(parents=True)
    other.write_text('another scheduler transition', encoding='utf-8')
    with dash.app.test_client() as client:
        stale = client.post('/api/control', json={'action': 'clear_flag', 'operation_id': op['id'], 'expected_revision': op['revision'] + 1})
        assert stale.status_code == 409
        assert flag.read_text() == 'scheduler transition'
        response = client.post('/api/control', json={'action': 'clear_flag', 'operation_id': op['id'], 'expected_revision': op['revision']})
    assert response.status_code == 200
    assert not flag.exists()
    assert other.read_text() == 'another scheduler transition'


def test_legacy_stop_after_run_action_is_refused(dashboard):
    dash, _, ctx = dashboard
    store = service.ledger(dash.ROOT)
    op, _ = store.accept('run', ctx, {})
    with dash.app.test_client() as client:
        response = client.post('/api/control', json={'action': 'stop_after_run', 'operation_id': op['id'], 'expected_revision': op['revision']})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'unknown action'
    assert not (Path(dash.ROOT) / 'logs' / 'main' / 'stop_after_run').exists()


@pytest.mark.parametrize('width', [1080, 720])
def test_tracked_calibration_does_not_persist_rendering_or_retarget(tmp_path, monkeypatch, width):
    from device import layout
    from player import calibrate
    from player.geometry import Display
    _, ctx = target(tmp_path)
    store = Operations(tmp_path / 'ops.db')
    op, _ = store.accept('calibrate', ctx, {})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    activated = []
    monkeypatch.setattr(layout, 'discover', lambda: Display(width, 2560, 360))
    monkeypatch.setattr(layout, 'activate', activated.append)
    def forbidden():
        pytest.fail('Tracked calibration must not rewrite config/rendering')
    monkeypatch.setattr(layout, 'prepare_scan', forbidden)
    if width == 1080:
        calibrate._prepare_display()
        assert len(activated) == 1
    else:
        with pytest.raises(ValueError, match='Rendering changed'):
            calibrate._prepare_display()
        assert not activated
    assert store.get(op['id'])['context'] == ctx
