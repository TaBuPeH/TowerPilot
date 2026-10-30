"""Local application operation adapter; no device/runtime-settings imports."""
import copy
import os
from functools import partial
from pathlib import Path

from control import diagnostics as diagnostic_store
from towerpilot_workflows.operations import (
    Conflict, IdempotencyKeyReused, Operations, SYNCHRONOUS_PERSISTENCE_KINDS, digest, file_revision,
)


class OperationRefused(Conflict):
    """A command refused before it changed anything. `code` is the stable word a client branches on and
    `operation` the operation as recorded at that moment (None when there is none)."""

    def __init__(self, code, message, operation=None):
        super().__init__(message)
        self.code, self.operation = code, operation


class ContextChanged(OperationRefused):
    """A write that named the emulator, account and profile its page was composed for (`expected_context`), refused
    before it wrote anything because the configuration now names another. `current` is the context now."""

    def __init__(self, current):
        super().__init__('context_changed', 'The emulator, account or profile changed since this page was read; '
                                            'review the new context before saving again')
        self.current = current


def stop_effects():
    """What one person's Stop has done so far, filled in by cancel / stop_driver / release_unverified_worker
    (and the dashboard route for the pause): harvesting paused, the stop recorded on the operation, the pids
    TerminateProcess was called on, how the verified family ended (process_handle.Stop.state), and whether an
    unverifiable worker was released. A conflict that arrives after any of it is a PARTIAL outcome, never a
    plain refusal."""
    return dict(paused=False, requested=False, signalled=[], family=None, released=False)


def effects_taken(effects):
    return bool(effects['paused'] or effects['requested'] or effects['signalled'] or effects['released'])


def ledger_path(root):
    """The one location of a checkout's operations database. Every reader and
    writer (including read-only guards) derives the path here."""
    return Path(root) / 'logs' / 'operations.sqlite3'


def ledger(root):
    return Operations(ledger_path(root))


def device_identity(cfg, instance=None):
    """The emulator identity every ledger row, admission check and setup-battle
    guard is keyed by: the ADB endpoint this process talks to plus the serial.

    Pure: reads configuration and the configured socket address, no device I/O.
    """
    from device import adbclient
    inst = instance or cfg.get('active_instance', 'main')
    device = (cfg.get('instances') or {}).get(inst) or {}
    host, port = adbclient._HOST
    host = '127.0.0.1' if str(host).lower() in ('localhost', '::1') else str(host).lower()
    return dict(instance=inst, serial=device.get('serial'), adb_host=host, adb_port=int(port),
                device_key=digest({'host': host, 'port': int(port), 'serial': device.get('serial')}))


PAGE_CONTEXT = ('instance', 'account', 'profile', 'serial')


def page_context(cfg):
    """The context a page composes its writes in, exactly as GET /api/config names it: the active emulator, the account
    and ADB serial of that emulator, and the profile in force (the account's own when one is bound). Pure: configuration
    only, no device or process is touched. The caller passes the configuration as load_config() returns it."""
    from player import accounts
    instance = cfg.get('active_instance', 'main')
    device = (cfg.get('instances') or {}).get(instance) or {}
    return dict(instance=instance, account=accounts.identity(cfg), profile=cfg.get('active_profile'),
                serial=device.get('serial'))


def expected_page_context(body):
    """None when the request names no `expected_context` (the legacy page: no fence); otherwise the context it names.
    ValueError when the field is present but is not exactly the four keys of PAGE_CONTEXT, each text or null. An explicit
    null is refused, never read as 'absent': a client that cannot name its context must not skip the fence by sending
    nothing useful."""
    if not isinstance(body, dict) or 'expected_context' not in body:
        return None
    expected = body['expected_context']
    if not isinstance(expected, dict) or set(expected) != set(PAGE_CONTEXT):
        raise ValueError('expected_context must be an object naming exactly ' + ', '.join(PAGE_CONTEXT))
    for key in PAGE_CONTEXT:
        if expected[key] is not None and not isinstance(expected[key], str):
            raise ValueError(f'expected_context.{key} must be text or null')
    return dict(expected)


