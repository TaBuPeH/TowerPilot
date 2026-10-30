"""The latency gate for the fast rules (roadmap item 7, spec 3.7): the harness. Tests: test_action_latency_gate.py. Not a test module.

WHAT IT PROTECTS. The fast wall loop of the orchestrator races a wall drain that is faster than a main-loop pass. A rescue tap that arrives late costs the run, and the
per-sample body is documented as the one place where a dict lookup is "too slow" (orchestrator.py `_fast_wall_watch`). Moving the timing of C-3 (fast rescue) and C-4 (fleet nuke) onto the
shared schedule policies adds code to exactly these paths. This module measures what any such change adds, against the code as it was BEFORE the migration. It changes no behaviour and
sends no input: every screen, detection and tap function is a fake from `_fakes.py` that does nothing but follow a script.

TWO GATES.
  Structural (hard). One pass of each path, with `sqlite3.connect`, file opens, lock acquisitions, image writes, log events and shots counted, may do no more of any of them than the
  unmigrated code did (the counts are recorded in fixtures/latency_gate/baseline_structural_v1.json), counted both up to the first tap and in total. A policy check is a comparison on host
  state; an execution record is written after the fire returns, never before the tap.
  The daily state is counted by CHARGE, not by measurement: its fake (`_fakes.py`) opens nothing, so every call to it is charged what the real `scheduling/daystate.py` registers
  (DAYSTATE_COST, pinned against the real module by the tests), however the orchestrator reached it (its own import, `scheduling.global_rewards`, a late import). Everything else in the
  structural counts is measured (the real `sqlite3.connect`, `open`, file lock, `cv2.imwrite` and logger, and a REAL daily state when a late import reaches one).
  Timing (measured, with provisional targets). A seeded workload of 10,000 iterations over eleven paths is replayed through the unmigrated code (the pinned snapshot
  fixtures/latency_gate/orchestrator_unmigrated.py.txt) and through the live orchestrator, back to back, in alternating order, on identical scripted worlds. Two numbers per iteration:
    added_pass_us   the whole call, candidate minus baseline. This is the added policy cost: policy, bookkeeping and any recording are all inside the call, so it can only over-state it.
    added_first_tap_us   from the return of the capture that produced the deciding frame (the entry of the call, when the frame is already in hand) to the first tap, candidate minus baseline.
  Targets: p99 of each <= 100 microseconds and <= 1 millisecond. They are provisional regression targets from the specification, not measured facts; the artifact records what was measured.

NOISE. Each side of an iteration is measured REPEATS times on a fresh identical world and the minimum is kept (the work is deterministic Python; what a repeat can only add is the machine). The
garbage collector is off during a replay, `time.sleep` is a no-op, and the first WARMUP iterations are dropped. The harness also reports baseline-against-baseline (the noise floor).

WHAT IT DOES NOT MEASURE. Detection, capture and the real adb round trip are fakes: absolute latency of a real rescue is dominated by them and is not claimed here, only what a migration adds.
The timing replay stubs `BattleSession.checkpoint` (its body is in the baseline and identical on both sides); the structural gate runs the REAL checkpoint over a temporary ledger.
The timing replay also runs the fake daily state and is not metered: what a state read or write would cost in production is refused by the structural charge, it is NOT in the
microseconds. A charge is a counting rule, not a timing proof.
"""

import paths as test_paths
import ast
import builtins
import contextlib
import gc
import hashlib
import importlib.util
import io
import json
import math
import os
import platform
import random
import sqlite3
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

from _fakes import _install_fakes, _rs, install_modules, restore_modules

BACKEND = test_paths.BACKEND
FIXTURES = test_paths.FIXTURES / 'latency_gate'
CANDIDATE_PATH = BACKEND / 'orchestrator.py'
SNAPSHOT_PATH = FIXTURES / 'orchestrator_unmigrated.py.txt'
BASELINE_PATH = FIXTURES / 'baseline_structural_v1.json'

TARGET_POLICY_US = 100.0          # p99 added cost of the whole call
TARGET_FIRST_TAP_US = 1000.0      # p99 added time from the deciding frame to the first tap
ITERATIONS = 10_000
WARMUP = 500
REPEATS = 3
SEED = 20261023
COUNTERS = ('sqlite_connect', 'file_open', 'lock_acquire', 'image_write', 'event', 'shot')
# The daily state (scheduling/daystate.py) is a fake under _fakes.py and opens nothing, so the structural meter charges every call to it what the real module registers under the same meter
# (pinned by test_a_fake_daily_state_is_charged_what_the_real_one_registers): a read loads the state file; any other function is one locked load-and-save step (`update`).
DAYSTATE_READS = ('get_raw', 'get_today', 'flag_today')
DAYSTATE_COST = dict(read=dict(file_open=1), write=dict(file_open=3, lock_acquire=3))
# The modules the orchestrator imports that bind the daily state. Every load executes them afresh while the fakes are installed, so each binds THIS load's fake whatever an earlier test left
# in sys.modules (nothing, the real module, or one bound to another suite's fake), and afterwards they are put back exactly as they were.
FRESH_PER_LOAD = ('scheduling.global_rewards',)
FAST_FUNCTIONS = ('_fast_wall_watch', 'fire_button', 'fleet_gate', 'fleet_fire_skipped', 'watch_frame', 'demon_mode_always', 'end_intro_sprint', 'marks', '_run_rules', '_rule_act', 'eval_rules')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def function_hashes(source, names=FAST_FUNCTIONS):
    """{name: sha256 of the function's source text} for the module-level functions of `source` (a path)."""
    text = Path(source).read_text(encoding='utf-8')
    found = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = hashlib.sha256(ast.get_source_segment(text, node).encode('utf-8')).hexdigest().upper()
    return found


