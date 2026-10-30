"""The reusable timed-action contract (roadmap item 6): pure definitions and schedule policies.

Nothing here reads a clock, a random source, a file or a device. A driver that already exists asks a policy
"is this due?" with the numbers it holds, runs its own handler behind its own guards, and records the result
through `towerpilot_workflows.actions`. There is no central scheduler. The host owns the state dict (its own
attributes today), so reset scope and restart behaviour do not change.

Stdlib and this package only (`tests/unit/test_workflow_boundary.py`): the package is installed without the rest of
the backend. Policies not yet used by a migrated caller are declared as parameters only; each gets an
`evaluate` when its first caller moves.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

SCHEMA_VERSION = 1
RESULTS = ('succeeded', 'no_op', 'failed', 'refused', 'cancelled')
EFFECTS = ('confirmed', 'unconfirmed', 'unknown', 'none')
TRIGGERS = ('automatic', 'manual', 'dependency', 'boundary')
STATES = ('running', 'yielded', *RESULTS, 'interrupted', 'unknown')
OPEN_STATES = ('running', 'yielded')            # the states of an execution that is still open
COVERAGES = ('full', 'since_adoption', 'unknown')
BINDING_STATES = ('pending', 'started', 'yielded', 'terminal')


class SystemClock:
    """Production clock. Reads at call time, so a test that patches `time` is honoured."""

    @staticmethod
    def mono() -> float:
        return time.monotonic()

    @staticmethod
    def wall() -> float:
        return time.time()


class FakeClock:
    """Deterministic clock for tests: advance it explicitly, never by waiting."""

    def __init__(self, mono: float = 0.0, wall: float = 1_700_000_000.0):
        self._mono, self._wall = float(mono), float(wall)

    def mono(self) -> float:
        return self._mono

    def wall(self) -> float:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += seconds


@dataclass(frozen=True)
class Field:
    """One control of an action. JSON-serializable, so a generic editor needs no per-action code."""
    path: str
    type: str                      # 'bool' | 'number'
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    label: str = ''
    default: float | None = None   # what an absent setting means, when the action has one
    live: bool = True              # a person may change it on a running battle
    saved: bool = True             # it is part of the saved run settings
    nullable: bool = True          # null removes the override and inherits

    def valid(self, value: Any) -> bool:
        """The validation `control_settings.validate_patch` applies: no coercion, no NaN, inside the bounds."""
        if self.type == 'bool':
            return type(value) is bool
        return type(value) in (int, float) and math.isfinite(value) and self.min <= value <= self.max

    def spec(self):
        """The legacy `FIELDS` entry: `bool` or `(min, max)`."""
        return bool if self.type == 'bool' else (self.min, self.max)

    def to_json(self) -> dict:
        return dict(path=self.path, type=self.type, min=self.min, max=self.max, unit=self.unit, label=self.label,
                    default=self.default, live=self.live, saved=self.saved, nullable=self.nullable)


@dataclass(frozen=True)
class Capabilities:
    live_controls: bool = False
    pause: bool = False
    run_now: bool | str = False    # True, False, or a refusal reason code
    variants: bool = False         # the host binding publishes serialized tokens, e.g. shopping:<section>:<id>

    def to_json(self) -> dict:
        return dict(live_controls=self.live_controls, pause=self.pause, run_now=self.run_now, variants=self.variants)


@dataclass(frozen=True)
class BindingResult:
    """What a host binding reports about a manual request or an active execution.

    `ack` and `diagnostic` serve the command acknowledgement. `ack`, when a binding has its own outcome record (the gem
    collectors and the shopper do, and the Dashboard already publishes it), is the evidence the acknowledgement carries, verbatim;
    without it a terminal result is acknowledged with a record built from `outcome`, `effect`, `reason`, `attempts`, `evidence`
    and `refs` (so a new action needs none). `diagnostic` is the binding's own status snapshot, used only to correlate log lines
    (window, offer, claim attempt, sweep ids) and never published."""
    state: str                     # pending | started | yielded | terminal
    outcome: str | None = None     # terminal: one of RESULTS
    effect: str = 'none'           # terminal: one of EFFECTS
    reason: str | None = None
    attempts: int = 0
    evidence: tuple = ()
    refs: Mapping[str, Any] = field(default_factory=dict)   # window_id, sweep_id, attempt_id: ids the code already mints
    ack: Mapping[str, Any] | None = None
    diagnostic: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.state not in BINDING_STATES:
            raise ValueError(f'Unknown binding state: {self.state}')
        if self.state == 'terminal' and self.outcome is not None and self.outcome not in RESULTS:
            raise ValueError(f'Unknown terminal outcome: {self.outcome}')
        if self.effect not in EFFECTS:
            raise ValueError(f'Unknown effect: {self.effect}')

    def acknowledgement(self) -> dict | None:
        """The evidence of the command acknowledgement this result stands for: the binding's own record when it has one;
        for a terminal result with an outcome, the record built from the result (unconfirmed stays `effect: unconfirmed`);
        otherwise nothing (a pending or started execution has proved nothing yet)."""
        if self.ack is not None:
            return dict(self.ack)
        if self.state == 'terminal' and self.outcome is not None:
            return dict(outcome=self.outcome, effect=self.effect, reason=self.reason, attempts=self.attempts,
                        evidence=list(self.evidence), refs=dict(self.refs))
        return None


@dataclass(frozen=True)
class Decision:
    """What a schedule policy says. Unknown is `None`, never zero."""
    kind: str                      # due | wait | blocked | skip_missed | not_applicable
    occurrence_key: str | None = None
    eligible_at: float | None = None
    next_due_at: float | None = None
    next_attempt_at: float | None = None
    missed: int = 0
    reason: str | None = None
    basis: str = ''


# ------------------------------------------------------------------ bindings
class Binding(Protocol):
    """What a driver registers, for each configured instance, so the generic control, Run now and status seams can reach its
    handler. The handler itself stays in the driver (the gem collectors, the shopper); a binding is the thin adapter in front of
    it (`runtime.action_bindings`). The session never names an action: it asks the binding.

    `configure` and `configure_document` return True when the value is installed and False when it cannot be installed yet
    (the owned flow is finishing), in which case the command stays pending. `request_now` returns False when the handler refuses
    the request (a window is open); `progress` reports the request and returns None when the binding has no opinion. A binding
    that publishes no `progress()` has no Run now."""

    def configure(self, values: Mapping) -> bool: ...
    def configure_document(self, name: str, value: Any) -> bool: ...
    def request_now(self, variant: str | None = None, *, command_id: str | None = None) -> bool: ...
    def progress(self) -> BindingResult | None: ...
    def status(self) -> dict: ...
    def routine(self, *, battle_visible: bool, now: float, epoch: float) -> dict: ...


_INSTANCE = re.compile(r'[a-z0-9][a-z0-9_-]*')       # no dot (a control path splits on it), no '@' and no ':' (a Run now token splits on it)


def split_key(key: str) -> tuple[str, str | None]:
    """A routine key is an action id, or `<action>@<instance>` for one of several configured instances of it."""
    action_id, separator, instance = key.partition('@')
    return action_id, (instance if separator else None)


def routine_key_valid(key) -> bool:
    if not isinstance(key, str):
        return False
    _, instance = split_key(key)
    return instance is None or _INSTANCE.fullmatch(instance) is not None


# ----------------------------------------------------------------- policies
class SchedulePolicy(Protocol):
    """What every implemented policy offers: a pure decision from the numbers the host passes in. It returns a new
    state and never mutates the one it was given; unknown is `None`, never zero."""
    basis: str

    def evaluate(self, now: float, state: Mapping, conditions: Mapping) -> tuple['Decision', dict]: ...


@dataclass(frozen=True)
class StartToStart:
    """Windows that start on a fixed or sampled cadence, independent of how long a window ran.

    State: `next_window_at` (monotonic; 0.0 = nothing scheduled yet, so the first window is due at once) and
    `window_open`. Conditions: `manual`, `interval` (the period skip arithmetic uses) and `skip_duration` (how
    long a window may last). A window that is wholly in the past is skipped, never owed: there is no catch-up.
    """
    basis = 'start_to_start'

    def evaluate(self, now: float, state: Mapping, conditions: Mapping) -> tuple[Decision, dict]:
        if not _clock_time(now):
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)
        new = dict(state)
        nwa, manual = state['next_window_at'], bool(conditions.get('manual'))
        if state.get('window_open'):
            return Decision('wait', next_due_at=nwa or None, reason='window_open', basis=self.basis), new
        if not manual and nwa and now >= nwa + conditions['skip_duration']:
            interval = conditions['interval']
            missed = math.floor((now - nwa) / interval) + 1
            new['next_window_at'] = nwa + missed * interval
            return Decision('skip_missed', next_due_at=new['next_window_at'], missed=missed, basis=self.basis), new
        if manual or now >= nwa:
            return Decision('due', eligible_at=nwa or None, basis=self.basis), new
        return Decision('wait', next_due_at=nwa, basis=self.basis), new

    @staticmethod
    def open_window(now: float, state: Mapping, interval: float, window_sec: float) -> tuple[dict, dict]:
        """The window that starts now: its length is capped by the interval so windows never overlap."""
        duration = min(window_sec, interval)
        window = dict(started_at=now, deadline=now + duration, duration=duration, interval=interval)
        return window, dict(state, next_window_at=now + interval, window_open=True)

    @staticmethod
    def close_window(state: Mapping) -> dict:
        return dict(state, window_open=False)

    @staticmethod
    def reanchor(last_started_at: float, interval: float) -> float:
        """A new interval counts from the last window's start; the schedule is neither reset nor awarded."""
        return last_started_at + interval

    @staticmethod
    def window_end(started_at: float, window_sec: float, deadline: float) -> float:
        """A shorter window shortens the live one and never extends it."""
        return min(deadline, started_at + window_sec)


