"""P6: the last compiled keys, consumed.

Three features, one file:
  * coin `max_wave` - end the run at wave N through the GUARDED surrender,
    one attempt, runflag fallback, and a tournament that can never reach it;
  * coin `cancel_sprint` - end the intro sprint once, through the verifying
    helper;
  * tournament `in_run_actions` - scheduled card switches inside the run,
    in order, one per pass, one retry then disabled, tournament-only.

Plus the tourney.setup() unification: the read-only parity pass, and that the
equipping now goes through loadout.py rather than a second copy of it.

Pointed at THE LIVE orchestrator.py / tourney.py, never patches/scratch/ - apply_p3
re-applies its hunks over the live file and the last definition wins, so a
scratch-based test grades code this tree does not run (test_p4_interpreter.py
and test_p5_scheduler.py carry the same warning).
"""
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fakes import (_f, _install_fakes, _patch_flows_shard,   # noqa: E402
                    _rs, patch_module)
from test_p4_interpreter import _import_live                   # noqa: E402

_MISSING = object()


_LIVE = []          # every live module imported here, so _isolation can find
                    # them: _import_live RESTORES sys.modules on the way out,
                    # so the private "_live_<name>" key is gone by then.


@pytest.fixture(scope="module")
def orchestrator():
    m = _import_live("orchestrator")
    _LIVE.append(m)
    return m


@pytest.fixture(autouse=True)
def _isolation():
    """This file mutates the REAL CONFIG on purpose - every legacy assertion is
    made against the shipped config.yaml - so it hands it back after each test.

    Restore by VALUE, not by key set. `tournament` is a real preset in
    config.yaml and one test below overwrites its body to exercise the legacy
    lock; a fixture that only popped the names it added would leave that
    clobbered body behind for whatever ran next. settings.CONFIG is a
    process-wide singleton shared with every other test file - the same shape
    of leak that broke test_playerprofile.py in P5.
    """
    import copy
    from settings import CONFIG
    # The live-module fixtures are module-scoped, so their fake event log is
    # shared by every test in the file. Clear it up front: a test that counts
    # its own events must not be able to pass or fail on what ran before it.
    for m in _LIVE:
        m._log.events.clear()
        m._log.shots.clear()
    before = (CONFIG.get("preset"), CONFIG.get("active_profile", _MISSING),
              copy.deepcopy(CONFIG["presets"]))
    yield
    CONFIG["preset"] = before[0]
    if before[1] is _MISSING:
        CONFIG.pop("active_profile", None)
    else:
        CONFIG["active_profile"] = before[1]
    CONFIG["presets"].clear()
    CONFIG["presets"].update(before[2])


@pytest.fixture
def preset_slot(orchestrator):
    """Install a temporary preset in the REAL CONFIG and select it."""
    from settings import CONFIG
    added, before = [], CONFIG["preset"]

    def install(**body):
        name = f"bp_p6_{len(added)}"
        CONFIG["presets"][name] = body
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


def _ev(orchestrator, name):
    return [kw for n, kw in orchestrator._log.events if n == name]


def _shard(monkeypatch, fail=False):
    """A shard stand-in whose abandon_run records, or raises."""
    calls = []
    m = types.ModuleType("shard")

    def abandon_run(*a, **k):
        calls.append(True)
        if fail:
            raise RuntimeError("no EXIT BATTLE button")
    m.abandon_run = abandon_run
    _patch_flows_shard(monkeypatch, m)
    return calls


def _no_shard(monkeypatch):
    """...and one that FAILS THE TEST if the surrender is ever reached."""
    m = types.ModuleType("shard")
    m.abandon_run = lambda *a, **k: pytest.fail(
        "a tournament run reached abandon_run")
    _patch_flows_shard(monkeypatch, m)


# ------------------------------------------------------------- coin max_wave

def test_max_wave_ends_the_run_through_the_guarded_flow(orchestrator, monkeypatch,
                                                        preset_slot):
    """The surrender goes through shard.abandon_run - the one chokepoint that
    carries its own tournament guard - never through taps of orchestrator's own."""
    preset_slot(kind="coin", max_wave=5000)
    calls = _shard(monkeypatch)
    rs = _rs(max_wave_done=False)
    assert orchestrator.max_wave_reached(rs, "FRAME", 4999) is False
    assert calls == []
    assert orchestrator.max_wave_reached(rs, "FRAME", 5000) is True
    assert calls == [True]
    assert rs.bot_left_battle is True       # the exit menus are ours
    assert orchestrator._taps == []
    ev = _ev(orchestrator, "max_wave")[0]
    assert ev == {"wave": 5000, "limit": 5000, "result": "surrendered"}


def test_max_wave_is_one_attempt_per_run(orchestrator, monkeypatch, preset_slot):
    """Retrying a failed surrender means walking the exit menus every pass with
    a live run on screen."""
    preset_slot(kind="coin", max_wave=100)
    calls = _shard(monkeypatch)
    rs = _rs(max_wave_done=False)
    for wave in (100, 101, 200):
        orchestrator.max_wave_reached(rs, "FRAME", wave)
    assert calls == [True]
    assert rs.max_wave_done is True


