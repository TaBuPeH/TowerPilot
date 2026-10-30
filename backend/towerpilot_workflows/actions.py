"""Durable history of timed-action executions, in the operations database (roadmap item 6).

One table, `action_executions`: a window (gems), a sweep (shopping), a launch attempt (harvest), or any later
action's execution. Written only by `begin` and `finish` (each a `BEGIN IMMEDIATE` compare-and-set), by the narrow
coalescing of `record_check`, and by settlement. Nothing here imports a device, adb or capture module, and
nothing replays an action: a row left open by a dead host is closed `interrupted` only on an exact authoritative
fence (its operation reads terminal, or its battle ended or moved to another generation), never on a guess and
never from a GET.

Constructing `Actions` creates the schema, so only mutations and startup construct it. Every read goes through
`operations.readonly` and never creates the file, a table or a row.

A stored row is trusted only after `usable` has read it: bad JSON, a shape no writer of this build makes (a null `host`, a
missing `trigger` or `outcome`: EVERY field is checked, not the ones a writer happens to read), or a body that disagrees with
the row's own index columns (`indexed(body)` is what the writer stored there) is a damaged row. A read skips it and counts it,
a settlement closes its index entry when its host is provably gone (the fence reads the indexed columns, never the body) and
leaves the body as it is, and a writer refuses to interpret it (`UnusableRow`). A page continues from the index values of the
last row it returned, never from a body. A damaged row can therefore never raise into a driver or the refresher, or move a page.
Every mutation first checks the ledger's schema version inside its own transaction, so a ledger written by a newer
build is never changed by this one.
"""
import json
import math
import time
import uuid

from towerpilot_workflows.action_contract import COVERAGES, EFFECTS, OPEN_STATES, RESULTS, SCHEMA_VERSION, TRIGGERS
from towerpilot_workflows.action_history import OUTCOME_KEYS, RECORD_KEYS, duration_or_none, finite_or_none, is_whole
from towerpilot_workflows.operations import ACTIVE, Conflict, Operations, digest, has_table, readonly

_ANY = object()                  # "no account filter" (None is a real account: the rows with none)
LIVE_BATTLE = ('prepared', 'active')
RETAIN_SEC = 30 * 86400          # terminal detail kept at least this long; pruning is not activated by this module
PRUNE_BATCH = 500


class ActiveExecution(Conflict):
    """The configured instance already has an execution that is still open."""
    code = 'action_active'


class SchemaTooNew(Conflict):
    """The ledger was written by a newer build. This build reads what it can and writes nothing."""
    code = 'action_schema_newer'


class UnusableRow(Conflict):
    """A stored row this build cannot read as an execution; it is left as it is."""
    code = 'action_row_unusable'