@dataclass(frozen=True)
class AppearanceDriven:
    """Attempts that open when the host recognizes what to collect, spaced only after input that was not confirmed.

    There is no cadence: nothing is owed while nothing is on screen, and a confirmed claim owes nothing but the host's own input
    pacing. State: `next_window_at` (monotonic; the earliest the next attempt may open, 0.0 = nothing owed) and `window_open`.
    Conditions: `manual` (an explicit request, which waits for neither an appearance nor the spacing) and `appearance` (True only
    when the pixels the host already holds show the thing; unknown is not absence, and authorizes nothing).

    The host keeps three numbers beside that state and passes them to the arithmetic below: the anchor of the spacing (the start of
    the last attempt that sent input and was not confirmed; `None` = no spacing owed), the safety floor an expired attempt leaves,
    and its own input pacing. The arithmetic is here so that one definition decides when the next attempt may open.
    """
    basis = 'appearance_driven'

    def evaluate(self, now: float, state: Mapping, conditions: Mapping) -> tuple[Decision, dict]:
        if not _clock_time(now):
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)
        new = dict(state)
        nwa = state['next_window_at']
        if state.get('window_open'):
            return Decision('wait', next_due_at=nwa or None, reason='window_open', basis=self.basis), new
        if conditions.get('manual'):
            return Decision('due', eligible_at=nwa or None, basis=self.basis), new
        if now < nwa:
            return Decision('wait', next_due_at=nwa, reason='spacing', basis=self.basis), new
        if not conditions.get('appearance'):
            return Decision('wait', next_due_at=nwa or None, reason='no_appearance', basis=self.basis), new
        return Decision('due', eligible_at=nwa or None, basis=self.basis), new

    @staticmethod
    def open_window(now: float, window_sec: float) -> dict:
        """The attempt that starts now. Its length is the window setting alone: spacing never shortens it."""
        return dict(started_at=now, deadline=now + window_sec, duration=window_sec)

    @staticmethod
    def window_end(started_at: float, window_sec: float, deadline: float) -> float:
        """A shorter window shortens the live attempt and never extends it."""
        return StartToStart.window_end(started_at, window_sec, deadline)

    @staticmethod
    def retry_anchor(attempt_started: float, had_input: bool) -> float | None:
        """Only an attempt that really sent input anchors the spacing; one that sent none owes none."""
        return attempt_started if had_input else None

    @staticmethod
    def safety_floor(now: float, backoff: float, input_ready_at: float) -> float:
        """What an expired attempt leaves: no new attempt sooner than the safety backoff or the host's input pacing."""
        return max(now + backoff, input_ready_at)

    @staticmethod
    def spacing_floor(anchor: float | None, interval: float, safety_floor: float) -> float:
        """The earliest the next attempt may open: `interval` after the anchor, never before the safety floor."""
        return max(0.0 if anchor is None else anchor + interval, safety_floor)

    @staticmethod
    def resume_at(now: float, input_ready_at: float) -> float:
        """A confirmed claim resumes watching at once, after the host's input pacing."""
        return max(now, input_ready_at)