# ------------------------------------------------------------------------------------ loading
_ABSENT = object()
_ACTIVE_CHARGES = []          # (thread id, counts) of each structural pass running now; the charge wrappers below bill the innermost one


def charge_daystate_fake(fake):
    """Make every public function of a fake daily state bill DAYSTATE_COST to the structural pass running on the calling thread (to nothing when no pass runs). Called BEFORE a consumer
    can import the fake, so rom scheduling.daystate import get_raw binds the metered function, not an unmetered original. Idempotent."""
    def billed(cost, function):
        def wrapper(*args, **kwargs):
            if _ACTIVE_CHARGES and _ACTIVE_CHARGES[-1][0] == threading.get_ident():
                counts = _ACTIVE_CHARGES[-1][1]
                for name, amount in cost.items():
                    counts[name] += amount
            return function(*args, **kwargs)
        wrapper.charged_by_latency_gate = True
        return wrapper
    for name, function in list(vars(fake).items()):
        if callable(function) and not name.startswith('_') and not getattr(function, 'charged_by_latency_gate', False):
            setattr(fake, name, billed(DAYSTATE_COST['read' if name in DAYSTATE_READS else 'write'], function))


def modules_bound_to(fakes, names=None):
    """{name: module} of the loaded modules among `names` (default: all of them) that hold one of `fakes` or a function one exports, or hold a module (or a function of a module) that does: what an import made while the fakes are installed leaves behind."""
    tainted = {id(fake) for fake in fakes} | {id(value) for fake in fakes for value in list(vars(fake).values()) if isinstance(value, (types.FunctionType, types.MethodType))}          # a fake and every function or bound method it exports
    tainted_globals, bound = set(), {}
    loaded = {name: sys.modules[name] for name in (list(sys.modules) if names is None else names) if sys.modules.get(name) is not None}

    def holds(value):
        if isinstance(value, types.MethodType):          # `from runtime.logger import event`: a method of the fake logger, a new bound object on every attribute read
            return id(value.__self__) in tainted
        return id(value) in tainted or (isinstance(value, types.FunctionType) and id(value.__globals__) in tainted_globals)          # `from <fake> import f` and `from <bound module> import f`
    grew = True
    while grew:
        grew = False
        for name, module in loaded.items():
            if name not in bound and any(holds(value) for value in list(getattr(module, '__dict__', {}).values())):
                bound[name], grew = module, True
                tainted.add(id(module))
                tainted_globals.add(id(module.__dict__))
    return bound


def _hide(names):
    """Take the modules `names` out of the way of the next import; the function returned puts every one back as it was (its sys.modules entry and its attribute on the package, present or absent)."""
    held = []
    for dotted in names:
        parent, _, leaf = dotted.rpartition('.')
        package = importlib.import_module(parent)
        held.append((dotted, package, leaf, sys.modules.pop(dotted, None), vars(package).pop(leaf, _ABSENT)))

    def restore():
        for dotted, package, leaf, module, attribute in held:
            if module is None:
                sys.modules.pop(dotted, None)
            else:
                sys.modules[dotted] = module
            if attribute is _ABSENT:
                vars(package).pop(leaf, None)
            else:
                setattr(package, leaf, attribute)
    return restore


def _evict(leaked):
    """Drop modules from sys.modules and from their package: the next importer gets the real ones."""
    for name, module in leaked.items():
        sys.modules.pop(name, None)
        parent, _, leaf = name.rpartition('.')
        if parent in sys.modules and vars(sys.modules[parent]).get(leaf) is module:
            del vars(sys.modules[parent])[leaf]


@contextlib.contextmanager
def scratch_daystate():
    """A late import can reach the REAL daily state: for the duration its STATE is a scratch file, never the machine's own."""
    with tempfile.TemporaryDirectory(prefix='latency-gate-state-', ignore_cleanup_errors=True) as scratch:
        try:
            daystate = importlib.import_module('scheduling.daystate')
        except ImportError:
            yield None
            return
        saved, daystate.STATE = daystate.STATE, os.path.join(scratch, 'daily_state.json')
        try:
            yield daystate
        finally:
            daystate.STATE = saved


