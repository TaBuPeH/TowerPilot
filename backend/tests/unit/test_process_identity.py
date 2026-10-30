"""A recorded process is judged by (pid, creation time); it is READ through psutil and DESTROYED through a handle.

The operating system reuses pids. Reading (is it alive, who is its parent) uses the real psutil.Process over
a fake platform layer; destroying (a worker's stop, the kill route) goes through runtime.process_handle's
kernel handles, over a FakeKernel on the same fake process table (test_process_handle.py has the kernel-level
schedules). A pid can change hands at an exact moment in both layers. These tests also cover what a worker whose
identity can never be verified is allowed to do (be proven gone, or be released by a person while no process holds
the device lease) and when a stop may be called confirmed: only when the verified family exited AND the device
lease is free. No real process is inspected, signalled or killed."""
import copy
import math
import os
from pathlib import Path

import psutil
import pytest

from control import harvest, operations
from control.operations import inspect_worker as real_inspect_worker
from harvest_support import decided, fake_platform
from runtime import device_lease, process_handle, process_identity
from towerpilot_workflows.battles import Battles

PID, CREATED = 910001, 1_790_000_000.123456
WORKER = {'pid': PID, 'creation_time': CREATED}


@pytest.fixture
def fake_os(monkeypatch):
    return fake_platform(monkeypatch)


@pytest.fixture
def world(dash, fake_os, monkeypatch):
    """The dashboard's ledger with the REAL worker inspection (conftest fakes it by default)."""
    monkeypatch.setattr(operations, 'inspect_worker', real_inspect_worker)
    cfg = dash.load_config()
    identity = operations.device_identity(cfg)
    context = operations.context(dash.ROOT, cfg, {}, None)
    store = operations.ledger(dash.ROOT)
    return type('World', (), dict(dash=dash, os=fake_os, store=store, context=context, identity=identity,
                                  root=dash.ROOT, mp=monkeypatch, count=iter(range(1, 1000))))


def running(world, worker=WORKER, kind='gems', status='running'):
    op, _ = world.store.accept(kind, world.context, {'preset': 'bp_farm'}, f'identity-{next(world.count)}')
    op = world.store.transition(op['id'], op['revision'], 'running', worker=copy.deepcopy(worker))
    return op if status == 'running' else world.store.transition(op['id'], op['revision'], status)


def hold_lease(world):
    return device_lease.acquire(world.identity['serial'], world.identity['adb_host'], world.identity['adb_port'])


def stop(world, op, **kwargs):
    return operations.cancel(world.root, dict(operation_id=op['id'], expected_revision=op['revision']), **kwargs)


def settle(world, op):
    """stop_driver on an operation that is already cancelling: the stop in isolation, without the
    resolve that cancel() starts with (which would already retire a worker that is plainly gone)."""
    op = world.store.transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
    return operations.stop_driver(world.root, op['id'])


def reuse_during_stop(world, created):
    """The pid changes hands right after stop_driver verified the worker and before it stops it."""
    real_verify, real_stop = process_identity.open_verified, operations.stop_driver
    armed = {'on': False}

    def verify(pid, recorded):
        result = real_verify(pid, recorded)
        if armed['on'] and result[0] is True:
            armed['on'] = False
            world.os.swap(pid, created)
        return result

    def stop_driver(root, operation_id, **kwargs):
        armed['on'] = True
        return real_stop(root, operation_id, **kwargs)
    world.mp.setattr(process_identity, 'open_verified', verify)
    world.mp.setattr(operations, 'stop_driver', stop_driver)


# ================================================================ H2R3-01: verify and stop the SAME object
def test_control_a_stable_worker_is_the_process_that_is_stopped(world):
    world.os.start(PID, CREATED)
    op = stop(world, running(world))
    assert op['status'] == 'cancelled' and world.os.kills == [(PID, CREATED)]


def test_control_a_replacement_already_owning_the_pid_at_the_check_is_never_stopped(world):
    world.os.start(PID, CREATED + 100)
    op = settle(world, running(world))
    assert op['status'] == 'cancelled' and world.os.kills == []      # the recorded worker is gone
    assert world.os.table == {PID: CREATED + 100, **{os.getpid(): world.os.table[os.getpid()]}}


def test_a_pid_reused_between_the_check_and_the_stop_stops_nothing(world):
    world.os.start(PID, CREATED)
    reuse_during_stop(world, CREATED + 100)
    op = settle(world, running(world))
    assert world.os.kills == [], 'the replacement process was stopped'
    assert world.os.table[PID] == CREATED + 100                         # it is still running, untouched
    assert op['status'] == 'cancelled'                                  # the recorded worker really is gone


def test_harvest_off_cannot_stop_a_replacement_either(world):
    world.os.start(PID, CREATED)
    collector = running(world)
    reuse_during_stop(world, CREATED + 100)
    outcome = harvest.stop_collectors(world.root, world.identity)
    assert world.os.kills == [] and outcome['stopped'] == [collector['id']] and outcome['stopping'] == []


@pytest.mark.parametrize('offset', [0.009, 0.002, -0.009], ids=['9ms-later', '2ms-later', '9ms-earlier'])
def test_a_replacement_started_within_milliseconds_is_a_different_process(world, offset):
    world.os.start(PID, CREATED + offset)
    op = settle(world, running(world))
    assert world.os.kills == [] and op['status'] == 'cancelled'


@pytest.mark.parametrize('how', ['denied'])
def test_a_worker_that_cannot_be_inspected_is_never_stopped_and_stays_the_owner(world, how):
    world.os.start(PID, CREATED)
    world.os.denied.add(PID)
    op = settle(world, running(world))
    assert world.os.kills == [] and op['status'] == 'cancelling'
    assert operations.stop_state(op) == dict(state='unverified', reason='worker_not_verifiable_lease_free')


