"""Settings transactions use temporary files only, without a device/server."""

import paths as test_paths
import importlib.util
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def dash(tmp_path, monkeypatch):
    source = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location('settings_dashboard_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'profiles').mkdir()
    cfg = dict(active_instance='main', active_profile='legacy',
               instances={'main': {'account': 'alice'}, 'other': {'account': 'bob'}},
               accounts={'alice': {'active_profile': 'alice', 'profiles': ['alice']},
                         'bob': {'active_profile': 'bob', 'profiles': ['bob']}},
               tap={'jitter_px': 4, 'unknown': 'keep'},
               missions={'visit_delay_sec': [60, 600]})
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(cfg))
    profile = dict(policies={'gather': {'shared': {'ad_gems': True, 'unknown': 123}}},
                   blueprints={'farm': {'label': 'Farm', 'policies': {'gather': 'shared'}}})
    for name in ('alice', 'bob', 'default'):
        (tmp_path / 'profiles' / f'{name}.yaml').write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(module, 'ROOT', str(tmp_path))
    monkeypatch.setattr(module, 'CONFIG_PATH', str(tmp_path / 'config.yaml'))
    return module


def save(client, snapshot, module, values):
    return client.post('/api/settings', json=dict(context=snapshot['context'],
        revision=snapshot['revision'], module=module, values=values))


def test_catalog_and_scoped_machine_persistence(dash):
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        assert snapshot['context'] == {'instance': 'main', 'account': 'alice', 'profile': 'alice'}
        gather = next(m for m in snapshot['modules'] if m['id'] == 'gather:shared')
        assert 'Farm' in gather['description']
        response = save(client, snapshot, 'missions', {'visit_delay_sec': [120, 300]})
        assert response.status_code == 200
        stored = yaml.safe_load(Path(dash.CONFIG_PATH).read_text())
        assert stored['instances']['main']['missions']['visit_delay_sec'] == [120, 300]
        assert stored['missions']['visit_delay_sec'] == [60, 600]
        assert stored['instances']['other'] == {'account': 'bob'}
        assert stored['active_profile'] == 'legacy'
        assert save(client, snapshot, 'tap', {'jitter_px': 5}).status_code == 409


def test_profile_patch_preserves_unknowns_and_other_accounts(dash):
    bob = Path(dash.ROOT, 'profiles', 'bob.yaml').read_bytes()
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        response = save(client, snapshot, 'ad:shared', {'ad_gems': False})
        assert response.status_code == 200
        alice = yaml.safe_load(Path(dash.ROOT, 'profiles', 'alice.yaml').read_text())
        assert alice['policies']['gather']['shared'] == {'ad_gems': False, 'unknown': 123}
        assert Path(dash.ROOT, 'profiles', 'bob.yaml').read_bytes() == bob
        assert list(Path(dash.ROOT, 'profiles').glob('alice.yaml.bak-*'))


@pytest.mark.parametrize('module,values', [
    ('tap', {'jitter_px': True}), ('tap', {'jitter_px': 2.5}),
    ('tap', {'jitter_ms': [20, 10]}), ('tap', {'max_rate_per_sec': 0}),
    ('tap', {'max_rate_per_sec': float('inf')}), ('tap', {'unknown': 1}),
    ('loop', {'dry_run': 1}), ('orbit:shared', {'gem_orbit.center': [1200, 800]}),
    ('orbit:shared', {'gem_orbit.taps': 0}), ('ad:shared', {'ad_gem_options.poll_sec': [.1, 4]}),
    ('ad:shared', {'gem_delay_sec': [3.5, 10]})])
def test_invalid_settings_leave_files_untouched(dash, module, values):
    config_before = Path(dash.CONFIG_PATH).read_bytes()
    profile_before = Path(dash.ROOT, 'profiles', 'alice.yaml').read_bytes()
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        assert save(client, snapshot, module, values).status_code == 400
    assert Path(dash.CONFIG_PATH).read_bytes() == config_before
    assert Path(dash.ROOT, 'profiles', 'alice.yaml').read_bytes() == profile_before


def test_account_change_and_external_edits_refuse_stale_save(dash):
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        cfg = yaml.safe_load(Path(dash.CONFIG_PATH).read_text())
        cfg['active_instance'] = 'other'
        Path(dash.CONFIG_PATH).write_text(yaml.safe_dump(cfg))
        assert save(client, snapshot, 'ad:shared', {'ad_gems': False}).status_code == 409
        current = client.get('/api/settings').get_json()
        profile = Path(dash.ROOT, 'profiles', 'bob.yaml')
        profile.write_text(profile.read_text() + '\n# external edit\n')
        assert save(client, current, 'ad:shared', {'ad_gems': False}).status_code == 409


def test_starter_profile_readonly(dash):
    cfg = yaml.safe_load(Path(dash.CONFIG_PATH).read_text())
    cfg['accounts']['alice']['active_profile'] = 'default'
    Path(dash.CONFIG_PATH).write_text(yaml.safe_dump(cfg))
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        assert save(client, snapshot, 'ad:shared', {'ad_gems': False}).status_code == 409
        assert save(client, snapshot, 'tap', {'jitter_px': 5}).status_code == 200


