"""Persistent gem harvesting: the person's choice, observed truth, reconciliation.

Harvesting reuses the existing collector (gem_collector.py + GemWatch) launched
as an ordinary 'gems' operation, so admission (setup guard, device ownership,
idempotency), the device lease and the battle ledger all apply unchanged. This
module adds no service: the dashboard's existing process-refresher thread calls
tick(); GET /api/status calls status(), which reads one read-only ledger
snapshot, creates nothing and never launches, stops or sends anything.

The person's choice has three states. Never chosen: runs keep their own gem
settings. On: harvesting should happen whenever a battle is on screen, using
the gem settings of the chosen run, whoever started the battle. Off: automatic
gem collection is suppressed. The choice persists across restarts and browser
tabs; it changes only by a person's request or an explicit Stop.

Who collects is observed, never inferred:
  * no other driver owns the emulator: one standalone collector, launched under
    a durable fence (the ledger accepts it only while the choice is still on at
    the revision it was launched for; the worker re-reads the choice before its
    first input and periodically);
  * a full driver owns the emulator: its existing revisioned battle controls
    carry one command per choice revision and battle generation. On sets each
    gem routine to the selected run's enabled value, Off suppresses both, and
    a person's own live choice for a routine in that battle always wins. The
    driver keeps its own cadence; the selected run's timing applies to the
    standalone collector only. Drivers without those controls are reported
    unsupported and never get a competing collector.
"""
import math
import sqlite3
import time
from collections import namedtuple

from control import actions as action_history, battles, operations
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.control_settings import flatten
from towerpilot_workflows.harvest import (
    BOUND_KEYS, GEM_ROUTINES, OVERLAY_PREFIX, Harvest, HarvestFenceRefused, HarvestKeyReused, HarvestRevisionChanged,
    HarvestStale, automatic_operations, context_matches, freeze_key, launch_key, read_choice, read_receipt, read_runtime,
    valid_freeze, verify_overlay,
)
from towerpilot_workflows.action_contract import CATALOG, OPEN_STATES
from towerpilot_workflows.operations import ACTIVE, Conflict, digest, has_table, readonly

VERSION = 1
FRESH_SEC = 15           # same freshness the live controls use for routine timing
SCHEDULE = CATALOG.get('harvest_launch').schedule   # the Backoff policy: the one definition of every number below
MIN_SPACING_SEC = SCHEDULE.min_spacing     # never launch the collector more often than this
FAIL_WINDOW_SEC = SCHEDULE.window          # an exit sooner than this counts as a failed attempt
CIRCUIT = SCHEDULE.circuit                 # consecutive fast failures before relaunching stops
REFUSAL_RETRY_SEC = SCHEDULE.refusal_retry # recheck a launch refusal (taps, digits, guard) this often
TRANSFER_HOLD_SEC = 20   # how long a run takeover holds off the collector
TRANSFER_WAIT_SEC = 10   # how long a takeover waits for the collector to release the emulator
TERMINAL = ('succeeded', 'failed', 'interrupted', 'cancelled', 'restoration_required', 'awaiting_apply')
ROUTINES = GEM_ROUTINES
CONTROL_PATHS = {name: f'routines.{name}.enabled' for name in ROUTINES}
LIVE = ('prepared', 'active')
CLAIM_FAILURES_WARN = 3  # consecutive ad windows that sent a tap and proved nothing before the Dashboard is told to warn
CLAIM_WINDOWS_READ = 60  # newest ad windows read for the streak and the latest miss
# Near-miss frames, among the orbit finder's recent frames with nothing recognized, before the finder is suspected. The
# collector searches about once a second, and the council measured a working finder recognizing a visible gem in about
# 60% of its frames (gem-collection GC-01), so five near misses with no recognition among the recent frames is rare for a
# working finder and certain for one that rejects gems at the glyph check. Blind spot: gems rejected by an earlier
# check (size, shape, pink border, HUD diamond) are not near misses, so they are NOT detected. The number is a judgment, not a measurement: tune it from
# live heartbeats (gem_orbit_sensing). The window is vision.orbit_gems.RECENT_FRAMES.
FINDER_NEAR_MISS_FRAMES = 5
# A closed ad window's recorded reason -> the code the Dashboard words. Anything else reads 'not_confirmed'.
MISS_CODES = {'no_offer_seen': 'no_offer_seen', 'offer_not_claimed': 'offer_not_claimed', 'offer_moved': 'offer_moved',
              'fresh_offer_missing': 'offer_gone', 'tap_refused': 'tap_refused', 'timeout': 'window_ran_out',
              'window_expired_during_verification': 'window_ran_out', 'balance_unreadable': 'balance_unread',
              'balance_unstable': 'balance_unread', 'ownership_lost': 'collector_stopped',
              'window_missed': 'window_ran_out'}      # a window the loop never opened ran out as well
CAUSE_CODES = tuple(sorted({*MISS_CODES.values(), 'not_confirmed', 'collector_stopped', 'collector_restarted'}))


Prepared = namedtuple('Prepared', 'command policy_revision')
Prepared.__doc__ = """What prepare() hands the lifecycle host: the worker command and the
policy revision of the run settings that were compiled for THIS launch (the same
compiled run the accepted envelope will freeze)."""


