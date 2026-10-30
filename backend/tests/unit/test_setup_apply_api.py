"""Offline API checks for immutable discovery evidence and verified persistence."""

import paths as test_paths
import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from control import operations
from harvest_support import fake_inspect


@pytest.fixture(scope='module')
def dashboard_module():
    path = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location('tp_setup_apply_api', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bound(dashboard_module, tmp_path, monkeypatch):
    dash = dashboard_module
    profiles = tmp_path / 'profiles'
    profiles.mkdir()
    profile = profiles / 'mine.yaml'
    profile.write_text('player: {}\nblueprints: {}\n', encoding='utf-8')
    (profiles / 'default.yaml').write_text('player: {}\nblueprints: {}\n', encoding='utf-8')
    cfg = {'active_instance': 'main', 'active_profile': 'mine',
           'instances': {'main': {'serial': 'fake:1', 'rendering': {'width': 1080, 'height': 2560, 'dpi': 360}}}}
    config = tmp_path / 'config.yaml'
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    monkeypatch.setattr(dash, 'ROOT', str(tmp_path))
    monkeypatch.setattr(dash, 'CONFIG_PATH', str(config))
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    monkeypatch.setattr(dash, '_profile_check', lambda *args, **kwargs: [])
    from player import playerprofile
    monkeypatch.setattr(playerprofile, 'validate', lambda profile: [])
    return dash, operations.context(tmp_path, cfg, {}, str(profile))


def captured(bound, *, kind='calibrate', status='awaiting_apply', snapshot=True, state=None):
    dash, context = bound
    store = operations.ledger(dash.ROOT)
    op, _ = store.accept(kind, context, {'phases': 'c'})
    op = store.transition(op['id'], op['revision'], 'running')
    state = copy.deepcopy(state) if state is not None else {
        'player': {'card_presets': ['discovered']},
        'phases': {'cards': {'status': 'done', 'results': {'inventory': {
            'complete': True, 'cards': [{'name': 'Intro Sprint', 'active': False}], 'unreadable': 0}}}},
    }
    state.setdefault('operation_id', op['id'])
    directory = Path(context['artifact_dir'])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'operations' / op['id'] if snapshot else directory
    path.mkdir(parents=True, exist_ok=True)
    filename = 'scan_state.json' if kind == 'scan' else 'screen_analysis.json' if kind == 'screen_analysis' else 'calibrate_state.json'
    artifact = path / filename
    artifact.write_text(json.dumps(state), encoding='utf-8')
    refs = {filename: {'path': str(artifact), 'revision': operations.file_revision(artifact)}}
    if kind == 'calibrate':
        report = path / 'calibrate_report.json'
        report.write_text(json.dumps({'operation_id': op['id'], 'entries': [], 'marker': 'captured'}))
        refs[report.name] = {'path': str(report), 'revision': operations.file_revision(report)}
    if kind == 'screen_analysis':
        image = path / 'screen_analysis.png'
        image.write_bytes(b'saved-png-payload')
        refs[image.name] = {'path': str(image), 'revision': operations.file_revision(image)}
    error = {'code': 'capture_incomplete', 'message': 'Some capture phases did not complete'} if status == 'failed' else None
    op = store.transition(op['id'], op['revision'], status, result_refs=refs, error=error, step='capture_complete')
    return op, artifact


def apply(dash, operation, **extra):
    with dash.app.test_client() as client:
        return client.post('/api/calibrate/apply', json={
            'operation_id': operation['id'], 'expected_revision': operation['revision'], **extra})


@pytest.mark.parametrize('snapshot', [False, True])
def test_apply_rejects_other_operation_even_when_reference_hash_matches(bound, snapshot):
    dash, context = bound
    op, artifact = captured(bound, snapshot=snapshot, state={
        'operation_id': 'another-operation', 'player': {'card_presets': ['wrong']}})
    before = Path(context['profile_path']).read_bytes()
    response = apply(dash, op)
    assert response.status_code == 409
    assert 'another operation' in response.json['error']
    assert operations.file_revision(artifact) == op['result_refs']['calibrate_state.json']['revision']
    assert Path(context['profile_path']).read_bytes() == before
    assert operations.ledger(dash.ROOT).get(op['id']) == op


def test_apply_reads_validated_snapshot_instead_of_new_shared_state(bound):
    dash, context = bound
    op, artifact = captured(bound)
    shared = Path(context['artifact_dir']) / 'calibrate_state.json'
    shared.write_text(json.dumps({'operation_id': 'later', 'player': {'card_presets': ['wrong']}}))
    response = apply(dash, op)
    assert response.status_code == 200, response.json
    saved = yaml.safe_load(Path(context['profile_path']).read_text())
    assert saved['player']['card_presets'] == ['discovered']
    receipt = operations.ledger(dash.ROOT).get(op['id'])
    assert receipt['status'] == 'succeeded'
    assert receipt['result_refs']['apply_status'] == 'succeeded'
    assert receipt['result_refs']['apply_profile_digest'] == operations.digest(saved)
    assert receipt['result_refs']['apply_profile_revision'] == operations.file_revision(context['profile_path'])
    assert operations.file_revision(artifact) == op['result_refs']['calibrate_state.json']['revision']


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_apply_retains_unsuccessful_capture_outcome_and_idempotent_receipt(bound, status):
    dash, context = bound
    op, artifact = captured(bound, status=status)
    response = apply(dash, op)
    assert response.status_code == 200, response.json
    assert response.json['applied'] is True
    saved = operations.ledger(dash.ROOT).get(op['id'])
    assert saved['status'] == status
    assert saved['error'] == op['error']
    assert saved['step'] == op['step']
    assert saved['result_refs']['apply_status'] == 'succeeded'
    assert yaml.safe_load(Path(context['profile_path']).read_text())['player']['card_presets'] == ['discovered']
    assert apply(dash, op).json['operation'] == response.json['operation']
    assert operations.file_revision(artifact) == op['result_refs']['calibrate_state.json']['revision']


@pytest.mark.parametrize('snapshot', [False, True])
def test_modified_artifact_never_applies_and_status_exposes_no_unverified_state(bound, snapshot):
    dash, context = bound
    op, artifact = captured(bound, snapshot=snapshot)
    artifact.write_text(json.dumps({'operation_id': op['id'], 'player': {'card_presets': ['altered']}}))
    before = Path(context['profile_path']).read_bytes()
    assert apply(dash, op).status_code == 409
    with dash.app.test_client() as client:
        response = client.get('/api/calibrate/status', query_string={'operation_id': op['id']})
    assert response.json['state'] == {}
    assert response.json['artifact_errors']['state']
    assert Path(context['profile_path']).read_bytes() == before
    assert operations.ledger(dash.ROOT).get(op['id']) == op


def test_status_uses_frozen_state_report_and_card_inventory(bound):
    dash, context = bound
    op, _ = captured(bound)
    for filename in ('calibrate_state.json', 'calibrate_report.json', 'card_manifest.json', 'module_manifest.json'):
        (Path(context['artifact_dir']) / filename).write_text(json.dumps({'operation_id': 'later', 'marker': 'later'}))
    with dash.app.test_client() as client:
        response = client.get('/api/calibrate/status', query_string={'operation_id': op['id']})
    assert response.json['state']['operation_id'] == op['id']
    assert response.json['report']['marker'] == 'captured'
    assert response.json['card_manifest']['cards'] == [{'name': 'Intro Sprint', 'active': False}]
    assert response.json['manifest'] == {}


@pytest.mark.parametrize('kind,route', [('scan', '/api/scan/status'), ('screen_analysis', '/api/screen-analysis/status')])
def test_other_status_routes_read_frozen_results(bound, kind, route):
    dash, context = bound
    op, artifact = captured(bound, kind=kind, status='succeeded', state={'status': 'done', 'marker': 'captured'})
    (Path(context['artifact_dir']) / artifact.name).write_text(json.dumps({'operation_id': 'later', 'marker': 'wrong'}))
    with dash.app.test_client() as client:
        response = client.get(route, query_string={'operation_id': op['id']})
    result = response.json['state'] if kind == 'scan' else response.json
    assert result['marker'] == 'captured'
    assert response.json['operation']['id'] == op['id']


def test_screen_analysis_frame_serves_the_bytes_it_validated(bound):
    dash, context = bound
    op, artifact = captured(bound, kind='screen_analysis', status='succeeded', state={'status': 'done'})
    (Path(context['artifact_dir']) / 'screen_analysis.png').write_bytes(b'later-image')
    with dash.app.test_client() as client:
        response = client.get('/api/screen-analysis/frame.png', query_string={'operation_id': op['id']})
    assert response.status_code == 200
    assert response.data == artifact.with_name('screen_analysis.png').read_bytes()


@pytest.mark.parametrize('status', ['awaiting_apply', 'failed'])
def test_profile_writer_must_persist_before_apply_reports_success(bound, monkeypatch, status):
    dash, context = bound
    op, _ = captured(bound, status=status)
    before = Path(context['profile_path']).read_bytes()
    monkeypatch.setattr(dash, '_save_yaml_backup', lambda *args: 'did-not-write')
    response = apply(dash, op)
    assert response.status_code == 400
    assert 'reading it back' in response.json['error']
    saved = operations.ledger(dash.ROOT).get(op['id'])
    assert saved['status'] == status
    assert saved['result_refs']['apply_status'] == 'failed'
    if status == 'failed':
        assert saved['error'] == op['error']
    assert Path(context['profile_path']).read_bytes() == before


def test_config_writer_must_persist_wall_roi_before_success(bound, monkeypatch):
    dash, context = bound
    op, _ = captured(bound, state={'player': {'wall': True, 'wall_bar': [1, 2, 3, 4]}})
    monkeypatch.setattr(dash, 'save_config', lambda cfg: 'did-not-write')
    response = apply(dash, op)
    assert response.status_code == 400
    assert 'reading configuration back' in response.json['error']
    saved = operations.ledger(dash.ROOT).get(op['id'])
    assert saved['status'] == 'awaiting_apply'
    assert saved['result_refs']['apply_status'] == 'failed'
    assert 'rois' not in dash.load_config()['instances']['main']
    assert yaml.safe_load(Path(context['profile_path']).read_text())['player']['wall'] is True


def test_config_readback_receipt_matches_persisted_effective_configuration(bound):
    dash, context = bound
    op, _ = captured(bound, state={'player': {'wall': True, 'wall_bar': [1, 2, 3, 4]}})
    response = apply(dash, op)
    assert response.status_code == 200, response.json
    saved = operations.ledger(dash.ROOT).get(op['id'])
    assert dash.load_config()['instances']['main']['rois']['wall_bar'] == [1, 2, 3, 4]
    assert saved['result_refs']['apply_config_revision'] == operations.digest(dash.load_config())


@pytest.mark.parametrize('change', ['profile', 'context'])
def test_snapshot_never_bypasses_original_profile_or_context_guard(bound, change):
    dash, context = bound
    op, _ = captured(bound)
    if change == 'profile':
        Path(context['profile_path']).write_text('player: {wall: true}\n')
    else:
        cfg = dash.load_config()
        cfg['instances']['main']['serial'] = 'different:2'
        Path(dash.CONFIG_PATH).write_text(yaml.safe_dump(cfg))
    before = Path(context['profile_path']).read_bytes()
    response = apply(dash, op)
    assert response.status_code == 409
    assert Path(context['profile_path']).read_bytes() == before
    assert operations.ledger(dash.ROOT).get(op['id']) == op


@pytest.mark.parametrize('status', ['succeeded', 'failed'])
def test_scan_promotion_uses_operation_snapshot_not_mutable_draft(bound, status):
    dash, context = bound
    op, _ = captured(bound, kind='scan', status=status)
    draft = Path(dash.ROOT) / 'profiles' / 'main.draft.yaml'
    draft.write_text('player: {card_presets: [wrong]}\n')
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json={
            'operation_id': op['id'], 'expected_revision': op['revision'],
            'draft': 'main', 'name': 'promoted'})
    assert response.status_code == 200, response.json
    saved = Path(dash.ROOT) / 'profiles' / 'promoted.yaml'
    assert yaml.safe_load(saved.read_text())['player']['card_presets'] == ['discovered']
    assert response.json['profile_revision'] == operations.file_revision(saved)
    assert operations.ledger(dash.ROOT).get(op['id']) == op


