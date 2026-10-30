"""A person's Stop (POST /api/operations/<id>/cancel, /api/scan/stop, /api/calibrate/stop).

Three promises, each proven against the real Flask routes over a temporary ledger, a fake process table and a
FakeKernel (no real process is inspected or signalled):

  * It runs WITHOUT the global action lock, so it is never refused with `action_lock`; what keeps it safe is that
    every step is atomic by itself (a compare-and-set on the operation's revision, handle-bound signals, a
    release that re-reads the device lease inside the write).
  * A refusal that changed nothing is 409 with a stable `code` and the current operation. A conflict that
    arrives AFTER something was done (harvesting paused, a process signalled) is never that refusal: the stop is
    finished from the operation's real state, or it is 409 `stop_outcome_partial` listing what was done.
  * Every answer says what THIS request did (`stop.by_this_request`), so an operation another writer settled a
    moment after this request killed its worker is not reported as one nobody stopped.

The races are driven deterministically between the steps of the real route; the two real-thread tests (a Stop
racing the refresher tick, two Stops at the same moment) use events, not sleeps."""
import threading

import pytest

from control import harvest, operations
from control.operations import inspect_worker as real_inspect_worker   # imported before any fixture patches it
from harvest_http import body as harvest_body, enable, make_app, revision as harvest_revision
from harvest_support import decided, fake_inspect, fake_platform
from runtime import device_lease, process_handle
from towerpilot_workflows.operations import Conflict, Operations

PID, CREATED = 910001, 1_790_000_000.123456
ROUTES = ['cancel', 'scan', 'calibrate']
NOTHING = dict(paused=False, requested=False, signalled=[], family=None, released=False)


@pytest.fixture
def app(dash, launches, monkeypatch):
    app = make_app(dash, launches, monkeypatch)
    app.platform = fake_platform(monkeypatch)
    monkeypatch.setattr(operations, 'inspect_worker', real_inspect_worker)   # the REAL liveness check over the fake process table
    return app


def collector(app, key='person-stop-1', kind='gems', worker=True):
    """A running operation with a worker the fake process table knows (or one that never had a worker)."""
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {'preset': 'bp_farm'}, None)
    op, _ = store.accept(kind, context, {'preset': 'bp_farm'}, key)
    if not worker:
        return op
    app.platform.start(PID, CREATED, ppid=100)
    return store.transition(op['id'], op['revision'], 'running', worker=dict(pid=PID, creation_time=CREATED))


def stored(app, op):
    return operations.ledger(app.dash.ROOT).get(op['id'])


def stop(app, op, route='cancel', client=None, **override):
    """POST a Stop for `op` on one of the three routes (the other two carry the id in the body)."""
    payload = {'expected_revision': op['revision'], **override}
    if route == 'cancel':
        return (client or app.client).post(f"/api/operations/{op['id']}/cancel", json=payload)
    return (client or app.client).post(f'/api/{route}/stop', json=dict(payload, operation_id=op['id']))


class OtherAction:
    """Another action in another thread, in the middle of a mutating request: it holds the REAL global action lock
    until finish(). It enables harvesting but never gets to record anything (it gives up with a Conflict)."""

    def __init__(self, app):
        self.entered, self.release, self.response = threading.Event(), threading.Event(), []
        entered, release = self.entered, self.release

        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(20)
            raise Conflict('the other action gave up')
        app.monkeypatch.setattr(harvest, 'apply_choice', slow)
        payload = harvest_body(app)
        self.thread = threading.Thread(target=lambda: self.response.append(app.dash.app.test_client().post('/api/harvest', json=payload)))
        self.thread.start()
        assert entered.wait(20), 'the other action never started'
        assert app.dash._ACTION_LOCK.locked()

    def finish(self):
        self.release.set()
        self.thread.join(20)
        assert [reply.status_code for reply in self.response] == [409]


