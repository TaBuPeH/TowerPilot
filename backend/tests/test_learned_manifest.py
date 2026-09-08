"""The learned manifest: every cut records its screen and rect, and from then
on setup / observe cut that control themselves on that screen, same size
(user, 2026-09-08: "record where I crop from ... crop it yourself matching
sizes"; "we build the MANIFEST ALWAYS and the screen we are on is part of the
manifest - navigate to screen, crop, that is all")."""
import json

import cv2
import numpy as np

from player import bootstrap, calibrate, flow_capture as fc, learned
from player.bootstrap_layout import manifest


def _p(tmp_path):
    return {"state": str(tmp_path / "calibrate_state.json"), "stop": str(tmp_path / "stop"),
            "report": str(tmp_path / "calibrate_report.json")}


def test_record_keeps_native_rects_per_screen_and_ignores_the_rest(tmp_path):
    p = _p(tmp_path)
    row = learned.record(p, "modules/assist_btn.png", [700, 1500, 250, 105], "modules", "dashboard_cropper")
    assert row["rect"] == [700, 1500, 250, 105] and row["relative"] == [round(700 / 1080, 4), round(1500 / 2560, 4), round(250 / 1080, 4), round(105 / 2560, 4)]
    assert learned.record(p, "x/y.png", [20, 21, 65, 64], "battle", "trim", frame_size=(101, 104)) is None   # a rect inside a crop
    assert learned.record(p, "x/y.png", [1000, 0, 200, 50], "home", "s") is None                             # outside the frame
    assert learned.record(p, "x/y.png", [0, 0, 10, 10], None, "s") is None                                   # no screen: nothing learned
    assert set(learned.known(p)) == {"modules/assist_btn.png"}
    assert learned.targets_on(p, "modules") == {"modules/assist_btn.png": row} and learned.targets_on(p, "home") == {}
    assert learned.path(p).name == "learned_manifest.json" and json.loads(learned.path(p).read_text())["version"] == 1


def test_recognize_screen_uses_the_anchors_the_side_menu_x_and_the_wave_counter(monkeypatch):
    m = manifest()
    frame = np.full((2560, 1080, 3), 240, np.uint8)
    lines = [(a["rect"][1], a["rect"][0], a["text"]) for a in m["screens"]["home"]["anchors"]]
    assert bootstrap.recognize_screen(frame, lines) == "home"
    from vision import wave_reader
    monkeypatch.setattr(wave_reader, "read_wave", lambda f: 17)
    assert bootstrap.recognize_screen(np.zeros((2560, 1080, 3), np.uint8), []) == "battle"
    x, y, w, h = m["side_menu"]["toggle"]["rect"]
    hud = np.zeros((2560, 1080, 3), np.uint8)
    cv2.rectangle(hud, (x + 8, y + 8), (x + w - 8, y + h - 8), (40, 220, 40), 6)         # the green X: in-run menu open
    assert bootstrap.recognize_screen(hud, []) == "battle_menu"
    monkeypatch.setattr(wave_reader, "read_wave", lambda f: None)
    assert bootstrap.recognize_screen(np.zeros((2560, 1080, 3), np.uint8), []) is None
    assert bootstrap.recognize_screen(np.zeros((1080, 2560, 3), np.uint8), []) is None      # not native


