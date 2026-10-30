"""The blind orbit harvest may only tap a battlefield it can vouch for.

2026-10-13: the player found gems bought with medals. The orbiting-diamond
harvest taps three fixed points on a 25s cadence and never looks at them; its
only gate was "wave_reader reads a number", which is true of every frame of a
run - including one where a panel is open over the battlefield, because the
wave counter sits above it. ~1,500 blind taps a day went into whatever was
drawn there.

Three gates now, and each one is tested refusing on its own: the screen must
classify as a battle, the GAME must be the window on screen (`dumpsys`
evidence, not pixels), and the arc must be MOVING - the check that catches an
in-game panel, which no window list can see."""

import paths as test_paths
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = test_paths.BACKEND
sys.path.insert(0, str(ROOT))

from flows import shard                                        # noqa: E402

POINTS = [(403, 672), (389, 684), (377, 698)]      # the measured tap set


def _frame(seed=0):
    """A frame whose probe box is full of noise - a live battlefield."""
    rng = np.random.default_rng(seed)
    f = np.zeros((2560, 1080, 3), np.uint8)
    y0, y1, x0, x1 = shard._probe_box(POINTS)
    f[y0:y1, x0:x1] = rng.integers(0, 255, (y1 - y0, x1 - x0, 3), dtype=np.uint8)
    return f


@pytest.fixture
def world(monkeypatch):
    """Everything battlefield_live reads, all of it answering "clean battle"."""
    state = types.SimpleNamespace(
        frames=[_frame(1), _frame(2)], wave=4465, screen="battle",
        windows=["com.TechTreeGames.TheTower/com.unity3d.player.UnityPlayerActivity"],
        taps=[], events=[], slept=[])
    monkeypatch.setattr(shard.capture, "layout_offset", 0)
    monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: 100)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        types.SimpleNamespace(center=(680., 805.), rect=(640, 765, 80, 80), score=.99))
    monkeypatch.setattr(shard.capture, "grab", lambda *a, **k: state.frames.pop(0)
                        if state.frames else _frame(9))
    monkeypatch.setattr(shard.wave_reader, "read_wave", lambda f: state.wave)
    monkeypatch.setattr(shard.screen, "identify",
                        lambda f: types.SimpleNamespace(name=state.screen))
    monkeypatch.setattr(shard.settings, "instance", lambda *a: {"serial": "x"})
    fake = types.ModuleType("device.overlays")
    from device.overlays import foreground_blocker
    fake.foreground_blocker = foreground_blocker
    fake.windows = lambda serial, **kw: state.windows
    fake.GAME_PKG = "com.TechTreeGames.TheTower"
    fake.EXPECTED_PREFIXES = (fake.GAME_PKG, "StatusBar", "app.lawnchair")
    import device
    monkeypatch.setitem(sys.modules, "device.overlays", fake)
    monkeypatch.setattr(device, "overlays", fake, raising=False)
    monkeypatch.setattr(shard.act, "tap",
                        lambda x, y, reason="", instant=False, deadline=None:
                        state.taps.append((x, y)) or {"x": x, "y": y})
    monkeypatch.setattr(shard.logger, "event",
                        lambda kind, **kw: state.events.append((kind, kw)))
    monkeypatch.setattr(shard.time, "sleep", lambda s: state.slept.append(s))
    return state


# ------------------------------------------------------------- the guard
def test_a_clean_live_battle_passes(world):
    assert shard.battlefield_live(POINTS) == (True, "live")


def test_motion_observation_exposes_only_its_verified_latest_frame(world):
    latest = world.frames[-1]
    ok, why, frame = shard.battlefield_observation(POINTS)
    assert ok and why == 'live' and frame is latest


def test_motion_refusal_exposes_no_usable_frame(world):
    world.wave = None
    assert shard.battlefield_observation(POINTS) == (False, 'no_wave', None)


def test_first_tap_reuses_the_motion_verified_frame(world, monkeypatch):
    fresh, motion, later_unknown = _frame(1), _frame(2), _frame(3)
    world.frames = [fresh, motion, later_unknown]
    monkeypatch.setattr(shard, 'read_gem_balance',
                        lambda frame: None if frame is later_unknown else 100)
    tapper = _tapper()
    tapper.taps = 1
    tapper.poll(_frame(4))
    assert len(world.taps) == 1
    assert tapper.balance_before == 100 and tapper.sent_tap
    assert not any(kind == 'gem_orbit_claim' for kind, _ in world.events)