# ================================================================== no global action lock
@pytest.mark.parametrize('route', ROUTES)
def test_a_persons_stop_is_not_refused_while_another_action_holds_the_global_lock(app, route):
    enable(app)
    op = collector(app)
    other = OtherAction(app)
    try:
        reply = stop(app, op, route)
        assert reply.status_code == 200, reply.get_json()
        answer = reply.get_json()
        assert answer['operation']['status'] == 'cancelled'
        assert decided(answer['stop']) == dict(state='stopped', reason=None, holder=None)
        assert answer['stop']['by_this_request'] == dict(paused=True, requested=True, signalled=[PID], family='stopped', released=False)
        assert app.platform.kills == [(PID, CREATED)]
        assert app.dash._ACTION_LOCK.locked(), 'the Stop must neither take nor release the other action\'s lock'
        control = app.client.post('/api/harvest', json=harvest_body(app))
        assert (control.status_code, control.get_json()['code']) == (409, 'action_lock')   # every other mutation is still refused
    finally:
        other.finish()
    assert harvest_revision(app) == 2                                                    # the pause was recorded exactly once


@pytest.mark.parametrize('route', ROUTES)
def test_a_stale_stop_under_the_lock_is_a_coded_refusal_that_did_nothing(app, route):
    enable(app)
    op = collector(app)
    other = OtherAction(app)
    try:
        reply = stop(app, op, route, expected_revision=op['revision'] - 1)
    finally:
        other.finish()
    answer = reply.get_json()
    assert (reply.status_code, answer['ok'], answer['code']) == (409, False, 'operation_changed')
    assert answer['error'] and answer['operation']['id'] == op['id'] and answer['operation']['status'] == 'running'
    assert 'stop' not in answer and harvest_revision(app) == 1 and app.platform.kills == []
    assert stored(app, op)['revision'] == op['revision']


def test_a_scan_stop_under_the_lock_records_its_marker_and_nothing_is_signalled(app):
    op = collector(app, kind='scan', worker=False)
    other = OtherAction(app)
    try:
        reply = stop(app, op, 'scan')
    finally:
        other.finish()
    assert reply.status_code == 200 and reply.get_json()['operation']['status'] == 'cancelling'
    assert reply.get_json()['stop']['by_this_request'] == dict(NOTHING, requested=True)
    from pathlib import Path
    assert (Path(op['context']['artifact_dir']) / 'scan_stop').read_text(encoding='utf-8') == op['id']


# ================================================================== a refusal that did nothing has a code
@pytest.mark.parametrize('route', ROUTES)
def test_every_refusal_that_changed_nothing_carries_a_code_and_the_current_operation(app, route):
    enable(app)
    op = collector(app)
    unknown = '00000000-0000-4000-8000-0000000000aa'
    cases = {
        'operation_changed': stop(app, op, route, expected_revision=op['revision'] - 1),
        'operation_unknown': (app.client.post(f'/api/operations/{unknown}/cancel', json=dict(expected_revision=1)) if route == 'cancel'
                              else app.client.post(f'/api/{route}/stop', json=dict(operation_id=unknown, expected_revision=1))),
        'operation_required': (app.client.post(f"/api/operations/{op['id']}/cancel", json={}) if route == 'cancel'
                               else app.client.post(f'/api/{route}/stop', json={})),
    }
    for code, reply in cases.items():
        answer = reply.get_json()
        assert (reply.status_code, answer['ok'], answer['code']) == (409, False, code), (code, answer)
        assert answer['error'], code
    assert cases['operation_changed'].get_json()['operation']['id'] == op['id']
    assert cases['operation_unknown'].get_json()['operation'] is None
    assert harvest_revision(app) == 1 and app.platform.kills == [] and stored(app, op)['status'] == 'running'


@pytest.mark.parametrize('route', ROUTES)
def test_an_operation_that_has_already_ended_is_refused_as_changed_with_the_ended_operation(app, route):
    op = collector(app)
    done = stop(app, op, route)
    assert done.status_code == 200
    again = stop(app, done.get_json()['operation'], route)
    answer = again.get_json()
    assert (again.status_code, answer['code'], answer['operation']['status']) == (409, 'operation_changed', 'cancelled')
    assert app.platform.kills == [(PID, CREATED)]


def test_an_operation_that_cannot_be_cancelled_is_refused_with_its_own_code(app):
    op = collector(app, kind='profile_write', worker=False)
    reply = stop(app, op)
    answer = reply.get_json()
    assert (reply.status_code, answer['code'], answer['operation']['kind']) == (409, 'not_cancellable', 'profile_write')
    assert stored(app, op)['status'] == 'accepted'


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('junk', [[1, 2], 'text', 7, None])
def test_a_body_that_is_not_an_object_is_a_coded_refusal_not_a_server_error(app, route, junk):
    url = '/api/operations/anything/cancel' if route == 'cancel' else f'/api/{route}/stop'
    reply = app.client.post(url, json=junk)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'operation_required'