def require_page_context(expected, current):
    """Refuse (ContextChanged) when the context in force (page_context of the saved configuration) is no longer the one the page
    composed its write for. The caller holds the lock every context switch holds, so the answer stays true until its write is done."""
    if expected is not None and expected != current:
        raise ContextChanged(current)


def context(root, cfg, command, profile_path):
    from player import accounts
    identity = device_identity(cfg)
    device = (cfg.get('instances') or {}).get(identity['instance']) or {}
    return dict(instance=identity['instance'], account=accounts.identity(cfg), serial=identity['serial'],
                rendering=device.get('rendering'), profile=cfg.get('active_profile'),
                profile_path=profile_path, profile_revision=file_revision(profile_path) if profile_path else None,
                selected_run=command.get('selected_run', command.get('preset')),
                config_revision=digest(cfg), artifact_dir=str(accounts.calibration_dir(root, cfg)),
                adb_host=identity['adb_host'], adb_port=identity['adb_port'],
                device_key=identity['device_key'], config=copy.deepcopy(cfg))


def public(op):
    result = copy.deepcopy(op)
    result['cancellable'] = (not synchronous_persistence(op)
                             and op['status'] in ('accepted', 'running', 'cancelling'))
    result['context'].pop('config', None)
    result.get('request', {}).pop('battle_envelope', None)
    return result


def synchronous_persistence(op):
    return op['kind'] in SYNCHRONOUS_PERSISTENCE_KINDS


def inspect_worker(worker):
    """(liveness, process) for the worker an operation recorded. THE one place a recorded
    worker is READ (runtime.process_identity.open_verified); everything that merely asks
    whether it runs calls worker_alive. The process object is for reading only (its family
    for labelling): nothing is ever terminated through it, because psutil re-opens the pid to
    do that. Stopping a worker is runtime.process_handle.terminate_tree, which verifies and
    terminates through one kernel handle.

    liveness is True (pid and creation time match a running process; process is that
    verified psutil.Process), False (PROVEN gone: no such process, a different process
    now owns the pid, it exited, or no worker was ever recorded) or None (UNKNOWN: access
    denied, any other OS/psutil failure, an identity that is malformed or lacks its
    creation time). process is None unless liveness is True."""
    from runtime import process_identity
    if worker is None or (isinstance(worker, dict) and not worker):
        return False, None    # the one sentinel for "no worker was ever recorded"; every other falsey value is malformed
    if not isinstance(worker, dict):
        return None, None
    return process_identity.open_verified(worker.get('pid'), worker.get('creation_time'))


def device_lease_free(context):
    """Is the device lease of this operation's emulator free? True: no process holds it. False: one
    does. None: it cannot be told (no ADB serial, or the lock files cannot be probed). The lease is the
    operating system's own record of who may drive the device, which no ledger record can contradict."""
    serial = (context or {}).get('serial')
    if not serial:
        return None
    from runtime import device_lease
    try:
        return not device_lease.lease_state(serial, context.get('adb_host', '127.0.0.1'),
                                            context.get('adb_port', 5037))['owned']
    except (OSError, ValueError, TypeError):
        return None


def inspect_operation(op):
    """(liveness, process) for an operation: inspect_worker, plus the one settlement a worker record
    alone cannot give. A record that names a usable pid but no usable creation time cannot be matched
    to a process, yet it IS proven gone when nothing has that pid and nothing holds the device lease
    (the lease would be held by any worker that was driving the emulator). A pid that exists, one that
    cannot be inspected, an unusable pid or a held or unreadable lease all stay unknown."""
    from runtime import process_identity
    worker = op.get('worker')
    liveness, process = inspect_worker(worker)
    if (liveness is None and isinstance(worker, dict) and process_identity.usable_pid(worker.get('pid'))
            and not process_identity.usable_created(worker.get('creation_time'))
            and process_identity.pid_absent(worker['pid']) and device_lease_free(op.get('context')) is True):
        return False, None
    return liveness, process


def operation_liveness(op):
    """inspect_operation()'s answer alone: True, False (PROVEN gone) or None (unknown)."""
    return inspect_operation(op)[0]


