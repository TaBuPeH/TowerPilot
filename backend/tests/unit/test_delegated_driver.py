"""Real child-environment handoff, frozen source and exit fencing, offline."""

import paths as test_paths
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import settings
from device import adbclient
from player.run_controls import effective_preset, resolve
from runtime import battle_source, device_lease
from runtime.battle_session import BattleSession
from scheduling import combo
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Conflict, Operations
from harvest_support import fake_inspect


class Child:
    def __init__(self):
        self.returncode = None
        self.pid = 1

    def poll(self):
        return self.returncode


@pytest.fixture
def delegation(tmp_path, monkeypatch):
    cfg = dict(active_instance='main', active_profile=None, preset='combo',
               instances={'main': dict(serial='synthetic-f04', preset='combo')},
               presets={
                   'combo': dict(kind='combo'),
                   'base': dict(kind='coin', tier=1, shopping=[dict(upgrade='damage')],
                                shop_interval_sec=37, gather={'ad_gems': False, 'guild': True}),
                   'inherited': dict(base='base', label='Frozen', gather={'quest_rewards': False}),
                   'flat': dict(kind='coin', tier=1, label='Frozen', shopping=[], shop_interval_sec=42),
               })
    context = dict(device_key='synthetic-endpoint', instance='main', serial='synthetic-f04',
                   account=None, adb_host='127.0.0.7', adb_port=5047, config=copy.deepcopy(cfg))
    operations = Operations(tmp_path / 'operations.db')
    op, _ = operations.accept('run', context, {})
    op = operations.transition(op['id'], op['revision'], 'running', worker={'pid': 900, 'creation_time': 1})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', operations.path)
    for key in ('TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, 'CONFIG', cfg)
    monkeypatch.setattr(combo, 'CONFIG', cfg)
    monkeypatch.setattr(settings, '_DEFAULTS', {})
    monkeypatch.setattr(settings, '_ACCOUNT_DEFAULTS', {})
    monkeypatch.setattr(adbclient, '_HOST', ('127.0.0.7', 5047))
    launched = []

    def spawn(args, **kwargs):
        process = Child()
        launched.append((process, args, kwargs['env']))
        return process

    monkeypatch.setattr(combo.subprocess, 'Popen', spawn)
    return SimpleNamespace(cfg=cfg, op=op, operations=operations, store=Battles(operations.path),
                           launched=launched, monkeypatch=monkeypatch)


def spawn_driver(d, key='inherited'):
    return combo._spawn(['orchestrator.py', '--instance', 'main', '--preset', key])


def enter_child(d, chosen=None):
    environment = d.launched[-1][2]
    for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB', 'TOWER_BATTLE_ID',
                'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
        d.monkeypatch.setenv(key, environment[key])
    # Simulate the new interpreter at the OS lease boundary; all lock files are
    # fixture-local and no ADB call is made by the actual environment builder.
    device_lease.release_current()
    d.monkeypatch.setattr(adbclient, '_HOST', ('unaccepted-host', 1))
    acquired = []
    acquire = device_lease.acquire

    def record(serial, host, port):
        acquired.append((serial, host, port))
        return acquire(serial, host, port)

    d.monkeypatch.setattr(device_lease, 'acquire', record)
    device_lease.prepare_instance('main')
    settings.select_instance('main', chosen)
    session = BattleSession.from_environment(effective_preset(settings.CONFIG), settings.CONFIG)
    assert acquired == [('synthetic-f04', '127.0.0.7', 5047)]
    return session


@pytest.mark.parametrize('key', ['flat', 'inherited'])
def test_real_environment_bootstrap_freezes_body_and_acknowledges_exact_battle(delegation, key):
    d = delegation
    expected = effective_preset(d.cfg, key)
    child = spawn_driver(d, key)
    env = d.launched[-1][2]
    assert env['TOWER_OPERATION_ID'] == d.op['id']
    assert env['TOWER_OPERATION_DB'] == d.operations.path
    assert json.loads(env['TOWER_DEVICE_DELEGATION'])['key'] == ['127.0.0.7', 5047, 'synthetic-f04']
    d.cfg['presets']['base']['tier'] = 99
    d.cfg['presets'][key]['label'] = 'Edited after acceptance'
    session = enter_child(d)  # omitted CLI uses the envelope, not instance combo
    assert session.battle['id'] == env['TOWER_BATTLE_ID']
    assert session.effective_preset() == expected
    assert session.battle['envelope']['controls'] == resolve(expected, session.config)
    rs = SimpleNamespace(shop=None)
    session.observe(rs, 12)
    assert session.store.get(session.battle['id'])['status'] == 'active'
    command = session.store.request_controls(session.battle['id'], d.op['id'], session.generation, 0,
        {'routines': {'ad_gems': {'enabled': False}}}, 'disable-ad')
    configured = []
    rs.gems = SimpleNamespace(ad_gems=SimpleNamespace(configure=lambda values: configured.append(values)))
    # Publishing telemetry is independent of command application; no clock or
    # collector behavior is replaced on the path under test.
    d.monkeypatch.setattr(session, 'publish', lambda *args, **kwargs: None)
    session.checkpoint(rs, battle_visible=True)
    assert configured == [{'enabled': False}]
    persisted = session.store.commands(session.battle['id'])[0]
    assert persisted['id'] == command['id'] and persisted['state'] == 'applied'
    assert session.store.get(session.battle['id'])['applied_revision'] == 1
    child.returncode = 0
    combo._reconcile_child(child)
    assert session.store.get(session.battle['id'])['status'] == 'interrupted'
    assert d.operations.get(d.op['id']) == d.op


@pytest.mark.parametrize('choice', ['flat', 'missing'])
def test_conflicting_selection_refuses_before_binding(delegation, choice):
    d = delegation
    spawn_driver(d)
    for name, value in d.launched[-1][2].items():
        if name.startswith('TOWER_'):
            d.monkeypatch.setenv(name, value)
    bound = []
    d.monkeypatch.setattr(settings, 'bind_device', lambda *args: bound.append(args))
    with pytest.raises(ValueError, match='different frozen preset'):
        settings.select_instance('main', choice)
    assert bound == []


@pytest.mark.parametrize('damage', ['generation', 'operation', 'device_key', 'account', 'instance', 'serial', 'terminal', 'cancelling', 'ended'])
def test_invalid_delegation_refuses_before_lease_or_configuration_install(delegation, damage):
    d = delegation
    spawn_driver(d)
    env = d.launched[-1][2]
    for name, value in env.items():
        if name.startswith('TOWER_'):
            d.monkeypatch.setenv(name, value)
    if damage == 'generation':
        d.monkeypatch.setenv('TOWER_BATTLE_GENERATION', 'wrong')
    elif damage == 'operation':
        d.monkeypatch.setenv('TOWER_OPERATION_ID', 'missing')
    elif damage in ('terminal', 'cancelling'):
        d.operations.transition(d.op['id'], d.op['revision'], 'succeeded' if damage == 'terminal' else 'cancelling')
    elif damage == 'ended':
        d.store.finish(env['TOWER_BATTLE_ID'], env['TOWER_BATTLE_GENERATION'], reason='observed')
    else:
        with d.store._db() as db:
            battle = d.store._battle(db, env['TOWER_BATTLE_ID'])
            battle['context'][damage] = 'different'
            d.store._save(db, battle)
    before, endpoint = copy.deepcopy(settings.CONFIG), adbclient._HOST
    d.monkeypatch.setattr(device_lease, 'acquire', lambda *args: pytest.fail('Invalid delegation acquired lease'))
    with pytest.raises((ValueError, KeyError)):
        device_lease.prepare_instance('main')
    assert settings.CONFIG == before and adbclient._HOST == endpoint


def test_wrong_instance_refuses_before_lease(delegation):
    d = delegation
    d.monkeypatch.setattr(device_lease, 'acquire', lambda *args: pytest.fail('Wrong instance acquired lease'))
    with pytest.raises(Conflict, match='another emulator'):
        device_lease.prepare_instance('other')


@pytest.mark.parametrize('checkpoint', [False, True])
@pytest.mark.parametrize('state', ['cancelling', 'succeeded'])
def test_cached_active_session_rechecks_parent_before_activation_or_checkpoint(delegation, checkpoint, state):
    d = delegation
    spawn_driver(d)
    session = enter_child(d)
    rs = SimpleNamespace(shop=None)
    session.observe(rs, 1)
    d.operations.transition(d.op['id'], d.op['revision'], state)
    d.monkeypatch.setattr(session, '_manual_progress', lambda *_: pytest.fail('Retired parent advanced action'))
    with pytest.raises(Conflict, match='not active'):
        session.checkpoint(rs, battle_visible=True) if checkpoint else session.observe(rs, 2)


def test_exit_reconciles_repeated_run_preserves_ends_and_fences_successor(delegation):
    d = delegation
    child = spawn_driver(d)
    session = enter_child(d)
    first = session.battle['id']
    session.observe(SimpleNamespace(shop=None), 1)
    session.finish('observed_results', evidence={'wave': 100})
    ended = d.store.get(first)
    session.prepare_next()
    second = session.battle['id']
    d.store.activate(second, session.generation, {'controls': ['routines.ad_gems.enabled']})
    d.store.request_controls(second, d.op['id'], session.generation, 0,
                             {'routines': {'ad_gems': {'enabled': False}}}, 'pending')
    combo._reconcile_child(child)
    assert d.store.get(second)['status'] == 'active'  # child still alive
    child.returncode = 1
    combo._reconcile_child(child)
    assert d.store.get(first) == ended
    assert d.store.get(second)['status'] == 'interrupted'
    assert d.store.get(second)['outcome'] == 'unknown'
    assert d.store.commands(second)[0]['state'] == 'expired'
    # Return to parent environment for the next supported worker.
    d.monkeypatch.delenv('TOWER_BATTLE_ID')
    d.monkeypatch.delenv('TOWER_BATTLE_GENERATION')
    successor = spawn_driver(d, 'flat')
    newer = d.store.active()[0]
    assert newer['previous_battle_id'] == second
    combo._reconcile_child(child)
    assert d.store.get(newer['id'])['status'] == 'prepared'
    successor.returncode = 0
    combo._reconcile_child(successor)
    assert d.operations.get(d.op['id']) == d.op


def test_supported_to_unsupported_strips_all_operation_and_battle_context(delegation):
    d = delegation
    child = spawn_driver(d)
    child.returncode = 0
    combo._reconcile_child(child)
    d.monkeypatch.setenv('TOWER_BATTLE_ID', 'stale')
    d.monkeypatch.setenv('TOWER_BATTLE_GENERATION', 'stale')
    proc = combo._spawn(['flows/shard.py', '--instance', 'main'])
    env = d.launched[-1][2]
    assert not any(key in env for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB', 'TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION'))
    assert not hasattr(proc, 'battle_delegation')
    assert d.store.active() == []
    assert d.operations.get(d.op['id']) == d.op


def test_popen_failure_retires_prepared_battle_without_finalizing_parent(delegation):
    d = delegation
    def fail(*args, **kwargs):
        raise OSError('Synthetic spawn failure')
    d.monkeypatch.setattr(combo.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='Synthetic'):
        spawn_driver(d)
    battle = d.store.list()[0]
    assert battle['status'] == 'interrupted' and battle['reason'] == 'delegated_launch_failed'
    assert d.operations.get(d.op['id']) == d.op


def test_overdue_boundary_preserves_driver_flag_and_lease_until_natural_exit(delegation, tmp_path):
    d = delegation
    child = spawn_driver(d)
    held = device_lease.ensure_current()
    flag = tmp_path / 'stop_after_run'
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(flag))
    child.terminate = lambda: pytest.fail('Boundary timeout terminated a live driver')
    child.kill = lambda: pytest.fail('Boundary timeout killed a live driver')
    waits = []
    def wait(timeout):
        waits.append(timeout)
        assert timeout == combo.POLL  # overdue waits remain blocking, never spin
        assert child.poll() is None
        assert flag.read_text() == 'synthetic boundary'
        assert d.store.active()
        assert device_lease.ensure_current() is held
        assert held.check() is held
        operation = d.operations.get(d.op['id'])
        assert operation['status'] == 'running'
        assert operation['step'] == 'boundary_overdue'
        assert 'synthetic boundary' in operation['recovery_action']
        if len(waits) == 1:
            raise combo.subprocess.TimeoutExpired('synthetic', timeout)
        child.returncode = 0
    child.wait = wait
    combo._stop_and_wait(child, 'synthetic boundary', timeout=0)
    assert waits == [combo.POLL, combo.POLL]
    assert not flag.exists()
    assert d.store.active() == []
    operation = d.operations.get(d.op['id'])
    assert operation['status'] == 'running' and operation['worker'] == d.op['worker']
    assert operation['step'] == 'scheduler_running' and operation['recovery_action'] is None
    events = [json.loads(line) for line in combo.logger.run_file().read_text().splitlines()]
    overdue = [event for event in events if event['kind'] == 'combo_boundary_overdue']
    assert len(overdue) == 1
    assert overdue[0]['child_pid'] == child.pid
    assert overdue[0]['reason'] == 'synthetic boundary'
    assert any(event['kind'] == 'combo_boundary_exited' and event['overdue'] for event in events)


@pytest.mark.parametrize('error', [KeyboardInterrupt, OSError])
def test_interrupted_wait_keeps_pending_flag_and_live_battle(delegation, tmp_path, error):
    d = delegation
    child = spawn_driver(d)
    flag = tmp_path / 'stop_after_run'
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(flag))
    held = device_lease.ensure_current()
    def wait(timeout):
        raise error('synthetic interruption')
    child.wait = wait
    with pytest.raises(error):
        combo._stop_and_wait(child, 'pending transition', timeout=0)
    assert child.poll() is None
    assert flag.read_text() == 'pending transition'
    assert d.store.active()
    assert device_lease.ensure_current() is held
    assert d.operations.get(d.op['id'])['step'] == 'boundary_overdue'


