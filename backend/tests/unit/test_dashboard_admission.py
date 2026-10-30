"""Admission truth for Dashboard quick actions and the drivers they start.

Offline: temporary ledgers and sealed artifacts, fake process creation, no
device, no network.

1. A setup battle whose end was never verified refuses new run/collector
   launches. The rule lives in control.operations admission (so a direct
   operations.launch is covered too) and at the entry of every driver that
   adopts a battle (orchestrator, gem collector, combo), however started.
   Only sealed per-operation evidence counts; present but malformed or foreign
   evidence is unreadable, never "nothing recorded". Identical retries keep
   their original receipt; other operation kinds are not gated.
2. The global action-lock 409 carries code 'action_lock' (status and message
   unchanged), so a retry can tell transient contention from a refusal.
3. A battle command that passed admission is never reported as a 4xx
   "nothing recorded" refusal when its reply fails afterwards.
"""
import hashlib
import json
import os
import socket
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from control import battles, operations
from harvest_support import EnvSpawn, Reached as WorkerReached, run_worker
from player.run_controls import resolve
from towerpilot_workflows.battles import Battles

LOCK_MESSAGE = 'Another action is in progress; wait for its result'


class Spawn:
    """Process creation stand-in: records every spawn, starts nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return SimpleNamespace(pid=os.getpid())


def config(dash, **changes):
    cfg = dash.load_config()
    cfg.update(changes)
    return cfg


def record(root, cfg, state=None, *, status='failed', instance='main', request=None, seal=None):
    """One calibrate operation on the instance's device, its state sealed like
    runtime.operations._retain_artifact (operations/<id>/, sha256 reference)."""
    cfg = dict(cfg, active_instance=instance)
    context = operations.context(root, cfg, {}, None)
    store = operations.ledger(root)
    op, _ = store.accept('calibrate', context, request or {'bootstrap': True, 'flows': True, 'phases': 'bootstrap'})
    if status == 'accepted':
        return op
    op = store.transition(op['id'], op['revision'], 'running')
    if status == 'running':
        return op
    refs = {}
    if state is not None:
        raw = state if isinstance(state, bytes) else json.dumps(state(op['id'])).encode()
        path = Path(context['artifact_dir']) / 'operations' / op['id'] / 'calibrate_state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        refs['calibrate_state.json'] = {'path': str(path), 'revision': hashlib.sha256(raw).hexdigest()}
        if seal:
            seal(path, refs)
    return store.transition(op['id'], op['revision'], status, result_refs=refs)


def calibrate(dash, state=None, **kwargs):
    return record(dash.ROOT, dash.load_config(), state, **kwargs)


def unresolved(op_id):
    return {'operation_id': op_id,
            'setup_battle': {'id': 'battle-' + op_id[:6], 'status': 'unknown', 'operation_id': op_id,
                             'started_at': 1.0, 'reason': "No fresh matching control for 'open intro sprint dialog'"},
            'final_screen': {'name': 'unknown', 'verified': False, 'operation_id': op_id}}


def cleared(op_id):
    """What a newer calibrate leaves: the record popped, nothing proven."""
    return {'operation_id': op_id, 'final_screen': {'name': 'unknown', 'verified': False, 'operation_id': op_id}}


def home(op_id):
    return {'operation_id': op_id, 'final_screen': {'name': 'home', 'verified': True, 'operation_id': op_id}}


def finished(op_id):
    return dict(home(op_id), setup_battle={'id': 'battle-' + op_id[:6], 'status': 'finished',
                                           'operation_id': op_id, 'finished_at': 2.0})


def operation_count(root):
    with operations.ledger(root)._db() as db:
        return db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]


def guard(dash, **changes):
    return dash._setup_battle_guard(config(dash, **changes))


def blocked(result, code, operation_id=None):
    return (result['blocks_launch'] is True and result['code'] == code
            and (operation_id is None or result['operation_id'] == operation_id))


START = {'action': 'start', 'preset': 'bp_farm'}
GEMS = {'action': 'start_gems', 'preset': 'bp_farm'}


# ------------------------------------------------- dashboard admission
@pytest.mark.parametrize('body', [START, GEMS], ids=['start', 'start_gems'])
def test_no_setup_record_allows_both_launch_paths(dash, launches, body):
    for scope, read in (('run', dash._setup_battle_guard), ('collection', dash._collection_guard)):
        assert read(dash.load_config()) == dict(
            blocks_launch=False, code=None, message=None, scope=scope, operation_id=None, setup_battle_id=None,
            recorded_status=None, evidence='none_recorded', evidence_revision=None, handoff_id=None,
            instance='main', serial='fake:1')
    response = dash.app.test_client().post('/api/control', json=dict(body, idempotency_key='key-1'))
    assert response.status_code == 200, response.get_json()
    assert len(launches) == 1


def test_unresolved_setup_battle_refuses_run_and_collector_and_records_nothing(dash, launches):
    op = calibrate(dash, unresolved)
    current = guard(dash)
    assert blocked(current, 'setup_battle_unresolved', op['id'])
    assert (current['setup_battle_id'], current['recorded_status'], current['evidence']) == (
        'battle-' + op['id'][:6], 'unknown', 'setup_battle_recorded')
    assert "open intro sprint dialog" in current['message'] and op['id'][:8] in current['message']
    sealed = Path(op['result_refs']['calibrate_state.json']['path'])
    assert current['evidence_revision'] == hashlib.sha256(sealed.read_bytes()).hexdigest()
    client = dash.app.test_client()
    before = operation_count(dash.ROOT)
    for body, key, scope in ((START, 'run-new', 'run'), (GEMS, 'gems-new', 'collection')):
        response = client.post('/api/control', json=dict(body, idempotency_key=key))
        assert response.status_code == 409
        assert response.get_json() == dict(ok=False, code='setup_battle_unresolved',
                                           error=current['message'], setup_battle_guard=dict(current, scope=scope))
    assert launches == [] and operation_count(dash.ROOT) == before
    assert not dash._ACTION_LOCK.locked()


def test_identical_retry_keeps_its_receipt_while_the_guard_refuses_new_keys(dash, launches):
    client = dash.app.test_client()
    first = client.post('/api/control', json=dict(START, idempotency_key='run-original')).get_json()
    store = operations.ledger(dash.ROOT)
    original = store.get(first['operation_id'])
    store.transition(original['id'], original['revision'], 'failed')
    calibrate(dash, unresolved)
    replay = client.post('/api/control', json=dict(START, idempotency_key='run-original'))
    assert replay.status_code == 200 and replay.get_json()['operation_id'] == original['id']
    # the receipt is resolved inside admission too, not only by the route
    spawn = Spawn()
    assert direct_launch(dash, 'run', dict(START, idempotency_key='run-original'), spawn)['id'] == original['id']
    assert spawn.calls == []
    refused = client.post('/api/control', json=dict(START, idempotency_key='run-other'))
    assert refused.status_code == 409 and refused.get_json()['code'] == 'setup_battle_unresolved'
    assert len(launches) == 1


def test_status_publishes_the_same_guard_admission_applies(dash, launches, monkeypatch):
    import player.scan_plan
    monkeypatch.setattr(player.scan_plan, 'control_gate', lambda root, cfg: {})
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    monkeypatch.setattr(dash, '_procs_status', lambda: {'state': 'fresh'})
    monkeypatch.setattr(dash, '_setup_complete', lambda: True)
    monkeypatch.setattr(dash, '_calibrate_missing', lambda: [])
    monkeypatch.setattr(dash, '_device_state', lambda cfg: {'device_state': 'device', 'port_open': True, 'serial': 'fake:1'})
    monkeypatch.setattr(dash, '_reconnect_status', lambda serial: {'state': 'idle'})
    client = dash.app.test_client()
    assert client.get('/api/status').get_json()['setup_battle_guard']['blocks_launch'] is False
    calibrate(dash, unresolved)
    published = client.get('/api/status').get_json()['setup_battle_guard']
    refused = client.post('/api/control', json=dict(START, idempotency_key='status-check')).get_json()
    assert published == refused['setup_battle_guard'] and published['blocks_launch'] is True
    assert 'setup_battle_guard' in client.get('/api/runtime').get_json()['capabilities']


def test_guard_read_failure_fails_closed_at_status_and_admission(dash, launches, monkeypatch):
    real = sqlite3.connect

    def connect(target, *args, **kwargs):
        if 'mode=ro' in str(target):
            raise sqlite3.OperationalError('database is locked')
        return real(target, *args, **kwargs)
    calibrate(dash, home)   # a ledger exists; only the guard's read fails
    monkeypatch.setattr(sqlite3, 'connect', connect)
    current = guard(dash)
    assert blocked(current, 'setup_guard_unavailable') and 'database is locked' in current['message']
    response = dash.app.test_client().post('/api/control', json=dict(START, idempotency_key='locked-db'))
    # The route's owner scan reads the same ledger: who holds the emulator is unknown, which is a refusal.
    assert response.status_code == 409 and response.get_json()['code'] == 'device_owner_unreadable'
    assert launches == []
    for kind in ('run', 'gems'):   # the admission guard itself fails closed for a launch that skips the route
        spawn = Spawn()
        with pytest.raises(battles.SetupBattleUnresolved) as refused:
            direct_launch(dash, kind, {'preset': 'bp_farm', 'idempotency_key': f'{kind}-locked'}, spawn)
        assert refused.value.guard['code'] == 'setup_guard_unavailable' and spawn.calls == []
    broken = config(dash, instances=['not', 'a', 'mapping'])
    assert blocked(dash._setup_battle_guard(broken), 'setup_guard_unavailable')


# ----------------------------------------- admission in control.operations
def direct_launch(dash, kind, command, spawn):
    cfg = dash.load_config()
    return operations.launch(dash.ROOT, cfg, kind, command, ['python', 'fake.py'], spawn,
                             dash._profile_path(cfg['active_profile']))


@pytest.mark.parametrize('kind', ['run', 'gems'])
def test_direct_operations_launch_is_refused_without_any_record(dash, kind):
    op = calibrate(dash, unresolved)
    spawn, before = Spawn(), operation_count(dash.ROOT)
    with pytest.raises(battles.SetupBattleUnresolved) as refused:
        direct_launch(dash, kind, {'preset': 'bp_farm', 'idempotency_key': f'{kind}-direct'}, spawn)
    assert refused.value.guard['operation_id'] == op['id']
    assert isinstance(refused.value, operations.Conflict)
    assert spawn.calls == [] and operation_count(dash.ROOT) == before


@pytest.mark.parametrize('kind', ['calibrate', 'scan', 'observe', 'screen_analysis', 'boot'])
def test_other_kinds_keep_their_admission_scope(dash, kind):
    calibrate(dash, unresolved)
    spawn = Spawn()
    accepted = direct_launch(dash, kind, {'idempotency_key': f'{kind}-key'}, spawn)
    assert accepted['kind'] == kind and accepted['status'] == 'accepted'
    assert len(spawn.calls) == 1


def test_direct_launch_replays_receipts_and_keeps_identity_conflicts_first(dash):
    spawn = Spawn()
    command = {'preset': 'bp_farm', 'idempotency_key': 'accepted-before'}
    original = direct_launch(dash, 'run', command, spawn)
    store = operations.ledger(dash.ROOT)
    store.transition(original['id'], store.get(original['id'])['revision'], 'failed')
    calibrate(dash, unresolved)
    assert direct_launch(dash, 'run', dict(command), spawn)['id'] == original['id']
    with pytest.raises(operations.Conflict, match='Idempotency key already belongs'):
        direct_launch(dash, 'run', dict(command, preset='bp_other'), spawn)
    stale = {'preset': 'bp_farm', 'idempotency_key': 'stale-target',
             'expected_context': {'instance': 'main', 'serial': 'fake:changed'}}
    with pytest.raises(operations.Conflict, match='before acceptance') as refused:
        direct_launch(dash, 'run', stale, spawn)
    assert not isinstance(refused.value, battles.SetupBattleUnresolved)
    assert len(spawn.calls) == 1


# ------------------------------------------------------ evidence rules
def test_clearing_by_a_newer_calibration_is_not_proof(dash):
    op = calibrate(dash, unresolved)
    calibrate(dash, cleared, request={'phases': 'c'})
    shared = Path(dash.ROOT) / 'logs' / 'main' / 'calibrate_state.json'
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(json.dumps({'phases': {}}))   # a manually cleared working file
    for current in (guard(dash), guard(dash, active_profile='another')):
        assert blocked(current, 'setup_battle_unresolved', op['id'])


def test_newer_verified_home_on_this_device_releases(dash, launches):
    calibrate(dash, unresolved)
    proof = calibrate(dash, home, status='awaiting_apply', request={'phases': 'c'})
    current = guard(dash)
    assert (current['blocks_launch'], current['evidence'], current['operation_id']) == (False, 'final_home_verified', proof['id'])
    shared = Path(dash.ROOT) / 'logs' / 'main' / 'calibrate_state.json'
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text('{}')   # the working file never changes sealed history
    assert guard(dash) == current
    response = dash.app.test_client().post('/api/control', json=dict(START, idempotency_key='after-home'))
    assert response.status_code == 200 and len(launches) == 1


def test_same_operation_finished_setup_battle_releases(dash):
    calibrate(dash, unresolved)
    proof = calibrate(dash, finished)
    current = guard(dash)
    assert (current['blocks_launch'], current['evidence'], current['operation_id'], current['recorded_status']) == (
        False, 'setup_battle_finished', proof['id'], 'finished')


def test_same_operation_home_does_not_release_its_own_unknown_battle(dash):
    op = calibrate(dash, lambda op_id: dict(unresolved(op_id), final_screen=home(op_id)['final_screen']))
    assert blocked(guard(dash), 'setup_battle_unresolved', op['id'])


def battle_with(**changes):
    def state(op_id):
        body = finished(op_id)
        body['setup_battle'] = {key: value for key, value in dict(body['setup_battle'], **changes).items()
                                if value is not ...}
        return body
    return state


def screen_with(**changes):
    def state(op_id):
        body = cleared(op_id)
        body['final_screen'] = {key: value for key, value in dict(home(op_id)['final_screen'], **changes).items()
                                if value is not ...}
        return body
    return state


@pytest.mark.parametrize('state', [
    pytest.param(battle_with(operation_id='another-operation'), id='finished-battle-foreign-owner'),
    pytest.param(battle_with(operation_id=...), id='finished-battle-missing-owner'),
    pytest.param(battle_with(operation_id=None), id='finished-battle-null-owner'),
    pytest.param(battle_with(id=...), id='finished-battle-missing-id'),
    pytest.param(battle_with(id=''), id='finished-battle-empty-id'),
    pytest.param(battle_with(status=7), id='battle-status-not-text'),
    pytest.param(battle_with(status=...), id='battle-status-missing'),
    pytest.param(lambda op_id: dict(home(op_id), setup_battle=None), id='battle-explicit-null'),
    pytest.param(lambda op_id: dict(home(op_id), setup_battle='finished'), id='battle-not-an-object'),
    pytest.param(screen_with(operation_id='another-operation'), id='home-proof-foreign-owner'),
    pytest.param(screen_with(operation_id=...), id='home-proof-missing-owner'),
    pytest.param(screen_with(verified='true'), id='home-proof-verified-not-boolean'),
    pytest.param(screen_with(name=...), id='home-proof-name-missing'),
    pytest.param(lambda op_id: dict(cleared(op_id), final_screen=None), id='final-screen-explicit-null'),
    pytest.param(lambda op_id: dict(cleared(op_id), final_screen=['home']), id='final-screen-not-an-object'),
])
def test_present_but_malformed_or_foreign_evidence_is_unreadable_even_alone(dash, launches, state):
    op = calibrate(dash, state)   # the only operation: nothing older to fall back on
    current = guard(dash)
    assert blocked(current, 'setup_evidence_unreadable', op['id']) and current['evidence'] == 'unreadable'
    response = dash.app.test_client().post('/api/control', json=dict(GEMS, idempotency_key='gems-x'))
    assert response.status_code == 409 and response.get_json()['code'] == 'setup_evidence_unreadable'
    assert launches == []


def test_foreign_finished_record_with_own_home_proof_does_not_release(dash):
    op = calibrate(dash, battle_with(operation_id='another-operation'))   # finished() carries own Home proof
    assert blocked(guard(dash), 'setup_evidence_unreadable', op['id'])


def _tamper(path, refs):
    path.write_bytes(path.read_bytes() + b' ')


def _point_at_shared_file(path, refs):
    """A reference to the mutable working file, even one whose bytes still match."""
    shared = path.parents[2] / 'calibrate_state.json'
    shared.write_bytes(path.read_bytes())
    refs['calibrate_state.json'] = dict(refs['calibrate_state.json'], path=str(shared))


def _point_at_another_operation(path, refs):
    other = path.parents[1] / 'another-operation' / path.name
    other.parent.mkdir(parents=True)
    other.write_bytes(path.read_bytes())
    refs['calibrate_state.json'] = dict(refs['calibrate_state.json'], path=str(other))


@pytest.mark.parametrize('kwargs', [
    pytest.param(dict(state=None, status='interrupted'), id='worker-lost-without-sealed-record'),
    pytest.param(dict(state=finished, seal=_tamper), id='sealed-record-changed'),
    pytest.param(dict(state=finished, seal=lambda path, refs: path.unlink()), id='sealed-record-missing'),
    pytest.param(dict(state=b'{"operation_id": '), id='sealed-record-not-json'),
    pytest.param(dict(state=lambda op_id: dict(finished(op_id), operation_id='someone-else')),
                 id='sealed-record-of-another-operation'),
    pytest.param(dict(state=home, seal=_point_at_shared_file), id='reference-to-shared-working-file'),
    pytest.param(dict(state=home, seal=_point_at_another_operation), id='reference-outside-own-sealed-directory'),
    pytest.param(dict(state=home, seal=lambda path, refs: refs.update({'calibrate_state.json': 'path'})),
                 id='reference-not-an-object'),
])
def test_unreadable_newer_evidence_never_becomes_permission(dash, launches, kwargs):
    calibrate(dash, home)   # an older proof must not shine through
    newer = calibrate(dash, **kwargs)
    current = guard(dash)
    assert blocked(current, 'setup_evidence_unreadable', newer['id']) and current['evidence'] == 'unreadable'
    response = dash.app.test_client().post('/api/control', json=dict(GEMS, idempotency_key='gems-x'))
    assert response.status_code == 409 and response.get_json()['code'] == 'setup_evidence_unreadable'
    assert launches == []


def _rewrite_row(root, op_id, sql, values):
    with operations.ledger(root)._db() as db:
        db.execute(sql, values)


@pytest.mark.parametrize('corrupt', [
    pytest.param(lambda body: '{not json', id='body-not-json'),
    pytest.param(lambda body: json.dumps([body]), id='body-not-an-object'),
    pytest.param(lambda body: json.dumps(dict(body, id='another-operation')), id='body-id-mismatch'),
    pytest.param(lambda body: json.dumps(dict(body, kind='scan')), id='body-kind-mismatch'),
    pytest.param(lambda body: json.dumps(dict(body, status='succeeded')), id='body-status-mismatch'),
    pytest.param(lambda body: json.dumps(dict(body, context=dict(body['context'], device_key='elsewhere'))),
                 id='body-device-mismatch'),
    pytest.param(lambda body: json.dumps(dict(body, context='main')), id='context-not-an-object'),
    pytest.param(lambda body: json.dumps(dict(body, result_refs=['calibrate_state.json'])), id='refs-not-an-object'),
])
def test_inconsistent_ledger_row_blocks(dash, corrupt):
    op = calibrate(dash, home)
    _rewrite_row(dash.ROOT, op['id'], 'UPDATE operations SET body=? WHERE id=?', (corrupt(op), op['id']))
    assert blocked(guard(dash), 'setup_evidence_unreadable', op['id'])


def test_unknown_operation_status_blocks(dash):
    op = calibrate(dash, home)
    body = json.dumps(dict(op, status='paused'))
    _rewrite_row(dash.ROOT, op['id'], 'UPDATE operations SET status=?, body=? WHERE id=?', ('paused', body, op['id']))
    assert blocked(guard(dash), 'setup_evidence_unreadable', op['id'])


def test_running_setup_operation_blocks_with_its_own_code(dash, launches):
    op = calibrate(dash, status='running')
    current = guard(dash)
    assert blocked(current, 'setup_operation_active', op['id']) and current['evidence'] == 'operation_active'
    # The route sees the accepted setup operation as an owner of the emulator first (shared owner body) ...
    response = dash.app.test_client().post('/api/control', json=dict(START, idempotency_key='during-setup'))
    assert response.status_code == 409 and response.get_json()['code'] == 'device_owned'
    assert response.get_json()['owner']['operation_id'] == op['id']
    assert launches == []
    # ... and admission itself, which a launch that skips the route reaches, refuses with the guard's own code.
    for kind in ('run', 'gems'):
        spawn = Spawn()
        with pytest.raises(battles.SetupBattleUnresolved) as refused:
            direct_launch(dash, kind, {'preset': 'bp_farm', 'idempotency_key': f'{kind}-during-setup'}, spawn)
        assert refused.value.guard['code'] == 'setup_operation_active' and spawn.calls == []


def test_guard_is_scoped_to_the_device_that_holds_the_record(dash):
    op = calibrate(dash, unresolved, instance='other')
    assert guard(dash)['blocks_launch'] is False
    other = guard(dash, active_instance='other')
    assert blocked(other, 'setup_battle_unresolved', op['id']) and (other['instance'], other['serial']) == ('other', 'fake:2')


def test_missing_ledger_is_nothing_recorded_and_is_not_created(tmp_path):
    identity = dict(instance='main', serial='fake:1', device_key='device')
    assert battles.setup_battle_guard(tmp_path, identity)['evidence'] == 'none_recorded'
    assert not (tmp_path / 'logs').exists()


def test_guard_read_is_read_only(dash):
    op = calibrate(dash, unresolved)
    ledger = operations.ledger_path(dash.ROOT)
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert blocked(guard(dash), 'setup_battle_unresolved', op['id'])
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before


# ------------------------------------------------------- driver entries
@pytest.fixture
def entry(tmp_path, monkeypatch):
    """A bound driver process: settings, endpoint and ledger all temporary."""
    import settings
    from device import adbclient
    cfg = dict(active_instance='main', preset='farm', presets={'farm': {'kind': 'coin', 'tier': 1}},
               instances={'main': {'serial': 'fake:1'}, 'other': {'serial': 'fake:2'}})
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    monkeypatch.setattr(settings, 'CONFIG', cfg)
    monkeypatch.setattr(adbclient, '_HOST', ('127.0.0.1', 5037))
    for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB', 'TOWER_BATTLE_ID',
                'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
        monkeypatch.delenv(key, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError('the setup-battle guard opened a socket')
    monkeypatch.setattr(socket, 'socket', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    events = []
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda kind, **fields: events.append((kind, fields)))
    return SimpleNamespace(root=tmp_path, cfg=cfg, events=events, monkeypatch=monkeypatch)


class Reached(Exception):
    """The first step after the guard: the test stops the driver there."""


def stop_at(order, name):
    def step(*args, **kwargs):
        order.append(name)
        raise Reached(name)
    return step


def refused(e):
    return [fields for kind, fields in e.events if kind == 'setup_battle_guard_refused']


def run_orchestrator(e, order):
    import orchestrator
    from runtime import battle_session, device_lease
    e.monkeypatch.setattr(orchestrator, 'CONFIG', e.cfg)
    e.monkeypatch.setattr(device_lease, 'ensure_current', lambda: order.append('lease'))
    e.monkeypatch.setattr(orchestrator.capture, 'grab', stop_at(order, 'frame'))
    e.monkeypatch.setattr(battle_session.BattleSession, 'from_environment', stop_at(order, 'adopt'))
    orchestrator.main()


def run_collector(e, order):
    import gem_collector
    from runtime import device_lease, runners
    e.monkeypatch.setattr(device_lease, 'prepare_instance', lambda name: order.append('lease'))
    e.monkeypatch.setattr(gem_collector.settings, 'select_instance', lambda *args: order.append('bind'))
    e.monkeypatch.setattr(runners, 'other_runners', stop_at(order, 'runners'))
    e.monkeypatch.setattr(gem_collector.capture, 'grab', stop_at(order, 'frame'))
    gem_collector.main('main', 'farm')


def run_combo(e, order, instance='main'):
    from scheduling import combo
    e.monkeypatch.setattr(combo, 'CONFIG', e.cfg)
    e.monkeypatch.setattr(combo, 'next_block', stop_at(order, 'schedule'))
    e.monkeypatch.setattr(combo, '_find_running_block', stop_at(order, 'adopt'))
    e.monkeypatch.setattr(combo, '_spawn', stop_at(order, 'child'))
    combo.run(instance)


DRIVERS = {'orchestrator': (run_orchestrator, ['lease'], ['lease', 'frame']),
           'gem_collector': (run_collector, ['lease', 'bind'], ['lease', 'bind', 'runners']),
           'combo': (run_combo, [], ['schedule'])}


@pytest.mark.parametrize('driver', sorted(DRIVERS))
def test_driver_entry_refuses_before_any_frame_adoption_or_child(entry, driver):
    start, before, after = DRIVERS[driver]
    op = record(entry.root, entry.cfg, unresolved)
    order = []
    with pytest.raises(battles.SetupBattleUnresolved) as refusal:
        start(entry, order)
    assert order == before
    assert refusal.value.guard['operation_id'] == op['id']
    assert [fields['code'] for fields in refused(entry)] == ['setup_battle_unresolved']
    # the refusing setup operation never overwrites the worker's own operation_id
    assert [fields['setup_operation_id'] for fields in refused(entry)] == [op['id']]
    assert all('operation_id' not in fields for fields in refused(entry))


@pytest.mark.parametrize('driver', sorted(DRIVERS))
def test_driver_entry_continues_when_evidence_releases(entry, driver):
    start, before, after = DRIVERS[driver]
    record(entry.root, entry.cfg, unresolved)
    record(entry.root, entry.cfg, home)
    order = []
    with pytest.raises(Reached):
        start(entry, order)
    assert order == after and refused(entry) == []


@pytest.mark.parametrize('driver', sorted(DRIVERS))
def test_driver_entry_refuses_unreadable_evidence(entry, driver):
    start, before, after = DRIVERS[driver]
    op = record(entry.root, entry.cfg, battle_with(operation_id='another-operation'))
    order = []
    with pytest.raises(battles.SetupBattleUnresolved) as refusal:
        start(entry, order)
    assert order == before and refusal.value.guard['code'] == 'setup_evidence_unreadable'
    assert refusal.value.guard['operation_id'] == op['id']


def test_accepted_operation_environment_is_not_a_bypass(entry):
    """A launch identity in the environment (valid or not) never skips the rule."""
    record(entry.root, entry.cfg, unresolved)
    context = operations.context(entry.root, entry.cfg, {}, None)
    store = operations.ledger(entry.root)
    op, _ = store.accept('run', context, {})
    store.transition(op['id'], op['revision'], 'running')
    entry.monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    entry.monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    for driver in ('combo', 'orchestrator'):
        order = []
        with pytest.raises(battles.SetupBattleUnresolved):
            DRIVERS[driver][0](entry, order)
        assert order == DRIVERS[driver][1]
    entry.monkeypatch.setenv('TOWER_OPERATION_ID', 'forged-operation')
    with pytest.raises(battles.SetupBattleUnresolved):
        run_combo(entry, [])


def test_driver_entry_uses_the_bound_instance_and_adb_endpoint(entry):
    from device import adbclient
    record(entry.root, entry.cfg, unresolved, instance='other')
    with pytest.raises(Reached):
        run_combo(entry, [], 'main')
    with pytest.raises(battles.SetupBattleUnresolved):
        run_combo(entry, [], 'other')
    # the ledger and the lease normalise loopback names the same way
    entry.monkeypatch.setattr(adbclient, '_HOST', ('LOCALHOST', 5037))
    with pytest.raises(battles.SetupBattleUnresolved):
        run_combo(entry, [], 'other')
    entry.monkeypatch.setattr(adbclient, '_HOST', ('127.0.0.1', 5038))   # another adb server
    with pytest.raises(Reached):
        run_combo(entry, [], 'other')


def test_identity_is_shared_by_admission_and_entry(entry):
    cfg = dict(entry.cfg, active_instance='other')
    identity = operations.device_identity(entry.cfg, 'other')
    context = operations.context(entry.root, cfg, {}, None)
    assert {key: context[key] for key in identity} == identity


def test_guard_reads_the_one_shared_ledger_path(dash, monkeypatch, tmp_path):
    """The guard derives no path of its own: redirecting ledger_path (as the
    suite-wide isolation does) moves every reader, the guard included."""
    op = calibrate(dash, unresolved)
    assert blocked(guard(dash), 'setup_battle_unresolved', op['id'])
    elsewhere = tmp_path / 'elsewhere'
    monkeypatch.setattr(operations, 'ledger_path', lambda root: elsewhere / 'logs' / 'operations.sqlite3')
    assert guard(dash)['evidence'] == 'none_recorded'
    assert not elsewhere.exists()


# --------------------------------------- collection-only handoff (milestone 1)
ATTEST = 'new_manual_battle'
HANDOFF = '/api/setup-battle/collection-handoff'


def handoff_body(blocker, **changes):
    body = dict(operation_id=blocker['operation_id'], setup_battle_id=blocker['setup_battle_id'],
                evidence_revision=blocker['evidence_revision'], attestation=ATTEST,
                expected_context=dict(instance='main', account=None, serial='fake:1'))
    body.update(changes)
    return body


def handoff_count(root):
    with operations.ledger(root)._db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_handoffs'").fetchone():
            return 0
        return db.execute('SELECT COUNT(*) FROM collection_handoffs').fetchone()[0]


def operation_row(root, op_id):
    with operations.ledger(root)._db() as db:
        return db.execute('SELECT id,device,kind,status,body FROM operations WHERE id=?', (op_id,)).fetchone()


def collection(dash, **changes):
    return dash._collection_guard(config(dash, **changes))


def test_handoff_allows_only_collection_and_leaves_setup_history_unchanged(dash, launches, runner_rows):
    op = calibrate(dash, unresolved)
    blocker = guard(dash)
    sealed = Path(op['result_refs']['calibrate_state.json']['path'])
    sealed_before, row_before = sealed.read_bytes(), operation_row(dash.ROOT, op['id'])
    assert blocked(collection(dash), 'setup_battle_unresolved', op['id'])   # no handoff yet
    client = dash.app.test_client()
    response = client.post(HANDOFF, json=handoff_body(blocker))
    assert response.status_code == 200, response.get_json()
    reply = response.get_json()
    handoff = reply['handoff']
    assert reply['created'] is True and handoff['scope'] == 'collection_only' and handoff['attestation'] == ATTEST
    assert handoff['blocker'] == dict(operation_id=op['id'], setup_battle_id=blocker['setup_battle_id'],
                                      evidence_revision=blocker['evidence_revision'], recorded_status='unknown')
    assert (handoff['instance'], handoff['account'], handoff['serial']) == ('main', None, 'fake:1')
    allowed = reply['collection_guard']
    assert (allowed['blocks_launch'], allowed['code'], allowed['evidence'], allowed['handoff_id']) == (
        False, None, 'collection_handoff', handoff['id'])
    assert (allowed['operation_id'], allowed['recorded_status'], allowed['scope']) == (op['id'], 'unknown', 'collection')
    assert reply['setup_battle_guard'] == blocker   # general runs stay refused
    again = client.post(HANDOFF, json=handoff_body(blocker)).get_json()
    assert again['created'] is False and again['handoff']['id'] == handoff['id'] and handoff_count(dash.ROOT) == 1
    collected = client.post('/api/control', json=dict(GEMS, idempotency_key='collect-manual'))
    assert collected.status_code == 200, collected.get_json()
    assert collected.get_json()['operation']['kind'] == 'gems' and len(launches) == 1
    [accepted] = operations.ledger(dash.ROOT).active(collected.get_json()['operation']['context']['device_key'])
    owned = client.post('/api/control', json=dict(START, idempotency_key='run-while-collecting'))
    assert owned.status_code == 409 and owned.get_json()['code'] == 'device_owned'   # the collector owns the emulator
    operations.ledger(dash.ROOT).transition(accepted['id'], accepted['revision'], 'interrupted')
    refused = client.post('/api/control', json=dict(START, idempotency_key='run-manual'))
    assert refused.status_code == 409 and refused.get_json()['code'] == 'setup_battle_unresolved'
    assert len(launches) == 1
    assert sealed.read_bytes() == sealed_before and operation_row(dash.ROOT, op['id']) == row_before


@pytest.mark.parametrize('changes,status,code', [
    pytest.param(dict(attestation='same_battle'), 400, None, id='wrong-attestation'),
    pytest.param(dict(attestation=None), 400, None, id='no-attestation'),
    pytest.param(dict(evidence_revision=None), 400, None, id='no-revision'),
    pytest.param(dict(expected_context=None), 400, None, id='no-context'),
    pytest.param(dict(setup_battle_id='another-battle'), 409, 'handoff_blocker_changed', id='other-battle'),
    pytest.param(dict(evidence_revision='0' * 64), 409, 'handoff_blocker_changed', id='other-revision'),
    pytest.param(dict(operation_id='another-operation'), 409, 'handoff_blocker_changed', id='other-operation'),
    pytest.param(dict(expected_context=dict(instance='main', account=None, serial='fake:9')), 409,
                 'context_changed', id='other-serial'),
    pytest.param(dict(expected_context=dict(instance='main', account='someone', serial='fake:1')), 409,
                 'context_changed', id='other-account'),
    pytest.param(dict(expected_context=dict(instance='other', account=None, serial='fake:1')), 409,
                 'context_changed', id='other-instance'),
])
def test_inexact_handoff_requests_record_nothing(dash, launches, runner_rows, changes, status, code):
    op = calibrate(dash, unresolved)
    response = post = dash.app.test_client().post(HANDOFF, json=handoff_body(guard(dash), **changes))
    assert post.status_code == status and response.get_json()['ok'] is False
    assert response.get_json().get('code') == code
    assert handoff_count(dash.ROOT) == 0
    assert blocked(collection(dash), 'setup_battle_unresolved', op['id'])


def test_handoff_is_refused_while_anything_owns_the_emulator(dash, launches, runner_rows):
    calibrate(dash, unresolved)
    blocker = guard(dash)
    client = dash.app.test_client()
    runner_rows.append((4242, 'orchestrator.py', ['python', 'orchestrator.py']))
    response = client.post(HANDOFF, json=handoff_body(blocker))
    assert response.status_code == 409 and response.get_json()['code'] == 'device_owned'
    assert '4242' in response.get_json()['error']
    runner_rows.clear()
    store = operations.ledger(dash.ROOT)
    context = operations.context(dash.ROOT, dash.load_config(), {}, None)
    owner, _ = store.accept('run', context, {}, 'manual-run-owner')
    store.transition(owner['id'], owner['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    response = client.post(HANDOFF, json=handoff_body(blocker))
    assert response.status_code == 409 and response.get_json()['code'] == 'device_owned'
    assert handoff_count(dash.ROOT) == 0


@pytest.mark.parametrize('setup,code', [('none', 'handoff_blocker_changed'),
                                        ('running', 'device_owned'),   # the running setup owns the device
                                        ('unreadable', 'handoff_blocker_changed'),
                                        ('home', 'handoff_blocker_changed')])
def test_only_an_unresolved_setup_record_can_be_handed_off(dash, runner_rows, setup, code):
    states = {'none': None, 'running': dict(status='running'), 'unreadable': dict(state=None, status='interrupted'),
              'home': dict(state=home)}
    if states[setup] is not None:
        calibrate(dash, **states[setup])
    current = guard(dash)
    body = handoff_body(dict(operation_id=current['operation_id'] or 'none',
                             setup_battle_id=current['setup_battle_id'] or 'none',
                             evidence_revision=current['evidence_revision'] or 'none'))
    response = dash.app.test_client().post(HANDOFF, json=body)
    assert response.status_code == 409 and response.get_json()['code'] == code
    assert handoff_count(dash.ROOT) == 0


def test_a_held_device_lease_refuses_the_handoff_even_when_the_ledger_and_process_table_read_empty(dash, runner_rows):
    # The handoff authorizes collection on the battle on screen: it is recorded only while nothing owns
    # the emulator, and the OS device lease is the final owner (another checkout's worker, an untracked process).
    from runtime import device_lease
    op = calibrate(dash, unresolved)
    blocker = guard(dash)
    identity = operations.device_identity(dash.load_config())
    held = device_lease.acquire(identity['serial'], identity['adb_host'], identity['adb_port'])
    refused = dash.app.test_client().post(HANDOFF, json=handoff_body(blocker))
    assert refused.status_code == 409 and refused.get_json()['code'] == 'device_owned'
    assert 'emulator lock' in refused.get_json()['error'] and handoff_count(dash.ROOT) == 0
    held.release()
    recorded = dash.app.test_client().post(HANDOFF, json=handoff_body(blocker))
    assert recorded.status_code == 200 and recorded.get_json()['created'] is True
    assert recorded.get_json()['handoff']['blocker']['operation_id'] == op['id']


def test_an_unreadable_device_lease_refuses_the_handoff_as_unreadable_not_as_a_bad_request(dash, runner_rows, monkeypatch):
    from runtime import device_lease
    calibrate(dash, unresolved)
    blocker = guard(dash)
    monkeypatch.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    refused = dash.app.test_client().post(HANDOFF, json=handoff_body(blocker))
    assert refused.status_code == 409 and refused.get_json()['code'] == 'device_owner_unreadable'
    assert handoff_count(dash.ROOT) == 0


def _hand_off(dash):
    op = calibrate(dash, unresolved)
    body = handoff_body(guard(dash))
    record, created, _ = battles.record_collection_handoff(dash.ROOT, dash.load_config(), body, owners=lambda i: [])
    assert created and collection(dash)['evidence'] == 'collection_handoff'
    return op, record


@pytest.mark.parametrize('newer,code,decisive,route_code', [
    pytest.param(dict(state=unresolved), 'setup_battle_unresolved', 'newer', 'setup_battle_unresolved',
                 id='new-setup-battle-unresolved'),
    pytest.param(dict(state=cleared, request={'phases': 'c'}), 'setup_battle_unresolved', 'older',
                 'setup_battle_unresolved', id='new-calibration-proving-nothing'),
    # a running setup operation holds the emulator, so the route answers with the shared owner body first
    pytest.param(dict(status='running'), 'setup_operation_active', 'newer', 'device_owned', id='new-setup-running'),
    pytest.param(dict(state=None, status='interrupted'), 'setup_evidence_unreadable', 'newer',
                 'setup_evidence_unreadable', id='new-setup-unreadable'),
])
def test_any_newer_setup_operation_ends_the_handoff(dash, launches, newer, code, decisive, route_code):
    old, record = _hand_off(dash)
    new = calibrate(dash, **newer)
    current = collection(dash)
    assert blocked(current, code, new['id'] if decisive == 'newer' else old['id'])
    assert current['handoff_id'] is None
    response = dash.app.test_client().post('/api/control', json=dict(GEMS, idempotency_key='after-new-setup'))
    assert response.status_code == 409 and response.get_json()['code'] == route_code
    assert launches == []
    spawn = Spawn()   # admission refuses with the guard's own code however the launch got there
    with pytest.raises(battles.SetupBattleUnresolved) as refused:
        direct_launch(dash, 'gems', {'preset': 'bp_farm', 'idempotency_key': 'gems-after-new-setup'}, spawn)
    assert refused.value.guard['code'] == code and spawn.calls == []


def test_a_newer_verified_home_releases_both_scopes_without_the_handoff(dash):
    _hand_off(dash)
    proof = calibrate(dash, home, request={'phases': 'c'})
    for current in (guard(dash), collection(dash)):
        assert (current['blocks_launch'], current['evidence'], current['operation_id'], current['handoff_id']) == (
            False, 'final_home_verified', proof['id'], None)


def test_handoff_is_bound_to_account_and_device(dash):
    op, record = _hand_off(dash)
    cfg = dash.load_config()
    cfg['instances']['main']['account'] = 'acct_b'
    cfg['accounts'] = {'acct_b': {'active_profile': 'mine'}}
    assert blocked(dash._collection_guard(cfg), 'setup_battle_unresolved', op['id'])
    other_serial = config(dash)
    other_serial['instances']['main']['serial'] = 'fake:1-alias'
    assert dash._collection_guard(other_serial)['evidence'] == 'none_recorded'   # another identity entirely
    assert collection(dash)['handoff_id'] == record['id']


@pytest.mark.parametrize('tamper', [
    pytest.param(dict(scope='general'), id='scope'),
    pytest.param(dict(attestation='same_battle'), id='attestation'),
    pytest.param(dict(account='someone-else'), id='account'),
    pytest.param(dict(serial='fake:9'), id='serial'),
    pytest.param(dict(device_key='elsewhere'), id='device'),
    pytest.param(dict(blocker={'operation_id': 'x', 'setup_battle_id': 'y', 'evidence_revision': 'z'}), id='blocker'),
])
def test_a_record_that_does_not_restate_its_identity_grants_nothing(dash, tamper):
    op, record = _hand_off(dash)
    with operations.ledger(dash.ROOT)._db() as db:
        db.execute('UPDATE collection_handoffs SET body=? WHERE id=?', (json.dumps(dict(record, **tamper)), record['id']))
    assert blocked(collection(dash), 'setup_battle_unresolved', op['id'])


def test_direct_admission_honours_the_handoff_for_collection_only(dash):
    _hand_off(dash)
    spawn = Spawn()
    accepted = direct_launch(dash, 'gems', {'preset': 'bp_farm', 'idempotency_key': 'direct-gems'}, spawn)
    assert accepted['kind'] == 'gems' and len(spawn.calls) == 1
    operations.ledger(dash.ROOT).transition(accepted['id'], accepted['revision'], 'failed')
    with pytest.raises(battles.SetupBattleUnresolved) as refused:
        direct_launch(dash, 'run', {'preset': 'bp_farm', 'idempotency_key': 'direct-run'}, spawn)
    assert refused.value.guard['scope'] == 'run' and len(spawn.calls) == 1


def test_collection_guard_read_is_read_only_with_handoffs(dash):
    _hand_off(dash)
    ledger = operations.ledger_path(dash.ROOT)
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert collection(dash)['evidence'] == 'collection_handoff'
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before


def _entry_handoff(entry, cfg=None):
    cfg = cfg or entry.cfg
    op = record(entry.root, cfg, unresolved)
    blocker = battles.setup_battle_guard(entry.root, battles.guard_identity(cfg))
    body = dict(operation_id=op['id'], setup_battle_id=blocker['setup_battle_id'],
                evidence_revision=blocker['evidence_revision'], attestation=ATTEST,
                expected_context={k: battles.guard_identity(cfg)[k] for k in ('instance', 'account', 'serial')})
    return battles.record_collection_handoff(entry.root, cfg, body, owners=lambda identity: [])[0]


def test_collector_entry_honours_the_handoff_and_other_drivers_still_refuse(entry):
    _entry_handoff(entry)
    order = []
    with pytest.raises(Reached):
        run_collector(entry, order)
    assert order == ['lease', 'bind', 'runners'] and refused(entry) == []
    for driver in ('orchestrator', 'combo'):
        with pytest.raises(battles.SetupBattleUnresolved):
            DRIVERS[driver][0](entry, [])


def test_collector_entry_never_takes_permission_from_the_environment(entry):
    record(entry.root, entry.cfg, unresolved)
    entry.monkeypatch.setenv('TOWER_OPERATION_ID', 'forged-operation')
    entry.monkeypatch.setenv('TOWER_COLLECTION_HANDOFF', 'forged')
    order = []
    with pytest.raises(battles.SetupBattleUnresolved):
        run_collector(entry, order)
    assert order == ['lease', 'bind']


def test_entry_binds_the_handoff_to_the_restored_accepted_context(entry):
    """An accepted collector's identity comes from the operation context that
    restore_worker_context installs (config and ADB endpoint), not the CLI's."""
    import settings
    from device import adbclient
    from runtime.operations import restore_worker_context
    cfg = dict(entry.cfg, accounts={'acct': {'active_profile': None}},
               instances={'main': {'serial': 'fake:1', 'account': 'acct'}})
    entry.monkeypatch.setattr(adbclient, '_HOST', ('127.0.0.7', 5047))
    accepted_identity = battles.guard_identity(cfg)
    handoff = _entry_handoff(entry, cfg)
    context = operations.context(entry.root, cfg, {'preset': 'farm'}, None)
    store = operations.ledger(entry.root)
    op, _ = store.accept('gems', context, {'preset': 'farm'}, 'accepted-collector')
    entry.monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    entry.monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    entry.monkeypatch.setattr(adbclient, '_HOST', ('127.0.0.1', 5037))   # the CLI default endpoint
    restore_worker_context(settings, 'main')
    restored = battles.guard_identity(settings.CONFIG)
    assert restored == accepted_identity and restored['account'] == 'acct'
    allowed = battles.require_runner_entry(entry.root, settings.CONFIG, 'main', scope='collection')
    assert allowed['handoff_id'] == handoff['id']
    settings.CONFIG['instances']['main']['account'] = None   # a context the handoff never named
    settings.CONFIG.pop('accounts')
    with pytest.raises(battles.SetupBattleUnresolved):
        battles.require_runner_entry(entry.root, settings.CONFIG, 'main', scope='collection')


