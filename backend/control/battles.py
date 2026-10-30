"""Read models and command admission for the local battle workspace."""
import copy
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from control import battle_lineage, operations
from towerpilot_workflows.battles import LIVE, Battles
from towerpilot_workflows.operations import STATUSES


class CommandOutcomeUnknown(Exception):
    """A battle command passed admission, so it is recorded, but its reply failed.

    Deliberately not a KeyError or ValueError: the HTTP layer maps those to
    definite refusals ("nothing was recorded"), which would be false here.
    """

    def __init__(self, command_id):
        super().__init__('The command was recorded, but its result could not be read back. '
                         'Refresh the battle before sending it again.')
        self.command_id = command_id


class SetupBattleUnresolved(operations.Conflict):
    """A run or collector launch refused before acceptance: nothing was recorded."""

    def __init__(self, guard):
        super().__init__(guard['message'])
        self.guard = guard


def ledger(root):
    return Battles(operations.ledger(root).path)


def active_read_only(root, device):
    """The live records of a device, read without creating the ledger or its tables (a GET, the takeover preview)."""
    return Battles.active_read_only(operations.ledger_path(root), device)


def reconcile(root, store, device=None):
    """Finish the battles whose driver operation ended. Only the process
    refresher calls this (via reconcile_existing); a GET never changes a battle.

    It closes the LEDGER record of a battle whose driver is gone. It never
    touches the game or the device, so a battle that is still on screen simply
    continues unrecorded until a driver adopts it into a new record. Two
    dashboards may run it at once: finishing is idempotent, and losing the race
    to another dashboard (or to the driver's own ending) is not an error."""
    for battle in store.active(device):
        operation = operations.resolve(root, battle['operation_id'])
        if operation['status'] not in ('accepted', 'running', 'cancelling'):
            try:
                store.finish(battle['id'], battle['generation'], reason='driver_observation_lost',
                             status='interrupted', evidence={'operation_status': operation['status']})
            except operations.Conflict:
                if store.get(battle['id'])['status'] in LIVE:
                    raise


def reconcile_existing(root, device=None):
    """reconcile() over every device, when a ledger exists. Creates nothing."""
    if operations.ledger_path(root).is_file():
        reconcile(root, ledger(root), device)


def public(store, battle):
    result = copy.deepcopy(battle)
    result['context'].pop('config', None)
    envelope = result['envelope']
    envelope.pop('config', None)
    envelope.pop('profile', None)
    preset = envelope.get('preset', {})
    runtime = result.get('runtime', {})
    boundary = result.get('boundary_evidence') or {}
    final_wave = boundary.get('last_observed_wave', boundary.get('previous_wave'))
    result.update(label=preset.get('label'), blueprint=(preset.get('_source') or {}).get('blueprint'),
                  tier=preset.get('tier'), started_at=result.get('observed_started_at'),
                  wave=runtime.get('observed_wave'), final_wave=final_wave,
                  wave_provenance='last_observed' if final_wave is not None else 'unknown',
                  result=result.get('outcome'), end_reason=result.get('reason'),
                  effective=copy.deepcopy(result['applied_controls']))
    result['commands'] = store.commands(battle['id'])
    # Takeover facts are additive and absent for every other battle, adopted ones included (the recorded contract fixtures do not change).
    attach = battle_lineage.attach_view(battle)
    if attach is not None:
        result['attach'] = attach
    lineage = battle_lineage.lineage(store, battle) if attach is not None else None      # a battle adopted outside a takeover reads exactly as before
    if lineage is not None:
        result['lineage'] = lineage
    return result


def active(root, device):
    """The battle on screen as recorded. Read-only: orphans are finished by the refresher."""
    store = ledger(root)
    rows = store.active(device)
    from player.shopping_plan import catalog
    return dict(battle=public(store, rows[0]) if rows else None,
                catalog=catalog(), server_time=time.time())


