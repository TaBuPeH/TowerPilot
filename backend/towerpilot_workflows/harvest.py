"""Durable collection-only handoffs and persistent gem-harvesting state.

Everything lives in the operations database beside the operation and battle
ledgers. No device imports, no process control, no scheduler: these tables only
record what a person asked for and what the lifecycle host decided. The
dashboard's lifecycle host reads them and launches the existing collector
through ordinary operation admission.

collection_handoffs: a person's explicit statement that the battle on screen
is a NEW manually started run, not the unresolved setup battle one exact sealed
record names. It authorizes collection only, on that device, account and
serial, and only while that same record is the decisive setup evidence. Failed
setup history is never rewritten.

harvest_choices: per emulator, the person's choice: on (with the run whose gem
settings to use), off, or no row at all (never chosen: runs keep their own
defaults). The revision changes ONLY when a person acts (or Stop pauses it).
Bookkeeping never touches it, so an automatic launch fenced on that revision is
invalidated by exactly the changes that should invalidate it.

harvest_runtime: reconciler bookkeeping for the current choice (a refusal's
recheck time, the last launch outcome, a run-transfer hold). Separate from the
choice by design. Consecutive failures and attempt numbers are not stored: they
are read from the operations themselves, so a crash between acceptance and
bookkeeping cannot lose or double count them.

harvest_receipts: one row per explicit On/Off request key, written in the same
transaction as the choice. An identical retry after a lost reply is answered
from its receipt even after the revision advanced; the same key with another
request is refused.

Reading for status goes through readonly(): a missing ledger or missing tables
mean "never chosen" and nothing is created. Schema is created only by the
mutating class below.
"""
import copy
import json
import math
import sqlite3
import time
import uuid

from towerpilot_workflows.battles import LIVE as LIVE_BATTLE
from towerpilot_workflows.operations import Conflict, Operations, digest, has_table, readonly

ATTESTATION = 'new_manual_battle'
CONTEXT_KEYS = ('instance', 'account', 'serial')
BOUND_KEYS = CONTEXT_KEYS + ('profile',)
BLOCKER_KEYS = ('operation_id', 'setup_battle_id', 'evidence_revision')
LAUNCH_PREFIX = 'harvest-'
OVERLAY_PREFIX = 'harvest-overlay-'
GEM_ROUTINES = ('ad_gems', 'floating_gems')
# What harvest_runtime holds. The host's bookkeeping may replace only BOOKKEEPING_KEYS (update_runtime);
# the run-takeover hold has its own writer (set_transfer) and the per-generation overlay decisions have
# theirs (freeze_overlay), so a stale host can never overwrite either through the generic path.
RUNTIME_KEYS = frozenset({'last_outcome', 'next_attempt_at', 'transfer', 'overlay_freezes'})
BOOKKEEPING_KEYS = frozenset({'last_outcome', 'next_attempt_at'})


