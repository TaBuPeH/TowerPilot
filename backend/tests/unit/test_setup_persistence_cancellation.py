"""Synchronous persistence cannot terminate its host or release ownership early."""
import json
import importlib.util
import os
from pathlib import Path

import pytest

from control import operations
from towerpilot_workflows.operations import Conflict, Operations
from test_setup_apply_api import apply, bound, captured, dashboard_module
from harvest_support import fake_inspect


@pytest.mark.parametrize('kind', ['profile_write', 'profile_promote'])
@pytest.mark.parametrize('entrypoint', ['service', 'api'])
def test_cancellation_during_inline_write_keeps_host_and_fence(bound, monkeypatch, kind, entrypoint):
    import psutil
    from runtime import files
    dash, context = bound
    spec = importlib.util.spec_from_file_location('tp_second_dashboard_cancel', dash.__file__)
    other_dashboard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other_dashboard)
    monkeypatch.setattr(other_dashboard, 'ROOT', dash.ROOT)
    monkeypatch.setattr(other_dashboard, 'load_config', dash.load_config)
    if kind == 'profile_promote':
        source, _ = captured(bound, kind='scan', status='succeeded')
        route = '/api/profile-promote'
        body = dict(operation_id=source['id'], expected_revision=source['revision'], draft='main', name='promoted')
    else:
        directory = Path(context['artifact_dir'])
        directory.mkdir(parents=True)
        (directory / 'calibrate_state.json').write_text(json.dumps({'player': {'wall': True}}))
        route, body = '/api/calibrate/apply', {'saved_observations': True}
    original = files.atomic_text
    cancelled_at = []
    process_calls = []
    def forbidden_process(pid):
        process_calls.append(pid)
        pytest.fail('Inline cancellation must never inspect or signal an external process')
    def cancel_while_writing(path, text):
        store = Operations(operations.ledger(dash.ROOT).path)
        active = next(op for op in store.active() if op['kind'] == kind)
        assert active['status'] == 'running'
        assert active['worker']['pid'] == os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: True))
            patch.setattr(psutil, 'Process', forbidden_process)
            request = {'operation_id': active['id'], 'expected_revision': active['revision']}
            if entrypoint == 'service':
                with pytest.raises(Conflict, match='cannot be cancelled'):
                    operations.cancel(dash.ROOT, request)
            else:
                # Another dashboard has its own request lock while sharing
                # the same operation database and in-flight writer.
                with other_dashboard.app.test_client() as other_client:
                    result = other_client.post('/api/operations/' + active['id'] + '/cancel', json=request)
                assert result.status_code == 409
                assert 'cannot be cancelled' in result.json['error']
                patch.setattr(other_dashboard, '_procs', lambda: [{'pid': os.getpid(), 'runner': 'calibrate'}])
                with other_dashboard.app.test_client() as other_client:
                    legacy_kill = other_client.post('/api/control', json={'action': 'kill', 'pid': os.getpid()})
                assert legacy_kill.status_code == 400
                assert legacy_kill.json['error'] == 'not a runner pid'
            assert store.get(active['id']) == active
            assert operations.public(active)['cancellable'] is False
            assert operations.project_processes(dash.ROOT, []) == []
            assert operations.project_processes(dash.ROOT, [{'pid': os.getpid(), 'runner': 'calibrate'}]) == []
            with pytest.raises(Conflict, match='already owns'):
                store.accept('calibrate', context, {})
        cancelled_at.append(active['id'])
        return original(path, text)
    monkeypatch.setattr(files, 'atomic_text', cancel_while_writing)
    with dash.app.test_client() as client:
        response = client.post(route, json=body)
    assert response.status_code == 200, response.json
    assert len(cancelled_at) == 1
    saved = Operations(operations.ledger(dash.ROOT).path).get(cancelled_at[0])
    assert saved['status'] == 'succeeded'
    assert saved['error'] is None
    assert not process_calls


@pytest.mark.parametrize('kind', ['profile_write', 'profile_promote'])
@pytest.mark.parametrize('current', ['accepted', 'running', 'applying'])
def test_ledger_itself_refuses_inline_cancellation(bound, kind, current):
    dash, context = bound
    store = operations.ledger(dash.ROOT)
    op, _ = store.accept(kind, context, {})
    if current != 'accepted':
        op = store.transition(op['id'], op['revision'], 'running')
    if current == 'applying':
        op = store.transition(op['id'], op['revision'], 'failed')
        op = store.transition(op['id'], op['revision'], 'applying')
    with pytest.raises(Conflict, match='cannot be cancelled'):
        store.transition(op['id'], op['revision'], 'cancelling')
    assert Operations(store.path).get(op['id']) == op
    with pytest.raises(Conflict, match='already owns'):
        Operations(store.path).accept('calibrate', context, {})


@pytest.mark.parametrize('kind', ['profile_write', 'profile_promote'])
def test_runtime_separates_inline_persistence_from_workers(bound, monkeypatch, kind):
    dash, context = bound
    store = operations.ledger(dash.ROOT)
    op, _ = store.accept(kind, context, {})
    op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 1234, 'creation_time': 1})
    monkeypatch.setattr(dash.build_info, 'compare', lambda *args: {'pid': 1234})
    monkeypatch.setattr(dash, '_procs_status', lambda: {'state': 'fresh'})
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: pytest.fail('Inline owner is not a worker row')))
    with dash.app.test_client() as client:
        response = client.get('/api/runtime')
    assert response.status_code == 200
    assert response.json['workers'] == []
    assert [entry['id'] for entry in response.json['persistence_operations']] == [op['id']]
    assert response.json['persistence_operations'][0]['cancellable'] is False


@pytest.mark.parametrize('status', ['awaiting_apply', 'failed', 'interrupted'])
def test_successful_apply_receipt_replays_without_process_or_config_read(bound, monkeypatch, status):
    dash, context = bound
    source, _ = captured(bound, status=status)
    first = apply(dash, source)
    assert first.status_code == 200
    later, _ = Operations(operations.ledger(dash.ROOT).path).accept('run', context, {})
    before = Path(context['profile_path']).read_bytes()
    monkeypatch.setattr(dash, '_procs', lambda: pytest.fail('Receipt does not need current process state'))
    monkeypatch.setattr(dash, 'load_config', lambda: pytest.fail('Receipt does not need current account configuration'))
    monkeypatch.setattr(dash, '_save_yaml_backup', lambda *args: pytest.fail('A receipt replay must not write'))
    for revision in (source['revision'], first.json['operation']['revision']):
        response = apply(dash, dict(source, revision=revision))
        assert response.status_code == 200
        assert response.json['operation'] == first.json['operation']
        assert response.json['applied'] is True
    assert Path(context['profile_path']).read_bytes() == before
    assert Operations(operations.ledger(dash.ROOT).path).get(later['id']) == later


def test_new_apply_still_refuses_a_busy_worker(bound, monkeypatch):
    dash, context = bound
    source, _ = captured(bound)
    before = Path(context['profile_path']).read_bytes()
    monkeypatch.setattr(dash, '_procs', lambda: [{'runner': 'calibrate', 'pid': 5678}])
    monkeypatch.setattr(dash, '_save_yaml_backup', lambda *args: pytest.fail('Busy first apply must not write'))
    response = apply(dash, source)
    assert response.status_code == 409
    assert Path(context['profile_path']).read_bytes() == before
    assert Operations(operations.ledger(dash.ROOT).path).get(source['id']) == source
