"""Pure, transactional local operation identity. No runtime settings imports.

Domain files remain authoritative. This ledger stores their references, never
copies of reports. Device actions are never replayed by reconciliation.
"""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ACTIVE = ('accepted', 'running', 'cancelling', 'applying')
SYNCHRONOUS_PERSISTENCE_KINDS = frozenset({'profile_write', 'profile_promote'})
STATUSES = {*ACTIVE, 'awaiting_apply', 'restoration_required', 'succeeded',
            'failed', 'interrupted', 'cancelled'}
EDGES = {
    'accepted': {'accepted', 'running', 'cancelling', 'failed', 'interrupted'},
    'running': {'running', 'cancelling', 'restoration_required', 'awaiting_apply', 'succeeded', 'failed', 'interrupted'},
    'cancelling': {'cancelled', 'restoration_required', 'failed', 'interrupted'},
    'awaiting_apply': {'applying'},
    'applying': {'applying', 'succeeded', 'awaiting_apply', 'interrupted', 'failed'},
    'failed': {'applying'}, 'interrupted': {'applying'},
    'restoration_required': set(), 'succeeded': set(), 'cancelled': set(),
}


class Conflict(ValueError):
    """A command no longer names the accepted target/revision. `code` is the stable word a client branches on; a
    refusal that has none leaves it None (control.operations.OperationRefused and the subclasses below name theirs)."""
    code = None


class IdempotencyKeyReused(Conflict):
    """The key belongs to another command or target."""
    code = 'idempotency_key_reused'


class DeviceBusy(Conflict):
    """Another operation is already accepted or active on the device."""
    code = 'operation_active'