def test_a_failed_surrender_falls_back_to_stop_after_run(orchestrator, monkeypatch,
                                                         preset_slot):
    """Later than asked, but never wrong: the runner leaves at its own death
    handler instead of re-walking the exit menus."""
    preset_slot(kind="coin", max_wave=100)
    calls = _shard(monkeypatch, fail=True)
    rf = _f(orchestrator, "runflag")
    rf.__dict__["requests"] = []
    rs = _rs(max_wave_done=False)
    assert orchestrator.max_wave_reached(rs, "FRAME", 100) is False
    assert calls == [True]                          # it did try, once
    assert rf.__dict__["requests"] == ["max_wave_100"]
    ev = _ev(orchestrator, "max_wave_fallback")[0]
    assert ev["fallback"] == "stop_after_run" and ev["limit"] == 100
    orchestrator.max_wave_reached(rs, "FRAME", 200)
    assert calls == [True]                          # and never again


@pytest.mark.parametrize("body,in_tournament,lock", [
    ({"kind": "tournament", "max_wave": 100}, False, "blueprint kind"),
    ({"tournament_setup": True, "max_wave": 100}, False, "tournament_setup"),
    ({"kind": "coin", "max_wave": 100}, True, "trophy badge on screen"),
])
def test_max_wave_can_NEVER_surrender_a_tournament(orchestrator, monkeypatch,
                                                   preset_slot, body,
                                                   in_tournament, lock):
    """CLAUDE.md hard rule #2. The ticket auto-starts the run and the next
    entry costs more, so `max_wave` on a tournament is unreachable by
    construction - proven per lock, with an abandon_run that fails the test if
    it is ever called."""
    preset_slot(**body)
    monkeypatch.setattr(_f(orchestrator, "screen"), "in_tournament",
                        lambda frame: in_tournament)
    _no_shard(monkeypatch)
    rs = _rs(max_wave_done=False)
    assert orchestrator.max_wave_reached(rs, "FRAME", 100) is False
    ev = _ev(orchestrator, "max_wave_refused")[0]
    assert ev["lock"] == lock and "TOURNAMENT" in ev["why"]
    assert orchestrator._taps == [] and orchestrator._log.shots == ["max_wave_refused"]


def test_the_legacy_tournament_preset_is_a_lock_for_max_wave_too(
        orchestrator, monkeypatch, preset_slot):
    from settings import CONFIG
    preset_slot(max_wave=100)
    CONFIG["presets"]["tournament"] = dict(CONFIG["presets"][CONFIG["preset"]])
    CONFIG["preset"] = "tournament"
    monkeypatch.setattr(_f(orchestrator, "screen"), "in_tournament",
                        lambda frame: False)
    _no_shard(monkeypatch)
    assert orchestrator.max_wave_reached(_rs(max_wave_done=False), "FRAME", 100) \
        is False
    assert _ev(orchestrator, "max_wave_refused")[0]["lock"] == \
        "legacy tournament preset"


def test_the_real_tournament_preset_survived_that(orchestrator):
    """Runs straight after the test that overwrites `tournament` in the
    process-wide CONFIG. If the fixture ever goes back to restoring by key set
    instead of by value, the clobbered body leaks into every later test file
    and this fails first, here, instead of somewhere unrelated."""
    from settings import CONFIG
    real = CONFIG["presets"]["tournament"]
    assert "max_wave" not in real and "in_run_actions" not in real
    assert real.get("tournament_setup") is True


def test_every_early_exit_asks_the_same_lock(orchestrator):
    """One implementation of "may this run be cancelled". The Tier B action and
    the P6 knob asked it separately until they could drift apart."""
    import inspect
    for fn in (orchestrator._rule_surrender, orchestrator.max_wave_reached):
        assert "_tournament_locked" in inspect.getsource(fn), fn.__name__


# --------------------------------------------------------- coin cancel_sprint

def test_cancel_sprint_ends_the_sprint_once(orchestrator, monkeypatch, preset_slot):
    """Through end_intro_sprint, which verifies every step and carries its own
    retry floor - so "once" is the successful end."""
    preset_slot(kind="coin", cancel_sprint=True)
    ended = []
    monkeypatch.setattr(orchestrator, "end_intro_sprint",
                        lambda rs, why: ended.append(why) or True)
    rs = _rs()
    assert orchestrator.apply_cancel_sprint(rs) is True
    assert ended == ["preset_cancel_sprint"] and rs.sprint_ended is True
    assert orchestrator.apply_cancel_sprint(rs) is False    # already ended
    assert len(ended) == 1
    assert _ev(orchestrator, "cancel_sprint")[0]["result"] == "ended"


def test_a_sprint_that_will_not_end_is_retried_not_latched(orchestrator, monkeypatch,
                                                           preset_slot):
    """A run with no sprint left simply logs "indicator not found" inside the
    helper and changes nothing - so the flag is only set on a real end."""
    preset_slot(kind="coin", cancel_sprint=True)
    monkeypatch.setattr(orchestrator, "end_intro_sprint", lambda rs, why: False)
    rs = _rs()
    assert orchestrator.apply_cancel_sprint(rs) is False
    assert rs.sprint_ended is False
    assert _ev(orchestrator, "cancel_sprint") == []


