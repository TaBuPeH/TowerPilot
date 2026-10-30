"""Golden traces of the timed-action callers (roadmap item 6, step A2-0).

Scenario drivers run TODAY's ad-gem collector, orbit tapper, shopper and harvest reconciler under a fake clock
and a seeded random source, and record every observable decision, including the ORDER OF RANDOM DRAWS. The
traces in fixtures/action_golden_v1.json were captured on the unchanged tree, before the contract modules
existed. The parity tests replay the same scenarios and compare, so a refactor that changes cadence, a guard
or a draw order fails here.

This module imports none of towerpilot_workflows.action_contract / actions or runtime.action_session. It
drives the callers only through the attributes they already had (poll, configure, request_now, status, due,
tick), so the scenarios stay valid after the migration. Regeneration is an explicit developer command, never
a test side effect:

    TP_REGENERATE_ACTION_GOLDEN=1 python -m pytest backend/tests/unit/test_action_golden_capture.py
"""

import paths as test_paths
from fixture_json import write_fixture_json
import contextlib
import hashlib
import itertools
import json
import math
import random
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

WALL0 = 1_700_000_000.0          # wall clock at monotonic 0: fixed, so epoch values are deterministic
# Since r14c the orbit tapper sends no input until a gem is RECOGNIZED on the frame (vision.orbit_gems.find_orbit_gem), and the fake world's
# frame is a balance, not an image. The world therefore shows one fixed visible gem, and a fixed reference geometry so no coordinate
# depends on the shipped bootstrap manifest (the values below are its battle.orbiting_gem reference at the time of capture: center (510, 805),
# radius 170, so the fixed gem lies exactly on the configured circle). Without a visible gem the orbit scenarios record only `gem_not_detected`
# backoffs and pin none of the tap path (a capture made without it did exactly that: 9 refusals, no hit).
ORBIT_GEM = SimpleNamespace(center=(680.0, 805.0), rect=(640, 765, 80, 80), score=0.99)
ORBIT_REFERENCE = ((510, 805), 170.0)