def test_scan_promotion_rejects_wrong_identity_with_a_matching_hash(bound):
    dash, _ = bound
    op, _ = captured(bound, kind='scan', status='succeeded', state={
        'operation_id': 'different', 'player': {'card_presets': ['wrong']}})
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json={
            'operation_id': op['id'], 'expected_revision': op['revision'],
            'draft': 'main', 'name': 'promoted'})
    assert response.status_code == 409
    assert not (Path(dash.ROOT) / 'profiles' / 'promoted.yaml').exists()


def test_scan_promotion_requires_operation_for_a_tracked_current_draft(bound):
    dash, _ = bound
    captured(bound, kind='scan', status='succeeded')
    draft = Path(dash.ROOT) / 'profiles' / 'main.draft.yaml'
    draft.write_text('player: {max_tier: 1}\n')
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json={'draft': 'main', 'name': 'promoted'})
    assert response.status_code == 409
    assert 'operation' in response.json['error']
    assert not draft.with_name('promoted.yaml').exists()


def test_scan_promotion_requires_persisted_output(bound, monkeypatch):
    from runtime import files
    dash, _ = bound
    op, _ = captured(bound, kind='scan', status='succeeded')
    dest = Path(dash.ROOT) / 'profiles' / 'promoted.yaml'
    dest.write_text('player: {max_tier: 1}\n')
    monkeypatch.setattr(files, 'atomic_text', lambda *args: None)
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json={
            'operation_id': op['id'], 'expected_revision': op['revision'],
            'draft': 'main', 'name': 'promoted', 'overwrite': True})
    assert response.status_code == 400
    assert 'reading it back' in response.json['error']
    assert operations.ledger(dash.ROOT).get(op['id']) == op


