"""P5: the plan-driven scheduler in combo.py.

Pointed at THE LIVE combo.py, not `patches/scratch/` - apply_p3.py's combo
hunk #1 anchors on `class _Adopted:`, which still occurs after the P3 apply, so
re-running it prepends a second copy of the P3 blueprint helpers and the last
definition wins. A scratch-based test of the scheduler would grade code this
tree does not run. (test_p4_interpreter.py learned the same lesson.)

Two things every test here is really asserting:
  1. WITH A PLAN, the compiled blocks decide - in order, first eligible wins,
     with no runtime defaults anywhere: a block missing a key is refused and
     said out loud, never guessed at.
  2. WITHOUT ONE, the constant era is bit-for-bit intact. That is not a comment
     in this file, it is `test_constant_era_equivalence`, which walks a whole
     week hour by hour and asserts the block scheduler and the original due()
     agree on every single one.
"""
import datetime
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fakes import (_patch_flows_shard, install_modules,       # noqa: E402
                    patch_module, restore_modules)

_MISSING = object()


# --------------------------------------------------------------- the fakes

class FakeLogger(types.ModuleType):
    def __init__(self):
        super().__init__("logger")
        self.events = []

    def event(self, name, **kw):
        self.events.append((name, kw))

    def shot(self, frame, tag):
        return f"shot/{tag}.png"

    def names(self):
        return [n for n, _ in self.events]


class FakeDaystate(types.ModuleType):
    """The real daystate semantics, in memory.

    Not a stub: the date-scoped VALUE vs bare-ISO FLAG distinction is exactly
    what the block counters ride on, and a fake that collapsed them would make
    the resume tests pass for the wrong reason.
    """

    def __init__(self):
        super().__init__("daystate")
        self.store = {}
        self.today = datetime.date.today().isoformat()

    def get_today(self, key, default=0):
        rec = self.store.get(key)
        if isinstance(rec, dict) and rec.get("date") == self.today:
            return rec.get("value", default)
        return default

    def set_today(self, key, value):
        self.store[key] = {"date": self.today, "value": value}

    def mark_today(self, key):
        self.store[key] = self.today

    def flag_today(self, key):
        return self.store.get(key) == self.today

    def get_raw(self, key, default=None):
        return self.store.get(key, default)

    def set_raw(self, key, value):
        self.store[key] = value

    def clear(self, key):
        self.store.pop(key, None)


def _import_live_combo():
    """Import this tree's combo.py with daystate/logger/runflag faked."""
    import settings                                   # the REAL one
    log, ds = FakeLogger(), FakeDaystate()
    rf = types.ModuleType("runflag")
    rf.requests = []
    rf.request = lambda reason="": rf.requests.append(reason)
    rf.requested = lambda: None
    rf.clear = lambda: None
    mods = {"settings": settings, "logger": log, "daystate": ds, "runflag": rf}
    saved_live = sys.modules.get("_live_combo")
    saved = install_modules(mods)      # bare + dotted + package attributes
    try:
        spec = importlib.util.spec_from_file_location(
            "_live_combo", ROOT / "scheduling" / "combo.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_live_combo"] = mod
        spec.loader.exec_module(mod)
    finally:
        restore_modules(saved)
        if saved_live is None:
            sys.modules.pop("_live_combo", None)
        else:
            sys.modules["_live_combo"] = saved_live
    mod._log, mod._ds, mod._rf = log, ds, rf
    return mod


@pytest.fixture(scope="module")
def combo():
    return _import_live_combo()


@pytest.fixture(autouse=True)
def _isolation(combo):
    """combo carries module-level edge-log state (refusals already announced,
    `plan_absent` already said) and mutates the REAL CONFIG. Both are handed
    back after every test - a suite that quietly breaks the suite after it is
    worse than no suite."""
    from settings import CONFIG
    before = (CONFIG.get("active_profile", _MISSING), set(CONFIG["presets"]))
    combo._log.events.clear()
    combo._ds.store.clear()
    combo._block_refusals.clear()
    combo._plan_absent_logged = False
    # The blocks below name blueprints, and a block whose preset was never
    # materialized is refused - correctly, but that is a different test.
    for name in ("coin_default", "shard_daily", "tourney_main"):
        CONFIG["presets"].setdefault(f"bp_{name}", {
            "kind": "coin", "loadout": "coin_farm", "tier": 14})
    yield
    if before[0] is _MISSING:
        CONFIG.pop("active_profile", None)
    else:
        CONFIG["active_profile"] = before[0]
    for extra in set(CONFIG["presets"]) - before[1]:
        CONFIG["presets"].pop(extra, None)
    CONFIG.pop("plan", None)


def _ev(combo, name):
    return [kw for n, kw in combo._log.events if n == name]


# ---- THE COMPILED SHAPE, taken from playerprofile.compile_plan(): per-weekday
# ordered block lists under a `week` wrapper, every key present, the window
# already parsed into minutes since midnight (after inclusive, until exclusive).
DAY_MINUTES = 24 * 60


def _block(i, day="monday", block="coin", blueprint="coin_default",
           kind="coin", after_min=0, until_min=DAY_MINUTES, count=None):
    return {"id": f"{day}#{i}", "block": block, "blueprint": blueprint,
            "preset": f"bp_{blueprint}" if blueprint else None, "kind": kind,
            "after_min": after_min, "until_min": until_min, "count": count}


GOLDEN_TOURNEY_DAY = [
    _block(0, "wednesday", "tournament", "tourney_main", "tournament",
           after_min=19 * 60, count=1),
    _block(1, "wednesday", "shards", "shard_daily", "shard",
           after_min=8 * 60, count=100),
    _block(2, "wednesday", "coin", "coin_default", "coin"),
]
GOLDEN_FARM_DAY = [
    _block(0, "monday", "shards", "shard_daily", "shard",
           after_min=8 * 60, count=100),
    _block(1, "monday", "coin", "coin_default", "coin"),
]


@pytest.fixture
def plan(combo):
    """Bind a compiled plan (and the presets its blocks name) for one test."""
    from settings import CONFIG
    saved = None

    def install(day_blocks: dict, presets=("coin_default", "shard_daily",
                                           "tourney_main", "quest_sm",
                                           "quest_ilm")):
        nonlocal saved
        CONFIG["active_profile"] = "test"
        for name in presets:
            CONFIG["presets"].setdefault(f"bp_{name}",
                                         {"kind": "coin", "loadout":
                                          "coin_farm", "tier": 14})
        pp = types.ModuleType("playerprofile")
        pp.PROFILE = {"blueprints": {}}
        pp.compiled_plan = lambda: {"week": day_blocks}
        if saved is not None:            # a second install() inside one test
            restore_modules(saved)
        saved = install_modules({"playerprofile": pp})
        return pp
    yield install
    # RESTORE, NEVER POP. Evicting the real playerprofile means the next
    # import builds a SECOND module object, and anything holding a reference
    # to the first (tools/plan_sim, another test's monkeypatched
    # PROFILES_DIR) is then patching a module nobody uses. install_modules /
    # restore_modules put back the exact prior sys.modules entries AND the
    # `player` package attribute, which is what `from player import
    # playerprofile` resolves first.
    if saved is not None:
        restore_modules(saved)