# ================================================================== a conflict after an effect is never a plain refusal
def bump(app, op):
    """Another writer moves the operation (a progress write): its revision changes, its status does not."""
    current = stored(app, op)
    return operations.ledger(app.dash.ROOT).transition(op['id'], current['revision'], 'running', progress={'n': current['revision']})


def test_the_operation_moving_after_the_pause_is_stop_outcome_partial_and_says_harvesting_was_paused(app, monkeypatch):
    enable(app)
    op = collector(app)
    real_pause = harvest.pause

    def pause_then_the_operation_moves(*args, **kwargs):
        result = real_pause(*args, **kwargs)
        bump(app, op)
        return result
    monkeypatch.setattr(harvest, 'pause', pause_then_the_operation_moves)
    reply = stop(app, op)
    answer = reply.get_json()
    assert (reply.status_code, answer['ok'], answer['code']) == (409, False, 'stop_outcome_partial')
    assert answer['stop']['by_this_request'] == dict(NOTHING, paused=True)
    assert answer['operation']['status'] == 'running' and answer['operation']['revision'] == op['revision'] + 1   # the CURRENT operation
    assert answer['stop']['state'] == 'active' and 'harvesting was paused' in answer['error'] and answer['conflict']
    assert harvest_revision(app) == 2 and app.platform.kills == []        # the pause really is recorded; nothing was signalled


def test_control_the_same_race_without_a_pause_is_a_plain_coded_refusal(app, monkeypatch):
    op = collector(app)                                                   # harvesting was never enabled: nothing to pause
    real_cancel = operations.cancel
    monkeypatch.setattr(operations, 'cancel', lambda *args, **kwargs: (bump(app, op), real_cancel(*args, **kwargs))[1])
    reply = stop(app, op)
    assert (reply.status_code, reply.get_json()['code']) == (409, 'operation_changed')
    assert 'stop' not in reply.get_json() and app.platform.kills == []


def lose_the_stop_compare_and_set(app, op, monkeypatch):
    """Another writer lands between cancel()'s revision check and its write that records the stop."""
    real = Operations.transition

    def transition(self, operation_id, revision, status, **changes):
        if status == 'cancelling':
            real(self, operation_id, revision, 'running', progress={'n': 1})
        return real(self, operation_id, revision, status, **changes)
    monkeypatch.setattr(Operations, 'transition', transition)


def test_losing_the_compare_and_set_that_records_the_stop_after_the_pause_is_partial_and_the_stop_is_not_recorded(app, monkeypatch):
    enable(app)
    op = collector(app)
    lose_the_stop_compare_and_set(app, op, monkeypatch)
    reply = stop(app, op)
    answer = reply.get_json()
    assert (reply.status_code, answer['code']) == (409, 'stop_outcome_partial')
    assert answer['stop']['by_this_request'] == dict(NOTHING, paused=True)        # requested is False: the stop was NOT recorded
    assert stored(app, op)['status'] == 'running' and app.platform.kills == []


def test_control_losing_that_compare_and_set_without_a_pause_is_a_plain_coded_refusal(app, monkeypatch):
    op = collector(app)
    lose_the_stop_compare_and_set(app, op, monkeypatch)
    reply = stop(app, op)
    assert (reply.status_code, reply.get_json()['code']) == (409, 'operation_changed')
    assert reply.get_json()['operation']['status'] == 'running' and app.platform.kills == []


