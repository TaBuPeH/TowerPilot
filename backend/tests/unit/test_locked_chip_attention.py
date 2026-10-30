"""A locked Guardian chip must not hold the Guardian step at needs_attention.

verify_asset_rels lists a chip the game draws as a padlock as an OPTIONAL skip
and the Guardian screen map reads verified - but mapping_session.Session.run
counted every new skip, optional ones included, as attention, so the
navigation step still ended 'needs_attention'. A non-optional skip (a real
miss) still raises attention.
"""
import json

import numpy as np
import pytest

from player import asset_library, bootstrap
from player.bootstrap_layout import manifest
from player.mapping_session import Session

from test_learn_flow_gaps import LIVE_DY, LOCK_IN_SCOUT_SLOT, SCOUT, _chips, _guardian_scanner


def _guardian_step(tmp_path, monkeypatch, lock_rect, extra_skip=None):
    sc, frame, follow, _ = _guardian_scanner(tmp_path, monkeypatch, lock_rect)
    sc.current = "guild"
    session = Session(sc)

    class _Driver:
        # the Guardian control was verified and tapped; on arrival the screen's
        # artwork pass runs, as Session.collect -> Scanner.harvest does
        def step(self, edge):
            sc.current = edge["destination"]
            sc.verify_asset_rels(_chips(), frame, follow)
            if extra_skip:
                sc.skipped.append(extra_skip)
            sc.update_screen_map(edge["destination"])
            return True
    session.driver = _Driver()
    edge = {"id": "route_6", "source": "guild", "destination": "guardians", "action": "Guardian"}
    session.run([edge])
    return sc, sc.steps[sc.step_index("route_6")]


def test_an_optional_locked_chip_skip_leaves_the_guardian_step_done(tmp_path, monkeypatch):
    sc, step = _guardian_step(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)
    assert sc.state["screen_map"]["guardians"]["status"] == "verified"
    assert [s["optional"] for s in sc.skipped] == [True]
    assert step["status"] == "done"


def test_a_non_optional_skip_on_the_way_still_needs_attention(tmp_path, monkeypatch):
    sc, step = _guardian_step(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT,
                              extra_skip={"target": "guardian/x.png", "reason": "Label not visible"})
    assert sc.state["screen_map"]["guardians"]["status"] == "verified"
    assert step["status"] == "needs_attention"


def test_a_chip_not_found_and_not_locked_still_needs_attention(tmp_path, monkeypatch):
    sc, step = _guardian_step(tmp_path, monkeypatch, None)
    assert sc.state["screen_map"]["guardians"]["status"] == "needs_mapping"
    assert step["status"] == "needs_attention"


# ------------------------------------------------- LFS-4: 'locked' never sticks
def _scout_unlocked(sc):
    # the account unlocked Scout: its artwork now sits in its own slot
    x, y, w, h = manifest()["asset_bindings"][SCOUT]["reference_rect"]
    owned = sc.asset_hit
    scout = {"rect": [x, y + LIVE_DY, w, h], "inliers": 30, "asset_id": SCOUT,
             "asset_name": SCOUT, "asset_sha256": SCOUT}
    sc.asset_hit = lambda rel, frame: scout if rel == SCOUT else owned(rel, frame)


def _guardian_row(sc):
    sc.update_screen_map("guardians")
    row = sc.state["screen_map"]["guardians"]
    return row["status"], row["verified"], row["total"], {b["target"]: b["status"] for b in row["blocks"]}[SCOUT]


def test_a_locked_chip_verified_later_in_the_same_scan_is_no_longer_locked(tmp_path, monkeypatch):
    # the observe watch loop keeps one scanner between looks
    sc, frame, follow, _ = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)
    assert sc.verify_asset_rels(_chips(), frame, follow)[SCOUT] == "locked"
    assert _guardian_row(sc)[3] == "unavailable"
    _scout_unlocked(sc)
    assert sc.verify_asset_rels([SCOUT], frame, follow) == {SCOUT: "verified"}
    assert sc.asset_mappings[SCOUT]["verification"] == "verified"
    assert _guardian_row(sc) == ("verified", len(_chips()), len(_chips()), "verified")