# ------------------------------------------------------------- eligibility

def _at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    """A datetime on a known weekday: 2026-08-17 was a Monday."""
    return datetime.datetime(2026, 8, 17 + day, hour, minute)


@pytest.mark.parametrize("after,until,hour,ok", [
    (0, DAY_MINUTES, 0, True),          # unbounded: the filler
    (0, DAY_MINUTES, 23, True),
    (8 * 60, DAY_MINUTES, 7, False),
    (8 * 60, DAY_MINUTES, 8, True),     # after is INCLUSIVE
    (8 * 60, DAY_MINUTES, 23, True),
    (0, 8 * 60, 7, True),
    (0, 8 * 60, 8, False),              # ...and until is EXCLUSIVE
    (8 * 60, 19 * 60, 7, False),
    (8 * 60, 19 * 60, 12, True),
    (8 * 60, 19 * 60, 19, False),
])
def test_the_time_window(combo, after, until, hour, ok):
    b = _block(0, after_min=after, until_min=until)
    assert combo._block_eligible(b, _at(0, hour)) is ok


@pytest.mark.parametrize("count,done,ok", [
    (None, 999, True),               # null count = unbounded
    (1, 0, True),
    (1, 1, False),
    (100, 99, True),
    (100, 100, False),
    (0, 0, False),                   # a zero-count block is switched off
])
def test_the_count_window(combo, count, done, ok):
    b = _block(0, count=count)
    combo._ds.set_today(combo._progress_key(b), done)
    assert combo._block_eligible(b, _at(0, 12)) is ok


def test_a_closed_block_is_never_eligible(combo):
    """The day-closing flag outranks everything: a tournament refused at 19:05
    must not come back at 19:25 because its count still says 0 of 1."""
    b = _block(0, "wednesday", "tournament", "tourney_main", "tournament",
               after_min=19 * 60, count=1)
    assert combo._block_eligible(b, _at(2, 20)) is True
    combo._mark_block_done(b)
    assert combo._block_eligible(b, _at(2, 20)) is False


def test_first_eligible_wins_and_the_last_block_is_the_filler(combo, plan):
    """The compiled list is a PRIORITY list. The tournament outranks the shard
    block because it is the one thing with a closing window - a missed entry is
    gone, where shard runs are only ever deferred."""
    plan({"wednesday": GOLDEN_TOURNEY_DAY})
    assert combo.next_block(_at(2, 3))["block"] == "coin"      # pre-08:00
    assert combo.next_block(_at(2, 9))["block"] == "shards"
    assert combo.next_block(_at(2, 20))["block"] == "tournament"
    # tournament done -> the shard block, then the filler
    combo._mark_block_done(GOLDEN_TOURNEY_DAY[0])
    assert combo.next_block(_at(2, 20))["block"] == "shards"
    combo._ds.set_today(combo._progress_key(GOLDEN_TOURNEY_DAY[1]), 100)
    assert combo.next_block(_at(2, 20))["block"] == "coin"


