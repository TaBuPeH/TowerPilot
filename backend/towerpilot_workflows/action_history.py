"""Validation of a battle's action history (roadmap item 7, spec 3.8). Pure: plain values in, a report out.

`validate_history(battle, executions, ...)` is applied to the bounded rows a read returned (never a scan of the whole history on each publication). It reads the
battle record and the rows it is given and nothing else: no ledger, no clock, no file, no device. The write path keeps its own identity and transition invariants
(`actions.Actions`: one open execution per instance, compare-and-set `finish`, an exact fence for settlement); this module judges what was stored.

What a row is measured against is the battle's actual attachments, `battle['segments']`: one entry per driver generation that held the battle. A generation is a
fencing token, not an ordered value, so membership decides whether a row belongs to the battle, never a comparison of wall times. Wall time is kept as measured
(`started_at`, `finished_at`) and is never read as proof of anything alone: a wall time before the attach point is annotated `clock_skew` and the row stays valid
since-adoption history, because the wall clock can be stepped; `duration_ms` is monotonic and is the only measured length (`duration_basis`).

Rules. A rejected row is returned in `rejected` and is not among `items`: it cannot be trusted to belong to this battle or to be well formed.
A flagged row stays in `items` and is also listed in `flagged`: it is stored as it was, and the reader must not present it as proof.

  rejected: row_damaged, newer_schema_row, context_mismatch, foreign_generation, invalid_duration
  flagged:  overlap, duplicate_occurrence, stale_running, unconfirmed_success, coverage_overclaim
  noted:    clock_skew (wall start before the attach point), clock_step (wall finish before wall start, a monotonic duration still measured), unknown_action

Limits that stay visible rather than being papered over:
  * Ordering across rows is by wall time only: no stored row carries a monotonic offset or a sequence number from the attach point, so `overlap` is evidence of a
    clock-ordered overlap and is flagged, never rejected, and rows annotated `clock_skew` or `clock_step` are left out of it. A producer that records an offset
    or a sequence is the boundary request of the r18h report; this module does not invent one.
  * Cross-row rules (overlap, duplicates) see the rows given plus `adjacent` (rows of a neighbouring page), and report only on the rows given.
  * `stale_running` needs the operations the caller knows to be terminal (`ended_operations`); the validator cannot ask the ledger.

A ledger written by a newer build is not interpreted (`status` `read_only`, reason `newer_schema`), the rule `control.actions` already follows.
"""
import math

from towerpilot_workflows import action_contract
from towerpilot_workflows.action_contract import COVERAGES, EFFECTS, OPEN_STATES, RESULTS, SCHEMA_VERSION, STATES, TRIGGERS

