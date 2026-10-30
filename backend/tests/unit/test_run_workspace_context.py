"""A content hash cannot substitute for saved run scope identity."""
from pathlib import Path

import pytest
import yaml

from test_battle_api import dash


def test_identical_profile_bytes_do_not_authorize_write_after_profile_switch(dash):
    client = dash.app.test_client()
    before = client.get('/api/run-controls?blueprint=farm').get_json()
    first = Path(dash.ROOT, 'profiles', 'mine.yaml')
    second = first.with_name('second.yaml')
    second.write_bytes(first.read_bytes())
    cfg = dash.load_config()
    cfg['active_profile'] = 'second'
    Path(dash.CONFIG_PATH).write_text(yaml.safe_dump(cfg), encoding='utf-8')
    after = client.get('/api/run-controls?blueprint=farm').get_json()
    assert after['profile_revision'] == before['profile_revision']
    assert after['context'] != before['context']
    original = second.read_bytes()
    response = client.post('/api/run-controls', json={
        'blueprint': 'farm', 'context': before['context'],
        'expected_revision': before['profile_revision'],
        'changes': {'routines.ad_gems.enabled': False}})
    assert response.status_code == 409
    assert 'changed' in response.get_json()['error']
    assert first.read_bytes() == original
    assert second.read_bytes() == original


@pytest.mark.parametrize('field,value', [('instance', 'other'), ('account', 'different'),
                                        ('profile', 'second'), ('missing', None)])
def test_saved_context_is_mandatory_for_every_identity_field(dash, field, value):
    client = dash.app.test_client()
    snapshot = client.get('/api/run-controls?blueprint=farm').get_json()
    context = snapshot['context']
    if field == 'missing':
        context = None
    else:
        context[field] = value
    original = Path(dash.ROOT, 'profiles', 'mine.yaml').read_bytes()
    response = client.post('/api/run-controls', json={
        'blueprint': 'farm', 'context': context,
        'expected_revision': snapshot['profile_revision'],
        'changes': {'routines.ad_gems.enabled': False}})
    assert response.status_code == 409
    assert Path(dash.ROOT, 'profiles', 'mine.yaml').read_bytes() == original
