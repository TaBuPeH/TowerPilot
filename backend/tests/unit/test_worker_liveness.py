"""Whether a recorded worker is still running is THREE-valued: alive, proven gone, or unknown.

Only a worker that is proven gone may be retired, and its battle record finished. A worker
this dashboard merely cannot inspect (access denied, a recorded identity that is malformed
or lacks its creation time) keeps its operation active, in a POST path and in the process
refresher alike, because the operating-system device lease can still be held by a process
that is really running. The lease itself is the final owner: the routes that need an idle
emulator also refuse while any process holds it, whatever the ledger says.

Real psutil identity of this test process, real temporary device lease, temporary ledger.
No device and no process is ever signalled."""

import paths as test_paths
import ast
import copy
import os
import time
from pathlib import Path

import psutil
import pytest

from control import operations
from control.operations import inspect_worker as real_inspect_worker   # imported before any fixture patches it
from control.operations import worker_alive as real_worker_alive
from harvest_support import Clock, decided, fake_inspect
from runtime import device_lease
from towerpilot_workflows import operations as ledger_ops
from towerpilot_workflows.battles import Battles

ME = dict(pid=os.getpid(), creation_time=psutil.Process().create_time())


def stand_in(*, init=None, created=ME['creation_time'], create_error=None, running=True):
    """psutil.Process as it answers for one inspected pid."""
    class Process:
        def __init__(self, pid=None):
            if init is not None:
                raise init
            self.pid = pid

        def create_time(self):
            if create_error is not None:
                raise create_error
            return created

        def is_running(self):
            return running
    return Process


# -------------------------------------------------------------------- the predicate
def test_a_worker_matching_a_running_process_is_alive():
    assert real_worker_alive(ME) is True


@pytest.mark.parametrize('worker', [None, {}], ids=['never-recorded', 'empty'])
def test_a_worker_that_was_never_recorded_is_not_alive_and_not_unknown(worker):
    assert real_worker_alive(worker) is False


@pytest.mark.parametrize('why,patch', [
    pytest.param('pid-reused', dict(created=ME['creation_time'] - 100), id='another-process-owns-the-pid'),
    pytest.param('exited', dict(running=False), id='exited'),
    pytest.param('no-such-process', dict(init=psutil.NoSuchProcess(ME['pid'])), id='no-such-process'),
    pytest.param('vanished-while-inspected', dict(create_error=psutil.NoSuchProcess(ME['pid'])), id='vanished-mid-inspection'),
    pytest.param('zombie', dict(create_error=psutil.ZombieProcess(ME['pid'])), id='zombie'),
])
def test_a_worker_proven_gone_is_false(monkeypatch, why, patch):
    monkeypatch.setattr(psutil, 'Process', stand_in(**patch))
    assert real_worker_alive(ME) is False


@pytest.mark.parametrize('patch', [
    pytest.param(dict(init=psutil.AccessDenied(ME['pid'])), id='access-denied-opening-the-process'),
    pytest.param(dict(create_error=psutil.AccessDenied(ME['pid'])), id='access-denied-reading-its-start-time'),
    pytest.param(dict(create_error=psutil.Error('any other psutil failure')), id='other-psutil-failure'),
    pytest.param(dict(create_error=OSError(5, 'access is denied')), id='os-error'),
])
def test_a_worker_that_cannot_be_inspected_is_unknown_never_gone(monkeypatch, patch):
    monkeypatch.setattr(psutil, 'Process', stand_in(**patch))
    assert real_worker_alive(ME) is None


MALFORMED = [
    pytest.param({'pid': ME['pid']}, id='no-creation-time'),
    pytest.param({'creation_time': ME['creation_time']}, id='no-pid'),
    pytest.param(dict(ME, creation_time='yesterday'), id='creation-time-text'),
    pytest.param(dict(ME, creation_time=None), id='creation-time-null'),
    pytest.param(dict(ME, pid=str(ME['pid'])), id='pid-text'),
    pytest.param(dict(ME, pid=True), id='pid-boolean'),
    pytest.param([ME['pid'], ME['creation_time']], id='a-list'),
    pytest.param('worker', id='a-string'),
]


@pytest.mark.parametrize('worker', MALFORMED)
def test_a_malformed_identity_is_unknown_never_gone(worker):
    assert real_worker_alive(worker) is None