class Refused(Exception):
    """The collector cannot be launched now; nothing was started."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ChoiceRefused(Conflict):
    """A harvesting request refused before anything was recorded."""

    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.extra = code, extra


class OwnersUnreadable(Conflict):
    """Who holds the emulator cannot be read. That is never read as 'nobody does'."""

    def __init__(self, cause):
        super().__init__(f'Could not read which automation holds the emulator ({cause}); try again.')


def backoff(failures):
    return SCHEDULE.delay(failures)


def identity_for(cfg):
    """Device and account identity exactly as admission derives it, plus the profile."""
    identity = battles.guard_identity(cfg)
    identity['profile'] = cfg.get('active_profile')
    return identity


def store(root):
    """Mutating access (creates the schema). Status never calls this."""
    return Harvest(operations.ledger_path(root))


# ------------------------------------------------------------ read model
def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def read_facts(root, identity):
    """Everything status needs from one read-only snapshot. A missing ledger or
    missing tables mean nothing was ever chosen. Raises ValueError/sqlite3.Error
    for unreadable rows: unreadable is never read as 'never chosen'."""
    import json
    facts = dict(choice=None, runtime={}, active=[], automatic=[], battle=None, commands=[], windows=None)
    with readonly(operations.ledger_path(root)) as db:
        if db is None:
            return facts
        device = identity['device_key']
        facts['choice'] = choice = read_choice(db, device)
        revision = choice['revision'] if choice else 0
        facts['runtime'] = read_runtime(db, device, revision)
        if has_table(db, 'operations'):
            marks = ','.join('?' for _ in ACTIVE)
            facts['active'] = [json.loads(row[0]) for row in db.execute(
                f'SELECT body FROM operations WHERE device=? AND status IN ({marks}) ORDER BY rowid', (device, *ACTIVE))]
            if choice:
                facts['automatic'] = automatic_operations(db, device, revision)
        if has_table(db, 'battles'):
            rows = db.execute("SELECT body FROM battles WHERE device=? AND status IN ('prepared','active')",
                              (device,)).fetchall()
            facts['battle'] = json.loads(rows[0][0]) if rows else None
            if facts['battle'] and has_table(db, 'battle_commands'):
                facts['commands'] = [(row[0], json.loads(row[1])) for row in db.execute(
                    'SELECT request_key,body FROM battle_commands WHERE battle_id=? ORDER BY rowid',
                    (facts['battle']['id'],))]
    if facts['battle']:
        facts['windows'] = ad_windows(root, facts['battle'])
    return facts


def ad_windows(root, battle):
    """The battle's recorded ad windows, newest first; None when they cannot be read (then no streak or cause is claimed).
    Missing history is an empty list; an unreadable ledger or a newer schema is never read as 'no misses'."""
    try:
        found = action_history.for_battle(root, battle, limit=CLAIM_WINDOWS_READ, action='ad_gems')
    except (ValueError, sqlite3.Error, OSError):
        return None
    return found['items'] if found['items'] is not None and found['reason'] is None else (
        [] if found['reason'] == 'recorded_before_action_history' else None)


def desired_view(choice):
    if choice is None:
        return dict(choice='unset', enabled=None, revision=0, preset=None, label=None, changed_at=None,
                    changed_by=None, reason=None, request_key=None, bound=None)
    bound = choice.get('bound') or {}
    return dict(choice='on' if choice['enabled'] else 'off', enabled=choice['enabled'], revision=choice['revision'],
                preset=choice.get('preset'), label=choice.get('label'), changed_at=choice.get('changed_at'),
                changed_by=choice.get('changed_by'), reason=choice.get('reason'), request_key=choice.get('request_key'),
                bound={key: bound.get(key) for key in BOUND_KEYS + ('policy_revision',)})


def source_view(source, choice):
    if source is None:
        return None
    view = dict(source)
    bound = (choice or {}).get('bound') or {}
    view['changed_since_enable'] = bool(choice and choice['enabled'] and view.get('valid')
                                        and bound.get('policy_revision') not in (None, view.get('policy_revision')))
    return view


def gem_gates(routines):
    """Enabled flags a driver published for the two gem routines; None where not reported."""
    gates = {}
    for name in ROUTINES:
        record = routines.get(name) if isinstance(routines, dict) else None
        gates[name] = record['enabled'] if isinstance(record, dict) and type(record.get('enabled')) is bool else None
    return gates


def battle_view(battle, now):
    """One live battle's publication: freshness is finite, non-future and at most FRESH_SEC old."""
    runtime = battle.get('runtime') or {}
    published = runtime.get('server_time')
    valid = finite(published) and published <= now
    age = now - published if valid else None
    observation = runtime.get('observation_status')
    return dict(id=battle['id'], status=battle['status'], generation=battle['generation'], origin=battle.get('origin'),
                control_revision=battle.get('control_revision'),
                observation_status=observation if isinstance(observation, str) else None,
                published_at=published if valid else None,
                observation_age_sec=round(age, 3) if valid else None,
                fresh=bool(valid and age <= FRESH_SEC), publication_valid=valid)


def _runtime_dict(battle, key):
    value = ((battle or {}).get('runtime') or {}).get(key)
    return value if isinstance(value, dict) else None


def finder_suspected(battle):
    """Is the orbit finder suspected of having stopped matching: frames were searched, nothing was recognized among the
    recent ones, and squares that look like gems kept failing the glyph check (near misses)?

    None when the collector reported no sensing (unknown, never a clean bill). False when the orbit routine is not
    known to be on: its tally is then history, not a live reading. The evidence is carried by runtime/battle_session.py
    (collection_evidence.sensing); this is the one place that says what it means."""
    sensing = (_runtime_dict(battle, 'collection_evidence') or {}).get('sensing')
    orbit = sensing.get('floating_gems') if isinstance(sensing, dict) else None
    recent = orbit.get('recent') if isinstance(orbit, dict) else None
    counts = [recent.get(name) for name in ('frames', 'recognized', 'near_miss_frames')] if isinstance(recent, dict) else None
    if counts is None or not all(type(count) is int and count >= 0 for count in counts):
        return None
    if gem_gates(_runtime_dict(battle, 'routines'))['floating_gems'] is not True:
        return False
    return recent['recognized'] == 0 and recent['near_miss_frames'] >= FINDER_NEAR_MISS_FRAMES


def miss_cause(row):
    """The cause of one closed ad window that did not succeed: the code the Dashboard words, and when."""
    code = ('collector_stopped' if row.get('state') == 'interrupted'
            else MISS_CODES.get((row.get('outcome') or {}).get('reason'), 'not_confirmed'))
    at = row.get('finished_at') if finite(row.get('finished_at')) else row.get('started_at')
    return dict(code=code, at=at if finite(at) else None)


def claim_summary(windows, autos, battle):
    """What went wrong with this battle's ad claims, from the recorded ad windows and the collector's exits.

    windows: the battle's ad executions newest first (None: unreadable, so nothing is claimed either way).
    failure_streak counts the newest windows in a row that sent a tap and confirmed nothing; a window with no
    offer to claim neither adds nor ends it, a confirmed claim ends it. failing is the one decision the
    Dashboard warns on. cause is the later of the latest window that did not succeed and the exit of an earlier
    collector during this battle. windows_missed is the collector's own counter of windows that ran out.
    finder_suspected: see finder_suspected(); the orbit side of "gems are not being collected" that no window shows."""
    counters = ((_runtime_dict(battle, 'routines') or {}).get('ad_gems') or {}).get('counters')
    missed = counters.get('windows_missed') if isinstance(counters, dict) else None
    streak, cause = None, None
    if windows is not None:
        closed = [row for row in windows if row.get('state') not in OPEN_STATES]
        streak = 0
        for row in closed:
            if row['state'] == 'succeeded':
                break
            streak += row['state'] in ('failed', 'refused')
        missed_row = next((row for row in closed if row['state'] != 'succeeded'), None)
        cause = None if missed_row is None else miss_cause(missed_row)
    exits = [op['updated_at'] for op in autos if op['status'] in TERMINAL and finite(op.get('updated_at'))
             and op['updated_at'] >= battle['created_at']]
    if exits and (cause is None or cause['at'] is None or max(exits) > cause['at']):
        cause = dict(code='collector_restarted', at=max(exits))
    return dict(windows_missed=missed if type(missed) is int and missed >= 0 else None, failure_streak=streak,
                failing=streak is not None and streak >= CLAIM_FAILURES_WARN, cause=cause,
                finder_suspected=finder_suspected(battle))


