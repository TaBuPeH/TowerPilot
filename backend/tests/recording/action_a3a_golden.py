"""Golden traces of the A3a callers (roadmap item 7, rewards and chores: rows R-1 to R-5 and R-7).

Scenario drivers run TODAY's `scheduling.global_rewards`, `scheduling.chores` and the two orchestrator functions the reward-menu
block of main() was moved into unchanged (`advance_rewards`, `step_reward_mission`; tests/verify_extraction proves the move) under a
world clock, a seeded random source and an in-memory daily state, and record every observable decision: what is due, what was
started, the state the host keeps, the daily-state writes, the events and the ORDER OF RANDOM DRAWS. The traces in
fixtures/action_a3a_golden_v1.json were captured BEFORE the producers were replaced by schedule policies. The parity tests replay the
same scenarios and compare, so a migration that changes a cadence, a guard or a draw order fails.

This module imports none of the modules item 7 adds. It drives the callers through the names they already had where it can; the
module-level producers it calls by name (`check_due`, `checked`, `store_due`, `store_claimed`) are the ones the migration removes, so
`PRODUCERS` maps each to the new entry point (`decide` / `settle`) and the driver uses whichever exists. Regeneration is an explicit
developer command, never a test side effect:

    TP_REGENERATE_ACTION_A3A_GOLDEN=1 python -m pytest backend/tests/unit/test_action_a3a_capture.py
"""

import paths as test_paths
from fixture_json import write_fixture_json
import contextlib
import datetime as real_datetime
import hashlib
import json
import math
import random
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import action_golden as base
from action_golden import clean

GOLDEN_PATH = test_paths.FIXTURES / 'action_a3a_golden_v1.json'
REGENERATE = 'TP_REGENERATE_ACTION_A3A_GOLDEN'
REPO = test_paths.REPO
SOURCES = ('backend/scheduling/global_rewards.py', 'backend/scheduling/chores.py', 'backend/orchestrator.py', 'backend/scheduling/housekeeping.py',
           'backend/scheduling/daystate.py', 'backend/interactions/missions.py')
UTC = real_datetime.timezone.utc
# 2026-09-10 00:00:00 UTC. The world's monotonic zero is that instant, so the daily store (01:00 UTC +/- 10 min) opens about an hour into a scenario.
WALL0 = real_datetime.datetime(2026, 9, 10, tzinfo=UTC).timestamp()


def source_hashes():
    return {rel: hashlib.sha256((REPO / rel).read_bytes()).hexdigest().upper() for rel in SOURCES if (REPO / rel).exists()}


def load():
    return json.loads(GOLDEN_PATH.read_text(encoding='utf-8'))


def roundtrip(value):
    return json.loads(json.dumps(value))


def write(document):
    write_fixture_json(GOLDEN_PATH, document)


def digest(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True).encode()).hexdigest()[:16].upper()


class Draws:
    """The random source the host is given: seeded, and it records every draw in order."""

    def __init__(self, seed, log):
        self._random, self._log = random.Random(seed), log

    def uniform(self, low, high):
        value = self._random.uniform(low, high)
        self._log.append(['uniform', low, high, round(value, 9)])
        return value


class Clock:
    """One world: `t` is the monotonic clock (zero = WALL0), the wall clock is WALL0 + t. `local` overrides the local calendar for the chores."""

    def __init__(self, local=None):
        self.t, self.local = 0.0, local

    def wall(self):
        return WALL0 + self.t if self.local is None else self.local.timestamp()

    def local_now(self):
        return real_datetime.datetime.fromtimestamp(self.wall())