@pytest.mark.parametrize("body", [{}, {"cancel_sprint": False}])
def test_cancel_sprint_absent_or_false_never_calls_the_helper(orchestrator,
                                                              monkeypatch,
                                                              preset_slot,
                                                              body):
    preset_slot(kind="coin", **body)
    monkeypatch.setattr(orchestrator, "end_intro_sprint",
                        lambda *a, **k: pytest.fail("touched the sprint"))
    assert orchestrator.apply_cancel_sprint(_rs()) is False


# ------------------------------------------------ tournament in_run_actions

def _cards(orchestrator, monkeypatch, fail_on=()):
    """Record apply_cards calls; raise for the named presets."""
    done = []

    def apply_cards(p):
        if p in fail_on:
            raise RuntimeError(f"no such card preset {p}")
        done.append(p)
        return "loaded"
    monkeypatch.setattr(orchestrator._loadout, "apply_cards", apply_cards)
    patch_module(monkeypatch, "loadout", orchestrator._loadout)
    return done


TOURNEY_ACTIONS = [{"id": "in_run#0", "at_wave": 1000,
                    "switch_cards": "disco"},
                   {"id": "in_run#1", "at_wave": 2000,
                    "switch_cards": "tourney_p1"}]


# THE FEATURE IS OFF (Codex P6 #2). The route it needed was
# loadout.apply_cards -> tourney.open_nav, which opens with a FIXED tap on the
# bottom nav row and returns by polling for HOME - both written for a game
# sitting at Home, neither verifiable from inside a live battle, and no
# template of the in-battle nav row exists to confirm the row is even drawn
# before that tap lands. In a paid tournament entry that is a blind tap, so it
# refuses instead. These tests pin the refusal; when a real route is built they
# are the ones to rewrite, deliberately.

@pytest.mark.parametrize("wave", [999, 1000, 5000])
def test_in_run_actions_never_touch_the_cards_screen(orchestrator, monkeypatch,
                                                     preset_slot, wave):
    """No wave, on the right kind of preset, reaches apply_cards."""
    preset_slot(kind="tournament", in_run_actions=TOURNEY_ACTIONS)
    monkeypatch.setattr(orchestrator._loadout, "apply_cards",
                        lambda p: pytest.fail("blind-tapped to the cards "
                                              "screen inside a paid run"))
    patch_module(monkeypatch, "loadout", orchestrator._loadout)
    rs = _rs(in_run_done=set(), in_run_off=set(), in_run_tries={})
    assert orchestrator.run_in_run_actions(rs, "FRAME", wave) is False
    assert rs.in_run_done == set()


def test_the_refusal_is_loud_but_said_once_per_run(orchestrator, monkeypatch,
                                                   preset_slot):
    """A silent refusal is a profile whose author never learns the swap did not
    happen; a per-pass refusal buries the events file at ~1.4fps."""
    preset_slot(kind="tournament", in_run_actions=TOURNEY_ACTIONS)
    rs = _rs(in_run_done=set(), in_run_off=set(), in_run_tries={})
    for _ in range(6):
        assert orchestrator.run_in_run_actions(rs, "FRAME", 5000) is False
    fails = _ev(orchestrator, "in_run_action_failed")
    assert len(fails) == 1
    assert fails[0]["disabled"] is True and fails[0]["count"] == 2
    assert "no verified route" in fails[0]["error"]


def test_the_refusal_does_not_leave_the_battle_screen(orchestrator, preset_slot):
    """bot_left_battle is the flag that says "this menu is ours". Refusing
    leaves no menu, so setting it would tell the off-battle handler to recover
    from something that never happened."""
    preset_slot(kind="tournament", in_run_actions=TOURNEY_ACTIONS)
    rs = _rs(in_run_done=set(), in_run_off=set(), in_run_tries={},
             bot_left_battle=False)
    orchestrator.run_in_run_actions(rs, "FRAME", 5000)
    assert rs.bot_left_battle is False


def test_a_preset_without_the_key_stays_completely_silent(orchestrator, preset_slot):
    """Every legacy preset and every coin blueprint: one dict miss, no event."""
    preset_slot(kind="coin")
    rs = _rs(in_run_done=set(), in_run_off=set(), in_run_tries={})
    assert orchestrator.run_in_run_actions(rs, "FRAME", 5000) is False
    assert _ev(orchestrator, "in_run_action_failed") == []
    assert _ev(orchestrator, "in_run_action") == []


def test_the_refusal_matches_the_death_phase_refusal(orchestrator):
    """The death phase already refused switch_cards for THIS EXACT REASON in
    P4 - apply_cards navigates from Home. P6 wired an in-run path without
    re-asking, and the two answers have to agree."""
    assert "switch_cards" not in orchestrator.RULE_DEATH_ACTIONS
    # On the AST, not the text: the docstring EXPLAINS the route it refuses to
    # take, and a substring search cannot tell an explanation from a call.
    import ast
    import inspect
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(orchestrator.run_in_run_actions)))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import)
                and any(a.name == "loadout" for a in n.names)]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                and n.attr == "apply_cards"]