def battle_claims(facts, op):
    """claim_summary for the battle this operation owns, else None."""
    battle = facts['battle']
    if op is None or battle is None or battle.get('operation_id') != op['id']:
        return None
    return claim_summary(facts['windows'], facts['automatic'], battle)


def _alive(alive):
    return operations.worker_alive if alive is None else alive


def collector_state(op, battle, alive, now, claims=None):
    """What the standalone collector observes, from its own battle evidence only (and, for the claim outcome, its recorded ad windows)."""
    runtime = (battle or {}).get('runtime') or {}
    view = dict(id=op['id'], kind=op['kind'], status=op['status'], revision=op['revision'],
                harvest_managed=bool(op['request'].get('harvest')),
                desire_revision=op['request'].get('desire_revision'),
                worker_alive=alive(op.get('worker')) if op['status'] != 'accepted' else None,
                battle=None, routines=None, evidence=None, claims=None)
    if op['status'] == 'accepted':
        return 'starting', 'collector_starting', 'The collector was accepted and is starting.', view
    if op['status'] == 'cancelling':
        return 'stopping', 'collector_stopping', 'The collector is stopping; the battle is not ended.', view
    if view['worker_alive'] is False:
        return ('unavailable', 'worker_not_running',
                'The collector process is not running; the ledger will retire it.', view)
    if view['worker_alive'] is not True:
        # Unknown is not dead: the process may still hold the emulator, so the operation stays its owner.
        return ('unavailable', 'worker_unverified',
                'The collector process cannot be checked from here, so it is treated as still holding the emulator.', view)
    if battle is None or battle.get('operation_id') != op['id']:
        return ('waiting_for_battle', 'between_battles',
                'The collector is running and waits for the next battle.', view)
    view['battle'] = shown = battle_view(battle, now)
    view['routines'] = runtime.get('routines') if isinstance(runtime.get('routines'), dict) else None
    view['evidence'] = _runtime_dict(battle, 'collection_evidence')
    view['claims'] = claims
    if not shown['publication_valid']:
        return ('unavailable', 'observation_invalid',
                'The collector reported no usable observation time; its state is unknown.', view)
    if not shown['fresh']:
        return ('unavailable', 'stale_observation',
                f"The collector last reported {int(shown['observation_age_sec'])} s ago; its state is unknown.", view)
    if battle['status'] != 'active' or shown['observation_status'] != 'observing':
        return 'waiting_for_battle', 'waiting_for_battle', 'The collector waits for a battle on screen.', view
    gates = gem_gates(view['routines'])
    if any(gates[name] is True for name in ROUTINES):
        if claims and claims['failing']:
            return ('collecting', 'claims_failing',
                    f"Ad gem claims failing: {claims['failure_streak']} in a row.", view)
        return 'collecting', 'collecting', 'Collecting gems in the battle on screen.', view
    if all(gates[name] is False for name in ROUTINES):
        return ('live_paused', 'gem_routines_paused',
                'Both gem routines are switched off for this battle; nothing is collected.', view)
    return ('unavailable', 'routines_not_reported',
            'The collector did not report its gem routines, so collecting cannot be confirmed.', view)


def owner_view(op, battle, row, now, harvest_revision, claims=None):
    """Another driver (an operation or an unknown process) that holds the emulator."""
    if op is None:
        return dict(kind='process', operation_id=None, operation_kind=None, status=None, pid=row.get('pid'),
                    script=row.get('runner'), foreign=True, harvest_managed=False,
                    gems=dict(ad_gems=None, floating_gems=None, basis='not_reported'), gathers=None, routines=None,
                    evidence=None, claims=None, recovery=None)
    owned = battle if battle is not None and battle.get('operation_id') == op['id'] else None
    runtime = (owned or {}).get('runtime') or {}
    routines = runtime.get('routines') if isinstance(runtime.get('routines'), dict) else None
    shown = battle_view(owned, now) if owned else None
    gates = gem_gates(routines)
    if owned is None or routines is None:
        basis = 'not_reported'
    elif not shown['fresh']:
        basis, gates = 'stale', dict.fromkeys(ROUTINES)
    else:
        basis = 'published_routines'
    values = [gates[name] for name in ROUTINES]
    gathers = True if True in values else False if values and all(value is False for value in values) else None
    worker = op.get('worker') or {}
    stoppable = op['status'] in ('accepted', 'running', 'cancelling') and not operations.synchronous_persistence(op)
    return dict(kind='operation', operation_id=op['id'], operation_kind=op['kind'], status=op['status'],
                pid=worker.get('pid'), script=None, foreign=False, harvest_managed=False,
                gems=dict(gates, basis=basis), gathers=gathers, routines=routines, battle=shown,
                evidence=_runtime_dict(owned, 'collection_evidence'), claims=claims,
                recovery=(dict(action='stop_operation', operation_id=op['id'], expected_revision=op['revision'])
                          if stoppable else None))


# --------------------------------------------------------------- overlay
def _overlay_key(choice, battle):
    """One command per choice revision and battle generation, whatever the source
    says later: a source edit follows at the next battle, or at an explicit
    re-apply (a new revision), never as a second command on this one."""
    return f"{OVERLAY_PREFIX}{battle['id']}-{battle['generation']}-{choice['revision']}"


def _command_state(command):
    state = command.get('state')
    return {'requested': 'requested', 'pending': 'pending', 'applied': 'applied',
            'rejected': 'rejected', 'expired': 'expired'}.get(state, 'requested')


def _frozen_targets(frozen, battle):
    """The recorded first decision for this battle generation, or None. `frozen` is
    the revision's map of decisions keyed by (battle, generation)."""
    record = frozen.get(freeze_key(battle['id'], battle['generation'])) if isinstance(frozen, dict) else None
    return dict(record['targets']) if valid_freeze(record, battle['id'], battle['generation']) else None


