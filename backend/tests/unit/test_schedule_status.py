"""The dashboard reads scheduler decisions; it never recreates eligibility."""

import paths as test_paths
import datetime
import os
import subprocess
import sys
from pathlib import Path

import pytest

from control import schedule_status
from scheduling import combo, daystate
from towerpilot_workflows.operations import Operations
from harvest_support import fake_inspect


NOW = datetime.datetime(2026, 10, 19, 7, 30, tzinfo=datetime.timezone.utc)


def block(uid, kind, start, end, count=None):
    return dict(id=uid, block=uid, blueprint=uid, preset='bp_' + uid, kind=kind,
                after_min=start, until_min=end, count=count)


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    import psutil
    blocks = [block('farm', 'coin', 0, 480), block('shards', 'shard', 480, 1440, 10)]
    cfg = dict(active_profile='synthetic', plan={'week': {'monday': blocks}},
               presets={'bp_farm': {'label': 'Synthetic farm'}, 'bp_shards': {'label': 'Synthetic shards'}})
    monkeypatch.setattr(combo, 'CONFIG', cfg)
    monkeypatch.setattr(combo, '_plan_unavailable_reason', None)
    monkeypatch.setattr(combo, '_plan_absent_logged', False)
    monkeypatch.setattr(combo, '_block_refusals', set())
    monkeypatch.setattr(daystate, 'STATE', str(tmp_path / 'daily.json'))
    store = Operations(tmp_path / 'logs' / 'operations.sqlite3')
    identity = dict(device_key='synthetic-device', instance='main', account=None,
                    serial='synthetic-serial', profile='synthetic', profile_revision='revision')
    operation, _ = store.accept('run', identity, {'preset': 'combo'})
    operation = store.transition(operation['id'], operation['revision'], 'running',
                                 worker={'pid': os.getpid(), 'creation_time': psutil.Process().create_time()},
                                 step='boundary_overdue',
                                 recovery_action='Existing pending transition',
                                 readiness={'ready': True}, progress={'other_feature': {'value': 7}})
    monkeypatch.setenv('TOWER_OPERATION_ID', operation['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    return tmp_path, store, operation, cfg


def read(root, now=NOW.timestamp(), **changes):
    target = dict(instance='main', account=None, serial='synthetic-serial', profile='synthetic')
    target.update(changes)
    return schedule_status.read(root, now=now, **target)


def test_canonical_decision_is_persisted_and_accessor_preserves_existing_progress(scheduler):
    root, store, before, cfg = scheduler
    winner = combo.next_block(NOW)
    after = store.get(before['id'])
    snapshot = after['progress']['schedule']
    assert winner['id'] == snapshot['winner']['id'] == 'farm'
    assert snapshot['winner']['label'] == 'Synthetic farm'
    assert snapshot['winner']['kind'] == 'coin'
    assert snapshot['reason'] == 'first_eligible_block'
    assert after['progress']['other_feature'] == {'value': 7}
    assert after['step'] == before['step']
    assert after['recovery_action'] == before['recovery_action']
    assert after['worker'] == before['worker'] and after['readiness'] == before['readiness']
    assert snapshot['observed_at'] == NOW.timestamp()
    assert snapshot['context'] == before['context']
    # An explicit aware test clock carries its scheduling timezone. Production
    # naive local clocks resolve each edge through the machine's local zone.
    local = NOW
    expected = local.replace(hour=8, minute=0, second=0, microsecond=0)
    if local.hour >= 8:
        expected = local.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    assert snapshot['next_clock_boundary']['at'] == expected.timestamp()
    assert snapshot['next_clock_boundary']['guarantees_handoff'] is False
    assert snapshot['next_clock_boundary']['kind'] == 'clock_proposal'
    response = read(root)
    assert response['available'] and response['availability'] == 'available'
    assert response['winner'] == snapshot['winner']
    assert response['step'] == 'boundary_overdue'
    assert response['handoff'] == 'natural_run_boundary'


def test_next_check_replaces_snapshot_using_the_actual_new_winner(scheduler):
    root, store, operation, cfg = scheduler
    combo.next_block(NOW)
    later = NOW.replace(hour=9)
    winner = combo.next_block(later)
    snapshot = store.get(operation['id'])['progress']['schedule']
    assert snapshot['winner']['id'] == winner['id'] == 'shards'
    assert snapshot['observed_at'] == later.timestamp()
    assert read(root, now=later.timestamp())['winner']['id'] == 'shards'


def test_unavailable_plan_is_distinct_from_known_idle_day(scheduler):
    root, store, operation, cfg = scheduler
    cfg['plan'] = None
    assert combo.next_block(NOW) is None
    unavailable = read(root)
    assert unavailable['availability'] == 'unavailable' and unavailable['winner'] is None
    assert unavailable['reason'] == 'plan_unavailable'
    assert unavailable['error']
    cfg['plan'] = {'week': {'tuesday': [block('farm', 'coin', 0, 1440)]}}
    assert combo.next_block(NOW) is None
    idle = read(root)
    assert idle['available'] and idle['winner'] is None
    assert idle['reason'] == 'no_blocks_today'
    assert idle['next_clock_boundary'] is None


def test_stale_hold_does_not_claim_a_current_winner_or_clock_boundary(scheduler):
    root, store, operation, cfg = scheduler
    combo.next_block(NOW)
    stale = read(root, now=NOW.timestamp() + 100)
    assert stale['availability'] == 'stale' and not stale['available']
    assert stale['winner'] is None and stale['next_clock_boundary'] is None
    assert stale['observed_winner']['id'] == 'farm'
    assert stale['step'] == 'boundary_overdue'
    assert stale['recovery_action'] == 'Existing pending transition'
    assert stale['reason'] == 'scheduler_observation_stale'


def test_recent_decision_requires_verified_live_worker_identity(scheduler, monkeypatch):
    root, store, operation, cfg = scheduler
    combo.next_block(NOW)
    assert read(root)['available']  # actual current process/create-time evidence
    monkeypatch.setattr(schedule_status.operations, 'inspect_worker', fake_inspect(lambda worker: False))
    lost = read(root)
    assert lost['availability'] == 'unavailable' and not lost['available']
    assert lost['reason'] == 'scheduler_worker_unavailable'
    assert lost['winner'] is None and lost['next_clock_boundary'] is None
    assert lost['observed_winner']['id'] == 'farm'
    assert lost['step'] == 'boundary_overdue'


def test_pid_reuse_cannot_make_old_scheduler_observation_current(scheduler):
    root, store, operation, cfg = scheduler
    combo.next_block(NOW)
    current = store.get(operation['id'])
    identity = dict(current['worker'], creation_time=current['worker']['creation_time'] - 10)
    store.transition(current['id'], current['revision'], 'running', worker=identity)
    assert read(root)['reason'] == 'scheduler_worker_unavailable'
    assert read(root)['winner'] is None


@pytest.mark.parametrize('changes', [dict(account='other'), dict(profile='other'),
                                     dict(serial='other'), dict(instance='other'),
                                     dict(device_key='other')])
def test_accessor_never_crosses_accepted_context(scheduler, changes):
    root, store, operation, cfg = scheduler
    combo.next_block(NOW)
    response = read(root, **changes)
    assert response['availability'] == 'unavailable'
    assert response['reason'] == 'scheduler_not_observed'
    assert response['winner'] is None


def test_no_snapshot_or_retired_scheduler_has_explicit_unavailable_state(scheduler):
    root, store, operation, cfg = scheduler
    assert read(root)['reason'] == 'scheduler_not_observed'
    combo.next_block(NOW)
    current = store.get(operation['id'])
    store.transition(current['id'], current['revision'], 'succeeded')
    assert read(root)['availability'] == 'unavailable'
    assert read(root)['winner'] is None


def test_accessor_imports_without_scheduler_settings_or_device_modules(tmp_path):
    backend = test_paths.BACKEND
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from control.schedule_status import read
assert 'scheduling.combo' not in sys.modules
assert 'settings' not in sys.modules
assert not any(name == 'device' or name.startswith('device.') for name in sys.modules)
from pathlib import Path
assert list(Path.cwd().iterdir()) == []
"""
    result = subprocess.run([sys.executable, '-I', '-c', script, str(backend)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
