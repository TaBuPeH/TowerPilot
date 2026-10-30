"""Review regressions for shared write ownership and durable apply receipts."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import psutil
import pytest
import yaml

from control import operations
from towerpilot_workflows.operations import Conflict, Operations
from test_setup_apply_api import apply, bound, captured, dashboard_module
from harvest_support import fake_inspect


def test_apply_refuses_new_admission_after_empty_process_snapshot(bound, monkeypatch):
    dash, context = bound
    original, _ = captured(bound)
    admitted = []
    def empty_snapshot_then_admit():
        other = Operations(operations.ledger(dash.ROOT).path)
        op, _ = other.accept('calibrate', context, {'phases': 'm'})
        # A capture that owns the device has a live worker (this process stands in for it); a 'running'
        # operation without one is a vanished worker and is retired, not honoured.
        me = psutil.Process()
        admitted.append(other.transition(op['id'], op['revision'], 'running',
                                         worker={'pid': me.pid, 'creation_time': me.create_time()}))
        return []
    monkeypatch.setattr(dash, '_procs', empty_snapshot_then_admit)
    monkeypatch.setattr(dash, '_save_yaml_backup', lambda *args: pytest.fail('No write while another capture owns the device'))
    before = Path(context['profile_path']).read_bytes()
    response = apply(dash, original)
    assert response.status_code == 409
    assert operations.ledger(dash.ROOT).get(original['id']) == original
    assert Path(context['profile_path']).read_bytes() == before
    assert [op['id'] for op in operations.ledger(dash.ROOT).active()] == [admitted[0]['id']]


def test_admission_is_refused_while_apply_has_write_ownership(bound, monkeypatch):
    dash, context = bound
    op, _ = captured(bound)
    writer = dash._save_yaml_backup
    refused = []
    def guarded_write(path, profile):
        another_connection = Operations(operations.ledger(dash.ROOT).path)
        with pytest.raises(Conflict, match='already owns'):
            another_connection.accept('calibrate', context, {'phases': 'm'})
        refused.append(True)
        return writer(path, profile)
    monkeypatch.setattr(dash, '_save_yaml_backup', guarded_write)
    assert apply(dash, op).status_code == 200
    assert refused == [True]


def test_two_independent_ledger_connections_cannot_claim_apply_together(bound):
    dash, context = bound
    a, _ = captured(bound)
    b, _ = captured(bound)
    database = operations.ledger(dash.ROOT).path
    barrier = threading.Barrier(2)
    def claim(op):
        store = Operations(database)
        barrier.wait(timeout=5)
        try:
            return store.transition(op['id'], op['revision'], 'applying')
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [a, b]))
    assert len([result for result in results if result]) == 1
    assert len(Operations(database).active(context['device_key'])) == 1


def test_other_device_does_not_block_apply(bound):
    dash, context = bound
    op, _ = captured(bound)
    different = dict(context, device_key='other', serial='other:1')
    operations.ledger(dash.ROOT).accept('run', different, {})
    assert apply(dash, op).status_code == 200


def test_apply_cannot_use_original_device_fence_after_adb_endpoint_changes(bound, monkeypatch):
    from device import adbclient
    dash, _ = bound
    op, _ = captured(bound)
    host, port = adbclient._HOST
    monkeypatch.setattr(adbclient, '_HOST', (host, port + 1))
    monkeypatch.setattr(dash, '_save_yaml_backup', lambda *args: pytest.fail('No write under a different device key'))
    assert apply(dash, op).status_code == 409


def test_legacy_apply_has_the_same_admission_fence(bound, monkeypatch):
    dash, context = bound
    directory = Path(context['artifact_dir'])
    directory.mkdir(parents=True)
    (directory / 'calibrate_state.json').write_text(json.dumps({'player': {'wall': True}}))
    def snapshot_then_admit():
        Operations(operations.ledger(dash.ROOT).path).accept('run', context, {})
        return []
    monkeypatch.setattr(dash, '_procs', snapshot_then_admit)
    monkeypatch.setattr(dash, '_save_yaml_backup', lambda *args: pytest.fail('Legacy writes must be fenced'))
    with dash.app.test_client() as client:
        response = client.post('/api/calibrate/apply', json={'saved_observations': True})
    assert response.status_code == 409


def test_partial_observation_merges_new_abilities_with_independent_discoveries(bound):
    dash, context = bound
    profile = Path(context['profile_path'])
    profile.write_text(yaml.safe_dump({'player': {
        'abilities': {'second_wind': True, 'demon_mode': True},
        'abilities_verified_by': 'setup', 'card_presets': ['existing'],
        'uws': {'black_hole': True}}, 'blueprints': {}}))
    context = operations.context(dash.ROOT, dash.load_config(), {}, str(profile))
    op, _ = captured((dash, context), kind='observe', state={'player': {
        'abilities': {'nuke': True}, 'abilities_verified_by': 'setup'}})
    assert apply(dash, op).status_code == 200
    saved = yaml.safe_load(profile.read_text())['player']
    assert saved['abilities'] == {'second_wind': True, 'demon_mode': True, 'nuke': True}
    assert saved['abilities_verified_by'] == 'setup'
    assert saved['card_presets'] == ['existing']
    assert saved['uws'] == {'black_hole': True}


def promotion_request(source, **kwargs):
    return dict(operation_id=source['id'], expected_revision=source['revision'],
                draft='main', name='promoted', **kwargs)


@pytest.mark.parametrize('status', ['succeeded', 'failed'])
def test_promotion_receipt_survives_reload_and_never_rewrites_later_edits(bound, monkeypatch, status):
    dash, context = bound
    source, _ = captured(bound, kind='scan', status=status)
    body = promotion_request(source)
    with dash.app.test_client() as client:
        first = client.post('/api/profile-promote', json=body)
    assert first.status_code == 200, first.json
    receipt = first.json['promotion']
    assert receipt['status'] == 'succeeded'
    assert Operations(operations.ledger(dash.ROOT).path).get(receipt['id'])['result_refs']['profile_promote']['profile_revision'] == first.json['profile_revision']
    target = Path(first.json['path'])
    target.write_text('player: {wall: true}\n')
    after_edit = target.read_bytes()
    monkeypatch.setattr(dash, '_procs', lambda: pytest.fail('A receipt replay needs no process scan or writer'))
    with dash.app.test_client() as client:
        second = client.post('/api/profile-promote', json=body)
    assert second.status_code == 200
    assert second.json == first.json
    assert target.read_bytes() == after_edit
    assert Operations(operations.ledger(dash.ROOT).path).get(source['id']) == source


@pytest.mark.parametrize('change', [{'name': 'different'}, {'overwrite': True}, {'base': 'mine'}])
def test_promotion_refuses_changed_request_without_writing(bound, change):
    dash, _ = bound
    source, _ = captured(bound, kind='scan', status='succeeded')
    body = promotion_request(source)
    with dash.app.test_client() as client:
        first = client.post('/api/profile-promote', json=body)
        assert first.status_code == 200
        second = client.post('/api/profile-promote', json=dict(body, **change))
    assert second.status_code == 409
    assert not (Path(dash.ROOT) / 'profiles/different.yaml').exists()


@pytest.mark.parametrize('crash_after_write', [False, True])
def test_promotion_recovers_interrupted_write_from_durable_intent(bound, monkeypatch, crash_after_write):
    from runtime import files
    dash, _ = bound
    source, _ = captured(bound, kind='scan', status='succeeded')
    body = promotion_request(source)
    original_writer = files.atomic_text
    writes = []
    def interrupted(path, text):
        if crash_after_write:
            original_writer(path, text)
            writes.append(True)
        raise SystemExit('Simulated dashboard exit')
    monkeypatch.setattr(files, 'atomic_text', interrupted)
    with dash.app.test_client() as client, pytest.raises(SystemExit):
        client.post('/api/profile-promote', json=body)
    key = 'profile-promote:' + source['id']
    interrupted_op = Operations(operations.ledger(dash.ROOT).path).find_by_key(key)
    assert interrupted_op['result_refs']['profile_promote_intent']
    def completed(path, text):
        writes.append(True)
        original_writer(path, text)
    monkeypatch.setattr(files, 'atomic_text', completed)
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))
    with dash.app.test_client() as client:
        result = client.post('/api/profile-promote', json=body)
    assert result.status_code == 200, result.json
    assert result.json['promotion']['status'] == 'succeeded'
    assert result.json['profile_revision'] == operations.file_revision(result.json['path'])
    assert writes == [True]


def test_promotion_interrupted_write_never_overwrites_newer_destination(bound, monkeypatch):
    from runtime import files
    dash, _ = bound
    source, _ = captured(bound, kind='scan', status='succeeded')
    body = promotion_request(source)
    original = files.atomic_text
    def crash(path, text):
        original(path, text)
        raise SystemExit('Simulated dashboard exit')
    monkeypatch.setattr(files, 'atomic_text', crash)
    with dash.app.test_client() as client, pytest.raises(SystemExit):
        client.post('/api/profile-promote', json=body)
    target = Path(dash.ROOT) / 'profiles/promoted.yaml'
    target.write_text('player: {wall: true}\n')
    before = target.read_bytes()
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))
    monkeypatch.setattr(files, 'atomic_text', lambda *args: pytest.fail('No overwrite after external changes'))
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json=body)
    assert response.status_code == 409
    assert target.read_bytes() == before


def test_scan_promotion_is_fenced_against_new_admission(bound, monkeypatch):
    dash, context = bound
    source, _ = captured(bound, kind='scan', status='succeeded')
    def snapshot_then_admit():
        operations.ledger(dash.ROOT).accept('run', context, {})
        return []
    monkeypatch.setattr(dash, '_procs', snapshot_then_admit)
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json=promotion_request(source))
    assert response.status_code == 409
    assert not (Path(dash.ROOT) / 'profiles/promoted.yaml').exists()


def test_scan_promotion_write_ownership_blocks_capture_admission(bound, monkeypatch):
    from runtime import files
    dash, context = bound
    source, _ = captured(bound, kind='scan', status='succeeded')
    writer = files.atomic_text
    checked = []
    def guarded(path, text):
        store = Operations(operations.ledger(dash.ROOT).path)
        assert [op['kind'] for op in store.active(context['device_key'])] == ['profile_promote']
        with pytest.raises(Conflict, match='already owns'):
            store.accept('calibrate', context, {})
        checked.append(True)
        return writer(path, text)
    monkeypatch.setattr(files, 'atomic_text', guarded)
    with dash.app.test_client() as client:
        response = client.post('/api/profile-promote', json=promotion_request(source))
    assert response.status_code == 200, response.json
    assert checked == [True]


def test_promotion_failed_write_can_retry_same_durable_intent(bound, monkeypatch):
    from runtime import files
    dash, _ = bound
    source, _ = captured(bound, kind='scan', status='succeeded')
    body = promotion_request(source)
    writer = files.atomic_text
    def denied(path, text):
        raise OSError('Simulated unwritable target')
    monkeypatch.setattr(files, 'atomic_text', denied)
    with dash.app.test_client() as client:
        first = client.post('/api/profile-promote', json=body)
    assert first.status_code == 400
    failed = Operations(operations.ledger(dash.ROOT).path).find_by_key('profile-promote:' + source['id'])
    assert failed['status'] == 'failed'
    assert failed['error']['code'] == 'promotion_failed'
    monkeypatch.setattr(files, 'atomic_text', writer)
    with dash.app.test_client() as client:
        retried = client.post('/api/profile-promote', json=body)
    assert retried.status_code == 200, retried.json
    assert retried.json['promotion']['id'] == failed['id']
    assert retried.json['promotion']['status'] == 'succeeded'