def test_a_family_member_that_cannot_be_signalled_leaves_the_stop_unconfirmed(world, monkeypatch):
    world.os.start(PID, CREATED)
    kernel = process_handle.kernel()
    monkeypatch.setattr(kernel, 'terminate', lambda handle: (_ for _ in ()).throw(process_handle.KernelError('TerminateProcess', 5)))
    op = settle(world, running(world))
    assert op['status'] == 'cancelling' and world.os.kills == []
    assert kernel.open_handles() == 0


def test_the_projection_never_labels_the_process_that_replaced_a_worker(world):
    world.os.start(PID, CREATED)
    op = running(world)
    rows = [{'pid': PID, 'runner': 'unrelated', 'cmdline': 'python something.py', 'created': CREATED}]
    labelled = operations.project_processes(world.root, rows)
    assert [(r['pid'], r.get('operation_id')) for r in labelled] == [(PID, op['id'])]       # control: the worker's own row
    real_verify = process_identity.open_verified

    def verify_then_swap(pid, recorded):
        result = real_verify(pid, recorded)
        world.os.swap(pid, CREATED + 100)                               # the pid changes hands after it was verified
        return result
    world.mp.setattr(process_identity, 'open_verified', verify_then_swap)
    # reconcile=False is the read path: the projection's OWN verification is then the only one, so the pid changes
    # hands exactly between that verification and the family read it is followed by.
    labelled = operations.project_processes(world.root, rows, reconcile=False)
    assert [(r['pid'], r.get('operation_id')) for r in labelled] == [(PID, None)]           # the replacement stays unlabelled


def test_the_projection_rechecks_the_identity_after_it_read_the_family(world):
    """The pid changes hands AFTER the family was read and before the row is labelled: the projection asks the
    verified object once more (is_running), so the replacement is still not labelled as the worker."""
    world.os.start(PID, CREATED)
    op = running(world)
    rows = [{'pid': PID, 'runner': 'unrelated', 'cmdline': 'python something.py', 'created': CREATED}]
    real_parents, armed = psutil.Process.parents, {'on': True}

    def parents_then_swap(self):
        family = real_parents(self)
        if armed['on']:
            world.os.swap(self.pid, CREATED + 100)
        return family
    world.mp.setattr(psutil.Process, 'parents', parents_then_swap)
    labelled = operations.project_processes(world.root, rows, reconcile=False)
    assert [(r['pid'], r.get('operation_id')) for r in labelled] == [(PID, None)]
    armed['on'] = False                                                         # the control: the same worker, nothing swapped
    world.os.swap(PID, CREATED)
    assert [(r['pid'], r.get('operation_id')) for r in operations.project_processes(world.root, rows, reconcile=False)] == [(PID, op['id'])]