def _scanner(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    cal = calibrate.Calibration(_p(tmp_path), False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    cuts = []
    monkeypatch.setattr(cal, "cut", lambda phase, rel, crop, frame, name, extra=None, *, unique=True:
                        cuts.append((phase, rel, crop.shape[:2], name, extra)) or {"verified": True})
    return sc, cuts


def test_learned_cuts_take_the_same_rect_on_the_proven_screen_when_stable(tmp_path, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)
    sc, cuts = _scanner(tmp_path, monkeypatch)
    learned.record(sc.cal.p, "icons/guild_coin.png", [300, 900, 80, 80], "guild", "dashboard_cropper")
    frame = np.zeros((2560, 1080, 3), np.uint8)
    cv2.circle(frame, (340, 940), 30, (0, 200, 255), -1)
    assert sc.learned_cuts("guild", frame, frame.copy()) == {"icons/guild_coin.png": "verified"}
    phase, rel, shape, name, extra = cuts[0]
    assert (phase, rel, shape) == ("learned", "icons/guild_coin.png", (80, 80))
    assert extra["rect"] == [300, 900, 80, 80] and extra["screen"] == "guild" and extra["learned_from"] == "dashboard_cropper"
    # another screen: nothing; a moved control: not seen; already on disk: skipped
    assert sc.learned_cuts("home", frame, frame.copy()) == {}
    moved = np.zeros((2560, 1080, 3), np.uint8)
    cv2.circle(moved, (380, 940), 30, (0, 200, 255), -1)
    assert sc.learned_cuts("guild", frame, moved) == {"icons/guild_coin.png": "not_seen"} and len(cuts) == 1
    (tmp_path / "icons").mkdir()
    cv2.imwrite(str(tmp_path / "icons/guild_coin.png"), frame[900:980, 300:380])
    assert sc.learned_cuts("guild", frame, frame.copy()) == {} and len(cuts) == 1


def test_learned_cuts_honour_the_text_and_colour_checks_the_shipped_manifest_knows(tmp_path, monkeypatch):
    import settings
    from vision import textocr
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)
    sc, cuts = _scanner(tmp_path, monkeypatch)
    learned.record(sc.cal.p, "modules/assist_btn.png", [700, 1500, 250, 105], "modules", "dashboard_cropper")
    learned.record(sc.cal.p, "icons/tile_cart.png", [864, 19, 92, 92], "home", "bootstrap")
    frame = np.zeros((2560, 1080, 3), np.uint8)
    cv2.rectangle(frame, (710, 1510), (940, 1595), (120, 60, 60), -1)
    cv2.putText(frame, "Assist", (740, 1570), cv2.FONT_HERSHEY_DUPLEX, 1.6, (255, 255, 255), 2)
    monkeypatch.setattr(textocr, "read_lines", lambda img, scale=1: [(0, 0, "Primary")])
    assert sc.learned_cuts("modules", frame, frame.copy()) == {"modules/assist_btn.png": "not_seen"}   # wrong words
    monkeypatch.setattr(textocr, "read_lines", lambda img, scale=1: [(0, 0, "Assist")])
    assert sc.learned_cuts("modules", frame, frame.copy()) == {"modules/assist_btn.png": "verified"}
    assert cuts[-1][1] == "modules/assist_btn.png" and cuts[-1][3] == "ASSIST"
    # the cart is a colour icon: its gold must be lit at the learned rect
    cv2.rectangle(frame, (870, 25), (950, 105), (200, 200, 200), 6)                     # grey box: not the cart
    assert sc.learned_cuts("home", frame, frame.copy()) == {"icons/tile_cart.png": "not_seen"}
    cv2.rectangle(frame, (870, 25), (950, 105), (20, 190, 240), 6)
    assert sc.learned_cuts("home", frame, frame.copy()) == {"icons/tile_cart.png": "verified"}


def test_every_verified_cut_with_a_screen_builds_the_manifest(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(calibrate, "template_path", lambda rel: tmp_path / rel)
    p = _p(tmp_path)
    cal = calibrate.Calibration(p, False, bootstrap=True)
    rng = np.random.default_rng(3)
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[100:160, 200:300] = rng.integers(0, 255, (60, 100, 3), dtype=np.uint8)
    crop = frame[100:160, 200:300].copy()
    e = cal.cut("bootstrap", "home/battle_btn.png", crop, frame, "BATTLE", {"rect": [200, 100, 100, 60], "screen": "home"})
    assert e["verified"] and learned.known(p)["home/battle_btn.png"]["screen"] == "home"
    # no screen known: nothing learned (a battle-time cut with no frame proof)
    cal.cut("flow", "buttons/retry.png", crop, frame, "RETRY", {"rect": [200, 100, 100, 60]})
    assert "buttons/retry.png" not in learned.known(p)
    # the cropper: its recognized screen goes in too
    calibrate.record_manual_cut(p, "icons/guild_coin.png", crop, frame, [200, 100, 100, 60], screen="guild")
    assert learned.known(p)["icons/guild_coin.png"] == dict(learned.known(p)["icons/guild_coin.png"], screen="guild", rect=[200, 100, 100, 60], source="dashboard_cropper")
    calibrate.record_manual_cut(p, "floaters/second_wind.png", crop[:30, :30], crop, [0, 0, 30, 30], screen=None)
    assert "floaters/second_wind.png" not in learned.known(p)


def test_observe_pass_and_battle_flow_cut_learned_targets_on_the_screen_they_see(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    seen = []

    class Cal:
        p = _p(tmp_path)

    class Scanner:
        cal = Cal()
        read = staticmethod(lambda f: [])
        skipped = []
        def verify_asset_rels(self, rels, a, b, *, unique=True): return {r: "not_seen" for r in rels}
        def learned_cuts(self, screen, first, second): seen.append(screen); return {"icons/guild_coin.png": "verified"}
        def cut(self, spec, first, second, lines, icon=False, screen=None): seen.append((screen, spec["rel"]))
    from device import capture
    monkeypatch.setattr(capture, "grab", lambda: np.zeros((2560, 1080, 3), np.uint8))
    monkeypatch.setattr(bootstrap, "time", type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(lambda: 0.0)}))
    monkeypatch.setattr(bootstrap, "recognize_screen", lambda frame, lines=None, *, read=None: "guild")
    monkeypatch.setattr(fc, "capture_uw_switches", lambda cal, a, b: {})
    monkeypatch.setattr(fc, "capture_missing_uw_labels", lambda f: [])
    import settings
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)      # nothing on disk
    out = bootstrap._observe_pass(Scanner(), Cal(), [])
    shipped = [t["rel"] for t in manifest()["screens"]["guild"]["targets"]]
    assert out["icons/guild_coin.png"] == "verified" and all(out[r] == "verified" for r in shipped)
    assert seen == [("guild", r) for r in shipped] + ["guild"]     # the shipped targets for that screen, then the learned ones
    seen.clear()
    # the battle passes: only when the learned manifest has battle rows
    class Flow:
        s = Scanner(); frames = 0
        def grab(self): Flow.frames += 1; return np.zeros((2560, 1080, 3), np.uint8)
        def pause(self, _): pass
    assert fc.capture_learned_targets(Flow(), "battle") == {} and Flow.frames == 0
    learned.record(Cal.p, "buttons/perks.png", [700, 60, 200, 80], "battle", "dashboard_cropper")
    assert fc.capture_learned_targets(Flow(), "battle") == {"icons/guild_coin.png": "verified"} and Flow.frames == 2
    assert fc.capture_learned_targets(object(), "battle") == {}


def test_scan_plan_and_readiness_show_the_learned_spot(tmp_path, monkeypatch):
    from player import accounts, readiness, scan_plan
    cfg = {"active_instance": "main", "instances": {"main": {}}, "loadouts": {}}
    monkeypatch.setattr(accounts, "calibration_dir", lambda root, cfg: tmp_path)
    monkeypatch.setattr(accounts, "template_path", lambda root, cfg, rel, write=False: tmp_path / "t" / rel)
    learned.record({"state": str(tmp_path / "calibrate_state.json")}, "floaters/second_wind.png", [80, 1351, 65, 64], "battle", "observe")
    plan = scan_plan.plan(tmp_path, cfg)
    row = next(t for s in plan["steps"] for t in s["targets"] if t["rel"] == "floaters/second_wind.png")
    assert row["learned"]["rect"] == [80, 1351, 65, 64] and row["automatic"]
    body = {"kind": "coin", "loadout": None, "shopping": [], "gather": {}, "abilities": {"dm_below": 0.5}}
    result = readiness.check(tmp_path, cfg, body)
    sw = next(r for r in result["required"] if r["alternatives"] == ["floaters/second_wind.png"])
    assert sw["learned"]["floaters/second_wind.png"]["screen"] == "battle"