def test_the_plan_is_read_from_the_materialized_artefact(combo, monkeypatch):
    """CONFIG["plan"] is what materialize() installs, beside the bp_ presets.
    Reading it means the ordinary path never recompiles a profile on a
    20-second poll - and never touches playerprofile at all."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "test")
    monkeypatch.setitem(CONFIG, "plan", {"week": {"monday": GOLDEN_FARM_DAY}})

    class Landmine(types.ModuleType):
        def __getattr__(self, item):
            pytest.fail("the profile layer was touched for an installed plan")
    patch_module(monkeypatch, "playerprofile", Landmine("pp"))
    assert combo.next_block(_at(0, 12))["id"] == "monday#0"


def test_a_day_with_nothing_scheduled_idles(combo, plan):
    """No eligible block = hold. Inventing a filler is how a day off becomes an
    unplanned coin run."""
    plan({"monday": []})
    assert combo.next_block(_at(0, 12)) is None
    plan({"monday": [_block(0, after_min=8 * 60, until_min=9 * 60)]})
    assert combo.next_block(_at(0, 12)) is None
    assert combo.next_block(_at(0, 8))["id"] == "monday#0"


def test_a_plan_that_does_not_mention_today_runs_nothing(combo, plan):
    plan({"wednesday": GOLDEN_TOURNEY_DAY})
    assert combo.next_block(_at(0, 12)) is None          # Monday
    assert _ev(combo, "combo_plan_no_day")[0]["day"] == "monday"


# ------------------------------------------------- the no-defaults refusals

@pytest.mark.parametrize("key", ("id", "block", "blueprint", "preset",
                                 "kind", "after_min", "until_min", "count"))
def test_a_block_missing_any_key_is_refused(combo, plan, key):
    """EVERY key explicit. `after: null` is a decision the compiler made;
    a MISSING after is nobody deciding, and the difference between them is a
    tournament entered at the wrong hour."""
    plan({"monday": []})
    b = _block(0, after_min=8 * 60, count=5)
    b.pop(key)
    assert combo._block_ok(b) is False
    assert combo._block_eligible(b, _at(0, 12)) is False
    problems = _ev(combo, "combo_block_refused")[0]["problems"]
    assert f"missing key '{key}'" in problems


@pytest.mark.parametrize("field,value,fragment", [
    ("kind", "farming", "unknown kind"),
    ("preset", None, "preset must be a bp_ name"),
    ("preset", "bp_never_compiled", "never materialized"),
    ("after_min", "08:00", "minutes since midnight"),
    ("until_min", 9999, "minutes since midnight"),
    ("after_min", -1, "minutes since midnight"),
    ("count", "100", "whole number or null"),
    ("count", -1, "whole number or null"),
    ("count", True, "whole number or null"),
])
def test_an_unreadable_block_value_is_refused(combo, plan, field, value,
                                              fragment):
    plan({"monday": []})
    b = _block(0)
    b[field] = value
    assert combo._block_ok(b) is False
    problems = " ".join(_ev(combo, "combo_block_refused")[0]["problems"])
    assert fragment in problems


def test_a_refusal_is_logged_once_not_every_poll(combo, plan):
    """The loop asks every POLL seconds, all day. One line per block, or the
    events file becomes something nobody reads - which is the same as silence."""
    plan({"monday": []})
    b = _block(0)
    b.pop("count")
    for _ in range(50):
        combo._block_ok(b)
    assert len(_ev(combo, "combo_block_refused")) == 1


def test_refused_blocks_never_fall_back_to_the_constants(combo, plan):
    """The failure mode this whole layer exists to prevent: a plan that cannot
    be read must not quietly farm the config.yaml preset it was meant to
    replace. Every block broken = idle, not `normal_run`."""
    broken = [{"id": "monday#0", "block": "coin"}]      # missing four keys
    plan({"monday": broken})
    assert combo.next_block(_at(0, 12)) is None


# --------------------------------------------- LEGACY: bit-for-bit constants

@pytest.mark.parametrize("day", range(7))
def test_constant_era_equivalence(combo, day):
    """THE equivalence assertion: with no profile, the block scheduler and the
    original due() agree on every hour of every day of the week - including
    after the tournament closes and after the shard quota fills."""
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    for hour in range(24):
        now = _at(day, hour)
        assert combo.next_block(now)["block"] == combo.due(now), (day, hour)
    # ...and again with the tournament already done
    combo._ds.mark_today("combo_tournament")
    for hour in range(24):
        now = _at(day, hour)
        assert combo.next_block(now)["block"] == combo.due(now), (day, hour)
    # ...and with the shard block closed too
    combo._ds.mark_today("combo_shards")
    for hour in range(24):
        now = _at(day, hour)
        assert combo.next_block(now)["block"] == combo.due(now), (day, hour)


def _golden_compiled_plan():
    """The REAL profiles/default.yaml, through the REAL compiler.

    End to end on purpose: this is the artefact the scheduler is handed in
    production, not a restatement of it, so a compiler change that moves a key
    or a boundary fails here rather than at 19:00 on a Wednesday.
    """
    from player import playerprofile
    from goldens import load_golden
    return playerprofile.compile_plan(load_golden())["week"]


@pytest.mark.parametrize("day", range(7))
def test_the_golden_plan_means_what_the_constants_mean(combo, plan, day):
    """The migration's whole promise: profiles/default.yaml was written to
    reproduce the constant-era schedule, so the compiled plan and due() must
    agree hour by hour on every day of the week - Wed/Sat tournaments included.

    This is the test that would have caught a plan.week slot that resolved to
    the wrong day, or a `after: "08:00"` that compiled to the wrong boundary."""
    from settings import CONFIG
    compiled = _golden_compiled_plan()
    plan(compiled)
    for hour in range(24):
        now = _at(day, hour)
        want = combo.next_block(now)
        CONFIG.pop("active_profile", None)          # ...and the constants
        legacy = combo.due(now)
        CONFIG["active_profile"] = "test"
        assert want is not None and want["block"] == legacy, (day, hour)


def test_the_legacy_blocks_use_the_pre_p5_daystate_keys(combo):
    """A combo restarted across the P5 boundary mid-day must find its OWN
    marks. New key names would replay a tournament that already ran."""
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    blocks = {b["block"]: b for b in combo.today_blocks(_at(2, 20))}
    assert combo._closed_key(blocks["tournament"]) == "combo_tournament"
    assert combo._closed_key(blocks["shards"]) == "combo_shards"
    assert combo._progress_key(blocks["shards"]) == "shard_runs"


def test_the_shard_quota_still_switches_the_block_off(combo, monkeypatch):
    """SHARD_RUNS = 0 disables the shard block entirely - and it does so through
    the ordinary count check, not a special case."""
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    monkeypatch.setattr(combo, "SHARD_RUNS", 0)
    assert combo.next_block(_at(0, 12))["block"] == "coin"
    assert combo.due(_at(0, 12)) == "coin"


def test_no_profile_never_imports_the_profile_layer(combo, monkeypatch):
    """Unchanged from P3, restated for the plan reader: an import-time failure
    in playerprofile.py must never be able to take down a legacy combo."""
    from settings import CONFIG
    CONFIG.pop("active_profile", None)

    class Landmine(types.ModuleType):
        def __getattr__(self, item):
            raise AssertionError("playerprofile touched on the legacy path")
    patch_module(monkeypatch, "playerprofile", Landmine("pp"))
    assert combo._plan_map() is None
    assert combo.next_block(_at(0, 3))["block"] == "coin"
    assert combo._log.names() == []             # and silently, too


def test_a_profile_without_a_plan_says_so_once_then_runs_the_constants(
        combo, monkeypatch):
    """THE RULING: profile bound but no plan = the legacy constants, logged
    once as plan_absent. No profile at all = the constants, silent."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    pp = types.ModuleType("playerprofile")
    pp.PROFILE = {"blueprints": {}}                     # no plan anywhere
    patch_module(monkeypatch, "playerprofile", pp)
    for _ in range(10):
        assert combo.next_block(_at(0, 3))["block"] == "coin"
    assert combo.next_block(_at(0, 12))["block"] == "shards"   # the constants
    assert len(_ev(combo, "plan_absent")) == 1
    assert _ev(combo, "plan_absent")[0]["profile"] == "acct2"