def plan_overlay(choice, source, battle, commands, *, applicable=True, frozen=None):
    """The one gem-routine command this choice asks of a full driver's battle.

    Returns a view dict with 'patch' (what to request, possibly empty) and
    'key'; pure over the snapshot. applicable is False when the choice is bound
    to another account, profile or emulator than the driver runs in: nothing is
    then asked of that driver. expected_control_revision is the battle control
    revision this plan was read at; the command is recorded only if it is still
    the current one.

    The targets are decided once per choice revision and battle generation: the
    first reconciliation that reaches the battle records them (view['freeze'], for
    the lifecycle host to persist), and `frozen` hands that record back, so a later
    edit of the selected run changes nothing within this generation, whether or not
    the first decision needed a command."""
    revision = choice['revision']
    view = dict(battle_id=battle['id'], generation=battle['generation'], choice_revision=revision,
                request_key=None, command_id=None, state='not_requested', paths=[], skipped={}, targets={},
                cadence_source='current_battle', patch={},
                expected_control_revision=battle['control_revision'])
    if not applicable:
        view['state'] = 'blocked'
        return view
    if choice['enabled']:
        if not source or source.get('valid') is not True:
            view['state'] = 'blocked'
            return view
        targets = {name: bool(source['policy'][name]['enabled']) for name in ROUTINES}
    else:
        targets = dict.fromkeys(ROUTINES, False)
    held = _frozen_targets(frozen, battle)
    if held is not None:
        targets = held
    view['targets'] = targets
    view['request_key'] = key = _overlay_key(choice, battle)
    if battle['status'] != 'active':
        view['state'] = 'waiting_for_battle'
        return view
    if held is None:
        view['freeze'] = dict(battle_id=battle['id'], generation=battle['generation'], targets=dict(targets))
    existing = next((body for request_key, body in commands if request_key == key), None)
    if existing is not None:
        sent = flatten(existing.get('payload') or {})
        for name in ROUTINES:   # what was actually asked, not what the source says now
            if CONTROL_PATHS[name] in sent:
                view['targets'][name] = sent[CONTROL_PATHS[name]]
        view.update(command_id=existing['id'], state=_command_state(existing), paths=sorted(sent))
        return view
    capabilities = set((battle.get('capabilities') or {}).get('controls', []))
    people = set()
    for request_key, body in commands:
        if (body.get('kind') == 'controls' and not request_key.startswith(OVERLAY_PREFIX)
                and body.get('state') not in ('rejected', 'expired')):
            people.update(flatten(body.get('payload') or {}))
    requested = flatten(battle.get('requested_controls') or {})
    patch = {}
    for name in ROUTINES:
        path = CONTROL_PATHS[name]
        if path not in capabilities:
            view['skipped'][name] = 'unsupported'
        elif path in people:
            view['skipped'][name] = 'live_choice'
        elif requested.get(path) is targets[name]:
            view['skipped'][name] = 'unchanged'
        else:
            patch[name] = {'enabled': targets[name]}
    if patch:
        view['patch'] = {'routines': patch}
        view['paths'] = sorted(CONTROL_PATHS[name] for name in patch)
        return view
    reasons = set(view['skipped'].values())
    view['state'] = ('unsupported' if reasons == {'unsupported'} else
                     'live_choice' if 'live_choice' in reasons and 'unsupported' not in reasons else
                     'not_needed' if reasons <= {'unchanged'} else 'live_choice')
    return view


def public_overlay(view):
    out = dict(view)
    out.pop('patch', None)
    out.pop('freeze', None)
    out.pop('expected_control_revision', None)
    return out


# ----------------------------------------------------------- assessment
def _failures(autos):
    """Consecutive fast failures of this revision's collectors, newest first, and the last exit."""
    failures, last = 0, None
    for op in autos:
        if op['status'] not in TERMINAL:
            continue
        ran = max(0.0, op['updated_at'] - op['created_at'])
        if last is None:
            error = op.get('error') or {}
            last = dict(kind='exited', operation_id=op['id'], status=op['status'], error=error.get('code'),
                        message=str(error['message'])[:300] if error.get('message') else None,
                        ran_sec=round(ran, 1), at=op['updated_at'])
        refused = (op.get('error') or {}).get('code') == 'CollectorRefused'
        if SCHEDULE.fast_failure(op['status'], ran, refused):
            failures += 1
        else:
            break
    return failures, last


def _base(now, identity, host):
    return dict(version=VERSION, server_time=now, host=host or dict(reconciler='inactive', last_tick_at=None, last_error=None),
                identity={key: identity.get(key) for key in BOUND_KEYS}, desired=desired_view(None), source=None,
                state='unavailable', code=None, message=None, operation=None, owner=None, overlay=None,
                failures=0, circuit_open=False, attempts=0, next_attempt_at=None, last_outcome=None, transfer=None,
                input_revoked=False, collection_guard=None)


def unavailable(now, code, message, identity=None, host=None):
    view = _base(now, identity or {}, host)
    view.update(state='unavailable', code=code, message=message)
    return view, dict(launchable=False, cancel=[], overlay=None, choice=None, autos=[], source=None, op=None)