@contextlib.contextmanager
def frozen(clock):
    """time.time / time.monotonic / datetime.now() / date.today() all read the world clock; nothing sleeps."""
    class DT(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.datetime.fromtimestamp(clock.wall(), tz) if tz else real_datetime.datetime.fromtimestamp(clock.wall())

    class D(real_datetime.date):
        @classmethod
        def today(cls):
            return real_datetime.date.fromtimestamp(clock.wall())
    with mock.patch('time.time', clock.wall), mock.patch('time.monotonic', lambda: clock.t), mock.patch('time.sleep', lambda seconds: None), \
            mock.patch('datetime.datetime', DT), mock.patch('datetime.date', D):
        yield


class Store:
    """The daily state as a dict (the getters and setters the callers use)."""

    def __init__(self):
        self.data, self.writes = {}, []

    def get_raw(self, key, default=None):
        return self.data.get(key, default)

    def set_raw(self, key, value):
        self.data[key] = value
        self.writes.append(['set_raw', key, value])

    def mark_today(self, key):
        self.data[key] = real_datetime.date.today().isoformat()
        self.writes.append(['mark_today', key, self.data[key]])

    def flag_today(self, key):
        return self.data.get(key) == real_datetime.date.today().isoformat()

    def drain(self):
        writes, self.writes = self.writes, []
        return writes


@contextlib.contextmanager
def daily_state(store):
    from scheduling import daystate
    with mock.patch.object(daystate, 'get_raw', store.get_raw), mock.patch.object(daystate, 'set_raw', store.set_raw), \
            mock.patch.object(daystate, 'mark_today', store.mark_today), mock.patch.object(daystate, 'flag_today', store.flag_today):
        yield store


# --------------------------------------------------------- the module-level producers, whichever shape they have
def reward_due(name, now):
    from scheduling import global_rewards as g
    if hasattr(g, 'check_due'):
        return g.check_due(name, now)
    return g.decide(name, now).kind == 'due'


def reward_checked(name, now):
    from scheduling import global_rewards as g
    (g.checked if hasattr(g, 'checked') else g.settle)(name, now)


def store_due(now):
    from scheduling import global_rewards as g
    if hasattr(g, 'store_due'):
        return g.store_due(now)
    return g.store_decide(now).kind == 'due'


def store_claimed(now):
    from scheduling import global_rewards as g
    if hasattr(g, 'store_claimed'):
        return g.store_claimed()
    return g.store_settle(now)


# ---------------------------------------------------------------------------- R-1 and R-4: the account clocks
def reward_clock_traces():
    """The 300 s check clock of the three reward behaviours: due at once on an empty state, not due before 300 s have passed, due from exactly 300 s,
    independent per behaviour and per account, and it survives a restart because it lives in the daily state."""
    from scheduling import global_rewards as g
    out = {}
    times = [0, 1, 299, 299.999, 300, 300.001, 301, 599.999, 600, 1e6, -5]
    for account in ('alice', 'bob'):
        store, clock = Store(), Clock()
        rows = []
        with frozen(clock), daily_state(store), mock.patch.object(g, 'account_key', lambda account=account: 'global_rewards:' + account):
            for name in g.REWARD_KEYS:
                for start in (1000, 0, 123456.789):
                    store.data.clear()
                    seq = []
                    for at in times:
                        seq.append([at, reward_due(name, start + at)])
                    reward_checked(name, start)
                    after = [[at, reward_due(name, start + at)] for at in times]
                    other = [[o, reward_due(o, start + 1)] for o in g.REWARD_KEYS]
                    rows.append([name, start, seq, after, other, store.drain()])
        out[account] = rows
    return clean(out)


def store_traces():
    """The daily store: once per UTC day, 01:00 +/- 10 minutes (a stable offset per account and day), closed by a claim, per account."""
    from scheduling import global_rewards as g
    out = {}
    days = [real_datetime.date(2026, 9, 10) + real_datetime.timedelta(days=n) for n in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)] + [real_datetime.date(2028, 2, 29), real_datetime.date(2026, 12, 31)]
    for account in ('alice', 'bob', 'carol'):
        store, clock = Store(), Clock()
        rows = []
        with frozen(clock), daily_state(store), mock.patch.object(g, 'account_key', lambda account=account: 'global_rewards:' + account):
            for day in days:
                midnight = real_datetime.datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
                store.data.pop(f'global_rewards:{account}:store', None)
                lo, hi = 0, 7200                           # the first due second: bisect over the first two hours (monotone in the unclaimed state)
                assert not store_due(midnight + lo) and store_due(midnight + hi)
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if not store_due(midnight + mid) else (lo, mid)
                first = hi
                grid = [[s, store_due(midnight + s)] for s in (0, 3000, first - 1, first, first + 1, 7199, 7200, 43200, 80000, 86399, 86400, 86400 + 3000)]
                # the claim closes the day only: the next UTC day opens again at its own offset
                clock.local = None
                store.data[f'global_rewards:{account}:store'] = day.isoformat()
                closed = [[s, store_due(midnight + s)] for s in (first, first + 1, 43200, 86399)]
                nxt = [[s, store_due(midnight + s)] for s in (86400, 86400 + 3000, 86400 + 4200)]
                store.data.pop(f'global_rewards:{account}:store', None)
                rows.append([day.isoformat(), first, grid, closed, nxt])
            # store_claimed writes the claim day in UTC (the world clock is UTC-anchored, so this does not depend on the machine's time zone)
            clock.local = None
            for seconds in (0, 3000, 43200, 86399, 86400):
                clock.t = float(seconds)
                store.data.clear()
                store_claimed(clock.wall())
                rows.append(['claimed', seconds, store.drain()])
        out[account] = rows
    return clean(out)