def test_a_bound_profile_that_cannot_read_its_plan_HOLDS(combo, monkeypatch):
    """Codex P5 HIGH, the fail-open fallback. A plan that raises is a FAILURE,
    not an absence - falling back would farm the config.yaml preset the profile
    exists to replace, at the wrong tier, and say nothing. It holds instead,
    loudly, once."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    pp = types.ModuleType("playerprofile")

    def boom():
        raise RuntimeError("plan compile blew up")
    pp.compiled_plan = boom
    patch_module(monkeypatch, "playerprofile", pp)
    for _ in range(10):
        assert combo.next_block(_at(0, 3)) is None      # never 'coin'
        assert combo.today_blocks(_at(0, 3)) == []
    assert len(_ev(combo, "combo_plan_unavailable")) == 1
    assert "blew up" in _ev(combo, "combo_plan_unavailable")[0]["error"]
    with pytest.raises(combo.ProfileMissing):
        combo._plan_map()


def test_a_plan_of_the_wrong_shape_also_holds(combo, monkeypatch):
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    monkeypatch.setitem(CONFIG, "plan", ["not", "a", "mapping"])
    assert combo.next_block(_at(0, 3)) is None
    assert _ev(combo, "combo_plan_unavailable")


# ------------------------------------------------------- counters + resume

def test_a_shard_block_resumes_mid_count_after_an_abort(combo, plan):
    """The whole point of the per-block counter: a human touching the screen
    ends a shard runner at run 37, and the next spawn asks for the REMAINING
    63 - not another 100."""
    plan({"monday": GOLDEN_FARM_DAY})
    b = GOLDEN_FARM_DAY[0]
    combo._ds.set_today("shard_runs", 37)
    argv = combo._block_argv(b, "acct2")
    assert argv[argv.index("--loops") + 1] == "63"
    # ...and the block is still due, because it is only partly done
    assert combo.next_block(_at(0, 12))["id"] == b["id"]
    # closing it at 37 of 100 leaves it partial rather than marking it done
    combo._close_block(b, 0)
    assert _ev(combo, "combo_shards_partial")[0] == {
        "done": 37, "target": 100, "block": b["id"]}
    assert combo.next_block(_at(0, 12))["id"] == b["id"]


def test_a_finished_shard_block_closes_and_yields_to_the_filler(combo, plan):
    plan({"monday": GOLDEN_FARM_DAY})
    b = GOLDEN_FARM_DAY[0]
    combo._ds.set_today("shard_runs", 100)
    combo._close_block(b, 0)
    assert combo.next_block(_at(0, 12))["block"] == "coin"


def test_a_runner_that_completed_nothing_closes_the_block(combo, plan):
    """The crash guard, carried over verbatim from _close_shards: a runner that
    dies on sight must not be respawned in a loop all day."""
    plan({"monday": GOLDEN_FARM_DAY})
    b = GOLDEN_FARM_DAY[0]
    combo._ds.set_today("shard_runs", 12)
    combo._close_block(b, 12)               # no progress since it was spawned
    assert combo.next_block(_at(0, 12))["block"] == "coin"


def test_a_counted_block_counts_one_unit_per_runner_exit(combo, plan):
    """Everything that is not a shard block counts runner exits - which is
    exactly what the constant era did for the tournament (mark_done on exit,
    count 1)."""
    plan({"wednesday": GOLDEN_TOURNEY_DAY})
    b = GOLDEN_TOURNEY_DAY[0]
    assert combo.next_block(_at(2, 20))["block"] == "tournament"
    combo._close_block(b, 0)
    assert combo._block_progress(b) == 1
    assert combo.next_block(_at(2, 20))["block"] == "shards"


def test_a_multi_run_block_stays_due_until_its_count_is_filled(combo, plan):
    day = [_block(0, "monday", "quest", "quest_ilm", "cycle_quest", count=3),
           _block(1, "monday", "coin", "coin_default", "coin")]
    plan({"monday": day})
    for expected in (1, 2, 3):
        assert combo.next_block(_at(0, 12))["id"] == "monday#0"
        combo._close_block(day[0], 0)
        assert combo._block_progress(day[0]) == expected
    assert combo.next_block(_at(0, 12))["block"] == "coin"


def test_an_unbounded_block_is_never_closed_on_exit(combo, plan):
    """The coin filler respawns forever - a crashed coin runner must not close
    the only block that has nowhere to fall back to."""
    plan({"monday": GOLDEN_FARM_DAY})
    filler = GOLDEN_FARM_DAY[1]
    combo._close_block(filler, 0)
    assert combo._block_progress(filler) == 0
    assert combo.next_block(_at(0, 3))["block"] == "coin"


def test_per_block_counters_do_not_collide(combo, plan):
    """Two blocks of the SAME kind AND the same blueprint keep separate
    counters, because the key is the STABLE BLOCK ID - not the kind, not the
    preset. Same kind is the version that actually proves it: two different
    kinds would pass even if the counter were keyed by kind.

    Deliberately NOT two tournament blocks - that artifact is unconstructible
    (the compiler refuses it, and _block_eligible's day lock refuses it again);
    see test_two_tournament_blocks_cannot_both_enter for that property.
    """
    day = [_block(0, "monday", "quest", "quest_ilm", "cycle_quest", count=1),
           _block(1, "monday", "quest", "quest_ilm", "cycle_quest",
                  after_min=21 * 60, count=1),
           _block(2, "monday", "coin", "coin_default", "coin")]
    plan({"monday": day})
    assert combo._progress_key(day[0]) != combo._progress_key(day[1])
    assert combo._closed_key(day[0]) != combo._closed_key(day[1])
    combo._close_block(day[0], 0)
    assert combo._block_progress(day[0]) == 1
    assert combo._block_progress(day[1]) == 0       # untouched by its twin
    assert combo.next_block(_at(0, 22))["id"] == "monday#1"


# ---------------------------------------------------------------- adoption

def _fake_psutil(cmdlines):
    m = types.ModuleType("psutil")
    procs = [types.SimpleNamespace(info={"pid": 1000 + i, "cmdline": cl})
             for i, cl in enumerate(cmdlines)]
    m.process_iter = lambda attrs=None: procs
    m.Process = lambda pid: types.SimpleNamespace(
        pid=pid, is_running=lambda: True, terminate=lambda: None,
        kill=lambda: None, wait=lambda timeout=None: 0)
    return m


@pytest.mark.parametrize("idx,cmdline,adopt", [
    (0, ["python", "orchestrator.py", "--instance", "acct2",
         "--preset", "bp_tourney_main"], True),
    (0, ["python", "orchestrator.py", "--instance", "acct2",
         "--preset", "bp_coin_default"], False),
    (1, ["python", "flows/shard.py", "--instance", "acct2", "--loops", "63",
         "--preset", "bp_shard_daily"], True),
    (1, ["python", "flows/shard.py", "--instance", "acct2", "--loops", "63"], False),
    (2, ["python", "orchestrator.py", "--instance", "acct2",
         "--preset", "bp_coin_default"], True),
    (2, ["python", "orchestrator.py", "--instance", "other",
         "--preset", "bp_coin_default"], False),
    (2, ["python", "orchestrator.py", "--instance", "acct2"], False),
])
def test_adoption_recognises_the_blueprint_argv(combo, plan, monkeypatch, idx,
                                                cmdline, adopt):
    """A combo restart must adopt a runner IT SPAWNED before the restart - so
    the matcher and the spawner have to agree about the argv in the same
    commit. `--preset bp_<blueprint>` is that token.

    Note the shard row: unlike the constant era, a plan shard block HAS a token
    of its own, so a bare `flows/shard.py --loops 63` is no longer adopted as one -
    it could belong to any shard blueprint, and adopting the wrong one would
    farm the wrong tier."""
    plan({"wednesday": GOLDEN_TOURNEY_DAY})
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil([cmdline]))
    b = GOLDEN_TOURNEY_DAY[idx]
    found = combo._find_running_block(b, "acct2", GOLDEN_TOURNEY_DAY)
    assert (found is not None) is adopt


def test_the_spawned_argv_is_what_adoption_matches(combo, plan, monkeypatch):
    """Spawn it, then look for it: the round trip is the property that a
    restart depends on, and it is easy to break one half at a time."""
    plan({"wednesday": GOLDEN_TOURNEY_DAY})
    for b in GOLDEN_TOURNEY_DAY:
        argv = combo._block_argv(b, "acct2")
        monkeypatch.setitem(sys.modules, "psutil",
                            _fake_psutil([["python"] + argv]))
        assert combo._find_running_block(b, "acct2",
                                         GOLDEN_TOURNEY_DAY) is not None


@pytest.mark.parametrize("kind,script,flag,value", [
    ("shard", "flows/shard.py", "--loops", "100"),
    ("uw_grant_quest", "flows/quest_sm.py", "--rides", "2"),
    ("cycle_quest", "flows/quest_ilm.py", "--cycles", "40"),
])
def test_each_kind_spawns_its_own_runner(combo, plan, kind, script, flag,
                                         value):
    from settings import CONFIG
    plan({"monday": []})
    CONFIG["presets"]["bp_k"] = {"kind": kind, "tier": None, "rides": 2,
                                 "cycles": 40, "loadout": "coin_farm"}
    b = _block(0, "monday", "quest", "k", kind, count=100)
    argv = combo._block_argv(b, "acct2")
    assert argv[0] == script
    assert argv[argv.index("--preset") + 1] == "bp_k"
    assert argv[argv.index(flag) + 1] == value


def test_a_shard_block_with_no_count_runs_unbounded(combo, plan):
    from settings import CONFIG
    plan({"monday": []})
    CONFIG["presets"]["bp_s"] = {"kind": "shard", "tier": 18}
    b = _block(0, "monday", "shards", "s", "shard")
    argv = combo._block_argv(b, "acct2")
    assert argv[argv.index("--loops") + 1] == "0"        # flows/shard.py's forever
    assert argv[argv.index("--tier") + 1] == "18"


# ----------------------------------------------------------------- handoff

def test_a_plan_handoff_uses_the_blueprints_own_loadout_and_tier(combo, plan,
                                                                 monkeypatch):
    """Not the phase->name table the constants use. Naming a blueprint in the
    plan is pointless if the handoff equips something else."""
    from settings import CONFIG
    plan({"monday": []})
    CONFIG["presets"]["bp_c"] = {"kind": "coin", "loadout": "tourney_1",
                                 "tier": 11}
    calls = []
    for name, mod in (("loadout", "apply"), ("shard", "set_tier"),
                      ("shard", "start_battle"), ("tourney", "ensure_home")):
        pass
    lo = types.ModuleType("loadout")
    lo.apply = lambda n: calls.append(("loadout", n))
    sh = types.ModuleType("shard")
    sh.set_tier = lambda t: calls.append(("tier", t))
    sh.start_battle = lambda: calls.append(("battle",))
    tny = types.ModuleType("tourney")
    tny.Abort = type("Abort", (Exception,), {})
    tny.ensure_home = lambda: calls.append(("home",))
    for n, m in (("loadout", lo), ("tourney", tny)):
        patch_module(monkeypatch, n, m)
    _patch_flows_shard(monkeypatch, sh)
    combo._block_handoff(_block(0, blueprint="c"), "acct2")
    assert ("loadout", "tourney_1") in calls and ("tier", 11) in calls
    assert calls[-1] == ("battle",)


def test_a_quest_block_is_handed_off_by_its_own_runner(combo, plan,
                                                       monkeypatch):
    """flows/quest_sm.py / flows/quest_ilm.py own their setup end to end and adopt an
    in-progress run on startup - walking the game Home underneath one takes
    work away rather than adding it."""
    from settings import CONFIG
    plan({"monday": []})
    CONFIG["presets"]["bp_q"] = {"kind": "uw_grant_quest"}

    class Landmine(types.ModuleType):
        def __getattr__(self, item):
            pytest.fail("a quest handoff touched the game")
    for n in ("loadout", "tourney"):
        patch_module(monkeypatch, n, Landmine(n))
    _patch_flows_shard(monkeypatch, Landmine("shard"))
    combo._block_handoff(_block(0, blueprint="q", kind="uw_grant_quest"),
                         "acct2")
    assert _ev(combo, "combo_handoff_skipped")[0]["kind"] == "uw_grant_quest"


def test_a_blueprint_with_no_loadout_or_tier_refuses_the_handoff(combo, plan,
                                                                 monkeypatch):
    """No guessing here either. Farming the previous block's tier because this
    one forgot to say is how a T18 shard build ends up on a T14 coin run."""
    from settings import CONFIG
    plan({"monday": []})
    CONFIG["presets"]["bp_bare"] = {"kind": "coin"}
    tny = types.ModuleType("tourney")
    tny.Abort = type("Abort", (Exception,), {})
    tny.ensure_home = lambda: None
    for n, m in (("loadout", types.ModuleType("loadout")),
                 ("tourney", tny)):
        patch_module(monkeypatch, n, m)
    _patch_flows_shard(monkeypatch, types.ModuleType("shard"))
    with pytest.raises(combo.ProfileMissing):
        combo._block_handoff(_block(0, blueprint="bare"), "acct2")


# ============================ Codex P5 audit fixes ============================

# ---- CRITICAL: ONE TOURNAMENT PER DAY, across every block and both eras

def test_two_tournament_blocks_cannot_both_enter(combo, plan):
    """A ticket purchase AUTO-STARTS the run and the next entry costs
    10 -> 20 -> 30 gems, so "how many tournaments today" is not a number a plan
    gets to choose. Two `count: 1` blocks each saw their own counter at zero
    and each spent a ticket."""
    day = [_block(0, "monday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1),
           _block(1, "monday", "tournament", "tourney_main", "tournament",
                  after_min=21 * 60, count=1),
           _block(2, "monday", "coin", "coin_default", "coin")]
    plan({"monday": day})
    assert combo.next_block(_at(0, 20))["id"] == "monday#0"
    combo._close_block(day[0], 0)                   # the first one runs
    assert combo.next_block(_at(0, 22))["block"] == "coin"
    assert combo._tournament_taken(_at(0, 22), day) == "combo_tournament"


def test_a_legacy_tournament_that_morning_closes_the_compiled_one(combo, plan):
    """MIGRATION DAY. The constants entered a tournament at 19:00 and the
    operator switched to a profile at 19:30; the compiled block must honour the
    entry that was already paid for."""
    day = [_block(0, "monday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1),
           _block(1, "monday", "coin", "coin_default", "coin")]
    plan({"monday": day})
    combo._ds.mark_today("combo_tournament")        # the constant era's flag
    assert combo.next_block(_at(0, 20))["block"] == "coin"


def test_a_compiled_entry_marks_the_shared_flag(combo, plan):
    """...and the other direction: a plan-era entry writes `combo_tournament`
    too, so a combo that falls BACK to the constants cannot have another turn.
    """
    day = [_block(0, "wednesday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1),
           _block(1, "wednesday", "coin", "coin_default", "coin")]
    plan({"wednesday": day})
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    assert combo.due(_at(2, 20)) == "tournament"    # a Wednesday, unspent
    CONFIG["active_profile"] = "test"
    combo._close_block(day[0], 0)                   # the plan era enters one
    assert combo._ds.flag_today("combo_tournament")
    CONFIG.pop("active_profile", None)
    assert combo.due(_at(2, 20)) == "shards"        # ...and the constants agree


def test_a_tournament_counter_alone_closes_the_day(combo, plan):
    """Evidence (b): a block whose closed-flag write was lost still shows its
    counter, and that is enough."""
    day = [_block(0, "monday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1),
           _block(1, "monday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1),
           _block(2, "monday", "coin", "coin_default", "coin")]
    plan({"monday": day})
    combo._ds.set_today(combo._progress_key(day[0]), 1)
    assert combo._tournament_taken(_at(0, 20), day) == "monday#0 ran 1"
    assert combo.next_block(_at(0, 20))["block"] == "coin"


@pytest.mark.parametrize("count", [None, 2, 0])
def test_a_tournament_block_that_is_not_count_one_is_refused(combo, plan,
                                                             count):
    """Defence in depth under the compiler's own daily cap: `count: 2` is a
    request to spend gems twice and `count: null` is a request to spend them
    until the day ends."""
    plan({"monday": []})
    b = _block(0, "monday", "tournament", "tourney_main", "tournament",
               after_min=19 * 60, count=count)
    assert combo._block_ok(b) is False
    assert "must be count 1" in " ".join(
        _ev(combo, "combo_block_refused")[0]["problems"])


def test_the_tournament_is_re_checked_immediately_before_the_gems(combo, plan):
    """The eligibility check ran a poll ago; an adopted foreign runner may have
    closed the day in between. _tournament_taken is asked again at the spawn
    site - cheap, and the mistake is not."""
    day = [_block(0, "monday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1)]
    plan({"monday": day})
    assert combo._tournament_taken(_at(0, 20), day) is None
    combo._ds.mark_today("combo_tournament")
    assert combo._tournament_taken(_at(0, 20), day) == "combo_tournament"


# ---- HIGH: legacy runners are FOREIGN WORK in plan mode

@pytest.mark.parametrize("cmdline,block", [
    (["python", "orchestrator.py", "--instance", "acct2", "--preset", "normal_run"],
     "coin"),
    (["python", "orchestrator.py", "--instance", "acct2", "--preset", "tournament"],
     "tournament"),
    (["python", "flows/shard.py", "--instance", "acct2", "--loops", "100"],
     "shards"),
])
def test_a_pre_migration_runner_is_adopted_not_navigated_under(combo, plan,
                                                               monkeypatch,
                                                               cmdline, block):
    """Codex P5 HIGH. With a plan bound, discovery matched `bp_` tokens only -
    so a runner still farming from before the switchover was invisible and the
    scheduler walked the game Home underneath a live run. That is the one thing
    this mode promises not to do, and the runner's era does not change it."""
    plan({"monday": GOLDEN_FARM_DAY})
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil([cmdline]))
    foreign = {b["block"]: b for b in combo.foreign_legacy_blocks()}
    b = foreign[block]
    assert combo._find_running_block(b, "acct2", list(foreign.values())) \
        is not None
    assert b["foreign"] is True