def test_the_tier_b_switch_cards_action_is_refused_the_same_way(orchestrator):
    """THE TWIN, found while fixing #2 and not in the audit's list.

    _rule_switch_cards is the same loadout.apply_cards call from the same place
    - the Tier B interpreter, mid-battle - and it is reachable on COIN farms as
    well as tournaments, so it could blind-tap a paid entry by a different
    route. Refusing one and not the other would leave the hazard open and the
    reasoning inconsistent, so it is retired at admission, in both phases.
    """
    for phase in ("battle", "death"):
        why = orchestrator._rule_admits(
            {}, "wave_at_least", {"value": 1}, False,
            "switch_cards", {"preset": "disco"}, phase=phase)
        assert why, f"switch_cards still admitted in the {phase} phase"
    # ...and it is the ROUTE that is refused, not a malformed parameter
    assert "live battle" in orchestrator._rule_admits(
        {}, "wave_at_least", {"value": 1}, False,
        "switch_cards", {"preset": "disco"})


def test_in_run_actions_never_run_in_the_death_phase(orchestrator):
    """There is no deck to change for a run that is already over, and the stats
    dialog is a menu. run_death_rules must not reach for them."""
    import inspect
    assert "run_in_run_actions" not in inspect.getsource(orchestrator.run_death_rules)
    assert "run_in_run_actions" not in inspect.getsource(orchestrator._run_rules)


# --------------------------------------------------------------- legacy path

def test_the_legacy_preset_never_touches_any_p6_key(orchestrator, monkeypatch):
    """LEGACY, bit for bit: normal_run carries none of the three keys, so all
    three consumers are dict misses that act on nothing and log nothing."""
    from settings import CONFIG
    CONFIG["preset"] = "normal_run"
    orchestrator._log.events.clear()
    monkeypatch.setattr(orchestrator, "end_intro_sprint",
                        lambda *a, **k: pytest.fail("touched the sprint"))
    monkeypatch.setattr(orchestrator._loadout, "apply_cards",
                        lambda p: pytest.fail("swapped cards"))
    _no_shard(monkeypatch)
    rs = _rs(max_wave_done=False, in_run_done=set(), in_run_off=set(),
             in_run_tries={})
    assert orchestrator.apply_cancel_sprint(rs) is False
    assert orchestrator.max_wave_reached(rs, "FRAME", 999999) is False
    assert orchestrator.run_in_run_actions(rs, "FRAME", 999999) is False
    assert orchestrator._log.events == [] and orchestrator._taps == []
    assert rs.max_wave_done is False                # not even the one attempt


@pytest.mark.parametrize("body", [
    {"kind": "coin"},                               # no max_wave at all
    {"kind": "coin", "max_wave": None},             # explicitly unbounded
])
def test_max_wave_null_means_unbounded(orchestrator, monkeypatch, preset_slot, body):
    preset_slot(**body)
    _no_shard(monkeypatch)
    assert orchestrator.max_wave_reached(_rs(max_wave_done=False), "FRAME", 10 ** 9) \
        is False


# ----------------------------------------------- reconciliation with the
# compiler. These are the only tests here that touch the sibling's module: the
# compiled preset is a CONTRACT between two files written by two people, and an
# agreed key name is worth nothing if only one side is tested against it.

def _compiler():
    try:
        from player import playerprofile
    except Exception:                                   # noqa: BLE001
        pytest.skip("playerprofile unavailable")
    return playerprofile


def test_the_compiler_really_emits_the_keys_this_file_consumes(orchestrator):
    """Both sides of `max_wave` / `cancel_sprint`, from the real compiler.

    `max_wave: None` is the compiler's explicit "no cap" - not an omission, and
    not a runtime default. The consumer above must read it as unbounded, which
    is why absent and null are tested as the same thing.
    """
    pp = _compiler()
    prof = {"player": {}, "policies": {},
            "blueprints": {"c": {"kind": "coin", "label": "Coin"}}}
    out = pp.compile_preset(prof, "c")
    assert "cancel_sprint" in out and "max_wave" in out, \
        "the compiler stopped emitting the P6 coin keys"
    assert out["cancel_sprint"] is False and out["max_wave"] is None
    # ...and the consumer reads that pair as "do nothing", not as a fault.
    from settings import CONFIG
    CONFIG["presets"]["bp_reconcile"] = out
    CONFIG["preset"] = "bp_reconcile"
    assert orchestrator.apply_cancel_sprint(_rs()) is False
    assert orchestrator.max_wave_reached(_rs(max_wave_done=False), "FRAME", 10 ** 9) \
        is False