def load_orchestrator(source, key):
    """An orchestrator module compiled from `source` under the shared fakes (every screen-touching module is a fake the module keeps a reference to), like the suite's `_import_live`
    but from any path: the live file, or the pinned snapshot. The source is compiled here, not imported, so no bytecode cache is written next to it.

    The window leaves nothing bound to a fake. The modules that bind the daily state (FRESH_PER_LOAD) are executed afresh for this load, so the module always reaches ITS fake through them
    (an earlier importer's real module, or another suite's leftover, cannot be what it gets), and are put back as they were; any other module first imported inside the window that ended up
    holding a fake is dropped, so the next importer imports it for real. The module keeps its own copies (`module.global_rewards.daystate is module._fakes['daystate']`). A module an earlier
    test left bound to ITS fakes is left as found: that leak is not this harness's."""
    rec_taps = []
    mods, log, _loadout = _install_fakes(rec_taps)
    charge_daystate_fake(mods['daystate'])          # before the fakes are installed: every alias a consumer takes of them is already metered
    fakes = [fake for fake in mods.values() if getattr(fake, '__spec__', None) is None]          # `settings` is the real module
    saved = install_modules(mods)
    restore_fresh = _hide(FRESH_PER_LOAD)
    known = set(sys.modules)
    previous = sys.modules.get(key)
    try:
        module = types.ModuleType(key)
        module.__file__ = str(source)
        sys.modules[key] = module
        exec(compile(Path(source).read_text(encoding='utf-8'), str(source), 'exec'), module.__dict__)
    finally:
        restore_modules(saved)
        _evict(modules_bound_to(fakes, [name for name in sys.modules if name not in known and name != key]))
        restore_fresh()
        if previous is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = previous
    module._fakes, module._log = mods, log
    return module


# ------------------------------------------------------------------------------------ scripted world
READY = SimpleNamespace(present=True, center=(242, 1493), ready=True, score=0.95)
ABSENT = SimpleNamespace(present=False, center=None, ready=False, score=0.0)
BATTLE = SimpleNamespace(name='battle', score=1.0)
SCHEDULE_ONLY = {'fleet': {'sighting': {'enabled': False}}}      # fleet_gate answers "schedule" without a vision call


class StubSession:
    """What the fast paths read of a BattleSession in the timing replay: config, gates and a checkpoint that costs nothing (its body is in both sides' baseline)."""

    def __init__(self):
        self.config, self.gates, self.calls = SCHEDULE_ONLY, {}, 0

    def checkpoint(self, rs, *, battle_visible):
        self.calls += 1

    def effective_preset(self):
        return {}


class World:
    """One orchestrator copy plus its script. The fakes the module holds are bound once; `begin()` resets the script for the next call. Frames are small integers: frame i is the
    i-th capture of the call."""

    def __init__(self, module, clock=time.perf_counter_ns):
        self.m, self.clock = module, clock
        self.log = module._log
        f = module._fakes
        f['capture'].grab = self.grab
        f['act'].tap = self.tap
        f['wave_reader'].read_wave = lambda frame: self.wave
        f['screen'].identify = lambda frame: BATTLE
        detect = f['detect']
        detect.wall_overheal = self.wall_overheal
        detect.button_state = lambda frame, name: self.buttons.get(name, ABSENT)
        detect.button_border_val = self.border
        detect.find_intro_sprint = lambda frame: self.sprint_pt
        detect.hp_fill = lambda frame: self.hp
        module.preset = lambda: self.preset
        module.marks = lambda: self.marks
        self.session_rs = None
        self.begin()

    def begin(self, on_first_tap=None):
        self.walls, self.next_frame, self.wave, self.hp = [None], 1, 1498, 1.0
        self.buttons, self.sprint_pt, self.preset, self.marks = {}, None, {}, []
        self.border_seq, self.border_calls = [200, 80], 0
        self.t_grab = self.t_first_tap = self.t_decide = None
        self.tap_log, self.on_first_tap = [], on_first_tap
        self.log.events.clear()
        self.log.shots.clear()
        self.m._battle_session = None

    def grab(self):
        frame = self.next_frame
        self.next_frame = frame + 1
        self.t_grab = self.clock()
        return frame

    def tap(self, x, y, reason='', instant=False, **kw):
        now = self.clock()
        if self.t_first_tap is None:
            self.t_first_tap, self.t_decide = now, self.t_grab
            if self.on_first_tap is not None:
                self.on_first_tap()
        self.tap_log.append((x, y, reason))
        return {'x': x, 'y': y}

    def wall_overheal(self, frame):
        walls = self.walls
        return walls[frame] if frame < len(walls) else (1.0, 'immune')

    def border(self, frame, name):
        index = self.border_calls
        self.border_calls = index + 1
        seq = self.border_seq
        return seq[index] if index < len(seq) else seq[-1]


def rs_for(**kw):
    base = dict(wave_hwm=None, dm_seen_at=None, sw_proc_count=0, dm_always_try_at=0.0, dm_always_count=0, gems=SimpleNamespace(poll=lambda frame: frame))
    base.update(kw)
    return _rs(**base)


def abilities(**kw):
    ab = {'dm_below': 0.2, 'rescue_bar': 'wall', 'hold_until_second_wind': True, 'refire_guard_sec': 15, 'nuke_on_fleet': None}
    ab.update(kw)
    return ab