def command(root, operation_id, body, *, action=False):
    if not isinstance(body, dict):
        raise ValueError('Command must be an object')
    for key in ('battle_id', 'generation', 'idempotency_key'):
        if not isinstance(body.get(key), str) or not body[key]:
            raise ValueError(f'{key} is required')
    if type(body.get('expected_revision')) is not int:
        raise ValueError('expected_revision is required')
    store = ledger(root)
    args = (body['battle_id'], operation_id, body['generation'])
    if action:
        fingerprint = store.action_fingerprint(*args, body.get('action'), body['expected_revision'])
    else:
        from control.run_workspace import normalize_changes
        patch = normalize_changes(body.get('changes', body.get('patch')))
        fingerprint = store.controls_fingerprint(*args, body['expected_revision'], patch)
    # An identical request that was already recorded is answered from its
    # receipt, whatever became of the driver or the battle since: a client that
    # lost the first reply must learn the outcome, not read "no longer active".
    # The fingerprint binds battle, operation, generation and payload, so the
    # same key with anything else is still a conflict.
    accepted = store.recorded_command(body['idempotency_key'], fingerprint)
    if accepted is None:
        operation = operations.resolve(root, operation_id)
        if operation['status'] not in ('accepted', 'running'):
            raise operations.Conflict('The battle driver is no longer active')
        if action:
            accepted = store.request_action(*args, body.get('action'), body['idempotency_key'],
                                            expected_revision=body['expected_revision'])
        else:
            accepted = store.request_controls(*args, body['expected_revision'], patch, body['idempotency_key'])
    # Admission has committed (or replayed) the command. Every failure from
    # here on is an unknown reply for a recorded command, never a refusal.
    try:
        battle = public(store, store.get(body['battle_id']))
        return dict(ok=True, command=accepted, battle=battle, control_revision=battle['control_revision'])
    except Exception as exc:  # noqa: BLE001 - classified, chained and re-raised
        raise CommandOutcomeUnknown(accepted.get('id') if isinstance(accepted, dict) else None) from exc


# ------------------------------------------------------- setup battle guard
# Full setup's consented flow stage starts its OWN normal run and must prove it
# back on Home (player/flow_capture.py). An interrupted pass records the battle
# as unresolved and revokes its own input authority; nothing may adopt it.
# Evidence is only the calibrate_state.json each calibrate operation SEALED at
# operations/<id>/ under its artifact directory (exclusive create, sha256 in
# result_refs), newest first on this device. The shared working file is never
# consulted: the next calibration clears it before proving anything, and a
# reference pointing at it is not sealed history. A record is released only by
# the existing proofs: the operation's own setup battle 'finished', or its own
# final Home screen verified. Present but malformed or foreign evidence is
# unreadable, never "nothing recorded".
#
# Two scopes, one rule. 'run' (general automation) is exactly the rule above.
# 'collection' (the gem collector, which never starts, ends, equips or shops)
# additionally accepts a person's collection-only handoff for the one exact
# unresolved record: same operation, setup battle and sealed sha256, on the same
# device, account and serial, and only while that operation is still the newest
# setup/calibration on the device. Any newer calibrate operation, unreadable or
# running evidence, or another context ends the handoff's effect. Nothing about
# the failed setup is rewritten.
SETUP_EVIDENCE = 'calibrate_state.json'
GUARD_SCOPES = {'run': 'run', 'gems': 'collection'}   # launches whose drivers adopt a battle on screen
_IN_PROGRESS = ('accepted', 'running', 'cancelling')
_RELEASE = 'A later setup or calibration on this emulator that verifies Home lifts this.'


def _setup_guard(context, evidence, *, scope='run', code=None, message=None, op=None, battle=None,
                 revision=None, handoff=None):
    battle = battle if isinstance(battle, dict) else {}

    def text(key):
        return battle[key] if isinstance(battle.get(key), str) else None
    return dict(blocks_launch=code is not None, code=code, message=message, scope=scope,
                operation_id=op.get('id') if isinstance(op, dict) else None,
                setup_battle_id=text('id'), recorded_status=text('status'), evidence=evidence,
                evidence_revision=revision, handoff_id=(handoff or {}).get('id'),
                instance=context.get('instance'), serial=context.get('serial'))


