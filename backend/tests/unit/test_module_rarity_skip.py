"""The Modules walk counts rare-blue tiles and never opens them.

The Learn-screens Modules step used to tap every inventory tile to read its
rarity and name (~3 s each). Nothing below Epic is wanted, and the rarity is
drawn on the tile itself (a rare-blue diamond ring), so `_walk_grid` classifies
each tile from the frame it already grabbed and opens only tiles that are NOT
positively rare-blue. Synthetic frames only; no device, no game images.
"""
import hashlib

import cv2
import numpy as np
import pytest

COLS = (126, 329, 533, 736, 939)
ROW0, PITCH, PAGE_SHIFT = 1198, 203, 794

# ring hue (OpenCV 0-179) per tile look; RING = (inner, outer) diamond distance
HUES = {"rare": 97, "epic": 148, "legendary": 16, "mythic": 171,
        "green": 60,            # a selected tile's big green check
        "near_hi": 108, "near_lo": 84}     # just outside shatter.BLUE_HUE (88, 104)
FULL_RING, THIN_RING = (72, 96), (93, 96)


def _bgr(hue):
    return tuple(int(x) for x in cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0])


def _grid(kind_of, seed=3):
    """One tall canvas of 14 tile rows. kind_of(row, col) -> a HUES key, or
    'faint' (rare hue but a hairline ring: too few lit pixels to trust)."""
    rng = np.random.default_rng(seed)
    canvas = np.zeros((2560 + 2000, 1080, 3), np.uint8)
    for row in range(14):
        cy = ROW0 + row * PITCH
        for col, cx in enumerate(COLS):
            kind = kind_of(row, col)
            hue, (inner, outer) = (HUES["rare"], THIN_RING) if kind == "faint" else (HUES[kind], FULL_RING)
            yy, xx = np.mgrid[cy - 100:cy + 100, cx - 100:cx + 100]
            d = np.abs(yy - cy) + np.abs(xx - cx)
            box = canvas[cy - 100:cy + 100, cx - 100:cx + 100]
            box[(d > inner) & (d < outer)] = _bgr(hue)
            art = d < 60                                     # the module art: rarity says nothing here
            box[art] = rng.integers(0, 256, (int(art.sum()), 3), dtype=np.uint8)
    return canvas


def _frame(canvas, shift):
    frame = canvas[shift:shift + 2560].copy()
    frame[2260:] = 0                                         # the filter bar
    return frame


class _Walk:
    """Run the real calibrate._walk_grid over three settled frames (rows 0-4,
    rows 4-8, the same again = the end) with the device layer faked. Records
    every tile that _inspect was asked to open as (canvas row, col) and every
    template that was cut. `rarity_of` maps (row, col) to the rarity the panel
    reads for that tile (default 'epic')."""

    def __init__(self, tmp_path, monkeypatch, kind_of, resume=None, rarity_of=None):
        from interactions import inventory
        from player import calibrate
        import runtime.logger as lg
        canvas = _grid(kind_of)
        self.frames = iter([_frame(canvas, 0), _frame(canvas, PAGE_SHIFT), _frame(canvas, PAGE_SHIFT)])
        self.moves = iter([PAGE_SHIFT, 0])
        self.shift = 0
        self.opened, self.events, self.messages, self.cuts = [], [], [], []
        self.rarity_of = rarity_of or {}
        self.inventory, self.calibrate = inventory, calibrate

        monkeypatch.setattr(inventory, "_close_panel", lambda tries=4: True)
        monkeypatch.setattr(inventory, "park_top", lambda tries=5: None)
        monkeypatch.setattr(inventory, "settle", self._settle)
        monkeypatch.setattr(inventory, "next_page", lambda: next(self.moves))
        monkeypatch.setattr(inventory, "take_page_frame", lambda: None)   # no hand-off from another test's real next_page()
        monkeypatch.setattr(calibrate, "_inspect", self._inspect)
        monkeypatch.setattr(calibrate, "module_slug", lambda name: name.lower().replace(" ", "_"))
        monkeypatch.setattr(calibrate.Calibration, "cut", self._cut)
        monkeypatch.setattr(lg, "event", lambda kind, **kw: self.events.append((kind, kw)))
        p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
        self.cal = calibrate.Calibration(p, overwrite=False)
        self._served = 0
        if resume is not None:
            row, col, rarity = resume
            cy = ROW0 + row * PITCH
            icon = inventory._tile_icon(_frame(canvas, 0), COLS[col], cy)
            saved = tmp_path / "saved.png"
            cv2.imwrite(str(saved), icon)
            monkeypatch.setattr(calibrate, "template_path", lambda rel: saved)
            self.cal.entries = [{"phase": "modules", "verified": True, "rel": "modules/resumed.png",
                                 "rarity": rarity, "image_sha256": hashlib.sha256(saved.read_bytes()).hexdigest()}]

    def _settle(self, *a, **k):
        self.shift = 0 if self._served == 0 else PAGE_SHIFT
        self._served += 1
        return next(self.frames)

    def _inspect(self, cx, cy, icon=None):
        row = round((cy + self.shift - ROW0) / PITCH)
        tile = (row, COLS.index(cx))
        self.opened.append(tile)
        return f"Module {row}x{tile[1]}", self.rarity_of.get(tile, "epic")

    def _cut(self, phase, rel, crop, frame, name, extra=None):
        self.cuts.append(rel)
        return {"rel": rel, **(extra or {})}

    def run(self):
        self.slugs, self.copies = self.calibrate._walk_grid(self.cal, self.messages.append)
        return self

    def below_epic_event(self):
        return [kw for k, kw in self.events if k == "calibrate_modules_below_epic"]