def device_unreleased(op):
    """True when an operation that HAD a worker has not given the device back: the lease of its emulator is
    held, or cannot be read. A worker's own delegated child (or a descendant the family walk could not
    reach) keeps the lease after the worker is gone, and nothing about the worker can say it is not there;
    only the lease can. An operation that never had a worker, or has no ADB serial, can hold no lease."""
    if not op.get('worker') or not (op.get('context') or {}).get('serial'):
        return False
    return device_lease_free(op.get('context')) is not True


def retirable_liveness(op):
    """What reconcile may retire on: operation_liveness, except that a stop which is still open (cancelling)
    on a worker that is gone is not finished while the device is unreleased. It stays the owner (unknown)
    until the lease is free, so a survivor is never hidden behind an `interrupted` record."""
    liveness = operation_liveness(op)
    if liveness is False and op.get('status') == 'cancelling' and device_unreleased(op):
        return None
    return liveness


def worker_alive(worker):
    """Is the worker process an operation recorded still running? THREE answers
    (see inspect_worker): True, False (PROVEN gone), None (UNKNOWN).

    Only False may retire an operation or finish its battle record. Unknown is
    never read as dead: a worker that is really alive keeps the device lease and
    its operation stays the owner until it is proven gone. Callers must compare
    with `is False` / `is True`, never test truthiness."""
    return inspect_worker(worker)[0]


def launch(root, cfg, kind, command, cmd, popen, profile_path=None, **kwargs):
    from runtime import diagnostics
    emit = partial(diagnostics.event_to, diagnostic_store.log_root(root) / '_dashboard')
    with diagnostics.attempt('operation.launch', emit=emit, operation_kind=kind) as attempt:
        result = _launch(root, cfg, kind, command, cmd, popen, profile_path, **kwargs)
        attempt.finish('accepted', operation_id=result['id'], operation_status=result['status'])
        return result


def verify_expected(target, command):
    """A command that names the context it was composed for is refused when the
    account, device or profile is no longer that one."""
    expected = command.get('expected_context')
    if expected is not None:
        if not isinstance(expected, dict) or any(expected.get(key) != target.get(key) for key in ('instance', 'account', 'serial', 'rendering', 'profile')):
            raise OperationRefused('context_changed', 'Selected account, device or profile changed before acceptance')


def _launch(root, cfg, kind, command, cmd, popen, profile_path=None, **kwargs):
    from runtime import diagnostics
    previous = retry(root, cfg, kind, command, profile_path)
    if previous:
        return previous
    store = ledger(root)
    payload = {k: v for k, v in command.items() if k != 'idempotency_key'}
    target = context(root, cfg, payload, profile_path)
    # Correlation travels through the environment, never the business command
    # or target digest: retries keep the same idempotency semantics.
    diagnostic = diagnostics.current_context()
    verify_expected(target, command)
    for active in store.active(target['device_key']):
        resolve(root, active['id'])
    # Run and collector drivers adopt a battle already on screen. Refuse them
    # while recorded setup evidence leaves a setup battle unresolved. Identical
    # retries already returned their receipt above; nothing is recorded here.
    from control import battles
    if kind in battles.GUARD_SCOPES:
        battles.refuse_if_blocked(battles.setup_battle_guard(root, target, scope=battles.GUARD_SCOPES[kind]))
    admit = None
    if kind == 'gems' and payload.get('harvest') is True:
        # The lifecycle host's collector is accepted only while the person's
        # choice still names it: checked inside the acceptance transaction.
        from towerpilot_workflows.harvest import verify_automatic_launch
        admit = lambda db: verify_automatic_launch(db, target, payload)  # noqa: E731
    op, created = store.accept(kind, target, payload, command.get('idempotency_key'), admit=admit)
    if not created:
        return op
    diagnostics.event_to(diagnostic_store.log_root(root) / '_dashboard', 'operation_accepted',
                         operation_id=op['id'], operation_kind=kind, outcome=op['status'],
                         instance=target['instance'])
    env = dict(os.environ, TOWER_OPERATION_DB=store.path, TOWER_OPERATION_ID=op['id'])
    for key, name in (('trace_id', 'TOWER_TRACE_ID'), ('request_id', 'TOWER_REQUEST_ID'),
                      ('span_id', 'TOWER_PARENT_SPAN_ID')):
        if diagnostic.get(key):
            env[name] = str(diagnostic[key])
    try:
        process = popen([cmd[0], '-m', 'runtime.operation_worker', *cmd[1:]], env=env, **kwargs)
        import psutil
        try:
            creation_time = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            creation_time = None
        if creation_time is not None:
            store.launched(op['id'], {'pid': process.pid, 'creation_time': creation_time})
    except (OSError, ValueError) as exc:
        diagnostics.event_to(diagnostic_store.log_root(root) / '_dashboard', 'operation_launch_failed',
                             operation_id=op['id'], operation_kind=kind, outcome='failed',
                             exception_type=type(exc).__name__, exception_message=str(exc),
                             instance=target['instance'])
        store.transition(op['id'], op['revision'], 'failed', error={'code': 'launch_failed', 'message': str(exc)})
        raise
    return store.get(op['id'])


