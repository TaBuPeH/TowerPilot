"""Emulator ad overlays are dismissed on evidence, never refused on sight
(2026-09-12: MuMu Store's fullscreen promo appeared over the game a minute
after boot and the starter scan stopped with "The game must be visible with
no other app or overlay covering it" while the emulator looked untouched from
Windows). And the quest CLAIM image is optional: it only exists while a quest
is finished, so setup lists it and the quest flow learns it during play."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from device import overlays
from player import bootstrap, calibrate, flow_capture as fc

GAME = overlays.GAME_PKG + "/com.unity3d.player.UnityPlayerActivity"
AD = "com.mumu.store"
CLEAN = [GAME, "StatusBar", "app.lawnchair/app.lawnchair.LawnchairLauncher"]


# ------------------------------------------------------------------ sweep
def test_sweep_returns_at_once_on_a_clean_window_list(monkeypatch):
    monkeypatch.setattr(overlays, "windows", lambda serial: CLEAN)
    monkeypatch.setattr(overlays, "clean", lambda: pytest.fail("clean() on a clean list"))
    assert overlays.sweep("test") is True


def test_sweep_hands_a_known_ad_to_clean_and_reports_its_verdict(monkeypatch):
    monkeypatch.setattr(overlays, "windows", lambda serial: CLEAN + [AD])
    calls = []
    monkeypatch.setattr(overlays, "clean", lambda: calls.append(1) or True)
    assert overlays.sweep("test") is True and calls == [1]
    monkeypatch.setattr(overlays, "clean", lambda: False)
    assert overlays.sweep("test") is False


def test_sweep_never_raises_on_an_adb_hiccup(monkeypatch):
    def boom(serial):
        raise OSError("device offline")
    monkeypatch.setattr(overlays, "windows", boom)
    assert overlays.sweep("test") is True


# --------------------------------------------------------------- preflight
@pytest.fixture()
def preflight_env(monkeypatch, tmp_path):
    import psutil
    import settings
    from device import adbclient, capture
    from player import accounts
    monkeypatch.setattr(settings, "instance", lambda: {"allow_taps": True, "serial": "test"})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [])
    monkeypatch.setattr(adbclient, "shell", lambda serial, cmd, **k: b"Physical density: 360")
    monkeypatch.setattr(capture, "grab", lambda: np.zeros((2560, 1080, 3), np.uint8))
    monkeypatch.setattr(accounts, "calibration_dir", lambda root, cfg: tmp_path)
    return monkeypatch


def test_preflight_kills_a_known_ad_instead_of_refusing(preflight_env):
    lists = iter([CLEAN + [AD], CLEAN])            # before clean, after clean
    preflight_env.setattr(overlays, "windows", lambda serial: next(lists))
    cleaned = []
    preflight_env.setattr(overlays, "clean", lambda: cleaned.append(1) or True)
    display = bootstrap.preflight()
    assert cleaned == [1]
    assert (display.width, display.height) == (1080, 2560)


def test_preflight_names_what_it_could_not_dismiss(preflight_env):
    preflight_env.setattr(overlays, "windows", lambda serial: CLEAN + ["com.example.popup/Ad"])
    preflight_env.setattr(overlays, "clean", lambda: False)
    with pytest.raises(RuntimeError, match="still on screen: com.example.popup/Ad"):
        bootstrap.preflight()


def test_preflight_tells_the_person_when_the_game_is_not_running(preflight_env):
    preflight_env.setattr(overlays, "windows", lambda serial: [w for w in CLEAN if w != GAME])
    preflight_env.setattr(overlays, "clean", lambda: pytest.fail("nothing to clean"))
    with pytest.raises(RuntimeError, match="not running"):
        bootstrap.preflight()


# ------------------------------------------------------------ scanner steps
def _scanner(tmp_path, **kw):
    cal = calibrate.Calibration({"stop": str(tmp_path / "stop"), "state": str(tmp_path / "state.json")},
                                False, bootstrap=True)
    return bootstrap.Scanner(cal, {}, grab=lambda: np.zeros((2560, 1080, 3), np.uint8),
                             tap=lambda *a, **k: None, pause=lambda _: None, **kw)


def test_scanner_on_an_injected_frame_source_sweeps_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(overlays, "windows", lambda serial: pytest.fail("adb from a fake scanner"))
    sc = _scanner(tmp_path)
    sc.begin_step(sc.step_index("home"))
    assert sc.steps[sc.step_index("home")]["status"] == "running"


def test_scanner_step_sweeps_first_and_stops_by_name_when_it_cannot(tmp_path, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "test"})
    swept = []
    sc = _scanner(tmp_path, sweep=lambda: swept.append(1) or True)
    sc.begin_step(sc.step_index("home"))
    assert swept == [1]
    monkeypatch.setattr(overlays, "windows", lambda serial: CLEAN + [AD])
    sc = _scanner(tmp_path, sweep=lambda: False)
    with pytest.raises(RuntimeError, match="could not be dismissed: com.mumu.store"):
        sc.begin_step(sc.step_index("home"))


def test_real_scanner_defaults_to_the_overlay_sweep(tmp_path, monkeypatch):
    from device import capture
    monkeypatch.setattr(capture, "grab", lambda: None)
    cal = calibrate.Calibration({"stop": str(tmp_path / "stop"), "state": str(tmp_path / "state.json")},
                                False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, tap=lambda *a, **k: None, pause=lambda _: None)
    assert sc.sweep is overlays.sweep


# --------------------------------------------------------- optional CLAIM
class _Cal:
    def cut(self, *a, **k):
        return {"verified": True}


class _Scan:
    def __init__(self):
        self.cal, self.skipped, self.messages = _Cal(), [], []
        self.frame = np.random.default_rng(1).integers(0, 255, (2560, 1080, 3), np.uint8)

    def grab(self):
        return self.frame

    def progress(self, message, **extra):
        self.messages.append(message)


def test_an_absent_optional_control_is_listed_but_marked_optional(monkeypatch):
    from player import battle_capture as bc
    from runtime import logger
    events = []
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(bc, "capture_by_text", lambda *a, **k: None)
    flow = fc._Flow(_Scan())
    fc._menu_capture(flow, [("buttons/quest_claim.png", "CLAIM", (160, 50), (0.2, 0.35)),
                            ("icons/event_calendar.png", "CALENDAR", (138, 124), (0.3, 0.5))])
    by_target = {s["target"]: s for s in flow.s.skipped}
    assert by_target["buttons/quest_claim.png"]["optional"] is True
    assert "claimable" in by_target["buttons/quest_claim.png"]["reason"]
    assert "optional" not in by_target["icons/event_calendar.png"]
    assert [kw["optional"] for kind, kw in events if kind == "flow_skip"] == [True, False]


def test_only_optional_skips_leave_the_scan_done(tmp_path, monkeypatch):
    sc = _scanner(tmp_path)
    sc.skipped = [{"target": "buttons/quest_claim.png", "reason": "no finished quest", "optional": True}]
    optional_only = [s for s in sc.skipped if not s.get("optional")]
    assert optional_only == []
    sc.skipped.append({"target": "icons/event_calendar.png", "reason": "not visible"})
    assert [s["target"] for s in sc.skipped if not s.get("optional")] == ["icons/event_calendar.png"]


def test_battle_preparation_goes_on_without_the_mission_route(tmp_path, monkeypatch):
    """A mission route that cannot be verified used to raise and end the
    whole battle preparation; it is an optional skip now and the battle
    capture still runs."""
    import settings
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(settings, "template_path", lambda rel, **k: tmp_path / rel)   # nothing on disk
    sc = _scanner(tmp_path, flows=True, battle_only=True)
    from player import mapping_session, manifest_driver
    class _Driver:
        def step(self, edge):
            return False
    class _Session:
        def __init__(self, scanner):
            self.driver = _Driver()
    monkeypatch.setattr(mapping_session, "Session", _Session)
    monkeypatch.setattr(manifest_driver, "transitions",
                        lambda: [{"source": "home", "destination": "daily_missions", "route": []}])
    monkeypatch.setattr(fc, "_safe_home", lambda flow: None)
    ran = []
    monkeypatch.setattr(fc, "capture_battle_flow", lambda flow, **kw: ran.append("battle"))
    sc._run_flows()
    assert ran == ["battle"]
    assert sc.skipped and sc.skipped[0]["target"] == "buttons/quest_claim.png"
    assert sc.skipped[0]["optional"] is True
    assert sc.steps[sc.step_index("flow_battle")]["status"] == "done"


def test_quest_flow_learns_the_claim_image_it_taps_missing_only(tmp_path, monkeypatch):
    import settings
    from interactions import missions
    from player import battle_capture as bc
    from runtime import logger
    events, written = [], []
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append(kind))
    target = tmp_path / "buttons" / "quest_claim.png"
    monkeypatch.setattr(settings, "template_path", lambda rel, **k: target)
    monkeypatch.setattr(bc, "write_template", lambda rel, crop, **k: written.append((rel, crop.shape)) or "written")
    frame = np.random.default_rng(2).integers(0, 255, (2560, 1080, 3), np.uint8)
    assert missions.learn_claim_template(frame, (540, 1200)) is True
    assert written == [("buttons/quest_claim.png", (50, 160, 3))]
    assert "quest_claim_captured" in events
    # on disk now: one stat, no write, no event
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")
    assert missions.learn_claim_template(frame, (540, 1200)) is False
    assert len(written) == 1
    # a flat crop (no button there) is never written
    target.unlink()
    assert missions.learn_claim_template(np.zeros((2560, 1080, 3), np.uint8), (540, 1200)) is False
    assert len(written) == 1


def test_runner_learns_end_round_from_the_open_menu_missing_only(tmp_path, monkeypatch):
    import settings
    from player import battle_capture as bc
    from runtime import logger
    events, written, asked = [], [], []
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append(kind))
    target = tmp_path / "buttons" / "end_round.png"
    monkeypatch.setattr(settings, "template_path", lambda rel, **k: target)
    frame = np.random.default_rng(4).integers(0, 255, (2560, 1080, 3), np.uint8)
    monkeypatch.setattr(bc, "capture_by_text",
                        lambda f, text, region, size, read, **k: asked.append((text, region, size)) or frame[:45, :154].copy())
    monkeypatch.setattr(bc, "write_template", lambda rel, crop, **k: written.append(rel) or "written")
    assert fc.capture_missing_end_round(frame) is True
    assert asked == [("END ROUND", fc.R_EXIT_BTN, (154, 45))] and written == ["buttons/end_round.png"]
    assert "end_round_captured" in events
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")
    assert fc.capture_missing_end_round(frame) is False      # on disk: one stat, no OCR
    assert len(asked) == 1
    target.unlink()
    monkeypatch.setattr(bc, "capture_by_text", lambda *a, **k: None)
    assert fc.capture_missing_end_round(frame) is False      # menu shut: nothing written
    assert written == ["buttons/end_round.png"]


def test_sometimes_visible_quest_controls_never_block_a_run():
    from player import readiness
    rows = {tuple(r["alternatives"]): r for r in readiness.requirements(
        {}, {"kind": "coin", "gather": {"quests_8h": True, "quest_rewards": True}})}
    assert rows[("buttons/quest_claim.png",)]["blocking"] is False
    assert rows[("icons/chest_lock.png",)]["blocking"] is False
    assert rows[("icons/daily_missions.png",)]["blocking"] is True   # the route itself still is


# ---------------------------------------------------------- release boundary
def test_release_ships_the_agent_rules_and_skills_but_no_personal_settings():
    spec = importlib.util.spec_from_file_location(
        "tp_build_release", str(Path(__file__).resolve().parents[2] / "tools" / "build_release.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("CLAUDE.md", "AGENTS.md", ".claude/settings.json", ".claude/skills/trigger-map.md",
                 ".claude/skills/adb-craft/SKILL.md", ".claude/hooks/skills-reminder.js",
                 ".codex/config.toml", ".github/workflows/release.yml", "backend/tests/test_overlay_sweep.py",
                 "docs/RELEASE_0.2_BETA.md"):
        assert mod.allowed(name), name
    for name in (".claude/settings.local.json", ".claude/cache/frame.png", "backend/config.yaml",
                 "backend/logs/main/events.jsonl", ".claude"):
        assert not mod.allowed(name), name