@dataclass(frozen=True)
class AfterCompletion:
    """Due once `interval` has strictly passed since the last completion, or at once for a confirmed continuation.

    State: `last_completed_at`. Conditions: `interval`, `continuation_ready`. Every terminal path completes, so
    a failed or empty sweep backs off exactly like a full one.
    """
    basis = 'after_completion'

    def evaluate(self, now: float, state: Mapping, conditions: Mapping) -> tuple[Decision, dict]:
        if not _clock_time(now):
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)
        last, interval = state['last_completed_at'], conditions['interval']
        if conditions.get('continuation_ready'):
            return Decision('due', eligible_at=now, next_due_at=now, reason='continuation', basis=self.basis), dict(state)
        if now - last > interval:
            return Decision('due', eligible_at=last + interval, next_due_at=last + interval, basis=self.basis), dict(state)
        return Decision('wait', next_due_at=last + interval, basis=self.basis), dict(state)

    @staticmethod
    def complete(now: float, state: Mapping) -> dict:
        return dict(state, last_completed_at=now)


@dataclass(frozen=True)
class Backoff:
    """Relaunch spacing after failed attempts, with a circuit.

    `delay(n)` is `min(cap, base * factor**(n-1))` after n consecutive fast failures. A launch is also never
    sooner than `min_spacing` after the previous one began, a refusal is rechecked every `refusal_retry`
    seconds, and `circuit` consecutive fast failures stop relaunching until an explicit On.
    State: `newest_created_at`, `newest_updated_at` (None while an attempt is still running), `failures`,
    `refusal_at`. Facts come from the operations ledger, which stays the authority for the circuit.
    """
    base: int = 30
    factor: int = 2
    cap: int = 900
    circuit: int = 5
    window: int = 120              # an exit sooner than this counts as a failed attempt
    min_spacing: int = 10
    refusal_retry: int = 10
    basis = 'backoff'

    def delay(self, failures: int) -> int:
        return 0 if failures <= 0 else min(self.cap, self.base * self.factor ** (failures - 1))

    def retry_at(self, now: float, failures: int) -> float:
        """The earliest next attempt after the `failures`-th consecutive failure at `now`: for a host that holds ONE deadline of its own (item 7, A3b: the Ultimate Weapon
        retry keeps `rs.uw_next_try` and `rs.uw_fails`, and a success resets the count but leaves the deadline), not the ledger facts `evaluate` reads. Such a host passes
        the held deadline to `evaluate` as `refusal_at` with `failures` 0, so no circuit can open."""
        return now + self.delay(failures)

    def fast_failure(self, status: str, ran_sec: float, refused: bool) -> bool:
        return status in ('failed', 'interrupted') and ran_sec < self.window and not refused

    def evaluate(self, now: float, state: Mapping, conditions: Mapping | None = None) -> tuple[Decision, dict]:
        if not _clock_time(now):
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)
        failures = state.get('failures', 0)
        spacing = None
        if state.get('newest_updated_at') is not None:
            spacing = max(state['newest_updated_at'] + self.delay(failures), state['newest_created_at'] + self.min_spacing)
        refusal_at = state.get('refusal_at')
        waits = [t for t in (spacing, refusal_at) if t is not None and t > now]
        until = max(waits) if waits else None
        if failures >= self.circuit:
            return Decision('blocked', next_attempt_at=until, reason='circuit_open', basis=self.basis), dict(state)
        if until is not None:
            return Decision('wait', next_attempt_at=until, reason='backing_off', basis=self.basis), dict(state)
        return Decision('due', basis=self.basis), dict(state)