# -------------------------------------------------------------------------------------------------------------- R-5 and R-7: chores
def chore_world(names=('quest_scan', 'shatter', 'housekeeping'), fail=(), results=None):
    from scheduling import chores
    calls = []

    def action(name):
        def run():
            calls.append(name)
            if name in fail:
                raise RuntimeError(f'{name} broke')
            return (results or {}).get(name, f'{name} done')
        return run
    registry = [(name, due, action(name)) for name, due, _ in chores.CHORES if name in names]
    return registry, calls


def chore_traces():
    """chores.run_due: one chore per gap in registry order, only from START_HOUR, once per local day, a failure is logged and marked, a disabled chore is
    skipped without a mark, an unreadable chores policy runs none."""
    from scheduling import chores
    from runtime import logger
    out = {}
    scenarios = {
        'plain': dict(),
        'first_fails': dict(fail=('quest_scan',)),
        'all_fail': dict(fail=('quest_scan', 'shatter', 'housekeeping')),
        'shatter_disabled': dict(policy={'shatter': False}),
        'all_disabled': dict(policy={'quest_scan': False, 'shatter': False, 'housekeeping': False}),
        'policy_unreadable': dict(policy=None),
        'limit_two': dict(limit=2),
        'limit_two_first_fails': dict(limit=2, fail=('quest_scan',)),
        'housekeeping_only': dict(names=('housekeeping',)),
    }
    for label, spec in scenarios.items():
        store, events = Store(), []
        # days: gaps at 02:59 (before the floor), 03:00, 03:00 again, 09:00 ..., then the next local day
        moments = [(9, 10, 2, 59), (9, 10, 3, 0), (9, 10, 3, 1), (9, 10, 9, 0), (9, 10, 9, 5), (9, 10, 9, 10), (9, 10, 23, 59), (9, 11, 0, 5), (9, 11, 2, 59), (9, 11, 3, 0),
                   (9, 11, 3, 1), (9, 11, 4, 0), (9, 11, 4, 1), (9, 11, 4, 2)]
        rows = []
        registry, calls = chore_world(spec.get('names', ('quest_scan', 'shatter', 'housekeeping')), spec.get('fail', ()))
        policy = spec.get('policy', {})
        clock = Clock()
        chores._disabled_logged.clear()
        with frozen(clock), daily_state(store), mock.patch.object(chores, 'CHORES', registry), mock.patch.object(chores, '_policy', lambda: policy), \
                mock.patch.object(logger, 'event', lambda kind, **fields: events.append([kind, clean(fields)])):
            for month, day, hour, minute in moments:
                clock.local = real_datetime.datetime(2026, month, day, hour, minute)
                ran = chores.run_due(spec.get('limit', 1))
                rows.append([f'{month:02d}-{day:02d} {hour:02d}:{minute:02d}', ran, list(calls), dict(sorted(store.data.items())), store.drain(), list(events)])
                calls.clear()
                events.clear()
        out[label] = rows
    # a due predicate that raises is logged and skipped
    store, events = Store(), []
    registry, calls = chore_world()
    registry = [(name, (lambda: (_ for _ in ()).throw(RuntimeError('due broke'))) if name == 'quest_scan' else due, action) for name, due, action in registry]
    clock = Clock(real_datetime.datetime(2026, 9, 10, 9, 0))
    with frozen(clock), daily_state(store), mock.patch.object(chores, 'CHORES', registry), mock.patch.object(chores, '_policy', lambda: {}), \
            mock.patch.object(logger, 'event', lambda kind, **fields: events.append([kind, clean(fields)])):
        ran = chores.run_due()
        out['due_raises'] = clean([ran, list(calls), dict(store.data), events])
    return out