# ----------------------------------------------------- the ledger retires only what is proven gone
@pytest.fixture
def ledger(dash):
    return operations.ledger(dash.ROOT), operations.context(dash.ROOT, dash.load_config(), {}, None)


def accepted_run(ledger, status='running', worker=ME):
    store, context = ledger
    op, _ = store.accept('run', context, {}, f'liveness-{status}-{time.time_ns()}')
    if status == 'accepted':
        return op
    op = store.transition(op['id'], op['revision'], 'running', worker=copy.deepcopy(worker))
    return op if status == 'running' else store.transition(op['id'], op['revision'], status)


@pytest.mark.parametrize('status', ['running', 'cancelling'])
@pytest.mark.parametrize('answer,expected', [(True, None), (None, None), (False, 'interrupted')])
def test_only_a_proven_gone_worker_retires_a_running_operation(ledger, status, answer, expected):
    op = accepted_run(ledger, status)
    after = ledger[0].reconcile(op['id'], lambda worker: answer)
    assert after['status'] == (expected or status)
    assert (after.get('error') or {}).get('code') == ('worker_lost' if expected else None)


@pytest.mark.parametrize('answer,expected', [(True, 'accepted'), (None, 'accepted'), (False, 'interrupted')])
def test_only_a_proven_gone_worker_retires_an_accepted_launch_after_the_grace(ledger, monkeypatch, answer, expected):
    op = accepted_run(ledger, 'accepted')
    monkeypatch.setattr(ledger_ops, 'time', Clock(time.time() + 120))   # past the 30 s launch grace
    assert ledger[0].reconcile(op['id'], lambda worker: answer)['status'] == expected


def test_a_lost_retirement_race_returns_the_newer_state(ledger):
    op = accepted_run(ledger)
    store = ledger[0]

    def alive(worker):
        store.transition(op['id'], op['revision'], 'succeeded')   # the worker (or another dashboard) settled it first
        return False
    assert store.reconcile(op['id'], alive)['status'] == 'succeeded'


# ----------------------------------------------------- the dashboard, with the real predicate
def scene(dash, monkeypatch, *, worker=ME, lease=True):
    """A running run whose driver battle is active, optionally with the device lease really held."""
    monkeypatch.setattr(operations, 'inspect_worker', real_inspect_worker)
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    cfg = dash.load_config()
    identity = operations.device_identity(cfg)
    held = device_lease.acquire(identity['serial'], identity['adb_host'], identity['adb_port']) if lease else None
    store = operations.ledger(dash.ROOT)
    op, _ = store.accept('run', operations.context(dash.ROOT, cfg, {}, None), {}, 'liveness-driver')
    op = store.transition(op['id'], op['revision'], 'running', worker=copy.deepcopy(worker))
    battles = Battles(store.path)
    battle = battles.create(op['id'], op['context'], {'preset': {}, 'controls': {}}, generation='driver', origin='adopted')
    battles.activate(battle['id'], 'driver', {'controls': [], 'actions': []})
    config = Path(dash.ROOT, 'config.yaml')
    return dict(store=store, op=op, battles=battles, battle=battle, held=held, config=config,
                before=config.read_bytes(), identity=identity)


def inaccessible(monkeypatch, how):
    if how == 'open':
        monkeypatch.setattr(psutil, 'Process', stand_in(init=psutil.AccessDenied(ME['pid'])))
    else:
        monkeypatch.setattr(psutil, 'Process', stand_in(create_error=psutil.AccessDenied(ME['pid'])))


UNKNOWN_SCENES = [
    pytest.param(dict(inspect='open'), id='access-denied'),
    pytest.param(dict(inspect='read'), id='access-denied-reading-start-time'),
    pytest.param(dict(worker={'pid': ME['pid']}), id='identity-without-creation-time'),
    pytest.param(dict(worker=dict(ME, creation_time='x')), id='identity-with-a-malformed-creation-time'),
    pytest.param(dict(worker=dict(ME, pid='1')), id='identity-with-a-malformed-pid'),
]


def arrange(dash, monkeypatch, spec, **extra):
    spec = dict(spec)
    inspect = spec.pop('inspect', None)
    world = scene(dash, monkeypatch, **spec, **extra)
    if inspect:
        inaccessible(monkeypatch, inspect)
    return world