def _finite(*values) -> bool:
    return all(type(value) in (int, float) and math.isfinite(value) for value in values)


def _clock_time(*values) -> bool:
    """A reading of a clock: a finite, non-negative real number. Anything else (NaN, infinity, a negative, a bool, a string) is invalid, never zero."""
    return _finite(*values) and all(value >= 0 for value in values)


@dataclass(frozen=True)
class Cooldown:
    """Not again before `floor_sec` after the last time (item 7, A3a: the 300 s reward-badge checks and the legacy side-menu visits).

    State: `next_at` (the earliest the next one may happen, in the host's own clock; 0.0 or absent = nothing owed, so it is due at once).
    A host that schedules its next occurrence by a sampled delay, not a fixed floor (the legacy visits), passes that delay to `after` and
    keeps drawing from its own random source in the order the old code did. Unknown, negative or non-finite numbers are never read as zero:
    a `now` that is not a finite non-negative number, or a `next_at` that is not finite, is `blocked` ("invalid_state"): never due, which is what the old
    comparison did with NaN and +inf. A stored `next_at` in the past, negative included, is just a time that has passed.
    """
    floor_sec: float = 0.0
    recent_window: float = 0.0                              # a repeat sooner than this after the last occurrence waits `recent_delay` instead
    recent_delay: tuple[float, float] | None = None
    basis = 'cooldown'

    def evaluate(self, now: float, state: Mapping, conditions: Mapping | None = None) -> tuple[Decision, dict]:
        next_at = state.get('next_at') or 0.0
        if not _clock_time(now) or not _finite(next_at):
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)
        if now >= next_at:
            return Decision('due', eligible_at=next_at or None, basis=self.basis), dict(state)
        return Decision('wait', next_due_at=next_at, basis=self.basis), dict(state)

    def after(self, now: float, delay: float | None = None) -> float:
        """The next-earliest time once it has happened at `now` (a fixed floor, or the host's sampled delay)."""
        return now + (self.floor_sec if delay is None else delay)

    @staticmethod
    def recent(now: float, last_at: float | None, window: float) -> bool:
        """Did it happen within `window` of now? Never, when there is no last time."""
        return last_at is not None and now - last_at < window

    def delay_range(self, now: float, last_at: float | None, usual: tuple[float, float]) -> tuple[float, float]:
        """The range the host draws its next delay from: `recent_delay` when it happened `recent_window` ago or less, else the host's usual range.
        The draw itself stays with the host, so the order of random draws is the old code's."""
        return self.recent_delay if self.recent_delay is not None and self.recent(now, last_at, self.recent_window) else usual