FLEET = {'after_waves': 1, 'window_waves': 60, 'throttle_sec': 5.0, 'require_ready': False}


# ------------------------------------------------------------------------------------ the paths
class PathSpec:
    """One measured path: how to script a world for it and the call to time. `session` says the path reads a BattleSession (config, gates, checkpoint)."""

    def __init__(self, name, weight, has_tap, session, prepare):
        self.name, self.weight, self.has_tap, self.session, self.prepare = name, weight, has_tap, session, prepare


def _attach(world, rs, ctx, ab_preset):
    """Install the session: a stub in the timing replay, the real BattleSession (over a temporary ledger) in the structural pass."""
    if ctx is None:
        world.m._battle_session = StubSession()
        return rs
    session, real_rs = ctx.session(world, ab_preset)
    world.m._battle_session = session
    return real_rs(rs)


def p_fast_idle(w, v, ctx):
    n = 5 + v % 3
    w.walls = [None] + [(0.9, 'normal')] * (n - 1) + [(1.0, 'immune')]
    rs, ab, m = rs_for(post_sw_until=math.inf), abilities(), w.m
    return lambda: m._fast_wall_watch(rs, ab)


def p_fast_checkpoint(w, v, ctx):
    w.walls = [None] + [(0.9, 'normal')] * 7 + [(1.0, 'immune')]
    ab, m = abilities(), w.m
    w.preset = {'abilities': ab}
    rs = _attach(w, rs_for(post_sw_until=math.inf), ctx, w.preset)
    return lambda: m._fast_wall_watch(rs, ab)


def p_fast_fleet(w, v, ctx):
    w.walls = [None] + [(0.9, 'normal')] * 7 + [(1.0, 'immune')]
    w.marks, w.buttons = [1495], {'nuke': READY}
    ab, m = abilities(nuke_on_fleet=FLEET), w.m
    w.preset = {'abilities': ab}
    rs = _attach(w, rs_for(post_sw_until=math.inf, tracker=SimpleNamespace(last=1498, update=lambda wave: None)), ctx, w.preset)
    return lambda: m._fast_wall_watch(rs, ab)


def _burst(w, v, sprint_ended):
    lead = v % 3
    w.walls = [None] + [(0.9, 'normal')] * (1 + lead) + [(0.5, 'normal'), (0.15, 'normal')]
    w.buttons, w.sprint_pt, w.border_seq = {'demon_mode': READY}, (540, 100), [100]
    rs, ab, m = rs_for(post_sw_until=math.inf, sprint_ended=sprint_ended), abilities(), w.m
    return lambda: m._fast_wall_watch(rs, ab)


def p_fast_burst(w, v, ctx):
    return _burst(w, v, False)


def p_fast_burst_dm_only(w, v, ctx):
    return _burst(w, v, True)


def p_fast_strict(w, v, ctx):
    w.walls = [None, (0.9, 'normal'), (0.5, 'normal'), (0.15, 'normal'), (0.10, 'normal'), (0.05, 'normal'), (0.03, 'normal'), (1.0, 'immune')]
    rs, ab, m = rs_for(post_sw_until=math.inf, sprint_ended=True), abilities(burst_require_match=True), w.m      # no glyph in strict mode: keep sampling, tap nothing
    return lambda: m._fast_wall_watch(rs, ab)


def p_hp_rescue(w, v, ctx):
    w.hp, w.buttons = 0.1, {'demon_mode': READY}
    w.preset = {'abilities': abilities(rescue_bar='hp', hold_until_second_wind=False, nuke_below=None)}
    rs, m = rs_for(sw_proc_count=1, sprint_ended=True), w.m
    return lambda: m.watch_frame(rs, 0, observation={'raw_wave': 1498})


def p_wf_idle(w, v, ctx):
    w.preset = {'abilities': abilities()}
    rs, m = rs_for(), w.m
    return lambda: m.watch_frame(rs, 0, observation={'raw_wave': 1498})


def p_wf_dm_always(w, v, ctx):
    w.buttons = {'demon_mode': READY}
    w.preset = {'abilities': abilities(), 'global_behaviors': {'demon_mode_always': True}}
    rs, m = rs_for(sprint_ended=True, tracker=SimpleNamespace(last=1498, update=lambda wave: None)), w.m
    return lambda: m.watch_frame(rs, 0, observation={'raw_wave': 1498})


def p_wf_fleet(w, v, ctx):
    w.marks, w.buttons = [1495], {'nuke': READY}
    ab, m = abilities(nuke_on_fleet=FLEET), w.m
    w.preset = {'abilities': ab}
    m._battle_session = StubSession()
    rs = rs_for(tracker=SimpleNamespace(last=1498, update=lambda wave: None))
    return lambda: m.watch_frame(rs, 0, observation={'raw_wave': 1498})