@pytest.mark.parametrize('unknown', ['counter', 'gem'])
def test_reused_motion_frame_still_needs_current_optical_evidence(world, monkeypatch, unknown):
    fresh, motion, later = _frame(1), _frame(2), _frame(3)
    world.frames = [fresh, motion, later]
    if unknown == 'counter':
        monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: None if frame is motion or frame is later else 100)
    else:
        detector = shard.find_orbit_gem
        monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw: None if frame is motion or frame is later else detector(frame, **kw))
    _tapper().poll(_frame(4))
    assert world.taps == []


def test_unknown_motion_counter_retains_the_original_fresh_capture(world, monkeypatch):
    fresh, motion, later = _frame(1), _frame(2), _frame(3)
    world.frames = [fresh, motion, later]
    monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: None if frame is motion else 100)
    tapper = _tapper()
    tapper.taps = 1
    tapper.poll(_frame(4))
    assert len(world.taps) == 1


def test_no_wave_is_not_a_battle(world):
    world.wave = None
    assert shard.battlefield_live(POINTS) == (False, "no_wave")


def test_no_tap_points_refuses(world):
    assert shard.battlefield_live([]) == (False, "no_points")


def test_a_menu_with_digits_in_the_wave_box_is_still_a_menu(world):
    """The wave reader alone cannot tell a run from a screen that happens to
    carry digits; the classifier is asked as well."""
    world.screen = "lab"
    assert shard.battlefield_live(POINTS) == (False, "screen:lab")


def test_a_window_in_front_of_the_game_refuses(world):
    """A screencap still shows the last game frame while another app is drawn
    over it, so only the window list can catch this."""
    world.windows = [
        "com.android.vending/com.android.vending.AssetBrowserActivity",
        "com.TechTreeGames.TheTower/com.unity3d.player.UnityPlayerActivity"]
    assert shard.battlefield_live(POINTS) == (
        False, "in_front:com.android.vending")


def test_an_app_BEHIND_the_game_is_not_an_overlay(world):
    """MEASURED 2026-10-13: the Play Store sat at #1 behind the game at #0 all
    day while the game played normally. `dumpsys` lists front to back and keeps
    backgrounded activities, so "is it in the list" would refuse every set
    forever - which is exactly what the first version of this guard did."""
    world.windows = [
        "com.TechTreeGames.TheTower/com.unity3d.player.UnityPlayerActivity",
        "com.android.vending/com.android.vending.AssetBrowserActivity",
        "app.lawnchair/app.lawnchair.LawnchairLauncher"]
    assert shard.battlefield_live(POINTS) == (True, "live")


def test_system_chrome_in_front_is_not_an_overlay(world):
    world.windows = [
        "StatusBar",
        "com.TechTreeGames.TheTower/com.unity3d.player.UnityPlayerActivity"]
    assert shard.battlefield_live(POINTS) == (True, "live")


def test_the_game_not_on_screen_at_all_refuses(world):
    world.windows = ["app.lawnchair/app.lawnchair.LawnchairLauncher"]
    assert shard.battlefield_live(POINTS) == (False, "game_not_on_screen")


def test_an_empty_window_list_refuses(world):
    world.windows = []
    assert shard.battlefield_live(POINTS) == (False, "no_windows")


@pytest.mark.parametrize("window", ["app.lawnchair/Launcher", "InputMethod", "NotificationShade"])
def test_interactive_chrome_above_game_refuses(world, window):
    world.windows.insert(0, window)
    assert shard.battlefield_live(POINTS) == (False, "in_front:" + window.split("/")[0])


def test_guard_requires_visible_window_evidence(world, monkeypatch):
    import device
    calls = []

    def windows(serial, *, visible_only=False):
        calls.append((serial, visible_only))
        return world.windows

    monkeypatch.setattr(device.overlays, "windows", windows)
    assert shard.battlefield_live(POINTS) == (True, "live")
    assert calls == [("x", True)]


def test_a_window_list_that_cannot_be_read_is_not_permission(world, monkeypatch):
    import device
    fake = device.overlays

    def boom(serial, **kw):
        raise OSError("adb said no")
    monkeypatch.setattr(fake, "windows", boom)
    ok, why = shard.battlefield_live(POINTS)
    assert ok is False and why.startswith("windows_unreadable")


def test_a_still_arc_is_a_panel_not_a_battlefield(world):
    """THE ONE THAT CATCHES AN IN-GAME PANEL. A live battlefield changed by
    25-70 mean abs diff over 0.4s when measured on wave 4465; a panel drawn
    over it does not change at all."""
    same = _frame(7)
    world.frames = [same, same.copy()]
    ok, why = shard.battlefield_live(POINTS)
    assert ok is False and why.startswith("static:")