def test_foreign_blocks_are_adoptable_but_never_schedulable(combo, plan):
    """They are something to FINISH, not something to schedule: next_block only
    ever walks today_blocks(), which is the plan."""
    plan({"monday": GOLDEN_FARM_DAY})
    ids = {b["id"] for b in combo.today_blocks(_at(0, 12))}
    assert not any(i.startswith("foreign#") for i in ids)
    assert combo.next_block(_at(0, 12))["id"] in ids


def test_a_foreign_matcher_does_not_steal_the_plans_own_runner(combo, plan,
                                                               monkeypatch):
    """The trap this avoids: _phase_tokens() adds the kind-mapped `bp_` name
    when a profile is loaded, so a foreign coin block would have matched the
    PLAN's own coin runner and stopped it a moment after starting it. Foreign
    blocks carry the literal legacy names instead."""
    plan({"monday": GOLDEN_FARM_DAY})
    monkeypatch.setattr(combo, "_blueprint", lambda ph: "coin_default")
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(
        [["python", "orchestrator.py", "--instance", "acct2",
          "--preset", "bp_coin_default"]]))
    foreign = combo.foreign_legacy_blocks()
    for b in foreign:
        assert combo._find_running_block(b, "acct2", foreign) is None


def test_legacy_mode_adoption_is_untouched(combo, monkeypatch):
    """And with NO plan, adoption still goes through _find_running(phase) -
    the original code, including its kind-mapped token."""
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    assert combo._plan_mode() is False
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(
        [["python", "orchestrator.py", "--instance", "acct2",
          "--preset", "normal_run"]]))
    coin = [b for b in combo.today_blocks(_at(0, 12))
            if b["block"] == "coin"][0]
    assert combo._find_running_block(coin, "acct2") is not None