@contextmanager
def readonly(path):
    """One consistent read-only snapshot of a ledger, or None when the file does
    not exist. Never creates the file, a table or a row: every GET path reads
    through this (or through a class that was constructed by a mutation)."""
    target = Path(os.path.abspath(path))
    if not target.is_file():
        yield None
        return
    db = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=15)
    try:
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def has_table(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _strict_worker(worker):
    """A worker identity is stored as strict JSON: NaN and Infinity never enter the ledger,
    because a creation time that is not a finite number identifies nothing."""
    json.dumps(worker, allow_nan=False)


def file_revision(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class Operations:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, device TEXT NOT NULL, kind TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
                revision INTEGER NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL)''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=15000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read(db, operation_id):
        row = db.execute('SELECT body FROM operations WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return json.loads(row[0])

    def get(self, operation_id):
        with self._db() as db:
            return self._read(db, operation_id)

    def find_by_key(self, idempotency_key):
        with self._db() as db:
            row = db.execute('SELECT body FROM operations WHERE idempotency_key=?',
                             (idempotency_key,)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _active_rows(db, device=None):
        placeholders = ','.join('?' for _ in ACTIVE)
        if device is None:
            rows = db.execute(f'SELECT body FROM operations WHERE status IN ({placeholders})', ACTIVE).fetchall()
        else:
            rows = db.execute(f'SELECT body FROM operations WHERE device=? AND status IN ({placeholders})', (device, *ACTIVE)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def active(self, device=None):
        with self._db() as db:
            return self._active_rows(db, device)

    @staticmethod
    def active_readonly(path, device=None):
        """The active operations of a ledger that may not exist yet: none, and
        nothing is created. For GET paths."""
        with readonly(path) as db:
            if db is None or not has_table(db, 'operations'):
                return []
            return Operations._active_rows(db, device)

    @staticmethod
    def _latest_of(db, context, kind):
        for row in db.execute('SELECT body FROM operations ORDER BY rowid DESC').fetchall():
            op = json.loads(row[0])
            if kind is not None and op['kind'] not in ((kind,) if isinstance(kind, str) else kind):
                continue
            if all(op['context'].get(k) == context.get(k) for k in ('instance', 'account', 'serial', 'profile', 'rendering')):
                return op
        return None

    def latest(self, context, kind=None):
        with self._db() as db:
            return self._latest_of(db, context, kind)

    @staticmethod
    def read_only(path, operation_id):
        """One operation exactly as recorded: never reconciled, nothing created
        (KeyError when the ledger or the row does not exist). For GET paths; the
        refresher and the POST paths are what retire a worker that died."""
        with readonly(path) as db:
            if db is None or not has_table(db, 'operations'):
                raise KeyError(operation_id)
            return Operations._read(db, operation_id)

    @staticmethod
    def latest_read_only(path, context, kind=None):
        """latest() for GET paths: as recorded, creating nothing."""
        with readonly(path) as db:
            if db is None or not has_table(db, 'operations'):
                return None
            return Operations._latest_of(db, context, kind)

    def accept(self, kind, context, command, idempotency_key=None, admit=None):
        """Record one operation. admit(db), when given, runs inside the same
        BEGIN IMMEDIATE transaction as the insert (after an identical retry has
        been answered from its receipt) and may raise Conflict to refuse: a
        decision another writer can change is therefore re-checked atomically
        with acceptance, not before it."""
        key = idempotency_key or str(uuid.uuid4())
        fingerprint = digest({'kind': kind, 'context': context, 'request': command})
        device = context['device_key']
        with self._db() as db:
            row = db.execute('SELECT body,fingerprint FROM operations WHERE idempotency_key=?', (key,)).fetchone()
            if row:
                if row[1] != fingerprint:
                    raise IdempotencyKeyReused('Idempotency key already belongs to another command or target')
                return json.loads(row[0]), False
            if admit is not None:
                admit(db)
            placeholders = ','.join('?' for _ in ACTIVE)
            if db.execute(f'SELECT id FROM operations WHERE device=? AND status IN ({placeholders})', (device, *ACTIVE)).fetchone():
                raise DeviceBusy('An operation already owns this device')
            now = time.time()
            op = dict(id=str(uuid.uuid4()), kind=kind, idempotency_key=key,
                      revision=1, status='accepted', context=context, request=command,
                      worker=None, step='accepted', created_at=now, updated_at=now,
                      result_refs={}, error=None, recovery_action=None, progress={})
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)',
                       (op['id'], device, kind, key, fingerprint, 1, 'accepted', json.dumps(op, sort_keys=True)))
            return op, True

    def transition(self, operation_id, expected_revision, status, *, guard=None, **changes):
        """Move one operation to `status` (compare-and-set on `expected_revision`). guard(op), when given,
        runs inside the same BEGIN IMMEDIATE transaction after the revision and the lifecycle edge were
        confirmed and before anything is written; it may raise Conflict to refuse. A fact that can change
        outside the ledger (a device lease) is therefore read at the moment of the write, not before it."""
        if status not in STATUSES:
            raise ValueError('Unknown operation status')
        if set(changes) - {'worker', 'step', 'result_refs', 'error', 'recovery_action', 'readiness', 'progress'}:
            raise ValueError('Immutable operation field')
        with self._db() as db:
            op = self._read(db, operation_id)
            if op['revision'] != expected_revision:
                raise Conflict('Operation revision changed; refresh before trying again')
            if op['kind'] in SYNCHRONOUS_PERSISTENCE_KINDS and status in ('cancelling', 'cancelled'):
                raise Conflict('Synchronous profile persistence cannot be cancelled')
            if status not in EDGES[op['status']]:
                raise Conflict('Invalid operation lifecycle transition')
            if status in ACTIVE:
                placeholders = ','.join('?' for _ in ACTIVE)
                if db.execute(f'SELECT id FROM operations WHERE device=? AND id<>? AND status IN ({placeholders})',
                              (op['context']['device_key'], operation_id, *ACTIVE)).fetchone():
                    raise DeviceBusy('An operation already owns this device')
            if guard is not None:
                guard(op)
            if 'worker' in changes:
                _strict_worker(changes['worker'])
            op.update(changes, status=status, revision=expected_revision + 1, updated_at=time.time())
            db.execute('UPDATE operations SET revision=?,status=?,body=? WHERE id=?',
                       (op['revision'], status, json.dumps(op, sort_keys=True), operation_id))
            return op

    def reconcile(self, operation_id, alive=None, *, policy=None):
        """Retire an operation whose worker is PROVEN gone. alive(worker) answers
        True, False (proven gone) or None (unknown); only False retires, so a
        worker whose liveness cannot be checked keeps its operation active.
        Safe to run from several dashboards at once: when another one (or the
        worker itself) moved the operation first, that newer state is the
        answer, not an error.

        policy(op) is the same question for a judgement that depends on more than the worker (the status
        the operation is in, its context): it is handed the very snapshot THIS call read and retires on
        that snapshot's revision, so what it judged and what is retired are one state. A caller must not
        judge a snapshot of its own and then pass only its worker here: the operation can move in between
        (a Stop turns running into cancelling) and the stale judgement would retire the new state. Give
        exactly one of alive and policy."""
        if (alive is None) == (policy is None):
            raise TypeError('reconcile takes exactly one of alive(worker) and policy(op)')
        judge = policy if policy is not None else (lambda snapshot: alive(snapshot.get('worker')))
        op = self.get(operation_id)
        if op['status'] in ('running', 'cancelling', 'applying') and judge(op) is False:
            retired = dict(error={'code': 'worker_lost', 'message': 'Worker exited without a confirmed outcome'},
                           recovery_action='Review saved observations and restoration state before retrying')
        elif op['status'] == 'accepted' and time.time() - op['updated_at'] > 30 and judge(op) is False:
            retired = dict(error={'code': 'launch_interrupted', 'message': 'No worker confirmed this launch'},
                           recovery_action='Review the device before starting a new operation')
        else:
            return op
        try:
            return self.transition(operation_id, op['revision'], 'interrupted', **retired)
        except Conflict:
            return self.get(operation_id)

    def launched(self, operation_id, worker):
        """Persist spawn identity without overwriting a worker's newer state."""
        _strict_worker(worker)
        with self._db() as db:
            op = self._read(db, operation_id)
            if op['status'] == 'accepted' and op['worker'] is None:
                op.update(worker=worker, revision=op['revision'] + 1, updated_at=time.time())
                db.execute('UPDATE operations SET revision=?,body=? WHERE id=?', (op['revision'], json.dumps(op, sort_keys=True), operation_id))
            return op