def test_fixture_rings_classify_as_designed():
    """The synthetic looks are what the tests below claim they are."""
    from interactions.shatter import tile_is_blue
    kinds = {"rare": True, "epic": False, "legendary": False, "mythic": False,
             "green": False, "near_hi": False, "near_lo": False, "faint": False}
    canvas = _grid(lambda r, c: list(kinds)[(r * 5 + c) % len(kinds)])   # kind i at flat tile index i
    frame = _frame(canvas, 0)
    for i, (kind, expected) in enumerate(kinds.items()):
        row, col = divmod(i, 5)
        assert tile_is_blue(frame, COLS[col], ROW0 + row * PITCH) is expected, kind


def _mixed(row, col):
    return "rare" if (row + col) % 2 == 0 else ("epic", "legendary", "mythic")[(row + col) % 3]


def test_rare_blue_tiles_are_counted_and_never_opened(tmp_path, monkeypatch):
    w = _Walk(tmp_path, monkeypatch, _mixed).run()
    tiles = [(r, c) for r in range(9) for c in range(5)]
    rare = [(r, c) for r, c in tiles if _mixed(r, c) == "rare"]
    other = [(r, c) for r, c in tiles if _mixed(r, c) != "rare"]
    assert sorted(w.opened) == sorted(other)             # each non-rare tile opened exactly once
    assert len(other) == 22 and len(rare) == 23
    assert len(w.copies) == 22 and len(w.slugs) == 22     # nothing Rare in slugs/copies
    assert all(cp["rarity"] == "epic" for cp in w.copies)
    assert w.below_epic_event() == [{"count": 23, "opened": 22, "opened_below_epic": 0}]


def test_overlap_rows_are_counted_once(tmp_path, monkeypatch):
    """Row 4 is on both pages; an all-Rare inventory is 45 tiles, not 50."""
    w = _Walk(tmp_path, monkeypatch, lambda r, c: "rare").run()
    assert w.opened == [] and w.copies == [] and w.slugs == []
    assert w.below_epic_event() == [{"count": 45, "opened": 0, "opened_below_epic": 0}]


@pytest.mark.parametrize("look", ["green", "near_hi", "near_lo", "faint"])
def test_anything_not_positively_rare_blue_is_still_opened(tmp_path, monkeypatch, look):
    """Allowlist only: another hue, a selected tile, a hue just outside the
    rare band, or a ring with too few lit pixels costs a tap, never a module."""
    w = _Walk(tmp_path, monkeypatch, lambda r, c: look if (r, c) == (1, 2) else "rare").run()
    assert w.opened == [(1, 2)]
    assert w.below_epic_event() == [{"count": 44, "opened": 1, "opened_below_epic": 0}]
    assert [cp["slug"] for cp in w.copies] == ["module_1x2"]


def test_count_is_surfaced_while_walking(tmp_path, monkeypatch):
    w = _Walk(tmp_path, monkeypatch, _mixed).run()
    counted = [m for m in w.messages if "below Epic" in m]
    assert counted and counted[-1] == "Modules: counted 23 below Epic (23 not opened)"
    assert w.below_epic_event()[0]["count"] == 23


@pytest.mark.parametrize("look,rarity,is_copy,counted", [("rare", "rare", False, 1), ("epic", "epic", True, 0)])
def test_resumed_report_entry_with_a_matching_tile(tmp_path, monkeypatch, look, rarity, is_copy, counted):
    """The 'seen' list preloaded from an earlier report: a Rare entry needs no
    report row and its Rare tile is still only counted; an Epic entry keeps
    its old behaviour (the tile is a copy, counted without a tap)."""
    kinds = lambda r, c: look if (r, c) == (0, 0) else "epic"
    w = _Walk(tmp_path, monkeypatch, kinds, resume=(0, 0, rarity)).run()
    assert (0, 0) not in w.opened
    assert [cp["slug"] for cp in w.copies if cp["slug"] == "resumed"] == (["resumed"] if is_copy else [])
    assert w.below_epic_event()[0]["count"] == counted