# ---------------------------------------------------------------- the dashboard's kill route
def listed(created=CREATED, pid=PID):
    return {'pid': pid, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py', 'created': created}


SEEN = object()   # "the person saw the row the scan lists now"


def kill(world, row, seen=SEEN, pid=None, rows=None):
    """POST the kill the page would send: the pid AND the creation time its row carried when it was drawn
    (`seen`), judged against a FRESH scan (`row`, or `rows`)."""
    world.mp.setattr(world.dash, '_procs', lambda: [dict(r) for r in (rows if rows is not None else [row])])
    world.mp.setattr(world.dash, '_procs_refresh', lambda: None)
    body = {'action': 'kill', 'pid': row['pid'] if pid is None else pid}
    if seen is not None:
        body['created'] = row['created'] if seen is SEEN else seen
    response = world.dash.app.test_client().post('/api/control', json=body)
    return response.status_code, response.get_json()


def test_kill_stops_the_listed_runner_through_the_object_it_verified(world):
    world.os.start(PID, CREATED)
    assert kill(world, listed()) == (200, {'ok': True, 'gone': True})
    assert world.os.kills == [(PID, CREATED)]


def test_kill_never_stops_a_process_that_replaced_the_listed_runner(world):
    world.os.start(PID, CREATED + 100)                                  # reused after the scan, before the signal
    assert kill(world, listed()) == (200, {'ok': True, 'gone': True})
    assert world.os.kills == [] and PID in world.os.table


def test_kill_refuses_a_pid_that_passed_to_another_runner_after_the_page_drew_the_row(world):
    """The page drew pid PID as the runner started at CREATED. That runner exited, and a DIFFERENT runner now
    has the pid (the fresh scan lists it, with its own creation time). The click names the runner the person
    saw, so the other runner is never stopped, though it is a listed runner and the pid matches."""
    world.os.start(PID, CREATED + 100)
    status, body = kill(world, listed(created=CREATED + 100), seen=CREATED)
    assert status == 409 and body['code'] == 'process_identity_unverified'
    assert world.os.kills == [] and world.os.table[PID] == CREATED + 100
    # the control: the same click for the runner that is really listed now stops exactly that one
    assert kill(world, listed(created=CREATED + 100), seen=CREATED + 100) == (200, {'ok': True, 'gone': True})
    assert world.os.kills == [(PID, CREATED + 100)]


@pytest.mark.parametrize('seen', [None, float('nan'), float('inf'), -1.0, 0, True, 'x', CREATED + 0.5],
                         ids=['missing', 'nan', 'infinity', 'negative', 'zero', 'bool', 'text', 'half-a-second-off'])
def test_kill_refuses_a_request_that_does_not_carry_the_creation_time_the_scan_lists(world, seen):
    world.os.start(PID, CREATED)
    status, body = kill(world, listed(), seen=seen)
    assert status == 409 and body['code'] == 'process_identity_unverified'
    assert world.os.kills == [] and world.os.table[PID] == CREATED


def test_kill_of_a_pid_nothing_owns_any_more_is_gone_and_signals_nothing(world):
    status, body = kill(world, listed(), rows=[])                         # the fresh scan lists nothing; no process has the pid
    assert (status, body) == (200, {'ok': True, 'gone': True}) and world.os.kills == []


def test_kill_of_a_live_pid_that_is_no_listed_runner_is_a_coded_refusal(world):
    world.os.start(PID, CREATED)
    status, body = kill(world, listed(), rows=[])                         # something owns the pid, but no scan row does
    assert status == 400 and body['code'] == 'not_a_runner' and world.os.kills == []


@pytest.mark.parametrize('pid', [None, 0, -3, True, '910001', 1.5], ids=lambda v: f'pid-{v!r}')
def test_kill_refuses_a_pid_that_is_not_an_integer_process_id(world, pid):
    world.os.start(PID, CREATED)
    body = {'action': 'kill', 'created': CREATED}
    if pid is not None:
        body['pid'] = pid
    response = world.dash.app.test_client().post('/api/control', json=body)
    assert response.status_code == 400 and response.get_json()['code'] == 'invalid_request' and world.os.kills == []


@pytest.mark.parametrize('moment', ['open', 'created', 'wait', 'terminate'])
def test_kill_with_the_pid_reissued_after_any_kernel_call_signals_only_the_listed_runner(world, moment):
    """The runner exits and its pid is handed to another process at each point between the scan and the
    signal. The kill holds one handle from the first check to TerminateProcess, so the kernel refuses the
    reissue (it is pinned) and the only process that can be signalled is the runner that was listed."""
    world.os.start(PID, CREATED)
    attempts = []
    world.os.watch(lambda event, pid: attempts.append(world.os.reuse(PID, CREATED + 100)) if event == moment and not attempts else None)
    assert kill(world, listed()) == (200, {'ok': True, 'gone': True})
    assert attempts == [False] and world.os.kills == [(PID, CREATED)]


def test_kill_stops_only_the_runner_it_was_asked_about_not_its_children(world):
    world.os.start(PID, CREATED, ppid=100)
    world.os.start(PID + 1, CREATED + 1, ppid=PID)
    assert kill(world, listed()) == (200, {'ok': True, 'gone': True})
    assert world.os.kills == [(PID, CREATED)] and PID + 1 in world.os.table


def test_kill_reports_a_runner_the_kernel_refuses_to_terminate(world, monkeypatch):
    world.os.start(PID, CREATED)
    monkeypatch.setattr(process_handle.kernel(), 'terminate', lambda handle: (_ for _ in ()).throw(process_handle.KernelError('TerminateProcess', 5)))
    status, body = kill(world, listed())
    assert (status, body['code']) == (500, 'process_access_denied') and world.os.kills == []
    assert process_handle.kernel().open_handles() == 0


def test_kill_reports_a_runner_that_would_not_exit(world):
    world.os.start(PID, CREATED)
    world.os.lingering = True
    status, body = kill(world, listed())
    assert status == 500 and 'would not die' in body['error'] and world.os.kills == [(PID, CREATED)]


@pytest.mark.parametrize('created', [None, float('nan'), -1.0, 'x'], ids=['no-identity', 'nan', 'negative', 'text'])
def test_kill_refuses_a_row_whose_identity_cannot_be_proven(world, created):
    world.os.start(PID, CREATED)
    status, body = kill(world, listed(created=created), seen=CREATED)
    assert status == 409 and body['code'] == 'process_identity_unverified'
    assert world.os.kills == []


def test_kill_refuses_a_runner_that_cannot_be_inspected(world):
    world.os.start(PID, CREATED)
    world.os.denied.add(PID)
    status, body = kill(world, listed())
    assert status == 409 and body['code'] == 'process_identity_unverified' and world.os.kills == []


# ================================================================ H2R3-02: a worker is never wedged for good
BAD_CREATED = [
    pytest.param(None, id='creation-time-null'),
    pytest.param('1790000000.1', id='creation-time-text'),
    pytest.param(float('nan'), id='creation-time-nan'),
    pytest.param(float('inf'), id='creation-time-infinity'),
    pytest.param(-5.0, id='creation-time-negative'),
    pytest.param(0, id='creation-time-zero'),
    pytest.param(True, id='creation-time-bool'),
]


def malformed(created):
    return {'pid': PID, 'creation_time': created}


def stored_without_validation(world, op, worker):
    """What an older writer or a damaged ledger could have left behind (the writer now refuses NaN)."""
    import json
    import sqlite3
    body = dict(world.store.get(op['id']), worker=worker)
    with sqlite3.connect(world.store.path) as db:
        db.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(body), op['id']))


@pytest.mark.parametrize('created', BAD_CREATED)
def test_a_record_without_a_usable_creation_time_is_retired_once_its_pid_is_absent_and_the_lease_is_free(world, created):
    op = running(world, WORKER)
    stored_without_validation(world, op, malformed(created))
    assert operations.resolve(world.root, op['id'])['status'] == 'interrupted'      # no process has the pid; nobody holds the lease
    assert world.store.get(op['id'])['error']['code'] == 'worker_lost'


@pytest.mark.parametrize('created', BAD_CREATED)
def test_the_refresher_retires_it_too_and_finishes_its_battle_record_only_then(world, created):
    op = running(world, WORKER)
    battles = Battles(world.store.path)
    battle = battles.create(op['id'], op['context'], {'preset': {}, 'controls': {}}, generation='g', origin='adopted')
    battles.activate(battle['id'], 'g', {'controls': [], 'actions': []})
    stored_without_validation(world, op, malformed(created))
    world.os.start(PID, CREATED)                                                    # the pid exists: unknown, so nothing is retired
    world.mp.setattr(world.dash, '_procs_cached', lambda: [])
    world.dash._harvest_tick()
    assert world.store.get(op['id'])['status'] == 'running' and battles.get(battle['id'])['status'] == 'active'
    world.os.exit(PID)
    world.dash._harvest_tick()
    assert world.store.get(op['id'])['status'] == 'interrupted' and battles.get(battle['id'])['status'] == 'interrupted'