def test_boundary_progress_does_not_overwrite_explicit_stop(delegation):
    d = delegation
    operation = d.operations.transition(d.op['id'], d.op['revision'], 'cancelling', step='stop_requested')
    combo._boundary_progress('boundary_overdue', 'late transition', 30000)
    assert d.operations.get(d.op['id']) == operation


def test_boundary_reports_overdue_at_threshold_then_keeps_blocking(delegation, tmp_path):
    d = delegation
    child = spawn_driver(d)
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(tmp_path / 'stop_after_run'))
    clock = [100.0]
    d.monkeypatch.setattr(combo.time, 'monotonic', lambda: clock[0])
    waits = []
    def wait(timeout):
        waits.append(timeout)
        operation = d.operations.get(d.op['id'])
        if len(waits) == 1:
            assert operation['step'] == 'boundary_wait'
            clock[0] += timeout
            raise combo.subprocess.TimeoutExpired('synthetic', timeout)
        assert operation['step'] == 'boundary_overdue'
        child.returncode = 0
    child.wait = wait
    combo._stop_and_wait(child, 'synthetic threshold', timeout=4)
    assert waits == [4, combo.POLL]


def test_progress_storage_failure_cannot_end_boundary_supervision(delegation, tmp_path):
    d = delegation
    child = spawn_driver(d)
    flag = tmp_path / 'stop_after_run'
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(flag))
    def unavailable(*args, **kwargs):
        raise combo.sqlite3.OperationalError('Synthetic ledger unavailable')
    d.monkeypatch.setattr(Operations, 'transition', unavailable)
    def wait(timeout):
        assert flag.exists() and d.store.active()
        child.returncode = 0
    child.wait = wait
    combo._stop_and_wait(child, 'synthetic storage failure', timeout=0)
    assert not flag.exists() and d.store.active() == []
    events = [json.loads(line) for line in combo.logger.run_file().read_text().splitlines()]
    assert any(event['kind'] == 'combo_boundary_progress_failed' for event in events)