def test_the_probe_box_covers_every_tap_point(world):
    y0, y1, x0, x1 = shard._probe_box(POINTS)
    for (x, y) in POINTS:
        assert y0 < y < y1 and x0 < x < x1


def test_panel_change_during_motion_probe_refuses(world, monkeypatch):
    def change_layout(delay):
        shard.capture.layout_offset = shard.capture.PANEL_SHIFT
    monkeypatch.setattr(shard.time, "sleep", change_layout)
    assert shard.battlefield_live(POINTS) == (False, "layout_changed")


@pytest.mark.parametrize("change,reason", [("screen", "screen:lab"), ("wave", "no_wave")])
def test_battle_evidence_lost_during_motion_probe_refuses(world, monkeypatch, change, reason):
    def change_battle(delay):
        if change == "screen":
            world.screen = "lab"
        else:
            world.wave = None

    monkeypatch.setattr(shard.time, "sleep", change_battle)
    assert shard.battlefield_live(POINTS) == (False, reason)


def test_frame_geometry_change_during_probe_refuses(world):
    world.frames[1] = np.zeros((1280, 540, 3), np.uint8)
    assert shard.battlefield_live(POINTS) == (False, "invalid_probe")


def test_probe_outside_frame_refuses(world):
    assert shard.battlefield_live([(2000, 2000)]) == (False, "invalid_probe")


def test_panel_change_before_first_tap_refuses(world, monkeypatch):
    def change_layout(points, frame=None):
        shard.capture.layout_offset = shard.capture.PANEL_SHIFT
        return True, "live", frame
    monkeypatch.setattr(shard, "battlefield_observation", change_layout)
    _tapper().poll(_frame(1))
    assert world.taps == []
    assert world.events[-1] == ("gem_orbit_skip", {"reason": "layout_changed"})


def test_arc_follows_tower_when_panel_closes(world):
    tapper = _tapper()
    opened = tapper.points()
    shard.capture.layout_offset = shard.capture.PANEL_SHIFT
    closed = tapper.points()
    for (ox, oy), (cx, cy) in zip(opened, closed):
        assert cx == ox
        assert abs(cy - oy - shard.capture.PANEL_SHIFT / 2) <= 1


def test_ability_rail_follows_closed_panel(world):
    x, y, w, h = shard.CONFIG["rois"]["ability_row"]
    shard.capture.layout_offset = shard.capture.PANEL_SHIFT
    assert shard._in_ability_row((x + w // 2, y + h // 2 + shard.capture.PANEL_SHIFT))


# ------------------------------------------------------------- the tapper
def _tapper(**kw):
    return shard.GemOrbitTapper(enabled=True, taps=3, jitter=0.0, **kw)


def test_the_set_is_tapped_when_the_guard_passes(world, monkeypatch):
    t = _tapper()
    monkeypatch.setattr(shard, "battlefield_observation", lambda pts, frame=None: (True, "live", frame))
    t.poll(_frame(1))
    assert len(world.taps) == 3
    assert [k for k, _ in world.events] == ["gem_orbit_window", "gem_orbit_geometry"] + ["gem_orbit_tap"] * 3


def test_a_refused_guard_taps_nothing_and_says_why_once(world, monkeypatch):
    t = _tapper()
    monkeypatch.setattr(shard, "battlefield_observation",
                        lambda pts, frame=None: (False, "static:0.0", None))
    for _ in range(3):
        t.next_at = 0.0                      # three polls, all due
        t.poll(_frame(1))
    assert world.taps == []
    skips = [kw for k, kw in world.events if k == "gem_orbit_skip"]
    assert skips == [{"reason": "static:0.0"}]       # once, not per poll


def test_a_new_reason_is_logged_again(world, monkeypatch):
    t = _tapper()
    reasons = iter(["static:0.0", "static:0.0", "overlay:com.android.vending"])
    monkeypatch.setattr(shard, "battlefield_observation",
                        lambda pts, frame=None: (False, next(reasons), None))
    for _ in range(3):
        t.next_at = 0.0
        t.poll(_frame(1))
    assert [kw["reason"] for k, kw in world.events if k == "gem_orbit_skip"] == [
        "static:0.0", "overlay:com.android.vending"]


def test_disabled_asks_nothing_at_all(world, monkeypatch):
    monkeypatch.setattr(shard, "battlefield_observation",
                        lambda *a, **k: pytest.fail("must not even look"))
    shard.GemOrbitTapper(enabled=False).poll(_frame(1))
    assert world.taps == []