def chore_real_registry():
    """The shipped registry itself: names, order and what each due predicate says in the same moments (the actions are never called)."""
    from scheduling import chores
    store, clock = Store(), Clock()
    rows = []
    with frozen(clock), daily_state(store):
        for hour, minute in ((2, 59), (3, 0), (12, 0)):
            clock.local = real_datetime.datetime(2026, 9, 10, hour, minute)
            rows.append([hour, minute, {name: bool(due()) for name, due, _ in chores.CHORES}])
        store.data['chore_quest_scan'] = '2026-09-10'
        rows.append(['quest_scan done', {name: bool(due()) for name, due, _ in chores.CHORES}])
    return clean(dict(names=[name for name, _, _ in chores.CHORES], rows=rows, start_hour=chores.START_HOUR))


# -------------------------------------------------------------------- R-2, R-3, R-4 in the loop: advance_rewards and step_reward_mission
class FlowGen:
    def __init__(self, label, on_success=None):
        self.label, self.on_success = label, on_success


class FakeMission:
    def __init__(self, started):
        self.active, self.started, self.flow, self.stepped, self.ends_on_step = False, started, None, 0, False

    def start(self, flow):
        if not self.active:
            self.active, self.flow, self.stepped = True, flow(), 0
            self.started.append(self.flow.label)

    def step(self, frame):
        self.stepped += 1
        if self.ends_on_step:                     # a flow whose last action was this step (the real Mission drops the generator and goes inactive here)
            self.active, self.ends_on_step = False, False

    def finish(self):
        self.active = False


def reward_world(clock, seed, events, draws, started, store, *, gather=None, global_=None, account='alice'):
    """Everything advance_rewards and step_reward_mission read, replaced: the badge detectors, the flows, the mission runner, the random source, the
    visit delay range, the preset and the account. Returns (rs, patches)."""
    import orchestrator
    from interactions import event_rewards, missions
    from interactions import store as store_module
    from scheduling import global_rewards as g
    badges = SimpleNamespace(quests=False, guild=False, events=False)
    rs = SimpleNamespace(menu_closed_frames=0, menu_open_frames=0, shop=SimpleNamespace(active=False), mission=FakeMission(started), quest_due=None, last_visit={},
                         pending_store=False, free_gems_try_at=0.0, await_guild_result=False)
    prefs = SimpleNamespace(gather=gather if gather is not None else {'quests_8h': True, 'quest_rewards': True, 'guild': True, 'guild_store': True, 'free_store_gems': False},
                            global_=global_ if global_ is not None else {})

    def flow(label):
        def make(*args, **kwargs):
            return FlowGen(label, kwargs.get('on_success'))
        make.__name__ = label
        return make
    patches = [
        mock.patch.object(missions, 'quests_badge', lambda frame: badges.quests), mock.patch.object(missions, 'guild_badge', lambda frame: badges.guild),
        mock.patch.object(missions, 'events_badge', lambda frame: badges.events), mock.patch.object(missions, 'quest_flow', flow('quest_flow')),
        mock.patch.object(missions, 'guild_flow', flow('guild_flow')), mock.patch.object(missions, 'free_gems_flow', flow('free_gems_flow')),
        mock.patch.object(event_rewards, 'event_flow', flow('event_flow')), mock.patch.object(store_module, 'store_flow', flow('store_flow')),
        mock.patch.object(orchestrator, 'random', Draws(seed, draws)), mock.patch.object(orchestrator, 'preset', lambda: {'gather': prefs.gather, 'global_behaviors': prefs.global_}),
        mock.patch.dict(orchestrator.CONFIG, {'missions': {'visit_delay_sec': [60, 600]}}), mock.patch.object(g, 'account_key', lambda: 'global_rewards:' + account),
        mock.patch.object(orchestrator.logger, 'event', lambda kind, **fields: events.append([kind, clean(fields)])),
    ]
    return rs, badges, prefs, patches


def reward_view(rs, badges, store, events, draws, started, passed):
    return clean(dict(passed=passed, menu_open_frames=rs.menu_open_frames, quest_due=rs.quest_due, last_visit=rs.last_visit, pending_store=rs.pending_store,
                      free_gems_try_at=rs.free_gems_try_at, await_guild_result=rs.await_guild_result, active=rs.mission.active,
                      started=list(started), writes=store.drain(), events=list(events), draws=list(draws), daystate=dict(sorted(store.data.items()))))