def test_a_later_setup_maps_the_unlocked_chip_from_scratch(tmp_path, monkeypatch):
    # first setup: Scout is a padlock, published to mapping.json and the state
    sc, frame, follow, _ = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)
    del sc.publish_assets                       # the real writer, into tmp_path
    sc.verify_asset_rels(_chips(), frame, follow)
    saved = json.loads((tmp_path / "mapping.json").read_text(encoding="utf-8"))
    assert saved["targets"][SCOUT]["verification"] == "locked"
    # a later setup on the same state: a fresh Scanner and prepare_assets'
    # map_targets - nothing of the old 'locked' is read back
    later = bootstrap.Scanner(sc.cal, sc.state, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    later.asset_folder, later.asset_index, later.asset_summary = tmp_path, sc.asset_index, {"version": "29.0.6"}
    later.asset_mappings = asset_library.map_targets(later.asset_index, manifest()["asset_bindings"])
    assert later.asset_mappings[SCOUT]["verification"] == "pending"
    later.progress = lambda *a, **k: None
    later.check_stop = lambda: None
    later.asset_hit = sc.asset_hit
    _scout_unlocked(later)
    later.verify_asset_rels(_chips(), frame, follow)
    saved = json.loads((tmp_path / "mapping.json").read_text(encoding="utf-8"))
    assert saved["targets"][SCOUT]["verification"] == "verified"
    assert not [s for s in later.skipped if s["target"] == SCOUT]
    assert _guardian_row(later) == ("verified", len(_chips()), len(_chips()), "verified")


def test_locked_is_decided_only_on_a_frame_that_shows_the_guardian_screen(tmp_path, monkeypatch):
    # The observe pass hands verify_asset_rels every missing bound rel on
    # whatever screen is up, and its watch loop keeps the scanner between
    # looks. The owned chips verified on an earlier Guardian look; this look is
    # another screen (no chip artwork on it) with a padlock where Scout's slot
    # would be. Nothing on THIS frame proves the Guardian screen: not locked.
    sc, frame, follow, looked = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)
    owned = [rel for rel in _chips() if rel != SCOUT]
    assert set(sc.verify_asset_rels(owned, frame, follow).values()) == {"verified"}
    other = np.zeros_like(frame)
    sc.asset_hit = lambda rel, fr: None
    assert sc.verify_asset_rels([SCOUT], other, other.copy()) == {SCOUT: "not_seen"}
    assert not sc.skipped and looked == []


@pytest.mark.parametrize("dx,dy", [(330, 0), (-1000, 0), (0, 2000), (0, -2500)],
                         ids=["right-of-frame", "left-of-frame", "below-frame", "above-frame"])
def test_a_slot_wholly_off_the_frame_is_never_searched(tmp_path, monkeypatch, dx, dy):
    # A verified sibling seen a long way from its reference puts Scout's padded
    # slot past a frame edge. Clamped on one side only, its width went negative:
    # off the right edge cv2 raised on an empty crop, off the left numpy read
    # frame[:, 0:-n] (nearly the whole row) and a padlock anywhere in the band
    # was called Scout's. Nothing of the frame lies in the slot: not locked.
    sc, frame, follow, looked = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)
    sibling = next(rel for rel in _chips() if rel != SCOUT)
    x, y, w, h = manifest()["asset_bindings"][sibling]["reference_rect"]
    sc.asset_mappings[sibling]["observed_rect"] = [x + dx, y + dy, w, h]
    assert sc.locked(SCOUT, frame, follow, {sibling: "verified"}) is False
    assert looked == []


def test_a_target_that_raises_in_the_locked_pass_is_named_by_the_note(tmp_path, monkeypatch):
    from player import asset_verify

    sc, frame, follow, _ = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)

    def boom(folder, mapping, fr, definition):
        raise RuntimeError("padlock search blew up")
    monkeypatch.setattr(asset_verify, "best_match", boom)
    with pytest.raises(RuntimeError) as raised:
        sc.verify_asset_rels(_chips(), frame, follow)
    assert any(SCOUT in note for note in raised.value.__notes__)