@dataclass(frozen=True)
class DailyCalendar:
    """Once per calendar day, from a floor hour, closed by a confirmed or an attempted occurrence (item 7, A3a: the daily free-gems store and the chores).

    `tz='utc'`: the day is the UTC date and the occurrence opens `floor_hour` hours after UTC midnight plus a stable offset in
    [-spread_sec, +spread_sec] derived from the host's seed and the day (it survives restarts and does not move the deadline).
    `tz='local'`: the day is the local date and the occurrence opens when the local clock reads `floor_hour` or later (a DST gap or repeat cannot
    grant a second occurrence or hide the day: the day key is the date).
    State: `consumed` (the day string already closed), `retry_at` (the earliest another attempt of the same day, in the host's retry clock; absent = none).
    Conditions: `seed` (utc offset), `retry_now` (the retry clock's now, default `now`).
    """
    tz: str = 'local'
    floor_hour: int = 0
    spread_sec: int = 0
    retry_sec: int = 0
    close_on: str = 'confirmed'
    zone: Any = None               # tz='local' only: the tzinfo whose calendar it reads (a test's fixed-rule zone); None is the machine's local time
    basis = 'calendar'

    def _moment(self, now: float):
        if self.tz == 'utc':
            return datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
        return datetime.datetime.fromtimestamp(now) if self.zone is None else datetime.datetime.fromtimestamp(now, self.zone)

    def day(self, now: float) -> str:
        """The ISO date `now` falls on in this calendar's time zone: the key an occurrence is closed under."""
        return self._moment(now).date().isoformat()

    def offset(self, seed: str, day: str) -> int:
        if not self.spread_sec:
            return 0
        digest = hashlib.sha256((seed + day).encode()).digest()
        return int.from_bytes(digest[:4], 'big') % (2 * self.spread_sec + 1) - self.spread_sec

    def eligible_at(self, day: str, seed: str = '') -> float:
        """When the occurrence of `day` opens, as an epoch time."""
        date = datetime.date.fromisoformat(day)
        if self.tz == 'utc':
            midnight = datetime.datetime.combine(date, datetime.time(), datetime.timezone.utc).timestamp()
            return midnight + self.floor_hour * 3600 + self.offset(seed, day)
        return datetime.datetime.combine(date, datetime.time(self.floor_hour), tzinfo=self.zone).timestamp()

    def _open(self, now: float, day: str, seed: str) -> bool:
        if self.tz == 'utc':
            return now >= self.eligible_at(day, seed)
        return self._moment(now).hour >= self.floor_hour

    def evaluate(self, now: float, state: Mapping, conditions: Mapping | None = None) -> tuple[Decision, dict]:
        conditions = conditions or {}
        seed, retry_now = conditions.get('seed', ''), conditions.get('retry_now', now)
        if not _clock_time(now, retry_now):
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)
        try:
            return self._decide(now, state, seed, retry_now)
        except (OverflowError, OSError, ValueError):        # a time the calendar cannot express (far beyond any real clock)
            return Decision('blocked', reason='invalid_state', basis=self.basis), dict(state)

    def _decide(self, now: float, state: Mapping, seed: str, retry_now: float) -> tuple[Decision, dict]:
        day = self.day(now)
        if state.get('consumed') == day:
            following = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
            return Decision('wait', occurrence_key=day, next_due_at=self.eligible_at(following, seed), reason='consumed', basis=self.basis), dict(state)
        if not self._open(now, day, seed):
            return Decision('wait', occurrence_key=day, next_due_at=self.eligible_at(day, seed), reason='before_floor', basis=self.basis), dict(state)
        retry_at = state.get('retry_at')
        if retry_at is not None and not retry_now >= retry_at:
            return Decision('wait', occurrence_key=day, eligible_at=self.eligible_at(day, seed), next_attempt_at=retry_at, reason='retry', basis=self.basis), dict(state)
        return Decision('due', occurrence_key=day, eligible_at=self.eligible_at(day, seed), basis=self.basis), dict(state)

    def attempt(self, retry_now: float) -> float:
        """The earliest another attempt of the same day, once one was made at `retry_now`."""
        return retry_now + self.retry_sec


@dataclass(frozen=True)
class Elapsed:
    seconds: float = 0.0
    basis = 'elapsed'


@dataclass(frozen=True)
class EventOnly:
    """No time at all: it happens when its event does (item 7, A3a: the guild-store follow-up after a guild claim landed).

    State: `pending` (the event happened and has not been consumed). Due while pending; otherwise there is nothing to wait for and the next
    time is unknown (`None` plus the reason, never zero).
    """
    reason: str = 'event'
    basis = 'event'

    def evaluate(self, now: float, state: Mapping, conditions: Mapping | None = None) -> tuple[Decision, dict]:
        if state.get('pending'):
            return Decision('due', reason=self.reason, basis=self.basis), dict(state)
        return Decision('wait', reason=self.reason, basis=self.basis), dict(state)

    @staticmethod
    def raised(state: Mapping) -> dict:
        return dict(state, pending=True)

    @staticmethod
    def consumed(state: Mapping) -> dict:
        return dict(state, pending=False)


# ---------------------------------------------------------------- definitions
@dataclass(frozen=True)
class ActionDefinition:
    id: str
    label: str
    family: str                    # gems | rewards | chores | rules | cycles | dayplan | harvest | setup
    hosts: frozenset
    schedule: Any
    fields: tuple = ()
    capabilities: Capabilities = Capabilities()
    saved: str | None = None       # name of the SavedAdapter the dashboard registers for it; None = live only
    restricted: str | None = None  # reason (never empty); non-None means never user-triggered, and the definition can never also offer Run now
    evidence: tuple = ()           # outcome reasons that prove a game effect
    consumes: str | None = None    # 'confirmed' | 'attempted' (calendar families)
    documents: tuple = ()          # whole-object controls the action owns (top-level control keys, replaced wholesale, never merged)
    saved_kinds: frozenset | None = None   # blueprint kinds that carry its saved controls; None = every kind
    scope: str | None = None       # who its persisted daily state belongs to: 'installation' (one file for every emulator), 'account', or None (process state only)

    def __post_init__(self):
        # A restricted action is one nobody may start by hand (a tournament entry, a ticket, a surrender). The definition can never say otherwise: the
        # contradiction cannot be built, and `runtime.action_bindings.run_now_tokens` refuses it again at the point where tokens are published.
        if self.restricted is not None and (not isinstance(self.restricted, str) or not self.restricted.strip()):
            raise ValueError(f'{self.id}: a restricted action needs a reason')
        if self.restricted is not None and self.capabilities.run_now is True:
            raise ValueError(f'{self.id}: a restricted action cannot be offered Run now')

    def field(self, path: str) -> Field | None:
        return next((item for item in self.fields if item.path == path), None)

    def saved_for(self, kind) -> bool:
        """Does a run of this blueprint kind carry the action's saved controls?"""
        return self.saved is not None and (self.saved_kinds is None or kind in self.saved_kinds)

    def descriptor(self) -> dict:
        return dict(fields=[item.to_json() for item in self.fields], capabilities=self.capabilities.to_json())