# ------------------------------------------------------------ action lock
def test_action_lock_409_keeps_status_and_message_and_adds_a_code(dash):
    client = dash.app.test_client()
    assert dash._ACTION_LOCK.acquire(blocking=False)
    try:
        for url, body in (('/api/control', {'action': 'clear_flag'}),
                          ('/api/operations/any/actions', {'battle_id': 'b'})):
            response = client.post(url, json=body)
            assert response.status_code == 409
            assert response.get_json() == {'ok': False, 'code': 'action_lock', 'error': LOCK_MESSAGE}
            assert response.get_json()['error'].startswith('Another action is in progress')
    finally:
        dash._ACTION_LOCK.release()
    assert client.post('/api/control', json={'action': 'clear_flag'}).status_code == 200
    assert not dash._ACTION_LOCK.locked()


# ------------------------------------------- post-admission reply failures
def live(dash):
    cfg = dash.load_config()
    context = operations.context(dash.ROOT, cfg, {'preset': 'bp_farm'}, None)
    store = operations.ledger(dash.ROOT)
    operation, _ = store.accept('run', context, {})
    operation = store.transition(operation['id'], operation['revision'], 'running')
    preset = {'tier': 1, 'gather': {'ad_gems': True}, 'label': 'Farm'}
    envelope = {'preset': preset, 'controls': resolve(preset, cfg), 'config': cfg, 'profile': {}}
    store = battles.ledger(dash.ROOT)
    battle = store.create(operation['id'], context, envelope, generation='driver')
    battle = store.activate(battle['id'], 'driver', {'controls': ['routines.ad_gems.enabled'], 'actions': ['ad_gems']})
    return store, operation, battle


