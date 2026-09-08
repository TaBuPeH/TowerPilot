"""The detection-free orbiting-gem blind harvest (flows/shard.GemOrbitTapper).

Its taps fire live during bot-owned farming, so these lock the SAFETY decisions
a wrong reading could get wrong: off unless explicitly enabled, never off a
battle screen, never over the Nuke/Demon-Mode ability row, and aimed at the
measured top-left (135deg) arc rather than into the tower or the abilities.
"""
import math

from flows import shard


def test_orbit_points_land_on_the_top_left_arc():
    cx, cy = shard.GEM_ORBIT_CENTER
    pts = shard._orbit_points(shard.GEM_ORBIT_CENTER, shard.GEM_ORBIT_RADIUS,
                              135.0, 3, 0.03, 6.0)
    assert len(pts) == 3
    for x, y in pts:
        assert x < cx and y < cy                       # up AND left of the tower core
        r = math.hypot(x - cx, y - cy)
        assert abs(r - shard.GEM_ORBIT_RADIUS) < shard.GEM_ORBIT_RADIUS * 0.12
        assert not shard._in_ability_row((x, y))       # clear of Nuke / Demon Mode


def test_tapper_off_by_default_never_taps(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap", lambda x, y, reason="": taps.append((x, y)))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: 100)
    shard.GemOrbitTapper().poll(object())              # default enabled=False
    assert taps == []


def test_tapper_refuses_off_a_battle_screen(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap", lambda x, y, reason="": taps.append((x, y)))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: None)  # menu/dialog
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    shard.GemOrbitTapper(enabled=True).poll(object())
    assert taps == []                                  # hands off when not in a run


def test_tapper_fires_one_set_and_respects_cadence(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap",
                        lambda x, y, reason="": (taps.append((x, y)), {"x": x, "y": y})[1])
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: 100)
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    t = shard.GemOrbitTapper(enabled=True, taps=3)
    t.poll(object())
    assert len(taps) == 3                              # exactly one set of 3
    t.poll(object())                                   # immediately again -> not due
    assert len(taps) == 3                              # cadence held


def test_tapper_never_taps_the_ability_row(monkeypatch):
    taps = []
    monkeypatch.setattr(shard.act, "tap", lambda x, y, reason="": taps.append((x, y)))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: 100)
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    ax, ay, aw, ah = shard.CONFIG["rois"]["ability_row"]
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