def test_completed_scan_cannot_be_used_as_a_successful_calibration_receipt(bound):
    dash, _ = bound
    op, _ = captured(bound, kind='scan', status='succeeded')
    assert apply(dash, op).status_code == 409


def test_active_status_reads_current_state_with_operation_identity(bound, monkeypatch):
    dash, context = bound
    store = operations.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', context, {'phases': 'c'})
    op = store.transition(op['id'], op['revision'], 'running')
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: True))
    directory = Path(context['artifact_dir'])
    directory.mkdir(parents=True)
    (directory / 'calibrate_state.json').write_text(json.dumps({'operation_id': op['id'], 'marker': 'live'}))
    with dash.app.test_client() as client:
        response = client.get('/api/calibrate/status', query_string={'operation_id': op['id']})
    assert response.json['running'] is True
    assert response.json['state']['marker'] == 'live'


@pytest.mark.parametrize('routes', [['route_17', 'route_18'], [f'route_{i}' for i in range(23, 28)]])
def test_bounded_route_request_passes_exact_selection_to_worker(bound, monkeypatch, routes):
    dash, context = bound
    launches = []
    def launch(cfg, kind, body, command, **kwargs):
        launches.append(command)
        return operations.ledger(dash.ROOT).accept(kind, context, body)[0]
    monkeypatch.setattr(dash, '_launch_operation', launch)
    with dash.app.test_client() as client:
        response = client.post('/api/calibrate/start', json={
            'bootstrap': True, 'allow_navigation': True, 'routes': routes})
    assert response.status_code == 200, response.json
    command = launches[0]
    assert command[command.index('--routes') + 1] == ','.join(routes)
    assert '--bootstrap' in command
    assert '--overwrite' not in command and '--flows' not in command and '--battle-only' not in command
    assert response.json['operation']['request']['routes'] == routes


@pytest.mark.parametrize('payload', [
    {'routes': ['route_17', 'route_18']},
    {'bootstrap': True, 'routes': []},
    {'bootstrap': True, 'routes': 'route_17,route_18'},
    {'bootstrap': True, 'routes': ['route_18', 'route_17']},
    {'bootstrap': True, 'routes': ['route_17', 'route_18'], 'flows': True},
    {'bootstrap': True, 'routes': ['route_17', 'route_18'], 'battle_only': True},
    {'bootstrap': True, 'routes': ['route_17', 'route_18'], 'observe': True},
])
def test_invalid_route_requests_fail_before_worker_or_process_lookup(bound, monkeypatch, payload):
    dash, _ = bound
    monkeypatch.setattr(dash, '_procs', lambda: pytest.fail('Invalid routes must fail before process lookup'))
    monkeypatch.setattr(dash, '_launch_operation', lambda *args, **kwargs: pytest.fail('No worker may start'))
    with dash.app.test_client() as client:
        response = client.post('/api/calibrate/start', json=payload)
    assert response.status_code == 400
    assert response.json['ok'] is False