def test_the_compiled_in_run_shape_drives_the_runtime_unchanged(orchestrator,
                                                                monkeypatch,
                                                                preset_slot):
    """END TO END on the shape: what the compiler emits reaches the consumer
    with nothing translating between them - and is REFUSED there, by the
    runtime rather than by the compiler.

    The two halves are deliberately still connected while the feature is off.
    The compiler keeps emitting `in_run_actions` (with a `requires` per action
    for the spawn gate, which the runtime does not read), so turning the
    feature on later is a change to one function and not an unwinding of the
    profile format. What this pins is that the refusal happens at the point of
    ACTING, where the screen is - the only place that can know a route is
    unsafe.
    """
    pp = _compiler()
    fn = getattr(pp, "_compile_in_run_actions", None)
    if fn is None:
        pytest.skip("compiler internals moved")
    actions = fn({"in_run_actions": [{"at_wave": 1000,
                                      "switch_cards": "disco"},
                                     {"at_wave": 2000,
                                      "switch_cards": "tourney_p1"}]})
    assert [a["id"] for a in actions] == ["in_run#0", "in_run#1"]
    assert "requires" in actions[0]                 # the key I do not read
    preset_slot(kind="tournament", in_run_actions=actions)
    monkeypatch.setattr(orchestrator._loadout, "apply_cards",
                        lambda p: pytest.fail("compiled actions reached the "
                                              "cards screen"))
    patch_module(monkeypatch, "loadout", orchestrator._loadout)
    rs = _rs(in_run_done=set(), in_run_off=set(), in_run_tries={})
    assert orchestrator.run_in_run_actions(rs, "FRAME", 1000) is False
    assert orchestrator.run_in_run_actions(rs, "FRAME", 2000) is False
    # the runtime saw the real compiled shape, and counted it correctly
    assert _ev(orchestrator, "in_run_action_failed")[0]["count"] == 2


# ------------------------------------------- tourney.setup() unification (P6)

@pytest.fixture(scope="module")
def tourney():
    m = _import_live("tourney")
    _LIVE.append(m)
    return m


def test_setup_equips_through_loadout_not_its_own_copy(tourney):
    """The unification. Two implementations of "put the tournament build on"
    drift apart one fix at a time: the stale-grid-scan bug was fixed in
    loadout.apply_modules on 2026-08-13 and was still live in module_swap at
    the 2026-08-15 tournament, which ran on the coin primaries because of it."""
    import inspect
    src = inspect.getsource(tourney.setup)
    assert "loadout.apply(name)" in src
    for gone in ("guardian_swap()", "card_swap()", "module_swap()"):
        assert gone not in src, gone
    assert "card_tweaks()" in src           # ...the part loadout does not own


def test_the_tournament_build_lives_in_config_not_in_constants(tourney):
    """The unification's end state (2026-09-06): the tournament build is the
    `tourney_1` loadout body in config.yaml and nothing else - no module
    plan, no card preset, no deck tweak constants in code, because those
    were one account's. A `global_preset` body carries NO manual keys (the
    validator refuses mixed bodies)."""
    from settings import CONFIG
    lo = CONFIG["loadouts"][tourney.TOURNEY_LOADOUT]
    if "global_preset" in lo:
        assert set(lo) == {"global_preset"}
    for gone in ("MODULE_PLAN", "CARD_PRESET", "CARDS_DROP", "CARDS_ADD",
                 "card_swap", "module_swap"):
        assert not hasattr(tourney, gone), gone


def test_the_card_tweaks_are_defined_once_and_read_from_config(tourney):
    """setup() applies the deck tweaks through the one card_tweaks(), which
    takes its plan from config.yaml `tourney_card_tweaks` - two copies of
    "drop this, add that" is two places to update when the plan changes."""
    import inspect
    assert inspect.getsource(tourney.setup).count("card_tweaks()") == 1
    body = inspect.getsource(tourney.card_tweaks)
    assert "_card_tweak_plan()" in body and "CARDS_SCREEN" in body


def test_read_only_verifies_and_taps_nothing_that_changes_state(tourney,
                                                                monkeypatch):
    """The gate P6 has to pass before it touches a real tournament: walk the
    whole flow, confirm the build is already on, change nothing.

    "Taps nothing" means no STATE-CHANGING tap - it still navigates, because
    you cannot read the cards screen without opening it (the pre-P6 read-only
    pass already tapped its way into the tournament and back out). So the
    assertion is on the reasons: navigation only, no equip/select/buy.
    """
    lo = types.ModuleType("loadout")
    lo.CARD_TPL = "cards/preset_{}.png"
    lo.spec = lambda n: {"cards": "tourney_p1", "guardians": True,
                         "modules": [["dimension_core", "assist"]]}
    lo.current_cards = lambda frame=None: "tourney_p1"
    patch_module(monkeypatch, "loadout", lo)
    monkeypatch.setattr(tourney, "open_nav", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "require", lambda *a, **k: ("FRAME", (0, 0)))
    monkeypatch.setattr(tourney, "find",
                        lambda frame, rel, th=0.9: ((10, 20), 0.99))
    monkeypatch.setattr(tourney, "greenness", lambda *a, **k: 1.0)
    monkeypatch.setattr(tourney, "verify_slot",
                        lambda name, slot, frame=None: True)
    taps = []
    monkeypatch.setattr(tourney, "tap_at",
                        lambda pt, reason: taps.append(reason))
    assert tourney.verify_loadout("tourney_1") == []
    assert taps == ["open guild", "guardian tab"]   # navigation, nothing else


