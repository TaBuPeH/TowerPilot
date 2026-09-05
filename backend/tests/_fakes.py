"""Shared test fakes for the runner suites.

Extracted verbatim from the patch-era test_p3_runtime.py (which tested the
hunk-application pipeline and did not ship with tower_pilot): every
screen-touching module the orchestrator/combo/shard runtime imports is
replaced here, so nothing in the suite captures, taps or talks to adb.

`settings` is deliberately REAL: the tests are about how the code reads the
actual config.yaml presets.
"""
import ast
import types


class FakeLogger(types.ModuleType):
    def __init__(self):
        super().__init__("logger")
        self.events = []
        self.shots = []

    def event(self, name, **kw):
        self.events.append((name, kw))

    def shot(self, frame, tag):
        self.shots.append(tag)
        return f"shot/{tag}.png"

    def names(self):
        return [n for n, _ in self.events]


class TapRefused(Exception):
    pass


class Abort(Exception):
    pass


def _fake_act(rec):
    m = types.ModuleType("act")
    m.TapRefused = TapRefused

    def tap(x, y, reason="", instant=False, **kw):
        rec.append((x, y, reason))
        return {"x": x, "y": y}
    m.tap = tap
    return m


def _install_fakes(rec_taps):
    """Replace every module the runtime touches for real work.

    `settings` is deliberately REAL: the tests are about how the code reads
    the actual config.yaml presets. It is seeded into sys.modules first so a
    stray copy of settings.py is never the one that gets imported.
    """
    import settings                                    # the real one
    log = FakeLogger()
    mods = {"settings": settings, "logger": log, "act": _fake_act(rec_taps)}

    cap = types.ModuleType("capture")
    cap.CaptureError = type("CaptureError", (Exception,), {})
    cap.grab = lambda: "FRAME"
    cap.layout_offset = 0
    cap.roi = lambda frame, name: None
    mods["capture"] = cap

    adb = types.ModuleType("adbclient")
    adb.screencap = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no adb in tests"))
    mods["adbclient"] = adb

    det = types.ModuleType("detect")
    det.hp_fill = lambda frame: 1.0
    det.wall_fill = lambda frame: 1.0
    det.wall_state = lambda frame: "normal"
    det.wall_overheal = lambda frame: (1.0, "normal")
    det.death_screen = lambda frame: (False, None)
    det.second_wind_badge = lambda frame: (False, 0.0)
    det.floating_gem = lambda frame: None
    det.side_menu_open = lambda frame: False
    det.intro_sprint_active = lambda frame: False
    det.find_intro_sprint = lambda frame: None
    det.button_state = lambda frame, name: types.SimpleNamespace(
        present=False, center=None, ready=False, score=0.0)
    det.button_border_val = lambda frame, name: None
    det.bar_fill = lambda frame, roi: 0.0
    det._match = lambda *a, **k: (False, 0.0, (0, 0))
    det._tpl = lambda rel: None
    mods["detect"] = det

    scr = types.ModuleType("screen")
    scr.in_tournament = lambda frame: False
    scr.identify = lambda frame: types.SimpleNamespace(name="battle",
                                                       score=1.0)
    scr.RECOVERABLE = ()
    scr._match = lambda *a, **k: (0.0, (0, 0))
    mods["screen"] = scr

    wr = types.ModuleType("wave_reader")
    wr.read_wave = lambda frame: 100
    wr.WaveTracker = lambda: types.SimpleNamespace(
        last=100, update=lambda w: None)
    mods["wave_reader"] = wr

    ds = types.ModuleType("daystate")
    ds._d = {}
    ds.get_raw = lambda k, d=None: ds._d.get(k, d)
    ds.set_raw = lambda k, v: ds._d.__setitem__(k, v)
    ds.get_today = lambda k: 0
    ds.set_today = lambda k, v: None
    ds.flag_today = lambda k: False
    ds.mark_today = lambda k: None
    mods["daystate"] = ds

    rf = types.ModuleType("runflag")
    rf.requested = lambda: None
    rf.request = lambda reason="": rf.__dict__.setdefault(
        "requests", []).append(reason)
    rf.clear = lambda: None
    mods["runflag"] = rf

    miss = types.ModuleType("missions")
    miss.quests_badge = lambda frame: False
    miss.guild_badge = lambda frame: False
    miss.Mission = lambda: types.SimpleNamespace(
        active=False, start=lambda f: None, step=lambda f: None)
    miss.find_skip = lambda frame: None
    miss.RETURN_STRIP = (0, 0)
    miss.last_guild_claims = 0
    mods["missions"] = miss

    shop = types.ModuleType("shopper")
    shop.Shopper = lambda preset: types.SimpleNamespace(
        active=False, finished=False, maxed=set(), start=lambda: None,
        step=lambda f: None, abort=lambda: None)
    shop.uw_toggle = lambda uw, want_on=True: True
    mods["shopper"] = shop

    lo = types.ModuleType("loadout")
    lo.calls = []
    lo.apply_cards = lambda p: lo.calls.append(("cards", p))
    lo.apply = lambda n, **k: lo.calls.append(("apply", n))
    lo.apply_modules = lambda plan: lo.calls.append(("modules", plan))
    lo.spec = lambda n: {"cards": "main_farm", "modules": []}
    mods["loadout"] = lo

    tny = types.ModuleType("tourney")
    tny.Abort = Abort
    tny.find = lambda *a, **k: None
    tny.require = lambda *a, **k: ("FRAME", (0, 0))
    tny.tap_at = lambda pt, why="": rec_taps.append((pt, why))
    tny.ensure_home = lambda: None
    tny.on_home = lambda frame: False
    tny.open_nav = lambda *a, **k: None
    tny.return_to_game = lambda *a, **k: None
    tny.NAV = {}
    mods["tourney"] = tny

    for name in ("store", "runlog", "chores", "psutil"):
        if name == "psutil":
            continue
        m = types.ModuleType(name)
        m.store_flow = lambda: None
        m.collect = lambda inst: None
        m.run_due = lambda: False
        mods[name] = m
    return mods, log, lo