def test_explicit_stop_now_can_cancel_an_overdue_boundary(delegation, tmp_path):
    from control import operations
    import psutil
    d = delegation
    child = spawn_driver(d)
    flag = tmp_path / 'stop_after_run'
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(flag))
    d.monkeypatch.setattr(operations, 'ledger', lambda root: d.operations)
    d.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: True))
    stopped = []
    from runtime import process_handle
    kernel_os = process_handle.kernel().os
    kernel_os.start(900, 1, ppid=100)           # the recorded worker (the delegation fixture's) ...
    kernel_os.start(901, 2, ppid=900)           # ... and the driver child it delegated to

    def signalled(event, pid):                  # stopped through kernel handles: the child first, then the worker
        if event == 'terminate':
            stopped.append('child' if pid == 901 else 'parent')
            if pid == 901:
                child.returncode = -15
            else:
                device_lease.release_current()    # this test process stands in for the worker: its lease dies with it
    kernel_os.watch(signalled)
    def wait(timeout):
        assert flag.exists() and child.poll() is None
        op = d.operations.get(d.op['id'])
        assert op['step'] == 'boundary_overdue'
        cancelled = operations.cancel(tmp_path, {'operation_id': op['id'], 'expected_revision': op['revision']})
        assert cancelled['status'] == 'cancelled'
    child.wait = wait
    combo._stop_and_wait(child, 'pending transition', timeout=0)
    assert stopped == ['child', 'parent']
    assert d.operations.get(d.op['id'])['step'] == 'driver_stopped'
    assert d.operations.get(d.op['id'])['status'] == 'cancelled'
    assert not flag.exists() and d.store.active() == []