@pytest.mark.parametrize("broken,fragment", [
    ("cards", "cards:"),
    ("guardians", "guardians:"),
    ("modules", "modules:"),
])
def test_read_only_reports_every_kind_of_mismatch(tourney, monkeypatch, broken,
                                                  fragment):
    """It is a REPORT, not an abort: an unverifiable module and a wrong deck
    both come back as text, so the operator sees the whole picture in one
    pass instead of the first problem only."""
    lo = types.ModuleType("loadout")
    lo.CARD_TPL = "cards/preset_{}.png"
    lo.spec = lambda n: {"cards": "tourney_p1", "guardians": ["attack"],
                         "modules": [["dimension_core", "assist"]]}
    lo.current_cards = lambda frame=None: ("main_farm" if broken == "cards"
                                           else "tourney_p1")
    patch_module(monkeypatch, "loadout", lo)
    for name in ("open_nav", "return_to_game"):
        monkeypatch.setattr(tourney, name, lambda *a, **k: None)
    monkeypatch.setattr(tourney, "require", lambda *a, **k: ("FRAME", (0, 0)))
    monkeypatch.setattr(tourney, "find",
                        lambda frame, rel, th=0.9: ((10, 20), 0.99))
    monkeypatch.setattr(tourney, "tap_at", lambda pt, reason: None)
    monkeypatch.setattr(tourney, "greenness",
                        lambda *a, **k: 0.0 if broken == "guardians" else 1.0)
    monkeypatch.setattr(tourney, "verify_slot",
                        lambda n, s, frame=None: broken != "modules")
    problems = tourney.verify_loadout("tourney_1")
    assert len(problems) == 1 and problems[0].startswith(fragment)


def test_an_unverifiable_module_is_a_problem_not_a_pass(tourney, monkeypatch):
    """verify_slot returns None for "no template / unknown module". Reading
    that as OK is exactly the inference the header check exists to abolish."""
    lo = types.ModuleType("loadout")
    lo.CARD_TPL = "cards/preset_{}.png"
    lo.spec = lambda n: {"modules": [["mystery_module", "primary"]]}
    patch_module(monkeypatch, "loadout", lo)
    for name in ("open_nav", "return_to_game"):
        monkeypatch.setattr(tourney, name, lambda *a, **k: None)
    monkeypatch.setattr(tourney, "verify_slot", lambda n, s, frame=None: None)
    problems = tourney.verify_loadout("tourney_1")
    assert len(problems) == 1 and "NOT VERIFIABLE" in problems[0]


def _parked(tourney, monkeypatch, where="home"):
    """Put the game on a screen read-only is allowed to start from."""
    monkeypatch.setattr(tourney.screen, "identify",
                        lambda frame: types.SimpleNamespace(name=where))
    monkeypatch.setattr(tourney, "on_home", lambda frame: where == "home")


def test_read_only_setup_starts_no_battle_and_equips_nothing(tourney,
                                                             monkeypatch):
    """The whole point: it returns False (no battle started) and never reaches
    the equip path or BATTLE."""
    _parked(tourney, monkeypatch)
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    monkeypatch.setattr(tourney, "open_tournament", lambda: "FRAME")
    monkeypatch.setattr(tourney, "read_conditions", lambda frame: {})
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "verify_loadout", lambda name: [])
    monkeypatch.setattr(tourney, "start_battle",
                        lambda *a, **k: pytest.fail("started a battle"))
    lo = types.ModuleType("loadout")
    lo.apply = lambda *a, **k: pytest.fail("equipped something")
    patch_module(monkeypatch, "loadout", lo)
    assert tourney.setup(read_only=True) is False
    done = [kw for n, kw in tourney._log.events
            if n == "tourney_setup" and kw.get("stage") == "read_only_done"]
    assert len(done) == 1 and done[0]["ok"] is True


def test_read_only_reports_a_failing_account_without_aborting(tourney,
                                                              monkeypatch):
    """A mismatch is information, not an exception - the operator wants the
    list, and a raise would hide every problem after the first."""
    _parked(tourney, monkeypatch)
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    monkeypatch.setattr(tourney, "open_tournament", lambda: "FRAME")
    monkeypatch.setattr(tourney, "read_conditions", lambda frame: {})
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "verify_loadout",
                        lambda name: ["cards: 'main_farm' selected"])
    assert tourney.setup(read_only=True) is False
    done = [kw for n, kw in tourney._log.events
            if n == "tourney_setup" and kw.get("stage") == "read_only_done"]
    assert done[-1]["ok"] is False and done[-1]["problems"]


# ============================================================================
# Codex P6 audit fixes. Four findings, all load-bearing on either a paid entry
# or an unattended overnight farm.
# ============================================================================