@pytest.mark.parametrize('created', BAD_CREATED)
def test_a_held_or_unreadable_lease_keeps_a_record_without_a_creation_time_as_the_owner(world, created, monkeypatch):
    op = running(world, WORKER)
    stored_without_validation(world, op, malformed(created))
    held = hold_lease(world)
    assert operations.resolve(world.root, op['id'])['status'] == 'running'          # pid absent, but something drives the emulator
    held.release()
    with monkeypatch.context() as unreadable:
        unreadable.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(OSError('cannot probe')))
        assert operations.resolve(world.root, op['id'])['status'] == 'running'      # the lease cannot be read: not free


@pytest.mark.parametrize('created', BAD_CREATED)
def test_a_pid_that_exists_with_an_unusable_record_is_never_retired_automatically(world, created):
    op = running(world, WORKER)
    stored_without_validation(world, op, malformed(created))
    world.os.start(PID, CREATED)
    assert operations.resolve(world.root, op['id'])['status'] == 'running'          # lease free, but a process has that pid
    assert operations.stop_driver(world.root, stop(world, world.store.get(op['id']))['id'])['status'] == 'cancelling'
    assert world.os.kills == []


@pytest.mark.parametrize('pid', [0, -4, True, False, '910001', None, 1.5, 4.0], ids=lambda v: f'pid-{v!r}')
def test_an_unusable_pid_cannot_be_proven_absent_so_it_is_not_retired_automatically(world, pid):
    op = running(world, WORKER)
    stored_without_validation(world, op, {'pid': pid, 'creation_time': CREATED})
    assert operations.resolve(world.root, op['id'])['status'] == 'running'
    assert world.os.kills == []


def test_a_pid_missing_from_the_record_is_unknown_too(world):
    op = running(world, WORKER)
    stored_without_validation(world, op, {'creation_time': CREATED})
    assert operations.resolve(world.root, op['id'])['status'] == 'running'


# ---- a person's way out (the Stop button): only an UNKNOWN worker, only while the lease is free
UNVERIFIABLE = [
    pytest.param(lambda w: (w.os.start(PID, CREATED), stored_without_validation(w, w.op, malformed(None))), id='creation-time-missing'),
    pytest.param(lambda w: stored_without_validation(w, w.op, {'pid': 0, 'creation_time': CREATED}), id='pid-unusable'),
    pytest.param(lambda w: (w.os.start(PID, CREATED), w.os.denied.add(PID)), id='access-denied'),
]


@pytest.mark.parametrize('arrange', UNVERIFIABLE)
def test_a_persons_stop_releases_an_unverifiable_worker_while_nothing_holds_the_lease(world, arrange):
    world.op = running(world)
    arrange(world)
    assert operations.resolve(world.root, world.op['id'])['status'] == 'running'    # automatic recovery has nothing to go on
    settled = stop(world, world.store.get(world.op['id']), release_unverified=True)
    assert settled['status'] == 'interrupted' and settled['error']['code'] == 'worker_released'
    assert operations.stop_state(settled) == dict(state='released', reason=None)
    assert world.os.kills == []                                                     # nothing was signalled


@pytest.mark.parametrize('arrange', UNVERIFIABLE)
def test_the_release_is_refused_while_a_process_holds_the_device_lease(world, arrange):
    world.op = running(world)
    arrange(world)
    held = hold_lease(world)
    settled = stop(world, world.store.get(world.op['id']), release_unverified=True)
    assert settled['status'] == 'cancelling'
    assert operations.stop_state(settled) == dict(state='unverified', reason='worker_not_verifiable_lease_held')
    held.release()
    again = stop(world, settled, release_unverified=True)                           # the same Stop, once the lease is free
    assert again['status'] == 'interrupted' and again['error']['code'] == 'worker_released'


@pytest.mark.parametrize('arrange', UNVERIFIABLE)
def test_the_release_is_refused_when_the_lease_cannot_be_read(world, arrange, monkeypatch):
    world.op = running(world)
    arrange(world)
    monkeypatch.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    settled = stop(world, world.store.get(world.op['id']), release_unverified=True)
    assert settled['status'] == 'cancelling'
    assert operations.stop_state(settled)['reason'] == 'worker_not_verifiable_lease_unreadable'


@pytest.mark.parametrize('arrange', UNVERIFIABLE)
def test_only_a_persons_stop_releases_never_the_harvest_lifecycle(world, arrange):
    world.op = running(world)
    arrange(world)
    outcome = harvest.stop_collectors(world.root, world.identity)
    assert outcome['stopped'] == [] and outcome['stopping'] == [world.op['id']]
    assert world.store.get(world.op['id'])['status'] == 'cancelling'


def test_a_worker_that_is_verifiably_running_is_stopped_never_released(world):
    world.os.start(PID, CREATED)
    settled = stop(world, running(world), release_unverified=True)
    assert settled['status'] == 'cancelled' and world.os.kills == [(PID, CREATED)]   # signalled through the verified object


def test_a_repeated_stop_retries_the_stop_of_a_worker_that_did_not_exit(world):
    world.os.start(PID, CREATED)
    world.os.lingering = True                                                       # the signalled process does not exit
    first = stop(world, running(world), release_unverified=True)
    assert first['status'] == 'cancelling' and world.os.kills == [(PID, CREATED)]
    world.os.lingering = False                                                      # it will honour the next signal
    second = stop(world, first, release_unverified=True)
    assert second['status'] == 'cancelled' and world.os.kills == [(PID, CREATED)] * 2