# ---- the shape of a stored record. Its single definition: the writer (`actions.py`, persistence) imports it from here, so persistence depends on the pure rules and never the other way.
def finite_or_none(value):
    """Unknown is None, never zero; a non-finite or non-numeric time is unknown."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def duration_or_none(value):
    """A measured length: finite and not negative. Anything else is unknown (None, `duration_basis` 'none'), never zero and never a negative length."""
    number = finite_or_none(value)
    return number if number is not None and number >= 0 else None


# every field new_record makes, in the order it makes them. A stored body must carry all of them: a consumer indexes them with [], so a
# missing one is a KeyError waiting in a driver. A test binds this list to new_record (a field added there but not here fails it).
RECORD_KEYS = ('schema_version', 'id', 'ledger_id', 'device', 'action', 'instance', 'trigger', 'occurrence_key', 'host', 'context',
               'config_revision', 'policy_revision', 'request_key', 'command_id', 'state', 'scheduled_for', 'accepted_at', 'started_at',
               'finished_at', 'duration_ms', 'duration_basis', 'outcome', 'aggregate', 'coverage', 'refs')
OUTCOME_KEYS = ('result', 'reason', 'effect', 'attempts', 'confirmed', 'evidence')


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text_or_none(value):
    return value is None or isinstance(value, str)


def _number_or_none(value):
    return value is None or finite_or_none(value) is not None


def is_whole(body):
    """Is every field of this body one a writer of this build makes? The whole schema is checked, so a consumer (the session's
    summary, a page, a settlement) never meets a field it did not expect: a new field a writer starts to make is added HERE."""
    if not isinstance(body, dict) or not all(key in body for key in RECORD_KEYS):
        return False
    if not _whole(body.get('schema_version')) or finite_or_none(body.get('started_at')) is None:
        return False
    for key in ('id', 'device', 'action', 'instance'):
        if not isinstance(body.get(key), str) or not body[key]:
            return False
    if body.get('trigger') not in TRIGGERS or body.get('state') not in STATES or body.get('coverage') not in COVERAGES:
        return False
    if body.get('duration_basis') not in ('none', 'monotonic'):
        return False
    if not all(_number_or_none(body.get(key)) for key in ('scheduled_for', 'accepted_at', 'finished_at', 'duration_ms', 'settled_at')):
        return False
    if not all(_text_or_none(body.get(key)) for key in ('ledger_id', 'occurrence_key', 'request_key', 'command_id', 'settled_reason')):
        return False
    if not all(value is None or isinstance(value, str) or _whole(value) for value in (body.get('config_revision'), body.get('policy_revision'))):
        return False
    host, context, outcome = body.get('host'), body.get('context'), body.get('outcome')
    if not all(isinstance(part, dict) for part in (host, context, outcome, body.get('refs'))) or not all(key in outcome for key in OUTCOME_KEYS):
        return False
    if not all(_text_or_none(host.get(key)) for key in ('driver', 'operation_id', 'battle_id', 'generation', 'parent_execution')):
        return False
    if not _text_or_none(context.get('account')):
        return False
    if outcome.get('result') is not None and outcome.get('result') not in (*RESULTS, 'interrupted'):
        return False
    if outcome.get('effect') not in EFFECTS or not _text_or_none(outcome.get('reason')):
        return False
    if not all(_whole(outcome.get(key)) and outcome[key] >= 0 for key in ('attempts', 'confirmed')):
        return False
    if not isinstance(outcome.get('evidence'), list) or not all(isinstance(item, str) for item in outcome['evidence']):
        return False
    aggregate = body.get('aggregate')
    if aggregate is not None and not (isinstance(aggregate, dict) and isinstance(aggregate.get('scope'), str) and _whole(aggregate.get('count'))
                                      and aggregate['count'] >= 1 and finite_or_none(aggregate.get('first_at')) is not None
                                      and finite_or_none(aggregate.get('last_at')) is not None):
        return False
    return True


ADOPTED = ('adopted', 'partial_adoption')       # battle origins whose history starts at the adoption, not at the battle's start
MAX_ROWS = 500                                  # one page of the read (`actions.Actions.read_page` caps its limit here): validation is bounded by it
REJECTED = ('row_damaged', 'newer_schema_row', 'context_mismatch', 'foreign_generation', 'invalid_duration')
FLAGGED = ('overlap', 'duplicate_occurrence', 'stale_running', 'unconfirmed_success', 'coverage_overclaim')
NOTES = ('clock_skew', 'clock_step', 'unknown_action')
UNRECORDED = 'recorded_before_action_history'


def unrecorded_reason(battle, ledger):
    """Why a battle with no execution has no history to show, else None. `ledger` is `Actions.read_meta` (None = no action history was ever recorded here).

    A battle created before the ledger gained its action history has no history: that is not a battle where nothing ran, and no timing is backfilled for it."""
    if ledger is None or (ledger.get('created_at') is not None and battle['created_at'] < ledger['created_at']):
        return UNRECORDED
    return None


def battle_coverage(battle):
    """What the battle's own rows can claim: `since_adoption` for a battle this driver joined (or re-joined after a wave-backwards replacement), else `full`."""
    return 'since_adoption' if battle.get('origin') in ADOPTED else 'full'


def _finite(value):
    return finite_or_none(value) is not None


def _segments(battle):
    """generation -> its attachment record, from the battle record; a damaged segment list attaches nothing."""
    found = {}
    for segment in battle.get('segments') or ():
        if isinstance(segment, dict) and isinstance(segment.get('generation'), str) and segment['generation']:
            found.setdefault(segment['generation'], segment)
    return found


