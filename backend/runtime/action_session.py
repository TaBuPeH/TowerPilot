"""Host adapter and projection of the timed-action contract (roadmap item 6).

One `ActionSession` per driver generation. It records executions through `towerpilot_workflows.actions` and
builds the additive `runtime.actions` projection. It never calls a handler and sends no input; it imports no
device, adb or capture module. A recording failure is counted and logged, never raised into the driver: a
handler's guards decide what happens on the screen, and a full disk, a locked ledger or a damaged row must not stop
gem collection. The boundary is every `Exception` on purpose: the history is bookkeeping beside a live driver, so
whatever it raises (a shape nobody anticipated included) is its own failure, counted by reason and logged with its
type, and never the driver's. Only BaseException (an interrupt, an exit) passes through.

The boundary is `contained`, one decorator on every public entry point of the session and of its recorder, around the WHOLE method:
the ledger call, the summary built from the row it returned, the arithmetic around it. A guarantee that covered only the ledger call
let a summary field nobody validated escape into collection; this one cannot, whatever the method comes to do. A test enumerates the
public methods and fails for one that is not contained or not named as contained elsewhere (`project`, by its caller).
"""
import functools
import math
import time

from runtime import logger
from towerpilot_workflows import action_contract, action_history
from towerpilot_workflows.action_contract import SCHEMA_VERSION, SystemClock
from towerpilot_workflows.actions import ActiveExecution, Actions, new_record



