"""LF-1 (2026-10-12 Learn-screens run): the side-menu pass raised KeyError
'inliers' right after "Verifying extracted artwork: event icon" and verified
none of the menu's remaining tiles. icons/tile_events.png is the one
`outline_shape` binding, and locate_outline was the one locator whose hit had
no 'inliers'. The contract every locator honours: 'inliers' is the SIFT RANSAC
inlier count, 0 for the keypoint-free shape matchers (silhouette, outline), so
Scanner.verify_asset_rels records it as source_inliers for every hit.
Synthetic art only: a hollow white glyph, drawn thicker and larger on screen
than in the sprite, as the in-run tile draws it.
"""
import time

import cv2
import numpy as np

from player import asset_verify, bootstrap, calibrate
from player.bootstrap_layout import manifest

EVENTS = "icons/tile_events.png"
GLYPH = np.array([[10, 6], [66, 6], [66, 64], [38, 46], [10, 64]])   # hollow tag outline, inside the crop


def _sprite():
    # transparent 200x200 sprite; the white outline sits inside the binding's source_crop
    sx, sy, sw, sh = manifest()["asset_bindings"][EVENTS]["source_crop"]
    image = np.zeros((200, 200, 4), np.uint8)
    cv2.polylines(image[sy:sy + sh, sx:sx + sw], [GLYPH], True, (255, 255, 255, 255), 3)
    return image


def _menu_frame():
    # dark side-menu column; the glyph drawn 1.3x larger with an 8 px outline
    frame = np.full((2560, 1080, 3), 30, np.uint8)
    cv2.polylines(frame, [(GLYPH * 1.3).astype(np.int32) + (920, 400)], True, (255, 255, 255), 8)
    return frame


def test_an_outline_hit_reports_zero_inliers_like_the_other_shape_matcher():
    definition = manifest()["asset_bindings"][EVENTS]
    sx, sy, sw, sh = definition["source_crop"]
    hit = asset_verify.locate_outline(_sprite()[sy:sy + sh, sx:sx + sw], _menu_frame(), definition["search"])
    assert hit and hit["outline_score"] >= .9
    assert hit["inliers"] == 0


def test_the_event_tile_found_by_its_outline_is_cut_and_the_menu_pass_goes_on(tmp_path, monkeypatch):
    (tmp_path / "images").mkdir()
    assert cv2.imwrite(str(tmp_path / "images" / "event.png"), _sprite())
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    sc.asset_folder = tmp_path
    sc.asset_summary = {"version": "29.0.6"}
    rels = [rel for rel, d in manifest()["asset_bindings"].items() if d["screen"] == "battle_menu"]
    assert EVENTS in rels and rels[-1] != EVENTS
    sc.asset_mappings = {rel: {"status": "mapped", "screen": "battle_menu", "verification": "pending",
                               "candidates": [{"id": "e", "name": "event icon", "sha256": "ev", "file": "images/event.png"}]
                               if rel == EVENTS else []} for rel in rels}
    sc.publish_assets = lambda: None
    sc.progress = lambda *a, **k: None
    sc.check_stop = lambda: None
    cuts = {}
    monkeypatch.setattr(sc.cal, "cut", lambda phase, rel, crop, fr, name, extra=None, *, unique=True:
                        cuts.update({rel: extra}) or {"verified": True, "t": time.time()})
    frame = _menu_frame()
    out = sc.verify_asset_rels(rels, frame, frame.copy())
    assert out[EVENTS] == "verified"
    assert cuts[EVENTS]["source_inliers"] == 0 and cuts[EVENTS]["asset_sha256"] == "ev"
    # the tiles after it were still looked for: nothing on this frame, so not_seen
    assert all(out[rel] == "not_seen" for rel in rels if rel != EVENTS)