def test_adopted_driver_uses_same_overdue_hold_and_wait_contract(monkeypatch, tmp_path):
    import psutil
    for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB'):
        monkeypatch.delenv(key, raising=False)
    flag = tmp_path / 'stop_after_run'
    monkeypatch.setattr(combo.runflag, '_path', lambda: str(flag))
    process = SimpleNamespace(pid=919, alive=True)
    process.is_running = lambda: process.alive
    process.create_time = lambda: 100.0         # cached by the walk that matched this runner
    waits = []
    def wait(timeout):
        waits.append(timeout)
        assert flag.read_text() == 'adopted transition'
        if len(waits) == 1:
            raise psutil.TimeoutExpired(timeout, pid=process.pid)
        process.alive = False
        return None  # psutil cannot recover an unrelated process's exit code
    process.wait = wait
    child = combo._Adopted(process)
    combo._stop_and_wait(child, 'adopted transition', timeout=0)
    assert waits == [combo.POLL, combo.POLL]
    assert child.returncode == -1 and child.poll() == -1
    assert not flag.exists()


def test_child_exit_wakes_wait_without_schedule_sleep(monkeypatch, tmp_path):
    import sys
    def no_sleep(*args):
        pytest.fail('Child exit waited for the schedule polling sleep')
    monkeypatch.setattr(combo.time, 'sleep', no_sleep)
    # Synthetic process only; no runner, device, account or configuration I/O.
    with combo.subprocess.Popen([sys.executable, '-c', 'pass'], cwd=tmp_path) as child:
        combo._wait_for_child(child)
        assert child.returncode == 0