# Where each fakeable module lives now that the backend is foldered by type.
# `from <pkg> import <name>` resolves the PACKAGE ATTRIBUTE first and the
# `<pkg>.<name>` sys.modules entry second, so a fake must be installed in
# both places (and under the bare name, for anything unpackaged).
_PKG = {
    "logger": "runtime", "runlog": "runtime",
    "act": "device", "capture": "device", "adbclient": "device",
    "detect": "vision", "screen": "vision", "wave_reader": "vision",
    "daystate": "scheduling", "runflag": "scheduling", "chores": "scheduling",
    "missions": "interactions", "shopper": "interactions",
    "loadout": "interactions", "tourney": "interactions",
    "store": "interactions",
    "playerprofile": "player",
}

_MISSING = object()


def install_modules(mods: dict) -> dict:
    """Install short-named fakes everywhere the runtime can find them.

    Returns an opaque save-state for restore_modules(). Covers, per fake:
    sys.modules under the bare name, sys.modules under the dotted package
    path, and the attribute on the (real, already-light) package module.
    """
    import importlib
    import sys
    saved = {"sys": {}, "attrs": {}}
    for name, fake in mods.items():
        keys = [name]
        pkg = _PKG.get(name)
        if pkg:
            keys.append(f"{pkg}.{name}")
            pkg_mod = importlib.import_module(pkg)
            saved["attrs"][(pkg, name)] = getattr(pkg_mod, name, _MISSING)
            setattr(pkg_mod, name, fake)
        for k in keys:
            saved["sys"][k] = sys.modules.get(k)
            sys.modules[k] = fake
    return saved


def restore_modules(saved: dict) -> None:
    import importlib
    import sys
    for k, v in saved["sys"].items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    for (pkg, name), v in saved["attrs"].items():
        pkg_mod = importlib.import_module(pkg)
        if v is _MISSING:
            try:
                delattr(pkg_mod, name)
            except AttributeError:
                pass
        else:
            setattr(pkg_mod, name, v)