# --- #1 CRITICAL: a max_wave surrender is a RUN BOUNDARY, not a pause -------

def test_the_max_wave_call_site_renews_the_run_state(orchestrator):
    """shard.abandon_run does not stop at the stats dialog - it taps RETRY
    itself and returns - so the NEXT RUN IS ALREADY STARTING when
    max_wave_reached returns True.

    Carrying the old RunState across that boundary capped only the first run of
    the session: max_wave_done stayed True so run 2 ran forever, and
    sprint_ended plus the rescue/rule ledgers described a tower that no longer
    existed. The fix is the death handler's own renewal, reused rather than
    reimplemented - which is what this asserts, structurally, on live source.
    """
    import ast
    import inspect
    src = textwrap.dedent(inspect.getsource(orchestrator.main))
    fix = [n for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.If) and isinstance(n.test, ast.Call)
           and getattr(n.test.func, "id", None) == "max_wave_reached"]
    assert len(fix) == 1, "the max_wave call site moved"
    body = fix[0].body
    assert any(isinstance(s, ast.Assign)
               and getattr(s.targets[0], "id", None) == "rs"
               and isinstance(s.value, ast.Call)
               and getattr(s.value.func, "id", None) == "RunState"
               for s in body), "the surrender path kept the old RunState"
    assert isinstance(body[-1], ast.Continue)


def test_the_renewal_matches_the_death_handlers(orchestrator):
    """Not a parallel implementation: the same two statements, so a change to
    how a run boundary settles cannot land on one path and miss the other."""
    import inspect
    import re
    src = inspect.getsource(orchestrator.main)
    both = re.findall(r"rs = RunState\(\)\s*\n\s*time\.sleep\(5\)", src)
    assert len(both) == 2, ("expected the death path and the max_wave path to "
                            f"renew identically, found {len(both)}")


def test_a_second_run_is_capped_too(orchestrator, monkeypatch, preset_slot):
    """THE BUG, at the level that matters: a fresh RunState caps again.

    Driven at the state level - the call site's renewal is pinned above -
    because running two whole runs through main() would need the whole game.
    """
    preset_slot(kind="coin", max_wave=100)
    calls = _shard(monkeypatch)
    rs = _rs(max_wave_done=False)
    assert orchestrator.max_wave_reached(rs, "FRAME", 100) is True
    assert rs.max_wave_done is True
    rs = orchestrator.RunState()                       # ...what the loop now does
    assert rs.max_wave_done is False, "a fresh RunState starts uncapped"
    assert orchestrator.max_wave_reached(rs, "FRAME", 100) is True
    assert len(calls) == 2, "the second run was never capped"


def test_a_fresh_run_state_clears_every_p6_ledger(orchestrator):
    """The rest of what the old state was wrongly carrying across."""
    rs = orchestrator.RunState()
    assert rs.max_wave_done is False
    assert rs.in_run_done == set() and rs.in_run_off == set()
    assert rs.in_run_tries == {}
    assert rs.sprint_ended is False


# --- #3 HIGH: a swallowed tap is not a loaded deck --------------------------

def _cards_screen(monkeypatch, active_after):
    """A cards screen whose tab goes active only after N taps.

    This drives the REAL loadout.py, so its logger is replaced too: the real
    one appends to the instance's events file and writes PNGs through cv2, and
    a test has no business doing either.
    """
    from interactions import loadout as real
    state = {"taps": 0, "events": []}
    fake_log = types.SimpleNamespace(
        event=lambda n, **kw: state["events"].append((n, kw)),
        shot=lambda frame, tag: None)
    monkeypatch.setattr(real, "logger", fake_log)
    monkeypatch.setattr(real.tourney, "open_nav", lambda *a, **k: None)
    monkeypatch.setattr(real.tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(real.tourney, "require",
                        lambda *a, **k: ("FRAME", (100, 200)))
    monkeypatch.setattr(real.tourney, "tap_at",
                        lambda pt, why: state.update(taps=state["taps"] + 1))
    monkeypatch.setattr(real, "_tab_active",
                        lambda frame, pt: state["taps"] >= active_after)
    return real, state


def test_apply_cards_confirms_the_tab_AFTER_tapping(monkeypatch):
    """It used to check _tab_active only BEFORE the tap and then report
    'loaded' unconditionally - success reported for a tap the game swallowed.
    The identical class was proved live on acct2 (2026-08-19): end_round_yes
    matched at 0.99 on the timeout shot; found, tapped, and swallowed."""
    real, state = _cards_screen(monkeypatch, active_after=1)
    assert real.apply_cards("tourney_p1") == "loaded"
    assert state["taps"] == 1


def test_a_swallowed_tap_is_retried_then_aborts(monkeypatch):
    """SWALLOWED-TAP SIMULATION: the tab never goes active. Two taps, then an
    Abort - never a quiet 'loaded' on a deck that was not selected."""
    real, state = _cards_screen(monkeypatch, active_after=99)
    with pytest.raises(real.tourney.Abort) as e:
        real.apply_cards("tourney_p1")
    assert state["taps"] == 2, "one retry, not an infinite hunt"
    assert "still not active" in str(e.value)


def test_an_already_active_tab_is_never_tapped(monkeypatch):
    real, state = _cards_screen(monkeypatch, active_after=0)
    assert real.apply_cards("tourney_p1") == "already"
    assert state["taps"] == 0


def test_setup_verifies_the_deck_before_spending_the_ticket(tourney,
                                                            monkeypatch):
    """A ticket costs 10 -> 20 -> 30 gems and the run AUTO-STARTS, so an
    unverified deck is not something to discover from the leaderboard."""
    order = []
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    monkeypatch.setattr(tourney, "open_tournament", lambda: "FRAME")
    monkeypatch.setattr(tourney, "read_conditions", lambda frame: {})
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "card_tweaks",
                        lambda *a, **k: order.append("tweaks"))
    monkeypatch.setattr(tourney, "verify_loadout",
                        lambda name: order.append("verify") or [])
    monkeypatch.setattr(tourney, "start_battle",
                        lambda *a, **k: order.append("battle"))
    lo = types.ModuleType("loadout")
    lo.apply = lambda *a, **k: order.append("apply")
    lo.spec = lambda n: {}          # legacy body -> the tweaks still run
    patch_module(monkeypatch, "loadout", lo)
    assert tourney.setup() is True
    assert order == ["apply", "tweaks", "verify", "battle"], \
        "the verify must be the LAST thing before BATTLE"