def _boom(error):
    def fail(*args, **kwargs):
        raise error
    return fail


@pytest.mark.parametrize('endpoint,extra', [
    ('live-controls', {'changes': {'routines.ad_gems.enabled': False}}),
    ('actions', {'action': 'ad_gems'}),
])
@pytest.mark.parametrize('target,error', [
    (battles, KeyError('battle row vanished')),
    (battles, ValueError('corrupt command row')),
    (Battles, KeyError('battle row vanished')),
])
def test_committed_command_is_never_reported_as_nothing_recorded(dash, monkeypatch, endpoint, extra, target, error):
    store, operation, battle = live(dash)
    client = dash.app.test_client()
    url = f"/api/operations/{operation['id']}/{endpoint}"
    body = dict(battle_id=battle['id'], generation='driver', expected_revision=0, idempotency_key='k-1', **extra)
    with monkeypatch.context() as fault:
        fault.setattr(target, 'public' if target is battles else 'get', _boom(error))
        response = client.post(url, json=body)
    assert response.status_code == 500
    reply = response.get_json()
    assert (reply['ok'], reply['code'], reply['outcome']) == (False, 'command_outcome_unknown', 'unknown')
    recorded = store.commands(battle['id'])
    assert [command['id'] for command in recorded] == [reply['command_id']]
    # the identical retry replays the recorded command: a definite answer
    replay = client.post(url, json=body)
    assert replay.status_code == 200 and replay.get_json()['command']['id'] == reply['command_id']
    assert len(store.commands(battle['id'])) == 1