def p_wf_wall_entry(w, v, ctx):
    w.walls = [None, (1.0, 'immune')]
    w.preset = {'abilities': abilities(hold_until_second_wind=False)}
    rs, m = rs_for(sw_proc_count=1, post_sw_until=math.inf), w.m
    return lambda: m.watch_frame(rs, 0, observation={'raw_wave': 1498})


def p_rules_quiet(w, v, ctx):
    """Three compiled rules and nothing to do: one not yet triggered, one cooling down (a suppression), one already fired this run."""
    rules = [{'id': 'wave_far', 'when': {'wave_at_least': 99999}, 'do': {'fire': {'button': 'nuke'}}},
             {'id': 'cooling', 'when': {'wave_at_least': 1}, 'repeat': True, 'refire_sec': 900, 'do': {'kind': 'fire', 'button': 'nuke'}},
             {'id': 'done', 'when': {'wave_at_least': 1}, 'do': {'kind': 'fire', 'button': 'nuke'}}]
    w.preset = {'rules': rules}
    rs, m = rs_for(rule_next={'cooling': math.inf}, rules_fired={'done'}), w.m
    return lambda: m.eval_rules(rs, 0, 1498)


def p_rules_fire(w, v, ctx):
    """A rule fires through fire_button (one contact), a second is deferred by the one-contact-per-tick budget."""
    w.buttons = {'nuke': READY, 'demon_mode': READY}
    rules = [{'id': 'first', 'when': {'wave_at_least': 1000}, 'do': {'kind': 'fire', 'button': 'nuke', 'require_ready': False}},
             {'id': 'second', 'when': {'wave_at_least': 1000}, 'repeat': True, 'do': {'kind': 'fire', 'button': 'demon_mode', 'require_ready': False}}]
    w.preset = {'rules': rules}
    rs, m = rs_for(), w.m
    return lambda: m.eval_rules(rs, 0, 1498)


def p_fire_button(w, v, ctx):
    w.buttons = {'demon_mode': READY}
    w.preset = {}
    m = w.m
    return lambda: m.fire_button(0, 'demon_mode', 'gate', require_ready=False)


PATHS = {p.name: p for p in (
    PathSpec('fast_idle_samples', 30, False, False, p_fast_idle),
    PathSpec('fast_checkpoint_sample', 10, False, True, p_fast_checkpoint),
    PathSpec('fast_fleet_fire', 8, True, True, p_fast_fleet),
    PathSpec('fast_burst_with_sprint_cancel', 8, True, False, p_fast_burst),
    PathSpec('fast_burst_demon_mode_only', 6, True, False, p_fast_burst_dm_only),
    PathSpec('fast_strict_unmatched', 8, False, False, p_fast_strict),
    PathSpec('hp_rescue_fire', 6, True, False, p_hp_rescue),
    PathSpec('watch_frame_idle', 10, False, False, p_wf_idle),
    PathSpec('watch_frame_demon_mode_always', 3, True, False, p_wf_dm_always),
    PathSpec('watch_frame_fleet_fire', 4, True, False, p_wf_fleet),
    PathSpec('watch_frame_wall_watch_entry', 4, False, False, p_wf_wall_entry),
    PathSpec('rules_pass_nothing_to_do', 4, False, False, p_rules_quiet),
    PathSpec('rules_pass_fire_and_defer', 4, True, False, p_rules_fire),
    PathSpec('fire_button_with_confirmation', 3, True, False, p_fire_button),
)}


def make_workload(count, seed=SEED, only=None):
    """[(path name, variation)] drawn by weight from a seeded generator: the same list on every machine."""
    rng = random.Random(seed)
    names = [n for n in PATHS if only is None or n in only]
    weights = [PATHS[n].weight for n in names]
    return [(rng.choices(names, weights)[0], rng.randrange(1000)) for _ in range(count)]


# ------------------------------------------------------------------------------------ one measurement
def measure(world, path, variation, ctx=None):
    """(pass_ns, first_tap_ns or None, digest, result) of one call on a freshly scripted world. The script is built outside the timed region."""
    world.begin()
    call = path.prepare(world, variation, ctx)
    clock = world.clock
    t0 = clock()
    world.t_grab = t0
    result = call()
    t1 = clock()
    tap = None if world.t_first_tap is None else world.t_first_tap - world.t_decide
    digest = (tuple(world.tap_log), repr(result))
    return t1 - t0, tap, digest


@contextlib.contextmanager
def quiet_replay():
    """No sleeping, no garbage collection: what is left is the Python the code under test runs."""
    saved_sleep, was_enabled = time.sleep, gc.isenabled()
    time.sleep = lambda seconds: None
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        time.sleep = saved_sleep
        if was_enabled:
            gc.enable()


