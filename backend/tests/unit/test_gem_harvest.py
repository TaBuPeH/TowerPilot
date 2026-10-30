"""The guarded orbiting-gem arc harvest (flows/shard.GemOrbitTapper).

Its taps fire live during bot-owned farming, so these lock the SAFETY decisions
a wrong reading could get wrong: off unless explicitly enabled, never off a
battle screen, never over the Nuke/Demon-Mode ability row, and aimed at the
measured top-left (135deg) arc rather than into the tower or the abilities.
"""
import math
import json
from pathlib import Path

from flows import shard
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def device_frames(monkeypatch):
    monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: 100)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        SimpleNamespace(center=(680., 805.), rect=(640, 765, 80, 80), score=.99))
    monkeypatch.setattr(shard.capture, 'grab', lambda: object())
    monkeypatch.setattr(shard.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(shard, 'battlefield_observation', lambda points, frame=None: (True, 'live', frame))


def test_diamonds_need_no_captured_images_even_with_legacy_flag():
    from player import readiness, accounts
    cfg = {"active_instance": "main", "instances": {"main": {}}, "loadouts": {}}
    required = readiness.requirements(cfg, {"kind": "coin", "gather": {
        "flying_gem": True, "gem_orbit": {"enabled": True}}})
    assert not any(p.startswith("floaters/gem_") for r in required for p in r["alternatives"])
    assert not any(p.startswith("floaters/gem_") for p in accounts.generic_names())
    targets = json.loads((Path(readiness.__file__).with_name("scan_targets.json")).read_text())
    assert not any(t["rel"].startswith("floaters/gem_") for t in targets)
    assert "floaters/second_wind.png" in accounts.generic_names()


def test_collector_refreshes_frame_after_ad_claim(monkeypatch):
    from types import SimpleNamespace
    from interactions import ad_gems
    calls = []
    fresh = object()
    monkeypatch.setattr(ad_gems, "AdGemCollector", lambda enabled, **kw: SimpleNamespace(poll=lambda f, reads=None: calls.append("ad") or fresh))
    monkeypatch.setattr(shard, "GemOrbitTapper", lambda **kw: SimpleNamespace(enabled=True, poll=lambda f, reads=None: calls.append(f)))
    monkeypatch.setattr(shard.capture, "grab", lambda: fresh)
    shard.GemWatch().poll(object())
    assert calls == ["ad", fresh]


def test_orbit_stops_when_screen_changes_between_taps(monkeypatch):
    taps = []
    frames = iter(['battle', 'exit_battle'])
    monkeypatch.setattr(shard.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(shard.screen, 'identify', lambda frame: SimpleNamespace(name=frame))
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(shard.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    shard.GemOrbitTapper(enabled=True, taps=3).poll('battle')
    assert len(taps) == 1


def test_orbit_does_not_capture_when_not_due(monkeypatch):
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('unnecessary capture'))
    collector = shard.GemOrbitTapper(enabled=True)
    collector.next_at = float('inf')
    collector.poll(object())


def test_orbit_points_land_on_the_top_left_arc():
    (cx, cy), radius = shard.reference_geometry()
    pts = shard._orbit_points((cx, cy), radius,
                              135.0, 3, 0.03, 6.0)
    assert len(pts) == 3
    for x, y in pts:
        assert x < cx and y < cy                       # up AND left of the tower core
        r = math.hypot(x - cx, y - cy)
        assert abs(r - radius) < radius * 0.12
        assert not shard._in_ability_row((x, y))       # clear of Nuke / Demon Mode


def test_tapper_off_by_default_never_taps(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap", lambda x, y, reason="", deadline=None: taps.append((x, y)))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: 100)
    shard.GemOrbitTapper().poll(object())              # default enabled=False
    assert taps == []


def test_tapper_refuses_off_a_battle_screen(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap", lambda x, y, reason="", deadline=None: taps.append((x, y)))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: None)  # menu/dialog
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    shard.GemOrbitTapper(enabled=True).poll(object())
    assert taps == []                                  # hands off when not in a run


def test_tapper_fires_one_set_and_respects_cadence(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap",
                        lambda x, y, reason="", deadline=None: (taps.append((x, y)), {"x": x, "y": y})[1])
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: 100)
    # the battlefield guard has its own file (test_gem_orbit_guard.py); here
    # it stands in as "a clean live battle", which is what these two are about
    monkeypatch.setattr(shard, "battlefield_observation",
                        lambda points, frame=None: (True, "live", frame))
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    t = shard.GemOrbitTapper(enabled=True, taps=3)
    t.poll(object())
    assert len(taps) == 3                              # exactly one set of 3
    t.poll(object())                                   # immediately again -> not due
    assert len(taps) == 3                              # cadence held


def test_tapper_never_taps_the_ability_row(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap", lambda x, y, reason="", deadline=None: taps.append((x, y)))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: 100)
    # the battlefield guard has its own file (test_gem_orbit_guard.py); here
    # it stands in as "a clean live battle", which is what these two are about
    monkeypatch.setattr(shard, "battlefield_observation",
                        lambda points, frame=None: (True, "live", frame))
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    ax, ay, aw, ah = shard.CONFIG["rois"]["ability_row"]
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        SimpleNamespace(center=(ax+aw//2+6, ay-3), rect=(0, 0, 80, 80), score=.99))
    # aim the whole set straight into the ability row (270deg = down in screen y)
    t = shard.GemOrbitTapper(enabled=True, center=(ax + aw // 2, ay - 3),
                             radius=6, angle=270, taps=3)
    t.poll(object())
    assert taps == []                                  # every point refused


def test_gem_orbit_opts_off_unless_a_blueprint_enables_it(monkeypatch):
    monkeypatch.setattr(shard, "CONFIG", {"preset": "shard_farm", "presets": {}})
    assert shard.gem_orbit_opts() == {"enabled": False}
    monkeypatch.setattr(shard, "CONFIG", {"preset": "bp_night", "presets": {
        "bp_night": {"gather": {"gem_orbit": {
            "enabled": True, "radius": 200, "interval_sec": 30,
            "center": [500, 800]}}}}})
    kw = shard.gem_orbit_opts()
    assert kw["enabled"] is True
    assert kw["radius"] == 200 and kw["interval"] == 30 and kw["center"] == (500, 800)


def test_farming_collector_preserves_inherited_opt_out(monkeypatch):
    from test_p4_interpreter import _import_live
    runner = _import_live('orchestrator')
    base = dict(runner.preset(), gather={'ad_gems': False})
    monkeypatch.setitem(runner.CONFIG, 'presets', {'parent': base, 'child': {'base': 'parent'}})
    monkeypatch.setitem(runner.CONFIG, 'preset', 'child')
    state = runner.RunState()
    assert state.gems.ad_gems.enabled is False


def test_farming_requests_new_tick_after_collection_observes_modal(monkeypatch):
    from test_p4_interpreter import _import_live
    runner = _import_live('orchestrator')
    state = runner.RunState()
    state.gems = SimpleNamespace(poll=lambda frame: object())
    assert runner.watch_frame(state, 'old battle') == 'refresh'


def test_farming_refresh_skips_rules_and_shopping(monkeypatch):
    from test_p4_interpreter import _import_live
    from player import readiness
    from runtime import device_lease
    monkeypatch.setattr(device_lease, 'ensure_current', lambda: None)
    runner = _import_live('orchestrator')
    monkeypatch.setattr(readiness, 'require', lambda *a: None)
    monkeypatch.setattr(runner, '_prepare_coin_start', lambda: None)
    monkeypatch.setattr(runner, 'watch_frame', lambda *a: 'refresh')
    monkeypatch.setattr(runner, 'eval_rules', lambda *a: pytest.fail('rule after stale frame'))
    captures = []
    def grab():
        captures.append(True)
        if len(captures) == 2:
            raise KeyboardInterrupt
        return 'old battle'
    monkeypatch.setattr(runner.capture, 'grab', grab)
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    assert len(captures) == 2
    assert runner._taps == []


def test_shard_does_not_return_nuke_frame_invalidated_by_collection(monkeypatch):
    frames = iter(['old battle', 'fresh battle'])
    monkeypatch.setattr(shard.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda frame: shard.NUKE_WAVE)
    monkeypatch.setattr(shard.detect, 'bar_fill', lambda *a: 1.0)
    gems = SimpleNamespace(poll=lambda frame: 'modal' if frame == 'old battle' else frame)
    assert shard.wait_for_nuke_point(gems=gems) == 'fresh battle'