def clean(value):
    """JSON-safe, rounded copy: floats to 6 places (a harmless reordering of arithmetic stays equal, a real
    change of a decision does not), non-finite numbers as text, unknown objects as their type name."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
                if key != 'traceback'}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [clean(item) for item in items]
    return type(value).__name__


class DrawLog:
    """The random source a collector is given: seeded, and it records every draw in order."""

    def __init__(self, seed, world):
        self._random, self._world = random.Random(seed), world

    def uniform(self, low, high):
        value = self._random.uniform(low, high)
        self._world.draws.append(['uniform', low, high, value])
        return value

    def randint(self, low, high):
        value = self._random.randint(low, high)
        self._world.draws.append(['randint', low, high, value])
        return value


class Frame(int):
    """What capture.grab returns: the gem balance (an int, as the orbit tapper reads it) that also carries the
    screen state the ad-gem recognizers are faked from. One object serves both collectors because they patch
    the same capture and recognizer modules."""

    def __new__(cls, balance, battle, offer, readable):
        frame = super().__new__(cls, balance)
        frame.battle, frame.offer, frame.readable = battle, offer, readable
        return frame


class World:
    """One fake device and clock. `frame()` is what capture.grab returns right now."""

    def __init__(self, seed=0):
        self.now = 100.0
        self.battle, self.offer, self.claim_confirms = True, False, True
        self.balance, self.balance_readable, self.hit_next_tap = 200, True, False
        self.gem_visible = True      # since r14g the tapper opens an attempt only for a gem recognized on the frame
        self.wave_readable, self.screen_battle = True, True      # the wave counter reads, and the screen is the battle: separate facts in the game, one in the shared scenarios
        self.taps, self.events, self.draws = [], [], []
        self.rng = DrawLog(seed, self)

    def event(self, name, **fields):
        self.events.append([name, clean(fields)])

    def frame(self):
        return Frame(self.balance if self.balance_readable else 0, self.battle, self.offer, self.balance_readable)

    def drain(self):
        events, draws, taps = self.events, self.draws, len(self.taps)
        self.events, self.draws = [], []
        return events, draws, taps


@contextlib.contextmanager
def gem_world(world, *, orbit_seed=5):
    """Fake clock, fake device and deterministic ids around the two gem collectors."""
    from flows import shard
    from interactions import ad_gems
    from runtime import logger
    ids = itertools.count(1)
    rect = (0, 1250, 200, 100)

    def tap(*args, **kwargs):
        world.taps.append([clean(arg) for arg in args])
        if world.claim_confirms and kwargs.get('reason') == 'HUD Ad Gems claim':   # only the ad claim removes the offer
            world.offer = False
        if world.hit_next_tap and isinstance(world.balance, int):
            world.balance += 2
            world.hit_next_tap = False
        return {}

    def identify(frame):
        return SimpleNamespace(name='battle' if frame.battle and world.screen_battle else 'menu')
    from device import act, capture
    from vision import screen, wave_reader
    patches = [
        mock.patch('time.monotonic', lambda: world.now),
        mock.patch('time.time', lambda: WALL0 + world.now),
        mock.patch('time.sleep', lambda seconds: None),
        mock.patch('uuid.uuid4', lambda: uuid.UUID(int=next(ids))),
        mock.patch.object(logger, 'event', world.event),
        mock.patch.object(wave_reader, 'read_wave', lambda frame: 100 if frame.battle and world.wave_readable else None),
        mock.patch.object(screen, 'identify', identify),
        mock.patch.object(ad_gems, 'find_claim_rect', lambda frame, **kwargs: rect if frame.offer else None),
        mock.patch.object(capture, 'grab', world.frame),
        mock.patch.object(capture, 'layout_offset', 0),
        mock.patch.object(ad_gems.overlays, 'windows', lambda serial, **kwargs: [ad_gems.overlays.GAME_PKG + '/Activity']),
        mock.patch.object(act, 'tap', tap),
        mock.patch.object(shard, 'read_gem_balance', lambda frame: int(frame) if frame.readable else None),
        mock.patch.object(shard, 'find_orbit_gem', lambda frame, **kwargs: ORBIT_GEM if world.gem_visible else None),
        mock.patch.object(shard, 'reference_geometry', lambda: ORBIT_REFERENCE),
        mock.patch.object(shard, 'battlefield_live', lambda *args, **kwargs: (True, 'live')),
        # since r14f the tapper reads the motion probe's own verified frame (battlefield_observation); the boolean helper above is kept for other callers
        mock.patch.object(shard, 'battlefield_observation', lambda points, frame=None, **kwargs: (True, 'live', frame)),
        mock.patch.object(shard, '_in_ability_row', lambda point: False),
        mock.patch.object(shard, 'random', random.Random(orbit_seed)),
    ]
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        yield world


# ------------------------------------------------------------------ ad gems
AD_STATE = ('enabled', 'next_at', 'next_window_at', 'window_deadline', 'window_started_at', 'last_window_started_at',
            'manual_requested', 'manual_active', 'visibility', 'visibility_reason', 'blocker', 'claim_at',
            'next_observation_at', 'window_id', 'claim_attempt_id', 'offer_id')
ORBIT_STATE = ('enabled', 'next_at', 'next_window_at', 'window_ends_at', 'attempt_started', 'manual_requested',
               'manual_active', 'interval', 'attempt_timeout', 'retry_interval', 'retry_backoff', 'sent_tap',
               'balance_before', 'window_id', 'attempt_id',
               # r14g: the retry spacing is an anchor (the start of the last attempt that sent input and was not confirmed), a safety floor
               # an expired attempt leaves, and the input pacing; none of them was visible in the trace before
               '_failed_retry_anchor', '_timeout_ready_at', '_attempt_had_input', '_input_ready_at')


def ad_state(collector):
    state = {name: getattr(collector, name, None) for name in AD_STATE}
    state['counters'] = dict(collector.counters)
    state['last_outcome'] = None if collector.last_outcome is None else {
        key: collector.last_outcome.get(key) for key in ('outcome', 'reason', 'confirmed')}
    state['options'] = dict(cooldown=collector.options['confirmed_cooldown_sec'], window=collector.options['attempt_timeout_sec'],
                            retry=collector.options['retry_cooldown_sec'], poll=collector.options['poll_sec'])
    return clean(state)


def orbit_state(tapper):
    state = {name: getattr(tapper, name, None) for name in ORBIT_STATE}
    state['last_outcome'] = None if tapper.last_outcome is None else {
        key: tapper.last_outcome.get(key) for key in ('reason', 'confirmed')}
    state['last_refusal'] = None if tapper.last_refusal is None else tapper.last_refusal.get('reason')
    return clean(state)


def apply_op(world, collector, op, state_of):
    """One scripted step. Returns the step record (operation, time, resulting state, events, draws, taps), or
    None for a pure time advance. A refusal raised by the collector is part of the trace."""
    kind = op[0]
    answer = None
    try:
        answer = _perform(world, collector, op)
    except Exception as exc:                      # noqa: BLE001 - a raised refusal is a recorded decision
        answer = ['raised', type(exc).__name__]
    if kind == 'advance':
        world.drain()
        return None
    events, draws, taps = world.drain()
    return dict(op=clean(list(op)), t=world.now, answer=answer, state=state_of(collector), events=events,
                draws=clean(draws), taps=taps)


def _perform(world, collector, op):
    kind = op[0]
    answer = None
    if kind == 'advance':
        world.now += op[1]
    elif kind == 'poll':
        world.battle, world.offer = op[1], op[2]
        sent = world.frame()
        answer = None if collector.poll(sent) is sent else 'newer_frame'   # callers must continue from the returned frame
    elif kind == 'manual':
        answer = collector.request_now()
    elif kind == 'configure':
        collector.configure(dict(op[1]))
    elif kind == 'status':
        status = collector.status()
        answer = clean({key: status.get(key) for key in (
            'enabled', 'state', 'next_due_at', 'window_ends_at', 'manual_pending', 'manual_active', 'blocker')})
    elif kind == 'claim_confirms':
        world.claim_confirms = op[1]
    elif kind == 'hit':
        world.hit_next_tap = True
    elif kind == 'gem':
        world.gem_visible = op[1]
    elif kind == 'wave':
        world.wave_readable = op[1]
    elif kind == 'screen':
        world.screen_battle = op[1]
    elif kind == 'balance':
        world.balance, world.balance_readable = op[1], op[1] is not None
    else:
        raise ValueError(kind)
    return answer


def run_script(make, script, state_of, *, seed=1):
    world = World(seed)
    with gem_world(world):
        collector = make(world)
        steps = [apply_op(world, collector, op, state_of) for op in script]
    return [step for step in steps if step is not None]


def compact(steps):
    """Readable where a decision happened (an event, an answer, a command), a digest of the full state beside
    the random draws where nothing else did. Equal compact traces mean equal full traces."""
    out = []
    for step in steps:
        if step['events'] or step['answer'] is not None or step['op'][0] != 'poll':
            out.append(step)
        else:
            out.append(dict(op=step['op'], t=step['t'], taps=step['taps'], draws=step['draws'],
                            state_digest=digest(step['state'])))
    return out


def make_ad(world, **kwargs):
    from interactions import ad_gems
    options = dict(enabled=True, delay=(0.5, 2.0), rng=world.rng)
    options.update(kwargs)
    return ad_gems.AdGemCollector(**options)


def make_orbit(world, **kwargs):
    from flows import shard
    options = dict(enabled=True, jitter=0)
    options.update(kwargs)
    return shard.GemOrbitTapper(**options)


def polls(times, battle=True, offer=False, step=None):
    """Poll once at each offset from now: ('advance', gap) then ('poll', ...)."""
    script, previous = [], 0.0
    for at in times:
        script += [('advance', at - previous), ('poll', battle, offer)]
        previous = at
    return script


def scenarios(maker, state_of):
    """name -> (script, kwargs): the curated cadence cases, shared by both collectors."""
    every = lambda stop, gap, **kw: polls([gap * i for i in range(1, int(stop / gap) + 1)], **kw)
    return {
        'first_window_at_once_then_idle_polls': (every(1500, 5), {}),
        'offer_claimed_inside_the_window': ([('claim_confirms', True), ('hit',)] + every(200, 1, offer=True) + every(900, 20), {}),
        'offer_that_never_vanishes_runs_the_window_out': ([('claim_confirms', False)] + every(260, 2, offer=True) + every(1200, 30), {}),
        'long_gap_skips_whole_windows_without_a_burst': (polls([1, 2, 30, 31, 5000, 5001, 5002, 5200, 9000, 9001]), {}),
        'not_a_battle_screen_never_acts': (every(300, 5, battle=False) + every(300, 5), {}),
        'manual_start_reanchors_and_is_refused_while_a_window_runs': (
            polls([1, 2]) + [('manual',), ('advance', 3), ('poll', True, False), ('manual',), ('status',)]
            + polls([100, 400, 1500]) + [('manual',), ('advance', 1), ('poll', True, False), ('status',)], {}),
        'manual_start_runs_while_disabled': (
            [('configure', {'enabled': False}), ('manual',)] + polls([1, 2, 400]) + [('status',)], {}),
        'configure_reanchors_and_shortens_a_live_window': (
            polls([1, 2]) + [('configure', {'interval_sec': 120})] + polls([10, 11])
            + [('configure', {'window_sec': 5})] + polls([20, 21, 22]) + [('configure', {'interval_sec': 30}), ('status',)]
            + polls([60, 90, 120, 600]), {}),
        'pause_mid_window_keeps_the_window_until_its_deadline': (
            polls([1, 2]) + [('configure', {'enabled': False})] + polls([3, 4, 30, 60, 90]) + [('status',)], {}),
        'reenable_after_a_long_pause_awards_no_missed_window': (
            polls([1]) + [('configure', {'enabled': False})] + polls([5, 4000]) + [('configure', {'enabled': True})]
            + polls([4001, 4002, 4010, 4500, 9000]), {}),
        'disabled_collector_observes_nothing': ([('configure', {'enabled': False})] + every(200, 5), {}),
    }

def orbit_scenarios():
    """name -> (script, kwargs): the cases of the appearance-driven tapper (r14g) that the shared cases cannot reach, because the shared
    world always shows the gem. `('gem', False)` takes it off the screen, `('hit',)` makes the next tap raise the balance by 2 (a confirmed
    claim), `('balance', None)` makes the counter unreadable (an attempt that can send no input)."""
    every = lambda stop, gap, **kw: polls([gap * i for i in range(1, int(stop / gap) + 1)], **kw)
    return {
        'no_gem_on_screen_opens_no_attempt_and_a_gem_that_appears_opens_one_at_once': (
            [('gem', False)] + every(300, 5) + [('status',), ('gem', True), ('advance', 1), ('poll', True, False), ('status',)] + every(120, 5), {}),
        'unconfirmed_taps_space_the_next_attempt_by_the_interval_from_its_start': (
            every(130, 5) + polls([300, 600, 900, 1000, 1020, 1040, 1100]) + [('status',)], {}),
        'an_attempt_that_sent_no_input_retries_after_the_safety_backoff_only': (
            [('balance', None)] + every(75, 5) + [('balance', 200)] + every(90, 5), {}),
        'a_confirmed_claim_resumes_watching_at_once_without_a_timed_window': (
            [('hit',)] + every(40, 5) + every(120, 5), {}),
        'a_manual_request_opens_an_attempt_with_no_gem_on_screen': (
            [('gem', False), ('manual',)] + polls([1, 2, 3]) + [('status',)] + every(150, 10), {}),
        'a_changed_interval_respaces_an_unconfirmed_attempt_and_leaves_a_confirmed_claim_alone': (
            every(130, 5) + [('configure', {'interval_sec': 120})] + polls([140, 200, 260]) + [('status',)]
            + [('hit',)] + every(40, 5) + [('configure', {'interval_sec': 600})] + every(60, 5) + [('status',)], {}),
        'a_shorter_window_shortens_a_live_attempt_and_a_longer_one_does_not_extend_it': (
            polls([1, 2]) + [('configure', {'window_sec': 5})] + polls([3, 4, 5, 6, 7, 8, 9]) + [('configure', {'window_sec': 60})]
            + every(100, 5), {}),
        'a_gem_over_an_unreadable_wave_counter_opens_no_attempt': (
            [('wave', False)] + every(120, 5) + [('status',), ('wave', True), ('advance', 1), ('poll', True, False), ('status',)] + every(30, 5), {}),
        'a_gem_over_a_screen_that_is_not_the_battle_opens_no_attempt': (
            [('screen', False)] + every(120, 5) + [('status',), ('screen', True), ('advance', 1), ('poll', True, False), ('status',)] + every(30, 5), {}),
        'a_poll_outside_a_battle_during_an_attempt_backs_off_and_the_attempt_runs_out': (
            every(20, 5) + [('poll', False, False), ('advance', 70), ('poll', False, False), ('poll', True, False)] + every(60, 5), {}),
    }


# --------------------------------------------------------------------- fuzz
def fuzz_script(seed, length=220, appearance=False):
    """A seeded random sequence of everything a host can do to a collector. `appearance` adds the gem leaving and returning
    (used for the tapper only; it draws extra numbers, so the sequence differs from the shared one of the same seed)."""
    rnd, script = random.Random(seed), []
    gaps, weights = [0.2, 0.5, 1.5, 3, 8, 20, 70, 600, 4000], [8, 10, 10, 10, 8, 5, 3, 2, 1]
    for _ in range(length):
        script.append(('advance', rnd.choices(gaps, weights)[0]))
        roll = rnd.random()
        if roll < 0.62:
            script.append(('poll', rnd.random() < 0.92, rnd.random() < 0.5))
        elif roll < 0.70:
            script.append(('manual',))
        elif roll < 0.76:
            script.append(('configure', {'enabled': rnd.random() < 0.6}))
        elif roll < 0.82:
            script.append(('configure', {'interval_sec': rnd.choice([30, 60, 120, 300, 900])}))
        elif roll < 0.88:
            script.append(('configure', {'window_sec': rnd.choice([5, 15, 30, 60])}))
        elif roll < 0.92:
            script.append(('claim_confirms', rnd.random() < 0.7))
        elif roll < 0.95:
            script.append(('hit',))
        elif roll < 0.97:
            script.append(('balance', rnd.choice([None, 200, 202, 204])))
        else:
            script.append(('status',))
        if appearance and rnd.random() < 0.10:
            script.append((rnd.choice(['gem', 'gem', 'wave', 'screen']), rnd.random() < 0.7))
    return script


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]


def summarize(steps):
    """A fuzz trace is kept as one digest per step plus the final state: a regression net, while the curated
    scenarios hold the readable traces."""
    return dict(steps=len(steps), digests=[digest(step) for step in steps], final=steps[-1]['state'] if steps else None)


GEM_FAMILIES = {'ad_gems': (make_ad, ad_state), 'floating_gems': (make_orbit, orbit_state)}
FUZZ_SEEDS = (11, 22, 33, 44)
APPEARANCE_FUZZ_SEEDS = (51, 52, 53, 54)      # the tapper only: sequences in which the gem leaves and returns


def gem_traces(family):
    """{'curated': {name: steps}, 'fuzz': {seed: summary}} for one gem collector."""
    make, state_of = GEM_FAMILIES[family]
    cases = dict(scenarios(make, state_of))
    if family == 'floating_gems':
        cases.update(orbit_scenarios())
    curated = {name: compact(run_script(make, script, state_of, seed=7)) for name, (script, _) in cases.items()}
    fuzz = {str(seed): summarize(run_script(make, fuzz_script(seed), state_of, seed=seed)) for seed in FUZZ_SEEDS}
    traces = dict(curated=curated, fuzz=fuzz)
    if family == 'floating_gems':
        traces['fuzz_appearance'] = {str(seed): summarize(run_script(make, fuzz_script(seed, appearance=True), state_of, seed=seed))
                                     for seed in APPEARANCE_FUZZ_SEEDS}
    return traces


# ----------------------------------------------------------------- shopping
@contextlib.contextmanager
def shopping_world(clock):
    """The market of tests/test_incremental_shopping.py (kept in step by hand: that file's fixture cannot be
    imported). clock is a one-item list; the returned namespace records the taps."""
    from interactions import shopper
    state = SimpleNamespace(cash=1000, prices={'health': 10, 'damage': 10}, tab='defense', taps=[], clock=clock)

    def observe(frame, stat):
        return dict(center=(stat, frame), state='buy', price=state.prices[stat], cash=state.cash, quantity=1,
                    total_price_verified=False, observed_at=clock[0])

    def tap(x, y, *, reason):
        if reason.startswith('tab_'):
            state.tab = reason.removeprefix('tab_')
            return {}
        state.taps.append((y, x, state.cash, state.prices[x]))
        state.cash -= state.prices[x]
        state.prices[x] += 1
        return {}
    ids = itertools.count(1)
    with contextlib.ExitStack() as stack:
        for patch in (mock.patch('time.monotonic', lambda: clock[0]), mock.patch('time.time', lambda: WALL0 + clock[0]),
                      mock.patch('uuid.uuid4', lambda: uuid.UUID(int=next(ids))),
                      mock.patch.object(shopper, '_TOP_RESETS', 0), mock.patch.object(shopper, '_swipe', lambda **kw: None),
                      mock.patch.object(shopper.detect, 'panel_tab', lambda frame: state.tab),
                      mock.patch.dict(shopper.CONFIG, {'tabs': {'defense': (1, 1), 'attack': (2, 2)}}),
                      mock.patch.object(shopper, '_purchase_quote', observe), mock.patch.object(shopper.act, 'tap', tap)):
            stack.enter_context(patch)
        yield state


def shopping_plan(**changes):
    from player.shopping_plan import normalize_plan
    return normalize_plan(dict(version=2, cadence='while_affordable', interval_sec=90, cash_reserve=50,
                               queue_mode='highest_affordable', queue=[{'id': 'health'}], **changes))


def due_table():
    """Shopper.due over the interval boundary, with and without a confirmed continuation: `>` not `>=`."""
    from interactions import shopper
    rows = []
    for interval in (20, 90):
        for delta in (-1, -0.001, 0, 0.001, 1, interval - 0.001, interval, interval + 0.001, interval + 1):
            for continuation in (False, True):
                instance = shopper.Shopper({'shopping_plan': shopping_plan()})
                instance._continuation_ready = continuation
                rows.append([interval, delta, continuation, instance.due(1000 + delta, 1000, interval)])
    return rows


def shopping_trace(cash, passes, *, interval=90, jump_after=None, jump=0.0):
    """advance_shopping driven the way the orchestrator drives it: one call per monitoring pass."""
    from interactions import shopper
    from orchestrator import advance_shopping
    clock, trace = [100.0], []
    with shopping_world(clock) as market, mock.patch.object(shopper.logger, 'event', lambda *a, **k: None):
        market.cash = cash
        instance = shopper.Shopper({'shopping_plan': shopping_plan()})
        rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False, mission=SimpleNamespace(active=False))
        for index in range(passes):
            clock[0] += 0.1
            if jump_after is not None and index == jump_after:
                clock[0] += jump
            was_active = instance.active
            advance_shopping(rs, index + 1, 20, False, clock[0], interval)
            status = instance.status()
            sweep = status['last_sweep'] or {}
            trace.append(clean([round(clock[0], 3), was_active, instance.active, rs.last_shop, status['sweep_id'],
                                sweep.get('outcome'), sweep.get('confirmed_purchases'), len(market.taps),
                                status['continuation_ready']]))
    return trace


def shopping_traces():
    return dict(due_table=due_table(),
                confirmed_windows_then_interval=shopping_trace(1000, 120, jump_after=80, jump=95.0),
                terminal_noop_backs_off=shopping_trace(50, 120, jump_after=80, jump=95.0),
                short_interval=shopping_trace(1000, 80, interval=20, jump_after=40, jump=21.0))


# ------------------------------------------------------------------ harvest
def _offset(value, base):
    return None if value is None else round(value - base, 6)


def _record(label, result, h, base):
    outcome = result.get('last_outcome') or {}
    return clean([label, result['action'], result['state'], result['code'], result['failures'], result['circuit_open'],
                  _offset(result['next_attempt_at'], base), result['attempts'], outcome.get('kind'), outcome.get('code'),
                  len(h.spawned)])


def harvest_traces(h):
    """The reconciler's launch decisions: spacing, back-off, refusal recheck and the circuit, from the facts."""
    from control import harvest
    from harvest_support import NOW, active_ops, choose, finish_last, operations
    traces = {'backoff_table': [harvest.backoff(failures) for failures in range(0, 12)],
              'constants': dict(min_spacing=harvest.MIN_SPACING_SEC, fail_window=harvest.FAIL_WINDOW_SEC,
                                circuit=harvest.CIRCUIT, refusal_retry=harvest.REFUSAL_RETRY_SEC)}
    # failures back off 30/60/120/240 and the fifth opens the circuit until an explicit On
    choose(h)
    steps, now = [_record('launch', h.tick(NOW), h, NOW)], NOW
    for failures in range(1, harvest.CIRCUIT + 1):
        finish_last(h, 'failed', ran=5, now=now)
        result = h.tick(now + 1)
        steps.append(_record(f'after_failure_{failures}', result, h, NOW))
        if failures < harvest.CIRCUIT:
            now = result['next_attempt_at']
            steps.append(_record(f'relaunch_{failures}', h.tick(now), h, NOW))
    steps.append(_record('latched_a_day_later', h.tick(now + 86400), h, NOW))
    choose(h)
    steps.append(_record('explicit_on_resets', h.tick(now + 86400), h, NOW))
    traces['circuit'] = steps
    return traces


