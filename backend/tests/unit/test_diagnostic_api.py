"""HTTP correlation and failures using the actual logger, without device calls."""
import pytest

from control import diagnostics
from test_dashboard_security import dash


def test_invalid_action_keeps_request_identity_and_reason(dash):
    response = dash.app.test_client().post('/api/clicker/step', json={'action': 'not_real'},
                                          headers={'X-Request-ID': 'request-test-123'})
    assert response.status_code == 400
    assert response.headers['X-Request-ID'] == 'request-test-123'
    rows = diagnostics.query(dash.ROOT, {'request_id': 'request-test-123'})['events']
    assert [row['kind'] for row in rows] == ['request_started', 'request_finished']
    assert rows[-1]['outcome'] == 'failed'
    assert 'Invalid action' in rows[-1]['error']
    assert rows[-1]['duration_ms'] >= 0


def test_runtime_exception_keeps_stack_and_does_not_leak_to_next_request(dash):
    @dash.app.get('/api/test-failure')
    def failing():
        raise RuntimeError('synthetic diagnostic failure')

    dash.app.config['TESTING'] = True
    client = dash.app.test_client()
    with pytest.raises(RuntimeError, match='synthetic'):
        client.get('/api/test-failure', headers={'X-Request-ID': 'failure-request'})
    rows = diagnostics.query(dash.ROOT, {'request_id': 'failure-request'})['events']
    error = next(row for row in rows if row['kind'] == 'request_exception')
    assert error['exception_type'] == 'RuntimeError'
    assert 'failing' in error['traceback']
    next_response = client.get('/api/diagnostics/events')
    assert next_response.status_code == 200
    assert next_response.headers['X-Request-ID'] != 'failure-request'


def test_client_failures_link_to_original_request_and_respect_security(dash):
    client = dash.app.test_client()
    response = client.post('/api/diagnostics/client', json={'kind': 'api_error',
        'request_id': 'original-request', 'message': 'network unavailable', 'path': '/api/control'})
    assert response.status_code == 202
    rows = diagnostics.query(dash.ROOT, {'request_id': 'original-request'})['events']
    assert rows[-1]['kind'] == 'browser_api_error'
    assert rows[-1]['trace_id'] == 'original-request'
    assert rows[-1]['reported_by_request_id'] != 'original-request'
    assert client.post('/api/diagnostics/client', json={'kind': 'api_error'},
                       headers={'Origin': 'https://untrusted.example'}).status_code == 403


def test_diagnostic_query_validation_and_client_size_limit(dash):
    client = dash.app.test_client()
    health = client.get('/api/diagnostics/events').get_json()['writer_health']
    assert isinstance(health['write_errors'], int)
    assert health['pid'] > 0
    assert client.get('/api/diagnostics/events?limit=100000').status_code == 400
    assert client.post('/api/diagnostics/client', data='x' * 17000).status_code == 413
    assert client.post('/api/diagnostics/client', json={'kind': 'arbitrary'}).status_code == 400


def test_successful_status_reads_do_not_generate_a_poll_event_storm(dash, monkeypatch):
    monkeypatch.setitem(dash.app.view_functions, 'api_status', lambda: {'ok': True})
    client = dash.app.test_client()
    for _ in range(4):
        assert client.get('/api/status').status_code == 200
    assert diagnostics.query(dash.ROOT, {'kind': 'request_finished'})['events'] == []


def test_accepted_command_joins_browser_request_to_worker_command_id(dash, monkeypatch):
    from control import battles
    result = dict(ok=True, command=dict(id='command-one', kind='action', payload={'action': 'ad_gems'}),
                  battle={'id': 'battle-one'}, control_revision=3)
    monkeypatch.setattr(battles, 'command', lambda *args, **kwargs: result)
    response = dash.app.test_client().post('/api/operations/operation-one/actions', json={},
                                          headers={'X-Request-ID': 'command-request'})
    assert response.status_code == 200
    rows = diagnostics.query(dash.ROOT, {'command_id': 'command-one'})['events']
    assert len(rows) == 1
    assert rows[0]['request_id'] == 'command-request'
    assert rows[0]['operation_id'] == 'operation-one'
    assert rows[0]['battle_id'] == 'battle-one'
    assert rows[0]['action'] == 'ad_gems'