class Catalog:
    """Data only: nothing here imports cv2, adb or settings."""

    def __init__(self, definitions=()):
        self._definitions: dict[str, ActionDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ActionDefinition) -> ActionDefinition:
        if definition.id in self._definitions:
            raise ValueError(f'Action already registered: {definition.id}')
        self._definitions[definition.id] = definition
        return definition

    def get(self, action_id: str) -> ActionDefinition:
        return self._definitions[action_id]

    def find(self, action_id: str) -> ActionDefinition | None:
        return self._definitions.get(action_id)

    def __contains__(self, action_id: str) -> bool:
        return action_id in self._definitions

    def __iter__(self):
        return iter(self._definitions.values())

    def copy(self) -> 'Catalog':
        return Catalog(self._definitions.values())

    def control_fields(self) -> dict:
        """The legacy `FIELDS` table of the actions that have controls: `{action: {field: bool | (min, max)}}`."""
        return {item.id: {f.path: f.spec() for f in item.fields} for item in self if item.fields}

    def documents(self) -> frozenset:
        """The names of every whole-object control an action owns."""
        return frozenset(name for item in self for name in item.documents)


_GEM_HOSTS = frozenset({'orchestrator', 'collector', 'shard', 'quest_sm', 'quest_ilm', 'harness'})
_ENABLED = Field('enabled', 'bool', label='Enabled')
_INTERVAL = Field('interval_sec', 'number', 1, 3600, 's', 'Interval')
_WINDOW = Field('window_sec', 'number', 5, 60, 's', 'Window')

AD_GEMS = ActionDefinition(
    id='ad_gems', label='Ad gems', family='gems', hosts=_GEM_HOSTS, schedule=StartToStart(),
    fields=(_ENABLED, _INTERVAL, _WINDOW),
    capabilities=Capabilities(live_controls=True, pause=True, run_now=True), saved='gather_policy',
    evidence=('balance_increased',))     # Home: stable exact +6. A battle offer that vanished after the tap is a sighting, not proof of a gem (see effect_of)
FLOATING_GEMS = ActionDefinition(
    id='floating_gems', label='Floating gems', family='gems', hosts=_GEM_HOSTS, schedule=AppearanceDriven(),
    fields=(_ENABLED,
            Field('interval_sec', 'number', 1, 3600, 's', 'Retry spacing after unconfirmed taps'),
            Field('window_sec', 'number', 5, 60, 's', 'Maximum floating gem claim attempt'),
            Field('tap_interval_sec', 'number', 0.5, 30, 's', 'Time between tap sets while seeking')),
    capabilities=Capabilities(live_controls=True, pause=True, run_now=True), saved='gather_policy',
    evidence=('balance_increased',))
SHOPPING = ActionDefinition(
    id='shopping', label='Shopping', family='rewards', hosts=frozenset({'orchestrator'}), schedule=AfterCompletion(),
    fields=(_ENABLED, Field('interval_sec', 'number', 1, 86400, 's', 'Interval', default=90)),
    capabilities=Capabilities(live_controls=True, pause=True, run_now=True, variants=True), saved='shopping_plan',
    evidence=('confirmed_purchase',), documents=('shopping_plan',), saved_kinds=frozenset({'coin', 'tournament'}))
HARVEST_LAUNCH = ActionDefinition(
    id='harvest_launch', label='Harvest collector launch', family='harvest', hosts=frozenset({'dashboard'}),
    schedule=Backoff(), capabilities=Capabilities(run_now='not_supported'))     # no evidence: a launch starts a process; only the collector's own reports show it ran



def _plain(ident, label, family, schedule, *, consumes=None, evidence=(), scope=None):
    """A timed action of the orchestrator host that has no live control, no Run now and no saved setting (item 7: its schedule and its history only)."""
    return ActionDefinition(id=ident, label=label, family=family, hosts=frozenset({'orchestrator'}), schedule=schedule, capabilities=Capabilities(),
                            evidence=evidence, consumes=consumes, scope=scope)