def test_the_stop_route_answers_how_the_stop_stands(world):
    client = world.dash.app.test_client()
    world.os.start(PID, CREATED)
    world.os.denied.add(PID)
    op = running(world)
    held = hold_lease(world)
    body = client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': op['revision']}).get_json()
    assert body['ok'] is True and body['operation']['status'] == 'cancelling'
    holder = harvest.lease_holder(world.identity)                                  # the owner body of the shared device_owned lease refusal
    assert holder['lease'] == dict(held=True, delegated=False, input_gate='enforced', holders=[dict(pid=os.getpid(), role='primary')])
    assert decided(body['stop']) == dict(state='unverified', reason='worker_not_verifiable_lease_held', holder=holder)
    assert holder['kind'] == 'process' and holder['foreign'] is True and holder['recovery'] is None
    held.release()
    current = world.store.get(op['id'])
    body = client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': current['revision']}).get_json()
    assert body['operation']['status'] == 'interrupted' and decided(body['stop']) == dict(state='released', reason=None, holder=None)


def test_the_stop_reply_names_a_holder_only_while_the_lease_is_the_reason(world, monkeypatch):
    client = world.dash.app.test_client()
    world.os.start(PID, CREATED)
    world.os.denied.add(PID)
    op = running(world)
    with monkeypatch.context() as unreadable:
        unreadable.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
        body = client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': op['revision']}).get_json()
    assert decided(body['stop']) == dict(state='unverified', reason='worker_not_verifiable_lease_unreadable', holder=None)
    current = world.store.get(op['id'])
    body = client.post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': current['revision']}).get_json()
    assert body['stop']['holder'] is None and body['stop']['state'] == 'released'           # lease free: a person's Stop releases it


# ================================================================ H2R3-04: a malformed identity decides nothing
@pytest.mark.parametrize('worker', [None, {}], ids=['null', 'empty-object'])
def test_only_the_two_sentinels_mean_no_worker_was_ever_recorded(worker):
    assert real_inspect_worker(worker) == (False, None)


@pytest.mark.parametrize('worker', [False, 0, '', [], (), 0.0, 'worker', [PID, CREATED], True, 7],
                         ids=lambda v: f'worker-{v!r}')
def test_every_other_non_object_is_malformed_not_gone(worker):
    assert real_inspect_worker(worker) == (None, None)


@pytest.mark.parametrize('pid', [0, -1, True, False, '910001', None, 1.5], ids=lambda v: f'pid-{v!r}')
def test_an_unusable_pid_is_unknown(fake_os, pid):
    fake_os.start(PID, CREATED)
    assert real_inspect_worker({'pid': pid, 'creation_time': CREATED}) == (None, None)


@pytest.mark.parametrize('created', [pytest.param(c.values[0], id=c.id) for c in BAD_CREATED] + [pytest.param(math.nan, id='nan-again')])
def test_an_unusable_creation_time_is_unknown_and_never_matches_a_running_process(fake_os, created):
    fake_os.start(PID, CREATED)
    assert real_inspect_worker({'pid': PID, 'creation_time': created}) == (None, None)
    assert fake_os.kills == []


def test_a_finite_matching_identity_is_still_alive_and_a_different_one_gone(fake_os):
    fake_os.start(PID, CREATED)
    answer, process = real_inspect_worker(WORKER)
    assert answer is True and isinstance(process, psutil.Process) and process.pid == PID
    assert real_inspect_worker({'pid': PID, 'creation_time': CREATED + 1}) == (False, None)
    assert real_inspect_worker({'pid': PID + 1, 'creation_time': CREATED}) == (False, None)


def test_the_ledger_never_stores_a_creation_time_that_is_not_a_finite_number(world):
    op, _ = world.store.accept('gems', world.context, {}, 'strict-json')
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            world.store.transition(op['id'], op['revision'], 'running', worker={'pid': PID, 'creation_time': bad})
    assert world.store.get(op['id'])['status'] == 'accepted' and world.store.get(op['id'])['worker'] is None
    with pytest.raises(ValueError):
        world.store.launched(op['id'], {'pid': PID, 'creation_time': math.nan})
    assert world.store.get(op['id'])['worker'] is None


def test_a_row_that_an_older_writer_stored_with_nan_is_unknown_not_alive(world):
    world.os.start(PID, CREATED)
    op = running(world)
    stored_without_validation(world, op, {'pid': PID, 'creation_time': math.nan})
    assert operations.worker_alive(world.store.get(op['id'])['worker']) is None
    assert operations.resolve(world.root, op['id'])['status'] == 'running'
    assert world.os.kills == []


def test_the_pid_probe_is_only_ever_true_for_a_usable_pid_nobody_has(fake_os):
    fake_os.start(PID, CREATED)
    fake_os.denied.add(PID + 2)
    assert process_identity.pid_absent(PID + 1) is True
    for not_absent in (PID, PID + 2, 0, -1, True, '5', None):
        assert process_identity.pid_absent(not_absent) is False


def test_a_pid_that_cannot_be_inspected_is_not_absent_so_a_bad_record_on_it_is_never_retired(world, monkeypatch):
    """psutil.Process() normally swallows an access-denied creation time, but any psutil/OS failure while opening
    the pid is 'cannot tell', never 'nobody has it'."""
    op = running(world, WORKER)
    stored_without_validation(world, op, malformed(None))

    def refuse(pid, *args, **kwargs):
        raise psutil.AccessDenied(pid)
    with monkeypatch.context() as denied:
        denied.setattr(psutil, 'Process', refuse)
        assert process_identity.pid_absent(PID) is False
        assert operations.resolve(world.root, op['id'])['status'] == 'running'
    assert world.os.kills == []


# ================================================================ H2R4-03: a stop is confirmed only when the device is released
CHILD, GRAND, ORPHAN = PID + 1, PID + 2, PID + 3


def family(world):
    """worker -> child -> grandchild, as the kernel's parent table shows them."""
    world.os.start(PID, CREATED, ppid=100)
    world.os.start(CHILD, CREATED + 1, ppid=PID)
    world.os.start(GRAND, CREATED + 2, ppid=CHILD)