def run_rewards(script, *, seed=1, **world_args):
    """Drive the reward-menu passes: `('tick', dt)` advances the clock and makes one pass the way main() does (the running flow first, else the menu upkeep);
    `('badge', quests, guild, events)`, `('shop', active)`, `('end', claims, store_claimed)` (the running flow ends; a guild flow claimed `claims`; a free-gems flow
    called its on_success), `('globals', {...})`, `('gather', {...})`, `('mission', active)` set the world."""
    import orchestrator
    from interactions import missions
    clock, store, events, draws, started = Clock(), Store(), [], [], []
    rs, badges, prefs, patches = reward_world(clock, seed, events, draws, started, store, **world_args)
    steps = []
    with frozen(clock), daily_state(store), contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        for op in script:
            kind = op[0]
            if kind == 'tick':
                clock.t += op[1]
                started.clear()
                if orchestrator.step_reward_mission(rs, None):
                    passed = 'mission_step'
                else:
                    passed = 'continue' if orchestrator.advance_rewards(rs, None, clock.t, 123, prefs.global_) else 'fall_through'
                steps.append([clock.t, passed, reward_view(rs, badges, store, events, draws, started, passed)])
                events.clear()
                draws.clear()
            elif kind == 'badge':
                badges.quests, badges.guild, badges.events = op[1], op[2], op[3]
            elif kind == 'shop':
                rs.shop.active = op[1]
            elif kind == 'visited':                       # ('visited', badge, at): the host last started a flow for that badge at monotonic time `at`
                rs.last_visit[op[1]] = op[2]
            elif kind == 'finish':                        # ('finish', claims, store_claimed): the running flow ends INSIDE its next step, as the real Mission's does
                flow = rs.mission.flow
                missions.last_guild_claims = op[1]
                if op[2] and flow is not None and flow.on_success:
                    flow.on_success()
                rs.mission.ends_on_step = True
                started.clear()
                passed = 'mission_step' if orchestrator.step_reward_mission(rs, None) else 'no_flow'
                steps.append([clock.t, 'finish', reward_view(rs, badges, store, events, draws, started, passed)])
                events.clear()
                draws.clear()
            elif kind == 'globals':
                prefs.global_ = dict(op[1])
            elif kind == 'gather':
                prefs.gather = dict(op[1])
            elif kind == 'end':
                flow = rs.mission.flow
                missions.last_guild_claims = op[1]
                if op[2] and flow is not None and flow.on_success:
                    flow.on_success()
                rs.mission.finish()
                steps.append([clock.t, 'end', reward_view(rs, badges, store, events, draws, started, 'end')])
                events.clear()
                draws.clear()
            else:
                raise ValueError(kind)
    return clean(steps)


def ticks(count, dt):
    return [('tick', dt)] * count