# ================================================================== a conflict after the signal finishes the stop
def test_another_writer_settling_the_operation_after_the_signal_is_reported_as_ended_with_this_requests_effects(app, monkeypatch):
    """The worker was killed by THIS request and the refresher retired the operation before the final write:
    the answer is `ended`, and `by_this_request` is what says this request stopped the worker."""
    enable(app)
    op = collector(app)
    real = process_handle.terminate_tree

    def terminate_then_the_refresher_retires(pid, created, **kwargs):
        stopped = real(pid, created, **kwargs)
        operations.resolve(app.dash.ROOT, op['id'])                      # the real refresher path: the worker is gone, the lease is free
        return stopped
    monkeypatch.setattr(process_handle, 'terminate_tree', terminate_then_the_refresher_retires)
    reply = stop(app, op)
    answer = reply.get_json()
    assert reply.status_code == 200 and answer['operation']['status'] == 'interrupted'
    assert answer['operation']['error']['code'] == 'worker_lost'
    assert decided(answer['stop']) == dict(state='ended', reason=None, holder=None)
    assert answer['stop']['by_this_request'] == dict(paused=True, requested=True, signalled=[PID], family='stopped', released=False)
    assert app.platform.kills == [(PID, CREATED)]


def test_control_ended_before_this_request_signalled_anything_says_so(app, monkeypatch):
    op = collector(app)
    real = operations.stop_driver

    def another_writer_settles_first(root, operation_id, **kwargs):
        current = stored(app, op)
        operations.ledger(root).transition(operation_id, current['revision'], 'interrupted',
                                           error={'code': 'worker_lost', 'message': 'Worker exited without a confirmed outcome'})
        return real(root, operation_id, **kwargs)
    monkeypatch.setattr(operations, 'stop_driver', another_writer_settles_first)
    answer = stop(app, op).get_json()
    assert decided(answer['stop']) == dict(state='ended', reason=None, holder=None)
    assert answer['stop']['by_this_request'] == dict(NOTHING, requested=True) and app.platform.kills == []


def test_losing_the_final_compare_and_set_to_a_writer_that_settled_it_returns_that_settlement_not_an_error(app, monkeypatch):
    op = collector(app)
    real = Operations.transition

    def transition(self, operation_id, revision, status, **changes):
        if status == 'cancelled':                                         # the refresher gets in between this stop's read and its write
            real(self, operation_id, revision, 'interrupted', error={'code': 'worker_lost', 'message': 'Worker exited without a confirmed outcome'})
        return real(self, operation_id, revision, status, **changes)
    monkeypatch.setattr(Operations, 'transition', transition)
    reply = stop(app, op)
    answer = reply.get_json()
    assert reply.status_code == 200 and answer['operation']['status'] == 'interrupted'
    assert answer['stop']['by_this_request']['signalled'] == [PID] and app.platform.kills == [(PID, CREATED)]


def test_a_final_write_that_keeps_losing_is_bounded_reports_the_stop_as_it_stands_and_never_signals_twice(app, monkeypatch):
    op = collector(app)
    calls = []
    real = Operations.transition

    def transition(self, operation_id, revision, status, **changes):
        if status == 'cancelled':
            calls.append(revision)
            raise Conflict('lost again')
        return real(self, operation_id, revision, status, **changes)
    monkeypatch.setattr(Operations, 'transition', transition)
    reply = stop(app, op)
    answer = reply.get_json()
    assert reply.status_code == 200 and len(calls) == 3 == operations.STOP_SETTLE_ATTEMPTS
    assert answer['operation']['status'] == 'cancelling' and answer['stop']['state'] == 'stopping'   # gone, the next settle closes it
    assert answer['stop']['by_this_request'] == dict(paused=False, requested=True, signalled=[PID], family='stopped', released=False)
    assert app.platform.kills == [(PID, CREATED)]


# ================================================================== real threads
def test_a_stop_racing_the_refresher_tick_does_not_transition_twice_and_is_not_lost(app, monkeypatch):
    """The Stop has killed the worker and is about to write; the refresher tick (which holds the global lock; the
    Stop does not) retires the operation in that gap. One transition each, one kill, and a truthful answer."""
    enable(app)
    op = collector(app)
    killed, tick_done = threading.Event(), threading.Event()
    real = process_handle.terminate_tree

    def terminate_then_wait_for_the_tick(pid, created, **kwargs):
        stopped = real(pid, created, **kwargs)
        killed.set()
        assert tick_done.wait(20)
        return stopped
    monkeypatch.setattr(process_handle, 'terminate_tree', terminate_then_wait_for_the_tick)
    replies = []
    stopper = threading.Thread(target=lambda: replies.append(stop(app, op, client=app.dash.app.test_client())))
    stopper.start()
    try:
        assert killed.wait(20)
        app.dash._harvest_tick()
    finally:
        tick_done.set()
        stopper.join(20)
    [reply] = replies
    answer = reply.get_json()
    assert reply.status_code == 200 and decided(answer['stop'])['state'] == 'ended'
    assert answer['stop']['by_this_request']['signalled'] == [PID]
    final = stored(app, op)
    assert (final['status'], final['revision']) == ('interrupted', op['revision'] + 2)    # cancelling, then exactly one settlement
    assert app.platform.kills == [(PID, CREATED)] and app.dash._HARVEST_HOST['last_error'] is None
    assert harvest_revision(app) == 2


