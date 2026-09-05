"""v29 preset-aware loadouts: validator rules, UTC ad-gem counter, and the
/api/loadout-patch contract the Loadouts UI editor drives.

The rules under test are the ones that keep a v29 account's builds safe:
- a global-preset loadout body is EXCLUSIVE (the game applies all five
  categories at battle entry; extra keys would be silently wiped),
- preset names are account data (player.global_presets /
  player.category_presets) and membership is enforced,
- hand-equipping a category that HAS presets without selecting one first is
  an ADVISORY (warnings()), not a refusal - refusing would brick profiles
  over parked quest loadouts, but staying silent would let a run rewrite
  the farming preset (v29 presets auto-save; nothing restores contents).
"""
import copy
import glob
import importlib.util
import os
import shutil

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def profile():
    from goldens import load_golden
    return copy.deepcopy(load_golden())


def _validate(profile):
    from player import playerprofile as pp
    return pp.validate(profile)


def _with_loadout(monkeypatch, name, body):
    from settings import CONFIG
    patched = dict(CONFIG.get("loadouts") or {})
    patched[name] = body
    monkeypatch.setitem(CONFIG, "loadouts", patched)


# ------------------------------------------------------------- validator
def test_global_preset_loadout_validates(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_gp", {"global_preset": "Farm Run"})
    profile["blueprints"]["coin_default"]["loadout"] = "zz_gp"
    assert _validate(profile) == []


def test_global_preset_mixed_body_refused(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_gp",
                  {"global_preset": "Farm Run", "cards": "main_farm"})
    profile["blueprints"]["coin_default"]["loadout"] = "zz_gp"
    problems = _validate(profile)
    assert problems and "mixes global_preset" in problems[0]


def test_unknown_global_preset_refused(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_gp", {"global_preset": "No Such Preset"})
    profile["blueprints"]["coin_default"]["loadout"] = "zz_gp"
    problems = _validate(profile)
    assert problems and "No Such Preset" in problems[0]


def test_global_preset_needs_capability(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_gp", {"global_preset": "Farm Run"})
    profile["blueprints"]["coin_default"]["loadout"] = "zz_gp"
    del profile["player"]["global_presets"]
    problems = _validate(profile)
    assert problems and "player.global_presets is empty" in problems[0]


def test_category_preset_membership(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_cat",
                  {"cards": "18v300", "module_preset": "Preset 9"})
    profile["blueprints"]["shard_daily"]["loadout"] = "zz_cat"
    problems = _validate(profile)
    assert problems and "Preset 9" in problems[0]


def test_shipped_loadouts_validate_clean(profile):
    """The as-shipped config (coin/tourney global presets, shard category
    preset) must validate with zero problems against the default profile."""
    assert _validate(profile) == []


def test_manual_swap_on_preset_account_is_advisory(profile, monkeypatch):
    from player import playerprofile as pp
    _with_loadout(monkeypatch, "zz_manual",
                  {"cards": "18v300",
                   "modules": [["dimension_core", "assist"]]})
    profile["blueprints"]["shard_daily"]["loadout"] = "zz_manual"
    assert _validate(profile) == []          # runs are not blocked...
    warns = pp.loadout_corruption_warnings(profile)
    assert any("zz_manual" in w and "rewrites" in w for w in warns)


def test_manual_with_explicit_preset_has_no_warning(profile, monkeypatch):
    from player import playerprofile as pp
    _with_loadout(monkeypatch, "zz_manual",
                  {"module_preset": "Tourney",
                   "modules": [["dimension_core", "assist"]]})
    profile["blueprints"]["shard_daily"]["loadout"] = "zz_manual"
    assert _validate(profile) == []
    assert not any("zz_manual" in w
                   for w in pp.loadout_corruption_warnings(profile))


# -------------------------------------------------------- loadout.apply
class _NullLogger:
    """Tests must not write events into the real logs/<instance>/ stream -
    a pytest-created events file becomes the 'newest log' that monitors and
    the dashboard's calibrate check resolve to."""
    def event(self, *a, **k):
        pass

    def shot(self, *a, **k):
        return "test-shot"


def test_apply_routes_global_preset(monkeypatch):
    """A global_preset body does exactly one thing: select it. No cards, no
    guardians, no modules, no extra screens."""
    import interactions.loadout as loadout
    import interactions.presets as presets
    monkeypatch.setattr(loadout, "logger", _NullLogger())
    calls = []
    monkeypatch.setattr(presets, "select_global",
                        lambda name: calls.append(("global", name)) or "selected")
    monkeypatch.setattr(loadout, "apply_cards",
                        lambda p: calls.append(("cards", p)))
    monkeypatch.setattr(loadout, "spec",
                        lambda n: {"global_preset": "Farm Run"})
    done = loadout.apply("whatever")
    assert calls == [("global", "Farm Run")]
    assert done["global_preset"] == "selected"


def test_apply_legacy_ends_on_none(monkeypatch):
    """A hand-assembled body must leave the picker on None, or battle entry
    re-applies a stale global preset over it."""
    import interactions.loadout as loadout
    import interactions.presets as presets
    monkeypatch.setattr(loadout, "logger", _NullLogger())
    calls = []
    monkeypatch.setattr(presets, "select_global",
                        lambda name: calls.append(("global", name)) or "selected")
    monkeypatch.setattr(presets, "select_category",
                        lambda c, p: calls.append(("cat", c, p)) or "selected")
    monkeypatch.setattr(presets, "available", lambda: True)
    monkeypatch.setattr(loadout, "apply_cards",
                        lambda p: calls.append(("cards", p)) or "already")
    monkeypatch.setattr(loadout, "spec",
                        lambda n: {"cards": "18v300",
                                   "module_preset": "Tourney"})
    loadout.apply("whatever")
    assert ("cat", "modules", "Tourney") in calls
    assert calls[-1] == ("global", None)
    # category selection must run BEFORE anything that could mutate state
    assert calls.index(("cat", "modules", "Tourney")) \
        < calls.index(("cards", "18v300"))


# ----------------------------------------------------- tourney.setup()
def test_tourney_setup_skips_card_tweaks_under_global_preset(monkeypatch):
    """The game re-applies the saved deck at battle entry under a global
    preset - a tweak made here would be silently wiped, so setup must skip
    card_tweaks (the tweak belongs inside the in-game card preset)."""
    import types
    import interactions.tourney as tourney
    from _fakes import patch_module
    monkeypatch.setattr(tourney, "logger", _NullLogger())
    order = []
    # setup() grabs a frame first; without this the test reached the REAL
    # adb server (it only ever passed with an emulator attached)
    monkeypatch.setattr(tourney.capture, "grab", lambda *a, **k: "FRAME")
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
    lo.spec = lambda n: {"global_preset": "Tournament"}
    patch_module(monkeypatch, "loadout", lo)
    assert tourney.setup() is True
    assert order == ["apply", "verify", "battle"]   # no "tweaks"


# ------------------------------------------------------- daystate (UTC)
def test_utc_counter_bumps_and_scopes(tmp_path, monkeypatch):
    from scheduling import daystate
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "day.json"))
    assert daystate.get_utc_today("zz") == 0
    assert daystate.bump_utc_today("zz") == 1
    assert daystate.bump_utc_today("zz") == 2
    assert daystate.get_utc_today("zz") == 2
    # a stale (different-date) record reads as the default
    daystate.set_raw("zz", {"date": "2001-01-01", "value": 99})
    assert daystate.get_utc_today("zz") == 0
    assert daystate.bump_utc_today("zz") == 1


def test_ad_gems_cap_gates(monkeypatch, tmp_path):
    from scheduling import daystate
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "day.json"))
    import orchestrator
    events = []
    monkeypatch.setattr(orchestrator.logger, "event",
                        lambda kind, **kw: events.append((kind, kw)))
    for _ in range(orchestrator.AD_GEMS_DAILY_CAP - 1):
        daystate.bump_utc_today("ad_gems_claimed_utc")
    assert orchestrator.free_gems_due() is True
    orchestrator.free_gems_mark_claimed()        # claim #60
    assert daystate.get_utc_today("ad_gems_claimed_utc") == 60
    assert orchestrator.free_gems_due() is False
    assert any(k == "ad_gems_cap_reached" for k, _ in events)
    # the cap log fires once, not on every poll
    n = len([1 for k, _ in events if k == "ad_gems_cap_reached"])
    assert orchestrator.free_gems_due() is False
    assert len([1 for k, _ in events if k == "ad_gems_cap_reached"]) == n