def _identity(row, definition):
    """The scope in which an occurrence key must be unique: its action instance and the reset identity of its state (an account's state is per account)."""
    account = row['context'].get('account') if definition is not None and definition.scope == 'account' else None
    return (row['device'], row['action'], row['instance'], account)


class _Verdict:
    def __init__(self):
        self.rejected, self.flagged, self.notes = {}, {}, {}

    @staticmethod
    def add(table, row_id, rule):
        table.setdefault(row_id, [])
        if rule not in table[row_id]:
            table[row_id].append(rule)


def _row_rules(row, battle, segments, scope, ended_operations, definition, verdict):
    """The rules that look at one row alone (plus the battle and the reading context)."""
    row_id = row['id']
    if not isinstance(row['schema_version'], int) or row['schema_version'] > SCHEMA_VERSION:
        verdict.add(verdict.rejected, row_id, 'newer_schema_row')
        return
    host = row['host']
    if row['device'] != scope['device_key'] or row['context'].get('account') != scope['account'] or host.get('battle_id') != battle['id']:
        verdict.add(verdict.rejected, row_id, 'context_mismatch')
    segment = segments.get(host.get('generation'))
    if segment is None:
        verdict.add(verdict.rejected, row_id, 'foreign_generation')
    duration = row.get('duration_ms')
    if duration is not None and (not _finite(duration) or duration < 0):
        verdict.add(verdict.rejected, row_id, 'invalid_duration')
    elif (duration is None) != (row.get('duration_basis') == 'none'):
        verdict.add(verdict.rejected, row_id, 'invalid_duration')          # a measured length must say it is monotonic, and an unmeasured one must say it is not
    if row['state'] == 'succeeded' and definition is not None and definition.evidence and row['outcome'].get('effect') != 'confirmed':
        verdict.add(verdict.flagged, row_id, 'unconfirmed_success')
    if definition is None:
        verdict.add(verdict.notes, row_id, 'unknown_action')
    if row['state'] in OPEN_STATES and (battle.get('status') not in ('prepared', 'active') or host.get('operation_id') in ended_operations
                                        or (segment is not None and segment.get('ended_at') is not None)):
        verdict.add(verdict.flagged, row_id, 'stale_running')
    if row['coverage'] == 'full' and battle_coverage(battle) == 'since_adoption':
        verdict.add(verdict.flagged, row_id, 'coverage_overclaim')
    if segment is not None and _finite(segment.get('started_at')) and row['started_at'] < segment['started_at']:
        verdict.add(verdict.notes, row_id, 'clock_skew')
    if _finite(row.get('finished_at')) and row['finished_at'] < row['started_at'] and duration is not None:
        verdict.add(verdict.notes, row_id, 'clock_step')


def _timed(row, verdict):
    """Can the row's wall interval take part in an overlap judgement? Not an aggregate (it has no duration of its own), not a row whose wall clock was stepped."""
    notes = verdict.notes.get(row['id'], ())
    return row.get('aggregate') is None and 'clock_skew' not in notes and 'clock_step' not in notes


def _end(row):
    """The wall time an execution ended, None while it is open. A terminal row that never measured its end (a settled one) ends where it began: nothing is claimed past it."""
    if row['state'] in OPEN_STATES:
        return None
    return row['finished_at'] if _finite(row.get('finished_at')) else row['started_at']


def _overlaps(rows, shown, verdict):
    by_instance = {}
    for row in rows:
        by_instance.setdefault((row['device'], row['action'], row['instance']), []).append(row)
    for group in by_instance.values():
        holder, holder_end = None, None       # the row whose wall end is the latest so far (end None = still open)
        for row in sorted((row for row in group if _timed(row, verdict)), key=lambda row: (row['started_at'], row['id'])):
            if holder is not None and (holder_end is None or row['started_at'] < holder_end):
                for row_id in (holder['id'], row['id']):
                    if row_id in shown:
                        verdict.add(verdict.flagged, row_id, 'overlap')
            end = _end(row)
            if holder is None or (holder_end is not None and (end is None or end > holder_end)):
                holder, holder_end = row, end