def test_scheduler_rechecks_schedule_after_boundary_instead_of_starting_expired_block(delegation, tmp_path):
    d = delegation
    child = spawn_driver(d)
    old = dict(id='current', block='farm', count=None, kind='coin', preset='inherited')
    expired = dict(id='expired', block='shards', count=1, kind='shard', preset='flat')
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(tmp_path / 'stop_after_run'))
    d.monkeypatch.setattr(combo, 'next_block', lambda *args: expired if child.poll() is None else None)
    d.monkeypatch.setattr(combo, 'today_blocks', lambda *args: [old, expired])
    d.monkeypatch.setattr(combo, '_plan_mode', lambda: False)
    d.monkeypatch.setattr(combo, '_block_ok', lambda value: True)
    d.monkeypatch.setattr(combo, '_find_running_block', lambda block, *args: child if block is old else None)
    d.monkeypatch.setattr(combo, '_block_progress', lambda block: 0)
    closed = []
    d.monkeypatch.setattr(combo, '_close_block', lambda block, base: closed.append(block['id']))
    d.monkeypatch.setattr(combo, '_block_handoff', lambda *args: pytest.fail('Started an expired schedule window'))
    def wait(timeout):
        assert combo.runflag.requested() == 'combo -> expired'
        child.returncode = 0
    child.wait = wait
    def finish(*args):
        raise KeyboardInterrupt
    d.monkeypatch.setattr(combo.time, 'sleep', finish)
    combo.run('main')
    assert closed == ['current']
    assert len(d.launched) == 1