def _strict(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


INDEX = ('id', 'device', 'action', 'instance', 'account', 'operation_id', 'battle_id', 'generation', 'request_key', 'started_at', 'terminal')
INDEX_SQL = ', '.join(INDEX)


def indexed(body):
    """The index columns of a row, derived from its body: the one place that says what a writer stores beside the body."""
    host = body['host']
    return dict(id=body['id'], device=body['device'], action=body['action'], instance=body['instance'],
                account=body['context'].get('account'), operation_id=host.get('operation_id'), battle_id=host.get('battle_id'),
                generation=host.get('generation'), request_key=body.get('request_key'), started_at=float(body['started_at']),
                terminal=int(body['state'] not in OPEN_STATES))


def index_of(row):
    """The index columns of a selected row (the first len(INDEX) columns, in INDEX order) as a mapping."""
    return dict(zip(INDEX, row))


def usable(raw, index=None):
    """The body a stored row holds, when it is one this build can interpret; None for a damaged row.

    Damaged is: not JSON, not a whole body (`_shape_is_whole`), or - when the caller knows the row it read and passes its
    `index` (index_of) - a body that does not agree with the columns the row is found by (an update that would land on the wrong
    row, a page that would be ordered or continued by a value the index does not hold, an open row the body says is over)."""
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not is_whole(body):
        return None
    if index is not None and indexed(body) != index:
        return None
    return body


def new_record(*, device, action, instance, trigger, occurrence_key=None, host=None, context=None, request_key=None,
               command_id=None, config_revision=None, policy_revision=None, scheduled_for=None, started_at=None,
               refs=None, coverage='full'):
    """The body of a running execution. Missing evidence stays None; nothing is invented."""
    if trigger not in TRIGGERS:
        raise ValueError(f'Unknown trigger: {trigger}')
    if coverage not in COVERAGES:
        raise ValueError(f'Unknown coverage: {coverage}')
    host = {**dict(driver=None, operation_id=None, battle_id=None, generation=None, parent_execution=None), **(host or {})}
    return dict(schema_version=SCHEMA_VERSION, id=uuid.uuid4().hex, ledger_id=None, device=device, action=action,
                instance=instance, trigger=trigger, occurrence_key=occurrence_key, host=host, context=dict(context or {}),
                config_revision=config_revision, policy_revision=policy_revision, request_key=request_key,
                command_id=command_id, state='running', scheduled_for=finite_or_none(scheduled_for), accepted_at=None,
                started_at=finite_or_none(started_at) if started_at is not None else time.time(), finished_at=None,
                duration_ms=None, duration_basis='none',
                outcome=dict(result=None, reason=None, effect='none', attempts=0, confirmed=0, evidence=[]),
                aggregate=None, coverage=coverage, refs=dict(refs or {}))


class Actions(Operations):
    def __init__(self, path):
        super().__init__(path)
        self.unusable = 0                    # damaged rows the last settle_open met
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS action_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1), schema_version INTEGER NOT NULL, ledger_id TEXT NOT NULL,
                created_at REAL NOT NULL)''')
            db.execute('INSERT OR IGNORE INTO action_meta(id, schema_version, ledger_id, created_at) VALUES (1, ?, ?, ?)',
                       (SCHEMA_VERSION, uuid.uuid4().hex, time.time()))
            db.execute('''CREATE TABLE IF NOT EXISTS action_executions (
                id TEXT PRIMARY KEY, device TEXT NOT NULL, action TEXT NOT NULL, instance TEXT NOT NULL, account TEXT,
                operation_id TEXT, battle_id TEXT, generation TEXT, request_key TEXT, started_at REAL NOT NULL,
                terminal INTEGER NOT NULL, body TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS action_executions_by_device ON action_executions(device, started_at)')
            db.execute('CREATE INDEX IF NOT EXISTS action_executions_by_account ON action_executions(device, account, started_at)')
            db.execute('CREATE INDEX IF NOT EXISTS action_executions_by_operation ON action_executions(operation_id)')
            db.execute('CREATE INDEX IF NOT EXISTS action_executions_by_battle ON action_executions(battle_id)')
            db.execute('CREATE INDEX IF NOT EXISTS action_executions_by_state ON action_executions(terminal, started_at)')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_open_execution_per_instance
                ON action_executions(device, instance) WHERE terminal = 0''')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_execution_per_request_key
                ON action_executions(request_key) WHERE request_key IS NOT NULL''')

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _meta(db):
        row = db.execute('SELECT schema_version, ledger_id, created_at FROM action_meta WHERE id=1').fetchone()
        return (dict(schema_version=row[0], ledger_id=row[1], created_at=row[2]) if row
                else dict(schema_version=None, ledger_id=None, created_at=None))

    @staticmethod
    def _writable(meta):
        if meta['schema_version'] is not None and meta['schema_version'] > SCHEMA_VERSION:
            raise SchemaTooNew(f"Action history schema {meta['schema_version']} is newer than this build's {SCHEMA_VERSION}")

    @staticmethod
    def _insert(db, body):
        columns = indexed(body)
        db.execute(f'INSERT INTO action_executions({INDEX_SQL}, body) VALUES ({", ".join("?" for _ in INDEX)}, ?)',
                   (*(columns[name] for name in INDEX), _strict(body)))

    @staticmethod
    def _update(db, body):
        """Only the state and the body change: every other index column is what the body says and stays so (a field the index
        is derived from is never edited after insert)."""
        db.execute('UPDATE action_executions SET terminal=?, body=? WHERE id=?', (indexed(body)['terminal'], _strict(body), body['id']))

    @staticmethod
    def _fenced(db, operation_id, battle_id, generation):
        """Is the host of an open row provably gone? Operation terminal, or its battle ended or replaced. It reads the
        row's indexed host columns, never its body, so a damaged body cannot break it; a battle row it cannot read is
        not proof of anything."""
        if operation_id:
            row = db.execute('SELECT status FROM operations WHERE id=?', (operation_id,)).fetchone()
            if row is not None and row[0] not in ACTIVE:
                return 'operation_ended'
        if battle_id and has_table(db, 'battles'):
            row = db.execute('SELECT body FROM battles WHERE id=?', (battle_id,)).fetchone()
            if row is not None:
                try:
                    battle = json.loads(row[0])
                except (TypeError, ValueError):
                    return None
                if not isinstance(battle, dict):
                    return None
                if battle.get('status') not in LIVE_BATTLE:
                    return 'battle_ended'
                if generation and battle.get('generation') != generation:
                    return 'generation_replaced'
        return None

    @staticmethod
    def _close_damaged(db, execution_id):
        """A damaged row whose host is gone stops blocking its instance. Its body is not touched: nothing can say what it was."""
        db.execute('UPDATE action_executions SET terminal=1 WHERE id=?', (execution_id,))

    def _settle(self, db, body, reason, now):
        """Close an open row whose host is gone. finished_at and duration stay unknown: nothing measured them."""
        body = json.loads(_strict(body))
        body.update(state='interrupted', settled_at=now, settled_reason=reason)
        body['outcome'].update(result='interrupted', reason=reason, effect='unknown')
        self._update(db, body)
        return body

    # ---------------------------------------------------------- mutations
    def begin(self, body):
        """Insert a running execution. A repeated durable request key returns the row it already made. An open
        execution of the same instance is refused unless its host is provably gone, in which case it is closed
        `interrupted` first."""
        now = time.time()
        with self._db() as db:
            meta = self._meta(db)
            self._writable(meta)
            if body.get('request_key'):
                made = self._made_by(db, body['request_key'])
                if made is not None:
                    return made
            open_row = db.execute(f'SELECT {INDEX_SQL}, body FROM action_executions WHERE device=? AND instance=? AND terminal=0',
                                  (body['device'], body['instance'])).fetchone()
            if open_row is not None:
                index = index_of(open_row)
                reason = self._fenced(db, index['operation_id'], index['battle_id'], index['generation'])
                if reason is None:
                    raise ActiveExecution(f"{body['action']} ({body['instance']}) already has an open execution")
                existing = usable(open_row[-1], index)
                if existing is None:
                    self._close_damaged(db, index['id'])
                else:
                    self._settle(db, existing, reason, now)
            body = dict(body, ledger_id=meta['ledger_id'])
            self._insert(db, body)
            return body

    @staticmethod
    def _made_by(db, request_key):
        """The row a durable request key already made (as a replay), None when it made none."""
        row = db.execute(f'SELECT {INDEX_SQL}, body FROM action_executions WHERE request_key=?', (request_key,)).fetchone()
        if row is None:
            return None
        made = usable(row[-1], index_of(row))
        if made is None:
            raise UnusableRow(f'The execution recorded for request key {request_key} cannot be read')
        return dict(made, replayed=True)

    def finish(self, execution_id, *, result, reason=None, effect='none', attempts=0, confirmed=0, evidence=(),
               finished_at=None, duration_ms=None, refs=None):
        """Compare-and-set on the open state: a repeat or a stale writer changes nothing. Returns the row."""
        if result not in RESULTS or effect not in EFFECTS:
            raise ValueError(f'Unknown result or effect: {result}/{effect}')
        duration = duration_or_none(duration_ms)
        with self._db() as db:
            self._writable(self._meta(db))
            row = db.execute(f'SELECT {INDEX_SQL}, body FROM action_executions WHERE id=?', (execution_id,)).fetchone()
            if row is None:
                raise KeyError(execution_id)
            body = usable(row[-1], index_of(row))
            if body is None:
                raise UnusableRow(f'Execution {execution_id} cannot be read')
            if body['state'] not in OPEN_STATES:
                return body
            body.update(state=result, finished_at=finite_or_none(finished_at) if finished_at is not None else time.time(),
                        duration_ms=duration, duration_basis='monotonic' if duration is not None else 'none')
            body['outcome'] = dict(result=result, reason=reason, effect=effect, attempts=int(attempts),
                                   confirmed=int(confirmed), evidence=list(evidence))
            body['refs'] = dict(body['refs'], **(refs or {}))
            self._update(db, body)
            return body

    def record_check(self, body, *, result, reason, aggregate=True):
        """A no-input check that ended `refused` or `no_op`, recorded as one terminal row. An automatic one shares
        a row only with the newest row of the same instance when that is an aggregate of the identical scope
        (action, instance, device, account, battle, generation, revisions, trigger, result, reason); anything
        else, a started, manual or key-linked execution included, is its own row."""
        if result not in ('refused', 'no_op'):
            raise ValueError('Only a refused or no-op check is recorded without a lifecycle')
        now = body['started_at']
        scope = dict(action=body['action'], instance=body['instance'], device=body['device'],
                     account=body['context'].get('account'), battle_id=body['host'].get('battle_id'),
                     generation=body['host'].get('generation'), config_revision=body.get('config_revision'),
                     policy_revision=body.get('policy_revision'), trigger=body['trigger'], result=result, reason=reason)
        scope_key = digest(scope)
        mergeable = aggregate and body['trigger'] == 'automatic' and not body.get('request_key')
        with self._db() as db:
            meta = self._meta(db)
            self._writable(meta)
            if mergeable:
                newest = db.execute(f'''SELECT {INDEX_SQL}, body FROM action_executions WHERE device=? AND action=? AND instance=?
                                        ORDER BY started_at DESC, rowid DESC LIMIT 1''',
                                    (body['device'], body['action'], body['instance'])).fetchone()
                row = None if newest is None else usable(newest[-1], index_of(newest))      # a damaged newest row is not an aggregate to join
                if row is not None:
                    if row.get('aggregate') and row['aggregate'].get('scope') == scope_key and row['state'] == result:
                        row['aggregate'].update(count=row['aggregate']['count'] + 1, last_at=now)
                        self._update(db, row)
                        return row
            body = dict(body, ledger_id=meta['ledger_id'], state=result, finished_at=now,
                        aggregate=dict(count=1, first_at=now, last_at=now, scope=scope_key) if mergeable else None)
            body['outcome'] = dict(result=result, reason=reason, effect='none', attempts=0, confirmed=0, evidence=[])
            self._insert(db, body)
            return body

    def record_terminal(self, body, *, result, reason=None, effect='none', attempts=0, confirmed=0, evidence=(),
                        finished_at=None, duration_ms=None):
        """An execution whose outcome is already known, written as one terminal row in one transaction (a launch
        attempt, a sweep that failed before any input). It is never open, so a dead host cannot leave it behind.
        A repeated durable request key returns the row it already made."""
        if result not in RESULTS or effect not in EFFECTS:
            raise ValueError(f'Unknown result or effect: {result}/{effect}')
        duration = duration_or_none(duration_ms)
        with self._db() as db:
            meta = self._meta(db)
            self._writable(meta)
            if body.get('request_key'):
                made = self._made_by(db, body['request_key'])
                if made is not None:
                    return made
            body = dict(body, ledger_id=meta['ledger_id'], state=result,
                        finished_at=finite_or_none(finished_at) if finished_at is not None else body['started_at'],
                        duration_ms=duration, duration_basis='monotonic' if duration is not None else 'none')
            body['outcome'] = dict(result=result, reason=reason, effect=effect, attempts=int(attempts),
                                   confirmed=int(confirmed), evidence=list(evidence))
            self._insert(db, body)
            return body

    def settle_open(self, now=None, *, device=None):
        """Close every open row whose host is provably gone. Called by the existing refresher and the host's own
        finalization, never by a GET. Returns the ids it closed."""
        now = time.time() if now is None else now
        settled = []
        self.unusable = 0
        with self._db() as db:
            self._writable(self._meta(db))
            sql = f'SELECT {INDEX_SQL}, body FROM action_executions WHERE terminal=0'
            rows = db.execute(sql + (' AND device=?' if device else ''), (device,) if device else ()).fetchall()
            for row in rows:
                index = index_of(row)
                body = usable(row[-1], index)
                if body is None:
                    self.unusable += 1
                reason = self._fenced(db, index['operation_id'], index['battle_id'], index['generation'])
                if reason is None:
                    continue
                if body is None:
                    self._close_damaged(db, index['id'])
                else:
                    settled.append(self._settle(db, body, reason, now)['id'])
        return settled

    @staticmethod
    def _still_counting(raw, cutoff):
        """Is this old row an aggregate whose LAST check is newer than the retention cutoff? Its index keeps the FIRST check's start time, so the
        index alone would drop a count that is still growing. A body `usable` cannot read is not counting."""
        body = usable(raw)
        last = body['aggregate'].get('last_at') if body is not None and body['aggregate'] else None
        return finite_or_none(last) is not None and last >= cutoff

    def prune(self, now=None, *, retain_sec=RETAIN_SEC, batch=PRUNE_BATCH):
        """Delete terminal rows older than `retain_sec`, at most `batch` per call. Open rows and every row a durable
        command receipt names (`battle_commands.request_key`) are kept. Not called by any production path until root
        accepts the retention."""
        cutoff = (time.time() if now is None else now) - retain_sec
        with self._db() as db:
            self._writable(self._meta(db))
            has_commands = has_table(db, 'battle_commands')
            keep = ' AND (request_key IS NULL OR request_key NOT IN (SELECT request_key FROM battle_commands))' if has_commands else ''
            ids, limit = [], int(batch)
            candidates = db.execute(f'SELECT id, body FROM action_executions WHERE terminal=1 AND started_at < ?{keep} ORDER BY started_at', (cutoff,))
            for execution_id, raw in candidates:
                if limit == 0:                       # a batch of none deletes none
                    break
                if not self._still_counting(raw, cutoff):
                    ids.append(execution_id)
                    if 0 < limit <= len(ids):         # a negative batch is "no limit", as SQLite's LIMIT was
                        break
            candidates.close()
            db.executemany('DELETE FROM action_executions WHERE id=?', [(item,) for item in ids])
        return len(ids)

    # -------------------------------------------------------------- reads
    @staticmethod
    def read_meta(path):
        with readonly(path) as db:
            return None if db is None or not has_table(db, 'action_meta') else Actions._meta(db)

    @staticmethod
    def cursor(started_at, identity):
        """The continuation token that follows the row stored with this start time and id: the order a read sorts by. A start
        time alone cannot continue a page that ends inside a run of equal times. Only INDEX values go in (read_page passes the
        columns it sorted by): a body never decides where a page continues."""
        return f'{float(started_at)!r}:{identity}'

    @staticmethod
    def parse_cursor(value):
        """(started_at, id) of a cursor, or (started_at, None) of a bare time (every row strictly older). ValueError when it
        is neither."""
        stamp, separator, identity = str(value).partition(':')
        try:
            at = float(stamp)
        except ValueError:
            raise ValueError('before must be a start time or a cursor from a previous page') from None
        if not math.isfinite(at):
            raise ValueError('before must be a finite number')
        if separator and not identity:
            raise ValueError('before must be a start time or a cursor from a previous page')
        return at, (identity if separator else None)

    @staticmethod
    def read(path, **kwargs):
        """(rows newest first, truncated); see read_page."""
        rows, truncated, _, _ = Actions.read_page(path, **kwargs)
        return rows, truncated

    @staticmethod
    def read_page(path, *, device=None, action=None, instance=None, battle_id=None, operation_id=None, account=_ANY,
                  before=None, limit=50):
        """(rows newest first, truncated, how many damaged rows were skipped, the cursor after the last row returned or None).
        Never creates anything; a missing ledger or table reads as no history.

        Every filter, the account's included (`account=None` selects the rows with no account), is applied by the
        database BEFORE the limit, so a page is a page of the rows asked for. The order is (started_at, id) descending,
        a total order, and `before` is a cursor in it (see `cursor`). A damaged row - a body that is not whole, or that
        disagrees with the columns the row is ordered by - is skipped and counted and never ends a page early, and the
        cursor handed back is made of the index values of the last row returned, so following it visits every usable row
        exactly once whatever any body says."""
        limit = max(1, min(int(limit), 500))
        with readonly(path) as db:
            if db is None or not has_table(db, 'action_executions'):
                return [], False, 0, None
            clauses, args = [], []
            for column, value in (('device', device), ('action', action), ('instance', instance),
                                  ('battle_id', battle_id), ('operation_id', operation_id)):
                if value is not None:
                    clauses.append(f'{column}=?')
                    args.append(value)
            if account is not _ANY:
                clauses.append('account IS ?')
                args.append(account)
            position = None if before is None else Actions.parse_cursor(before)
            bodies, keys, damaged = [], [], 0
            while len(bodies) <= limit:
                scope, scope_args = list(clauses), list(args)
                if position is not None:
                    at, identity = position
                    if identity is None:
                        scope.append('started_at < ?')
                        scope_args.append(at)
                    else:
                        scope.append('(started_at < ? OR (started_at = ? AND id < ?))')
                        scope_args.extend((at, at, identity))
                where = ('WHERE ' + ' AND '.join(scope)) if scope else ''
                batch = db.execute(f'SELECT {INDEX_SQL}, body FROM action_executions {where} '
                                   'ORDER BY started_at DESC, id DESC LIMIT ?', (*scope_args, limit + 1)).fetchall()
                for row in batch:
                    index = index_of(row)
                    body = usable(row[-1], index)
                    if body is None:
                        damaged += 1
                    else:
                        bodies.append(body)
                        keys.append((index['started_at'], index['id']))
                if len(batch) <= limit:
                    break
                position = (batch[-1][INDEX.index('started_at')], batch[-1][INDEX.index('id')])
        truncated = len(bodies) > limit
        return bodies[:limit], truncated, damaged, (Actions.cursor(*keys[limit - 1]) if truncated else None)