def test_a_failed_success_diagnostic_is_not_a_refusal(dash, monkeypatch):
    store, operation, battle = live(dash)
    original = dash._diagnostic_event

    def diagnostic(kind, **fields):
        if kind == 'battle_command_accepted':
            raise ValueError('diagnostic sink rejected the record')
        return original(kind, **fields)
    monkeypatch.setattr(dash, '_diagnostic_event', diagnostic)
    response = dash.app.test_client().post(f"/api/operations/{operation['id']}/actions", json=dict(
        battle_id=battle['id'], generation='driver', expected_revision=0, idempotency_key='k-2', action='ad_gems'))
    assert response.status_code == 500   # unknown, never 400 "nothing recorded"
    assert len(store.commands(battle['id'])) == 1


def test_pre_admission_refusals_keep_their_contract_and_record_nothing(dash):
    store, operation, battle = live(dash)
    client = dash.app.test_client()
    body = dict(battle_id=battle['id'], generation='driver', expected_revision=0, idempotency_key='k-3', action='ad_gems')
    assert client.post('/api/operations/missing/actions', json=body).status_code == 404
    assert client.post(f"/api/operations/{operation['id']}/actions", json=dict(body, generation='')).status_code == 400
    assert client.post(f"/api/operations/{operation['id']}/actions", json=dict(body, action='unknown')).status_code == 400
    assert client.post(f"/api/operations/{operation['id']}/actions", json=dict(body, generation='old')).status_code == 409
    assert client.post(f"/api/operations/{operation['id']}/actions", json=dict(body, expected_revision=7)).status_code == 409
    assert store.commands(battle['id']) == []