# ------------------------------------------------------ /api/loadout-patch
@pytest.fixture(scope="module")
def dash():
    path = os.path.join(os.path.dirname(BACKEND), "frontend", "dashboard.py")
    spec = importlib.util.spec_from_file_location("tp_dashboard_v29", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def config_guard(dash):
    """Snapshot config.yaml and settings.CONFIG['loadouts']; restore after."""
    from settings import CONFIG
    cfg_path = os.path.join(BACKEND, "config.yaml")
    backup = cfg_path + ".zz_v29_test"
    shutil.copyfile(cfg_path, backup)
    old_loadouts = copy.deepcopy(CONFIG.get("loadouts"))
    yield
    shutil.copyfile(backup, cfg_path)
    os.remove(backup)
    CONFIG["loadouts"] = old_loadouts
    for f in glob.glob(cfg_path + ".bak-*"):
        # only the backups this test run created survive a normal session;
        # leave real ones alone by matching the marker-free window is not
        # possible, so remove none here.
        break


def _patch_loadout(dash, name, body):
    with dash.app.test_client() as c:
        return c.post("/api/loadout-patch", json={"name": name, "body": body})


def test_api_loadout_valid_roundtrip(dash, config_guard, tmp_path, monkeypatch):
    # The endpoint validates the ACTIVE profile against the patched body, and
    # the shipped starter owns no preset by design (2026-09-06) - so the
    # active profile here is the frozen account, which owns "Tournament".
    import yaml
    from goldens import load_golden
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    doc = {k: v for k, v in load_golden().items() if not str(k).startswith("_")}
    (pdir / "default.yaml").write_text(yaml.safe_dump(doc, sort_keys=False),
                                       encoding="utf-8")
    monkeypatch.setattr(dash, "_profiles_dir", lambda: str(pdir))
    r = _patch_loadout(dash, "tourney_2", {"global_preset": "Tournament"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["loadouts"]["tourney_2"] == \
        {"global_preset": "Tournament"}


def test_api_loadout_mixed_refused(dash, config_guard):
    r = _patch_loadout(dash, "coin_farm",
                       {"global_preset": "Farm Run", "cards": "main_farm"})
    assert r.status_code == 400
    assert "mixes global_preset" in r.get_json()["error"]


def test_api_loadout_unknown_preset_refused(dash, config_guard):
    r = _patch_loadout(dash, "coin_farm", {"global_preset": "Nope"})
    assert r.status_code == 400
    assert "Nope" in r.get_json()["error"]


def test_api_loadout_unknown_name_404(dash, config_guard):
    r = _patch_loadout(dash, "zz_missing", {"global_preset": "Farm Run"})
    assert r.status_code == 404


# ---------------------------------------------------- modules_restore (v29)
def test_modules_restore_validates_and_is_owned(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_q",
                  {"cards": "main_farm", "module_preset": "Farm",
                   "modules": [["space_displacer", "primary"]],
                   "modules_restore": [["sharp_fortitude", "primary"]]})
    profile["blueprints"]["quest_ilm"]["loadout"] = "zz_q"
    assert _validate(profile) == []


def test_modules_restore_unowned_refused(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_q",
                  {"module_preset": "Farm",
                   "modules_restore": [["nonexistent_module", "primary"]]})
    profile["blueprints"]["quest_ilm"]["loadout"] = "zz_q"
    problems = _validate(profile)
    assert any("nonexistent_module" in p for p in problems)


def test_modules_restore_mixed_with_global_refused(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_q",
                  {"global_preset": "Farm Run",
                   "modules_restore": [["sharp_fortitude", "primary"]]})
    profile["blueprints"]["coin_default"]["loadout"] = "zz_q"
    problems = _validate(profile)
    assert problems and "mixes global_preset" in problems[0]


def test_modules_restore_without_preset_gets_advisory(profile, monkeypatch):
    from player import playerprofile as pp
    _with_loadout(monkeypatch, "zz_q",
                  {"modules_restore": [["sharp_fortitude", "primary"]]})
    profile["blueprints"]["quest_ilm"]["loadout"] = "zz_q"
    assert _validate(profile) == []
    assert any("zz_q" in w for w in pp.loadout_corruption_warnings(profile))


def test_ilm_quest_restores_after_last_cycle(monkeypatch):
    """The quest must put the farm health module back AFTER the cycles, and
    the restore plan comes from the loadout (declarative - a restarted
    process still knows it)."""
    import flows.quest_ilm as q
    order = []
    monkeypatch.setattr(q, "_cli", lambda argv=None: type(
        "A", (), {"instance": "i", "cycles": 2, "preset": None})())
    monkeypatch.setattr(q, "_bind_preset", lambda i, n: None)
    monkeypatch.setattr(q, "_preset", lambda: {"loadout": "zz", "tier": 1})
    monkeypatch.setattr(q, "one_cycle",
                        lambda n, last: order.append(f"cycle{n}"))
    import interactions.loadout as loadout
    import interactions.tourney as tourney
    from flows import shard
    monkeypatch.setattr(loadout, "spec", lambda n: {
        "modules_restore": [["sharp_fortitude", "primary"]]})
    monkeypatch.setattr(loadout, "apply",
                        lambda n, **k: order.append("apply"))
    monkeypatch.setattr(loadout, "apply_modules",
                        lambda plan: order.append(("restore", plan)))
    monkeypatch.setattr(loadout, "logger", _NullLogger())
    monkeypatch.setattr(q, "__name__", "quest_ilm", raising=False)
    import runtime.logger as rl
    monkeypatch.setattr(rl, "event", lambda *a, **k: None)
    monkeypatch.setattr(tourney, "ensure_home", lambda: None)
    monkeypatch.setattr(shard, "set_tier", lambda t: None)
    monkeypatch.setattr(shard, "start_battle", lambda: None)
    monkeypatch.setattr(shard, "abandon_run", lambda **k: None)
    import device.capture as capture
    import vision.detect as detect
    import vision.wave_reader as wave_reader
    monkeypatch.setattr(capture, "grab", lambda: "FRAME")
    monkeypatch.setattr(detect, "death_screen", lambda f: (False, None))
    monkeypatch.setattr(wave_reader, "read_wave", lambda f: None)
    q.main()
    assert order == ["apply", "cycle1", "cycle2",
                     ("restore", [("sharp_fortitude", "primary")])]


def test_shard_setup_routes_through_loadout_apply(monkeypatch):
    """shard.setup must use loadout.apply (v29-aware: preset keys OR manual
    lists), never read spec()["modules"] directly - the direct read crashed
    the whole 78-run shard block with a KeyError when the body became
    preset-based (found 2026-08-28 via runner_crashed)."""
    from flows import shard
    calls = []
    monkeypatch.setattr(shard, "ensure_home", lambda: None)
    monkeypatch.setattr(shard, "set_tier",
                        lambda t: calls.append(("tier", t)))
    monkeypatch.setattr(shard.loadout, "apply",
                        lambda n, **k: calls.append(("apply", n)))
    monkeypatch.setattr(shard, "start_battle",
                        lambda: calls.append(("battle",)))
    monkeypatch.setattr(shard, "on_home", lambda f: True)
    monkeypatch.setattr(shard.capture, "grab", lambda: "F")
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    shard.setup(tier=18)
    assert calls == [("tier", 18), ("apply", "shard_farm"), ("battle",)]


def test_bot_preset_failure_degrades_loudly(monkeypatch):
    """A failed bots selection must NOT abort the handoff (a closed block
    loses the day's quota over an optimization) - it logs bot_preset_failed
    and continues with a distinct FAILED sentinel, never a silent pass."""
    import interactions.loadout as loadout
    import interactions.presets as presets
    import interactions.tourney as tourney
    events = []

    class _Log(_NullLogger):
        def event(self, kind, **kw):
            events.append((kind, kw))
    monkeypatch.setattr(loadout, "logger", _Log())

    def sel_cat(cat, name):
        if cat == "bots":
            raise tourney.Abort("event tile not found")
        return "selected"
    monkeypatch.setattr(presets, "select_category", sel_cat)
    monkeypatch.setattr(presets, "select_global", lambda n: "selected")
    monkeypatch.setattr(presets, "available", lambda: True)
    monkeypatch.setattr(loadout, "apply_cards", lambda p: "already")
    monkeypatch.setattr(loadout, "spec",
                        lambda n: {"cards": "18v300",
                                   "module_preset": "Tourney",
                                   "bot_preset": "Tourney"})
    done = loadout.apply("whatever")
    assert done["module_preset"] == "selected"
    assert done["bot_preset"].startswith("FAILED")
    assert any(k == "bot_preset_failed" for k, _ in events)


# ------------------------------------------------------- cards_restore
def test_cards_restore_validates_and_ownership(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_s",
                  {"cards": "18v300", "module_preset": "Tourney",
                   "cards_restore": "main_farm"})
    profile["blueprints"]["shard_daily"]["loadout"] = "zz_s"
    assert _validate(profile) == []
    _with_loadout(monkeypatch, "zz_s",
                  {"cards": "18v300", "module_preset": "Tourney",
                   "cards_restore": "no_such_deck"})
    problems = _validate(profile)
    assert any("no_such_deck" in p and "cards_restore" in p
               for p in problems)


def test_cards_restore_mixed_with_global_refused(profile, monkeypatch):
    _with_loadout(monkeypatch, "zz_s",
                  {"global_preset": "Farm Run", "cards_restore": "main_farm"})
    profile["blueprints"]["coin_default"]["loadout"] = "zz_s"
    problems = _validate(profile)
    assert problems and "mixes global_preset" in problems[0]


def test_shard_run_restores_cards_at_exit(monkeypatch):
    """After the loop (clean completion OR stop flag) the shard flow puts
    the farming deck back on the cards screen - the coin global preset only
    applies at battle entry and leaves the screen's selection where later
    card mutations land (user, 2026-08-28)."""
    from flows import shard
    calls = []
    monkeypatch.setattr(shard, "setup", lambda t: None)
    monkeypatch.setattr(shard, "one_loop",
                        lambda n, gems=None, last=False:
                        calls.append((f"loop{n}", last)))
    monkeypatch.setattr(shard, "GemWatch", lambda **k: None)
    monkeypatch.setattr(shard, "gem_opts", lambda: {})
    monkeypatch.setattr(shard.runflag, "requested", lambda: None)
    monkeypatch.setattr(shard.daystate, "set_today", lambda *a: None)
    monkeypatch.setattr(shard.daystate, "get_today", lambda *a, **k: 0)
    monkeypatch.setattr(shard.loadout, "spec",
                        lambda n: {"cards_restore": "main_farm"})
    monkeypatch.setattr(shard.loadout, "apply_cards",
                        lambda p: calls.append(("restore", p)) or "loaded")
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    assert shard.run(loops=2) == 2
    # the LAST loop carries last=True: it exits to HOME instead of RETRY
    # (2026-08-29: the final RETRY chained an extra run that the orphan
    # branch then adopted as coin, and it parked the restore off-screen)
    assert calls == [("loop1", False), ("loop2", True),
                     ("restore", "main_farm")]


def test_shard_stop_flag_closes_chained_run(monkeypatch):
    """The runflag exit must surrender the RETRY-chained battle to HOME
    (2026-08-29): since orphan adoption, a leftover live run gets adopted
    (coin) or held on (tournament - stalling the block) by the next phase
    instead of being ended by the handoff, and cards_restore needs the
    nav row."""
    from flows import shard
    calls = []
    monkeypatch.setattr(shard, "setup", lambda t: None)
    monkeypatch.setattr(shard, "one_loop",
                        lambda n, gems=None, last=False:
                        calls.append(f"loop{n}"))
    monkeypatch.setattr(shard, "GemWatch", lambda **k: None)
    monkeypatch.setattr(shard, "gem_opts", lambda: {})
    flags = iter([None, "phase_change"])
    monkeypatch.setattr(shard.runflag, "requested", lambda: next(flags))
    monkeypatch.setattr(shard, "abandon_run",
                        lambda **k: calls.append(("close", k)))
    monkeypatch.setattr(shard.daystate, "set_today", lambda *a: None)
    monkeypatch.setattr(shard.daystate, "get_today", lambda *a, **k: 0)
    monkeypatch.setattr(shard.loadout, "spec",
                        lambda n: {"cards_restore": "main_farm"})
    monkeypatch.setattr(shard.loadout, "apply_cards",
                        lambda p: calls.append(("restore", p)) or "loaded")
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    assert shard.run(loops=5) == 1
    assert calls == ["loop1", ("close", {"to_home": True}),
                     ("restore", "main_farm")]


def test_set_tier_bursts_then_verifies(monkeypatch):
    """set_tier fires the whole tier delta as ONE burst of instant taps
    after a single read (user, 2026-08-28: "make the clicks between tiers
    way faster, like 140 msec") - the old loop paid a ~350ms verify grab
    per single step. One grab verifies the burst; any missed step falls
    back to the original verified single-step path."""
    from flows import shard
    calls = []
    reads = iter([16, 15, 14])          # burst lands 1 short, then verified
    monkeypatch.setattr(shard.capture, "grab",
                        lambda: calls.append("grab") or "F")
    monkeypatch.setattr(shard, "on_home", lambda f: True)
    monkeypatch.setattr(shard, "read_tier", lambda f: next(reads))
    monkeypatch.setattr(shard.act, "tap",
                        lambda x, y, reason="", instant=False:
                        calls.append(("instant", reason, instant)))
    monkeypatch.setattr(shard, "tap_at",
                        lambda pt, reason: calls.append(("step", reason)))
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(shard.logger, "shot", lambda *a, **k: None)
    shard.set_tier(14)
    assert calls == ["grab",
                     ("instant", "tier burst -> 14", True),
                     ("instant", "tier burst -> 14", True),
                     "grab",
                     ("step", "tier 15 -> 14"),
                     "grab"]


def test_ilm_cycle_polls_gems_through_wait(monkeypatch):
    """The ILM cycle wait must poll frames through shard.GemWatch instead of
    a blind sleep (user, 2026-08-28: circling gems went unclaimed through
    whole quest batches - this was the only battle loop that never looked
    at the screen)."""
    import flows.quest_ilm as q
    from flows import shard
    import device.capture as capture
    import runtime.logger as rl
    polls = []

    class FakeWatch:
        def poll(self, frame):
            polls.append(frame)

    monkeypatch.setattr(shard, "GemWatch", lambda **k: FakeWatch())
    monkeypatch.setattr(shard, "gem_opts", lambda: {})
    monkeypatch.setattr(shard, "wait_for_wave", lambda w: ("F", 1))
    monkeypatch.setattr(shard, "ensure_max_speed", lambda: None)
    monkeypatch.setattr(shard, "abandon_run", lambda **k: None)
    monkeypatch.setattr(q, "_preset", lambda: {"cycle_sec": 0.05})
    monkeypatch.setattr(capture, "grab", lambda: "FRAME")
    monkeypatch.setattr(rl, "event", lambda *a, **k: None)
    q.one_cycle(2, last=False)          # n=2 skips the UW sweep
    assert polls and all(f == "FRAME" for f in polls)