def test_control_a_running_worker_with_a_held_lease_refuses_the_context_write(dash, launches, monkeypatch):
    world = scene(dash, monkeypatch)
    reply = dash.app.test_client().post('/api/profile-activate', json={'name': None})
    assert reply.status_code == 409 and reply.get_json()['code'] == 'device_owned'
    assert reply.get_json()['owner']['operation_id'] == world['op']['id']
    assert world['config'].read_bytes() == world['before'] and world['store'].get(world['op']['id'])['status'] == 'running'


@pytest.mark.parametrize('spec', UNKNOWN_SCENES)
def test_an_unknown_worker_stays_the_owner_and_the_context_write_is_refused(dash, launches, monkeypatch, spec):
    world = arrange(dash, monkeypatch, spec)
    reply = dash.app.test_client().post('/api/profile-activate', json={'name': None})
    body = reply.get_json()
    assert reply.status_code == 409 and body['code'] == 'device_owned', body
    assert body['owner']['operation_id'] == world['op']['id'] and body['recovery']['action'] == 'stop_operation'
    assert world['store'].get(world['op']['id'])['status'] == 'running'          # not retired
    assert world['config'].read_bytes() == world['before']                       # nothing was written
    assert device_lease.lease_state(*(world['identity'][key] for key in ('serial', 'adb_host', 'adb_port')))['owned']


@pytest.mark.parametrize('spec', UNKNOWN_SCENES)
def test_the_refresher_does_not_finish_the_battle_of_an_unknown_worker(dash, launches, monkeypatch, spec):
    world = arrange(dash, monkeypatch, spec)
    dash._harvest_tick()
    dash._harvest_tick()
    assert world['store'].get(world['op']['id'])['status'] == 'running'
    assert world['battles'].get(world['battle']['id'])['status'] == 'active'     # the live driver's record is untouched


@pytest.mark.parametrize('spec', UNKNOWN_SCENES)
def test_every_read_keeps_an_unknown_worker_as_the_owner_and_a_stop_never_calls_it_cancelled(dash, launches, monkeypatch, spec):
    world = arrange(dash, monkeypatch, spec, lease=False)
    client = dash.app.test_client()
    shown = client.get(f"/api/operations/{world['op']['id']}").get_json()['operation']
    assert shown['status'] == 'running'
    runtime = client.get('/api/runtime').get_json()['workers']
    assert [(w['operation_id'], w['alive']) for w in runtime] == [(world['op']['id'], None)]   # unknown, not False
    stop = client.post(f"/api/operations/{world['op']['id']}/cancel", json={'expected_revision': world['op']['revision']})
    assert stop.status_code == 200
    # A person's Stop on a worker nobody can verify, while nothing holds the device lease, RELEASES it (and says so);
    # it is never 'cancelled', which would claim the worker was stopped.
    assert stop.get_json()['operation']['status'] == 'interrupted'
    assert decided(stop.get_json()['stop']) == dict(state='released', reason=None, holder=None)


@pytest.mark.parametrize('spec', UNKNOWN_SCENES)
def test_a_stop_cannot_release_an_unknown_worker_while_the_lease_is_held(dash, launches, monkeypatch, spec):
    world = arrange(dash, monkeypatch, spec)                                      # the lease really is held
    stop = dash.app.test_client().post(f"/api/operations/{world['op']['id']}/cancel",
                                       json={'expected_revision': world['op']['revision']})
    assert stop.status_code == 200
    assert stop.get_json()['operation']['status'] == 'cancelling'
    reply = stop.get_json()['stop']
    assert (reply['state'], reply['reason']) == ('unverified', 'worker_not_verifiable_lease_held')
    assert reply['holder']['kind'] == 'process' and reply['holder']['lease']['held'] is True      # who holds the lease, for the person


def test_a_worker_proven_gone_is_retired_and_the_route_proceeds_when_nothing_holds_the_device(dash, launches, monkeypatch):
    world = scene(dash, monkeypatch, worker=dict(ME, creation_time=ME['creation_time'] - 100), lease=False)
    reply = dash.app.test_client().post('/api/profile-activate', json={'name': None})
    assert reply.status_code == 200
    assert world['store'].get(world['op']['id'])['status'] == 'interrupted'
    assert world['config'].read_bytes() != world['before']