# ------------------------------------- M1 review closure: F1-F4, F6, L1, L2
@pytest.mark.parametrize('stored', [['list'], 'text', 7, None], ids=['list', 'str', 'int', 'null'])
def test_a_handoff_whose_blocker_is_not_an_object_grants_nothing_and_never_500s(dash, launches, runner_rows, stored):
    op = calibrate(dash, unresolved)
    client = dash.app.test_client()
    record = client.post(HANDOFF, json=handoff_body(guard(dash))).get_json()['handoff']
    with operations.ledger(dash.ROOT)._db() as db:
        db.execute('UPDATE collection_handoffs SET body=? WHERE id=?', (json.dumps(dict(record, blocker=stored)), record['id']))
    assert blocked(collection(dash), 'setup_battle_unresolved', op['id'])
    response = client.post('/api/control', json=dict(GEMS, idempotency_key='tampered-blocker'))
    assert response.status_code == 409 and launches == []


@pytest.mark.parametrize('body,code', [('[]', 'setup_battle_unresolved'), ('not json', 'setup_guard_unavailable')],
                         ids=['not-an-object', 'not-json'])
def test_a_handoff_row_that_is_not_a_record_fails_closed(dash, launches, runner_rows, body, code):
    calibrate(dash, unresolved)
    client = dash.app.test_client()
    record = client.post(HANDOFF, json=handoff_body(guard(dash))).get_json()['handoff']
    with operations.ledger(dash.ROOT)._db() as db:
        db.execute('UPDATE collection_handoffs SET body=? WHERE id=?', (body, record['id']))
    assert blocked(collection(dash), code)
    assert client.post('/api/control', json=dict(GEMS, idempotency_key='tampered-row')).status_code == 409 and launches == []