def test_a_mismatched_deck_aborts_instead_of_entering(tourney, monkeypatch):
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    monkeypatch.setattr(tourney, "open_tournament", lambda: "FRAME")
    monkeypatch.setattr(tourney, "read_conditions", lambda frame: {})
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "card_tweaks", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "verify_loadout",
                        lambda name: ["modules: galaxy_compressor in primary"])
    monkeypatch.setattr(tourney, "start_battle",
                        lambda *a, **k: pytest.fail("PAID ENTRY on an "
                                                    "unverified loadout"))
    lo = types.ModuleType("loadout")
    lo.apply = lambda *a, **k: None
    lo.spec = lambda n: {}
    patch_module(monkeypatch, "loadout", lo)
    with pytest.raises(tourney.Abort) as e:
        tourney.setup()
    assert "galaxy_compressor" in str(e.value)


# --- #4 MEDIUM: read-only never ends anything -------------------------------

@pytest.mark.parametrize("where", ["battle", "unknown", "cards", "game_stats"])
def test_read_only_refuses_anywhere_it_would_have_to_clear_the_screen(
        tourney, monkeypatch, where):
    """open_tournament -> ensure_home ENDS a live coin run to make room. Right
    for a real entry, fatal for a validation that promised to tap nothing - so
    read-only refuses unless the game is already parked somewhere safe."""
    _parked(tourney, monkeypatch, where=where)
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    monkeypatch.setattr(tourney, "open_tournament",
                        lambda: pytest.fail("read-only navigated, and "
                                            "ensure_home can end a live run"))
    monkeypatch.setattr(tourney, "verify_loadout",
                        lambda name: pytest.fail("verified from nowhere"))
    assert tourney.setup(read_only=True) is False
    ref = [kw for n, kw in tourney._log.events
           if n == "tourney_setup" and kw.get("stage") == "read_only_refused"]
    assert len(ref) == 1 and ref[0]["on"] == where


@pytest.mark.parametrize("where", ["home", "tournament"])
def test_read_only_proceeds_from_a_parked_screen(tourney, monkeypatch, where):
    _parked(tourney, monkeypatch, where=where)
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    monkeypatch.setattr(tourney, "open_tournament", lambda: "FRAME")
    monkeypatch.setattr(tourney, "read_conditions", lambda frame: {})
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "verify_loadout", lambda name: [])
    monkeypatch.setattr(tourney, "start_battle",
                        lambda *a, **k: pytest.fail("started a battle"))
    assert tourney.setup(read_only=True) is False
    assert [kw for n, kw in tourney._log.events
            if n == "tourney_setup" and kw.get("stage") == "read_only_done"]


def test_a_real_entry_still_clears_the_screen(tourney, monkeypatch):
    """The refusal is READ-ONLY's alone. A real entry must still be able to end
    a farm run to make room - that is ensure_home's job, and P6 leaves it."""
    _parked(tourney, monkeypatch, where="battle")
    monkeypatch.setattr(tourney, "in_tournament", lambda frame: False)
    reached = []
    monkeypatch.setattr(tourney, "open_tournament",
                        lambda: reached.append(True) or "FRAME")
    monkeypatch.setattr(tourney, "read_conditions", lambda frame: {})
    monkeypatch.setattr(tourney, "return_to_game", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "card_tweaks", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "verify_loadout", lambda name: [])
    monkeypatch.setattr(tourney, "start_battle", lambda *a, **k: None)
    lo = types.ModuleType("loadout")
    lo.apply = lambda *a, **k: None
    lo.spec = lambda n: {}
    patch_module(monkeypatch, "loadout", lo)
    assert tourney.setup() is True
    assert reached == [True]