def lease_dies_with(world, lease, pid):
    """The lease is held by the process `pid`: the OS lock goes away when that process is terminated."""
    world.os.watch(lambda event, who: lease.release() if event == 'terminate' and who == pid else None)


def cancel_route(world, op):
    return world.dash.app.test_client().post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': op['revision']}).get_json()


def test_control_a_lease_held_by_a_reachable_descendant_is_released_by_the_stop(world):
    family(world)
    lease = hold_lease(world)
    lease_dies_with(world, lease, GRAND)               # the delegated driver is the grandchild
    op = running(world)
    reply = cancel_route(world, op)
    assert reply['operation']['status'] == 'cancelled' and decided(reply['stop']) == dict(state='stopped', reason=None, holder=None)
    assert [pid for pid, _ in world.os.kills] == [GRAND, CHILD, PID]
    assert operations.device_lease_free(world.store.get(op['id'])['context']) is True


def test_a_survivor_outside_the_verified_family_keeps_the_stop_unconfirmed_and_is_named(world):
    """Astra's H2R4-03: the family walk cannot reach a descendant (its parent is gone), it keeps the real lease,
    and the old code called that `cancelled` / `stopped`. The worker is terminated, the lease is still held, so the
    operation stays the owner and the reply says who still holds the device."""
    world.os.start(PID, CREATED, ppid=100)
    world.os.start(ORPHAN, CREATED + 2, ppid=9999)     # its parent no longer exists
    lease = hold_lease(world)                           # the survivor's lock
    op = running(world)
    reply = cancel_route(world, op)
    assert world.os.kills == [(PID, CREATED)] and ORPHAN in world.os.table
    assert reply['operation']['status'] == 'cancelling'
    assert (reply['stop']['state'], reply['stop']['reason']) == ('unverified', 'lease_held_after_stop')
    holder = reply['stop']['holder']
    assert holder['lease']['held'] is True and holder['lease']['holders'] == [dict(pid=os.getpid(), role='primary')]
    assert operations.stop_state(world.store.get(op['id']))['reason'] == 'lease_held_after_stop'
    lease.release()


def test_a_worker_that_is_gone_while_its_lease_is_held_is_not_retired_or_finished_until_the_lease_is_free(world):
    world.os.start(PID, CREATED, ppid=100)
    op = running(world)
    battles = Battles(world.store.path)
    battle = battles.create(op['id'], op['context'], {'preset': {}, 'controls': {}}, generation='g', origin='adopted')
    battles.activate(battle['id'], 'g', {'controls': [], 'actions': []})
    world.mp.setattr(world.dash, '_procs_cached', lambda: [])
    lease = hold_lease(world)
    cancelled = cancel_route(world, op)
    assert cancelled['operation']['status'] == 'cancelling'
    world.os.exit(PID)
    for _ in range(2):                                                         # the refresher, and a POST path
        world.dash._harvest_tick()
        assert operations.resolve(world.root, op['id'])['status'] == 'cancelling'
    assert world.store.get(op['id'])['status'] == 'cancelling' and battles.get(battle['id'])['status'] == 'active'
    lease.release()
    world.dash._harvest_tick()
    assert world.store.get(op['id'])['status'] == 'interrupted' and battles.get(battle['id'])['status'] == 'interrupted'


def test_once_the_survivor_releases_the_lease_the_open_stop_settles_and_is_never_left_cancelling(world):
    world.os.start(PID, CREATED, ppid=100)
    lease = hold_lease(world)
    op = running(world)
    first = cancel_route(world, op)
    assert first['operation']['status'] == 'cancelling' and first['stop']['reason'] == 'lease_held_after_stop'
    lease.release()
    settled = operations.resolve(world.root, op['id'])                         # the next POST path or refresher tick
    assert settled['status'] == 'interrupted' and settled['error']['code'] == 'worker_lost'
    assert operations.stop_state(settled) == dict(state='ended', reason=None)
    again = world.dash.app.test_client().post(f"/api/operations/{op['id']}/cancel", json={'expected_revision': first['operation']['revision']})
    assert again.status_code == 409                                              # it is over: a stale Stop is refused, not repeated


def test_a_stop_repeated_while_the_survivor_still_holds_the_lease_stays_unconfirmed_and_signals_nothing_new(world):
    world.os.start(PID, CREATED, ppid=100)
    lease = hold_lease(world)
    first = cancel_route(world, running(world))
    again = cancel_route(world, first['operation'])
    assert again['operation']['status'] == 'cancelling' and again['stop']['reason'] == 'lease_held_after_stop'
    assert world.os.kills == [(PID, CREATED)]                                    # the worker was signalled once; nothing else exists to signal
    lease.release()


def test_an_unreadable_lease_after_the_stop_is_never_read_as_free(world, monkeypatch):
    world.os.start(PID, CREATED, ppid=100)
    op = running(world)
    monkeypatch.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    reply = cancel_route(world, op)
    assert reply['operation']['status'] == 'cancelling'
    assert decided(reply['stop']) == dict(state='unverified', reason='lease_unreadable_after_stop', holder=None)


def test_an_operation_that_never_had_a_worker_is_not_held_open_by_somebody_elses_lease(world):
    held = hold_lease(world)
    op, _ = world.store.accept('gems', world.context, {'preset': 'bp_farm'}, 'never-launched')
    reply = cancel_route(world, op)
    assert reply['operation']['status'] == 'cancelled' and reply['stop']['state'] == 'stopped'
    held.release()


def test_without_an_adb_serial_there_is_no_lease_to_wait_for(world):
    world.os.start(PID, CREATED, ppid=100)
    op, _ = world.store.accept('gems', dict(world.context, serial=None), {'preset': 'bp_farm'}, 'no-serial')
    op = world.store.transition(op['id'], op['revision'], 'running', worker=dict(WORKER))
    assert operations.device_unreleased(op) is False
    assert stop(world, op)['status'] == 'cancelled'