def evaluate(root, cfg, *, processes, resolve_source, alive=None, now=None, host=None):
    """(view, plan): the status projection and what the lifecycle host should do.
    Read-only. processes: runner rows already projected through the ledger."""
    alive = _alive(alive)
    try:
        identity = identity_for(cfg)
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        return unavailable(time.time() if now is None else now, 'context_unavailable', str(exc), host=host)
    try:
        facts = read_facts(root, identity)
    except (ValueError, sqlite3.Error, OSError) as exc:
        return unavailable(time.time() if now is None else now, 'harvest_state_unreadable',
                           f'Harvesting records could not be read ({type(exc).__name__}); nothing will be started.',
                           identity, host)
    now = time.time() if now is None else now   # sampled after the read: stored times are never later
    choice, runtime = facts['choice'], facts['runtime']
    view = _base(now, identity, host)
    from runtime import device_lease
    view['input_revoked'] = bool(device_lease.is_revoked(identity['serial'], identity['adb_host'], identity['adb_port']))
    view['desired'] = desired_view(choice)
    plan = dict(launchable=False, cancel=[], overlay=None, choice=choice, autos=facts['automatic'], source=None, op=None)
    source = None
    if choice and choice.get('preset'):
        source = resolve_source(cfg, choice['preset'])
        view['source'] = source_view(source, choice)
    plan['source'] = source
    failures, last = _failures(facts['automatic'])
    view.update(failures=failures, attempts=len(facts['automatic']))
    outcomes = [o for o in (last, runtime.get('last_outcome')) if isinstance(o, dict)]   # on a tie the exit is the later event
    view['last_outcome'] = max(outcomes, key=lambda o: o.get('at') if finite(o.get('at')) else 0) if outcomes else None
    transfer = runtime.get('transfer')
    holding = (isinstance(transfer, dict) and finite(transfer.get('until')) and transfer['until'] > now)
    view['transfer'] = dict(until=transfer['until'], reason=transfer.get('reason')) if holding else None
    newest = facts['automatic'][0] if facts['automatic'] and facts['automatic'][0]['status'] in TERMINAL else None
    refusal_at = runtime.get('next_attempt_at') if finite(runtime.get('next_attempt_at')) else None
    schedule, _ = SCHEDULE.evaluate(now, dict(
        newest_created_at=None if newest is None else newest['created_at'],
        newest_updated_at=None if newest is None else newest['updated_at'], failures=failures, refusal_at=refusal_at))
    view.update(next_attempt_at=schedule.next_attempt_at, circuit_open=schedule.kind == 'blocked')
    enabled = bool(choice and choice['enabled'])

    def done(state, code, message):
        view.update(state=state, code=code, message=message)
        return view, plan

    collectors = [op for op in facts['active'] if op['kind'] == 'gems']
    drivers = [op for op in facts['active'] if op['kind'] != 'gems']
    foreign = [row for row in processes if not row.get('operation_id')]
    battle = facts['battle']
    if collectors:
        op = plan['op'] = collectors[0]
        state, code, message, shown = collector_state(op, battle, alive, now, battle_claims(facts, op))
        view['operation'] = shown
        managed = shown['harvest_managed']
        if managed:
            stale = (not enabled or shown['desire_revision'] != choice['revision'])
        else:   # a person's own collector: only an Off recorded after it started asks it to stop
            stale = choice is not None and not enabled and covered_by_off(op, choice)
        invalid = managed and enabled and source is not None and source.get('valid') is not True
        if stale and op['status'] != 'cancelling':
            plan['cancel'].append(op)
            return done('stopping', 'harvest_off' if not enabled else 'harvest_changed',
                        'Harvesting changed; the standalone collector is being stopped. The battle is not ended.')
        if invalid and op['status'] != 'cancelling':
            plan['cancel'].append(op)
            return done('blocked', 'policy_invalid', 'The selected run can no longer collect gems; the collector is being stopped.')
        return done(state, code, message)
    if drivers or foreign:
        op = plan['op'] = drivers[0] if drivers else None
        owner = view['owner'] = owner_view(op, battle, foreign[0] if foreign else {}, now,
                                           choice['revision'] if choice else 0, battle_claims(facts, op))
        # The choice acts on a driver only inside the context it was made for: the
        # dashboard's own account/profile/emulator AND the driver's accepted one.
        in_context = (choice is not None and op is not None and context_matches(choice.get('bound'), identity)
                      and context_matches(choice.get('bound'), op['context']))
        if op is not None and choice is not None and battle is not None and battle.get('operation_id') == op['id']:
            overlay = plan_overlay(choice, source, battle, facts['commands'], applicable=in_context,
                                   frozen=runtime.get('overlay_freezes'))
            view['overlay'] = public_overlay(overlay)
            plan['overlay'] = overlay
        if enabled and op is not None and not in_context:
            return done('blocked', 'context_changed',
                        'Harvesting was turned on for another account, profile or emulator than the running '
                        'automation uses; turn it on again here.')
        if op is None:
            return done('owned_by_driver', 'process_owns_device',
                        f"Another automation process ({owner['script']}, PID {owner['pid']}) is using the emulator "
                        'and is not tracked here, so gem handling is unknown.')
        if enabled and owner['gathers'] is False:
            return done('owned_by_driver', 'owner_gathers_off',
                        'Running automation owns the emulator and its own gem routines are off for this battle.')
        return done('owned_by_driver', 'operation_owns_device',
                    'Running automation owns the emulator and handles gems with its own battle controls.')
    if holding:
        return done('transferring', 'run_transfer', 'A run is taking over the emulator; harvesting resumes after it exits.')
    if choice is None:
        return done('off', 'never_enabled', 'Harvesting has not been turned on for this emulator.')
    if not enabled:
        return done('off', 'paused', 'Harvesting is off.')
    bound = choice.get('bound') or {}
    if any(bound.get(key) != identity.get(key) for key in BOUND_KEYS):
        return done('blocked', 'context_changed',
                    'Harvesting was turned on for another account, profile or emulator; turn it on again here.')
    if source is None or source.get('valid') is not True:
        return done('blocked', 'policy_invalid', (source or {}).get('message') or 'The selected run is unavailable.')
    if view['circuit_open']:
        return done('blocked', 'collector_failing',
                    f'The collector failed {failures} times in a row; turn harvesting on again to retry.')
    instance = (cfg.get('instances') or {}).get(identity['instance']) or {}
    if not instance.get('allow_taps'):
        return done('blocked', 'taps_disabled', 'Game input is disabled for this emulator.')
    if view['input_revoked']:
        return done('blocked', 'input_revoked',
                    'Automation input was revoked for this emulator; allow taps again to resume harvesting.')
    guard = view['collection_guard'] = battles.setup_battle_guard(root, identity, scope='collection')
    if guard['blocks_launch']:
        return done('blocked', guard['code'], guard['message'])
    if view['next_attempt_at'] is not None:
        last_refusal = runtime.get('last_outcome') or {}
        if refusal_at is not None and refusal_at > now and last_refusal.get('kind') == 'refused':
            return done('blocked', last_refusal.get('code'), last_refusal.get('message'))
        return done('backing_off', 'backing_off', 'Waiting before starting the collector again.')
    plan['launchable'] = True
    return done('starting', 'launch_due', 'The collector will start for the battle on screen.')


def status(root, cfg, *, processes, resolve_source, alive=None, now=None, host=None):
    """GET /api/status projection. Reads one snapshot; writes, launches and sends nothing."""
    return evaluate(root, cfg, processes=processes, resolve_source=resolve_source, alive=alive, now=now, host=host)[0]


# --------------------------------------------- a collector found at start
COLLECTOR_RUNNER = 'gem_collector'


def _found(code, adopted, reason, op=None, row=None, now=None):
    worker = (op or {}).get('worker') or {}
    row = row or {}
    started = worker.get('creation_time') if op else row.get('created')
    return dict(code=code, adopted=adopted, reason=reason, operation_id=(op or {}).get('id'),
                pid=worker.get('pid') if op else row.get('pid'), started_at=started if finite(started) else None,
                found_at=now)


def collector_at_start(root, cfg, *, processes, resolve_source, alive=None, now=None):
    """What a dashboard that has just started found of a collector from before it: None when there was none,
    else one report {code, adopted, reason, operation_id, pid, started_at, found_at}.

    Adopting needs no act of its own: the ledger outlives the dashboard, so the next tick finds the collector's
    operation active and launches nothing (evaluate). This only says what that tick will do. code is the one key
    the Dashboard words its line from:
      adopted     the worker is verified alive and the tick keeps it
      will_stop   alive, but it is stopping or the choice asks the tick to stop it (reason: stopping, harvest_off,
                  harvest_changed or policy_invalid)
      dead        the ledger records a collector whose worker is proven gone; the tick retires it
      unverified  its worker cannot be checked (reason worker_unverified) or has not started (starting); it stays
                  the owner, nothing is launched over it
      untracked   a collector process the ledger does not know (reason not_tracked); it cannot be adopted
    Read-only: call it before the first tick, which is what retires a dead record. processes: runner rows."""
    now = time.time() if now is None else now
    view, plan = evaluate(root, cfg, processes=processes, resolve_source=resolve_source, alive=alive, now=now)
    if view['code'] in ('context_unavailable', 'harvest_state_unreadable'):
        raise ValueError(view['message'])    # nothing was assessed: an unchecked start is not a start with no collector
    op = plan['op']
    if op is not None and op['kind'] == 'gems':
        # One assessment: evaluate read the ledger and asked about the worker once, and every answer below is its.
        liveness = view['operation']['worker_alive']
        if op['status'] == 'accepted':
            return _found('unverified', False, 'starting', op, now=now)
        if liveness is False:
            return _found('dead', False, 'worker_not_running', op, now=now)
        if liveness is not True:
            return _found('unverified', False, 'worker_unverified', op, now=now)
        if op['status'] == 'cancelling':
            return _found('will_stop', False, 'stopping', op, now=now)
        if any(stopped['id'] == op['id'] for stopped in plan['cancel']):
            return _found('will_stop', False, view['code'], op, now=now)
        return _found('adopted', True, 'running', op, now=now)
    for row in processes:
        if not row.get('operation_id') and row.get('runner') == COLLECTOR_RUNNER:
            return _found('untracked', False, 'not_tracked', row=row, now=now)
    return None