def stored(root, operation_id):
    """An operation exactly as recorded, for GET paths: nothing is reconciled and
    nothing is created. A worker that died without an outcome is retired by the
    process refresher (project_processes) or by the next POST, never by a read."""
    return Operations.read_only(ledger_path(root), operation_id)


def resolve(root, operation_id):
    store = ledger(root)
    judged = []

    def policy(snapshot):
        judged.append(snapshot)
        return retirable_liveness(snapshot)
    # Only a worker PROVEN gone retires an operation (see inspect_operation for the one corroborated case). The
    # judgement is made on the snapshot reconcile read and retires that same revision: no earlier read of this
    # function is consulted, so an operation a Stop moved to cancelling meanwhile is judged AS cancelling.
    result = store.reconcile(operation_id, policy=policy)
    before = judged[-1] if judged else None
    if before is not None and result['status'] != before['status']:
        from runtime import diagnostics
        with diagnostics.context(operation_id=operation_id, instance=before['context'].get('instance')):
            diagnostics.event_to(diagnostic_store.log_root(root) / '_dashboard', 'operation_reconciled', previous_status=before['status'],
                         outcome=result['status'], error=result.get('error'),
                         worker=before.get('worker'))
    return result


def retry(root, cfg, kind, command, profile_path):
    """Resolve an identical retried command before legacy process guards."""
    if not command.get('idempotency_key'):
        return None
    store = ledger(root)
    with store._db() as db:
        row = db.execute('SELECT id FROM operations WHERE idempotency_key=?', (command['idempotency_key'],)).fetchone()
    if row is None:
        return None
    payload = {k: v for k, v in command.items() if k != 'idempotency_key'}
    original = store.get(row[0])
    current = context(root, cfg, payload, profile_path)
    identity = ('instance', 'account', 'serial', 'rendering', 'profile', 'device_key', 'selected_run')
    original_payload = {k: v for k, v in original['request'].items() if k != 'battle_envelope'}
    payload.pop('battle_envelope', None)
    if (original['kind'] != kind or original_payload != payload
            or any(original['context'].get(key) != current.get(key) for key in identity)):
        raise IdempotencyKeyReused('Idempotency key already belongs to another command or target')
    return resolve(root, row[0])


def require_revision(root, body, allowed):
    """The operation the caller saw, or an OperationRefused (a Conflict with a stable code) that did nothing:
    operation_required | operation_unknown | operation_changed (the current operation rides along)."""
    if not body.get('operation_id') or not isinstance(body.get('expected_revision'), int):
        raise OperationRefused('operation_required', 'operation_id and expected_revision are required')
    try:
        op = resolve(root, body['operation_id'])
    except KeyError:
        raise OperationRefused('operation_unknown', 'Unknown operation') from None
    if op['revision'] != body['expected_revision'] or op['status'] not in allowed:
        raise OperationRefused('operation_changed', 'Operation changed; refresh its status before trying again', op)
    return op