def test_a_handoff_for_a_blocker_that_is_not_the_newest_setup_is_refused_not_stored_inert(dash, launches, runner_rows):
    old = calibrate(dash, unresolved)
    blocker = guard(dash)
    calibrate(dash, cleared, request={'phases': 'c'})
    assert blocker['operation_id'] == old['id'] and guard(dash)['operation_id'] == old['id']   # still decisive, but older
    response = dash.app.test_client().post(HANDOFF, json=handoff_body(blocker))
    assert response.status_code == 409 and response.get_json()['code'] == 'handoff_blocker_changed'
    assert handoff_count(dash.ROOT) == 0


def test_an_identical_handoff_retry_is_answered_while_the_collector_it_enabled_runs(dash, launches, runner_rows):
    calibrate(dash, unresolved)
    client = dash.app.test_client()
    first = client.post(HANDOFF, json=handoff_body(guard(dash))).get_json()
    collected = client.post('/api/control', json=dict(GEMS, idempotency_key='collect-after-handoff'))
    assert collected.status_code == 200
    again = client.post(HANDOFF, json=handoff_body(first['setup_battle_guard']))   # the reply was lost; the collector owns the device
    assert again.status_code == 200, again.get_json()
    assert again.get_json()['created'] is False and again.get_json()['handoff']['id'] == first['handoff']['id']
    assert again.get_json()['collection_guard']['handoff_id'] == first['handoff']['id']
    assert handoff_count(dash.ROOT) == 1


