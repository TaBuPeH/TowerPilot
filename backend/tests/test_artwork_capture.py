"""Controls are found on screen by the installed game's own artwork.

"With the artwork you can screenshot and search the screenshot for what you
need" (user, 2026-09-08). The manifest binds the HUD ability buttons and the
UW panel switches to their APK sprites; bootstrap.Scanner.verify_asset_rels
locates them on two consecutive frames and cuts the on-screen pixels; the
battle passes call it where those controls are visible. Positive-only: a
control that is not on screen is "not seen", never a refusal.
"""
import json
from pathlib import Path

import numpy as np

from player import bootstrap, calibrate, flow_capture as fc
from player.bootstrap_layout import manifest, writable_targets


def test_manifest_binds_the_hud_controls_inside_the_native_frame():
    ab = manifest()["asset_bindings"]
    assert ab["buttons/nuke.png"]["names"] == ["protector-nuke"]        # the HUD button, not the auto-nuke perk
    assert ab["buttons/demon_mode.png"]["names"] == ["DemonMode"]
    assert ab["uw/toggle_on.png"]["names"] == ["switch-on"]
    assert ab["uw/toggle_off.png"]["names"] == ["switch-off"]
    for rel in fc.HUD_ABILITY_TARGETS + fc.UW_SWITCH_TARGETS:
        x, y, w, h = ab[rel]["search"]
        assert 0 <= x < x + w <= 1080 and 0 <= y < y + h <= 2560, rel
        assert ab[rel]["screen"] == "battle" and not ab[rel]["locate_navigation"]
        assert rel in writable_targets()


def _scanner(tmp_path, hits):
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    sc.asset_folder = tmp_path
    sc.asset_summary = {"version": "29.0.2"}
    sc.asset_mappings = {rel: {"status": "mapped", "candidates": [], "screen": "battle", "verification": "pending"}
                         for rel in fc.HUD_ABILITY_TARGETS + fc.UW_SWITCH_TARGETS}
    sc.asset_hit = lambda rel, frame: hits.get(rel)
    sc.publish_assets = lambda: None
    sc.progress = lambda *a, **k: None
    sc.check_stop = lambda: None
    return sc


def test_verify_asset_rels_cuts_a_stable_hit_and_marks_the_rest_not_seen(tmp_path, monkeypatch):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[1457:1526, 55:121] = 200                      # a "button" where the artwork was found
    hit = {"rect": [55, 1457, 66, 69], "inliers": 9, "asset_id": "a", "asset_name": "protector-nuke", "asset_sha256": "s"}
    sc = _scanner(tmp_path, {"buttons/nuke.png": hit})
    cuts = []
    monkeypatch.setattr(sc.cal, "cut", lambda phase, rel, crop, fr, name, extra=None, *, unique=True:
                        cuts.append((rel, crop.shape, name, unique)) or {"verified": True})
    out = sc.verify_asset_rels(list(fc.HUD_ABILITY_TARGETS), frame, frame.copy())
    assert out == {"buttons/nuke.png": "verified", "buttons/demon_mode.png": "not_seen"}
    assert cuts == [("buttons/nuke.png", (69, 66, 3), "protector-nuke", True)]
    assert sc.asset_mappings["buttons/nuke.png"]["observed_rect"] == [55, 1457, 66, 69]
    # switches repeat on screen: the cut is told so
    cuts.clear()
    sc.asset_hit = lambda rel, frame: hit if rel == "uw/toggle_on.png" else None
    sc.verify_asset_rels(list(fc.UW_SWITCH_TARGETS), frame, frame.copy(), unique=False)
    assert cuts[0][0] == "uw/toggle_on.png" and cuts[0][3] is False


def test_verify_asset_rels_refuses_a_hit_that_moved_between_frames(tmp_path, monkeypatch):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    seq = iter([{"rect": [55, 1457, 66, 69], "inliers": 9, "asset_id": "a", "asset_name": "n", "asset_sha256": "s"},
                {"rect": [90, 1457, 66, 69], "inliers": 9, "asset_id": "a", "asset_name": "n", "asset_sha256": "s"}])
    sc = _scanner(tmp_path, {})
    sc.asset_hit = lambda rel, fr: next(seq)
    monkeypatch.setattr(sc.cal, "cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not cut")))
    assert sc.verify_asset_rels(["buttons/nuke.png"], frame, frame.copy()) == {"buttons/nuke.png": "unstable"}


def test_battle_pass_records_a_verified_ability_and_skips_targets_on_disk(tmp_path, monkeypatch):
    class Cal:
        player = {}

    class Scanner:
        def __init__(self):
            self.calls = []

        def verify_asset_rels(self, rels, first, second, *, unique=True):
            self.calls.append((tuple(rels), unique))
            return {rels[0]: "verified", **{r: "not_seen" for r in rels[1:]}}

    class Flow:
        def __init__(self):
            self.s = Scanner(); self.cal = Cal(); self.frames = 0
        def progress(self, *a, **k): pass
        def grab(self): self.frames += 1; return np.zeros((2560, 1080, 3), np.uint8)
        def pause(self, _): pass

    monkeypatch.setattr(fc, "_have_templates", lambda rels: set())
    flow = Flow()
    out = fc.capture_artwork_targets(flow, fc.HUD_ABILITY_TARGETS)
    assert out["buttons/nuke.png"] == "verified" and flow.frames == 2
    assert flow.cal.player == {"abilities": {"nuke": True}, "abilities_verified_by": "setup"}   # proven on the HUD, positive-only
    assert flow.s.calls == [(fc.HUD_ABILITY_TARGETS, True)]
    # already on disk: nothing to do, no frames taken
    monkeypatch.setattr(fc, "_have_templates", lambda rels: set(rels))
    assert fc.capture_artwork_targets(flow, fc.UW_SWITCH_TARGETS, unique=False) == {}
    assert flow.frames == 2
    # a flow without a scanner (stand-alone harness): a no-op
    assert fc.capture_artwork_targets(object(), fc.HUD_ABILITY_TARGETS) == {}