def reward_scenarios():
    quiet = {}
    return {
        # the global checks: every 300 s per behaviour, the flow starts on a badge; a disabled behaviour is never checked
        'globals_no_badge': (dict(global_={'daily_missions': True, 'guild_progress': True, 'event_missions': True}, gather={}),
                             [('tick', 1), ('tick', 1)] + ticks(10, 30)),
        'globals_badge_daily': (dict(global_={'daily_missions': True, 'guild_progress': True, 'event_missions': True}, gather={}),
                                [('badge', True, False, False), ('tick', 1), ('tick', 1), ('end', 0, False)] + ticks(12, 30)),
        'globals_badge_guild_then_events': (dict(global_={'guild_progress': True, 'event_missions': True}, gather={}),
                                            [('badge', False, True, True), ('tick', 1), ('tick', 1), ('end', 0, False), ('tick', 1), ('tick', 1), ('end', 0, False)] + ticks(12, 30)),
        'globals_one_disabled': (dict(global_={'daily_missions': False, 'event_missions': True}, gather={}),
                                 [('badge', True, True, False)] + ticks(8, 100)),
        'globals_shop_active_blocks': (dict(global_={'daily_missions': True}, gather={}),
                                       [('shop', True), ('badge', True, False, False)] + ticks(4, 10) + [('shop', False)] + ticks(4, 10)),
        # the legacy visits: a seen badge schedules a visit after a sampled delay; a repeat badge soon after a visit waits longer
        'visit_quests': (dict(), [('badge', True, False, False)] + ticks(40, 60)),
        'visit_guild_claims_then_store': (dict(), [('badge', False, True, False)] + ticks(30, 60) + [('end', 2, False)] + ticks(6, 5) + [('end', 0, False)] + ticks(6, 5)),
        'visit_guild_claims_no_store_gather': (dict(gather={'quests_8h': True, 'guild': True, 'guild_store': False}),
                                               [('badge', False, True, False)] + ticks(30, 60) + [('end', 2, False)] + ticks(6, 5)),
        'visit_guild_no_claims': (dict(), [('badge', False, True, False)] + ticks(30, 60) + [('end', 0, False)] + ticks(8, 5)),
        'visit_badge_flips': (dict(), [('badge', True, False, False)] + ticks(3, 5) + [('badge', False, True, False)] + ticks(4, 5) + [('badge', False, False, False)] + ticks(3, 5)
                              + [('badge', True, False, False)] + ticks(20, 60)),
        'visit_recent_repeat': (dict(), [('badge', True, False, False)] + ticks(40, 60) + [('end', 0, False)] + ticks(60, 60)),
        'visit_quest_gates': (dict(gather={'quests_8h': False, 'quest_rewards': False, 'guild': False}), [('badge', True, True, False)] + ticks(40, 60)),
        'visit_superseded_by_globals': (dict(global_={'daily_missions': True, 'guild_progress': True}), [('badge', True, True, False)] + ticks(40, 60)),
        'visit_shop_active': (dict(), [('shop', True), ('badge', True, False, False)] + ticks(40, 30) + [('shop', False)] + ticks(10, 30)),
        # the daily store: UTC day, about 01:00, a failed or unavailable claim retries after 300 s, a confirmed claim closes the day
        'store_unavailable_retries': (dict(gather={'free_store_gems': True}), ticks(8, 600) + [('tick', 60)] * 20 + [('end', 0, False)] + ticks(30, 60)),
        'store_confirmed_closes_the_day': (dict(gather={'free_store_gems': True}), ticks(8, 600) + [('tick', 60)] * 10 + [('end', 0, True)] + ticks(30, 3600)),
        'store_global_switch': (dict(global_={'free_store_gems': True}, gather={'free_store_gems': False, 'quests_8h': False, 'guild': False}), ticks(8, 600) + [('tick', 60)] * 10 + [('end', 0, False)] + ticks(10, 60)),
        'store_off': (dict(gather={'free_store_gems': False, 'quests_8h': False, 'guild': False}), ticks(10, 600)),
        'store_blocked_by_menu_settling': (dict(gather={'free_store_gems': True, 'quests_8h': False, 'guild': False}), ticks(1, 4000) + ticks(5, 60)),
    }


def reward_traces():
    return {name: run_rewards(script, **args) for name, (args, script) in reward_scenarios().items()}


def extra_reward_scenarios():
    """Scenarios added after the first capture, captured on the SAME pre-migration code (the fixture's `captured_on` hashes prove it): the 1200 s
    'recently visited' rule at its boundary. The second tick (t = 10) is the pass that sees the badge for the second time and draws the delay."""
    seen = [('badge', True, False, False)]
    guild = [('badge', False, True, False)]
    return {
        'recent_visit_1199_999_s_ago': (dict(), [('visited', 'quests', 10 - 1199.999)] + seen + ticks(3, 5)),
        'recent_visit_1200_s_ago': (dict(), [('visited', 'quests', 10 - 1200.0)] + seen + ticks(3, 5)),
        'recent_visit_1200_001_s_ago': (dict(), [('visited', 'quests', 10 - 1200.001)] + seen + ticks(3, 5)),
        'recent_guild_visit_600_s_ago': (dict(), [('visited', 'guild', 10 - 600.0)] + guild + ticks(3, 5)),
        'recent_visit_of_the_other_badge_does_not_count': (dict(), [('visited', 'guild', 10 - 60.0)] + seen + ticks(3, 5)),
        'recent_repeat_after_a_real_visit': (dict(), seen + ticks(70, 10) + [('end', 0, False)] + ticks(130, 10)),
        'recent_guild_repeat_with_store': (dict(), guild + ticks(70, 10) + [('end', 2, False)] + ticks(14, 5) + [('end', 0, False)] + ticks(130, 10)),
    }