def test_a_handoff_replay_is_refused_once_the_blocker_is_no_longer_the_newest_setup(dash, launches, runner_rows):
    calibrate(dash, unresolved)
    client = dash.app.test_client()
    first = client.post(HANDOFF, json=handoff_body(guard(dash))).get_json()
    calibrate(dash, unresolved)                                                      # a newer setup battle
    again = client.post(HANDOFF, json=handoff_body(first['setup_battle_guard']))
    assert again.status_code == 409 and again.get_json()['code'] == 'handoff_blocker_changed'


def test_a_refused_handoff_creates_no_schema(dash, runner_rows):
    calibrate(dash, unresolved)
    client = dash.app.test_client()
    assert client.post(HANDOFF, json=handoff_body(guard(dash), attestation='same_battle')).status_code == 400
    assert client.post(HANDOFF, json=handoff_body(guard(dash), expected_context=dict(
        instance='main', account=None, serial='fake:9'))).status_code == 409
    with operations.ledger(dash.ROOT)._db() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not tables & {'collection_handoffs', 'harvest_choices', 'harvest_runtime', 'harvest_receipts', 'harvest_desires'}


def test_a_finished_setup_battle_cannot_be_handed_off(dash, runner_rows):
    calibrate(dash, finished)
    current = guard(dash)
    assert current['blocks_launch'] is False and current['evidence'] == 'setup_battle_finished'
    body = handoff_body(dict(operation_id=current['operation_id'], setup_battle_id=current['setup_battle_id'],
                             evidence_revision=current['evidence_revision']))
    response = dash.app.test_client().post(HANDOFF, json=body)
    assert response.status_code == 409 and response.get_json()['code'] == 'handoff_blocker_changed'
    assert handoff_count(dash.ROOT) == 0


def never_started(dash, *, error='launch_failed', step='accepted', worker=False, refs=None):
    """A calibrate operation as launch bookkeeping can leave it."""
    context = operations.context(dash.ROOT, dash.load_config(), {}, None)
    store = operations.ledger(dash.ROOT)
    op, _ = store.accept('calibrate', context, {'bootstrap': True})
    if worker:
        op = store.launched(op['id'], {'pid': 99, 'creation_time': 1.0})
    changes = dict(error={'code': error, 'message': 'x'})
    if step != 'accepted':
        op = store.transition(op['id'], op['revision'], 'running', step=step)
    if refs:
        changes['result_refs'] = refs
    return store.transition(op['id'], op['revision'], 'failed', **changes)


@pytest.mark.parametrize('error', ['launch_failed', 'launch_interrupted'])
def test_a_setup_that_never_reached_running_proves_nothing_and_older_evidence_stays_in_force(dash, launches, error):
    older = calibrate(dash, unresolved)
    never_started(dash, error=error)
    assert blocked(guard(dash), 'setup_battle_unresolved', older['id'])
    assert blocked(collection(dash), 'setup_battle_unresolved', older['id'])