def harvest_spacing_traces(make_h):
    """Each case on a fresh environment: quick exit, long run, fence refusal, cancel, crash, refusals, offline."""
    from control import harvest
    from harvest_support import NOW, active_ops, choose, finish_last, operations
    out = {}
    h = make_h()
    choose(h)
    steps = [_record('launch', h.tick(NOW), h, NOW)]
    finish_last(h, 'succeeded', ran=3, now=NOW + 3)
    steps += [_record('quick_exit_waits', h.tick(NOW + 4), h, NOW), _record('at_min_spacing', h.tick(NOW + harvest.MIN_SPACING_SEC), h, NOW)]
    out['quick_clean_exit'] = steps
    h = make_h()
    choose(h)
    steps = [_record('launch', h.tick(NOW), h, NOW)]
    finish_last(h, 'failed', ran=harvest.FAIL_WINDOW_SEC + 1)
    steps.append(_record('long_run_is_not_a_failure', h.tick(NOW + 1), h, NOW))
    finish_last(h, 'failed', ran=1, now=NOW + 1, error={'code': 'CollectorRefused', 'message': 'no longer asked'})
    steps.append(_record('fence_refusal_is_not_a_failure', h.tick(NOW + harvest.MIN_SPACING_SEC), h, NOW))
    finish_last(h, 'cancelled', ran=30, now=NOW + harvest.MIN_SPACING_SEC + 30)
    steps.append(_record('cancel_is_not_a_failure', h.tick(NOW + harvest.MIN_SPACING_SEC + 31), h, NOW))
    out['long_run_fence_refusal_and_cancel'] = steps
    h = make_h()
    choose(h)
    steps = [_record('launch', h.tick(NOW), h, NOW)]
    op = active_ops(h)[0]
    operations.ledger(h.root).transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    h.alive['value'] = False
    steps.append(_record('crashed_worker_is_counted', h.tick(NOW + 2), h, NOW))
    out['crashed_worker'] = steps
    h = make_h()
    choose(h)
    h.refusal = ('digits_missing', 'The digit font is not captured.')
    steps = [_record('refused', h.tick(NOW), h, NOW), _record('inside_the_recheck', h.tick(NOW + 5), h, NOW),
             _record('recheck_refused_again', h.tick(NOW + harvest.REFUSAL_RETRY_SEC), h, NOW)]
    h.refusal = None
    h.adb['state'] = 'offline'
    steps.append(_record('emulator_offline', h.tick(NOW + 25), h, NOW))
    h.adb['state'] = 'device'
    steps.append(_record('launched_after_the_refusals', h.tick(NOW + 40), h, NOW))
    out['refusals_then_launch'] = steps
    return out