def finishing_reward_scenarios():
    """Scenarios added after the first capture, again on the SAME pre-migration code: the first capture ended a flow by setting `active` from outside, so the end of a flow INSIDE
    `step_reward_mission` (where the guild-store follow-up is raised and the await flag is cleared) was never exercised. Here the flow ends inside its step."""
    seen = [('badge', True, False, False)]
    guild = [('badge', False, True, False)]
    return {
        'guild_claims_raise_the_follow_up_and_it_runs_once': (dict(), guild + ticks(40, 30) + [('finish', 2, False)] + ticks(3, 5) + [('finish', 0, False)] + ticks(8, 5)),
        'guild_without_claims_raises_nothing': (dict(), guild + ticks(40, 30) + [('finish', 0, False)] + ticks(6, 5)),
        'guild_claims_but_the_store_gather_is_off': (dict(gather={'quests_8h': True, 'guild': True, 'guild_store': False}), guild + ticks(40, 30) + [('finish', 3, False)] + ticks(6, 5)),
        'guild_claims_while_shopping_wait_for_the_sweep': (dict(), guild + ticks(40, 30) + [('finish', 1, False), ('shop', True)] + ticks(3, 5) + [('shop', False)] + ticks(3, 5) + [('finish', 0, False)] + ticks(4, 5)),
        'quest_visit_ends_with_no_follow_up': (dict(), seen + ticks(40, 30) + [('finish', 5, False)] + ticks(6, 5)),
        'global_guild_check_claims_raise_no_follow_up': (dict(global_={'guild_progress': True}, gather={}), guild + ticks(2, 1) + [('finish', 4, False)] + ticks(6, 5)),
        'store_claim_confirmed_inside_the_flow_closes_the_day': (dict(gather={'free_store_gems': True, 'quests_8h': False, 'guild': False}), ticks(8, 600) + ticks(4, 300) + [('finish', 0, True)] + ticks(6, 300)),
        'store_flow_ends_without_a_confirmed_claim_and_retries_after_300_s': (dict(gather={'free_store_gems': True, 'quests_8h': False, 'guild': False}), ticks(8, 600) + [('finish', 0, False)] + ticks(2, 100) + ticks(6, 100)),
    }


def finishing_reward_traces():
    return {name: run_rewards(script, **args) for name, (args, script) in finishing_reward_scenarios().items()}


def extra_reward_traces():
    return {name: run_rewards(script, **args) for name, (args, script) in extra_reward_scenarios().items()}


def reward_fuzz(seed, length=160):
    rng = random.Random(seed)
    script = []
    for _ in range(length):
        roll = rng.random()
        if roll < 0.55:
            script.append(('tick', rng.choice([1, 5, 30, 60, 299, 300, 301, 900, 3600])))
        elif roll < 0.7:
            script.append(('badge', rng.random() < 0.4, rng.random() < 0.4, rng.random() < 0.3))
        elif roll < 0.78:
            script.append(('shop', rng.random() < 0.3))
        elif roll < 0.86:
            script.append(('globals', {key: rng.random() < 0.5 for key in ('daily_missions', 'guild_progress', 'event_missions', 'free_store_gems')}))
        elif roll < 0.92:
            script.append(('gather', {'quests_8h': rng.random() < 0.8, 'quest_rewards': rng.random() < 0.8, 'guild': rng.random() < 0.8, 'guild_store': rng.random() < 0.8, 'free_store_gems': rng.random() < 0.5}))
        else:
            script.append(('end', rng.choice([0, 0, 1, 3]), rng.random() < 0.5))
    return script


FUZZ_SEEDS = (101, 102, 103, 104, 105, 106)


def fuzz_traces():
    return {str(seed): run_rewards(reward_fuzz(seed), seed=seed, gather={'quests_8h': True, 'quest_rewards': True, 'guild': True, 'guild_store': True, 'free_store_gems': True}) for seed in FUZZ_SEEDS}


def all_traces():
    return dict(version=1, captured_on=source_hashes(), reward_clocks=reward_clock_traces(), store=store_traces(), chores=chore_traces(), chore_registry=chore_real_registry(),
                rewards=reward_traces(), fuzz=fuzz_traces(), rewards_extra=extra_reward_traces(), rewards_finishing=finishing_reward_traces())