@pytest.mark.parametrize('wave', [0, 123])
def test_orphan_battle_adopts_before_handoff_even_with_unreadable_wave(delegation, tmp_path, wave):
    d = delegation
    child = spawn_driver(d)
    coin = dict(id='farm', block='farm', count=None, kind='coin', preset='inherited')
    wanted = dict(id='shards', block='shards', count=1, kind='shard', preset='flat')
    d.monkeypatch.setattr(combo.runflag, '_path', lambda: str(tmp_path / 'stop_after_run'))
    d.monkeypatch.setattr(combo, 'next_block', lambda *args: wanted if child.poll() is None else None)
    d.monkeypatch.setattr(combo, 'today_blocks', lambda *args: [wanted, coin])
    d.monkeypatch.setattr(combo, '_plan_mode', lambda: False)
    d.monkeypatch.setattr(combo, '_block_ok', lambda value: True)
    d.monkeypatch.setattr(combo, '_find_running_block', lambda *args: None)
    d.monkeypatch.setattr(combo, '_block_progress', lambda block: 0)
    d.monkeypatch.setattr(combo, '_hold_while_unknown', lambda: None)
    d.monkeypatch.setattr(combo, '_orphan_battle', lambda: wave)
    d.monkeypatch.setattr(combo, '_block_handoff', lambda *args: pytest.fail('Handoff ran over an orphan battle'))
    adopted = []
    def runner(block, instance):
        adopted.append(block['id'])
        return child
    d.monkeypatch.setattr(combo, '_block_runner', runner)
    d.monkeypatch.setattr(combo, '_close_block', lambda *args: None)
    waits = []
    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            # Adopt first, then let the ordinary switch request stop it at
            # its natural boundary on the next scheduler pass.
            assert combo.runflag.requested() is None
            raise combo.subprocess.TimeoutExpired('synthetic', timeout)
        assert combo.runflag.requested() == 'combo -> shards'
        child.returncode = 0
    child.wait = wait
    def finish(*args):
        raise KeyboardInterrupt
    d.monkeypatch.setattr(combo.time, 'sleep', finish)
    combo.run('main')
    assert adopted == ['farm'] and len(waits) == 2
    assert not (tmp_path / 'stop_after_run').exists()


def test_scheduler_exit_branch_reconciles_while_no_successor_is_scheduled(delegation):
    d = delegation
    child = spawn_driver(d)
    child.returncode = 0
    block = dict(id='finished', block='farm')
    d.monkeypatch.setattr(combo, 'next_block', lambda *args: None)
    d.monkeypatch.setattr(combo, 'today_blocks', lambda *args: [block])
    d.monkeypatch.setattr(combo, '_plan_mode', lambda: False)
    d.monkeypatch.setattr(combo, '_block_ok', lambda value: True)
    d.monkeypatch.setattr(combo, '_find_running_block', lambda *args: child)
    d.monkeypatch.setattr(combo, '_block_progress', lambda value: 0)
    closed = []
    def close(*args):
        assert d.store.active() == []
        closed.append(args)
    def stop(*args):
        raise KeyboardInterrupt
    d.monkeypatch.setattr(combo, '_close_block', close)
    d.monkeypatch.setattr(combo.time, 'sleep', stop)
    combo.run('main')
    assert closed == [(block, 0)]
    assert len(d.launched) == 1
    assert d.operations.get(d.op['id']) == d.op


def test_direct_dashboard_acceptance_freezes_resolved_inherited_body(delegation):
    d = delegation
    source = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location('f04_dashboard', source)
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    d.monkeypatch.setattr(dash.operation_api, 'launch', lambda root, cfg, kind, body, *args, **kwargs: body)
    body = dash._launch_operation(d.cfg, 'run', {'preset': 'inherited'}, ['orchestrator.py'])
    envelope = body['battle_envelope']
    expected = effective_preset(d.cfg, 'inherited')
    assert envelope['preset'] == expected
    assert envelope['controls'] == resolve(expected, d.cfg)
    op = d.operations.get(d.op['id'])
    with d.operations._db() as db:
        op['request']['battle_envelope'] = envelope
        db.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), op['id']))
    pinned, _ = battle_source.accepted_source('main')
    session = BattleSession.from_environment(effective_preset(pinned), pinned)
    assert session.effective_preset() == expected
    d.cfg['presets']['base']['tier'] = 77
    assert session.effective_preset()['tier'] == 1