# ----------------------------------------------------------------- the store
GOLDEN_PATH = test_paths.FIXTURES / 'action_golden_v1.json'
REGENERATE = 'TP_REGENERATE_ACTION_GOLDEN'
REPO = test_paths.REPO
# The callers whose behaviour the traces pin. Their hashes at capture time prove the traces predate the migration.
SOURCES = ('backend/interactions/ad_gems.py', 'backend/flows/shard.py', 'backend/interactions/shopper.py',
           'backend/control/harvest.py', 'backend/orchestrator.py', 'backend/runtime/battle_session.py',
           'backend/towerpilot_workflows/harvest.py', 'backend/gem_collector.py')


def source_hashes():
    return {rel: hashlib.sha256((REPO / rel).read_bytes()).hexdigest().upper() for rel in SOURCES}


def load():
    return json.loads(GOLDEN_PATH.read_text(encoding='utf-8'))


def roundtrip(value):
    return json.loads(json.dumps(value))


def write(document):
    write_fixture_json(GOLDEN_PATH, document)

# ---------------------------------------------------------------- snapshots
def baseline_snapshots(tmp_path, dash):
    """Byte snapshots of what the Dashboard and the driver publish today: the capability lists, `runtime.routines`
    and the collection evidence of a battle (idle, then not visible), and the Flask route table. The migration
    must leave the first three field-for-field equal and add exactly the routes it declares."""
    from flows.shard import GemWatch
    from interactions.shopper import Shopper
    from runtime import logger
    from runtime.battle_session import BattleSession
    from towerpilot_workflows.battles import Battles
    from towerpilot_workflows.operations import Operations
    clock, ids = [100.0], itertools.count(1)
    shop_plan = shopping_plan()
    with (mock.patch('time.monotonic', lambda: clock[0]), mock.patch('time.time', lambda: WALL0 + clock[0]),
          mock.patch('uuid.uuid4', lambda: uuid.UUID(int=next(ids))), mock.patch.object(logger, 'event', lambda *a, **k: None)):
        path = tmp_path / 'baseline-operations.db'
        context = dict(device_key='device', account='account', instance='main', serial='fake')
        operation, _ = Operations(path).accept('farm', context, {})
        body = dict(gather={'ad_gems': True, 'gem_orbit': {'enabled': True}}, shopping=[], shop_interval_sec=90,
                    shopping_plan=shop_plan)
        session = BattleSession(Battles(path), operation['id'], body, {})
        rs = SimpleNamespace(gems=GemWatch(body['gather']), shop=Shopper(body), mission=SimpleNamespace(active=False),
                             shopping_enabled=True, rewards_enabled=True, repeat_enabled=True, tracker=SimpleNamespace(last=123),
                             last_shop=0)
        gems_only = SimpleNamespace(gems=rs.gems, shop=None)
        out = dict(capabilities_full=session.capabilities(rs), capabilities_gems_only=session.capabilities(gems_only))
        session.observe(rs, 123)
        keys = ('routines', 'server_time', 'collection_evidence', 'observed_wave', 'observation_status', 'driver_generation')
        session.publish(rs, battle_visible=True)
        out['runtime_idle'] = {key: session.store.get(session.battle['id'])['runtime'].get(key) for key in keys}
        clock[0] += 5
        session.publish(rs, battle_visible=False)
        out['runtime_not_visible'] = {key: session.store.get(session.battle['id'])['runtime'].get(key) for key in keys}
        out['routine_names'] = sorted(out['runtime_idle']['routines'])
    out['routes'] = sorted([rule.rule, sorted(rule.methods - {'HEAD', 'OPTIONS'}), rule.endpoint]
                           for rule in dash.app.url_map.iter_rules())
    return clean(out)