def contained(reason, default=None):
    """Run the method so that nothing it raises (Exception) reaches the caller: it is counted under `reason` on the session
    (`unrecorded`), logged with its type, and `default` is returned (called when it is a function, for a fresh list)."""
    def decorate(method):
        @functools.wraps(method)
        def guarded(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except Exception as exc:                 # noqa: BLE001 - the history is bookkeeping beside a live driver
                self._failed(reason, exc)
                return default() if callable(default) else default
        guarded.contained_as = reason
        return guarded
    return decorate


class ActionSession:
    def __init__(self, path, *, context, driver, operation_id=None, identity=None, coverage=None, config_revision=None,
                 policy_revision=None, clock=None, catalog=None):
        """path: the operations ledger. context: device_key/account/instance/serial/profile of the driver.
        identity(): (battle_id, generation) read at each record, so a driver that moves to its next battle
        records under the right one; None for an operation-scoped host. coverage(): what its rows cover ('full', or
        'since_adoption' for a battle this driver joined instead of starting), read the same way; None is 'full'."""
        self.path, self.context, self.driver = str(path), dict(context), driver
        self.operation_id, self._identity, self._coverage = operation_id, identity, coverage
        self.config_revision, self.policy_revision = config_revision, policy_revision
        self.clock, self._catalog = clock or SystemClock(), catalog
        self.unrecorded = {}                 # reason -> count of records that could not be written
        self._store = None
        self._ledger = None
        self._open = {}                      # execution id -> monotonic start
        self.last = {}                       # instance -> summary of the newest finished execution, for the projection

    def _catalog_in_force(self):
        """The catalog this session records and projects against: the one it was given, else the active one."""
        return self._catalog if self._catalog is not None else action_contract.catalog()

    def _coverage_in_force(self):
        """What a row recorded now covers: the host's own callback (`since_adoption` for a battle it joined), else `full`. The one expression both the rows
        (`_body`) and the projection (`project`) read, so what is published about an action is what is stored for it."""
        return self._coverage() if self._coverage else 'full'

    def _projected_coverage(self):
        """`_coverage_in_force` for the projection, which must never raise into a driver: a callback that fails or answers outside the contract is `unknown`."""
        try:
            value = self._coverage_in_force()
        except Exception as exc:                 # noqa: BLE001 - see the module docstring
            self._failed('coverage', exc)
            return 'unknown'
        return value if value in action_contract.COVERAGES else 'unknown'

    # --------------------------------------------------------- factories
    @classmethod
    def for_battle(cls, battle_session, driver='orchestrator', *, tracked_only=False):
        """The session of a tracked battle driver. It follows the driver's battles through `prepare_next`.

        tracked_only: a driver that also acts between battles (the collector's Home claims) records an execution that
        starts while its battle is neither prepared nor active as operation-scoped (no battle id, full coverage),
        never under the battle that already ended."""
        context = dict(battle_session.battle['context'])
        context['profile'] = (battle_session.config or {}).get('active_profile')

        def tracked():
            return not tracked_only or battle_session.battle['status'] in ('prepared', 'active')

        session = cls(battle_session.store.path, context=context, driver=driver, operation_id=battle_session.operation_id,
                      identity=lambda: (battle_session.battle['id'], battle_session.generation) if tracked() else (None, None),
                      coverage=lambda: action_history.battle_coverage(battle_session.battle) if tracked() else 'full')
        battle_session.actions = session
        return session

    @classmethod
    def for_operation(cls, driver):
        """The session of an operation-scoped host (no battle tracking), or None when this process is not running
        under a ledger operation: such a run records nothing, and the caller test names it as uncovered."""
        from runtime.operations import environment_operation
        try:
            ledger, operation_id = environment_operation()
            if ledger is None:
                return None
            context = dict(ledger.get(operation_id)['context'])
        except Exception as exc:             # noqa: BLE001 - an unreadable ledger must not stop a host that never needed it
            logger.event('action_record_failed', reason='session', error=str(exc), error_type=type(exc).__name__)
            return None
        return cls(ledger.path, context=context, driver=driver, operation_id=operation_id)

    # ------------------------------------------------------------ records
    def _actions(self):
        if self._store is None:
            self._store = Actions(self.path)
        return self._store

    def _failed(self, reason, exc):
        self.unrecorded[reason] = self.unrecorded.get(reason, 0) + 1
        try:
            logger.event('action_record_failed', reason=reason, error=str(exc), error_type=type(exc).__name__)
        except Exception:                            # noqa: BLE001 - a failing log must not turn a counted failure into a raised one
            pass

    def _body(self, action, instance, *, trigger, occurrence_key, scheduled_for, command_id, request_key, refs, coverage=None,
              config_revision=None, policy_revision=None, parent_execution=None):
        definition = self._catalog_in_force().find(action)
        if definition is None:
            raise ValueError(f'{action} is not in the action catalog')
        if self.driver not in definition.hosts:
            raise ValueError(f'{action} is not run by a {self.driver} host')
        battle_id, generation = self._identity() if self._identity else (None, None)
        host = dict(driver=self.driver, operation_id=self.operation_id, battle_id=battle_id, generation=generation, parent_execution=parent_execution)
        context = dict(device_key=self.context.get('device_key'), account=self.context.get('account'),
                       instance_name=self.context.get('instance'), profile=self.context.get('profile'))
        return new_record(device=self.context['device_key'], action=action, instance=instance or action, trigger=trigger,
                          occurrence_key=occurrence_key, host=host, context=context, request_key=request_key,
                          command_id=command_id, config_revision=self.config_revision if config_revision is None else config_revision,
                          policy_revision=self.policy_revision if policy_revision is None else policy_revision,
                          scheduled_for=scheduled_for, started_at=self.clock.wall(), refs=refs,
                          coverage=coverage or self._coverage_in_force())

    @contained('begin')
    def begin(self, action, instance=None, *, trigger='automatic', occurrence_key=None, scheduled_for=None,
              command_id=None, request_key=None, refs=None, config_revision=None, policy_revision=None, parent_execution=None):
        """Open an execution; returns its id, or None when it could not be recorded. `parent_execution` is the id of the execution
        this one follows (a dependent action, trigger `dependency`)."""
        try:
            row = self._actions().begin(self._body(action, instance, trigger=trigger, occurrence_key=occurrence_key,
                                                   scheduled_for=scheduled_for, command_id=command_id,
                                                   request_key=request_key, refs=refs, config_revision=config_revision,
                                                   policy_revision=policy_revision, parent_execution=parent_execution))
        except ActiveExecution as exc:               # the one failure with a reason of its own: a live window of this instance is open
            self._failed('open_execution', exc)
            return None
        if not row.get('replayed'):
            self._open[row['id']] = self.clock.mono()
        return row['id']

    def _elapsed_ms(self, started):
        """Monotonic milliseconds since `started`. Unknown (None) when the clock cannot give one - a non-finite reading, or one earlier than the start - never a zero
        made up from it: a duration the row stores as measured must have been measured."""
        elapsed = (self.clock.mono() - started) * 1000
        return elapsed if math.isfinite(elapsed) and elapsed >= 0 else None

    @contained('finish')
    def finish(self, execution_id, *, result, reason=None, effect='none', attempts=0, confirmed=0, evidence=(), refs=None):
        if execution_id is None:
            return None
        started = self._open.pop(execution_id, None)
        duration = None if started is None else self._elapsed_ms(started)
        row = self._actions().finish(execution_id, result=result, reason=reason, effect=effect, attempts=attempts,
                                     confirmed=confirmed, evidence=evidence, finished_at=self.clock.wall(),
                                     duration_ms=duration, refs=refs)
        self._remember(row, result=result, reason=reason, effect=effect, trigger=row['trigger'], duration_ms=row['duration_ms'])
        return row

    @contained('check')
    def check(self, action, instance=None, *, result, reason, trigger='automatic', scheduled_for=None, refs=None,
              config_revision=None, policy_revision=None):
        """A no-input check that ended refused or no-op: one row, or a count on the identical open aggregate."""
        row = self._actions().record_check(
            self._body(action, instance, trigger=trigger, occurrence_key=None, scheduled_for=scheduled_for,
                       command_id=None, request_key=None, refs=refs, config_revision=config_revision,
                       policy_revision=policy_revision), result=result, reason=reason)
        self._remember(row, result=result, reason=reason, effect='none', trigger=trigger, duration_ms=None)
        return row

    @contained('record')
    def record(self, action, instance=None, *, result, reason=None, effect='none', trigger='automatic', occurrence_key=None,
               scheduled_for=None, command_id=None, request_key=None, attempts=0, confirmed=0, evidence=(), refs=None,
               config_revision=None, policy_revision=None):
        """An execution already over, as one terminal row. Returns the row, or None when it could not be written."""
        row = self._actions().record_terminal(
            self._body(action, instance, trigger=trigger, occurrence_key=occurrence_key, scheduled_for=scheduled_for,
                       command_id=command_id, request_key=request_key, refs=refs, config_revision=config_revision,
                       policy_revision=policy_revision),
            result=result, reason=reason, effect=effect, attempts=attempts, confirmed=confirmed, evidence=evidence,
            finished_at=self.clock.wall())
        self._remember(row, result=result, reason=reason, effect=effect, trigger=trigger, duration_ms=None)
        return row

    def _remember(self, row, *, result, reason, effect, trigger, duration_ms):
        """The newest finished execution of an instance, as the projection's `last`. `command_id` is the Run now command that started it
        (None for an automatic one), so one read links a command to its execution."""
        self.last[row['instance']] = dict(execution_id=row['id'], command_id=row.get('command_id'), started_at=row['started_at'],
                                          finished_at=row['finished_at'], duration_ms=duration_ms, result=result, reason=reason,
                                          effect=effect, trigger=trigger)

    @contained('settle', default=list)
    def settle(self):
        """The host's own normal finalization closes what it left open; the refresher does the same for a dead host."""
        store = self._actions()
        settled = store.settle_open(device=self.context['device_key'])
        if store.unusable:
            logger.event('action_history_unusable', rows=store.unusable, where='session')
        return settled

    def recorder(self, action, instance=None):
        return Recorder(self, action, instance or action)

    # --------------------------------------------------------- projection
    def project(self, routines, *, observed_at=None, control_revision=None, variants=None, effective=None):
        """`runtime.actions`, derived from the same routine statuses `runtime.routines` is built from. A routine the
        catalog does not know is not projected. Unknown is None plus a reason, never zero. `effective` is the controls in force
        (`{routine key: {field path: value}}`, the ledger's applied controls): `values` carries each descriptor field's value in force,
        None where nothing says (a field the controls do not hold is unknown, not zero)."""
        now = self.clock.wall()
        items = []
        coverage = self._projected_coverage()
        known = {definition.id: definition for definition in self._catalog_in_force()}
        for key in sorted(routines, key=lambda name: (list(known).index(name.partition('@')[0]) if name.partition('@')[0] in known else 10**6, name)):
            action_id, _, instance = key.partition('@')       # 'ad_gems' or '<action>@<instance>': one item per configured instance
            definition, status = known.get(action_id), routines[key]
            if definition is None:
                continue
            instance = instance or action_id
            active = status.get('state') == 'active'
            due = status.get('next_due_at')
            if not status.get('enabled') and not active:
                state = 'disabled'
            elif active:
                state = 'running'
            else:
                state = 'due' if isinstance(due, (int, float)) and due <= now else 'waiting'
            last = self.last.get(instance)
            applied = (effective or {}).get(key)
            applied = applied if isinstance(applied, dict) else {}
            values = {field.path: applied.get(field.path) for field in definition.fields}
            values['enabled'] = bool(status.get('enabled'))               # the routine's own gate is what runs; the applied value may still be waiting
            items.append(dict(
                id=definition.id, key=key, instance=instance, label=definition.label, state=state, enabled=bool(status.get('enabled')),
                eligibility=dict(blocker=status.get('blocker'), reason=None),
                last=None if last is None else dict(last),
                active=dict(window_ends_at=status.get('window_ends_at')) if active else None,
                next=dict(due_at=due, basis=getattr(definition.schedule, 'basis', None), attempt_at=None,
                          start_estimate=None, reason=None if due is not None else 'unknown'),
                descriptor=dict(definition.descriptor(),
                                variants=[dict(token=token, label=token, refusal=None)
                                          for token in (variants or {}).get(instance, (variants or {}).get(definition.id, []))]),
                values=values, counters=dict(status.get('counters') or {}),
                coverage=coverage))
        meta = dict(self.context)
        return dict(schema_version=SCHEMA_VERSION, published_at=now, observed_at=observed_at or now,
                    ledger_id=self._ledger_id(), control_revision=control_revision,
                    context=dict(device_key=meta.get('device_key'), account=meta.get('account'),
                                 instance_name=meta.get('instance'), profile=meta.get('profile')), items=items)

    def _ledger_id(self):
        """The ledger this driver records into. The driver is a mutation host, so it may create the schema here once."""
        if self._ledger is None:
            try:
                self._actions()
                meta = Actions.read_meta(self.path)
                self._ledger = None if meta is None else meta['ledger_id']
            except Exception as exc:    # noqa: BLE001 - see the module docstring
                self._failed('ledger', exc)
        return self._ledger


class Recorder:
    """What a collector or the shopper calls: open an execution when its window or sweep starts, close it when it
    ends. All failures stay inside the session."""

    def __init__(self, session, action, instance):
        self.session, self.action, self.instance = session, action, instance
        self.execution_id = None

    def _failed(self, reason, exc):
        self.session._failed(reason, exc)

    @contained('recorder')
    def opened(self, *, trigger='automatic', occurrence_key=None, scheduled_for=None, command_id=None, refs=None, parent_execution=None):
        if self.execution_id is not None:      # a window that never reported its end: the new one must not hide it
            self.closed(result='failed', reason='superseded', effect='unknown')
        self.execution_id = self.session.begin(self.action, self.instance, trigger=trigger, occurrence_key=occurrence_key,
                                               scheduled_for=scheduled_for, command_id=command_id, refs=refs,
                                               parent_execution=parent_execution)
        return self.execution_id

    @contained('recorder')
    def closed(self, *, result, reason=None, effect='none', attempts=0, confirmed=0, evidence=(), refs=None):
        execution_id, self.execution_id = self.execution_id, None
        return self.session.finish(execution_id, result=result, reason=reason, effect=effect, attempts=attempts,
                                   confirmed=confirmed, evidence=evidence, refs=refs)

    @contained('recorder')
    def check(self, *, result, reason, trigger='automatic', scheduled_for=None):
        return self.session.check(self.action, self.instance, result=result, reason=reason, trigger=trigger,
                                  scheduled_for=scheduled_for)

    @contained('recorder')
    def record(self, **fields):
        return self.session.record(self.action, self.instance, **fields)