def percentile(values, q):
    """Nearest-rank percentile: the ceil(q * n)-th smallest value."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def summarize(values):
    if not values:
        return None
    ordered = sorted(values)
    return dict(n=len(ordered), min=round(ordered[0], 3), p50=round(percentile(ordered, 0.5), 3), p90=round(percentile(ordered, 0.9), 3), p99=round(percentile(ordered, 0.99), 3),
                p999=round(percentile(ordered, 0.999), 3), max=round(ordered[-1], 3), mean=round(sum(ordered) / len(ordered), 3))


def paired_replay(baseline_source, candidate_source, *, iterations=ITERATIONS, warmup=WARMUP, repeats=REPEATS, seed=SEED, only=None):
    """Replay one seeded workload through both orchestrators, back to back in alternating order, and return the distributions of the candidate-minus-baseline differences."""
    base, cand = load_orchestrator(baseline_source, '_latency_gate_baseline'), load_orchestrator(candidate_source, '_latency_gate_candidate')
    worlds = (World(base), World(cand))
    workload = make_workload(iterations + warmup, seed, only)
    pass_us = {name: [] for name in PATHS}
    tap_us = {name: [] for name in PATHS}
    mismatches = []
    with quiet_replay(), scratch_daystate():          # the replay is not metered, but a late import must not reach the machine's daily state either
        for index, (name, variation) in enumerate(workload):
            path, best = PATHS[name], {}
            for repeat in range(repeats):
                order = (0, 1) if (index + repeat) % 2 == 0 else (1, 0)
                for side in order:
                    p, t, digest = measure(worlds[side], path, variation)[:3]
                    kept = best.get(side)
                    if kept is None:
                        best[side] = [p, t, digest]
                    else:
                        if digest != kept[2]:
                            mismatches.append(dict(path=name, index=index, reason='the same script gave two different outcomes on one side'))
                        kept[0] = min(kept[0], p)
                        if t is not None:
                            kept[1] = t if kept[1] is None else min(kept[1], t)
            if best[0][2] != best[1][2]:
                mismatches.append(dict(path=name, index=index, reason='baseline and candidate did different things on the identical script', baseline=repr(best[0][2])[:200], candidate=repr(best[1][2])[:200]))
            if index < warmup:
                continue
            pass_us[name].append((best[1][0] - best[0][0]) / 1000.0)
            if best[0][1] is not None and best[1][1] is not None:
                tap_us[name].append((best[1][1] - best[0][1]) / 1000.0)
    return dict(iterations=iterations, warmup=warmup, repeats=repeats, seed=seed, pass_us=pass_us, tap_us=tap_us, behavior_mismatches=mismatches)


def evaluate(replay, policy_us=TARGET_POLICY_US, first_tap_us=TARGET_FIRST_TAP_US):
    """The verdict on a replay: overall and per path p99 against the targets, and zero behaviour mismatches (a replay that did different work on the two sides measures nothing)."""
    all_pass = [x for values in replay['pass_us'].values() for x in values]
    all_tap = [x for values in replay['tap_us'].values() for x in values]
    summary = dict(overall=dict(added_pass_us=summarize(all_pass), added_first_tap_us=summarize(all_tap)),
                   per_path={name: dict(added_pass_us=summarize(replay['pass_us'][name]), added_first_tap_us=summarize(replay['tap_us'][name])) for name in PATHS if replay['pass_us'][name]})
    failures = []
    if replay['behavior_mismatches']:
        failures.append(f"{len(replay['behavior_mismatches'])} iterations did different things on the two sides: {replay['behavior_mismatches'][0]}")
    for scope, block in [('overall', summary['overall'])] + list(summary['per_path'].items()):
        if block['added_pass_us'] and block['added_pass_us']['p99'] > policy_us:
            failures.append(f"{scope}: p99 added policy cost {block['added_pass_us']['p99']} us > {policy_us} us")
        if block['added_first_tap_us'] and block['added_first_tap_us']['p99'] > first_tap_us:
            failures.append(f"{scope}: p99 added frame-to-first-tap {block['added_first_tap_us']['p99']} us > {first_tap_us} us")
    return dict(summary, failures=failures, passed=not failures, targets=dict(policy_us=policy_us, first_tap_us=first_tap_us))


# ------------------------------------------------------------------------------------ structural counts
class Meter:
    """Counts the I/O of the calling thread while a path runs. A log event is one file open and append in production (diagnostics.write_event opens the file per event), so events and
    shots are counted as such; the fakes' own bookkeeping is not."""

    def __init__(self, world):
        self.world, self.counts, self.at_first_tap, self.real_events, self.real_shots = world, dict.fromkeys(COUNTERS, 0), None, 0, 0
        self.thread = threading.get_ident()

    def snapshot(self):
        counts = dict(self.counts)
        counts['event'] = self.real_events + len(self.world.log.events)
        counts['shot'] = self.real_shots + len(self.world.log.shots)
        return counts

    def first_tap(self):
        self.at_first_tap = self.snapshot()

    @contextlib.contextmanager
    def instrument(self):
        if _ACTIVE_CHARGES:
            raise RuntimeError('Meter.instrument does not nest: the daily-state charge goes to one pass only')
        thread, counts = self.thread, self.counts

        def counted(name, function):
            def wrapper(*args, **kwargs):
                if threading.get_ident() == thread:
                    counts[name] += 1
                return function(*args, **kwargs)
            return wrapper

        def charged(cost, function):
            def wrapper(*args, **kwargs):
                if threading.get_ident() == thread:
                    for name, amount in cost.items():
                        counts[name] += amount
                return function(*args, **kwargs)
            return wrapper

        def real_event(*args, **kwargs):
            if threading.get_ident() == thread:
                self.real_events += 1

        def real_shot(*args, **kwargs):
            if threading.get_ident() == thread:
                self.real_shots += 1
            return None

        patched = [(sqlite3, 'connect', counted('sqlite_connect', sqlite3.connect)), (builtins, 'open', counted('file_open', io.open)), (io, 'open', counted('file_open', io.open))]
        try:
            import cv2
            patched.append((cv2, 'imwrite', counted('image_write', cv2.imwrite)))
        except ImportError:
            pass
        try:
            from runtime import file_lock
            patched += [(file_lock, 'try_lock', counted('lock_acquire', file_lock.try_lock)), (file_lock, 'lock', counted('lock_acquire', file_lock.lock))]
        except ImportError:
            pass                                    # before the daily-state lock landed there is no file lock to count
        fake = getattr(getattr(self.world, 'm', None), '_fakes', {}).get('daystate')          # the daily state of the orchestrator under test: a fake that opens nothing
        if fake is not None:
            charge_daystate_fake(fake)          # a no-op for a loaded orchestrator's fake (wrapped at load); wraps a fake built by hand
        ambient = sys.modules.get('scheduling.global_rewards')          # what a LATE import of global_rewards finds is whatever an earlier test left here: during the pass it reaches this fake too
        if fake is not None and getattr(ambient, 'daystate', None) is not None:
            patched.append((ambient, 'daystate', fake))
        from runtime import logger as real_logger
        patched += [(real_logger, 'event', real_event), (real_logger, 'shot', real_shot)]
        with scratch_daystate() as daystate:
            if daystate is not None and hasattr(daystate, '_locked'):
                patched.append((daystate, '_locked', counted('lock_acquire', daystate._locked)))
            originals = [(target, name, getattr(target, name)) for target, name, _ in patched]
            for target, name, replacement in patched:
                setattr(target, name, replacement)
            _ACTIVE_CHARGES.append((thread, counts))
            try:
                yield self
            finally:
                _ACTIVE_CHARGES.pop()
                for target, name, original in originals:
                    setattr(target, name, original)