# ------------------------------------------------------------- stopping
def active_collectors(root, identity):
    """Active collector operations on this emulator, whoever started them, dead workers retired first."""
    found = []
    for op in operations.ledger(root).active(identity['device_key']):
        op = operations.resolve(root, op['id'])
        if op['kind'] == 'gems' and op['status'] in ('accepted', 'running', 'cancelling'):
            found.append(op)
    return found


def covered_by_off(op, choice):
    """Does the person's Off ask this standalone collector to stop?

    Every collector the lifecycle host launched, and a collector a person
    started that was already running when the Off was recorded. One started
    after the Off is a newer, explicit request and is left alone, which also
    keeps a late replay of an old Off from stopping it."""
    changed = choice.get('changed_at')
    return op['request'].get('harvest') is True or not finite(changed) or op['created_at'] <= changed


def stop_collectors(root, identity, *, select=None):
    """Stop collector input on this emulator (all of them, or those select(op)
    accepts). Returns {requested, stopped, stopping, errors}."""
    return stop_operations(root, [op for op in active_collectors(root, identity) if select is None or select(op)])


def stop_operations(root, ops):
    """Cancel these collector operations. Cancellation terminates the
    collector's worker and never ends or surrenders the battle. 'stopping'
    means a worker is still exiting: the stop is requested, input is not yet
    proven stopped."""
    result = dict(requested=True, stopped=[], stopping=[], errors=[])
    for op in ops:
        try:
            op = operations.cancel(root, dict(operation_id=op['id'], expected_revision=op['revision']))
        except Conflict as exc:
            result['errors'].append(dict(operation_id=op['id'], message=str(exc)))
            continue
        if op['status'] == 'cancelling':
            op = operations.stop_driver(root, op['id'])   # a stuck stop is retried, never assumed
        (result['stopped'] if op['status'] == 'cancelled' else result['stopping']).append(op['id'])
    return result


# ------------------------------------------------------------ the choice
def _normalize(body):
    if not isinstance(body, dict):
        raise ValueError('Harvesting request must be an object')
    key = body.get('idempotency_key')
    import re
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', key):
        raise ValueError('idempotency_key is required (8-100 letters, digits, - or _)')
    enabled = body.get('enabled')
    if type(enabled) is not bool:
        raise ValueError('enabled must be true or false')
    expected = body.get('expected_context')
    if not isinstance(expected, dict):
        raise ValueError('expected_context is required')
    needed = BOUND_KEYS if enabled else ('instance', 'serial')
    if any(key_ not in expected for key_ in needed):
        raise ValueError('expected_context must name ' + ', '.join(needed))
    if enabled:
        if not isinstance(body.get('preset'), str) or not body['preset']:
            raise ValueError("Choose a run's diamond settings")
        if type(body.get('expected_revision')) is not int or body['expected_revision'] < 0:
            raise ValueError('expected_revision is required to turn harvesting on')
    elif body.get('expected_revision') is not None and (type(body['expected_revision']) is not int
                                                        or body['expected_revision'] < 0):
        raise ValueError('expected_revision must be a whole number')
    return dict(enabled=enabled, preset=body.get('preset') if enabled else None,
                expected_revision=body.get('expected_revision') if enabled else None,
                expected_context={key_: expected.get(key_) for key_ in needed}), key


def apply_choice(root, cfg, body, *, resolve_source, now=None, event=None):
    """Record the person's On/Off with a durable request identity.

    Returns {choice, replayed, stop}. An identical retry is answered from its
    receipt even after the revision advanced; Off re-runs its stop only while
    that Off is still the current choice. ValueError -> invalid request;
    ChoiceRefused / Conflict -> a definite refusal that recorded nothing.
    """
    event = event or (lambda *args, **kwargs: None)
    request, key = _normalize(body)
    identity = identity_for(cfg)
    fingerprint = digest(request)
    with readonly(operations.ledger_path(root)) as db:   # a refusal before this point creates nothing
        recorded = None if db is None else read_receipt(db, key)
    label = bound = None
    if recorded is None:
        expected = request['expected_context']
        if any(expected.get(name) != identity.get(name) for name in expected):
            raise ChoiceRefused('context_changed', 'Account, profile or emulator changed; review harvesting again')
        if request['enabled']:
            source = resolve_source(cfg, request['preset'])
            if source.get('valid') is not True:
                raise ChoiceRefused('policy_invalid', source.get('message') or 'That run cannot collect gems',
                                    source=source)
            label = source.get('label')
            bound = {name: identity.get(name) for name in BOUND_KEYS}
            bound['policy_revision'] = source['policy_revision']
        else:
            bound = {name: identity.get(name) for name in BOUND_KEYS}
    try:
        choice, replayed = store(root).set_choice(
            identity, enabled=request['enabled'], preset=request['preset'], label=label,
            expected_revision=request['expected_revision'], bound=bound, request_key=key, fingerprint=fingerprint,
            reason='enabled_by_person' if request['enabled'] else 'paused_by_person', changed_by='person', now=now)
    except HarvestRevisionChanged as exc:
        raise ChoiceRefused('harvest_revision_changed', str(exc), desired=desired_view(exc.current)) from exc
    except HarvestKeyReused as exc:
        raise ChoiceRefused('idempotency_key_reused', str(exc)) from exc
    # The choice is committed. Nothing below may raise: a failure is reported in
    # the stop result, never as a refusal that says nothing was recorded.
    stop = dict(requested=False, stopped=[], stopping=[], errors=[])
    try:
        with readonly(operations.ledger_path(root)) as db:
            current = read_choice(db, identity['device_key'])
        if not choice['enabled'] and current and current['revision'] == choice['revision']:
            stop = stop_collectors(root, identity, select=lambda op: covered_by_off(op, current))
        event('harvest_choice_recorded', revision=choice['revision'], enabled=choice['enabled'],
              preset=choice.get('preset'), replayed=replayed, outcome='recorded')
    except Exception as exc:   # noqa: BLE001 - see above
        stop['errors'].append(dict(operation_id=None, message=f'{type(exc).__name__}: {exc}'))
    return dict(choice=choice, replayed=replayed, stop=stop)