def cancel(root, body, *, release_unverified=False, effects=None):
    """Ask an operation to stop. release_unverified is a PERSON's explicit Stop (the dashboard route):
    if the worker then still cannot be verified, and nothing holds the device lease, the operation is
    released (see release_unverified_worker). The harvest lifecycle never passes it.

    Raises OperationRefused only for a refusal that changed nothing here (stale revision, unknown or
    uncancellable operation). Once the stop is recorded (effects['requested']) a writer that moves the
    operation concurrently does not make this a refusal: stop_driver finishes it from the operation's real
    current state. `effects` (stop_effects()) collects what this call did."""
    effects = stop_effects() if effects is None else effects
    op = require_revision(root, body, ('accepted', 'running', 'cancelling'))
    if synchronous_persistence(op):
        raise OperationRefused('not_cancellable', 'Synchronous profile persistence cannot be cancelled', op)
    store = ledger(root)
    if op['status'] == 'cancelling':
        if not release_unverified:
            return op
        if op['kind'] not in ('scan', 'calibrate', 'observe'):
            op = stop_driver(root, op['id'], effects=effects)   # a repeated Stop tries again (safe to repeat) before it releases anything
        return release_unverified_worker(root, op['id'], effects=effects)
    try:
        op = store.transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
    except Conflict as exc:   # the compare-and-set lost: the stop was NOT recorded, so this request did nothing here
        raise OperationRefused('operation_changed', str(exc), store.get(op['id'])) from exc
    effects['requested'] = True
    if op['kind'] not in ('scan', 'calibrate', 'observe'):
        op = stop_driver(root, op['id'], effects=effects)
    else:
        directory = Path(op['context']['artifact_dir'])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ('scan_stop' if op['kind'] == 'scan' else 'calibrate_stop')).write_text(op['id'], encoding='utf-8')
    return release_unverified_worker(root, op['id'], effects=effects) if release_unverified else op


def _releasable(op):
    """May a person's Stop release this operation NOW? It must be cancelling, its worker must be unverifiable
    (liveness unknown) and the device lease must be readable and free. Read at the moment of asking."""
    return op['status'] == 'cancelling' and inspect_operation(op)[0] is None and device_lease_free(op.get('context')) is True


def release_unverified_worker(root, operation_id, effects=None):
    """A person's way out of an operation whose worker can never be verified (its record is malformed,
    or the process cannot be inspected) and that therefore stays `cancelling` forever.

    It only ever acts on an operation that is already cancelling, whose liveness is UNKNOWN, and only
    while the device lease is readable and FREE: no process is driving the emulator, whatever the record
    says. It then settles the operation as interrupted (worker_released). It never signals any process,
    and a worker that is verifiably running is never released, only stopped. While some process holds
    the lease, or the lease cannot be read, the operation stays the owner.

    The facts are read twice: first to avoid a write that cannot succeed, then AGAIN inside the ledger
    transaction that records the release (Operations.transition guard), after the revision was confirmed
    and before anything is written. A lease taken (or a worker that became verifiable) in between refuses
    the release, so the lease is judged at the moment of release, not at the moment of asking."""
    store = ledger(root)
    op = store.get(operation_id)
    if not _releasable(op):
        return op

    def still_releasable(current):
        if not _releasable(current):
            raise Conflict('The worker or the device lease changed; nothing was released')
    try:
        released = store.transition(
            op['id'], op['revision'], 'interrupted', guard=still_releasable, step='worker_released',
            error={'code': 'worker_released',
                   'message': 'Released by a person: the worker could not be verified and no process holds the device lease'},
            recovery_action='Review the device before starting a new operation')
    except Conflict:
        return store.get(operation_id)
    if effects is not None:
        effects['released'] = True
    return released


