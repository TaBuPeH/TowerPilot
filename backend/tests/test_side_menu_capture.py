"""Four hand-cropped templates (2026-09-08) made automatic.

The person cropped the Quests tile and the cart tile from Home's side menu,
the Assist button from the module slot prompt, and the Second Wind badge from
a dying run - and nothing recorded where. Now: Full setup opens the side menu
it finds shut and cuts the column's tiles by the game's own artwork; the top
bar's cart and hamburger are cut at their fixed position by colour; the Second
Wind badge is an artwork-bound HUD target every battle frame pair tries and
the observe WATCH keeps looking for; the module restore step cuts the
Primary/Assist buttons when the prompt shows; and every cropper cut leaves a
report entry with its native rect. Readiness no longer asks for the slot
buttons v29 never renders.
"""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from player import bootstrap, calibrate, flow_capture as fc, readiness
from player.bootstrap_layout import manifest, scan_steps, screen_targets, writable_targets

TOGGLE = manifest()["side_menu"]["toggle"]


def test_manifest_v3_binds_the_badge_and_the_quests_tile_and_puts_the_hud_tiles_in_the_run():
    m = manifest()
    assert m["version"] >= 3
    ab = m["asset_bindings"]
    assert ab["floaters/second_wind.png"]["names"] == ["SecondWind"] and ab["floaters/second_wind.png"]["screen"] == "battle"
    from vision import detect
    (y0, y1), (x0, x1) = detect.SW_BAND
    assert ab["floaters/second_wind.png"]["search"] == [x0, y0, x1 - x0, y1 - y0]   # the band the runtime reads
    assert ab["icons/tile_quests.png"]["names"] == ["MissionsIcon"] and ab["icons/tile_quests.png"]["screen"] == "battle_menu"
    assert "icons/daily_missions.png" not in ab          # that file is the Missions header text, not a tile
    assert "icons/tile_cart.png" not in ab               # cut by position: the cart sprite never matched the glow
    # the cart and the hamburger are the in-run HUD, never Home (Home's top-right is a chevron)
    assert not any(t["rel"] in ("icons/tile_cart.png", "buttons/menu_closed_tile.png") for t in m["screens"]["home"]["targets"])
    hud = {t["rel"]: t for t in screen_targets("battle")}
    assert hud["icons/tile_cart.png"]["icon"] and hud["icons/tile_cart.png"]["hue"] == "gold"
    assert hud["buttons/menu_closed_tile.png"]["icon"] and hud["buttons/menu_closed_tile.png"]["hue"] == "bars"
    assert TOGGLE["rect"] == hud["buttons/menu_closed_tile.png"]["rect"] and m["side_menu"]["screen"] == "battle"
    assert {t["rel"] for t in screen_targets("battle_menu")} == {"buttons/menu_collapsed.png"}
    assert screen_targets("home") == m["screens"]["home"]["targets"] and screen_targets("nowhere") == []
    for rel in ("icons/tile_cart.png", "buttons/menu_closed_tile.png", "buttons/menu_collapsed.png",
                "icons/tile_quests.png", "floaters/second_wind.png"):
        assert rel in writable_targets(), rel
    assert "side_menu" not in [s["id"] for s in scan_steps()]
    assert fc.HUD_STATE_TARGETS == ("floaters/second_wind.png",)


def _box(frame, x, y, w, h, colour, thick=6):
    cv2.rectangle(frame, (x + 6, y + 6), (x + w - 6, y + h - 6), colour, thick)


def _bars(frame, x, y, w, h):
    _box(frame, x, y, w, h, (235, 235, 235), 3)                                  # the white outline box
    for i in range(3):                                                           # three white bars
        cv2.rectangle(frame, (x + 20, y + 18 + i * 22), (x + w - 20, y + 30 + i * 22), (235, 235, 235), -1)