def patch_module(monkeypatch, name: str, fake) -> None:
    """monkeypatch-scoped version of install_modules for a single module."""
    import importlib
    import sys
    monkeypatch.setitem(sys.modules, name, fake)
    pkg = _PKG.get(name)
    if pkg:
        monkeypatch.setitem(sys.modules, f"{pkg}.{name}", fake)
        monkeypatch.setattr(importlib.import_module(pkg), name, fake,
                            raising=False)


def _patch_flows_shard(monkeypatch, mod):
    """Install `mod` where the runtime will find shard: the code under test
    imports it with `from flows import shard`, which resolves the package
    ATTRIBUTE first and the `flows.shard` submodule second - so both are
    patched, and the legacy bare name too for anything that still uses it."""
    import sys
    import flows
    monkeypatch.setitem(sys.modules, "shard", mod)
    monkeypatch.setitem(sys.modules, "flows.shard", mod)
    monkeypatch.setattr(flows, "shard", mod, raising=False)


def _f(mod, name):
    """The fake module the imported-under-fakes module actually holds a
    reference to.

    sys.modules is restored after each import (so the live modules are never
    left shadowed), which means patching sys.modules here would patch
    nothing at all. The module's own globals are the real target.
    """
    return mod._fakes[name]


def _rs(**kw):
    """A RunState stand-in carrying only what the unit under test touches."""
    base = dict(cl_offsets={}, cl_always_above=None, rules_fired=set(),
                rule_next={}, rule_cards_tries={}, rules_cards_off=set(),
                sprint_ended=False, bot_left_battle=False, sw_proc_count=0,
                sw_floater_seen=False, sw_miss=0, post_sw_until=0.0,
                sw_immune_until=0.0, dm_fired=False, nuke_fired_at=0.0,
                last_fire={"nuke": 0.0, "demon_mode": 0.0}, wall_prev=None,
                wall_last=None, nuked_marks=set(), fleet_try_at=0.0,
                no_wave=0, dead_frames=0, wave_seen=None, wave_seen_at=0.0,
                wave_stall_logged=False, gem_due=None,
                tracker=types.SimpleNamespace(last=100,
                                              update=lambda w: None))
    base.update(kw)
    return types.SimpleNamespace(**base)


# --------------------------- _fast_wall_watch source-structure invariants

def _watch_parts(src):
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "_fast_wall_watch")
    loop = next(n for n in fn.body if isinstance(n, ast.While))
    return fn, loop


def _config_reads_in_watch_loop(src) -> list[str]:
    """Every `<config>.get(...)` / `<config>[...]` / `preset()` inside the
    sampling loop, as source text. Empty is the invariant."""
    fn, loop = _watch_parts(src)

    def root(node):
        """The Name a Call/Subscript chain ultimately hangs off."""
        while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
            node = (node.func if isinstance(node, ast.Call) else node.value)
        return node.id if isinstance(node, ast.Name) else None

    # Anything derived from a config dict is itself a config dict: `_fleet =
    # fleet_cfg or {}` must not be readable inside the loop either.
    tainted = {"ab", "fleet_cfg", "preset"}
    head = [n for n in fn.body if n is not loop]
    for _ in range(3):                       # transitive closure, 3 is plenty
        for node in ast.walk(ast.Module(body=head, type_ignores=[])):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0],
                                                           ast.Name):
                names = {n.id for n in ast.walk(node.value)
                         if isinstance(n, ast.Name)}
                if names & tainted:
                    tainted.add(node.targets[0].id)

    offenders = []
    for node in ast.walk(loop):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if root(node.func.value) in tainted:
                offenders.append(ast.unparse(node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in tainted:
                offenders.append(ast.unparse(node))
        elif isinstance(node, ast.Subscript) and root(node.value) in tainted:
            offenders.append(ast.unparse(node))
    return offenders


def _assert_policies_are_hoisted(src) -> None:
    fn, loop = _watch_parts(src)
    head = [n for n in fn.body if n is not loop]
    bound = {n.targets[0].id for n in head
             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    for name in ("falling_samples", "deadband", "collapse_from",
                 "burst_cancel_sprint", "burst_retaps", "burst_require_match",
                 "unmatched_logged", "fleet_cfg", "after", "window",
                 "throttle", "fleet_ready"):
        assert name in bound, f"{name} is not hoisted"
