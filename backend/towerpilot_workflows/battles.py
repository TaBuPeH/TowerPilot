"""Transactional battle identity and command history, in the operations database.

No device imports, scheduler, or action replay. Outcome defaults to unknown.
The driver's generation and operation are fencing tokens, never UI claims.
"""
import copy
import json
import time
import uuid

from towerpilot_workflows.control_settings import documents, flatten, merge_patch, validate_patch
from towerpilot_workflows.operations import Conflict, Operations, digest, has_table, readonly

LIVE = ('prepared', 'active')
TERMINAL_COMMANDS = ('applied', 'completed', 'failed', 'rejected', 'expired')


def _live_rows(db, device):
    query = "SELECT body FROM battles WHERE status IN ('prepared','active')"
    params = ()
    if device is not None:
        query += ' AND device=?'
        params = (device,)
    return [json.loads(row[0]) for row in db.execute(query, params).fetchall()]


class Battles(Operations):
    def __init__(self, path):
        super().__init__(path)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS battles (
                id TEXT PRIMARY KEY, device TEXT NOT NULL, status TEXT NOT NULL,
                creation_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL)''')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_live_battle_per_device
                ON battles(device) WHERE status IN ('prepared','active')''')
            db.execute('''CREATE TABLE IF NOT EXISTS battle_commands (
                id TEXT PRIMARY KEY, battle_id TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, body TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS battle_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, battle_id TEXT NOT NULL, body TEXT NOT NULL)''')

    @staticmethod
    def _battle(db, battle_id):
        row = db.execute('SELECT body FROM battles WHERE id=?', (battle_id,)).fetchone()
        if row is None:
            raise KeyError(battle_id)
        return json.loads(row[0])

    @staticmethod
    def _save(db, battle):
        battle['updated_at'] = time.time()
        db.execute('UPDATE battles SET status=?,body=? WHERE id=?',
                   (battle['status'], json.dumps(battle, sort_keys=True, allow_nan=False), battle['id']))

    @staticmethod
    def _event(db, battle, kind, **values):
        event = dict(battle_id=battle['id'], generation=battle['generation'],
                     envelope_hash=battle['envelope_hash'], kind=kind, timestamp=time.time(), **values)
        db.execute('INSERT INTO battle_events(battle_id,body) VALUES (?,?)',
                   (battle['id'], json.dumps(event, sort_keys=True, allow_nan=False)))

    @staticmethod
    def _scope(battle, generation, operation_id=None, live=True):
        if generation != battle['generation'] or (operation_id is not None and operation_id != battle['operation_id']):
            raise Conflict('Stale battle driver or operation')
        if live and battle['status'] not in LIVE:
            raise Conflict('Battle has ended')

    @staticmethod
    def _owner(db, battle):
        row = db.execute('SELECT body FROM operations WHERE id=?', (battle['operation_id'],)).fetchone()
        if row is None:
            raise Conflict('Driver operation is missing')
        operation = json.loads(row[0])
        if operation['status'] not in ('accepted', 'running'):
            raise Conflict('Driver operation is not active')
        if operation['context']['device_key'] != battle['context']['device_key']:
            raise Conflict('Driver operation belongs to another device')
        for key in ('account', 'instance', 'serial'):
            if operation['context'].get(key) != battle['context'].get(key):
                raise Conflict('Driver context changed')

    def create(self, operation_id, context, envelope, *, generation, origin='started',
               previous_battle_id=None, idempotency_key=None):
        if not isinstance(generation, str) or not generation:
            raise ValueError('A driver generation is required')
        if origin not in ('started', 'adopted', 'partial_adoption'):
            raise ValueError('Unknown battle origin')
        context, envelope = copy.deepcopy(context), copy.deepcopy(envelope)
        # Reject non-JSON values and non-finite data before acquiring ownership.
        json.dumps(envelope, allow_nan=False)
        envelope.setdefault('controls', {})
        key = idempotency_key or str(uuid.uuid4())
        fingerprint = digest(dict(operation_id=operation_id, context=context,
                                  envelope=envelope, generation=generation, origin=origin,
                                  previous_battle_id=previous_battle_id))
        with self._db() as db:
            row = db.execute('SELECT body,fingerprint FROM battles WHERE creation_key=?', (key,)).fetchone()
            if row:
                if row[1] != fingerprint:
                    raise Conflict('Battle creation key reused for a different request')
                return json.loads(row[0])
            now = time.time()
            battle = dict(id=str(uuid.uuid4()), operation_id=operation_id, context=context,
                          generation=generation, origin=origin, previous_battle_id=previous_battle_id,
                          envelope=envelope, envelope_hash=digest(envelope), status='prepared',
                          created_at=now, prepared_at=now, observed_started_at=None,
                          start_certainty='unknown' if origin != 'started' else 'prepared',
                          ended_at=None, outcome='unknown', reason=None, updated_at=now,
                          control_revision=0, applied_revision=0, overrides={}, applied_overrides={},
                          requested_controls=copy.deepcopy(envelope['controls']),
                          applied_controls=copy.deepcopy(envelope['controls']),
                          capabilities={'controls': [], 'actions': []}, runtime={},
                          segments=[dict(generation=generation, operation_id=operation_id,
                                         started_at=now, ended_at=None)])
            self._owner(db, battle)
            if previous_battle_id:
                previous = self._battle(db, previous_battle_id)
                if previous['context']['device_key'] != context['device_key']:
                    raise Conflict('Adoption link belongs to another device')
            if db.execute("SELECT id FROM battles WHERE device=? AND status IN ('prepared','active')", (context['device_key'],)).fetchone():
                raise Conflict('A live battle already owns this device')
            db.execute('INSERT INTO battles VALUES (?,?,?,?,?,?)',
                       (battle['id'], context['device_key'], 'prepared', key, fingerprint, json.dumps(battle, sort_keys=True)))
            self._event(db, battle, 'prepared')
            return battle

    def get(self, battle_id):
        with self._db() as db:
            return self._battle(db, battle_id)

    def validate_driver(self, battle_id, generation, operation_id=None):
        """Read and fence a driver without activating or updating its battle."""
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation, operation_id)
            self._owner(db, battle)
            return battle

    def reconcile_driver(self, operation_id, device, generation, *, reason):
        """Interrupt only the exited driver's remaining live observations.

        Repeated runs share a generation; previously observed ends retain their
        evidence and a late process handle cannot retire a successor driver.
        """
        with self._db() as db:
            rows = db.execute("SELECT body FROM battles WHERE device=? AND status IN ('prepared','active')",
                              (device,)).fetchall()
            finished = []
            for row in rows:
                battle = json.loads(row[0])
                if battle['operation_id'] == operation_id and battle['generation'] == generation:
                    finished.append(self._finish(db, battle, reason=reason, status='interrupted'))
            return finished

    def active(self, device=None):
        with self._db() as db:
            return _live_rows(db, device)

    @staticmethod
    def active_read_only(path, device=None):
        """`active` for a GET: reads the ledger file as it is. No file, or no battles table yet, is no live battle; nothing is created."""
        with readonly(path) as db:
            return [] if db is None or not has_table(db, 'battles') else _live_rows(db, device)

    def list(self, limit=50, offset=0, device=None, status=None, context=None):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid battle pagination')
        if status is not None and status not in ('prepared', 'active', 'ended', 'interrupted'):
            raise ValueError('Invalid battle status filter')
        if context is not None and (not isinstance(context, dict)
                or set(context) - {'device_key', 'instance', 'account', 'serial'}):
            raise ValueError('Invalid battle context filter')
        with self._db() as db:
            query = 'SELECT body FROM battles'
            params, conditions = [], []
            if device is not None:
                conditions.append('device=?')
                params.append(device)
            if status is not None:
                conditions.append('status=?')
                params.append(status)
            for key, value in (context or {}).items():
                if value is not None and not isinstance(value, str):
                    raise ValueError('Invalid battle context value')
                # Keys are allowlisted above; IS includes explicitly unbound
                # account/serial values, and filtering precedes pagination.
                conditions.append(f"json_extract(body, '$.context.{key}') IS ?")
                params.append(value)
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
            query += ' ORDER BY rowid DESC LIMIT ? OFFSET ?'
            return [json.loads(row[0]) for row in db.execute(query, (*params, limit, offset)).fetchall()]

    def activate(self, battle_id, generation, capabilities=None):
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation)
            self._owner(db, battle)
            if capabilities is not None:
                if set(capabilities) - {'controls', 'actions'} or any(not isinstance(v, list) or any(not isinstance(k, str) for k in v) for v in capabilities.values()):
                    raise ValueError('Invalid driver capabilities')
                battle['capabilities'] = copy.deepcopy(capabilities)
            if battle['status'] == 'prepared':
                battle.update(status='active', observed_started_at=time.time(),
                              start_certainty='observed_entry' if battle['origin'] == 'started' else 'partial_observation')
                self._event(db, battle, 'active')
            self._save(db, battle)
            return battle

    def update_status(self, battle_id, generation, status):
        """Persist driver observation/capture references, including after finalization."""
        if not isinstance(status, dict):
            raise ValueError('Runtime status must be a mapping')
        json.dumps(status, allow_nan=False)
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation, live=False)
            battle['runtime'].update(copy.deepcopy(status))
            self._save(db, battle)
            self._event(db, battle, 'status', status=status)
            return battle

    @staticmethod
    def _replay(db, key, fingerprint):
        row = db.execute('SELECT body,fingerprint FROM battle_commands WHERE request_key=?', (key,)).fetchone()
        if row:
            if row[1] != fingerprint:
                raise Conflict('Idempotency key reused for another command')
            return json.loads(row[0])
        return None

    @staticmethod
    def controls_fingerprint(battle_id, operation_id, generation, expected_revision, patch):
        return digest(dict(battle_id=battle_id, operation_id=operation_id, generation=generation,
                           expected_revision=expected_revision, patch=validate_patch(patch)))

    @staticmethod
    def action_fingerprint(battle_id, operation_id, generation, action, expected_revision=None):
        return digest(dict(battle_id=battle_id, operation_id=operation_id, generation=generation,
                           action=action, expected_revision=expected_revision))

    def recorded_command(self, idempotency_key, fingerprint):
        """The command an identical earlier request recorded, or None. Read-only.

        The fingerprint binds battle, operation, generation and payload, so a
        receipt is returned only to the exact request that was recorded; the
        same key with another request is a Conflict.
        """
        with self._db() as db:
            return self._replay(db, idempotency_key, fingerprint)

    def _command(self, db, battle, kind, payload, key, fingerprint):
        command = dict(id=str(uuid.uuid4()), battle_id=battle['id'], operation_id=battle['operation_id'],
                       generation=battle['generation'], kind=kind, payload=copy.deepcopy(payload),
                       revision=battle['control_revision'], state='requested', applied_paths=[],
                       reason=None, evidence=None, requested_at=time.time(), updated_at=time.time())
        db.execute('INSERT INTO battle_commands VALUES (?,?,?,?,?)',
                   (command['id'], battle['id'], key, fingerprint, json.dumps(command, sort_keys=True)))
        self._event(db, battle, 'command_requested', command=command)
        return command

    def request_controls(self, battle_id, operation_id, generation, expected_revision, patch, idempotency_key,
                         admit=None):
        """Record one revisioned control command. admit(db, battle), when given,
        runs inside the same BEGIN IMMEDIATE transaction as the insert, after an
        identical retry has been answered from its receipt, and may raise
        Conflict to refuse. A decision another writer can change (a person's
        later choice) is therefore re-checked atomically with the command, not
        before it."""
        patch = validate_patch(patch)
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError('An idempotency key is required')
        fingerprint = self.controls_fingerprint(battle_id, operation_id, generation, expected_revision, patch)
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation, operation_id)
            self._owner(db, battle)
            replay = self._replay(db, idempotency_key, fingerprint)
            if replay:
                return replay
            if type(expected_revision) is not int or expected_revision != battle['control_revision']:
                raise Conflict('Control revision changed')
            if admit is not None:
                admit(db, battle)
            unsupported = set(flatten(patch)) - set(battle['capabilities'].get('controls', []))
            if unsupported:
                raise ValueError('Unsupported controls: ' + ', '.join(sorted(unsupported)))
            # Only one unapplied control revision at a time prevents an older
            # delayed acknowledgement from overwriting newer installed gates.
            for row in db.execute('SELECT body FROM battle_commands WHERE battle_id=?', (battle_id,)):
                pending = json.loads(row[0])
                if pending['kind'] == 'controls' and pending['state'] not in TERMINAL_COMMANDS:
                    raise Conflict('Previous control revision is still pending')
            battle['control_revision'] += 1
            self._set_overrides(battle['overrides'], patch)
            battle['requested_controls'] = merge_patch(battle['envelope']['controls'], battle['overrides'])
            validate_patch(battle['requested_controls'])
            command = self._command(db, battle, 'controls', patch, idempotency_key, fingerprint)
            self._save(db, battle)
            return command

    @staticmethod
    def _set_overrides(target, patch):
        for key, value in patch.items():
            if key in documents() and value is not None:
                target[key] = copy.deepcopy(value)
            elif isinstance(value, dict):
                Battles._set_overrides(target.setdefault(key, {}), value)
            elif value is None:
                target.pop(key, None)
            else:
                target[key] = copy.deepcopy(value)

    def request_action(self, battle_id, operation_id, generation, action, idempotency_key, expected_revision=None):
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError('An idempotency key is required')
        fingerprint = self.action_fingerprint(battle_id, operation_id, generation, action, expected_revision)
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation, operation_id)
            self._owner(db, battle)
            replay = self._replay(db, idempotency_key, fingerprint)
            if replay:
                return replay
            if expected_revision is not None and (type(expected_revision) is not int or expected_revision != battle['control_revision']):
                raise Conflict('Control revision changed')
            if action not in battle['capabilities'].get('actions', []):
                raise ValueError('Unsupported manual action')
            for row in db.execute('SELECT body FROM battle_commands WHERE battle_id=?', (battle_id,)):
                pending = json.loads(row[0])
                if pending['kind'] == 'action' and pending['state'] not in TERMINAL_COMMANDS:
                    if pending['payload']['action'] == action:
                        # Persist alias keys too, so their later conflicting replay
                        # cannot silently attach to a different action.
                        db.execute('INSERT INTO battle_commands VALUES (?,?,?,?,?)',
                                   (str(uuid.uuid4()), battle_id, idempotency_key, fingerprint, json.dumps(pending, sort_keys=True)))
                        return pending
                    raise Conflict('Another manual action is pending or active')
            return self._command(db, battle, 'action', {'action': action}, idempotency_key, fingerprint)

    def pending_commands(self, battle_id, generation):
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation)
            rows = db.execute('SELECT body FROM battle_commands WHERE battle_id=? ORDER BY rowid', (battle_id,)).fetchall()
            commands = {c['id']: c for c in (json.loads(row[0]) for row in rows)}
            return [c for c in commands.values() if c['state'] not in TERMINAL_COMMANDS]

    def commands(self, battle_id):
        """Current command states, deduplicating idempotency aliases.

        Each command lists request_keys: every client idempotency key recorded
        for it, in recording order (an action alias adds a key to the command it
        joined). Keys are client-chosen request identities, not credentials. A
        row joins a command only when it names the same command, battle,
        operation and generation; anything else is left out rather than mixed in.
        """
        with self._db() as db:
            battle = self._battle(db, battle_id)
            rows = db.execute('SELECT request_key,body FROM battle_commands WHERE battle_id=? ORDER BY rowid',
                              (battle_id,)).fetchall()
        # Commands are only ever created under their battle's own operation and
        # generation (_scope), so the battle row is the reference identity.
        identity = dict(battle_id=battle_id, operation_id=battle['operation_id'], generation=battle['generation'])
        commands, keys = {}, {}
        for key, raw in rows:
            command = json.loads(raw)
            if any(command.get(field) != value for field, value in identity.items()):
                continue
            commands[command['id']] = command   # aliases hold the same current body
            keys.setdefault(command['id'], []).append(key)
        return [dict(command, request_keys=keys[command_id]) for command_id, command in commands.items()]

    def acknowledge(self, battle_id, generation, command_id, *, applied_paths=None,
                    state='applied', reason=None, evidence=None):
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation)
            self._owner(db, battle)
            row = db.execute('SELECT body FROM battle_commands WHERE id=? AND battle_id=?', (command_id, battle_id)).fetchone()
            if not row:
                raise KeyError(command_id)
            command = json.loads(row[0])
            if command['generation'] != generation:
                raise Conflict('Stale command generation')
            allowed = ('pending', 'applied', 'rejected', 'expired') if command['kind'] == 'controls' else ('started', 'completed', 'failed', 'rejected', 'expired')
            if state not in allowed:
                raise ValueError('Invalid acknowledgement state')
            if command['state'] in TERMINAL_COMMANDS:
                if command['state'] != state or command['reason'] != reason or command['evidence'] != evidence:
                    raise Conflict('Command already finalized')
                return command
            if command['kind'] == 'controls':
                paths = flatten(command['payload'])
                applied = set(paths) if applied_paths is None and state == 'applied' else set(applied_paths or [])
                if applied - set(paths):
                    raise ValueError('Acknowledgement contains unrequested controls')
                acknowledged = set(command['applied_paths']) | applied
                for path in applied:
                    nested = {}
                    cursor = nested
                    pieces = path.split('.')
                    for key in pieces[:-1]:
                        cursor = cursor.setdefault(key, {})
                    cursor[pieces[-1]] = paths[path]
                    self._set_overrides(battle['applied_overrides'], nested)
                battle['applied_controls'] = merge_patch(battle['envelope']['controls'], battle['applied_overrides'])
                command['applied_paths'] = sorted(acknowledged)
                if state in ('pending', 'applied'):
                    state = 'applied' if acknowledged == set(paths) else 'pending'
                if state == 'applied':
                    battle['applied_revision'] = command['revision']
                elif state in ('rejected', 'expired'):
                    battle['overrides'] = copy.deepcopy(battle['applied_overrides'])
                    battle['requested_controls'] = copy.deepcopy(battle['applied_controls'])
            elif state == 'completed' and command['state'] != 'started':
                raise Conflict('Manual action must start before completion')
            command.update(state=state, reason=reason, evidence=copy.deepcopy(evidence), updated_at=time.time())
            db.execute('UPDATE battle_commands SET body=? WHERE battle_id=? AND (id=? OR json_extract(body,\'$.id\')=?)',
                       (json.dumps(command, sort_keys=True, allow_nan=False), battle_id, command_id, command_id))
            self._event(db, battle, 'command_acknowledged', command=command)
            self._save(db, battle)
            return command

    def finish(self, battle_id, generation, *, reason, outcome='unknown', status='ended', evidence=None):
        if status not in ('ended', 'interrupted') or outcome not in ('unknown', 'death', 'surrendered'):
            raise ValueError('Invalid battle finalization')
        if status == 'interrupted' and outcome != 'unknown':
            raise ValueError('Interrupted observation has unknown outcome')
        with self._db() as db:
            battle = self._battle(db, battle_id)
            self._scope(battle, generation, live=False)
            return self._finish(db, battle, reason=reason, outcome=outcome, status=status, evidence=evidence)

    def _finish(self, db, battle, *, reason, outcome='unknown', status='ended', evidence=None):
        fingerprint = digest(dict(reason=reason, outcome=outcome, status=status, evidence=evidence))
        if battle['status'] not in LIVE:
            if battle.get('finalization_fingerprint') != fingerprint:
                raise Conflict('Battle already finalized with different evidence')
            return battle
        now = time.time()
        battle.update(status=status, outcome=outcome, reason=reason, ended_at=now,
                      boundary_evidence=copy.deepcopy(evidence), finalization_fingerprint=fingerprint)
        battle['segments'][-1]['ended_at'] = now
        for row in db.execute('SELECT id,body FROM battle_commands WHERE battle_id=?', (battle['id'],)).fetchall():
            command = json.loads(row[1])
            if command['state'] not in TERMINAL_COMMANDS:
                command.update(state='expired', reason='battle_ended', updated_at=now)
                db.execute('UPDATE battle_commands SET body=? WHERE id=?', (json.dumps(command, sort_keys=True), row[0]))
                self._event(db, battle, 'command_expired', command=command)
        self._save(db, battle)
        self._event(db, battle, status, outcome=outcome, reason=reason, evidence=evidence)
        return battle

    def events(self, battle_id):
        with self._db() as db:
            self._battle(db, battle_id)
            rows = db.execute('SELECT sequence,body FROM battle_events WHERE battle_id=? ORDER BY sequence', (battle_id,)).fetchall()
            return [dict(json.loads(row[1]), sequence=row[0]) for row in rows]

    def history(self, battle_id):
        return self.events(battle_id)