def test_the_refresher_tick_running_after_a_finished_stop_changes_nothing(app):
    enable(app)
    op = collector(app)
    assert stop(app, op).status_code == 200
    before = stored(app, op)
    app.dash._harvest_tick()
    assert stored(app, op) == before and before['status'] == 'cancelled' and app.platform.kills == [(PID, CREATED)]
    assert harvest_revision(app) == 2                                          # the Stop is not undone by the tick


@pytest.mark.parametrize('with_harvesting', [False, True], ids=['plain', 'harvesting-on'])
def test_two_stops_at_the_same_moment_stop_the_worker_once(app, monkeypatch, with_harvesting):
    """A double click: both requests pass the entry check and meet at cancel(). One compare-and-set wins; the other
    is a coded refusal (nothing done) - or, if it was the one that wrote the pause, a partial that says so.

    Two settlements are legal for the winner: its own `cancelled`, or `interrupted` when the loser's revision check
    (require_revision reconciles, like the refresher) saw the killed worker gone and retired the operation first.
    Both are one transition after `cancelling`, and the winner's answer says it signalled the worker either way."""
    if with_harvesting:
        enable(app)
    op = collector(app)
    meet = threading.Barrier(2, timeout=20)
    real = operations.cancel

    def cancel_together(*args, **kwargs):
        meet.wait()
        return real(*args, **kwargs)
    monkeypatch.setattr(operations, 'cancel', cancel_together)
    replies = []
    threads = [threading.Thread(target=lambda: replies.append(stop(app, op, client=app.dash.app.test_client()))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    winner = next(reply for reply in replies if reply.status_code == 200).get_json()
    loser = next(reply for reply in replies if reply.status_code == 409).get_json()
    settled = winner['operation']['status']
    assert settled in ('cancelled', 'interrupted') and winner['stop']['by_this_request']['signalled'] == [PID]
    assert decided(winner['stop'])['state'] == ('stopped' if settled == 'cancelled' else 'ended')
    assert loser['code'] in ('operation_changed', 'stop_outcome_partial') and loser['operation']['status'] in ('cancelling', 'cancelled', 'interrupted')
    final = stored(app, op)
    assert (final['status'], final['revision']) == (settled, op['revision'] + 2)          # one stop request recorded, one settlement
    assert app.platform.kills == [(PID, CREATED)]
    if with_harvesting:
        written = [r['stop']['by_this_request']['paused'] for r in (winner, loser) if 'stop' in r]
        assert written.count(True) == 1 and harvest_revision(app) == 2                    # exactly one of them wrote the pause
    else:
        assert harvest_revision(app) == 0


def test_a_pause_that_only_replays_another_requests_pause_is_not_this_requests_effect(app, monkeypatch):
    """Two Stops read the choice as enabled before either wrote. The first writes the pause; the second finds the
    first one's receipt (same key, same revision) and replays it: it changed nothing, so it must not claim it did."""
    from towerpilot_workflows import harvest as ledger_h
    enable(app)
    identity = harvest.identity_for(app.dash.load_config())
    with ledger_h.readonly(operations.ledger_path(app.dash.ROOT)) as db:
        before = ledger_h.read_choice(db, identity['device_key'])
    assert before['enabled'] is True and before['revision'] == 1
    wrote, replayed = operations.stop_effects(), operations.stop_effects()
    harvest.pause(app.dash.ROOT, identity, 'stopped_by_person', 'stop-the-same-moment', effects=wrote)
    monkeypatch.setattr(harvest, 'read_choice', lambda db, device: before)       # the second request read the choice before the first one wrote
    harvest.pause(app.dash.ROOT, identity, 'stopped_by_person', 'stop-the-same-moment', effects=replayed)
    assert (wrote['paused'], replayed['paused']) == (True, False)
    assert harvest_revision(app) == 2                                            # one pause was recorded, not two


# ================================================================== release: the lease is read at the moment of the write
def test_a_release_is_refused_when_the_lease_is_taken_between_the_check_and_the_write(app, monkeypatch):
    op = collector(app)
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: None))      # the worker can never be verified
    ident = operations.device_identity(app.dash.load_config())
    held, real = [], Operations.transition

    def transition(self, operation_id, revision, status, **changes):
        if changes.get('step') == 'worker_released' and not held:                             # first check passed; now something takes the lease
            held.append(device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port']))
        return real(self, operation_id, revision, status, **changes)
    monkeypatch.setattr(Operations, 'transition', transition)
    try:
        reply = stop(app, op)
        answer = reply.get_json()
        assert reply.status_code == 200 and answer['operation']['status'] == 'cancelling'
        assert decided(answer['stop'])['reason'] == 'worker_not_verifiable_lease_held'
        assert answer['stop']['by_this_request'] == dict(NOTHING, requested=True) and app.platform.kills == []
    finally:
        held[0].release()


def test_control_a_release_with_the_lease_free_is_recorded_and_says_this_request_released_it(app, monkeypatch):
    op = collector(app)
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: None))
    answer = stop(app, op).get_json()
    assert answer['operation']['status'] == 'interrupted' and decided(answer['stop'])['state'] == 'released'
    assert answer['stop']['by_this_request'] == dict(NOTHING, requested=True, released=True) and app.platform.kills == []