def test_boundary_resolves_single_level_inheritance_before_readiness(delegation, tmp_path):
    from player import readiness
    d = delegation
    d.cfg['preset'] = 'inherited'
    saved = copy.deepcopy(d.cfg)
    saved['presets']['base']['shop_interval_sec'] = 66
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(saved), encoding='utf-8')
    d.monkeypatch.setattr(settings, 'CONFIG_PATH', path)
    checked = []
    d.monkeypatch.setattr(readiness, 'require', lambda root, cfg, body: checked.append(copy.deepcopy(body)))
    expected = effective_preset(saved)
    body, cfg = battle_source.reload_battle_source()
    assert body == expected and checked == [expected]
    body['shopping'][0]['upgrade'] = 'changed'
    assert cfg['presets']['base']['shopping'][0]['upgrade'] == 'damage'


@pytest.mark.parametrize('environment', [
    {'TOWER_BATTLE_ID': 'orphan'}, {'TOWER_BATTLE_GENERATION': 'orphan'},
    {'TOWER_BATTLE_ID': 'orphan', 'TOWER_BATTLE_GENERATION': 'orphan'},
    {'TOWER_OPERATION_ID': 'orphan'}, {'TOWER_OPERATION_DB': 'orphan'},
])
def test_incomplete_driver_identity_fails_closed(monkeypatch, environment):
    from runtime.operations import environment_operation
    for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB', 'TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION'):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(Conflict, match='Incomplete'):
        environment_operation()


def test_effective_preset_preserves_one_level_merge_and_owns_nested_values():
    cfg = {'preset': 'child', 'presets': {
        'grandparent': {'not_inherited': True},
        'base': {'base': 'grandparent', 'nested': {'deep': {'base': True}, 'kept': True}, 'list': [1]},
        'child': {'base': 'base', 'nested': {'deep': {'child': True}}}}}
    resolved = effective_preset(cfg)
    assert resolved == {'base': 'base', 'nested': {'deep': {'child': True}, 'kept': True}, 'list': [1]}
    resolved['nested']['deep']['child'] = False
    resolved['list'].append(2)
    assert cfg['presets']['child']['nested']['deep'] == {'child': True}
    assert cfg['presets']['base']['list'] == [1]


@pytest.mark.parametrize('state', ['accepted', 'succeeded'])
def test_worker_bootstrap_accounts_for_context_restore_failure_without_retiring_terminal_parent(delegation, state):
    from runtime import operation_worker
    d = delegation
    if state == 'accepted':
        # Construct a fresh accepted operation on a separate synthetic device.
        context = copy.deepcopy(d.op['context'])
        context['device_key'] = 'another-bootstrap-device'
        op, _ = d.operations.accept('run', context, {})
    else:
        op = d.operations.transition(d.op['id'], d.op['revision'], 'succeeded')
    d.monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    def fail(settings):
        raise Conflict('Synthetic rejected context')
    d.monkeypatch.setattr(operation_worker, 'restore_worker_context', fail)
    d.monkeypatch.setattr(device_lease, 'acquire', lambda *args: pytest.fail('Rejected bootstrap acquired lease'))
    with pytest.raises(Conflict, match='Synthetic rejected context'):
        operation_worker._main()
    final = d.operations.get(op['id'])
    assert final['status'] == ('failed' if state == 'accepted' else 'succeeded')
    if state == 'accepted':
        assert final['error']['code'] == 'bootstrap_failed'


def test_direct_worker_prestart_cancellation_preserves_lifecycle_without_acquiring_device(delegation):
    from runtime import operation_worker
    d = delegation
    context = copy.deepcopy(d.op['context'])
    context.update(device_key='cancelled-before-start', artifact_dir='unused')
    op, _ = d.operations.accept('run', context, {})
    op = d.operations.transition(op['id'], op['revision'], 'cancelling')
    d.monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    d.monkeypatch.setattr(device_lease, 'acquire', lambda *args: pytest.fail('Cancelled worker acquired device'))
    with pytest.raises(SystemExit, match='cancelled before worker start'):
        operation_worker._main()
    assert d.operations.get(op['id'])['status'] == 'cancelled'
    assert d.operations.get(d.op['id']) == d.op