def setup_guard_unavailable(context, error, scope='run'):
    return _setup_guard(context, 'unavailable', scope=scope, code='setup_guard_unavailable',
                        message=f'Setup battle records could not be read ({error}). '
                                'Start and Collect are refused until they can be.')


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _sealed_setup_state(op):
    """The state this operation sealed in its own directory, verified, or (None, why)."""
    ref = op['result_refs'].get(SETUP_EVIDENCE)
    if ref is None:
        return None, 'no sealed final record'
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not isinstance(ref.get('revision'), str):
        return None, 'malformed sealed reference'
    sealed = Path(op['context']['artifact_dir']) / 'operations' / op['id'] / SETUP_EVIDENCE
    if not _same_path(ref['path'], sealed):
        return None, 'reference is not this operation\'s sealed copy'
    try:
        raw = Path(ref['path']).read_bytes()
    except OSError as exc:
        return None, f'sealed record unavailable: {exc}'
    if hashlib.sha256(raw).hexdigest() != ref['revision']:
        return None, 'sealed record changed after the operation ended'
    try:
        state = json.loads(raw)
    except ValueError:
        return None, 'sealed record is not valid JSON'
    if not isinstance(state, dict) or state.get('operation_id') != op['id']:
        return None, 'sealed record belongs to another operation'
    return state, None


def _never_ran(op):
    """A terminal operation whose launch failed or was abandoned before any worker
    existed (never 'running', no worker identity, nothing sealed): it cannot have
    sent input or started a setup battle. Anything else with no sealed record
    stays unreadable evidence."""
    error = op.get('error')
    return (op.get('step') == 'accepted' and op.get('worker') is None and not op.get('result_refs')
            and isinstance(error, dict) and error.get('code') in ('launch_failed', 'launch_interrupted'))


def _owned_battle(battle, op):
    return (isinstance(battle, dict) and isinstance(battle.get('id'), str) and bool(battle['id'])
            and isinstance(battle.get('status'), str) and battle.get('operation_id') == op['id'])


def _owned_screen(screen, op):
    return (isinstance(screen, dict) and screen.get('operation_id') == op['id']
            and isinstance(screen.get('name'), str) and isinstance(screen.get('verified'), bool))


def _decide(context, row, scope='run'):
    """One calibrate ledger row: a blocking or releasing guard, or None to look further back."""
    operation_id, device, kind, status, body = row
    short = str(operation_id)[:8]

    def unreadable(reason, op=None, battle=None):
        return _setup_guard(context, 'unreadable', scope=scope, code='setup_evidence_unreadable',
                            op=op or {'id': operation_id}, battle=battle,
                            message=f'Calibration {short} on this emulator has unreadable setup evidence '
                                    f'({reason}), so whether it left a setup battle on screen is unknown. '
                                    f'Start and Collect are refused. {_RELEASE}')
    try:
        op = json.loads(body)
        if not (isinstance(op, dict) and op.get('id') == operation_id and op.get('kind') == kind
                and op.get('status') == status and isinstance(op.get('context'), dict)
                and op['context'].get('device_key') == device):
            return unreadable('ledger record does not match its row')
        if status not in STATUSES:
            return unreadable(f'unknown operation status {status!r}', op)
        if status in _IN_PROGRESS:
            return _setup_guard(context, 'operation_active', scope=scope, code='setup_operation_active', op=op,
                                message=f'Setup or calibration {short} is still running on this emulator; '
                                        'Start and Collect wait for its recorded outcome.')
        if _never_ran(op):
            return None   # no worker ever started, so no setup battle was started either
        state, problem = _sealed_setup_state(op)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return unreadable(f'malformed ledger record: {type(exc).__name__}')
    if state is None:
        return unreadable(problem, op)
    revision = op['result_refs'][SETUP_EVIDENCE]['revision']
    battle, screen = state.get('setup_battle'), state.get('final_screen')
    if 'setup_battle' in state and not _owned_battle(battle, op):
        return unreadable('setup battle record is malformed or not owned by this operation', op, battle)
    if 'final_screen' in state and not _owned_screen(screen, op):
        return unreadable('final screen record is malformed or not owned by this operation', op)
    if 'setup_battle' in state:
        if battle['status'] != 'finished':
            reason = battle.get('reason')
            detail = battle['status'] + (f'; {str(reason)[:300]}' if reason else '')
            return _setup_guard(context, 'setup_battle_recorded', scope=scope, code='setup_battle_unresolved',
                                op=op, battle=battle, revision=revision,
                                message=f'The setup test battle from operation {short} was never verified back '
                                        f'on Home (recorded: {detail}). Start and Collect are refused because they '
                                        f'could adopt a battle still on screen. {_RELEASE}')
        return _setup_guard(context, 'setup_battle_finished', scope=scope, op=op, battle=battle, revision=revision)
    if 'final_screen' in state and screen['name'] == 'home' and screen['verified'] is True:
        return _setup_guard(context, 'final_home_verified', scope=scope, op=op, revision=revision)
    return None   # proved nothing either way; older evidence stays in force