# ---- item 7, A3a: rewards (a side-menu visit during a run) and chores (between runs, from Home). Each schedule is declared once and shared.
REWARD_CHECK = Cooldown(300)                                       # the badge of a global reward behaviour is looked at every five minutes
VISIT_SCHEDULE = Cooldown(recent_window=1200, recent_delay=(1500, 2400))   # a seen badge is visited after a sampled delay; a repeat soon after a visit waits longer
STORE_SCHEDULE = DailyCalendar('utc', floor_hour=1, spread_sec=600, retry_sec=300, close_on='confirmed')   # 01:00 UTC +/- 10 min, a failed claim retries after 300 s
CHORE_SCHEDULE = DailyCalendar('local', floor_hour=3, close_on='attempted')                              # nothing runs before 03:00 local; a failed chore still uses the day

# A badge is not a game effect: the daily-missions and event checks have no evidence (their flows are recorded completed, effect unknown); the guild check proves one by its claims.
DAILY_MISSIONS = _plain('daily_missions', 'Daily missions check', 'rewards', REWARD_CHECK, scope='account')
GUILD_PROGRESS = _plain('guild_progress', 'Guild progress check', 'rewards', REWARD_CHECK, evidence=('milestones_claimed',), scope='account')
EVENT_MISSIONS = _plain('event_missions', 'Event missions check', 'rewards', REWARD_CHECK, scope='account')
QUEST_VISIT = _plain('quest_visit', 'Quest board visit', 'rewards', VISIT_SCHEDULE)
GUILD_VISIT = _plain('guild_visit', 'Guild visit', 'rewards', VISIT_SCHEDULE, evidence=('milestones_claimed',))
GUILD_STORE = _plain('guild_store', 'Guild store follow-up', 'rewards', EventOnly('guild_claim'))
FREE_STORE_GEMS = _plain('free_store_gems', 'Daily free gems', 'rewards', STORE_SCHEDULE, consumes='confirmed', evidence=('cooldown_shown',), scope='account')
# The chores' day flag is one key per chore in the installation-wide daily state (scheduling/daystate.py): every emulator's runner reads and closes the same day.
QUEST_SCAN = _plain('quest_scan', 'Quest scan', 'chores', CHORE_SCHEDULE, consumes='attempted', scope='installation')
SHATTER = _plain('shatter', 'Shatter', 'chores', CHORE_SCHEDULE, consumes='attempted', scope='installation')
HOUSEKEEPING = _plain('housekeeping', 'Disk housekeeping', 'chores', CHORE_SCHEDULE, consumes='attempted', scope='installation')


# ---- item 7, A3b kind A (r18d): DECLARATIONS ONLY. Nothing below moves a timer, owns a state, publishes a control or a routine, or starts anything: no driver
# records these yet (`hosts` names who may, when a later package migrates the caller), none has a field or a live control, and none is offered Run now. They exist so
# that the restricted and retired actions are NAMED where Run now is decided, and so that the event-only boundaries the code already has (the death handler, the wave
# target) and the two non-calendar daily-state consumers have an entry the later packages extend instead of inventing.
def _declared(ident, label, family, event, *, hosts=(), restricted=None, scope=None):
    return ActionDefinition(id=ident, label=label, family=family, hosts=frozenset(hosts), schedule=EventOnly(event), capabilities=Capabilities(),
                            restricted=restricted, scope=scope)


# Row 20, restricted: never user-triggered, whatever a binding says (`run_now_tokens`), and not constructible with Run now (`ActionDefinition.__post_init__`).
TOURNAMENT_ENTRY = _declared('tournament_entry', 'Tournament entry', 'dayplan', 'tournament_block',
                             restricted='a ticket purchase starts the run and the gem cost escalates 10, 20, 30: entered only by tourney.setup() in a tournament run, either the day plan tournament block (once a day under the combo day lock) or a person starting a tournament preset (the lock is not checked there); never by Run now')
TOURNAMENT_TICKET = _declared('tournament_ticket', 'Tournament ticket', 'dayplan', 'ticket_dialog',
                              restricted='the free ad first, gems only up to an explicit cap: it is part of the entry, never an action of its own')
IN_RUN_CARDS = _declared('in_run_cards', 'In-run card actions', 'rules', 'wave_rule',
                         restricted='retired: refused at runtime, there is no verified route from a live battle to the cards screen and back (orchestrator.run_in_run_actions)')
SURRENDER = _declared('surrender', 'Surrender', 'rules', 'guarded_exit',
                      restricted='ends a live run: only the guarded flows (shard.abandon_run, the rule surrender) may, one attempt, and never on a tournament run')
SETUP_CAPTURE = _declared('setup_capture', 'Setup capture run', 'setup', 'setup_scan',
                          restricted='setup starts its own run to capture the intro-sprint, exit and stats screens and surrenders that same run: human-started, consented, clean Home only')
MAX_WAVE = _declared('max_wave', 'Max wave', 'cycles', 'wave_target', hosts=('orchestrator',),
                     restricted='ends the run at the configured wave through the guarded surrender: one attempt per run, never on a tournament run')
