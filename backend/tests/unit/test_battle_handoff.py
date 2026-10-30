"""Scheduler-child snapshots stay bound to entry even if saved source changes."""
import copy
import os
from types import SimpleNamespace

import pytest

from runtime.battle_session import BattleSession
from runtime import battle_source
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations


def test_scheduler_child_adopts_exact_delegated_snapshot_and_marks_partial_origin(tmp_path, monkeypatch):
    from scheduling import combo
    from runtime import device_lease, logger
    cfg = dict(preset='combo', active_instance='main', active_profile=None,
               instances={'main': {'preset': 'combo', 'serial': 'fake'}},
               presets={'combo': {'kind': 'combo'}, 'farm': {'kind': 'coin', 'tier': 1, 'label': 'At entry', 'shopping': []}})
    path = tmp_path/'operations.db'
    context = dict(device_key='device', instance='main', account=None, serial='fake')
    operation, _ = Operations(path).accept('run', context, {'battle_envelope': {'preset_key': 'combo', 'config': copy.deepcopy(cfg), 'preset': cfg['presets']['combo']}})
    monkeypatch.setenv('TOWER_OPERATION_DB', str(path))
    monkeypatch.setenv('TOWER_OPERATION_ID', operation['id'])
    monkeypatch.delenv('TOWER_BATTLE_ID', raising=False)
    monkeypatch.delenv('TOWER_BATTLE_GENERATION', raising=False)
    monkeypatch.setattr(combo, 'CONFIG', cfg)
    monkeypatch.setattr(device_lease, 'child_environment', lambda: dict(os.environ))
    monkeypatch.setattr(logger, 'event', lambda *args, **kwargs: None)
    captured = {}
    def spawn(args, **kwargs):
        captured.update(kwargs['env'])
        return SimpleNamespace(pid=1)
    monkeypatch.setattr(combo.subprocess, 'Popen', spawn)
    combo._spawn(['orchestrator.py', '--instance', 'main', '--preset', 'farm'])
    cfg['presets']['farm']['label'] = 'Edited after entry'
    for key in ('TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION'):
        monkeypatch.setenv(key, captured[key])
    pinned, _ = battle_source.accepted_source('main', 'farm')
    assert pinned['presets']['farm']['label'] == 'At entry'
    assert pinned['preset'] == 'farm'
    session = BattleSession.from_environment(pinned['presets']['farm'], pinned, origin='adopted')
    assert session.battle['id'] == captured['TOWER_BATTLE_ID']
    assert session.effective_preset()['label'] == 'At entry'
    assert session.battle['origin'] == 'partial_adoption'
    assert session.battle['start_certainty'] == 'unknown'
    assert len(Battles(path).active()) == 1


def test_reloaded_source_must_pass_readiness_before_config_commit(tmp_path, monkeypatch):
    import yaml
    import settings
    from player import accounts, readiness
    cfg = dict(preset='normal', active_instance='main', active_profile=None,
               instances={'main': {'serial': 'fake'}}, presets={'normal': {'kind': 'coin', 'tier': 1, 'label': 'old'}})
    edited = copy.deepcopy(cfg)
    edited['presets']['normal']['label'] = 'new'
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(edited))
    monkeypatch.setattr(settings, 'CONFIG', cfg)
    monkeypatch.setattr(settings, 'CONFIG_PATH', path)
    monkeypatch.setattr(accounts, 'effective', lambda raw: copy.deepcopy(raw))
    def refuse(*args):
        raise ValueError('Missing new run calibration')
    monkeypatch.setattr(readiness, 'require', refuse)
    with pytest.raises(ValueError, match='Missing new run calibration'):
        battle_source.reload_battle_source()
    assert cfg['presets']['normal']['label'] == 'old'


def test_unsupported_shard_driver_never_creates_batch_as_battle(tmp_path, monkeypatch):
    from scheduling import combo
    from runtime import device_lease, logger
    path = tmp_path/'operations.db'
    context = dict(device_key='device', instance='main', account=None, serial='fake')
    operation, _ = Operations(path).accept('run', context, {})
    monkeypatch.setenv('TOWER_OPERATION_DB', str(path))
    monkeypatch.setenv('TOWER_OPERATION_ID', operation['id'])
    monkeypatch.setenv('TOWER_BATTLE_ID', 'inherited-stale-record')
    monkeypatch.setenv('TOWER_BATTLE_GENERATION', 'inherited-stale-generation')
    monkeypatch.setattr(device_lease, 'child_environment', lambda: dict(os.environ))
    events, captured = [], {}
    monkeypatch.setattr(logger, 'event', lambda kind, **values: events.append((kind, values)))
    def spawn(args, **kwargs):
        captured.update(kwargs['env'])
        return SimpleNamespace(pid=1)
    monkeypatch.setattr(combo.subprocess, 'Popen', spawn)
    combo._spawn(['flows/shard.py', '--instance', 'main', '--loops', '100'])
    assert Battles(path).active() == []
    assert Battles(path).list() == []
    assert 'TOWER_BATTLE_ID' not in captured
    assert 'TOWER_BATTLE_GENERATION' not in captured
    capability = next(values for kind, values in events if kind == 'combo_driver_capability')
    assert capability['battle_history'] == capability['live_controls'] == 'unsupported'