def test_a_setup_that_never_reached_running_with_nothing_older_is_nothing_recorded(dash, launches):
    never_started(dash)
    assert guard(dash)['evidence'] == 'none_recorded' and collection(dash)['blocks_launch'] is False
    response = dash.app.test_client().post('/api/control', json=dict(GEMS, idempotency_key='after-never-started'))
    assert response.status_code == 200 and len(launches) == 1


@pytest.mark.parametrize('kwargs', [
    pytest.param(dict(worker=True), id='a-worker-existed'),
    pytest.param(dict(step='capture'), id='it-reached-running'),
    pytest.param(dict(error='worker_lost'), id='it-died-without-an-outcome'),
    pytest.param(dict(error='bootstrap_failed'), id='its-bootstrap-failed-after-launch'),
    pytest.param(dict(refs={'calibrate_state.json': {'path': 'x', 'revision': 'y'}}), id='it-references-evidence'),
])
def test_anything_else_without_sealed_evidence_stays_unreadable(dash, launches, kwargs):
    older = calibrate(dash, unresolved)
    newer = never_started(dash, **kwargs)
    assert blocked(guard(dash), 'setup_evidence_unreadable', newer['id']) and older['id'] != newer['id']


def drive(dash, monkeypatch):
    """A live battle whose driver has since exited, and the request that was recorded for it."""
    store, operation, battle = live(dash)
    client = dash.app.test_client()
    url = f"/api/operations/{operation['id']}/live-controls"
    body = dict(battle_id=battle['id'], generation='driver', expected_revision=0, idempotency_key='k-replay',
                changes={'routines.ad_gems.enabled': False})
    first = client.post(url, json=body)
    assert first.status_code == 200
    ledger = operations.ledger(dash.ROOT)
    ledger.transition(operation['id'], ledger.get(operation['id'])['revision'], 'succeeded')
    return client, url, body, first.get_json()['command']['id'], store, battle


def test_an_identical_command_is_answered_from_its_receipt_after_the_driver_ended(dash, monkeypatch):
    client, url, body, command_id, store, battle = drive(dash, monkeypatch)
    replay = client.post(url, json=body)
    assert replay.status_code == 200 and replay.get_json()['command']['id'] == command_id
    assert len(store.commands(battle['id'])) == 1


def test_only_an_identical_request_replays_after_the_driver_ended(dash, monkeypatch):
    client, url, body, command_id, store, battle = drive(dash, monkeypatch)
    changed = client.post(url, json=dict(body, changes={'routines.ad_gems.enabled': True}))
    assert changed.status_code == 409 and 'another command' in changed.get_json()['error']
    other_revision = client.post(url, json=dict(body, expected_revision=3))
    assert other_revision.status_code == 409
    elsewhere = client.post(url.replace(url.split('/')[3], 'another-operation'), json=body)
    assert elsewhere.status_code in (404, 409)
    fresh = client.post(url, json=dict(body, idempotency_key='k-new'))
    assert fresh.status_code == 409 and 'no longer active' in fresh.get_json()['error']
    assert len(store.commands(battle['id'])) == 1


def test_a_replay_whose_readback_fails_is_unknown_never_a_refusal(dash, monkeypatch):
    client, url, body, command_id, store, battle = drive(dash, monkeypatch)
    with monkeypatch.context() as fault:
        fault.setattr(battles, 'public', _boom(KeyError('battle row vanished')))
        response = client.post(url, json=body)
    assert response.status_code == 500 and response.get_json()['code'] == 'command_outcome_unknown'
    assert response.get_json()['command_id'] == command_id


# ------------------------------- real worker bootstrap: frozen context probes
def _admit_collector(dash, monkeypatch, key='e2e-collect'):
    spawn = EnvSpawn()
    monkeypatch.setattr(dash.subprocess, 'Popen', spawn)
    client = dash.app.test_client()
    reply = client.post(HANDOFF, json=handoff_body(guard(dash)))
    assert reply.status_code == 200, reply.get_json()
    accepted = client.post('/api/control', json=dict(GEMS, idempotency_key=key))
    assert accepted.status_code == 200, accepted.get_json()
    assert len(spawn.calls) == 1
    return reply.get_json()['handoff'], accepted.get_json()['operation'], spawn.calls[0]


def test_the_real_bootstrap_uses_the_frozen_accepted_account_not_the_edited_file(dash, runner_rows, worker_env, monkeypatch):
    """Same device either way; only the account differs. A guard that read the
    edited file would refuse, because the handoff names the accepted account."""
    import yaml
    calibrate(dash, unresolved)
    handoff, accepted, call = _admit_collector(dash, monkeypatch, key='frozen-account')
    cfg = dash.load_config()
    cfg['instances']['main']['account'] = 'acct_b'
    cfg['accounts'] = {'acct_b': {'active_profile': 'mine'}}
    Path(dash.CONFIG_PATH).write_text(yaml.safe_dump(cfg))
    assert battles.guard_for(dash.ROOT, cfg, scope='collection')['code'] == 'setup_battle_unresolved'   # the file view refuses
    seen = []
    real = battles.require_runner_entry

    def spy(root, cfg_, instance=None, **kwargs):
        result = real(root, cfg_, instance, **kwargs)
        seen.append((battles.guard_identity(cfg_, instance)['account'], kwargs.get('scope'), result['handoff_id'],
                     result['evidence']))
        return result
    monkeypatch.setattr(battles, 'require_runner_entry', spy)
    order, events, error = run_worker(dash.ROOT, monkeypatch, call)
    assert isinstance(error, WorkerReached), repr(error)
    assert order == ['runners', 'frame'] and seen == [(None, 'collection', handoff['id'], 'collection_handoff')]


def _insert_newer_calibrate(dash, state):
    """A calibrate row written behind admission's back (a second server, a race),
    sealed exactly as runtime.operations seals it."""
    import time
    import uuid
    ctx = operations.context(dash.ROOT, dash.load_config(), {}, None)
    new_id = str(uuid.uuid4())
    raw = json.dumps(state(new_id)).encode()
    path = Path(ctx['artifact_dir']) / 'operations' / new_id / 'calibrate_state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    now = time.time()
    body = dict(id=new_id, kind='calibrate', idempotency_key=new_id, revision=3, status='failed', context=ctx,
                request={}, worker=None, step='done', created_at=now, updated_at=now,
                result_refs={'calibrate_state.json': {'path': str(path), 'revision': hashlib.sha256(raw).hexdigest()}},
                error=None, recovery_action=None, progress={})
    with operations.ledger(dash.ROOT)._db() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)',
                   (new_id, ctx['device_key'], 'calibrate', new_id, 'probe', 3, 'failed', json.dumps(body, sort_keys=True)))
    return new_id


@pytest.mark.parametrize('state,decisive', [(unresolved, 'newer'), (cleared, 'older')],
                         ids=['newer-unresolved', 'newer-proving-nothing'])
def test_a_setup_that_lands_between_acceptance_and_entry_refuses_before_any_frame(
        dash, runner_rows, worker_env, monkeypatch, state, decisive):
    old = calibrate(dash, unresolved)
    handoff, accepted, call = _admit_collector(dash, monkeypatch)
    new_id = _insert_newer_calibrate(dash, state)
    order, events, error = run_worker(dash.ROOT, monkeypatch, call)
    assert isinstance(error, battles.SetupBattleUnresolved), repr(error)
    assert order == []   # no process-table read, no frame, no session
    [(fields, _)] = [(fields, ctx) for kind, fields, ctx in events if kind == 'setup_battle_guard_refused']
    assert fields['code'] == 'setup_battle_unresolved' and fields['handoff_id'] is None and fields['scope'] == 'collection'
    assert fields['setup_operation_id'] == (new_id if decisive == 'newer' else old['id']) and 'operation_id' not in fields
    final = operations.ledger(dash.ROOT).get(accepted['id'])
    assert final['status'] == 'failed' and final['error']['code'] == 'SetupBattleUnresolved'