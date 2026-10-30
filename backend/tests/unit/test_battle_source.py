"""Battle boundaries and accepted workers read temporary files, never devices."""
import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import yaml

import settings
from player import accounts, playerprofile
from runtime import battle_source, operations, operation_worker, device_lease


@pytest.fixture
def source(tmp_path, monkeypatch):
    from player import readiness
    monkeypatch.setattr(readiness, 'require', lambda *args: None)
    # Frozen binding deliberately replaces these process-wide caches. Scope
    # that replacement just like CONFIG so later tests keep shipped defaults.
    monkeypatch.setattr(settings, '_DEFAULTS', copy.deepcopy(settings._DEFAULTS))
    monkeypatch.setattr(settings, '_ACCOUNT_DEFAULTS', copy.deepcopy(settings._ACCOUNT_DEFAULTS))
    raw = {'active_instance': 'main', 'active_profile': 'local', 'preset': 'bp_farm',
           'instances': {'main': {'serial': 'fake:1', 'account': 'test',
                                 'preset': 'bp_farm', 'loop': {'interval': 2}},
                         'other': {'serial': 'fake:2'}},
           'accounts': {'test': {'active_profile': 'local', 'loadouts': {}}},
           'presets': {}, 'loop': {'interval': 10}}
    profile = {'player': {}, 'policies': {}, 'blueprints': {
        'farm': {'kind': 'coin', 'loadout': 'as_is', 'tier': 1}}}
    config_path = tmp_path / 'config.yaml'
    profiles = tmp_path / 'profiles'
    profiles.mkdir()
    profile_path = profiles / 'local.yaml'
    config_path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    profile_path.write_text(yaml.safe_dump(profile), encoding='utf-8')
    current = accounts.effective(raw)
    current['presets']['bp_farm'] = {'kind': 'coin', 'tier': 1}
    monkeypatch.setattr(settings, 'CONFIG', current)
    monkeypatch.setattr(settings, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(playerprofile, 'PROFILES_DIR', profiles)
    monkeypatch.setattr(playerprofile, 'PROFILE', {'old': True})
    return SimpleNamespace(raw=raw, profile=profile, config_path=config_path,
                           profile_path=profile_path, current=current)


def test_boundary_reloads_current_profile_but_pins_driver_instance_and_run(source):
    source.raw['active_instance'] = 'other'
    source.raw['preset'] = 'different_dashboard_selection'
    source.raw['instances']['main']['preset'] = 'different_dashboard_selection'
    source.profile['blueprints']['farm']['tier'] = 2
    source.config_path.write_text(yaml.safe_dump(source.raw), encoding='utf-8')
    source.profile_path.write_text(yaml.safe_dump(source.profile), encoding='utf-8')
    old_ref = settings.CONFIG
    body, cfg = battle_source.reload_battle_source()
    assert settings.CONFIG is old_ref
    assert cfg['active_instance'] == 'main'
    assert cfg['preset'] == 'bp_farm'
    assert cfg['instances']['main']['serial'] == 'fake:1'
    assert body['tier'] == 2
    assert body['_source']['profile_revision'] == operations.file_revision(source.profile_path)
    assert cfg['loop']['interval'] == 2
    cfg['presets']['bp_farm']['tier'] = 99
    assert settings.CONFIG['presets']['bp_farm']['tier'] == 2


def test_accepted_device_binding_uses_frozen_defaults_not_import_time_values(source, monkeypatch):
    accepted = copy.deepcopy(source.current)
    accepted['rois'] = {'cash': [1, 2, 3, 4]}
    monkeypatch.setattr(battle_source, 'accepted_source', lambda *args: (accepted, None))
    monkeypatch.setattr(settings, '_DEFAULTS', {'rois': {'cash': [9, 9, 9, 9]}, 'loop': {'interval': 99}})
    monkeypatch.setattr(settings, '_ACCOUNT_DEFAULTS', {'active_profile': 'unaccepted'})
    settings.select_instance('main', 'bp_farm')
    assert settings.CONFIG['rois']['cash'] == [1, 2, 3, 4]
    assert settings.CONFIG['loop']['interval'] == 2
    assert settings.CONFIG['active_profile'] == 'local'


@pytest.mark.parametrize('field,value', [('serial', 'different:2'), ('account', None),
                                        ('rendering', {'width': 99})])
def test_boundary_refuses_rebinding_without_mutating_current_source(source, field, value):
    before = copy.deepcopy(settings.CONFIG)
    source.raw['instances']['main'][field] = value
    source.config_path.write_text(yaml.safe_dump(source.raw), encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        battle_source.reload_battle_source()
    assert settings.CONFIG == before
    assert playerprofile.PROFILE == {'old': True}


def test_removed_selected_blueprint_refuses_boundary_instead_of_switching_run(source):
    before = copy.deepcopy(settings.CONFIG)
    source.profile['blueprints'] = {'other': {'kind': 'coin', 'loadout': 'as_is', 'tier': 1}}
    source.profile_path.write_text(yaml.safe_dump(source.profile), encoding='utf-8')
    with pytest.raises(ValueError, match='no longer available'):
        battle_source.reload_battle_source()
    assert settings.CONFIG == before


def accepted(monkeypatch, cfg, profile=None):
    operation = {'status': 'running',
                 'context': dict(instance='main', serial='fake:1', account='test'),
                 'request': {'battle_envelope': {'preset_key': cfg['preset'],
        'config': copy.deepcopy(cfg), 'profile': copy.deepcopy(profile)}}}
    monkeypatch.setattr(operations, 'environment_operation', lambda: (
        SimpleNamespace(get=lambda _: copy.deepcopy(operation)), 'operation-1'))
    return operation


def test_accepted_snapshot_ignores_midflight_profile_edits_and_uses_child_selection(source, monkeypatch):
    cfg = copy.deepcopy(settings.CONFIG)
    cfg['presets']['bp_child'] = {'kind': 'coin', 'tier': 3}
    cfg['preset'] = 'bp_child'
    op = accepted(monkeypatch, cfg, source.profile)
    source.profile['blueprints']['farm']['tier'] = 9
    source.profile_path.write_text(yaml.safe_dump(source.profile), encoding='utf-8')
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Accepted source must not load editable profile')
    monkeypatch.setattr(playerprofile, 'load', forbidden)
    frozen, profile = battle_source.accepted_source('main', 'bp_child')
    assert frozen['preset'] == 'bp_child'
    assert profile['blueprints']['farm']['tier'] == 1
    frozen['presets']['bp_child']['tier'] = 99
    assert op['request']['battle_envelope']['config']['presets']['bp_child']['tier'] == 3
    with pytest.raises(ValueError, match='another emulator'):
        battle_source.accepted_source('other', 'bp_child')
    with pytest.raises(ValueError, match='different frozen preset'):
        battle_source.accepted_source('main', 'missing')


def test_settings_frozen_branch_skips_profile_recompile_and_preserves_selected_child(source, monkeypatch):
    cfg = copy.deepcopy(settings.CONFIG)
    cfg['presets']['bp_child'] = {'kind': 'coin', 'tier': 3}
    cfg['preset'] = 'bp_child'
    accepted(monkeypatch, cfg, source.profile)
    bindings = []
    monkeypatch.setattr(settings, 'bind_device', lambda name: bindings.append((name, settings.CONFIG['preset'])))
    monkeypatch.setattr(playerprofile, 'select_profile', lambda *_: pytest.fail('Frozen branch recompiled mutable profile'))
    settings.select_instance('main', 'bp_child')
    assert bindings == [('main', 'bp_child')]
    assert settings.CONFIG['preset'] == 'bp_child'
    assert playerprofile.PROFILE == source.profile


@pytest.mark.parametrize('frozen', [True, False])
def test_worker_checks_profile_revision_only_without_accepted_envelope(tmp_path, monkeypatch, frozen):
    from device import adbclient
    profile = tmp_path / 'local.yaml'
    profile.write_text('new editable profile', encoding='utf-8')
    op = {'id': 'operation-1', 'kind': 'run', 'revision': 1, 'status': 'accepted',
          'request': {'battle_envelope': {'accepted': True}} if frozen else {},
          'context': {'instance': 'main', 'serial': 'fake:1', 'profile_path': str(profile),
                      'profile_revision': 'old', 'adb_host': '127.0.0.2', 'adb_port': 5040}}
    transitions, bindings, calls = [], [], []
    ledger = SimpleNamespace(path=str(tmp_path / 'logs' / 'operations.sqlite3'),
                             get=lambda _: copy.deepcopy(op),
                             transition=lambda *args, **kwargs: transitions.append((args, kwargs)))
    monkeypatch.setattr(operation_worker, 'environment_operation', lambda: (ledger, op['id']))
    # Production launches always pass this alongside the accepted context;
    # bootstrap diagnostics use its parent as their existing local log root.
    monkeypatch.setenv('TOWER_OPERATION_DB', str(tmp_path / 'logs' / 'operations.sqlite3'))
    monkeypatch.setattr(device_lease, 'acquire', lambda *args: bindings.append(args))
    monkeypatch.setattr(operation_worker, 'restore_worker_context', lambda _: calls.append('restore'))
    monkeypatch.setattr(operation_worker, 'worker_operation', nullcontext)
    monkeypatch.setattr(operation_worker.runpy, 'run_module', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(operation_worker.sys, 'argv', ['operation_worker.py', '-m', 'fake_target', '--preset', 'bp_child'])
    monkeypatch.setattr(adbclient, '_HOST', ('old', 1))
    revisions = []
    monkeypatch.setattr(operation_worker, 'file_revision', lambda path: revisions.append(path) or 'new')
    if frozen:
        operation_worker.main()
        assert revisions == []
        assert transitions == []
        assert calls == ['restore', (('fake_target',), {'run_name': '__main__'})]
        assert operation_worker.sys.argv == ['fake_target', '--preset', 'bp_child']
    else:
        with pytest.raises(operations.Conflict, match='Profile changed'):
            operation_worker.main()
        assert revisions == [str(profile)]
        assert transitions[0][0] == ('operation-1', 1, 'failed')
        assert calls == ['restore']
    assert bindings == [('fake:1', '127.0.0.2', 5040)]
    rows = [json.loads(line) for path in (tmp_path / 'logs' / 'main').glob('events_*.jsonl')
            for line in path.read_text().splitlines()]
    assert [row['kind'] for row in rows] == ['attempt_start', 'attempt_end']
    assert all(row['operation_id'] == op['id'] and row['instance'] == 'main' for row in rows)