def _handoff(db, context, decision):
    """The collection-only handoff recorded for exactly this decisive blocker, or None."""
    from towerpilot_workflows.harvest import blocker_key
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_handoffs'").fetchone():
        return None   # no handoff was ever recorded with this ledger
    blocker = {'operation_id': decision['operation_id'], 'setup_battle_id': decision['setup_battle_id'],
               'evidence_revision': decision['evidence_revision']}
    if any(not isinstance(value, str) or not value for value in blocker.values()):
        return None
    row = db.execute('SELECT device,body FROM collection_handoffs WHERE blocker=?',
                     (blocker_key(context['device_key'], context, blocker),)).fetchone()
    if row is None:
        return None
    record = json.loads(row[1])
    if row[0] != context['device_key'] or not _stored_handoff_valid(record, context, blocker):
        return None   # a record that does not restate its own identity grants nothing
    return record


def _stored_handoff_valid(record, context, blocker):
    """Does a stored handoff restate exactly this device, account, serial and blocker?"""
    from towerpilot_workflows.harvest import ATTESTATION, BLOCKER_KEYS, CONTEXT_KEYS
    stored = record.get('blocker') if isinstance(record, dict) else None
    return (isinstance(record, dict) and isinstance(stored, dict)
            and record.get('device_key') == context['device_key']
            and record.get('scope') == 'collection_only' and record.get('attestation') == ATTESTATION
            and all(record.get(key) == context.get(key) for key in CONTEXT_KEYS)
            and all(stored.get(key) == blocker[key] for key in BLOCKER_KEYS))


def _collection_release(context, decision, handoff):
    short = str(decision['operation_id'])[:8]
    return dict(decision, blocks_launch=False, code=None, evidence='collection_handoff',
                handoff_id=handoff['id'],
                message=f'Collection only: you confirmed the battle on screen is a new manually started run, '
                        f'not the unresolved setup battle from operation {short}. Gem collection may run; '
                        f'other automation stays refused until a calibration verifies Home.')


def _evaluate(root, context, scope):
    """(guard, index): the decisive calibrate row's position, newest first
    (None when nothing was recorded or the evidence could not be read)."""
    if scope not in ('run', 'collection'):
        raise ValueError('Unknown guard scope')
    try:
        device = context['device_key']
        path = Path(os.path.abspath(operations.ledger_path(root)))
        if not path.is_file():
            return _setup_guard(context, 'none_recorded', scope=scope), None   # nothing was ever recorded
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as exc:
        return setup_guard_unavailable(context, exc, scope), None
    try:
        db.execute('BEGIN')   # one consistent snapshot of operations and handoffs
        rows = db.execute("SELECT id,device,kind,status,body FROM operations "
                          "WHERE device=? AND kind='calibrate' ORDER BY rowid DESC", (device,)).fetchall()
        for index, row in enumerate(rows):
            decision = _decide(context, row, scope)
            if decision is None:
                continue
            if scope == 'collection' and index == 0 and decision['code'] == 'setup_battle_unresolved':
                handoff = _handoff(db, context, decision)
                if handoff is not None:
                    return _collection_release(context, decision, handoff), index
            return decision, index
        return _setup_guard(context, 'none_recorded', scope=scope), None
    except (sqlite3.Error, ValueError, TypeError, AttributeError, KeyError) as exc:
        return setup_guard_unavailable(context, exc, scope), None
    finally:
        db.close()


def setup_battle_guard(root, context, *, scope='run'):
    """Whether recorded setup evidence lets new automation start on this device.

    Device-scoped (the device_key admission and the ledger use for ownership),
    so a profile or account switch cannot hide a record; a collection handoff
    additionally binds account and serial. Pure read: the ledger is opened
    read-only and nothing is resolved, rewritten or dismissed. Unreadable
    evidence blocks; it is never treated as permission.
    """
    return _evaluate(root, context, scope)[0]


def guard_identity(cfg, instance=None):
    """Device and account identity exactly as admission derives it from cfg."""
    from player import accounts
    identity = operations.device_identity(cfg, instance)
    bound = dict(cfg, active_instance=identity['instance'])
    identity['account'] = accounts.identity(bound)
    return identity