def test_a_transition_guard_runs_inside_the_write_and_a_refusal_writes_nothing(tmp_path):
    store = Operations(tmp_path / 'ops.sqlite3')
    op, _ = store.accept('run', dict(device_key='d', serial='s'), {}, 'k1')
    seen = []

    def refuse(current):
        seen.append((current['status'], current['revision']))
        raise Conflict('not now')
    with pytest.raises(Conflict, match='not now'):
        store.transition(op['id'], op['revision'], 'running', guard=refuse, worker=dict(pid=1, creation_time=1.0))
    assert seen == [('accepted', 1)] and store.get(op['id'])['revision'] == 1 and store.get(op['id'])['worker'] is None
    moved = store.transition(op['id'], op['revision'], 'running', guard=lambda current: seen.append('ok'))
    assert moved['status'] == 'running' and seen[-1] == 'ok'
    with pytest.raises(Conflict):                                                             # the guard is not asked about a stale revision
        store.transition(op['id'], 1, 'running', guard=lambda current: seen.append('stale'))
    assert 'stale' not in seen


# ================================================================== a repeated Stop after harvesting was turned back on
def test_a_repeated_stop_pauses_again_after_harvesting_was_turned_on_in_between(app):
    enable(app)
    app.platform.lingering = True                                                             # the worker does not exit: the stop stays open
    op = collector(app)
    first = stop(app, op)
    assert first.status_code == 200 and decided(first.get_json()['stop'])['state'] == 'stopping'
    assert harvest_revision(app) == 2
    enable(app)                                                                               # the person turns harvesting back on
    assert harvest_revision(app) == 3
    second = stop(app, first.get_json()['operation'])
    assert second.status_code == 200, second.get_json()
    assert second.get_json()['stop']['by_this_request']['paused'] is True and harvest_revision(app) == 4


# ================================================================== H2R7-01 / H2R7-02: Stop against the lease, on real lock files
def lease_identity(app):
    return operations.device_identity(app.dash.load_config())