def pause(root, cfg_or_identity, reason, request_key, *, effects=None):
    """Turn harvesting off after an explicit Stop so the lifecycle host does not
    undo the person's Stop. Idempotent by request_key; no-op when not on.

    The receipt key carries the choice revision it paused: a repeated Stop after the person turned
    harvesting on again is a new pause, not a reuse of the old receipt. `effects`, when given, records
    that THIS call changed the choice (effects['paused']); a call that only replays a pause another request
    just wrote (two Stops at the same moment) changed nothing and says so."""
    identity = (cfg_or_identity if 'device_key' in cfg_or_identity else identity_for(cfg_or_identity))
    with readonly(operations.ledger_path(root)) as db:
        choice = None if db is None else read_choice(db, identity['device_key'])
    if choice is None or not choice['enabled']:
        return choice
    chosen, replayed = store(root).set_choice(
        identity, enabled=False, preset=None, label=None, expected_revision=None, bound=choice.get('bound'),
        request_key=f"{request_key}@{choice['revision']}",
        fingerprint=digest(dict(pause=reason, revision=choice['revision'])), reason=reason, changed_by='person')
    if effects is not None and not replayed:
        effects['paused'] = True
    return chosen


# ---------------------------------------------------------- reconciliation
def _record(root, identity, revision, **changes):
    """Visible bookkeeping only; a failure here never changes what was decided."""
    try:
        store(root).update_runtime(identity['device_key'], revision, **changes)
    except (sqlite3.Error, OSError, ValueError):
        pass


def action_session(root, cfg):
    """The recorder of this dashboard's launch attempts, bound to the context a choice is made for."""
    from runtime.action_session import ActionSession
    identity = identity_for(cfg)
    return ActionSession(operations.ledger_path(root), driver='dashboard',
                         context={key: identity.get(key) for key in ('device_key', 'account', 'instance', 'serial', 'profile')})


def tick(root, cfg, *, processes, resolve_source, prepare, launch, device_state, alive=None, now=None, event=None,
         actions=None):
    """One reconciliation step. Only the lifecycle host calls this, never a GET.

    processes: runner rows projected through the ledger (operation_id set for
    tracked workers). prepare(cfg, preset) -> an opaque launch plan, or raises
    Refused (run settings, digits). launch(cfg, body, plan) -> the accepted
    operation, through ordinary admission. device_state(identity) -> adb state
    ('device' when online); called only right before a launch.

    With nothing ever chosen it reads and writes nothing. Returns the status it
    ended on, with 'action'.
    """
    event = event or (lambda *args, **kwargs: None)
    kwargs = dict(processes=processes, resolve_source=resolve_source, alive=alive)
    identity = identity_for(cfg)
    view, plan = evaluate(root, cfg, now=now, **kwargs)
    choice = plan['choice']
    if choice is None:
        return dict(view, action='none')
    retired = False
    for op in operations.ledger(root).active(identity['device_key']):
        retired |= operations.resolve(root, op['id'])['status'] != op['status']
    if retired:
        view, plan = evaluate(root, cfg, now=now, **kwargs)
    current = time.time() if now is None else now

    def finish(action):
        again, _ = evaluate(root, cfg, now=now, **kwargs)
        return dict(again, action=action)
    if plan['cancel']:
        outcome = stop_operations(root, plan['cancel'])
        event('harvest_collector_stopped', reason=view['code'], stopped=outcome['stopped'],
              stopping=outcome['stopping'], errors=outcome['errors'])
        return finish('stopped_collector')
    overlay = plan['overlay']
    if overlay is not None and overlay.get('freeze'):
        # The first decision for this battle generation is fixed before anything is asked of the
        # driver, so a later edit of the selected run cannot change it. First writer wins. The
        # write is refused unless this battle is still the live one at this generation, so a plan
        # made for a battle that has since ended is dropped here, never carried into the next one.
        decided = (overlay['battle_id'], overlay['generation'])
        try:
            store(root).freeze_overlay(identity['device_key'], choice['revision'], **{
                key: overlay['freeze'][key] for key in ('battle_id', 'generation', 'targets')})
        except HarvestStale as exc:
            event('harvest_overlay_dropped', battle_id=decided[0], generation=decided[1], reason=str(exc))
            return finish('none')
        event('harvest_overlay_frozen', battle_id=decided[0], choice_revision=choice['revision'],
              targets=overlay['freeze']['targets'])
        view, plan = evaluate(root, cfg, now=now, **kwargs)
        overlay = plan['overlay']
        if overlay is None or (overlay['battle_id'], overlay['generation']) != decided or overlay.get('freeze'):
            # The battle moved on between the record and the re-read: this plan belongs to the
            # old generation. The next step plans the new one from its own facts.
            event('harvest_overlay_dropped', battle_id=decided[0], generation=decided[1], reason='battle_changed')
            return finish('none')
    if overlay is not None and overlay['patch'] and overlay['state'] == 'not_requested':
        op = plan['op']
        planned = dict(revision=choice['revision'], enabled=choice['enabled'])
        try:
            # Compare-and-set: the command is recorded only if the battle's control
            # revision is still the one this plan was read at AND, in the same
            # transaction, the choice is still the one it was planned from. A
            # person's later live choice or a later Off therefore drops this stale
            # plan; the next step recomputes from the new facts.
            command = Battles(operations.ledger_path(root)).request_controls(
                overlay['battle_id'], op['id'], overlay['generation'], overlay['expected_control_revision'],
                overlay['patch'], overlay['request_key'], admit=lambda db, battle: verify_overlay(db, planned, battle))
            event('harvest_overlay_requested', battle_id=overlay['battle_id'], command_id=command['id'],
                  choice_revision=choice['revision'], paths=overlay['paths'])
        except (Conflict, ValueError, KeyError) as exc:   # superseded plan or a pending revision: reread next tick
            event('harvest_overlay_deferred', battle_id=overlay['battle_id'], reason=str(exc))
        return finish('overlay')
    if not plan['launchable']:
        return finish('none')
    source = plan['source']
    revision = choice['revision']

    def refuse(code, message):
        _record(root, identity, revision, next_attempt_at=current + REFUSAL_RETRY_SEC,
                last_outcome=dict(kind='refused', code=code, message=message, at=current))
        event('harvest_launch_refused', code=code, message=message)
        if actions is not None:       # a refusal precedes any operation: identical automatic ones share one counted row
            actions.check('harvest_launch', result='refused', reason=code, config_revision=revision,
                          policy_revision=source.get('policy_revision'))
        return finish('refused')
    state = device_state(identity)
    if state != 'device':
        return refuse('emulator_offline', f'The emulator is not online (adb state {state}).')
    try:
        prepared = prepare(cfg, choice['preset'])
    except Refused as exc:
        return refuse(exc.code, str(exc))
    if prepared.policy_revision != source['policy_revision']:
        # The run's gem settings were edited between planning and preparing. Never
        # launch a hybrid of the two: drop this attempt and plan again next step.
        event('harvest_launch_replanned', planned=source['policy_revision'], prepared=prepared.policy_revision)
        if actions is not None:
            actions.check('harvest_launch', result='no_op', reason='replanned', config_revision=revision,
                          policy_revision=prepared.policy_revision)
        return finish('replanned')
    attempt = len(plan['autos']) + 1
    body = dict(preset=choice['preset'], harvest=True, desire_revision=revision,
                policy_revision=prepared.policy_revision,
                idempotency_key=launch_key(identity['device_key'], revision, attempt))
    try:
        op = launch(cfg, body, prepared.command)
    except HarvestFenceRefused as exc:
        return refuse(exc.code, str(exc))
    except battles.SetupBattleUnresolved as exc:
        return refuse(exc.guard['code'], str(exc))
    except Conflict as exc:
        return refuse('device_owned', str(exc))
    except (OSError, ValueError) as exc:   # the ledger already marked the operation failed; it counts toward the circuit
        _record(root, identity, revision, last_outcome=dict(kind='launch_failed', message=str(exc), at=current))
        event('harvest_launch_failed', error=str(exc))
        if actions is not None:
            actions.record('harvest_launch', result='failed', reason='launch_failed', effect='none',
                           occurrence_key=f'launch:{revision}:{attempt}', request_key=body['idempotency_key'], attempts=1,
                           config_revision=revision, policy_revision=prepared.policy_revision)
        return finish('launch_failed')
    _record(root, identity, revision, last_outcome=dict(kind='launched', operation_id=op['id'], at=current))
    event('harvest_collector_launched', operation_id=op['id'], preset=choice['preset'],
          desire_revision=revision, attempt=attempt)
    if actions is not None:      # one row per durable launch key, whichever dashboard host made the attempt
        actions.record('harvest_launch', result='succeeded', reason='launched', effect='unconfirmed',
                       occurrence_key=f'launch:{revision}:{attempt}', request_key=body['idempotency_key'], attempts=1,
                       refs=dict(operation_id=op['id'], preset=choice['preset']), config_revision=revision,
                       policy_revision=prepared.policy_revision)
    return finish('launched')