def guard_for(root, cfg, instance=None, *, scope='run'):
    """setup_battle_guard for cfg's emulator, identified as admission identifies it."""
    try:
        identity = guard_identity(cfg, instance)
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        return setup_guard_unavailable(dict(instance=instance), exc, scope)
    return setup_battle_guard(root, identity, scope=scope)


def refuse_if_blocked(guard, event=None):
    if guard['blocks_launch']:
        if event is not None:
            # The refusing setup operation is not the caller's own operation:
            # keep the worker's operation_id correlation intact.
            event('setup_battle_guard_refused', **{('setup_operation_id' if key == 'operation_id' else key): value
                                                   for key, value in guard.items()})
        raise SetupBattleUnresolved(guard)
    return guard


def require_runner_entry(root, cfg, instance=None, *, scope='run', event=None):
    """Entry of a driver that may adopt a battle (orchestrator, gem collector,
    combo), however it was started. Evaluated for the bound emulator before any
    frame or input; it never trusts launch environment variables.
    """
    return refuse_if_blocked(guard_for(root, cfg, instance, scope=scope), event)


# ---------------------------------------------------- collection handoff
class HandoffRefused(operations.Conflict):
    """A collection handoff request refused before anything was recorded."""

    def __init__(self, code, message, guard=None):
        super().__init__(message)
        self.code, self.guard = code, guard


def record_collection_handoff(root, cfg, body, *, owners):
    """Record a person's collection-only handoff for the exact current blocker.

    owners: callable(identity) -> list of descriptions of anything that holds
    this emulator now (active operations, runner processes). The handoff is
    refused while anything does, while the named record is not the current
    decisive evidence, or when account, instance or serial changed.
    """
    from towerpilot_workflows.harvest import ATTESTATION, Harvest, blocker_key, has_table, readonly
    if not isinstance(body, dict):
        raise ValueError('Handoff must be an object')
    blocker = {key: body.get(key) for key in ('operation_id', 'setup_battle_id', 'evidence_revision')}
    if any(not isinstance(value, str) or not value for value in blocker.values()):
        raise ValueError('Name the exact setup record: operation_id, setup_battle_id and evidence_revision')
    if body.get('attestation') != ATTESTATION:   # before any store is opened: a refusal creates nothing
        raise ValueError('Confirm that the battle on screen is a new manually started run')
    expected = body.get('expected_context')
    if not isinstance(expected, dict):
        raise ValueError('expected_context is required')
    identity = guard_identity(cfg)
    if any(expected.get(key) != identity.get(key) for key in ('instance', 'account', 'serial')):
        raise HandoffRefused('context_changed', 'Account or emulator changed; review the current battle again')

    def names_blocker(guard, index):
        # The handoff takes effect only for the newest setup evidence; recording
        # one for anything else would store a row that can never apply.
        return (index == 0 and guard['code'] == 'setup_battle_unresolved'
                and all(guard.get(key) == value for key, value in blocker.items()))
    current, index = _evaluate(root, identity, 'run')
    # An identical retry (a lost reply) is answered from the record, even while
    # the collector it enabled is running and owns the emulator.
    if names_blocker(current, index):
        with readonly(operations.ledger_path(root)) as db:
            row = None
            if db is not None and has_table(db, 'collection_handoffs'):
                row = db.execute('SELECT body FROM collection_handoffs WHERE blocker=?',
                                 (blocker_key(identity['device_key'], identity, blocker),)).fetchone()
        if row is not None and _stored_handoff_valid(json.loads(row[0]), identity, blocker):
            return json.loads(row[0]), False, identity
    busy = owners(identity)
    if busy:
        raise HandoffRefused('device_owned', 'Something is using this emulator (' + ', '.join(busy)
                             + '); a handoff is recorded only while nothing else owns it')
    if not names_blocker(current, index):
        raise HandoffRefused('handoff_blocker_changed',
                             'The named setup record is not the current blocker on this emulator; nothing was recorded',
                             current)
    blocker['recorded_status'] = current['recorded_status']
    store = Harvest(operations.ledger_path(root))
    record, created = store.record_handoff(identity, blocker, attestation=body.get('attestation'))
    return record, created, identity