# ---- MEDIUM: midnight continuity

def test_an_unbounded_farm_continues_across_midnight(combo):
    """Block ids carry the weekday, so an overnight coin farm's id changes at
    00:00 though nothing about the work did. Stopping and respawning there
    costs a run boundary and a full handoff for a rename."""
    sun = _block(1, "sunday", "coin", "coin_default", "coin")
    mon = _block(1, "monday", "coin", "coin_default", "coin")
    assert sun["id"] != mon["id"]
    assert combo._same_work(sun, mon) is True


def test_continuity_survives_a_different_index_on_the_new_day(combo):
    """THE INDEX MOVES, and it moves on exactly the nights this matters. The
    coin filler is last in its day's list, so it is `saturday#2` on a
    tournament day and `tuesday#1` on a farm day - a Saturday-night farm
    crossing into Sunday changes BOTH halves of the id. Identity is the work,
    never the position."""
    sat = _block(2, "saturday", "coin", "coin_default", "coin")
    sun = _block(1, "sunday", "coin", "coin_default", "coin")
    assert sat["id"] == "saturday#2" and sun["id"] == "sunday#1"
    assert combo._same_work(sat, sun) is True


def test_continuity_rebinds_the_counter_to_the_new_days_id(combo, plan):
    """...and the counter follows the new id, which is what keeps a per-day
    quota per-day: yesterday's progress must not be readable as today's."""
    sat = _block(2, "saturday", "coin", "coin_default", "coin")
    sun = _block(1, "sunday", "coin", "coin_default", "coin")
    combo._ds.set_today(combo._progress_key(sat), 5)
    assert combo._block_progress(sat) == 5
    assert combo._block_progress(sun) == 0          # a fresh key for a new day


@pytest.mark.parametrize("a,b,same", [
    # a bounded block ALWAYS re-evaluates: a count is per-day by design, and
    # continuing would carry a spent quota across the rollover
    (dict(count=100), dict(count=100), False),
    (dict(count=None), dict(count=100), False),
    (dict(count=100), dict(count=None), False),
    # different work is different work
    (dict(blueprint="coin_default"), dict(blueprint="coin_t19"), False),
    (dict(kind="coin"), dict(kind="shard"), False),
    (dict(), dict(), True),
])
def test_continuity_requires_identical_unbounded_work(combo, a, b, same):
    assert combo._same_work(_block(0, "sunday", **a),
                            _block(0, "monday", **b)) is same