# --------------------------------------------------- owners and takeovers
HOLDING = ('accepted', 'running', 'cancelling')


def holds_device(op):
    """An accepted operation holds the emulator from acceptance on, before its
    worker process exists. Dashboard-inline persistence steps never do."""
    return op['status'] in HOLDING and not operations.synchronous_persistence(op)


def owners(root, cfg, rows, *, now=None):
    """Everything that holds this emulator, shaped for a refusal: every accepted
    ledger operation, whether or not its worker has spawned yet, and every
    runner process no operation accounts for. Read-only (a POST path retires
    dead workers first). Who holds a device does not depend on which account is
    selected, so an unreadable account never hides an owner."""
    identity = operations.device_identity(cfg)
    now = time.time() if now is None else now
    try:
        ops = [op for op in operations.Operations.active_readonly(operations.ledger_path(root), identity['device_key'])
               if holds_device(op)]
    except (ValueError, sqlite3.Error, OSError) as exc:
        raise OwnersUnreadable(exc) from exc
    try:
        facts = read_facts(root, identity)
    except (ValueError, sqlite3.Error, OSError):   # an unreadable choice must not hide who owns the emulator
        facts = dict(choice=None, battle=None, windows=None, automatic=[])
    found = []
    revision = facts['choice']['revision'] if facts['choice'] else 0
    for op in ops:
        owner = owner_view(op, facts['battle'], {}, now, revision, battle_claims(facts, op))
        if op['kind'] == 'gems' and op['request'].get('harvest'):
            owner.update(harvest_managed=True,
                         recovery=dict(action='pause_harvesting', operation_id=op['id'], expected_revision=revision))
        found.append(owner)
    found += [owner_view(None, None, row, now, revision) for row in rows if not row.get('operation_id')]
    return found


def device_owners(root, cfg, rows, *, now=None):
    """owners() for a POST path: workers that died without an outcome are
    retired first, and a ledger that cannot be read refuses (OwnersUnreadable)."""
    try:
        projected = operations.project_processes(root, rows)
    except (sqlite3.Error, OSError) as exc:
        raise OwnersUnreadable(exc) from exc
    return owners(root, cfg, projected, now=now)


def lease_owner(cfg, *, now=None):
    """lease_holder() for the emulator cfg selects."""
    return lease_holder(operations.device_identity(cfg), now=now)


def lease_holder(identity, *, now=None):
    """The device lease is the FINAL owner of an emulator: an OS lock held by the
    process that drives it, across checkouts. This is the owner body (shaped like an
    untracked process) when any process holds it, None when it is free, and raises
    OwnersUnreadable when the lock files cannot be probed: an unprobed lease is never
    read as a free one.

    The ledger and the process table can both read empty (a worker of another
    checkout, an operation retired by mistake) while the lease is still held, so a
    context write that needs an idle emulator asks the lease as well."""
    if not identity['serial']:
        return None   # no ADB serial: no lease can exist for it
    from runtime import device_lease
    try:
        state = device_lease.lease_state(identity['serial'], identity['adb_host'], identity['adb_port'])
    except (OSError, ValueError) as exc:
        raise OwnersUnreadable(exc) from exc
    if not state['owned']:
        return None
    owner = owner_view(None, None, {'pid': None, 'runner': None}, time.time() if now is None else now, 0)
    owner['lease'] = dict(held=True, delegated=bool(state['delegated']), input_gate=state['input_gate'],
                          holders=list(state.get('holders') or []))   # live processes the lock files name; [] when none can be proven
    return owner


def only_collectors(found):
    """True when the sole owners are standalone harvest collectors (a run may take over)."""
    return bool(found) and all(owner['kind'] == 'operation' and owner['harvest_managed'] for owner in found)


def begin_transfer(root, identity, reason, *, now=None):
    """Hold off every lifecycle host's collector while a run takes the emulator.
    The choice is untouched: harvesting resumes when the run exits."""
    store(root).hold_transfer(identity['device_key'], TRANSFER_HOLD_SEC, reason, now)


def end_transfer(root, identity):
    store(root).release_transfer(identity['device_key'])


def wait_for_release(root, identity, *, lease_free, timeout=TRANSFER_WAIT_SEC, sleep=time.sleep, clock=time.monotonic):
    """Wait until no operation holds the emulator and its device lease is free.
    Returns (released, waited_sec)."""
    started = clock()
    while True:
        busy = [op for op in operations.ledger(root).active(identity['device_key'])
                if operations.resolve(root, op['id'])['status'] in ('accepted', 'running', 'cancelling', 'applying')]
        if not busy and lease_free():
            return True, round(clock() - started, 3)
        if clock() - started >= timeout:
            return False, round(clock() - started, 3)
        sleep(0.1)