def test_stop_state_calls_a_finished_operation_ended_and_only_a_live_one_active(world):
    done = {'accepted': 'active', 'running': 'active', 'applying': 'active', 'failed': 'ended', 'succeeded': 'ended',
            'interrupted': 'ended', 'cancelled': 'stopped'}
    for status, state in done.items():
        assert operations.stop_state(dict(status=status, context={}, worker=None, error=None))['state'] == state, status


# ================================================================ H2R4-04: a projected ancestor must still be the process the scan listed
LAUNCHER = PID + 10


def launched_worker(world):
    world.os.start(LAUNCHER, CREATED - 5, ppid=100)
    world.os.start(PID, CREATED, ppid=LAUNCHER)
    return running(world)


def rows_of_launcher(created):
    return [{'pid': LAUNCHER, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py', 'created': created}]


def labelled(world, rows):
    return [(r['pid'], r.get('operation_id')) for r in operations.project_processes(world.root, rows, reconcile=False)]


def test_control_the_launcher_row_that_the_scan_pinned_is_labelled_with_the_operation(world):
    op = launched_worker(world)
    assert labelled(world, rows_of_launcher(CREATED - 5)) == [(LAUNCHER, op['id'])]


def test_an_ancestor_pid_reissued_after_the_ancestry_was_read_does_not_inherit_the_operation(world):
    op = launched_worker(world)
    real_parents, armed = psutil.Process.parents, {'on': True}

    def parents_then_swap(self):
        family_ = real_parents(self)
        if armed['on']:
            world.os.swap(LAUNCHER, CREATED + 50, ppid=100)               # the launcher exits; an unrelated process takes its pid
        return family_
    world.mp.setattr(psutil.Process, 'parents', parents_then_swap)
    assert labelled(world, rows_of_launcher(CREATED - 5)) == [(LAUNCHER, None), (PID, op['id'])]     # the worker keeps its own row


def test_an_ancestor_row_without_the_creation_time_the_scan_pinned_is_never_labelled(world):
    op = launched_worker(world)
    for created in (None, CREATED - 4, 'x', float('nan')):
        assert labelled(world, rows_of_launcher(created)) == [(LAUNCHER, None), (PID, op['id'])]


def worker_row(created, **extra):
    return dict({'pid': PID, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py', 'created': created}, **extra)


def test_control_the_workers_own_row_is_labelled_when_its_creation_time_is_the_workers(world):
    op = running(world)
    world.os.start(PID, CREATED)
    assert labelled(world, [worker_row(CREATED)]) == [(PID, op['id'])]


@pytest.mark.parametrize('created', [CREATED + 100, CREATED - 100, CREATED + 0.5, None, 'x', float('nan'), float('inf'), True, 10 ** 400],
                         ids=['later', 'earlier', 'half-a-second', 'not-pinned', 'text', 'nan', 'infinity', 'bool', 'huge'])
def test_a_scan_row_at_the_workers_pid_with_another_creation_time_is_not_the_worker_and_stays_a_separate_unlabelled_row(world, created):
    """The scan is older than the verification. A row at the worker's pid that the scan pinned to a different start
    (or could not pin at all) is whatever owned that pid then, not the worker: it is kept as its own row without the
    operation, and the verified worker gets a row of its own."""
    op = running(world)
    world.os.start(PID, CREATED)
    stale = worker_row(created, cmdline='python something_else.py')
    shown = operations.project_processes(world.root, [stale], reconcile=False)
    assert [(r['pid'], r.get('operation_id'), r['cmdline']) for r in shown] == [(PID, None, 'python something_else.py'), (PID, op['id'], '')]
    assert shown[0].get('operation') is None and shown[1]['operation']['id'] == op['id']


def test_a_scan_row_missing_the_creation_key_entirely_is_not_attached_either(world):
    op = running(world)
    world.os.start(PID, CREATED)
    row = worker_row(CREATED)
    del row['created']
    assert labelled(world, [row]) == [(PID, None), (PID, op['id'])]


def test_only_the_row_whose_creation_time_matches_gets_the_operation_when_two_rows_share_the_pid(world):
    op = running(world)
    world.os.start(PID, CREATED)
    stale, current = worker_row(CREATED + 100, cmdline='stale'), worker_row(CREATED, cmdline='current')
    shown = operations.project_processes(world.root, [stale, current], reconcile=False)
    assert [(r['cmdline'], r.get('operation_id')) for r in shown] == [('stale', None), ('current', op['id'])]


# ================================================================ H2R4-05: an identity check never raises, whatever the number
HUGE = [10 ** 400, -10 ** 400, 10 ** 309, 2 ** 1024, -(2 ** 1024), 1e308, -1e308, 253402300800.0, 253402300800, 10 ** 18]
EDGE_OK = [5e-324, 1.0, 1790000000.123456, 253402300799.0, 253402300799]


@pytest.mark.parametrize('created', HUGE, ids=lambda v: f'created-{str(v)[:14]}')
def test_a_creation_time_too_big_for_a_float_is_malformed_not_an_exception(created):
    assert process_identity.usable_created(created) is False
    assert process_identity.usable(PID, created) is False
    assert process_identity.same_created(created, CREATED) is False and process_identity.same_created(CREATED, created) is False
    assert process_identity.open_verified(PID, created) == (None, None)
    assert process_identity.matches(PID, created) is False
    assert real_inspect_worker({'pid': PID, 'creation_time': created}) == (None, None)


@pytest.mark.parametrize('created', EDGE_OK, ids=lambda v: f'created-{v}')
def test_the_edges_of_the_usable_range_are_usable(created):
    assert process_identity.usable_created(created) is True


@pytest.mark.parametrize('pid', [10 ** 400, -10 ** 400, 2 ** 32, 2 ** 64, 0, -1], ids=lambda v: f'pid-{str(v)[:12]}')
def test_a_pid_outside_the_range_of_a_process_id_is_malformed_not_an_exception(pid):
    assert process_identity.usable_pid(pid) is False
    assert process_identity.pid_absent(pid) is False
    assert process_identity.open_verified(pid, CREATED) == (None, None)
    assert process_identity.usable_pid(2 ** 32 - 1) is True


@pytest.mark.parametrize('created', [10 ** 400, -10 ** 400], ids=['huge', 'huge-negative'])
def test_a_stored_worker_with_a_huge_creation_time_stays_unknown_through_reconciliation(world, created):
    world.os.start(PID, CREATED)
    op = running(world)
    stored_without_validation(world, op, {'pid': PID, 'creation_time': created})
    assert operations.worker_alive(world.store.get(op['id'])['worker']) is None
    assert operations.resolve(world.root, op['id'])['status'] == 'running'                  # not retired, no exception
    world.dash._harvest_tick()
    assert world.store.get(op['id'])['status'] == 'running' and world.os.kills == []


@pytest.mark.parametrize('seen', [10 ** 400, -10 ** 400, 1e308], ids=['huge', 'huge-negative', 'huge-float'])
def test_the_kill_route_refuses_a_huge_creation_time_with_a_code_not_a_500(world, seen):
    world.os.start(PID, CREATED)
    status, body = kill(world, listed(), seen=seen)
    assert (status, body['code']) == (409, 'process_identity_unverified') and world.os.kills == []


def test_two_readings_of_one_start_differ_by_floating_point_steps_and_a_millisecond_is_another_process():
    ulp = math.ulp(CREATED)
    assert process_identity.same_created(CREATED, CREATED) is True
    for steps in (1, 4, 8):
        assert process_identity.same_created(CREATED, CREATED + steps * ulp) is True, steps
        assert process_identity.same_created(CREATED + steps * ulp, CREATED) is True, steps
    assert process_identity.same_created(CREATED, CREATED + 64 * ulp) is False          # ~1.5e-5 s: not a rounding difference
    assert process_identity.same_created(CREATED, CREATED + 0.001) is False
    assert process_identity.same_created(CREATED, CREATED - 0.001) is False


# ================================================================ H2R4-07: the holder is read only for the reasons that name the lease
def test_the_holder_is_never_read_for_a_reason_that_does_not_name_the_lease_even_if_the_lease_changes_meanwhile(world, monkeypatch):
    """The reason was computed while the lease was free (or unreadable). A holder appearing a moment later must
    not be attached to a reason that says the lease is free: the reply would contradict itself. The reply reads
    the lease once, for the reason, and names a holder only when that reason is a held lease."""
    world.os.start(PID, CREATED)
    world.os.denied.add(PID)
    reads, held = [], []
    real_state = device_lease.lease_state

    def state_then_acquire(*args, **kwargs):
        result = real_state(*args, **kwargs)
        reads.append(result['owned'])
        if len(reads) == 1 and not held:
            held.append(hold_lease(world))                       # somebody takes the lease right after it was read free
        return result
    left = stop(world, running(world))                                     # a Harvest-Off style stop: it never releases, so it stays cancelling
    assert left['status'] == 'cancelling'
    monkeypatch.setattr(device_lease, 'lease_state', state_then_acquire)
    with world.dash.app.test_request_context():
        reply = world.dash.jsonify(ok=True, **world.dash._stop_view(left, operations.stop_effects())).get_json()
    assert reply['stop']['reason'] == 'worker_not_verifiable_lease_free' and reply['stop']['holder'] is None
    assert reads == [False], 'the lease was read again after the reason was fixed'
    held[0].release()


# ================================================================ what a stop did is on record
def stop_events(world, kind):
    import json
    return [row for path in (Path(world.root) / 'logs' / '_dashboard').glob('events_*.jsonl')
            for row in map(json.loads, path.read_text(encoding='utf-8').splitlines()) if row['kind'] == kind]


def test_a_stop_leaves_a_record_of_what_was_signalled_skipped_and_refused(world):
    family(world)
    world.os.start(ORPHAN, CREATED + 9, ppid=CHILD)
    world.os.denied.add(ORPHAN)                                      # a descendant the kernel will not open
    op = running(world)
    stop(world, op)
    [event] = stop_events(world, 'operation_process_stop')
    assert event['operation_id'] == op['id'] and event['state'] == 'stopping' and event['incomplete'] is True
    assert event['signalled'] == [GRAND, CHILD, PID] and event['reason'] is None and event['survivors'] == []
    assert event['skipped'] == [dict(pid=ORPHAN, reason='access_denied')] and dict(pid=ORPHAN, step='open', code=5) in event['errors']


def test_a_stop_whose_family_cannot_be_listed_is_unverified_for_that_reason_in_the_record_and_the_worker_is_not_signalled(world, monkeypatch):
    family(world)
    monkeypatch.setattr(process_handle.kernel(), 'parents', lambda: (_ for _ in ()).throw(process_handle.KernelError('Process32NextW', 5)))
    op = running(world)
    left = stop(world, op)
    [event] = stop_events(world, 'operation_process_stop')
    assert (event['state'], event['reason']) == ('unverified', 'family_enumeration_failed') and event['signalled'] == []
    assert left['status'] == 'cancelling' and world.os.kills == [] and PID in world.os.table     # nothing was done, and it is not called stopped


def test_a_stop_that_leaves_the_lease_held_says_so_in_the_record(world):
    world.os.start(PID, CREATED, ppid=100)
    held = hold_lease(world)
    op = running(world)
    stop(world, op)
    [event] = stop_events(world, 'operation_stop_unreleased')
    assert event['operation_id'] == op['id'] and event['lease_free'] is False
    held.release()