def test_instance_settings_reach_runtime_binding(dash, monkeypatch):
    import copy
    import settings
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        response = save(client, snapshot, 'loop', {'fps': 4, 'dry_run': True})
        assert response.status_code == 200
    saved = yaml.safe_load(Path(dash.CONFIG_PATH).read_text())
    saved['loop'] = {'fps': 2, 'dry_run': False}
    # Exercise real binding, with independent state, never a runner/device.
    monkeypatch.setattr(settings, 'CONFIG', copy.deepcopy(saved))
    monkeypatch.setattr(settings, '_DEFAULTS', {'loop': {'fps': 2, 'dry_run': False}})
    settings.bind_device('main')
    assert settings.CONFIG['loop'] == {'fps': 4, 'dry_run': True}
    settings.bind_device('other')
    assert settings.CONFIG['loop'] == {'fps': 2, 'dry_run': False}


def test_profile_validation_rejects_new_errors_preserves_old_gaps(dash, monkeypatch):
    checked = []

    def check(profile, cfg):
        checked.append(profile['policies']['gather']['shared']['ad_gems'])
        return ['pre-existing unrelated error'] + ([] if checked[-1] else ['new policy error'])

    monkeypatch.setattr(dash, '_profile_check', check)
    profile = Path(dash.ROOT, 'profiles', 'alice.yaml')
    before = profile.read_bytes()
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        response = save(client, snapshot, 'ad:shared', {'ad_gems': False})
        assert response.status_code == 400
        assert 'new policy error' in response.get_json()['error']
        assert checked == [True, False]
        assert profile.read_bytes() == before


def test_catalog_uses_consumer_defaults_and_integer_gem_delay(dash):
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
    modules = {item['id']: item for item in snapshot['modules']}
    gather_keys = {f['key'] for f in modules['gather:shared']['fields']}
    assert 'flying_gem' not in gather_keys and 'gem_delay_sec' not in gather_keys
    ad = {f['key']: f for f in modules['ad:shared']['fields']}
    assert ad['ad_gems']['default'] is True
    assert ad['gem_delay_sec']['wholeNumbers'] and ad['gem_delay_sec']['step'] == 1
    assert {f['key'] for f in modules['global_abilities']['fields']} == {'demon_mode_always'}
    orbit = {f['key']: f for f in modules['orbit:shared']['fields']}
    assert orbit['gem_orbit.jitter_frac']['value'] == .03
    assert orbit['gem_orbit.jitter_frac']['step'] == 'any'
    for module in modules.values():
        for field in module['fields']:
            expected = 1 if field['type'] == 'integer' or field['wholeNumbers'] else 'any'
            assert field['step'] == expected


def test_preview_settings_are_per_instance_and_editable_with_starter_profile(dash):
    cfg = yaml.safe_load(Path(dash.CONFIG_PATH).read_text())
    cfg['accounts']['alice']['active_profile'] = 'default'
    Path(dash.CONFIG_PATH).write_text(yaml.safe_dump(cfg))
    profile_before = Path(dash.ROOT, 'profiles', 'default.yaml').read_bytes()
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        preview = next(m for m in snapshot['modules'] if m['id'] == 'device-preview')
        assert (preview['title'], preview['group'], preview['scope'], preview['editable']) == (
            'Video preview', 'Setup', 'instance', True)
        fields = {f['key']: f for f in preview['fields']}
        assert {key: f['value'] for key, f in fields.items()} == {
            'backend': 'auto', 'fps': 15, 'mumu.install_dir': '', 'mumu.index': -1,
            'scrcpy.bitrate': 8000000}
        values = {'backend': 'mumu', 'fps': 30, 'mumu.install_dir': 'C:/MuMu Player',
                  'mumu.index': 2, 'scrcpy.bitrate': 12000000}
        response = save(client, snapshot, 'device-preview', values)
        assert response.status_code == 200
        current = next(m for m in response.get_json()['modules'] if m['id'] == 'device-preview')
        assert {f['key']: f['value'] for f in current['fields']} == values
    stored = yaml.safe_load(Path(dash.CONFIG_PATH).read_text())
    assert stored['instances']['main']['preview'] == {
        'backend': 'mumu', 'fps': 30, 'mumu': {'install_dir': 'C:/MuMu Player', 'index': 2},
        'scrcpy': {'bitrate': 12000000}}
    assert stored['instances']['other'] == {'account': 'bob'}
    assert 'preview' not in stored
    assert Path(dash.ROOT, 'profiles', 'default.yaml').read_bytes() == profile_before


@pytest.mark.parametrize('values', [
    {'backend': 'unknown'}, {'backend': True}, {'mumu.install_dir': 123},
    {'mumu.install_dir': None}, {'mumu.index': -2}, {'mumu.index': 1025},
    {'mumu.index': 1.5}, {'mumu.index': True}, {'fps': .4}, {'fps': 31},
    {'fps': float('nan')}, {'scrcpy.bitrate': 999999}, {'scrcpy.bitrate': 40000001},
    {'scrcpy.bitrate': 8000000.5}])
def test_invalid_preview_settings_do_not_write_config(dash, values):
    before = Path(dash.CONFIG_PATH).read_bytes()
    with dash.app.test_client() as client:
        snapshot = client.get('/api/settings').get_json()
        assert save(client, snapshot, 'device-preview', values).status_code == 400
    assert Path(dash.CONFIG_PATH).read_bytes() == before