def test_foreign_work_is_never_continued(combo, plan):
    """A foreign runner is finished, not continued - it is the previous era's,
    and the whole point of adopting it is to let it end."""
    plan({"monday": GOLDEN_FARM_DAY})
    foreign = [b for b in combo.foreign_legacy_blocks()
               if b["block"] == "coin"][0]
    assert combo._same_work(foreign, GOLDEN_FARM_DAY[1]) is False


# ---- MEDIUM: the synthetic-legacy done-state divergence

def test_a_filled_shard_counter_without_the_flag_matches_due(combo):
    """Codex P5 MEDIUM. due() asks `not done_today("shards")` and never looks
    at the counter, so shard_runs == 100 with combo_shards unset still reads
    "shards" there - a real state, reached whenever a quota fills before
    _close_shards runs. Counting here instead made the two disagree."""
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    combo._ds.set_today("shard_runs", 100)
    for hour in range(24):
        now = _at(0, hour)
        assert combo.next_block(now)["block"] == combo.due(now), hour
    assert combo.next_block(_at(0, 12))["block"] == "shards"


@pytest.mark.parametrize("runs", [0, 37, 100, 250])
@pytest.mark.parametrize("flag", [False, True])
def test_legacy_equivalence_holds_across_every_counter_state(combo, runs,
                                                             flag):
    from settings import CONFIG
    CONFIG.pop("active_profile", None)
    combo._ds.set_today("shard_runs", runs)
    if flag:
        combo._ds.mark_today("combo_shards")
    for day in range(7):
        for hour in range(24):
            now = _at(day, hour)
            assert combo.next_block(now)["block"] == combo.due(now), (day, hour)


# ---- LOW: canonical weekday keys only

def test_only_the_canonical_weekday_name_is_read(combo, plan):
    """Accepting `mon` and `0` alongside `monday` meant a plan carrying two of
    them had a PRECEDENCE rather than an error, and precedence in a scheduler
    is a silently different week."""
    plan({"mon": GOLDEN_FARM_DAY})
    assert combo.next_block(_at(0, 12)) is None
    assert _ev(combo, "combo_plan_no_day")[0]["day"] == "monday"
    plan({0: GOLDEN_FARM_DAY})
    assert combo.next_block(_at(0, 12)) is None


def test_an_alias_never_shadows_the_canonical_day(combo, plan):
    tourney = list(GOLDEN_TOURNEY_DAY)
    plan({"monday": GOLDEN_FARM_DAY, "mon": tourney, 0: tourney})
    assert combo.next_block(_at(0, 20))["id"] == "monday#0"    # shards, not #0
    assert combo.next_block(_at(0, 20))["block"] == "shards"


# ========================= P5b gate: the two NOT rows =========================

# ---- 1. plan ABSENT (the profile never spoke) vs plan EMPTY (it compiled to
#         nothing). The first is a documented preference; the second can only
#         be an authoring or compile accident.

def test_a_profile_that_never_spoke_about_scheduling_uses_the_constants(
        combo, monkeypatch):
    """THE HALF THAT STANDS. A rules-only profile must not change what the day
    runs, so a genuinely missing plan is the one bound-profile case that
    reaches the legacy constants - said once, then silent."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    CONFIG.pop("plan", None)
    pp = types.ModuleType("playerprofile")
    pp.PROFILE = {"blueprints": {}}                  # no plan, no helper
    patch_module(monkeypatch, "playerprofile", pp)
    assert combo._plan_map() is None
    assert combo.next_block(_at(0, 3))["block"] == "coin"
    assert len(_ev(combo, "plan_absent")) == 1
    assert _ev(combo, "combo_plan_unavailable") == []


@pytest.mark.parametrize("artefact,fragment", [
    ({}, "schedules NOTHING"),
    ({"week": {}}, "schedules NOTHING"),
    ({"week": {d: [] for d in ("monday", "tuesday", "wednesday", "thursday",
                               "friday", "saturday", "sunday")}},
     "EVERY day is empty"),
])
def test_a_plan_that_compiled_to_nothing_HOLDS(combo, monkeypatch, artefact,
                                               fragment):
    """PRESENT AND EMPTY IS UNREADABLE, not absent. Something compiled the
    schedule down to nothing; reading that as "run the constants" would farm
    the config.yaml preset the profile exists to replace and say nothing about
    it. It holds, exactly as an exception does."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    monkeypatch.setitem(CONFIG, "plan", artefact)
    for _ in range(5):
        assert combo.next_block(_at(0, 3)) is None       # never 'coin'
        assert combo.today_blocks(_at(0, 3)) == []
    assert len(_ev(combo, "combo_plan_unavailable")) == 1
    assert _ev(combo, "plan_absent") == []
    with pytest.raises(combo.ProfileMissing) as e:
        combo._plan_map()
    assert fragment in str(e.value)


def test_a_helper_that_returns_None_means_absence(combo, monkeypatch):
    """ABSENCE PROPAGATES (P5c, branch 1). compile_plan() returns None for a
    profile with no plan section, so a helper answering None is that profile
    saying it never spoke about scheduling - and the rules-only farm must farm.
    """
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    CONFIG.pop("plan", None)
    pp = types.ModuleType("playerprofile")
    pp.compiled_plan = lambda: None
    patch_module(monkeypatch, "playerprofile", pp)
    assert combo._plan_map() is None
    assert combo.next_block(_at(0, 3))["block"] == "coin"
    assert len(_ev(combo, "plan_absent")) == 1
    assert _ev(combo, "combo_plan_unavailable") == []


def test_a_helper_that_raises_holds_rather_than_falling_back(combo,
                                                             monkeypatch):
    """The other half of the same branch: only a helper that RETURNS may say
    "there is no plan". One that raises is a failure, and a failure holds."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    CONFIG.pop("plan", None)
    pp = types.ModuleType("playerprofile")

    def boom():
        raise RuntimeError("half-written profile")
    pp.compiled_plan = boom
    patch_module(monkeypatch, "playerprofile", pp)
    with pytest.raises(combo.ProfileMissing):
        combo._plan_map()
    assert combo.next_block(_at(0, 3)) is None
    assert _ev(combo, "combo_plan_unavailable")
    assert _ev(combo, "plan_absent") == []


def test_a_present_but_null_plan_key_is_a_defect_not_an_absence(combo,
                                                                monkeypatch):
    """P5c, branch 2. materialize() is the only writer of CONFIG['plan'] and it
    never writes None, so the key existing with no value is a defective
    artefact - a half-finished write, a hand-edit, a stub. Absence is the key
    being ABSENT; a null in it is something else, and it holds."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    monkeypatch.setitem(CONFIG, "plan", None)

    class Landmine(types.ModuleType):
        def __getattr__(self, item):
            pytest.fail("a present key must not send us hunting for helpers")
    patch_module(monkeypatch, "playerprofile", Landmine("pp"))
    with pytest.raises(combo.ProfileMissing) as e:
        combo._plan_map()
    assert "present but null" in str(e.value)
    for _ in range(5):
        assert combo.next_block(_at(0, 3)) is None      # never 'coin'
        assert combo.today_blocks(_at(0, 3)) == []
    assert len(_ev(combo, "combo_plan_unavailable")) == 1
    assert _ev(combo, "plan_absent") == []