class RealSessions:
    """The structural pass runs the REAL BattleSession over a temporary ledger (so the eighth-sample checkpoint's own I/O is in the counts)."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.counter = 0

    def close(self):
        self.directory.cleanup()

    def session(self, world, preset):
        from flows.shard import GemWatch
        from interactions.shopper import Shopper
        from runtime.battle_session import BattleSession
        from towerpilot_workflows.battles import Battles
        from towerpilot_workflows.operations import Operations
        self.counter += 1
        path = Path(self.directory.name) / f'operations{self.counter}.db'
        context = dict(device_key='device', account='account', instance='main', serial='fake')
        operation, _ = Operations(path).accept('farm', context, {})
        body = dict(gather={'ad_gems': True, 'quests_8h': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
        from runtime import logger as real_logger
        original, real_logger.event = real_logger.event, (lambda *args, **kwargs: None)
        try:
            session = BattleSession(Battles(path), operation['id'], body, SCHEDULE_ONLY)
            fields = dict(gems=GemWatch(body['gather']), shop=Shopper(body), mission=SimpleNamespace(active=False), shopping_enabled=False, rewards_enabled=True,
                          repeat_enabled=True, last_shop=0)
            session.observe(SimpleNamespace(tracker=SimpleNamespace(last=123), **fields), 123)
        finally:
            real_logger.event = original

        def with_session_fields(rs):
            for key, value in fields.items():
                setattr(rs, key, value)
            return rs
        return session, with_session_fields


def structural_counts(source=CANDIDATE_PATH):
    """{path: {first_tap: bool, before_first_tap: counts or None, total: counts}} for one pass of every path of `source`."""
    module = load_orchestrator(source, '_latency_gate_structural')
    world, sessions, out = World(module), RealSessions(), {}
    try:
        with quiet_replay():
            for name, path in PATHS.items():
                world.begin()
                meter = Meter(world)
                call = path.prepare(world, 0, sessions if path.session else None)
                world.on_first_tap = meter.first_tap
                with meter.instrument():
                    world.t_grab = world.clock()
                    call()
                    total = meter.snapshot()
                out[name] = dict(first_tap=meter.at_first_tap is not None, before_first_tap=meter.at_first_tap, total=total)
    finally:
        sessions.close()
        module._battle_session = None
    return out


def structural_failures(counts, baseline):
    """Every counter of every path must be <= the recorded baseline, up to the first tap and in total. [] = the gate passes."""
    failures = []
    for name, recorded in baseline['paths'].items():
        got = counts.get(name)
        if got is None:
            failures.append(f'{name}: the path was not measured')
            continue
        if got['first_tap'] != recorded['first_tap']:
            failures.append(f"{name}: tapped={got['first_tap']} but the baseline tapped={recorded['first_tap']}")
        for scope in ('before_first_tap', 'total'):
            if recorded[scope] is None:
                continue
            for counter in COUNTERS:
                have = (got[scope] or {}).get(counter, 0)
                if have > recorded[scope][counter]:
                    failures.append(f'{name}: {scope} {counter} {have} > baseline {recorded[scope][counter]}')
    return failures


def record_baseline(source=SNAPSHOT_PATH):
    """The structural baseline, from the pinned unmigrated snapshot. An explicit developer command (see the tests); regenerating it after a migration would erase the proof."""
    return dict(version=1, captured_on=dict(snapshot_sha256=sha256(source), snapshot=str(Path(source).name), functions=function_hashes(source),
                                            note='counts of one pass of each path of the pinned unmigrated orchestrator, under the fakes of _fakes.py, with the real BattleSession for the checkpoint-bearing paths'),
                counters=list(COUNTERS), paths=structural_counts(source))


def run_gate(candidate=CANDIDATE_PATH, snapshot=SNAPSHOT_PATH, *, iterations=ITERATIONS, warmup=WARMUP, seeds=(SEED, SEED + 1), only=None):
    """The timing gate. One replay decides; a replay that misses a target is repeated once on another seed and the gate fails only if the repeat misses too (a transient load on a shared
    machine is not a regression, a regression repeats). A replay in which the two sides did different things fails at once: it measured nothing."""
    attempts = []
    for seed in seeds:
        replay = paired_replay(snapshot, candidate, iterations=iterations, warmup=warmup, seed=seed, only=only)
        verdict = evaluate(replay)
        attempts.append(dict(seed=seed, replay=replay, verdict=verdict))
        if verdict['passed'] or replay['behavior_mismatches']:
            break
    last = attempts[-1]
    return dict(passed=last['verdict']['passed'], attempts=attempts, final=last, repeated=len(attempts) > 1)


def environment():
    return dict(python=sys.version.split()[0], implementation=platform.python_implementation(), platform=platform.platform(), processor=platform.processor(), cpu_count=os.cpu_count(),
                clock=time.get_clock_info('perf_counter').implementation, clock_resolution_ns=int(time.get_clock_info('perf_counter').resolution * 1e9))


def artifact(gate, structural, baseline, candidate=CANDIDATE_PATH, snapshot=SNAPSHOT_PATH):
    """The record kept of a gate run: source and build, hardware and runtime, warmup, repeats, the distributions and the verdicts."""
    final = gate['final']
    replay, verdict = final['replay'], final['verdict']
    candidate_functions = function_hashes(candidate)
    return dict(
        version=1, targets=verdict['targets'], environment=environment(),
        sources=dict(candidate=dict(path=str(Path(candidate).relative_to(BACKEND.parent)).replace(os.sep, '/') if Path(candidate).is_relative_to(BACKEND.parent) else str(candidate),
                                    sha256=sha256(candidate), fast_functions=candidate_functions),
                     unmigrated_snapshot=dict(path='backend/tests/fixtures/latency_gate/orchestrator_unmigrated.py.txt', sha256=sha256(snapshot), fast_functions=function_hashes(snapshot)),
                     harness=dict(path='backend/tests/latency_gate.py', sha256=sha256(test_paths.TESTS / 'support' / 'latency_gate.py')), fakes=dict(path='backend/tests/_fakes.py', sha256=sha256(test_paths.TESTS / 'support' / '_fakes.py'))),
        migrated_fast_functions=sorted(name for name, digest in candidate_functions.items() if digest != baseline['captured_on']['functions'].get(name)),
        method=dict(iterations=replay['iterations'], warmup_dropped=replay['warmup'], repeats_min_of=replay['repeats'], seed=replay['seed'], gc='disabled during the replay', sleep='no-op',
                    order='baseline and candidate alternate which goes first by iteration and repeat',
                    structural_accounting=dict(measured='sqlite3.connect, builtins.open and io.open, runtime.file_lock, cv2.imwrite, runtime.logger and the real daily state when a late import reaches one',
                                               charged_by_rule=dict(subject='the fake daily state of _fakes.py (opens nothing)', cost=DAYSTATE_COST, reads=list(DAYSTATE_READS),
                                                                    pinned_against='scheduling/daystate.py by test_a_fake_daily_state_is_charged_what_the_real_one_registers'),
                                               timing_replay='runs the fake daily state unmetered: a charge is a counting rule, not a timing proof'),
                    paths={name: dict(weight=PATHS[name].weight, taps=PATHS[name].has_tap, real_checkpoint_in_structural_pass=PATHS[name].session) for name in PATHS}),
        timing=dict(passed=gate['passed'], repeated_on_another_seed=gate['repeated'], attempts=[dict(seed=a['seed'], passed=a['verdict']['passed'], failures=a['verdict']['failures']) for a in gate['attempts']],
                    overall=verdict['overall'], per_path=verdict['per_path'], behavior_mismatches=len(replay['behavior_mismatches'])),
        structural=dict(passed=not structural_failures(structural, baseline), failures=structural_failures(structural, baseline), counts=structural))