def test_observe_searches_two_frames_without_taps_and_records_what_it_found(tmp_path, monkeypatch):
    """bootstrap.observe: the read-only 'capture from the screen now'."""
    import settings
    from device import capture
    from player import asset_library
    from runtime import logger
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"entries": [], "player": {"card_presets": []}}))
    p = {"state": str(state), "stop": str(tmp_path / "stop"), "report": str(tmp_path / "report.json")}
    grabs = []
    monkeypatch.setattr(capture, "grab", lambda: grabs.append(1) or np.zeros((2560, 1080, 3), np.uint8))
    monkeypatch.setattr(asset_library, "acquire", lambda folder, serial, progress, stop: (tmp_path, {"installation": {"version": "x"}, "images": []}))
    monkeypatch.setattr(asset_library, "map_targets", lambda index, defs: {r: {"candidates": []} for r in defs})
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "s"})
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)   # nothing on disk: all bound targets wanted
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "time", type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(lambda: 0.0)}))
    calls = []
    monkeypatch.setattr(bootstrap.Scanner, "verify_asset_rels",
                        lambda self, rels, a, b, unique=True: calls.append((tuple(rels), unique)) or
                        {r: ("verified" if r == "buttons/nuke.png" else "not_seen") for r in rels})
    monkeypatch.setattr(bootstrap.Scanner, "publish_assets", lambda self: None)
    monkeypatch.setattr(calibrate.Calibration, "save_report", lambda self: None)
    result = bootstrap.observe(p)
    assert result["buttons/nuke.png"] == "verified" and len(grabs) == 2
    uniq = [c for c in calls if c[1]]; rep = [c for c in calls if not c[1]]
    assert set(fc.UW_SWITCH_TARGETS) == set(rep[0][0]) and "buttons/nuke.png" in uniq[0][0]
    saved = json.loads(state.read_text())
    assert saved["player"]["abilities"] == {"nuke": True} and saved["player"]["abilities_verified_by"] == "setup"
    assert saved["player"]["card_presets"] == []                       # the rest of the record is kept


def _panel_frame(state_by_row):
    """A synthetic UW tab: one weapon label (a bright bar) per row with a
    pill under it - teal-green when ON, grey with a light rim when OFF."""
    import cv2
    frame = np.full((2560, 1080, 3), (60, 30, 25), np.uint8)
    labels = {}
    for i, (name, state) in enumerate(state_by_row.items()):
        lx, ly = 40, 1930 + i * 210
        label = np.zeros((55, 240, 3), np.uint8)
        cv2.putText(label, name.upper()[:9], (5, 40), cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2)
        frame[ly:ly + 55, lx:lx + 240] = label
        labels[name] = label.copy()
        px, py = lx + 20, ly + 55 + 47
        colour = (150, 210, 40) if state == "on" else (70, 70, 70)
        cv2.rectangle(frame, (px, py), (px + 83, py + 44), colour, -1)
        if state == "off":
            cv2.rectangle(frame, (px, py), (px + 83, py + 44), (170, 170, 170), 2)
    return frame, labels


def test_uw_switches_are_found_under_the_labels_and_classified():
    frame, labels = _panel_frame({"chain": "off", "death": "on", "chrono": "on"})
    found = {(s, n): rect for s, rect, n in fc.find_uw_switches(frame, labels)}
    assert ("off", "chain") in found and ("on", "death") in found and ("on", "chrono") in found
    x, y, w, h = found[("on", "death")]
    assert abs(w - (83 + 2 * fc.SWITCH_PAD)) <= 2 and abs(h - (44 + 2 * fc.SWITCH_PAD)) <= 2   # cv2 draws inclusive edges
    assert abs(y - (1930 + 210 + 55 + 47 - fc.SWITCH_PAD)) <= 1 and abs(x - (40 + 20 - fc.SWITCH_PAD)) <= 1
    assert fc.find_uw_switches(np.zeros((2560, 1080, 3), np.uint8), labels) == []


def test_capture_uw_switches_cuts_one_of_each_state_when_both_frames_agree(tmp_path, monkeypatch):
    import cv2
    import settings
    from player import accounts
    frame, labels = _panel_frame({"chain_lightning": "off", "death_wave": "on", "chronofield": "on"})
    for name, img in labels.items():
        path = tmp_path / "uw" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), img)
    monkeypatch.setattr(accounts, "template_path", lambda root, cfg, rel, write=False: tmp_path / rel)
    cuts = []

    class Cal:
        def cut(self, phase, rel, crop, fr, name, extra=None, *, unique=True):
            cuts.append((rel, crop.shape[:2], name, unique, extra["verifier"]))
            return {"verified": True}
    out = fc.capture_uw_switches(Cal(), frame, frame.copy())
    assert out == {"uw/toggle_on.png": "verified", "uw/toggle_off.png": "verified"}
    assert {c[0] for c in cuts} == {"uw/toggle_on.png", "uw/toggle_off.png"}
    assert all(c[3] is False and abs(c[1][0] - (44 + 2 * fc.SWITCH_PAD)) <= 4 and abs(c[1][1] - (83 + 2 * fc.SWITCH_PAD)) <= 4 for c in cuts)
    # a switch that moved between the frames is not cut
    cuts.clear()
    moved, _ = _panel_frame({"chain_lightning": "on", "death_wave": "off", "chronofield": "off"})
    assert fc.capture_uw_switches(Cal(), frame, moved) == {} and cuts == []