def test_icon_checks_tell_the_gold_cart_the_three_bar_hamburger_and_the_green_x_apart():
    x, y, w, h = TOGGLE["rect"]
    spec = lambda hue: {"rect": [x, y, w, h], "hue": hue}
    frame = np.zeros((2560, 1080, 3), np.uint8)
    _bars(frame, x, y, w, h)
    assert bootstrap.icon_present(frame, spec("bars"))
    assert not bootstrap.icon_present(frame, spec("green")) and not bootstrap.icon_present(frame, spec("gold"))
    chevron = np.zeros((2560, 1080, 3), np.uint8)                                # Home's chevron: not bars
    cv2.line(chevron, (x + 15, y + 30), (x + w // 2, y + 65), (235, 235, 235), 9)
    cv2.line(chevron, (x + w // 2, y + 65), (x + w - 15, y + 30), (235, 235, 235), 9)
    assert not bootstrap.icon_present(chevron, spec("bars"))
    check = np.zeros((2560, 1080, 3), np.uint8)                                  # a checkmark: not bars
    cv2.line(check, (x + 15, y + 50), (x + 40, y + 75), (235, 235, 235), 9)
    cv2.line(check, (x + 40, y + 75), (x + w - 12, y + 25), (235, 235, 235), 9)
    assert not bootstrap.icon_present(check, spec("bars"))
    green = np.zeros((2560, 1080, 3), np.uint8)
    _box(green, x, y, w, h, (40, 220, 40))
    assert bootstrap.icon_present(green, spec("green")) and not bootstrap.icon_present(green, spec("bars"))
    gold = np.zeros((2560, 1080, 3), np.uint8)
    _box(gold, x, y, w, h, (20, 190, 240))
    assert bootstrap.icon_present(gold, spec("gold")) and not bootstrap.icon_present(gold, spec("bars"))


def _hud_frame(toggle):
    """An in-run HUD stand-in: the toggle box painted as the hamburger
    ('shut'), the green X ('open') or nothing."""
    frame = np.zeros((2560, 1080, 3), np.uint8)
    x, y, w, h = TOGGLE["rect"]
    if toggle == "shut":
        _bars(frame, x, y, w, h)
    elif toggle == "open":
        _box(frame, x, y, w, h, (40, 220, 40))
    return frame


class _Flow:
    """A battle flow whose grab paints the toggle from `state` and whose tap
    flips it, as the game does; the scanner records what it was asked."""
    def __init__(self, state):
        self.state, self.taps, self.skips = state, [], []

        class Scanner:
            def __init__(self):
                self.skipped, self.verified, self.cuts, self.learned = [], [], [], []
            read = staticmethod(lambda f: [])
            def verify_asset_rels(self, rels, a, b, *, unique=True):
                self.verified.append(tuple(rels)); return {r: "verified" for r in rels}
            def cut(self, spec, a, b, lines, icon=False, screen=None): self.cuts.append((spec["rel"], screen))
            def learned_cuts(self, screen, a, b): self.learned.append(screen); return {}
        self.s = Scanner()
    def grab(self): return _hud_frame(self.state["toggle"])
    def tap(self, x, y, reason):
        self.taps.append((x, y, reason)); self.state["toggle"] = {"shut": "open", "open": "shut"}[self.state["toggle"]]
    def pause(self, _): pass
    def progress(self, *a, **k): pass
    def skip(self, target, reason): self.skips.append((target, reason))


def test_side_menu_found_shut_is_opened_read_and_shut_again(tmp_path, monkeypatch):
    import settings
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)     # nothing on disk
    monkeypatch.setattr(fc, "_have_templates", lambda rels: set())
    state = {"toggle": "shut"}
    flow = _Flow(state)
    fc.capture_side_menu(flow)
    x, y, w, h = TOGGLE["rect"]
    assert [t[:2] for t in flow.taps] == [(x + w // 2, y + h // 2)] * 2          # open, then close - nothing else
    assert flow.s.verified == [("icons/tile_quests.png",)]                         # the column's artwork-bound tile
    assert flow.s.cuts == [("buttons/menu_collapsed.png", "battle_menu")]          # the X, cut while it showed
    assert flow.s.learned == ["battle_menu"] and state["toggle"] == "shut" and flow.skips == []


def test_side_menu_found_open_is_read_as_it_is_and_left_open(tmp_path, monkeypatch):
    import settings
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)
    monkeypatch.setattr(fc, "_have_templates", lambda rels: set())
    state = {"toggle": "open"}
    flow = _Flow(state)
    fc.capture_side_menu(flow)
    assert flow.taps == [] and flow.s.verified == [("icons/tile_quests.png",)] and state["toggle"] == "open"


def test_side_menu_toggle_missing_sends_no_tap(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    flow = _Flow({"toggle": "none"})
    assert fc.capture_side_menu(flow) == {}
    assert flow.taps == [] and flow.s.verified == [] and "no tap" in flow.skips[0][1]


def test_hud_targets_are_cut_from_the_run_by_their_colour_on_both_frames(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    cuts = []
    monkeypatch.setattr(cal, "cut", lambda phase, rel, crop, frame, name, extra=None, *, unique=True: cuts.append((rel, extra["screen"])) or {"verified": True})
    spec = {"rel": "icons/tile_cart.png", "rect": [864, 19, 92, 92], "icon": True, "hue": "gold"}
    lit = np.zeros((2560, 1080, 3), np.uint8)
    cv2.rectangle(lit, (870, 25), (950, 105), (20, 190, 240), 6)
    dark = np.zeros((2560, 1080, 3), np.uint8)
    sc.cut(spec, lit, dark, [], screen="battle")
    assert cuts == [] and sc.skipped[-1]["reason"].startswith("Icon not lit")
    sc.cut(spec, lit, lit.copy(), [], screen="battle")
    assert cuts == [("icons/tile_cart.png", "battle")]
    # the battle-pass helper drives the same cut for every HUD target still missing
    import settings
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)
    flow = _Flow({"toggle": "shut"})
    out = fc.capture_hud_targets(flow, "battle")
    assert set(out) == {"icons/tile_cart.png", "buttons/menu_closed_tile.png"}
    assert {c[0] for c in flow.s.cuts} == set(out) and flow.s.learned == ["battle"]


def test_observe_watch_keeps_looking_until_the_badge_shows_or_time_runs_out(tmp_path, monkeypatch):
    import settings
    from player import asset_library
    from runtime import logger
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"entries": [], "player": {}}))
    p = {"state": str(state), "stop": str(tmp_path / "stop"), "report": str(tmp_path / "report.json")}
    monkeypatch.setattr(asset_library, "acquire", lambda folder, serial, progress, stop: (tmp_path, {"installation": {"version": "x"}, "images": []}))
    monkeypatch.setattr(asset_library, "map_targets", lambda index, defs: {r: {"candidates": []} for r in defs})
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "s"})
    monkeypatch.setattr(settings, "template_path", lambda rel: tmp_path / rel)   # nothing on disk
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap.Scanner, "publish_assets", lambda self: None)
    monkeypatch.setattr(calibrate.Calibration, "save_report", lambda self: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(bootstrap, "time", type("T", (), {"sleep": staticmethod(lambda s: clock.__setitem__("t", clock["t"] + s)),
                                                          "time": staticmethod(lambda: clock["t"])}))
    looks = []

    def one_pass(scanner, cal, bound):
        looks.append(list(bound))
        # the badge is up only on the third look
        return {r: ("verified" if r == "floaters/second_wind.png" and len(looks) == 3 else "not_seen") for r in bound}
    monkeypatch.setattr(bootstrap, "_observe_pass", one_pass)
    result = bootstrap.observe(p, watch=30)
    assert result["floaters/second_wind.png"] == "verified" and len(looks) > 3
    assert all("floaters/second_wind.png" in b for b in looks[:3])
    assert all("floaters/second_wind.png" not in b for b in looks[3:])   # found: no longer looked for
    # a plain observe is one look, whatever it finds
    looks.clear()
    assert bootstrap.observe(p)["floaters/second_wind.png"] == "not_seen" and len(looks) == 1
    # the watch gives up at the deadline
    looks.clear()
    monkeypatch.setattr(bootstrap, "_observe_pass", lambda scanner, cal, bound: {r: "not_seen" for r in bound})
    bootstrap.observe(p, watch=3)
    assert clock["t"] >= 3 and len(looks) == 0


def test_battle_passes_try_the_second_wind_badge_without_recording_an_ability(monkeypatch):
    class Cal:
        player = {}

    class Scanner:
        def verify_asset_rels(self, rels, first, second, *, unique=True):
            return {r: "verified" for r in rels}

    class Flow:
        s = Scanner(); cal = Cal()
        def progress(self, *a, **k): pass
        def grab(self): return np.zeros((2560, 1080, 3), np.uint8)
        def pause(self, _): pass
    monkeypatch.setattr(fc, "_have_templates", lambda rels: set())
    out = fc.capture_artwork_targets(Flow(), fc.HUD_STATE_TARGETS, label="the Second Wind badge")
    assert out == {"floaters/second_wind.png": "verified"} and Flow.cal.player == {}   # a badge is not an ability
    src = Path(fc.__file__).read_text(encoding="utf-8")
    assert src.count("capture_artwork_targets(flow, HUD_STATE_TARGETS") == 1          # the top-tier battle, as the tower dies
    assert "HUD_ABILITY_TARGETS + HUD_STATE_TARGETS" in src                           # and the Tier-1 digit run
    # never a reason to START a battle: the digit-run gate ignores it
    assert "HUD_STATE_TARGETS" not in src.split("hud_targets = (")[1].split(")")[0]


def test_slot_prompt_cuts_primary_and_assist_from_their_pills(monkeypatch):
    from player import module_roundtrip
    from vision import pills
    cuts = []

    class Cal:
        p = {}
        def cut(self, phase, rel, crop, frame, name, extra=None, *, unique=True):
            cuts.append((rel, crop.shape[:2], name, extra["rect"]))
            return {"verified": True}
    driver = module_roundtrip.ManifestDriver(Cal())
    frame = np.zeros((2560, 1080, 3), np.uint8)
    monkeypatch.setattr(pills, "pills", lambda f, y0, y1, *a, **k: [{"rect": (120, 1500, 250, 105)}, {"rect": (700, 1500, 250, 105)}])
    driver._cut_slot_buttons(frame, frame.copy(), {"primary": (200, 1550), "assist": (800, 1550)})
    assert cuts == [("modules/primary_btn.png", (105, 250), "PRIMARY", [120, 1500, 250, 105]),
                    ("modules/assist_btn.png", (105, 250), "ASSIST", [700, 1500, 250, 105])]


def test_readiness_no_longer_asks_for_slot_buttons_v29_never_shows():
    cfg = {"active_instance": "main", "instances": {"main": {}}, "loadouts": {"eq": {"modules": [["shrink_ray", "primary"]]}}}
    body = {"kind": "coin", "loadout": "eq", "shopping": [], "gather": {}, "abilities": {}}
    rows = {row["alternatives"][0]: row for row in readiness.requirements(cfg, body)}
    assert "Equip modules" in rows["modules/v29_equip_btn.png"]["reasons"]
    assert "modules/primary_btn.png" not in rows and "modules/assist_btn.png" not in rows


def test_manual_cut_leaves_a_report_entry_with_its_native_rect(tmp_path, monkeypatch):
    from runtime import logger
    events = []
    monkeypatch.setattr(logger, "event", lambda *a, **k: events.append((a[0], k)))
    monkeypatch.setattr(calibrate, "template_path", lambda rel: tmp_path / rel)
    frame = np.zeros((400, 300, 3), np.uint8)
    cv2.rectangle(frame, (50, 60), (110, 100), (0, 200, 255), -1)
    crop = frame[50:110, 40:120].copy()
    (tmp_path / "floaters").mkdir()
    cv2.imwrite(str(tmp_path / "floaters/second_wind.png"), crop)
    p = {"state": str(tmp_path / "state.json"), "report": str(tmp_path / "report.json")}
    entry = calibrate.record_manual_cut(p, "floaters/second_wind.png", crop, frame, [40, 50, 80, 60])
    assert entry["phase"] == "cropper" and entry["rect"] == [40, 50, 80, 60] and entry["frame"] == [300, 400]
    assert entry["verified"] and entry["source"] == "dashboard_cropper" and entry["image_sha256"]
    saved = json.loads((tmp_path / "state.json").read_text())["entries"]
    assert [e["rel"] for e in saved] == ["floaters/second_wind.png"]
    assert (tmp_path / "report.json").exists()
    assert events and events[-1][0] == "calibrate_cut" and events[-1][1]["rect"] == [40, 50, 80, 60]
    # a second cut of the same name replaces its entry, never duplicates it
    calibrate.record_manual_cut(p, "floaters/second_wind.png", crop, frame, [41, 50, 80, 60])
    saved = json.loads((tmp_path / "state.json").read_text())["entries"]
    assert len(saved) == 1 and saved[0]["rect"] == [41, 50, 80, 60]


def test_dashboard_cropper_records_provenance_and_observe_accepts_a_watch(monkeypatch):
    src = Path(__file__).resolve().parents[2].joinpath("frontend", "dashboard.py").read_text(encoding="utf-8")
    assert "calibrate.record_manual_cut(" in src and '"--observe-watch"' in src
    html = Path(__file__).resolve().parents[2].joinpath("frontend", "webui", "index.html").read_text(encoding="utf-8")
    assert html.count("observeNow(120)") == 2 and "async function observeNow(watch)" in html