def stop_state(op):
    """How a stop request stands, for the person who made it: {state, reason}.
    stopped (settled cancelled: the verified family exited AND the device lease is free) | released (a
    person released an unverifiable worker) | stopping (the verified worker was signalled and has not
    exited) | unverified (the stop cannot be confirmed; reason says why) | ended (it ended on its own, or
    another writer settled it) | active (not stopping: accepted, running, applying).

    unverified reasons: worker_not_verifiable_lease_free|_lease_held|_lease_unreadable (the worker's
    identity cannot be proven) and lease_held_after_stop|lease_unreadable_after_stop (the worker is gone or
    was stopped, but some process still holds the device lease, or the lease cannot be read: something
    the family walk could not reach survives; the operation stays the owner)."""
    status = op['status']
    if status == 'cancelled':
        return dict(state='stopped', reason=None)
    if status == 'interrupted':
        released = (op.get('error') or {}).get('code') == 'worker_released'
        return dict(state='released' if released else 'ended', reason=None)
    if status in ('accepted', 'running', 'applying'):
        return dict(state='active', reason=None)
    if status != 'cancelling':
        return dict(state='ended', reason=None)   # failed or succeeded on its own
    liveness, _ = inspect_operation(op)
    if liveness is True:
        return dict(state='stopping', reason=None)
    free = device_lease_free(op.get('context'))
    if liveness is None:
        return dict(state='unverified', reason={True: 'worker_not_verifiable_lease_free',
                                                False: 'worker_not_verifiable_lease_held'}.get(free, 'worker_not_verifiable_lease_unreadable'))
    if device_unreleased(op):
        return dict(state='unverified', reason='lease_held_after_stop' if free is False else 'lease_unreadable_after_stop')
    return dict(state='stopping', reason=None)   # the worker is gone and the device is free: the next settle closes it


STOP_SETTLE_ATTEMPTS = 3   # a stop whose final write keeps losing to other writers is reported as it stands, not retried forever


def stop_driver(root, operation_id, effects=None):
    """Terminate a cancelling driver's worker family and settle the operation.

    Driver cancellation stops input, never surrenders the game battle. Safe to
    repeat: a worker that was still exiting is terminated again, and an
    operation another writer already settled is returned unchanged.

    The processes are signalled BEFORE the final write, so that write must not turn a lost
    compare-and-set into an error the caller reads as 'nothing happened': when another writer moved
    the operation meanwhile (a progress write, the refresher retiring a worker it saw die) the stop
    looks again at the operation's real current state - a second look signals nothing, the worker is
    gone - and settles it, or returns it as another writer left it. `effects` records what was signalled."""
    store = ledger(root)
    for _ in range(STOP_SETTLE_ATTEMPTS):
        op = store.get(operation_id)
        if op['status'] != 'cancelling':
            return op
        liveness, _ = inspect_operation(op)   # a read-only question (psutil); it decides nothing destructive
        if liveness is None:
            return op   # identity cannot be proven: nothing is terminated on a guess, the stop stays unconfirmed
        if liveness:
            from runtime import process_handle
            worker = op['worker']
            # The worker and its family are verified and terminated through kernel handles held for the
            # whole stop (runtime.process_handle): a pid that passes to another process meanwhile is
            # never signalled, and a candidate that cannot be shown to be a descendant is skipped.
            stop = process_handle.terminate_tree(worker.get('pid'), worker.get('creation_time'))
            report_stop(root, op, stop)
            if effects is not None:
                effects['signalled'].extend(pid for pid in stop.signalled if pid not in effects['signalled'])
                effects['family'] = stop.state
            if not stop.confirmed:
                return op   # unverified, a member did not exit or could not be signalled: still stopping
        if device_unreleased(op):
            # The verified family is gone (or the worker already was) but a process still holds the device
            # lease, or the lease cannot be read: a descendant the walk could not reach survives. The stop
            # is NOT confirmed, so the operation stays the owner; the lease holder is named in the reply.
            report_unreleased(root, op)
            return op
        op = store.get(operation_id)
        if op['status'] != 'cancelling':
            return op
        try:
            return store.transition(op['id'], op['revision'], 'cancelled', step='driver_stopped')
        except Conflict:
            continue   # another writer got in first: read the operation again
    return store.get(operation_id)


def report_unreleased(root, op):
    from runtime import diagnostics
    with diagnostics.context(operation_id=op['id'], instance=op['context'].get('instance')):
        diagnostics.event_to(diagnostic_store.log_root(root) / '_dashboard', 'operation_stop_unreleased',
                             lease_free=device_lease_free(op.get('context')))