def test_a_worker_proven_gone_is_retired_but_a_held_lease_still_refuses_the_context_write(dash, launches, monkeypatch):
    # The recorded worker is gone, yet something (a delegated child, another checkout's worker) holds
    # the OS lease: the ledger may retire the record, the lease is the final owner and still refuses.
    world = scene(dash, monkeypatch, worker=dict(ME, creation_time=ME['creation_time'] - 100))
    reply = dash.app.test_client().post('/api/profile-activate', json={'name': None})
    body = reply.get_json()
    assert reply.status_code == 409 and body['code'] == 'device_owned', body
    assert body['owner']['kind'] == 'process' and body['owner']['foreign'] is True and body['owner']['lease']['held'] is True
    assert body['recovery'] is None and body['retry'] == 'after_confirmed_stop'
    assert world['store'].get(world['op']['id'])['status'] == 'interrupted'      # retired: proven gone
    assert world['config'].read_bytes() == world['before']                       # but nothing changed under the lease


def test_the_refresher_finishes_the_battle_of_a_worker_proven_gone(dash, launches, monkeypatch):
    world = scene(dash, monkeypatch, worker=dict(ME, creation_time=ME['creation_time'] - 100), lease=False)
    dash._harvest_tick()
    assert world['store'].get(world['op']['id'])['status'] == 'interrupted'
    assert world['battles'].get(world['battle']['id'])['status'] == 'interrupted'


# ---------------------------------------------------- the harvest status never calls unknown "not running"
def test_the_status_calls_an_unverifiable_collector_unverified_not_stopped(dash, launches, monkeypatch):
    from harvest_http import enable, make_app, running_collector, stub_status
    app = make_app(dash, launches, monkeypatch)
    stub_status(dash, monkeypatch)
    enable(app)
    running_collector(app)
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: None))
    shown = app.client.get('/api/harvest').get_json()['harvest']
    assert (shown['state'], shown['code']) == ('unavailable', 'worker_unverified')
    assert shown['operation']['worker_alive'] is None and shown['operation']['status'] == 'running'
    dash._harvest_tick()
    assert [o['status'] for o in operations.ledger(dash.ROOT).active()] == ['running']   # not retired, no second collector
    assert len(app.spawned) == 1
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: False))
    assert app.client.get('/api/harvest').get_json()['harvest']['code'] == 'worker_not_running'


# ------------------------------------------------ nobody may read the answer by its truthiness
def truthiness_uses(source):
    """Calls of worker_alive / an injected alive() that are used as a plain boolean."""
    tree = ast.parse(source)

    def is_probe(node):
        # worker_alive(...) / module.worker_alive(...), and the injected alive(...) parameter of the
        # ledger and the harvest status (a bare name; adbclient.alive is a different, boolean question)
        func = getattr(node, 'func', None)
        probes = {'worker_alive', 'operation_liveness', 'inspect_worker', 'inspect_operation'}
        return (isinstance(node, ast.Call)
                and ((isinstance(func, ast.Name) and func.id in probes | {'alive', '_alive'})
                     or (isinstance(func, ast.Attribute) and func.attr in probes)))
    bad = []
    for node in ast.walk(tree):
        operands = []
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operands = [node.operand]
        elif isinstance(node, ast.BoolOp):
            operands = node.values
        elif isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            operands = [node.test]
        elif isinstance(node, ast.comprehension):
            operands = node.ifs
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'bool':
            operands = node.args
        bad += [operand.lineno for operand in operands if is_probe(operand)]
    return sorted(bad)


def test_the_detector_flags_the_idioms_that_would_read_unknown_as_dead():
    for line in ('if not worker_alive(w): pass', 'x = bool(alive(w))', 'y = a and alive(w)', 'z = 1 if worker_alive(w) else 2',
                 'w = [o for o in ops if operations.worker_alive(o)]', 'if inspect_worker(w): pass',
                 'if not operations.operation_liveness(op): pass', 'x = a or inspect_operation(op)'):
        assert truthiness_uses(line), line
    for line in ('if worker_alive(w) is False: pass', 'x = alive(w)', 'y = alive(w) is not True'):
        assert not truthiness_uses(line), line


def test_no_source_reads_a_liveness_answer_by_truthiness():
    root = test_paths.REPO
    files = [p for p in (root / 'backend').rglob('*.py') if not {'tests', 'logs'} & set(p.relative_to(root).parts)]
    offenders = {str(p.relative_to(root)): truthiness_uses(p.read_text(encoding='utf-8')) for p in files}
    assert str((root / 'backend' / 'orchestrator.py').relative_to(root)) in offenders
    assert str(test_paths.DASHBOARD.relative_to(root)) in offenders      # the dashboard is scanned by the backend walk
    assert {name: lines for name, lines in offenders.items() if lines} == {}