def test_the_two_nulls_are_told_apart(combo, monkeypatch):
    """The distinction in one place: the SAME value, None, means absence when a
    helper returns it and a defect when the config key holds it."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    pp = types.ModuleType("playerprofile")
    pp.compiled_plan = lambda: None
    patch_module(monkeypatch, "playerprofile", pp)
    CONFIG.pop("plan", None)
    assert combo._plan_map() is None                    # absence -> constants
    monkeypatch.setitem(CONFIG, "plan", None)
    with pytest.raises(combo.ProfileMissing):           # defect -> hold
        combo._plan_map()


def test_a_helper_that_answers_with_nothing_also_holds(combo, monkeypatch):
    """The same rule one layer out: a helper that RETURNS `{}` answered the
    question - with nothing. Only a helper that is not there at all, and a
    PROFILE with no plan, count as never having spoken."""
    from settings import CONFIG
    monkeypatch.setitem(CONFIG, "active_profile", "acct2")
    CONFIG.pop("plan", None)
    pp = types.ModuleType("playerprofile")
    pp.compiled_plan = lambda: {}
    patch_module(monkeypatch, "playerprofile", pp)
    assert combo.next_block(_at(0, 3)) is None
    assert _ev(combo, "combo_plan_unavailable")


def test_one_empty_day_is_still_a_legal_day_off(combo, plan):
    """The line is drawn at the WEEK, not the day: a single empty day is a
    deliberate day off and still idles quietly, without the alarm."""
    plan({"monday": [], "tuesday": GOLDEN_FARM_DAY})
    assert combo.next_block(_at(0, 12)) is None
    assert _ev(combo, "combo_plan_unavailable") == []
    assert combo.next_block(_at(1, 12))["id"] == "monday#0"


# ---- 2. foreign legacy work is EVERY runner shape, EVERY day

def test_a_saturday_tournament_crossing_into_sunday_is_still_seen(combo, plan,
                                                                  monkeypatch):
    """THE CROSSOVER (audit P5b). The foreign set used to be derived from what
    TODAY would start, so on a Sunday it offered shards and coin only - and a
    Saturday-night legacy TOURNAMENT runner still on screen at 00:30 was
    invisible. The plan's handoff would then walk the game Home under a live
    tournament run: hard rule 3 broken in order to break hard rule 2."""
    plan({"sunday": GOLDEN_FARM_DAY})
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(
        [["python", "orchestrator.py", "--instance", "acct2",
          "--preset", "tournament"]]))
    foreign = combo.foreign_legacy_blocks()
    tourney = [b for b in foreign if b["block"] == "tournament"][0]
    assert combo._find_running_block(tourney, "acct2", foreign) is not None
    # ...and it is NOT schedulable - Sunday has no tournament block at all
    assert "tournament" not in {b["block"]
                                for b in combo.today_blocks(_at(6, 0, 30))}


@pytest.mark.parametrize("day", range(7))
@pytest.mark.parametrize("cmdline,block", [
    (["python", "orchestrator.py", "--instance", "acct2", "--preset", "normal_run"],
     "coin"),
    (["python", "orchestrator.py", "--instance", "acct2", "--preset", "tournament"],
     "tournament"),
    (["python", "flows/shard.py", "--instance", "acct2", "--loops", "100"],
     "shards"),
    (["python", "flows/quest_sm.py", "--instance", "acct2", "--rides", "1"],
     "quest_sm"),
    (["python", "flows/quest_ilm.py", "--instance", "acct2", "--cycles", "40"],
     "quest_ilm"),
])
def test_every_legacy_runner_shape_is_foreign_on_every_day(combo, plan,
                                                           monkeypatch, day,
                                                           cmdline, block):
    """Foreign detection is about what MIGHT BE RUNNING - every runner this
    project has ever spawned - not about what today's plan would start. The
    quest runners bind their preset internally and take no `--preset`, so their
    SCRIPT is the identification."""
    plan({combo.WEEKDAY_NAMES[day]: GOLDEN_FARM_DAY})
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil([cmdline]))
    foreign = combo.foreign_legacy_blocks()
    b = [x for x in foreign if x["block"] == block][0]
    assert combo._find_running_block(b, "acct2", foreign) is not None
    # ...and no OTHER foreign shape claims the same process
    others = [x for x in foreign
              if x["block"] != block
              and combo._find_running_block(x, "acct2", foreign) is not None]
    assert others == [], [x["id"] for x in others]


def test_the_foreign_set_does_not_depend_on_the_date(combo):
    ids = {b["id"] for b in combo.foreign_legacy_blocks()}
    assert ids == {"foreign#coin", "foreign#tournament", "foreign#shards",
                   "foreign#quest_sm", "foreign#quest_ilm"}


def test_a_finished_foreign_tournament_spends_the_day(combo, plan):
    """Closing an adopted constant-era tournament writes the SAME legacy flag
    the constants would have - so the plan's own tournament block is then
    refused. Without it the crossover case would end in two entries."""
    day = [_block(0, "sunday", "tournament", "tourney_main", "tournament",
                  after_min=19 * 60, count=1),
           _block(1, "sunday", "coin", "coin_default", "coin")]
    plan({"sunday": day})
    assert combo.next_block(_at(6, 20))["block"] == "tournament"
    foreign = [b for b in combo.foreign_legacy_blocks()
               if b["block"] == "tournament"][0]
    combo._close_block(foreign, 0)
    assert combo._ds.flag_today("combo_tournament")
    assert combo.next_block(_at(6, 20))["block"] == "coin"


def test_an_adopted_foreign_shard_runner_closes_the_legacy_way(combo, plan):
    """The other block that owns legacy daily state: a foreign shard runner is
    closed by _close_shards's own two conditions, against flows/shard.py's counter."""
    plan({"monday": GOLDEN_FARM_DAY})
    foreign = [b for b in combo.foreign_legacy_blocks()
               if b["block"] == "shards"][0]
    assert combo._progress_key(foreign) == "shard_runs"
    assert combo._closed_key(foreign) == "combo_shards"
    combo._ds.set_today("shard_runs", 37)
    combo._close_block(foreign, 0)                      # partial: stays open
    assert not combo._ds.flag_today("combo_shards")
    combo._ds.set_today("shard_runs", 100)
    combo._close_block(foreign, 37)
    assert combo._ds.flag_today("combo_shards")


def test_foreign_blocks_pass_admission(combo, plan):
    """They go through _block_ok in the adoption sweep, so a malformed one
    would be skipped and the runner would stay invisible."""
    plan({"monday": []})
    for b in combo.foreign_legacy_blocks():
        assert combo._block_problems(b) == [], b["id"]
