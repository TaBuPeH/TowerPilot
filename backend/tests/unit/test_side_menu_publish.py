"""A target that raises inside verify_asset_rels must not drop the others.

Live 2026-10-12: the side-menu pass verified icons/tile_quests.png, the next
tile's hit raised KeyError 'inliers', and mapping.json still read 'pending'
for tile_quests - verify_asset_rels published only after its loop. The
flow_side_menu_error event said just "'inliers'": no target, no type.
"""
import json

import numpy as np
import pytest

from player import bootstrap, calibrate
from player import flow_capture as fc

QUESTS, EVENTS, GUILD = "icons/tile_quests.png", "icons/tile_events.png", "icons/tile_guild.png"


def _scanner(tmp_path, monkeypatch):
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    sc.asset_folder = tmp_path
    sc.asset_summary = {"version": "29.0.6"}
    sc.asset_mappings = {rel: {"status": "mapped", "candidates": [], "screen": "battle_menu", "verification": "pending"}
                         for rel in (QUESTS, EVENTS, GUILD)}
    good = {"rect": [100, 400, 120, 120], "inliers": 30, "asset_id": "q", "asset_name": "q", "asset_sha256": "q"}
    # tile_events' hit lacks 'inliers', as the live one did
    hits = {QUESTS: good, EVENTS: {k: v for k, v in good.items() if k != "inliers"}}
    sc.asset_hit = lambda rel, frame: hits.get(rel)
    sc.progress = lambda *a, **k: None
    sc.check_stop = lambda: None
    monkeypatch.setattr(sc.cal, "cut", lambda phase, rel, crop, fr, name, extra=None, *, unique=True: {"verified": True})
    frame = np.random.default_rng(3).integers(0, 255, (2560, 1080, 3), np.uint8)
    return sc, frame


def _published(tmp_path):
    return {rel: t["verification"] for rel, t in
            json.loads((tmp_path / "mapping.json").read_text(encoding="utf-8"))["targets"].items()}


def test_a_raising_target_keeps_the_targets_verified_before_it_published(tmp_path, monkeypatch):
    sc, frame = _scanner(tmp_path, monkeypatch)
    with pytest.raises(KeyError) as raised:
        sc.verify_asset_rels([QUESTS, EVENTS, GUILD], frame, frame.copy())
    assert _published(tmp_path) == {QUESTS: "verified", EVENTS: "pending", GUILD: "pending"}
    # the exception keeps its type and names the target it was raised on
    assert any(EVENTS in note for note in raised.value.__notes__)


def test_a_stop_request_still_publishes_what_was_verified_before_it(tmp_path, monkeypatch):
    from player.calibrate import Stopped
    sc, frame = _scanner(tmp_path, monkeypatch)
    calls = []

    def check_stop():
        calls.append(1)
        if len(calls) == 2:                     # the person pressed Stop before EVENTS
            raise Stopped("stopped")
    sc.check_stop = check_stop
    with pytest.raises(Stopped):
        sc.verify_asset_rels([QUESTS, EVENTS, GUILD], frame, frame.copy())
    assert _published(tmp_path) == {QUESTS: "verified", EVENTS: "pending", GUILD: "pending"}


def _side_menu_errors(sc, frame, monkeypatch):
    """Run capture_side_menu over an open menu; the flow_side_menu_error messages."""
    from runtime import logger
    events = []
    monkeypatch.setattr(logger, "event", lambda kind, **k: events.append((kind, k)))
    monkeypatch.setattr(fc, "_have_templates", lambda rels: set())
    monkeypatch.setattr(fc, "capture_hud_targets", lambda flow, screen: {})
    toggle = bootstrap.manifest()["side_menu"]["toggle"]
    # the menu is found OPEN: read as it is, nothing tapped
    monkeypatch.setattr(bootstrap, "icon_present", lambda f, spec: spec["hue"] == toggle["open_hue"])

    class Flow:
        s = sc
        taps = []
        def grab(self): return frame
        def pause(self, _): pass
        def tap(self, *a): self.taps.append(a)
        def progress(self, *a, **k): pass
        def skip(self, *a, **k): pass
    flow = Flow()
    with pytest.raises((KeyError, ValueError)):
        fc.capture_side_menu(flow)
    assert flow.taps == []
    return [k["error"] for kind, k in events if kind == "flow_side_menu_error"]


def test_the_side_menu_error_names_the_target_and_the_exception_type(tmp_path, monkeypatch):
    sc, frame = _scanner(tmp_path, monkeypatch)
    errors = _side_menu_errors(sc, frame, monkeypatch)
    assert len(errors) == 1 and "KeyError" in errors[0] and "inliers" in errors[0] and EVENTS in errors[0]
    assert _published(tmp_path)[QUESTS] == "verified"


def test_a_long_exception_message_cannot_push_the_target_out_of_the_logged_error(tmp_path, monkeypatch):
    # OpenCV errors run past 160 characters; the logged error is cut at 200
    sc, frame = _scanner(tmp_path, monkeypatch)
    hit = sc.asset_hit

    def asset_hit(rel, fr):
        if rel == EVENTS:
            raise ValueError("x" * 400)
        return hit(rel, fr)
    sc.asset_hit = asset_hit
    errors = _side_menu_errors(sc, frame, monkeypatch)
    assert len(errors) == 1 and errors[0].startswith("ValueError") and EVENTS in errors[0]