def _duplicates(rows, shown, definitions, verdict):
    """An occurrence consumed twice: a later row of a key whose earlier row already used it. What 'used' means is the action's own `consumes`:
    a confirmed claim, or an attempt of any outcome; an action with no calendar (a window id) uses its key with every row."""
    used = {}
    for row in sorted((row for row in rows if row.get('occurrence_key') and row.get('aggregate') is None), key=lambda row: (row['started_at'], row['id'])):
        definition = definitions(row['action'])
        key = (_identity(row, definition), row['occurrence_key'])
        if key in used and row['id'] in shown:
            verdict.add(verdict.flagged, row['id'], 'duplicate_occurrence')
        if definition is None or definition.consumes != 'confirmed' or row['outcome'].get('effect') == 'confirmed':
            used.setdefault(key, row['id'])


def validate_history(battle, executions, *, context=None, ledger=None, ended_operations=(), adjacent=(), catalog=None):
    """Validate the bounded rows of one battle.

    battle: the battle record (`id`, `context`, `origin`, `status`, `segments`, `created_at`).
    executions: the rows a read returned for it, at most MAX_ROWS (ValueError beyond: unbounded input is a caller error, never silently truncated).
    context: the reading context (`device_key`, `account`); the battle's own when omitted.
    ledger: `Actions.read_meta` of the ledger the rows came from; a newer schema is not interpreted.
    ended_operations: ids of operations the caller knows to be terminal.
    adjacent: rows of a neighbouring page, used by the cross-row rules and never reported on.
    catalog: the action catalog (the one in force when omitted).

    Returns a new dict and changes nothing it was given:
      status      'ok' | 'flagged' | 'read_only'
      reason      None, or 'newer_schema'
      items       the rows that are not rejected, in the order given, or None when the battle has no recorded history (`reason` `recorded_before_action_history`)
      rejected    [{id, rules}], flagged [{id, rules}], notes [{id, notes}], damaged (count of rows that are not whole bodies)
      generations {generation: [row ids]} for the rows kept, in the order given (a battle held by several drivers lists each separately)
      coverage    what the battle's rows can claim: 'full' or 'since_adoption'
    """
    rows = list(executions)
    if len(rows) > MAX_ROWS:
        raise ValueError(f'validate_history takes one bounded page of at most {MAX_ROWS} rows, not {len(rows)}')
    catalog = catalog if catalog is not None else action_contract.catalog()
    base = dict(status='ok', reason=None, items=[], rejected=[], flagged=[], notes=[], damaged=0, generations={}, coverage=battle_coverage(battle))
    if ledger is not None and ledger.get('schema_version') is not None and ledger['schema_version'] > SCHEMA_VERSION:
        return dict(base, status='read_only', reason='newer_schema', items=[])
    scope = dict(device_key=(context or battle['context'])['device_key'], account=(context or battle['context']).get('account'))
    if not rows:
        reason = unrecorded_reason(battle, ledger)
        return dict(base, items=None, reason=reason) if reason else base
    verdict, whole = _Verdict(), []
    definitions = catalog.find
    segments = _segments(battle)
    for row in rows:
        if not is_whole(row):
            base['damaged'] += 1
            continue
        whole.append(row)
    shown = {row['id'] for row in whole}
    neighbours = [row for row in adjacent if is_whole(row) and row['id'] not in shown]
    ended = set(ended_operations)
    for row in (*whole, *neighbours):
        _row_rules(row, battle, segments, scope, ended, definitions(row['action']), verdict)
    context_rows = [row for row in (*whole, *neighbours) if row['id'] not in verdict.rejected]
    _overlaps(context_rows, shown, verdict)
    _duplicates(context_rows, shown, definitions, verdict)
    kept = [row for row in whole if row['id'] not in verdict.rejected]
    for row in kept:
        base['generations'].setdefault(row['host']['generation'], []).append(row['id'])
    base.update(items=kept,
                rejected=[dict(id=row_id, rules=rules) for row_id, rules in verdict.rejected.items() if row_id in shown],
                flagged=[dict(id=row_id, rules=rules) for row_id, rules in verdict.flagged.items() if row_id in shown and row_id not in verdict.rejected],
                notes=[dict(id=row_id, notes=notes) for row_id, notes in verdict.notes.items() if row_id in shown])
    base['status'] = 'flagged' if base['rejected'] or base['flagged'] or base['damaged'] else 'ok'
    return base