def test_a_stop_is_not_confirmed_while_a_delegated_owner_is_promoted_between_the_two_lock_probes(app, monkeypatch):
    """H2R7-01 end to end. The worker's family is stopped, but a delegated scheduler child still drives the
    emulator; at the instant the Stop looks at the lease the child is promoted (primary lock taken, then the
    activity lock released). The lease was never free, so the Stop must stay cancelling and unverified; before
    the fix the two probes straddled the promotion, the device read free and the Stop was reported stopped."""
    from harvest_support import delegated_lease, promote_after_the_primary_probe
    op = collector(app)
    ident = lease_identity(app)
    lease = delegated_lease(monkeypatch, ident['serial'], ident['adb_host'], ident['adb_port'])
    try:
        fired = promote_after_the_primary_probe(monkeypatch, lease)
        reply = stop(app, op)
        answer = reply.get_json()
        assert fired == [True] and reply.status_code == 200, reply.get_json()
        assert answer['operation']['status'] == 'cancelling'
        assert decided(answer['stop'])['state'] == 'unverified' and decided(answer['stop'])['reason'] == 'lease_held_after_stop'
        assert stored(app, op)['status'] == 'cancelling' and app.platform.kills == [(PID, CREATED)]
        actual = device_lease.lease_state(ident['serial'], ident['adb_host'], ident['adb_port'])
        assert actual['owned'] is True                                   # what the Stop said is what is true
        with pytest.raises(device_lease.DeviceBusy):
            device_lease._lock(lease.path)
    finally:
        lease.release()


def test_control_a_stop_with_the_delegated_owner_holding_still_is_also_unverified(app, monkeypatch):
    from harvest_support import delegated_lease
    op = collector(app)
    ident = lease_identity(app)
    lease = delegated_lease(monkeypatch, ident['serial'], ident['adb_host'], ident['adb_port'])
    try:
        answer = stop(app, op).get_json()
        assert answer['operation']['status'] == 'cancelling' and decided(answer['stop'])['reason'] == 'lease_held_after_stop'
    finally:
        lease.release()
    assert operations.resolve(app.dash.ROOT, op['id'])['status'] == 'interrupted'     # the owner is gone: the operation is retired, not left owning


class StopDuringTheRead:
    """Runs a real Flask Stop in the gap H2R7-02 is about: right after the first ledger read of the next
    operations.resolve(), i.e. after the reader's snapshot and before it retires anything."""

    def __init__(self, app, op, monkeypatch):
        self.replies, self.armed = [], [True]
        real = Operations.get

        def get(this, operation_id):
            seen = real(this, operation_id)
            if self.armed:
                self.armed.clear()
                self.replies.append(stop(app, op))
            return seen
        monkeypatch.setattr(Operations, 'get', get)


def test_a_refresher_that_read_the_operation_before_a_stop_does_not_retire_what_the_stop_left_owned(app, monkeypatch):
    """H2R7-02. The refresher starts reconciling a RUNNING operation; a person's Stop turns it into cancelling,
    stops the worker, and finds the device lease still held (unverified, lease_held_after_stop). The refresher
    then judged the operation by the status it had read earlier ("running"), saw a gone worker, and wrote
    interrupted/worker_lost over a held lease: the Stop reported ended while the device was still owned. The
    liveness policy now judges the snapshot reconcile itself reads, and retires that revision only."""
    op = collector(app)
    ident = lease_identity(app)
    held = device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port'])     # a survivor owns the emulator
    try:
        race = StopDuringTheRead(app, op, monkeypatch)
        after = operations.resolve(app.dash.ROOT, op['id'])
        [reply] = race.replies
        answer = reply.get_json()
        assert decided(answer['stop'])['state'] == 'unverified' and decided(answer['stop'])['reason'] == 'lease_held_after_stop'
        assert after['status'] == 'cancelling', after                  # not retired: its revision moved under the stale judgement
        final = stored(app, op)
        assert (final['status'], final['error']) == ('cancelling', None)
        assert operations.stop_state(final) == dict(state='unverified', reason='lease_held_after_stop')
        assert device_lease.lease_state(ident['serial'], ident['adb_host'], ident['adb_port'])['owned'] is True
    finally:
        held.release()


def test_control_without_the_stop_the_refresher_retires_a_gone_worker_with_a_free_lease(app):
    op = collector(app)
    app.platform.exit(PID)                                              # the worker died; nothing holds the lease
    retired = operations.resolve(app.dash.ROOT, op['id'])
    assert retired['status'] == 'interrupted' and retired['error']['code'] == 'worker_lost'


def test_control_a_refresher_that_reads_after_the_stop_keeps_the_held_lease_operation_cancelling(app):
    op = collector(app)
    ident = lease_identity(app)
    held = device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port'])
    try:
        assert decided(stop(app, op).get_json()['stop'])['reason'] == 'lease_held_after_stop'
        assert operations.resolve(app.dash.ROOT, op['id'])['status'] == 'cancelling'
    finally:
        held.release()