def report_stop(root, op, stop):
    """Leave a record of what a stop did to which processes (diagnostics only: it never changes the
    outcome). Candidates that failed verification are listed with the reason they were not touched."""
    from runtime import diagnostics
    with diagnostics.context(operation_id=op['id'], instance=op['context'].get('instance')):
        diagnostics.event_to(diagnostic_store.log_root(root) / '_dashboard', 'operation_process_stop',
                             state=stop.state, reason=stop.reason, signalled=stop.signalled, skipped=stop.skipped,
                             errors=stop.errors, survivors=stop.survivors, incomplete=stop.incomplete)


def inline_owner_pids(root):
    """The pids of the dashboards that are running a synchronous persistence write right now (a profile
    write or promotion). They own the emulator's profile files but are no runner: never listed, never
    stoppable, and never probed by a pid that merely names them."""
    path = ledger_path(root)
    if not path.is_file():
        return set()
    return {(op.get('worker') or {}).get('pid') for op in Operations.active_readonly(path)
            if synchronous_persistence(op)} - {None}


def _is_worker_row(row, worker, recorded, ancestors):
    """Is this scan row the verified worker, or its launcher? A pid number alone is not enough for either:
    a scan row is older than the verification and its pid may have passed to another process since. The
    WORKER's row must carry the creation time the scan pinned to that process and it must be the creation
    time the operation recorded for the worker (which was just verified); an ANCESTOR's row must carry the
    creation time of the ancestor object read from the worker, and that object must still be running. A row
    without a usable creation time, or with a different one, belongs to nobody we can vouch for: it stays a
    separate unlabelled row and the worker gets its own."""
    import psutil
    from runtime import process_identity
    pid = row.get('pid')
    if pid == worker.pid:
        return process_identity.same_created(row.get('created'), recorded)
    ancestor = ancestors.get(pid)
    if ancestor is None:
        return False
    try:
        return (process_identity.same_created(row.get('created'), ancestor.create_time())
                and ancestor.is_running())
    except psutil.Error:
        return False


def project_processes(root, rows, *, reconcile=True):
    """Attach durable identity only to a verified live worker/launcher family.

    reconcile=True (the refresher and POST paths) retires workers that died
    without an outcome. reconcile=False is for GET paths: nothing is created or
    changed, a missing ledger means no operations, and an operation whose worker
    is not alive is simply not projected (the refresher retires it)."""
    import psutil
    reconcile = reconcile and ledger_path(root).is_file()   # no ledger: nothing to retire, and nothing is created
    active = ledger(root).active() if reconcile else Operations.active_readonly(ledger_path(root))
    inline_pids = {(op.get('worker') or {}).get('pid') for op in active
                   if synchronous_persistence(op)} - {None}
    output = [copy.deepcopy(row) for row in rows
              if row.get('pid') not in inline_pids
              and not (row.get('operation') and synchronous_persistence(row['operation']))]
    for prior in active:
        if synchronous_persistence(prior):
            continue  # Its owner is the dashboard, never a stoppable runner.
        op = resolve(root, prior['id']) if reconcile else prior
        if op['status'] not in ('accepted', 'running', 'cancelling') or not op.get('worker'):
            continue   # pre-spawn: there is no process to project yet
        liveness, process = inspect_worker(op['worker'])
        if liveness is not True:
            continue   # gone, or unverifiable (an unverifiable one stays an owner in the ledger)
        try:
            ancestors = {parent.pid: parent for parent in process.parents()}   # objects read from the verified worker, never a second open
            if not process.is_running():
                continue   # the pid changed hands while its family was read: whatever owns it now is not this worker
        except psutil.Error:
            continue
        row = next((row for row in output if _is_worker_row(row, process, op['worker'].get('creation_time'), ancestors)), None)
        if row is None:
            row = {'pid': process.pid, 'runner': op['kind'], 'cmdline': ''}
            output.append(row)
        row.update(operation_id=op['id'], operation_revision=op['revision'], operation_context=public(op)['context'], operation=public(op))
        row['stop_flag'] = (Path(root) / 'logs' / op['context']['instance'] / 'stop_after_run').exists()
    return output