# Rows 16 and 17, event only, no Run now. The `repeat` ROUTINE (its enable flag, a literal gate) is not this entry and stays unprojected until it migrates (ACTIONS.md).
REPEAT_AFTER_DEATH = _declared('repeat_after_death', 'Repeat after death', 'cycles', 'death_boundary', hosts=('orchestrator',))
SM_RIDE = _declared('sm_ride', 'Smart Missiles ride', 'cycles', 'wave_target', hosts=('quest_sm',))
# Rows 21 and 22, NOT timers: consumers of the daily-state file through its serialized public functions (scheduling/daystate.py), never calendar flags of their own.
TOURNAMENT_DAY_MARKER = _declared('tournament_day_marker', 'Tournament day marker (by hand)', 'dayplan', 'manual_tournament', scope='installation')
PERK_BAN_CACHE = _declared('perk_ban_cache', 'Perk ban cache', 'setup', 'perk_bans_applied', scope='account')

# ---- item 7, A3b kind B (r18e): main-loop upkeep with a refire floor or a retry ladder. Each migrated caller keeps the state it already held (`rs.rule_next`,
# `rs.dm_always_try_at`, `rs.uw_next_try`/`rs.uw_fails`) and asks this definition whether it is due and when it may try again. History: one terminal row per CONTACT
# (`upkeep_outcome`), written by the host after the input returned and after its own state was updated; a suppression, a retired rule or a pass without contact records none.
COMPILED_RULE = _plain('compiled_rule', 'Compiled rule', 'rules', Cooldown(), evidence=('rule_confirmed',))       # the floor is the rule's own (`refire_sec` ...); instance = compiled_rule@<rule id>
DEMON_MODE_ALWAYS = _plain('demon_mode_always', 'Demon Mode on cooldown', 'rules', Cooldown(15.0), evidence=('fire_confirmed',))
UW_TOGGLE = _plain('uw_toggle', 'Ultimate Weapon toggle', 'rules', Backoff(10, 2, 120), evidence=('toggle_verified',))   # ONE state for the three call sites of main()


def upkeep_outcome(action_id: str, ok: bool) -> dict:
    """The terminal row of one contact of an upkeep action (`ActionSession.record` fields). Confirmed is `succeeded` with the definition's evidence; anything else is
    `failed` with the effect UNKNOWN (the input may have reached the game), never `none`."""
    if ok:
        evidence = catalog().get(action_id).evidence[:1]
        return dict(result='succeeded', reason=evidence[0], effect='confirmed', attempts=1, confirmed=1, evidence=evidence)
    return dict(result='failed', reason='not_confirmed', effect='unknown', attempts=1, confirmed=0, evidence=())


CATALOG = Catalog((AD_GEMS, FLOATING_GEMS, SHOPPING, HARVEST_LAUNCH, DAILY_MISSIONS, GUILD_PROGRESS, EVENT_MISSIONS, QUEST_VISIT, GUILD_VISIT, GUILD_STORE,
                   FREE_STORE_GEMS, QUEST_SCAN, SHATTER, HOUSEKEEPING,
                   TOURNAMENT_ENTRY, TOURNAMENT_TICKET, IN_RUN_CARDS, SURRENDER, SETUP_CAPTURE, MAX_WAVE, REPEAT_AFTER_DEATH, SM_RIDE, TOURNAMENT_DAY_MARKER, PERK_BAN_CACHE,
                   COMPILED_RULE, DEMON_MODE_ALWAYS, UW_TOGGLE))
_ACTIVE: list[Catalog] = []


def effect_of(action_id: str, reason: str | None) -> str:
    """The recorded effect of a window or command that reported a confirmed outcome for `reason`: 'confirmed' only when the
    definition lists that reason as evidence of a game effect, else 'unconfirmed' (the claim went out and something was seen, but
    nothing proves a gem arrived). The one place that decides it; every writer of such a row asks here."""
    return 'confirmed' if reason in catalog().get(action_id).evidence else 'unconfirmed'


def catalog() -> Catalog:
    """The catalog every generated seam (validators, capability lists, projections, saved controls) reads. Production never
    changes it; a test that proves an extension swaps in a COPY with `use_catalog`, so the pilot catalog is never touched."""
    return _ACTIVE[-1] if _ACTIVE else CATALOG


@contextlib.contextmanager
def use_catalog(candidate: Catalog):
    _ACTIVE.append(candidate)
    try:
        yield candidate
    finally:
        _ACTIVE.pop()

DEFAULT_SHOPPING_INTERVAL_SEC = SHOPPING.field('interval_sec').default
SPRINT_SHOPPING_INTERVAL_SEC = 20     # an untracked run shops this often during the intro sprint


def shopping_interval(preset: Mapping, *, tracked: bool = True, sprint: bool = False) -> float:
    """Seconds between shopping sweeps: the one definition. A tracked run follows its shopping plan, else the preset's
    `shop_interval_sec`, else the default; a run with no battle record ignores the plan and shops every 20 s during
    the intro sprint."""
    if not tracked:
        return SPRINT_SHOPPING_INTERVAL_SEC if sprint else preset.get('shop_interval_sec', DEFAULT_SHOPPING_INTERVAL_SEC)
    return (preset.get('shopping_plan') or {}).get('interval_sec', preset.get('shop_interval_sec', DEFAULT_SHOPPING_INTERVAL_SEC))