@pytest.mark.parametrize("rarity", ["rare", "rare+", "common"])
def test_a_rare_tile_the_ring_classifier_missed_is_counted_not_recorded(tmp_path, monkeypatch, rarity):
    """tile_is_blue fails safe by tapping. When the panel then reads Rare or Common
    (a ring too faint to trust), the tap is spent but the tile is still below Epic: it is
    counted with the blue ones, never recorded in slugs/copies and never cut, so
    a Rare frame cannot become a module's only template. The event keeps the two
    figures apart: `opened` tiles were tapped, `opened_below_epic` of them were read
    below Epic. So does the progress line: one of the 45 counted tiles was opened."""
    w = _Walk(tmp_path, monkeypatch, lambda r, c: "faint" if (r, c) == (1, 2) else "rare",
              rarity_of={(1, 2): rarity}).run()
    assert w.opened == [(1, 2)]
    assert w.copies == [] and w.slugs == [] and w.cuts == []
    assert w.below_epic_event() == [{"count": 45, "opened": 1, "opened_below_epic": 1}]
    assert [m for m in w.messages if "below Epic" in m][-1] == "Modules: counted 45 below Epic (44 not opened)"


def test_a_missed_rare_tile_does_not_hide_the_epic_tiles_around_it(tmp_path, monkeypatch):
    """Only the Rare read is dropped: the Epic tile next to it is recorded as before."""
    kinds = lambda r, c: "faint" if (r, c) in ((1, 2), (1, 3)) else "rare"
    w = _Walk(tmp_path, monkeypatch, kinds, rarity_of={(1, 2): "rare"}).run()
    assert w.opened == [(1, 2), (1, 3)]
    assert [cp["slug"] for cp in w.copies] == ["module_1x3"] and w.cuts == ["modules/module_1x3.png"]
    assert w.below_epic_event() == [{"count": 44, "opened": 2, "opened_below_epic": 1}]


def test_the_count_is_kept_on_the_player_and_survives_a_save(tmp_path, monkeypatch):
    """player.modules_below_epic is the integer the walk counted: 0 (not absent)
    when nothing was below Epic, replaced (not added to) by the next walk, and
    carried by the state file and the report like the other player keys."""
    w = _Walk(tmp_path, monkeypatch, _mixed).run()
    assert w.cal.player["modules_below_epic"] == 23
    w.cal.p["state"], w.cal.p["report"] = str(tmp_path / "state.json"), str(tmp_path / "report.json")
    w.calibrate._state_save(w.cal.p, {"player": w.cal.player})
    assert w.calibrate._state_load(w.cal.p)["player"]["modules_below_epic"] == 23
    w.cal.save_report()
    import json
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["player"]["modules_below_epic"] == 23
    # a re-run starts from the saved player (main() loads it): the new count replaces the old one
    none_below = _Walk(tmp_path, monkeypatch, lambda r, c: "epic")
    none_below.cal.player["modules_below_epic"] = 23
    assert none_below.run().cal.player["modules_below_epic"] == 0


def test_the_count_reaches_the_scan_state_and_the_draft_and_outlives_a_later_merge(tmp_path, monkeypatch):
    """calibrate._merge_draft carries every player key to scan_state.json and the
    draft yaml; a merge that does not hold the key (another phase) leaves it."""
    import json
    import yaml
    from player import scan
    sp = {"state": str(tmp_path / "scan_state.json"), "draft": str(tmp_path / "x.draft.yaml")}
    monkeypatch.setattr(scan, "_paths", lambda: sp)
    w = _Walk(tmp_path, monkeypatch, _mixed).run()
    w.calibrate._merge_draft(w.cal.player)
    w.calibrate._merge_draft({"card_presets": ["a"]})
    assert json.loads((tmp_path / "scan_state.json").read_text(encoding="utf-8"))["player"]["modules_below_epic"] == 23
    assert yaml.safe_load((tmp_path / "x.draft.yaml").read_text(encoding="utf-8"))["player"]["modules_below_epic"] == 23


def test_a_stale_page_frame_from_another_test_is_never_read_as_page_one(tmp_path, monkeypatch):
    """inventory._page_frame is module state a real next_page() leaves behind.
    The faked walk must not hand it out as page 1: with a stale all-Rare frame
    preset, the walk test below still sees its own frames."""
    from interactions import inventory
    monkeypatch.setattr(inventory, "_page_frame", _frame(_grid(lambda r, c: "rare", seed=9), 0), raising=False)
    test_rare_blue_tiles_are_counted_and_never_opened(tmp_path, monkeypatch)


@pytest.mark.parametrize("rarity", ["rare", "common"])
def test_a_resumed_rare_report_entry_is_not_preloaded_as_a_known_module(tmp_path, monkeypatch, rarity):
    """Reports written before Rare was skipped still list Rare modules. A missed
    Rare tile (faint ring) that is pixel-identical to such an entry must not be
    recorded as a copy of it: it is opened and counted like any missed Rare tile."""
    kinds = lambda r, c: "faint" if (r, c) == (0, 0) else "rare"
    w = _Walk(tmp_path, monkeypatch, kinds, resume=(0, 0, rarity), rarity_of={(0, 0): rarity}).run()
    assert w.opened == [(0, 0)]
    assert w.copies == [] and w.slugs == [] and w.cuts == []
    assert w.below_epic_event() == [{"count": 45, "opened": 1, "opened_below_epic": 1}]