class HarvestFenceRefused(Conflict):
    """An automatic collector launch no longer matches the person's choice."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class CollectorRefused(RuntimeError):
    """A harvest-managed collector found its choice gone before it sent input.

    Raised inside the worker; the operation records it as error code
    'CollectorRefused', which the lifecycle host does not count as a failure."""


class HarvestStale(Conflict):
    """Bookkeeping was planned under a choice revision that is no longer current."""


class HarvestRevisionChanged(Conflict):
    """Enabling named a revision that is no longer current."""

    def __init__(self, current):
        super().__init__('Harvesting settings changed; refresh before changing them again')
        self.current = current


class HarvestKeyReused(Conflict):
    def __init__(self):
        super().__init__('That request identity already belongs to a different harvesting request')


GEM_POLICY_KEYS = ('ad_gems', 'ad_gem_options', 'gem_delay_sec', 'gem_orbit')


def gem_policy_revision(preset, gather):
    """The identity of the gem settings a run carries: the revision the UI shows,
    the lifecycle host plans against, the request records, and the ledger and the
    worker recompute from the envelope the collector actually runs with."""
    gather = gather if isinstance(gather, dict) else {}
    return digest({'preset': preset, 'gather': {key: gather.get(key) for key in GEM_POLICY_KEYS}})


def envelope_policy_revision(command):
    """The policy revision of the frozen envelope inside an automatic launch's
    request, or None when it carries none (a hybrid cannot be proven consistent)."""
    envelope = command.get('battle_envelope')
    preset = envelope.get('preset') if isinstance(envelope, dict) else None
    if not isinstance(preset, dict):
        return None
    return gem_policy_revision(command.get('preset'), preset.get('gather'))


def blocker_key(device_key, context, blocker):
    """One identity per exact blocker on one device for one account/serial."""
    return digest(dict(device=device_key, **{key: context.get(key) for key in CONTEXT_KEYS},
                       **{key: blocker.get(key) for key in BLOCKER_KEYS}))


# ------------------------------------------------------------ read-only reads
# (readonly() and has_table() come from towerpilot_workflows.operations)
def read_choice(db, device):
    """The stored choice, None when never chosen. Present but malformed raises
    ValueError: an unreadable choice is never read as 'never chosen'."""
    if not has_table(db, 'harvest_choices'):
        return None
    row = db.execute('SELECT revision,body FROM harvest_choices WHERE device=?', (device,)).fetchone()
    if row is None:
        return None
    try:
        body = json.loads(row[1])
    except ValueError as exc:
        raise ValueError('Harvesting choice is not valid JSON') from exc
    if (not isinstance(body, dict) or body.get('revision') != row[0] or type(row[0]) is not int
            or type(body.get('enabled')) is not bool or body.get('device_key') != device):
        raise ValueError('Harvesting choice record is malformed')
    return body


def freeze_key(battle_id, generation):
    """One overlay decision per (battle, generation)."""
    return f'{battle_id}/{generation}'


def valid_targets(targets):
    return isinstance(targets, dict) and all(type(targets.get(name)) is bool for name in GEM_ROUTINES)


def valid_freeze(record, battle_id, generation):
    """A well-formed decision for exactly this (battle, generation). Anything else a
    record under its key might contain decides nothing."""
    return (isinstance(record, dict) and record.get('battle_id') == battle_id
            and record.get('generation') == generation and valid_targets(record.get('targets')))


def read_runtime(db, device, revision):
    """Bookkeeping for this choice revision; a run-transfer hold outlives revisions."""
    if not has_table(db, 'harvest_runtime'):
        return {}
    row = db.execute('SELECT body FROM harvest_runtime WHERE device=?', (device,)).fetchone()
    if row is None:
        return {}
    body = json.loads(row[0])
    if not isinstance(body, dict):
        raise ValueError('Harvesting bookkeeping is malformed')
    if body.get('for_revision') == revision:
        return body
    return {'transfer': body.get('transfer')} if body.get('transfer') else {}


def read_receipt(db, request_key):
    if not has_table(db, 'harvest_receipts'):
        return None
    row = db.execute('SELECT fingerprint,body FROM harvest_receipts WHERE request_key=?', (request_key,)).fetchone()
    return None if row is None else (row[0], json.loads(row[1]))


def launch_key(device_key, revision, attempt):
    return f'{LAUNCH_PREFIX}{device_key[:16]}-{revision}-{attempt}'


def automatic_operations(db, device_key, revision):
    """Every collector the lifecycle host launched for this choice revision, newest first."""
    pattern = f'{LAUNCH_PREFIX}{device_key[:16]}-{revision}-%'
    rows = db.execute('SELECT body FROM operations WHERE device=? AND kind=? AND idempotency_key LIKE ? '
                      'ORDER BY rowid DESC', (device_key, 'gems', pattern)).fetchall()
    return [json.loads(row[0]) for row in rows]


def verify_automatic_launch(db, context, command, now=None):
    """The admission fence: inside the acceptance transaction an automatic
    collector must still match the person's current, enabled, bound choice and
    no run transfer may be in progress."""
    now = time.time() if now is None else now
    device = context['device_key']
    try:
        choice = read_choice(db, device)
        runtime = read_runtime(db, device, choice['revision'] if choice else 0)
    except ValueError as exc:
        raise HarvestFenceRefused('harvest_unreadable', str(exc)) from exc
    if choice is None or choice['enabled'] is not True:
        raise HarvestFenceRefused('harvest_off', 'Harvesting is off; the collector was not started')
    if choice['revision'] != command.get('desire_revision') or choice.get('preset') != command.get('preset'):
        raise HarvestFenceRefused('harvest_changed', 'Harvesting settings changed before the collector was accepted')
    bound = choice.get('bound') or {}
    if any(bound.get(key) != context.get(key) for key in BOUND_KEYS):
        raise HarvestFenceRefused('context_changed', 'Account, profile or emulator changed since harvesting was turned on')
    transfer = runtime.get('transfer')
    if (isinstance(transfer, dict) and type(transfer.get('until')) in (int, float)
            and math.isfinite(transfer['until']) and transfer['until'] > now):
        raise HarvestFenceRefused('transfer_pending', 'A run is taking over the emulator; the collector was not started')
    # One policy end to end: the revision the host planned with must be the one
    # the frozen envelope (what the worker will actually run) carries. A slow
    # prepare during a settings edit would otherwise run a hybrid.
    if not isinstance(command.get('policy_revision'), str) or command['policy_revision'] != envelope_policy_revision(command):
        raise HarvestFenceRefused('policy_changed', "The run's gem settings changed while the collector was being prepared")


def verify_overlay(db, planned, battle):
    """The overlay fence: inside the transaction that records a gem-routine
    command on a full driver's battle, the choice the command was planned from
    must still be the person's current choice (same revision, same On/Off), and
    it must be bound to exactly the context the driver runs in. A person's
    later Off, a new run, or a profile/account change makes the stale plan
    refuse; the lifecycle host then recomputes from the new facts."""
    device = battle['context']['device_key']
    try:
        current = read_choice(db, device)
    except ValueError as exc:
        raise HarvestFenceRefused('harvest_unreadable', str(exc)) from exc
    if current is None or current['revision'] != planned['revision'] or current['enabled'] is not planned['enabled']:
        raise HarvestFenceRefused('harvest_changed', 'Harvesting settings changed before the gem command was recorded')
    if not context_matches(current.get('bound'), battle['context']):
        raise HarvestFenceRefused('context_changed', "The driver does not run in the context harvesting is set for")


def context_matches(bound, context):
    """True when a choice's bound account/profile/emulator are exactly the context's."""
    return isinstance(bound, dict) and isinstance(context, dict) and all(
        bound.get(key) == context.get(key) for key in BOUND_KEYS)


def automatic_launch_valid(path, operation, now=None):
    """(ok, code) for a running harvest-managed collector: its choice is still
    on, at the revision it was launched under, for the context it runs in."""
    context = operation['context']
    try:
        with readonly(path) as db:
            if db is None:
                return False, 'harvest_off'
            verify_automatic_launch(db, context, operation['request'], now)
    except HarvestFenceRefused as exc:
        return False, exc.code
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return False, 'harvest_unreadable'
    return True, None


class Harvest(Operations):
    """Mutating access. Constructing it creates the schema, so only mutations
    and startup construct it; status reads use readonly()."""

    def __init__(self, path):
        super().__init__(path)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS collection_handoffs (
                id TEXT PRIMARY KEY, device TEXT NOT NULL, blocker TEXT NOT NULL UNIQUE, body TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS harvest_choices (
                device TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS harvest_runtime (
                device TEXT PRIMARY KEY, body TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS harvest_receipts (
                request_key TEXT PRIMARY KEY, device TEXT NOT NULL, fingerprint TEXT NOT NULL,
                revision INTEGER NOT NULL, body TEXT NOT NULL)''')

    # ------------------------------------------------------------ handoffs
    def record_handoff(self, context, blocker, *, attestation, request_id=None):
        """Record (or return the existing) collection-only handoff. Pure storage:
        the caller has already proved that blocker is the current decisive
        setup evidence and that nothing else owns the device."""
        if attestation != ATTESTATION:
            raise ValueError('Confirm that the battle on screen is a new manually started run')
        if any(not isinstance(blocker.get(key), str) or not blocker[key] for key in BLOCKER_KEYS):
            raise ValueError('The handoff must name the exact setup record')
        device = context['device_key']
        key = blocker_key(device, context, blocker)
        with self._db() as db:
            row = db.execute('SELECT body FROM collection_handoffs WHERE blocker=?', (key,)).fetchone()
            if row:
                return json.loads(row[0]), False
            record = dict(id=str(uuid.uuid4()), device_key=device, scope='collection_only',
                          attestation=attestation, created_at=time.time(), request_id=request_id,
                          blocker={key_: blocker[key_] for key_ in BLOCKER_KEYS + ('recorded_status',)
                                   if key_ in blocker},
                          **{key_: context.get(key_) for key_ in CONTEXT_KEYS})
            db.execute('INSERT INTO collection_handoffs VALUES (?,?,?,?)',
                       (record['id'], device, key, json.dumps(record, sort_keys=True)))
            return record, True

    # ------------------------------------------------------------- choices
    def choice(self, device):
        with self._db() as db:
            return read_choice(db, device)

    def receipt(self, request_key):
        with self._db() as db:
            return read_receipt(db, request_key)

    def set_choice(self, context, *, enabled, preset, label, expected_revision, bound, reason,
                   changed_by, request_key, fingerprint, now=None):
        """Record the person's choice and its request receipt in ONE transaction.

        Returns (choice, replayed). An identical retry (same key and request) is
        answered from its receipt, whatever the revision is by then. Turning
        harvesting on is a compare-and-set on expected_revision; turning it off
        always applies. Raises HarvestKeyReused, HarvestRevisionChanged.
        """
        if type(enabled) is not bool:
            raise ValueError('enabled must be true or false')
        if not isinstance(request_key, str) or not request_key:
            raise ValueError('A request identity is required')
        now = time.time() if now is None else now
        device = context['device_key']
        with self._db() as db:
            receipt = read_receipt(db, request_key)
            if receipt is not None:
                if receipt[0] != fingerprint:
                    raise HarvestKeyReused()
                return receipt[1]['choice'], True
            current = read_choice(db, device)
            revision = current['revision'] if current else 0
            if enabled:
                if type(expected_revision) is not int:
                    raise ValueError('expected_revision is required to turn harvesting on')
                if expected_revision != revision:
                    raise HarvestRevisionChanged(current)
                if not isinstance(preset, str) or not preset or not isinstance(bound, dict):
                    raise ValueError("Choose a run's diamond settings")
            body = dict(device_key=device, revision=revision + 1, enabled=enabled,
                        preset=preset if enabled else (current or {}).get('preset'),
                        label=label if enabled else (current or {}).get('label'),
                        bound=copy.deepcopy(bound if enabled else (current or {}).get('bound') or bound),
                        changed_at=now, changed_by=changed_by, reason=reason, request_key=request_key)
            db.execute('INSERT OR REPLACE INTO harvest_choices VALUES (?,?,?)',
                       (device, body['revision'], json.dumps(body, sort_keys=True, allow_nan=False)))
            db.execute('INSERT INTO harvest_receipts VALUES (?,?,?,?,?)',
                       (request_key, device, fingerprint, body['revision'],
                        json.dumps(dict(choice=body), sort_keys=True, allow_nan=False)))
            return body, False

    # ------------------------------------------------------------- runtime
    def update_runtime(self, device, revision, **changes):
        """Reconciler bookkeeping (a refusal's recheck time, the last launch
        outcome) for ONE choice revision. Never changes the choice.

        The write is a compare-and-set on the choice: it lands only while
        `revision` is still the person's current revision, in the same
        transaction. A host that planned its step under an older revision (a
        slow prepare, a restarted dashboard) therefore cannot replace or erase
        the bookkeeping of the revision that replaced it. Raises
        HarvestStale, which callers treat as 'drop it'."""
        if set(changes) - BOOKKEEPING_KEYS:
            raise ValueError("Bookkeeping may not change the person's choice")
        with self._db() as db:
            current = read_choice(db, device)
            if current is None or current['revision'] != revision:
                raise HarvestStale('The choice changed; this bookkeeping belongs to an older revision')
            row = db.execute('SELECT body FROM harvest_runtime WHERE device=?', (device,)).fetchone()
            body = json.loads(row[0]) if row else {}
            if body.get('for_revision') != revision:
                body = {'for_revision': revision, 'transfer': body.get('transfer')}
            body.update(copy.deepcopy(changes))
            db.execute('INSERT OR REPLACE INTO harvest_runtime VALUES (?,?)',
                       (device, json.dumps(body, sort_keys=True, allow_nan=False)))
            return body

    def freeze_overlay(self, device, revision, battle_id, generation, targets):
        """Fix, once, what this choice revision applies to this battle generation.

        A compare-and-set on everything the decision depends on, in ONE transaction
        with the write, so a host that planned long ago can never record anything
        about a generation that is no longer the one on screen:

        - the choice is still this revision (HarvestStale otherwise);
        - the battle is still the LIVE battle of this emulator and still at this
          generation (HarvestStale otherwise: it ended, or a newer one replaced it);
        - no decision for a newer battle is already recorded (HarvestStale);
        - the first decision for (battle, generation) wins and is returned unchanged
          to whoever asks later, another host or a later tick after the selected run
          was edited.

        Decisions are keyed by (battle, generation); a record of an older battle is
        dropped once a newer one is recorded. A new revision decides afresh, which is
        how a source edit is followed."""
        if not valid_targets(targets):
            raise ValueError('An overlay decision names a target for every gem routine')
        with self._db() as db:
            current = read_choice(db, device)
            if current is None or current['revision'] != revision:
                raise HarvestStale('The choice changed; this overlay decision belongs to an older revision')
            order = self._current_battle_order(db, device, battle_id, generation)
            row = db.execute('SELECT body FROM harvest_runtime WHERE device=?', (device,)).fetchone()
            body = json.loads(row[0]) if row else {}
            if body.get('for_revision') != revision:
                body = {'for_revision': revision, 'transfer': body.get('transfer')}
            held = body.get('overlay_freezes')
            held = held if isinstance(held, dict) else {}
            key = freeze_key(battle_id, generation)
            if valid_freeze(held.get(key), battle_id, generation):
                return held[key]
            if any(isinstance(record, dict) and type(record.get('order')) is int and record['order'] > order
                   for record in held.values()):
                raise HarvestStale('A newer battle already has its overlay decision; this one is out of date')
            frozen = dict(battle_id=battle_id, generation=generation, targets=copy.deepcopy(targets),
                          at=time.time(), order=order)
            body['overlay_freezes'] = {name: record for name, record in held.items()
                                       if not (isinstance(record, dict) and type(record.get('order')) is int
                                               and record['order'] < order)}
            body['overlay_freezes'][key] = frozen
            db.execute('INSERT OR REPLACE INTO harvest_runtime VALUES (?,?)',
                       (device, json.dumps(body, sort_keys=True, allow_nan=False)))
            return frozen

    @staticmethod
    def _current_battle_order(db, device, battle_id, generation):
        """The creation order of this emulator's live battle when it is exactly
        (battle_id, generation); HarvestStale when it is anything else."""
        if not has_table(db, 'battles'):
            raise HarvestStale('There is no battle record to decide for')
        row = db.execute('SELECT rowid, device, status, body FROM battles WHERE id=?', (battle_id,)).fetchone()
        if row is None or row[1] != device:
            raise HarvestStale('That battle is not a battle of this emulator')
        if row[2] not in LIVE_BATTLE:
            raise HarvestStale('That battle has ended; a decision for it is out of date')
        if json.loads(row[3]).get('generation') != generation:
            raise HarvestStale('That battle is now at another driver generation')
        return row[0]

    def set_transfer(self, device, transfer):
        """The run-takeover hold is about the emulator, not about one choice
        revision: it survives a new revision and touches nothing else stored."""
        with self._db() as db:
            row = db.execute('SELECT body FROM harvest_runtime WHERE device=?', (device,)).fetchone()
            body = json.loads(row[0]) if row else {}
            body['transfer'] = copy.deepcopy(transfer)
            db.execute('INSERT OR REPLACE INTO harvest_runtime VALUES (?,?)',
                       (device, json.dumps(body, sort_keys=True, allow_nan=False)))
            return body

    def hold_transfer(self, device, seconds, reason, now=None):
        """Keep every lifecycle host from launching a collector while a run takes the emulator."""
        now = time.time() if now is None else now
        return self.set_transfer(device, dict(until=now + seconds, reason=reason, since=now))

    def release_transfer(self, device):
        return self.set_transfer(device, None)
