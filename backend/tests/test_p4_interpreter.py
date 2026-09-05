"""P4: the main-loop Tier B rule interpreter in orchestrator.py.

Same fakes as `test_p3_runtime.py` - every screen-touching module is replaced,
so nothing here captures, taps or talks to adb - but pointed at THE LIVE
orchestrator.py IN THIS TREE, not at `patches/scratch/`.

That distinction is load-bearing. apply_p3.py's hunk #24 anchors on `def
_fast_wall_watch(...)`, which still occurs exactly once after the P3 apply, so
re-running it prepends a SECOND copy of the P3 rule evaluator to the scratch
file. Python takes the last definition, so a scratch-based test of `eval_rules`
silently exercises the P3 block no matter what this tree's orchestrator.py says. The
P4 interpreter therefore has to be tested where it actually runs.

What these tests are really asserting, group by group:
  1. every trigger in the Tier B vocabulary is EVALUATED, against synthetic rs
     and frame state - and the ones that belong to another tier or another
     phase are retired and logged, never silently skipped;
  2. the interpreter reads BOTH compiled shapes - P3's raw schema block and the
     compiler's normalized `kind` spec - because the two halves of P4 do not
     have to land in the same commit;
  3. the cooldown, the one-action-per-tick budget and the per-rule state keys
     behave as documented;
  4. A TOURNAMENT RUN CANNOT BE ABORTED BY A RULE. That one is not a
     behaviour test, it is the hard rule: three independent locks, each proven
     to refuse on its own, with zero taps and zero calls into the abandon flow.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fakes import (_assert_policies_are_hoisted,              # noqa: E402
                    patch_module,
                    _patch_flows_shard,
                    _config_reads_in_watch_loop, _f,
                    _install_fakes, _rs,
                    install_modules, restore_modules)

_MISSING = object()


def _import_live(modname: str):
    """Import THIS TREE's <modname>.py under a private name, with every
    screen-touching dependency faked. Mirrors test_p3_runtime._import_scratch
    except for the source path - see the module docstring for why that matters.
    """
    rec_taps: list = []
    mods, log, lo = _install_fakes(rec_taps)
    key = f"_live_{modname}"
    saved_live = sys.modules.get(key)
    saved = install_modules(mods)      # bare + dotted + package attributes
    try:
        from _fakes import _PKG
        rel = (f"{_PKG[modname]}/{modname}.py" if modname in _PKG
               else f"{modname}.py")
        spec = importlib.util.spec_from_file_location(key, ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    finally:
        restore_modules(saved)
        if saved_live is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = saved_live
    mod._log, mod._loadout, mod._taps, mod._fakes = log, lo, rec_taps, mods
    return mod


@pytest.fixture(scope="module")
def orchestrator():
    return _import_live("orchestrator")


@pytest.fixture(autouse=True)
def _config_isolation():
    """Same contract as test_p3_runtime's: this file mutates the REAL CONFIG
    (so the legacy assertions are made against the shipped config.yaml) and
    must hand it back exactly as it found it.

    By VALUE, not by key set. `tournament` is a real preset in config.yaml and
    the legacy-lock test below overwrites its BODY; popping only the names this
    file added left that clobbered body in the process-wide CONFIG for every
    test file that ran afterwards. Found in P6 by a guard test that asserts the
    real body is still intact.
    """
    import copy
    from settings import CONFIG
    before = (CONFIG.get("preset"), CONFIG.get("active_instance"),
              CONFIG.get("active_profile", _MISSING),
              copy.deepcopy(CONFIG["presets"]))
    yield
    CONFIG["preset"], CONFIG["active_instance"] = before[0], before[1]
    if before[2] is _MISSING:
        CONFIG.pop("active_profile", None)
    else:
        CONFIG["active_profile"] = before[2]
    CONFIG["presets"].clear()
    CONFIG["presets"].update(before[3])


@pytest.fixture
def rules(orchestrator):
    """Install a temporary preset carrying `rules` and clear the log."""
    from settings import CONFIG
    before, added = CONFIG["preset"], []

    def install(rule_list, **extra):
        name = f"bp_p4_{len(added)}"
        CONFIG["presets"][name] = {"rules": rule_list, **extra}
        CONFIG["preset"] = name
        added.append(name)
        orchestrator._log.events.clear()
        orchestrator._log.shots.clear()
        orchestrator._taps.clear()
        return name
    yield install
    for n in added:
        CONFIG["presets"].pop(n, None)
    CONFIG["preset"] = before


def _fires(orchestrator, monkeypatch):
    """Record every fire_button call instead of tapping."""
    seen = []

    def fake(frame, name, why, require_ready=True):
        seen.append((name, why, require_ready))
        return True
    monkeypatch.setattr(orchestrator, "fire_button", fake)
    return seen


def _ev(orchestrator, name):
    return [kw for n, kw in orchestrator._log.events if n == name]


# ------------------------------------------------------------ trigger: waves

def test_wave_at_least_reads_both_compiled_shapes(orchestrator, monkeypatch, rules):
    """P3 emitted the raw schema block; the compiler emits {"kind": ...,
    "wave": N}. The interpreter must read both, or the runtime half of P4 can
    only ship in lockstep with the compiler half."""
    for when in ({"wave_at_least": 4000},
                 {"kind": "wave_at_least", "wave": 4000}):
        rules([{"when": when, "do": {"kind": "fire", "button": "nuke",
                                     "require_ready": False}}])
        fired = _fires(orchestrator, monkeypatch)
        rs = _rs()
        orchestrator.eval_rules(rs, "FRAME", 3999)
        assert fired == [], when
        orchestrator.eval_rules(rs, "FRAME", 4000)
        assert fired == [("nuke", "rule0", False)], when


def test_wave_between_is_a_window_not_a_threshold(orchestrator, monkeypatch, rules):
    rules([{"when": {"wave_between": [1000, 2000]}, "repeat": True,
            "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    rs = _rs()
    for w in (999, 1000, 1500, 2000, 2001):
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", w)
    assert len(rf.__dict__["requests"]) == 3      # 1000, 1500, 2000


def test_wave_window_shapes(orchestrator):
    """Every shape a compiler might reasonably emit resolves to the same
    window; anything short of two numbers is not a window at all."""
    assert orchestrator._wave_window({"value": [10, 20]}) == (10.0, 20.0)
    assert orchestrator._wave_window({"from": 10, "to": 20}) == (10.0, 20.0)
    assert orchestrator._wave_window({"min": 10, "max": 20}) == (10.0, 20.0)
    assert orchestrator._wave_window({"at_least": 10, "below": 20}) == (10.0, 20.0)
    assert orchestrator._wave_window({"from": 10}) is None
    assert orchestrator._wave_window({"value": "soon"}) is None


# -------------------------------------------------------------- trigger: bars

def test_bar_hp_is_a_plain_threshold_by_default(orchestrator, monkeypatch, rules):
    """No falling_samples -> the level alone decides, which is exactly what the
    P3 evaluator did for `bar: hp`."""
    rules([{"when": {"bar": "hp", "below": 0.3},
            "do": {"cancel_sprint": True}}])
    ended = []
    monkeypatch.setattr(orchestrator, "end_intro_sprint",
                        lambda rs, why: ended.append(why) or True)
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.5)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert ended == []
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.29)
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert ended == ["rule0"]


def test_bar_wall_honours_falling_samples_and_deadband(orchestrator, monkeypatch,
                                                       rules):
    """DIRECTION IS THE TRIGGER, not the level - the Tier A rule, expressed at
    Tier B. A wall sitting below the threshold but not falling must not fire,
    and jitter inside the deadband is not a fall."""
    rules([{"when": {"kind": "bar", "bar": "wall", "below": 0.5,
                     "falling_samples": 2, "deadband": 0.01},
            "repeat": True, "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    seq = [0.40, 0.399, 0.398,      # jitter inside the deadband: not falling
           0.30, 0.20]              # two real falls -> fires on the second
    det = _f(orchestrator, "detect")
    rs = _rs()
    for v in seq:
        monkeypatch.setattr(det, "wall_overheal", lambda frame, v=v:
                            (v, "normal"))
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", 100)
    assert rf.__dict__["requests"] == ["rule0"]


def test_a_compiled_bar_rule_must_state_every_number(orchestrator, monkeypatch,
                                                     rules):
    """Codex P4 #4. The compiler defaulted falling_samples to 1 while the
    runtime defaulted it to 0, and the drift was invisible until a rescue did
    not happen: a compiled hp rule sat under its threshold for three passes
    without firing while its raw-shaped twin fired at once. A normalized rule
    that omits a bar number is a COMPILER BUG - refused, not defaulted."""
    for missing in ("falling_samples", "deadband"):
        when = {"kind": "bar", "bar": "hp", "below": 0.3,
                "falling_samples": 0, "deadband": 0.0}
        when.pop(missing)
        rules([{"when": when, "do": {"stop_after_run": True}}])
        monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.1)
        rf = _f(orchestrator, "runflag")
        rf.__dict__["requests"] = []
        orchestrator.eval_rules(_rs(), "FRAME", 100)
        why = _ev(orchestrator, "rule_unsupported")[0]["why"]
        assert missing in why and "no default" in why
        assert rf.__dict__["requests"] == []


def test_a_compiled_plain_threshold_fires_on_the_first_sample(orchestrator,
                                                              monkeypatch,
                                                              rules):
    """The agreed spelling of "level alone decides": falling_samples 0,
    deadband 0.0 - and it must behave EXACTLY like the raw P3 shape, which is
    the comparison the drift broke."""
    for when in ({"kind": "bar", "bar": "hp", "below": 0.3,
                  "falling_samples": 0, "deadband": 0.0},
                 {"bar": "hp", "below": 0.3}):        # the raw P3 shape
        rules([{"when": when, "do": {"stop_after_run": True}}])
        monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.1)
        rf = _f(orchestrator, "runflag")
        rf.__dict__["requests"] = []
        orchestrator.eval_rules(_rs(), "FRAME", 100)
        assert rf.__dict__["requests"] == ["rule0"], when   # FIRST pass


def test_bar_immune_reading_is_not_a_wall_reading(orchestrator, monkeypatch, rules):
    """During a Second Wind the wall ROI shows the pink immunity countdown.
    Treating that as a wall value fires the rule on every single proc."""
    rules([{"when": {"bar": "wall", "below": 0.9}, "repeat": True,
            "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    monkeypatch.setattr(_f(orchestrator, "detect"), "wall_overheal",
                        lambda frame: (0.0, "immune"))
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert rf.__dict__["requests"] == []


def test_a_proc_wipes_the_falling_history(orchestrator, monkeypatch, rules):
    """Codex P4 #7, reproduced by the audit: a fall recorded BEFORE a Second
    Wind paired with the post-proc rebuild reads as a fresh drain and fires the
    rescue on a wall that is coming back on its own. The wall regrows from zero
    after every proc, so the history has to die with the proc."""
    rules([{"when": {"kind": "bar", "bar": "wall", "below": 0.5,
                     "falling_samples": 1, "deadband": 0.01},
            "repeat": True, "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    det = _f(orchestrator, "detect")
    rs = _rs()
    #        pre-proc fall            proc          harmless rebuild
    for v, st in [(0.90, "normal"), (0.80, "normal"),
                  (0.00, "immune"), (0.10, "normal"), (0.20, "normal")]:
        monkeypatch.setattr(det, "wall_overheal", lambda frame, v=v, st=st:
                            (v, st))
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", 100)
    assert rf.__dict__["requests"] == []


def test_bar_rebuilding_is_below_every_threshold(orchestrator, monkeypatch, rules):
    """A broken wall shows the 'Rebuilding' banner instead of a value. It is
    the one state a slow sample can never misread, so it triggers outright."""
    rules([{"when": {"bar": "wall", "below": 0.02},
            "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    monkeypatch.setattr(_f(orchestrator, "detect"), "wall_overheal",
                        lambda frame: (0.0, "rebuilding"))
    orchestrator.eval_rules(_rs(), "FRAME", 100)
    assert rf.__dict__["requests"] == ["rule0"]


def test_wall_collapse_needs_a_fat_wall_first(orchestrator, monkeypatch, rules):
    """from_above is about the PREVIOUS sample: a wall that was already thin
    did not collapse, it drained."""
    det = _f(orchestrator, "detect")

    def drive(seq):
        rules([{"when": {"kind": "wall_collapse", "from_above": 0.3},
                "do": {"stop_after_run": True}}])
        rf = _f(orchestrator, "runflag")
        rf.__dict__["requests"] = []
        rs = _rs()
        for v, st in seq:
            monkeypatch.setattr(det, "wall_overheal",
                                lambda frame, v=v, st=st: (v, st))
            rs.rule_next.clear()
            orchestrator.eval_rules(rs, "FRAME", 100)
        return rf.__dict__["requests"]

    assert drive([(0.9, "normal"), (0.0, "rebuilding")]) == ["rule0"]
    assert drive([(0.1, "normal"), (0.0, "rebuilding")]) == []
    assert drive([(0.0, "rebuilding")]) == []      # no previous sample at all


# ------------------------------------------------------- trigger: fleet marks

def test_fleet_mark_fires_once_per_mark_and_shares_the_ledger(orchestrator, monkeypatch,
                                                              rules):
    """The Tier B fleet rule retires the mark in rs.nuked_marks too, so the
    Tier A schedule in watch_frame can never nuke the same mark again."""
    rules([{"when": {"kind": "fleet_mark", "after_waves": 3,
                     "window_waves": 60}, "repeat": True,
            "do": {"kind": "fire", "button": "nuke"}}])
    fired = _fires(orchestrator, monkeypatch)
    rs = _rs()
    for w in (2400, 2497, 2498, 2500):      # first mark is 2495
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", w)
    assert len(fired) == 1                  # 2498 = 2495 + 3
    assert 2495 in rs.nuked_marks


def test_fleet_mark_past_the_window_is_retired_never_fired_late(orchestrator,
                                                                monkeypatch,
                                                                rules):
    rules([{"when": {"fleet_mark": {"after_waves": 1, "window_waves": 60}},
            "repeat": True, "do": {"fire": {"button": "nuke"}}}])
    fired = _fires(orchestrator, monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 2600)     # 2495 + 60 is long gone
    assert fired == []


# -------------------------------------------------------- trigger: rs state

def test_second_wind_states_read_the_run_state(orchestrator, monkeypatch, rules):
    """No new detection: the proc counter and the badge flag are what
    watch_frame already maintains on the RunState."""
    cases = [
        ({"state": "open"}, dict(sw_proc_count=1, sw_floater_seen=True), True),
        ({"state": "open"}, dict(sw_proc_count=1, sw_floater_seen=False),
         False),
        ({"state": "closed"}, dict(sw_proc_count=2, sw_floater_seen=False),
         True),
        ({"state": "any"}, dict(sw_proc_count=0, sw_floater_seen=False),
         False),
        ({"state": "any", "min_procs": 3}, dict(sw_proc_count=2), False),
        ({"state": "any", "min_procs": 3}, dict(sw_proc_count=3), True),
    ]
    for params, state, want in cases:
        rules([{"when": {"kind": "second_wind", **params},
                "do": {"stop_after_run": True}}])
        rf = _f(orchestrator, "runflag")
        rf.__dict__["requests"] = []
        orchestrator.eval_rules(_rs(**state), "FRAME", 100)
        assert bool(rf.__dict__["requests"]) is want, (params, state)


def test_second_wind_after_immunity_waits_for_the_window(orchestrator, rules):
    import time
    rules([{"when": {"second_wind": {"state": "after_immunity"}},
            "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    rs = _rs(sw_proc_count=1, sw_floater_seen=False,
             sw_immune_until=time.monotonic() + 60)
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert rf.__dict__["requests"] == []
    rs.sw_immune_until = 0.0
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert rf.__dict__["requests"] == ["rule0"]


# --------------------------------------------- retired: wrong tier, wrong phase

@pytest.mark.parametrize("rule,fragment", [
    ({"when": {"death_screen": True}, "do": {"surrender_retry": True}},
     "death handler"),
    ({"when": {"bar": "hp"}, "do": {"fire": {"button": "nuke"}}},
     "no `below` threshold"),
    ({"when": {"bar": "hp", "below": 0.3}, "do": {"fire": {"button": "wall"}}},
     "unknown button"),
    ({"when": {"wave_at_least": 10}, "do": {"switch_cards": {}}},
     "card preset name"),
    ({"when": {"wave_at_least": 10}, "do": {"toggle_uw": {}}},
     "weapon name"),
    ({"when": {"wall_collapse": {}}, "do": {"stop_after_run": True}},
     "from_above"),
    ({"when": {"second_wind": {"state": "sideways"}},
      "do": {"stop_after_run": True}}, "unknown second_wind state"),
    ({"when": {"nonsense": 1}, "do": {"stop_after_run": True}},
     "no known trigger"),
    ({"when": {"wave_at_least": 10}, "do": {"nonsense": True}},
     "no known action"),
])
def test_unrunnable_rules_are_retired_on_sight_and_logged(orchestrator, monkeypatch,
                                                          rules, rule,
                                                          fragment):
    """ADMISSION BEFORE EVALUATION. A rule this loop can never run is retired
    the first time it is SEEN - not the first time its trigger happens to be
    true - and it says why. Silence here is the exact failure mode profiles
    exist to abolish."""
    rules([rule])
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda *a, **k: pytest.fail("must not act"))
    monkeypatch.setattr(orchestrator, "_rule_act",
                        lambda *a, **k: pytest.fail("must not act"))
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    unsupported = _ev(orchestrator, "rule_unsupported")
    assert len(unsupported) == 1
    assert fragment in unsupported[0]["why"], unsupported[0]["why"]
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert len(_ev(orchestrator, "rule_unsupported")) == 1     # retired, not spam


def test_repeat_does_not_resurrect_an_unsupported_rule(orchestrator, monkeypatch,
                                                       rules):
    """Codex P4 #8. `rules_fired` means "done for now" and `repeat: true`
    overrides it - so an unsupported rule marked `repeat` re-announced itself
    on every single pass, ~1.4 times a second. RETIRED is a separate state that
    nothing overrides."""
    rules([{"when": {"nonsense": 1}, "repeat": True,
            "do": {"stop_after_run": True}}])
    rs = _rs()
    for _ in range(10):
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", 100)
    assert len(_ev(orchestrator, "rule_unsupported")) == 1


def test_a_death_rule_is_skipped_by_the_observe_loop_not_retired(orchestrator, rules):
    """A properly compiled death rule belongs to the other phase. The observe
    loop must leave it alone - skipping it in silence, with nothing logged and
    nothing retired, so the death handler still finds it armed."""
    rules([{"id": "rescue#2", "latency": "death_handler",
            "when": {"kind": "death_screen"},
            "do": {"kind": "stop_after_run"}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert orchestrator._log.events == [] and rf.__dict__["requests"] == []
    assert rs.rules_fired == set()
    # ...and the death handler runs it
    orchestrator.run_death_rules(rs, "FRAME")
    assert rf.__dict__["requests"] == ["rescue#2"]
    assert rs.rules_fired == {"rescue#2"}


def test_the_death_screen_refuses_every_navigating_action(orchestrator, monkeypatch,
                                                          rules):
    """Codex P4 #3. stop_after_run is the ONLY death-phase action, because it
    is the only one that touches nothing.

    fire/burst/cancel_sprint were never candidates - the stats dialog has no
    ability row, no sprint and no wall. switch_cards and surrender_retry looked
    like candidates and are not: loadout.apply_cards navigates FROM HOME, and
    the only verified way off the stats dialog is restart_from_home's
    HOME-then-poll sequence, which does not stop at Home - it ends in a running
    battle, leaving the handler nowhere to resume from. shard.abandon_run
    surrenders a LIVE battle and would hunt for an EXIT BATTLE button that does
    not exist here. So both are refused, out loud, instead of tapping."""
    for do in ({"kind": "fire", "button": "nuke"},
               {"kind": "burst", "button": "demon_mode"},
               {"kind": "cancel_sprint"},
               {"kind": "switch_cards", "preset": "disco"},
               {"kind": "surrender_retry"}):
        rules([{"id": "p#0", "latency": "death_handler",
                "when": {"kind": "death_screen"}, "do": do}])
        for name in ("fire_button", "end_intro_sprint", "_rule_switch_cards",
                     "_rule_surrender"):
            monkeypatch.setattr(orchestrator, name,
                                lambda *a, **k: pytest.fail("must not tap"))
        _patch_flows_shard(monkeypatch, types.ModuleType("shard"))
        assert orchestrator.run_death_rules(_rs(), "FRAME") is False
        why = _ev(orchestrator, "rule_unsupported")[0]["why"]
        assert "death screen" in why and orchestrator._taps == []
        assert orchestrator._loadout.calls == []


# --------------------------------------------------------------- cooldowns

def test_refire_sec_is_the_compilers_field(orchestrator, monkeypatch, rules):
    """One cooldown, ranked once by the compiler into `refire_sec` - the
    runtime does not re-derive it from three spellings."""
    rules([{"when": {"wave_at_least": 1}, "repeat": True, "refire_sec": 30.0,
            "do": {"kind": "fire", "button": "nuke"}}])
    fired = _fires(orchestrator, monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 10)
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert len(fired) == 1
    assert rs.rule_next[0] > 0
    rs.rule_next[0] = 0.0
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert len(fired) == 2


@pytest.mark.parametrize("value", [float("nan"), float("inf"),
                                   float("-inf")])
def test_a_non_finite_cooldown_is_refused(orchestrator, monkeypatch, rules, value):
    """NaN loses every comparison, so it disables the guard entirely; infinity
    wins every one, so it suppresses the rule forever. Both are silent, which
    is what makes them worth refusing rather than clamping."""
    rules([{"when": {"wave_at_least": 1}, "repeat": True,
            "refire_sec": value, "do": {"stop_after_run": True}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    orchestrator.eval_rules(_rs(), "FRAME", 10)
    assert rf.__dict__["requests"] == []
    assert "finite" in _ev(orchestrator, "rule_unsupported")[0]["why"]


def test_cooldown_precedence_and_a_compiled_zero(orchestrator):
    """`is not None`, never `or`: a compiled 0 means NO floor, and silently
    restoring the 5s default instead is the bug NEW#6 caught in the fast
    watch."""
    assert orchestrator._rule_cooldown({"refire_sec": 30}, {"throttle_sec": 5}) == 30
    assert orchestrator._rule_cooldown({}, {"throttle_sec": 7}) == 7
    assert orchestrator._rule_cooldown({}, {"refire_guard_sec": 9}) == 9
    assert orchestrator._rule_cooldown({}, {}) == orchestrator.RULE_REFIRE_SEC
    assert orchestrator._rule_cooldown({"refire_sec": 0}, {}) == 0.0


def test_a_rule_on_cooldown_logs_once_not_once_per_pass(orchestrator, monkeypatch,
                                                        rules):
    """~1.4 passes a second: a suppressed line per pass is a log nobody reads,
    which is the same as not logging at all."""
    rules([{"when": {"wave_at_least": 1}, "repeat": True, "refire_sec": 900,
            "do": {"kind": "fire", "button": "nuke"}}])
    _fires(orchestrator, monkeypatch)
    rs = _rs()
    for _ in range(20):
        orchestrator.eval_rules(rs, "FRAME", 10)
    assert len(_ev(orchestrator, "rule_suppressed")) == 1
    assert _ev(orchestrator, "rule_suppressed")[0]["why"] == "cooldown"
    assert _ev(orchestrator, "rule_suppressed")[0]["level"] == "debug"


# ------------------------------------------------- one action per loop tick

def test_only_one_screen_touching_action_per_tick(orchestrator, monkeypatch, rules):
    """Two acting rules, both triggered: the second is DEFERRED, not dropped -
    no cooldown stamp, no retirement - so the next pass judges it against a
    fresh frame instead of one captured before the first action moved the
    screen."""
    rules([{"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"kind": "fire", "button": "nuke"}},
           {"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"kind": "fire", "button": "demon_mode"}}])
    fired = _fires(orchestrator, monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert [f[0] for f in fired] == ["nuke"]
    assert 1 not in rs.rule_next               # deferred, not stamped
    deferred = _ev(orchestrator, "rule_suppressed")
    assert deferred and deferred[0]["why"] == "one_action_per_tick"
    rs.rule_next.clear()
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert [f[0] for f in fired] == ["nuke", "nuke"]


def test_a_non_tapping_action_does_not_spend_the_budget(orchestrator, monkeypatch,
                                                        rules):
    """stop_after_run writes a flag file and moves nothing on screen, so it
    cannot be what stops a real action from happening in the same pass.

    (P6: this used to pair stop_after_run with switch_cards. switch_cards is
    now retired at admission - see the P6 note on _rule_admits_action - so the
    partner is toggle_uw, which is what the test was always about: the budget,
    not the card screen.)
    """
    rules([{"when": {"wave_at_least": 1}, "do": {"stop_after_run": True}},
           {"when": {"wave_at_least": 1},
            "do": {"toggle_uw": {"weapon": "black_hole", "state": "on"}}}])
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    uw = []
    monkeypatch.setattr(orchestrator.shopper, "uw_toggle",
                        lambda w, on: uw.append((w, on)) or True)
    orchestrator.eval_rules(_rs(), "FRAME", 10)
    assert rf.__dict__["requests"] == ["rule0"]
    assert uw == [("black_hole", True)]


def test_the_budget_is_spent_on_contact_not_on_success(orchestrator, monkeypatch,
                                                       rules):
    """Codex P4 #6. A fire that TAPPED and did not confirm has still moved the
    game, so the next rule's frame is stale - and acting on a stale frame is
    the blind tap the hard rules exist to prevent. The budget is spent on
    contact, whatever the outcome."""
    rules([{"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"kind": "fire", "button": "nuke"}},
           {"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"kind": "fire", "button": "demon_mode"}}])
    fired = []

    def fake(frame, name, why, require_ready=True):
        fired.append(name)
        return False                        # tapped, never confirmed
    monkeypatch.setattr(orchestrator, "fire_button", fake)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert fired == ["nuke"]                # the second rule waits a pass
    assert rs.rules_fired == set()          # neither one succeeded
    ev = [kw for n, kw in orchestrator._log.events if n == "rule_fire"]
    assert ev[0]["touched"] is True and ev[0]["result"] is False


def test_a_rule_that_only_evaluated_spends_nothing(orchestrator, monkeypatch, rules):
    """The property that must survive the change: a rule whose trigger was
    false, or whose action short-circuited without touching anything, leaves
    the budget for the rules below it."""
    rules([{"when": {"wave_at_least": 9999}, "repeat": True,
            "do": {"kind": "fire", "button": "nuke"}},
           {"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"stop_after_run": True}},
           {"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"kind": "fire", "button": "demon_mode"}}])
    fired = _fires(orchestrator, monkeypatch)
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    orchestrator.eval_rules(_rs(), "FRAME", 10)
    assert rf.__dict__["requests"] == ["rule1"]     # no contact
    assert [f[0] for f in fired] == ["demon_mode"]  # ...so rule 2 still ran


# ---------------------------------------- THE ABSOLUTE RULE: no tournament abort

def _no_abandon(monkeypatch):
    """A shard stand-in whose abandon_run FAILS THE TEST if it is ever
    reached. The refusal has to happen before the flow, not inside it."""
    fake = types.ModuleType("shard")
    fake.abandon_run = lambda *a, **k: pytest.fail(
        "abandon_run reached on a tournament run")
    _patch_flows_shard(monkeypatch, fake)
    return fake


@pytest.mark.parametrize("extra,in_tournament,lock", [
    ({"kind": "tournament"}, False, "blueprint kind"),
    ({"tournament_setup": True}, False, "tournament_setup"),
    ({}, True, "trophy badge on screen"),
])
def test_a_rule_can_never_abort_a_tournament_run(orchestrator, monkeypatch, rules,
                                                 extra, in_tournament, lock):
    """CLAUDE.md hard rule #2: a tournament run is NEVER cancelled - the ticket
    auto-starts the run and the gem cost escalates 10 -> 20 -> 30. THREE
    INDEPENDENT LOCKS, and this proves each one refuses ON ITS OWN: no tap, no
    call into the abandon flow, the rule retired for the rest of the run, and a
    screenshot on the record."""
    rules([{"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"surrender_retry": True}}], **extra)
    monkeypatch.setattr(_f(orchestrator, "screen"), "in_tournament",
                        lambda frame: in_tournament)
    _no_abandon(monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 10)
    refused = _ev(orchestrator, "rule_refused")
    assert len(refused) == 1, lock
    assert refused[0]["action"] == "surrender_retry"
    assert "TOURNAMENT" in refused[0]["why"]
    assert orchestrator._taps == [] and orchestrator._log.shots == ["rule_surrender_refused"]
    assert rs.rules_fired == {0}            # retired: never allowed later
    # ...and it STAYS refused - `repeat: true` and an expired cooldown must not
    # bring it back (Codex P4 #8: the refusal recurred once the guard lapsed)
    for _ in range(5):
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", 11)
    assert len(_ev(orchestrator, "rule_refused")) == 1
    assert orchestrator._taps == [] and orchestrator._log.shots == ["rule_surrender_refused"]


def test_the_legacy_tournament_preset_is_a_lock_too(orchestrator, monkeypatch, rules):
    """The legacy `tournament` preset carries no `kind`, so the preset NAME is
    its own lock - a profile is not the only way to end up in a tournament."""
    from settings import CONFIG
    rules([{"when": {"wave_at_least": 1}, "do": {"surrender_retry": True}}])
    CONFIG["presets"]["tournament"] = dict(CONFIG["presets"][CONFIG["preset"]])
    CONFIG["preset"] = "tournament"
    monkeypatch.setattr(_f(orchestrator, "screen"), "in_tournament",
                        lambda frame: False)
    _no_abandon(monkeypatch)
    orchestrator.eval_rules(_rs(), "FRAME", 10)
    assert len(_ev(orchestrator, "rule_refused")) == 1


def test_surrender_on_a_farm_run_goes_through_the_guarded_flow(orchestrator,
                                                              monkeypatch,
                                                              rules):
    """The other side of the same coin: on a coin run it DOES surrender, and it
    does so through shard.abandon_run - the one chokepoint that carries the
    tournament guard - never through taps of its own."""
    rules([{"when": {"wave_at_least": 1}, "do": {"surrender_retry": True}}],
          kind="coin")
    monkeypatch.setattr(_f(orchestrator, "screen"), "in_tournament",
                        lambda frame: False)
    called = []
    fake = types.ModuleType("shard")
    fake.abandon_run = lambda *a, **k: called.append(True)
    _patch_flows_shard(monkeypatch, fake)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert called == [True]
    assert rs.bot_left_battle is True       # the menu is OURS to clean up
    assert orchestrator._taps == []                # no coordinate taps of our own


# ----------------------------------------------------------- action plumbing

def test_toggle_uw_goes_through_the_verifying_helper(orchestrator, monkeypatch, rules):
    """shopper.uw_toggle reads the pill before AND after the tap. A coordinate
    tap on that panel is how the wrong weapon ends up off for a whole run with
    the log claiming success."""
    rules([{"when": {"wave_at_least": 1},
            "do": {"toggle_uw": {"weapon": "chain_lightning", "on": False}}}])
    seen = []
    monkeypatch.setattr(_f(orchestrator, "shopper"), "uw_toggle",
                        lambda uw, want_on=True: seen.append((uw, want_on))
                        or True)
    rs = _rs(cl_on=True)
    orchestrator.eval_rules(rs, "FRAME", 10)
    assert seen == [("chain_lightning", False)]
    assert rs.cl_on is False                # the choreography's own bookkeeping
    assert orchestrator._taps == []


def _burst_rule(rules, **over):
    body = {"kind": "burst", "button": "demon_mode", "cancel_sprint": True,
            "retaps": 3, "require_ready": False}
    body.update(over)
    return rules([{"when": {"kind": "bar", "bar": "hp", "below": 0.3,
                            "falling_samples": 0, "deadband": 0.0},
                   "do": body}])


def test_a_tier_b_burst_uses_the_confirming_fire_path(orchestrator, monkeypatch,
                                                      rules):
    """A burst that spilled out of Tier A is NOT three blind taps: at 1.4fps
    there is no sub-second race to justify the fixed-coordinate fallback, so it
    cancels the sprint, RE-CAPTURES, and fires through fire_button's confirmed
    tap - never on the frame the trigger was judged on."""
    _burst_rule(rules)
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.1)
    ended = []
    monkeypatch.setattr(orchestrator, "end_intro_sprint",
                        lambda rs, why: ended.append(why) or True)
    seen = []
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda frame, name, why, require_ready=True:
                        seen.append((frame, name, why)) or True)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert ended == ["rule0_sprint"] and rs.sprint_ended is True
    assert seen == [("FRAME", "demon_mode", "rule0_1")]   # capture.grab()'s
    assert rs.dm_fired is True              # the rescue is spent for this proc
    assert orchestrator._taps == []


def test_a_burst_aborts_when_the_sprint_will_not_cancel(orchestrator, monkeypatch,
                                                        rules):
    """Codex P4 #5. end_intro_sprint returns False AFTER tapping the indicator,
    so the confirm dialog may be half-drawn over the ability row. Firing there
    puts the ability tap into that dialog - so it stops and logs instead."""
    _burst_rule(rules)
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.1)
    monkeypatch.setattr(orchestrator, "end_intro_sprint", lambda rs, why: False)
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda *a, **k: pytest.fail("fired into a dialog"))
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert _ev(orchestrator, "rule_burst_aborted")[0]["id"] == "rule0"
    assert rs.dm_fired is False and rs.rules_fired == set()
    # ...and it counted as contact: the indicator tap already happened
    assert _ev(orchestrator, "rule_fire")[0]["touched"] is True


def test_a_burst_will_not_fire_without_a_wave_counter(orchestrator, monkeypatch,
                                                      rules):
    """The tower-on-screen proof every other tap site uses. If the recapture
    after the sprint cancel has no readable wave, we are not looking at a live
    battle and nothing is tapped."""
    _burst_rule(rules, cancel_sprint=False)
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.1)
    monkeypatch.setattr(_f(orchestrator, "wave_reader"), "read_wave",
                        lambda frame: None)
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda *a, **k: pytest.fail("tapped off-battle"))
    orchestrator.eval_rules(_rs(sprint_ended=True), "FRAME", 100)
    assert "no wave counter" in _ev(orchestrator, "rule_burst_aborted")[0]["why"]


def test_burst_retaps_are_bounded_verified_retries(orchestrator, monkeypatch, rules):
    """`retaps` was compiled and never read. It is honoured as a bounded retry
    of fire_button's WHOLE tap-and-confirm cycle, each attempt on a fresh
    frame - never a blind second tap at the same coordinate off a stale one."""
    _burst_rule(rules, cancel_sprint=False, retaps=3)
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.1)
    tries = []
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda frame, name, why, require_ready=True:
                        tries.append(why) or False)
    grabs = []
    monkeypatch.setattr(_f(orchestrator, "capture"), "grab",
                        lambda: grabs.append(1) or "FRESH")
    orchestrator.eval_rules(_rs(sprint_ended=True), "FRAME", 100)
    assert tries == ["rule0_1", "rule0_2", "rule0_3"]
    assert len(grabs) == 3          # one before the first, one per retry


# ------------------------------------------------------------ event shapes

def test_rule_fire_carries_id_trigger_snapshot_and_result(orchestrator, monkeypatch,
                                                          rules):
    """A fired rule that cannot be diagnosed from its own log line is a rule
    nobody can debug at 3am - the 5495-fleet postmortem had no coordinates at
    all. Id, the trigger reading it fired on, and what the action returned."""
    rules([{"id": "high_tier_wall#3", "latency": "main_loop",
            "when": {"kind": "bar", "bar": "hp", "below": 0.4,
                     "falling_samples": 0, "deadband": 0.0},
            "do": {"kind": "fire", "button": "nuke"}}])
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill", lambda frame: 0.12)
    _fires(orchestrator, monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 1234)
    ev = _ev(orchestrator, "rule_fire")[0]
    assert ev["id"] == "high_tier_wall#3" and ev["index"] == 0
    assert ev["latency"] == "main_loop" and ev["phase"] == "battle"
    assert ev["wave"] == 1234 and ev["action"] == "fire"
    assert ev["result"] is True and ev["ok"] is True and ev["touched"] is True
    assert ev["trigger"] == {"bar": "hp", "fill": 0.12, "state": "normal",
                             "falling": 0, "below": 0.4}
    # per-run state hangs on the COMPILED ID, not the list index
    assert rs.rules_fired == {"high_tier_wall#3"}


def test_a_failed_action_is_logged_as_a_fire_that_did_not_take(orchestrator,
                                                               monkeypatch,
                                                               rules):
    rules([{"when": {"wave_at_least": 1},
            "do": {"kind": "fire", "button": "nuke"}}])
    monkeypatch.setattr(orchestrator, "fire_button", lambda *a, **k: False)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 10)
    ev = _ev(orchestrator, "rule_fire")[0]
    assert ev["result"] is False and ev["ok"] is False
    assert rs.rules_fired == set()          # not retired: it never happened


def test_no_rules_key_is_still_a_no_op(orchestrator):
    """LEGACY, restated here because it is the one assertion that must survive
    every future extension of this file: a preset with no `rules` costs nothing
    and logs nothing."""
    from settings import CONFIG
    CONFIG["preset"] = "normal_run"
    orchestrator._log.events.clear()
    orchestrator.eval_rules(_rs(), "FRAME", 4000)
    assert orchestrator.run_death_rules(_rs(), "FRAME") is False
    assert orchestrator._log.events == []


def test_bars_are_read_at_most_once_per_pass(orchestrator, monkeypatch, rules):
    """Cheap by construction: five rules watching the hp bar cost ONE read of
    the frame, not five. The observe loop's budget is already spent on wave
    OCR, the badge match and the gem search."""
    rules([{"when": {"bar": "hp", "below": 0.01}, "repeat": True,
            "do": {"stop_after_run": True}} for _ in range(5)])
    reads = []
    monkeypatch.setattr(_f(orchestrator, "detect"), "hp_fill",
                        lambda frame: reads.append(1) or 0.9)
    orchestrator.eval_rules(_rs(), "FRAME", 100)
    assert len(reads) == 1


# ------------------------------- the P3 contract, re-pinned on the live module
#
# test_p3_runtime.py's eval_rules group runs against patches/scratch/, where
# hunk #24 re-injects the P3 evaluator over this tree's - so those tests can no
# longer tell whether THIS orchestrator.py still honours the behaviour they pin. These
# do. Every one is the P3 assertion, unchanged, against the live module.

def test_p3_wave_rule_fires_once_per_run(orchestrator, monkeypatch, rules):
    rules([{"when": {"wave_at_least": 4000},
            "do": {"fire": {"button": "nuke"}}, "latency": "main_loop"}])
    fired = _fires(orchestrator, monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 3999)
    assert fired == []
    orchestrator.eval_rules(rs, "FRAME", 4000)
    assert fired == [("nuke", "rule0", False)]   # rescue-path require_ready
    orchestrator.eval_rules(rs, "FRAME", 4200)
    assert len(fired) == 1
    assert [n for n, _ in orchestrator._log.events] == ["rule_fire"]
    assert orchestrator._log.events[0][1]["index"] == 0
    assert orchestrator._log.events[0][1]["latency"] == "main_loop"


def test_p3_repeat_refires_after_the_action_throttle(orchestrator, monkeypatch, rules):
    rules([{"when": {"wave_at_least": 10}, "repeat": True,
            "do": {"fire": {"button": "nuke", "throttle_sec": 30}}}])
    fired = _fires(orchestrator, monkeypatch)
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 20)
    orchestrator.eval_rules(rs, "FRAME", 21)
    assert len(fired) == 1                       # inside the 30s throttle
    rs.rule_next[0] = 0.0
    orchestrator.eval_rules(rs, "FRAME", 22)
    assert len(fired) == 2


def test_p3_a_wall_burst_never_runs_at_tier_b(orchestrator, monkeypatch, rules):
    """The wall rescue is Tier A's, and the schema's own compiler keeps
    `bar: wall` out of Tier B entirely. A burst block with no ability named is
    retired and logged rather than tapping anything."""
    rules([{"when": {"bar": "wall", "below": 0.02}, "do": {"burst": {}}}])
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda *a, **k: pytest.fail("must not fire"))
    rs = _rs()
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert [n for n, _ in orchestrator._log.events] == ["rule_unsupported"]
    orchestrator.eval_rules(rs, "FRAME", 100)
    assert [n for n, _ in orchestrator._log.events] == ["rule_unsupported"]


def test_switch_cards_is_retired_on_sight_not_executed(orchestrator, monkeypatch,
                                                       rules):
    """P6 SUPERSEDES THE P3/P4 BEHAVIOUR HERE, deliberately.

    This test used to assert that a failing switch_cards rule retried once and
    then disabled itself. It no longer gets that far: the Codex P6 #2 ruling
    (the in-run card schedule cannot reach the cards screen from a live battle
    without a blind tap) applies word for word to this action, which is the
    same loadout.apply_cards call from the same place - and reaches coin farms
    too, not just tournaments. So it is refused at ADMISSION, which retires it
    the first time the rule is seen rather than at whatever wave its trigger
    first fires.
    """
    rules([{"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"switch_cards": {"preset": "nope"}}}])
    monkeypatch.setattr(orchestrator._loadout, "apply_cards",
                        lambda p: pytest.fail("navigated to the cards screen "
                                              "from a live battle"))
    monkeypatch.setitem(sys.modules, "loadout", orchestrator._loadout)
    rs = _rs()
    for _ in range(4):
        rs.rule_next[0] = 0.0
        orchestrator.eval_rules(rs, "FRAME", 10)
    assert rs.rules_cards_off == set()      # never even reached the ledger
    assert rs.rule_cards_tries == {}
    said = [kw for n, kw in orchestrator._log.events if n == "rule_unsupported"]
    assert len(said) == 1                   # retired, once, with a reason
    assert "cannot run from a live battle" in said[0]["why"]


def test_a_broken_rule_does_not_silence_its_neighbour(orchestrator, monkeypatch,
                                                      rules):
    """AUDIT NEW#7, restated under the contact budget: retry/disable state is
    PER RULE. Rule 0's two failed attempts DO spend the budget - they touched
    the screen - but its own ledger disables it after the second, and from then
    on it costs no contact and rule 1 runs every pass. A broken rule buys two
    ticks, not the rest of the run.

    (P6: was written on switch_cards' retry ledger, which is unreachable now
    that the action is retired at admission - and that ledger was the only one
    of its kind, so there is no drop-in replacement. The property that SURVIVES
    is the one worth pinning, and it is the stronger half: a rule that this
    runtime refuses outright must not cost its neighbour anything, on any pass.
    Retirement is now the way a rule "breaks".)
    """
    rules([{"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"switch_cards": {"preset": "broken"}}},     # retired
           {"when": {"wave_at_least": 1}, "repeat": True,
            "do": {"toggle_uw": {"weapon": "good", "state": "on"}}}])
    done = []
    monkeypatch.setattr(orchestrator._loadout, "apply_cards",
                        lambda p: pytest.fail("the retired rule ran"))
    monkeypatch.setitem(sys.modules, "loadout", orchestrator._loadout)
    monkeypatch.setattr(orchestrator.shopper, "uw_toggle",
                        lambda w, on: done.append(w) or True)
    rs = _rs()
    for _ in range(4):
        rs.rule_next.clear()
        orchestrator.eval_rules(rs, "FRAME", 10)
    assert done == ["good"] * 4         # every pass, not two ticks and silence
    said = [kw for n, kw in orchestrator._log.events if n == "rule_unsupported"]
    assert len(said) == 1               # ...and rule 0 complained exactly once


# --------------------------------------------------------- the gating hook

def _gate(monkeypatch, fn=_MISSING):
    """Install a playerprofile stand-in exposing (or not) the gate helper."""
    pp = types.ModuleType("playerprofile")
    if fn is not _MISSING:
        pp.check_capabilities = fn
    patch_module(monkeypatch, "playerprofile", pp)
    return pp


def test_gate_refuses_when_the_profile_layer_reports_problems(orchestrator,
                                                              monkeypatch,
                                                              rules):
    """check_capabilities returns the reasons this account cannot run the
    preset. A non-empty list stops the process before the first capture - the
    cost of getting it wrong is a rescue tapping a FIXED COORDINATE for an
    ability the account does not have."""
    rules([{"when": {"wave_at_least": 1}, "do": {"stop_after_run": True}}])
    _gate(monkeypatch, lambda compiled: ["taps 'demon_mode', unowned"])
    assert orchestrator._gate_preset() is False
    assert _ev(orchestrator, "rule_gate_refused")[0]["problems"] == \
        ["taps 'demon_mode', unowned"]


def test_gate_passes_on_an_empty_problem_list(orchestrator, monkeypatch, rules):
    rules([{"when": {"wave_at_least": 1}, "do": {"stop_after_run": True}}])
    _gate(monkeypatch, lambda compiled: [])
    assert orchestrator._gate_preset() is True
    assert _ev(orchestrator, "rule_gate_ok")[0]["helper"] == "<lambda>"


def test_a_raising_gate_is_a_refusal(orchestrator, monkeypatch, rules):
    rules([{"when": {"wave_at_least": 1}, "do": {"stop_after_run": True}}])

    def boom(compiled):
        raise RuntimeError("profile is not bound")
    _gate(monkeypatch, boom)
    assert orchestrator._gate_preset() is False
    assert "not bound" in _ev(orchestrator, "rule_gate_refused")[0]["error"]


def test_an_unavailable_gate_fails_CLOSED(orchestrator, monkeypatch, rules):
    """Codex P4 #2. For a COMPILED preset there is no "probably fine": no
    helper, or a profile layer that will not import, means the process refuses
    to start rather than tapping fixed coordinates ungated."""
    rules([{"when": {"wave_at_least": 1}, "do": {"stop_after_run": True}}])
    _gate(monkeypatch)                              # no helper on it at all
    assert orchestrator._gate_preset() is False
    ev = _ev(orchestrator, "rule_gate_unavailable")
    assert len(ev) == 1 and ev[0]["tried"] == list(orchestrator.RULE_GATE_HELPERS)

    class Boom(types.ModuleType):
        def __getattr__(self, name):
            raise ImportError("playerprofile is broken")
    patch_module(monkeypatch, "playerprofile", Boom("playerprofile"))
    orchestrator._log.events.clear()
    assert orchestrator._gate_preset() is False
    assert _ev(orchestrator, "rule_gate_unavailable")


def test_every_compiled_preset_is_gated_even_with_no_rules(orchestrator, monkeypatch,
                                                           rules):
    """THE CRITICAL ONE (Codex P4 #1). All three ability-using golden
    blueprints compile to `rules: []` - their rescue lives in Tier A's
    `abilities{}` - so a gate that ran only on rule-carrying presets skipped
    exactly the Demon Mode fixed-coordinate tap it exists to stop."""
    rules([], abilities={"rescue_bar": "wall", "dm_below": 0.02})
    seen = []
    _gate(monkeypatch, lambda compiled: seen.append(compiled) or [])
    assert orchestrator.is_compiled_preset() is True
    assert orchestrator._gate_preset() is True
    assert seen[0]["abilities"]["dm_below"] == 0.02     # the WHOLE preset


@pytest.mark.parametrize("name,body,compiled", [
    ("bp_by_name", {}, True),
    ("plain", {"_source": {"profile": "acct2", "blueprint": "coin"}}, True),
    ("plain", {}, False),
])
def test_the_compiled_marker_is_the_source_stamp_or_the_bp_name(
        orchestrator, name, body, compiled):
    """Detection agreed with the compiler: compile_preset() stamps `_source` on
    every output and materialize() installs under `bp_<name>`. EITHER is proof,
    so a preset cannot slip past the gate by losing one of them."""
    from settings import CONFIG
    CONFIG["presets"][name] = body
    CONFIG["preset"] = name
    assert orchestrator.is_compiled_preset() is compiled


def test_a_legacy_preset_is_never_gated(orchestrator, monkeypatch):
    """The other half: `normal_run` carries no `_source` and no bp_ name, so it
    never reaches the profile layer - not even an import."""
    from settings import CONFIG
    CONFIG["preset"] = "normal_run"

    class Boom(types.ModuleType):
        def __getattr__(self, name):
            pytest.fail("legacy path imported the profile layer")
    patch_module(monkeypatch, "playerprofile", Boom("playerprofile"))
    assert orchestrator.is_compiled_preset() is False


# ------------------------------------------------- the fast-watch hoist guard

def test_the_live_fast_watch_body_still_touches_no_dicts():
    """Codex P4 #9. test_p3_runtime asserts this against patches/scratch/; the
    file that RUNS is this one. _fast_wall_watch races a wall drain faster than
    a main-loop pass, so every policy value it needs is hoisted before the
    `while` - one dict lookup back in the sampling body is the regression."""
    src = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    assert _config_reads_in_watch_loop(src) == [], (
        f"config reads inside the live sampling loop: "
        f"{_config_reads_in_watch_loop(src)}")
    _assert_policies_are_hoisted(src)
