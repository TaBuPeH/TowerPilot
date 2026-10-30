"""The Modules walk recognises another copy of a module it already read.

A copy sits a few pixels off the lattice from the tile that was opened, so a
pixel-for-pixel comparison never matched and every copy was tapped (~3 s each).
`_walk_grid` now compares tile crops with calibrate.match_module_crop (shift
tolerant) AND requires the same border colour, read off the grid frame, AND
the same plus state (the 3 or 4 pale markers of a '+' frame: Epic and Epic+
share a ring colour, and where the markers lie outside the crop a crop too). A
near miss opens the tile. Synthetic frames only: no device, no game images.
"""
import hashlib

import cv2
import numpy as np
import pytest

COLS = (126, 329, 533, 736, 939)
ROW0, PITCH = 1198, 203
EPIC, LEGENDARY = 148, 16
MARKER = tuple(int(x) for x in cv2.cvtColor(np.uint8([[[166, 50, 235]]]), cv2.COLOR_HSV2BGR)[0, 0])   # pale pink, like the real tips


def _bgr(hue):
    return tuple(int(x) for x in cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0])


def _art(seed):
    """Seeded noise inside a diamond of radius 68: the module art. It stays
    clear of the ring the hue is sampled from (diamond distance 70..105)."""
    art = np.random.default_rng(seed).integers(0, 256, (150, 150, 3), dtype=np.uint8)
    yy, xx = np.mgrid[-75:75, -75:75]
    art[np.abs(yy) + np.abs(xx) >= 68] = 0
    return art


def _variant(seed, other=0, w=1.0, noise=0.0):
    """The art of `seed`, optionally mixed with the art of `other` (weight `w`
    on `seed`) and noised: a look-alike that scores below 1.0 against the
    plain art (measured scores are asserted in the tests that use it)."""
    x = _art(seed).astype(float)
    if w < 1.0:
        x = w * x + (1 - w) * _art(other).astype(float)
    if noise:
        x = x + np.random.default_rng(99).normal(0, noise, x.shape)
    out = np.clip(x, 0, 255).astype(np.uint8)
    yy, xx = np.mgrid[-75:75, -75:75]
    out[np.abs(yy) + np.abs(xx) >= 68] = 0
    return out


def _paint(canvas, row, col, seed, hue, shift=(0, 0), ring=(72, 105), plus=0, art=None, clear=False, markers=None):
    """One tile, the whole of it moved by `shift` px off the lattice like a real
    copy: the module art inside the 150 px crop, the frame glow as a diamond
    ring (its inner part overlaps the crop corners, like the real frames; with
    `clear` it lies entirely OUTSIDE the crop square, so the crop score cannot
    see the colour) and `plus` pale-pink 10 px markers on the ring, top /
    right / bottom / left in that order (a diamond frame's '+'; outside the
    crop), or at the explicit `markers` offsets from the tile centre (the real
    circular and triangular frames carry theirs inside the crop)."""
    cy, cx = ROW0 + row * PITCH + shift[1], COLS[col] + shift[0]
    canvas[cy - 75:cy + 75, cx - 75:cx + 75] = _art(seed) if art is None else art
    yy, xx = np.mgrid[-105:105, -105:105]
    d = np.abs(yy) + np.abs(xx)
    ring_px = (d > ring[0]) & (d < ring[1]) & ((np.abs(xx) > 75) | (np.abs(yy) > 75) | (not clear))
    canvas[cy - 105:cy + 105, cx - 105:cx + 105][ring_px] = _bgr(hue)
    for mx, my in markers if markers is not None else [(0, -82), (82, 0), (0, 82), (-82, 0)][:plus]:
        canvas[cy + my - 5:cy + my + 5, cx + mx - 5:cx + mx + 5] = MARKER


def _canvas():
    return np.zeros((2560, 1080, 3), np.uint8)


class _Walk:
    """Run the real calibrate._walk_grid over one settled page (the end of the
    list) with the device layer faked. `_inspect` records every tile it is
    asked to open as (row, col) and answers from `reads[(row, col)]`."""

    def __init__(self, tmp_path, monkeypatch, canvas, reads, resume=None, saved_trim=0):
        from interactions import inventory
        from player import calibrate
        import runtime.logger as lg
        self.canvas, self.reads = canvas, reads
        self.opened, self.events = [], []
        self.inventory, self.calibrate = inventory, calibrate
        monkeypatch.setattr(inventory, "_close_panel", lambda tries=4: True)
        monkeypatch.setattr(inventory, "park_top", lambda tries=5: None)
        monkeypatch.setattr(inventory, "settle", lambda *a, **k: canvas.copy())
        monkeypatch.setattr(inventory, "next_page", lambda: 0)
        monkeypatch.setattr(inventory, "take_page_frame", lambda: None)   # no hand-off from another test's real next_page()
        # the lattice rows: every canvas row that holds a tile (row detection by lit
        # projection is its own tested subsystem; a lone synthetic tile is too faint for it)
        monkeypatch.setattr(calibrate.pills, "grid_rows", lambda frame: [
            ROW0 + r * PITCH for r in range(4) if frame[ROW0 + r * PITCH - 75:ROW0 + r * PITCH + 75].any()])
        monkeypatch.setattr(calibrate, "_inspect", self._inspect)
        monkeypatch.setattr(calibrate, "module_slug", lambda name: name.lower().replace(" ", "_"))
        monkeypatch.setattr(calibrate.Calibration, "cut", lambda s, phase, rel, crop, frame, name, extra=None: {"rel": rel, **(extra or {})})
        monkeypatch.setattr(lg, "event", lambda kind, **kw: self.events.append((kind, kw)))
        p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
        self.cal = calibrate.Calibration(p, overwrite=False)
        if resume is not None:
            seed, rarity = resume                      # a saved crop = the art at the lattice, no shift
            canvas_r = _canvas()
            _paint(canvas_r, 0, 0, seed, EPIC)
            saved = tmp_path / "saved.png"
            icon_r = inventory._tile_icon(canvas_r, COLS[0], ROW0)
            cv2.imwrite(str(saved), icon_r[saved_trim:icon_r.shape[0] - saved_trim, saved_trim:icon_r.shape[1] - saved_trim])
            monkeypatch.setattr(calibrate, "template_path", lambda rel: saved)
            self.cal.entries = [{"phase": "modules", "verified": True, "rel": "modules/resumed.png", "rarity": rarity,
                                 "image_sha256": hashlib.sha256(saved.read_bytes()).hexdigest()}]

    def _inspect(self, cx, cy, icon=None):
        self.opened.append((round((cy - ROW0) / PITCH), COLS.index(cx)))
        return self.reads[self.opened[-1]]

    def run(self):
        self.slugs, self.copies = self.calibrate._walk_grid(self.cal)
        return self


def test_fixture_the_ring_clear_of_the_crop_leaves_only_the_border_class_to_reject_it():
    """Premise of the border tests: with the ring clear of the crop square, the
    same art under an Epic and a Legendary ring scores as a copy on the crop
    alone, so only the border class rejects it. (With the ring overlapping the
    crop corners, as on the real frames, the crop score drops by itself: that
    pair scored 0.825-0.892 on run 2's frames, below COPY_MATCH - the colour
    gate is a second line there, the only one here.)"""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, clear=True)
    _paint(c, 0, 1, 1, LEGENDARY, shift=(5, 3), clear=True)
    _paint(c, 0, 2, 2, EPIC, clear=True)
    a, b, other = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 1, 2))
    assert not inventory._same_icon(a, b)                              # the old comparison misses a shifted copy
    assert calibrate.match_module_crop(b, a)[0] >= calibrate.COPY_MATCH
    assert calibrate.match_module_crop(other, a)[0] < calibrate.COPY_MATCH


def test_shifted_copies_are_not_tapped_but_another_module_or_another_border_is(tmp_path, monkeypatch):
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)                                           # module A, first copy: opened
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3))                             # A again, 5/3 px off the lattice
    _paint(c, 0, 2, 1, EPIC, shift=(-4, 6))                            # A again, the other way
    _paint(c, 0, 3, 2, EPIC)                                           # a different module: opened
    _paint(c, 0, 4, 1, LEGENDARY, shift=(2, -3), clear=True)                    # A's art, Legendary frame: opened
    _paint(c, 2, 1, 2, EPIC, shift=(4, -4))                            # B again, a row down (row 1 would fuse with row 0's ring): not opened
    reads = {(0, 0): ("Module A", "epic"), (0, 3): ("Module B", "epic"), (0, 4): ("Module A", "legendary")}
    w = _Walk(tmp_path, monkeypatch, c, reads).run()
    assert w.opened == [(0, 0), (0, 3), (0, 4)]
    assert len(w.copies) == 6
    by_tile = {(cp["row"], cp["col"]): cp["rarity"] for cp in w.copies}
    assert by_tile == {(0, 0): "epic", (0, 1): "epic", (0, 2): "epic", (0, 3): "epic", (0, 4): "legendary", (1, 1): "epic"}


def test_a_tile_whose_border_cannot_be_read_is_opened_even_when_the_art_matches(tmp_path, monkeypatch):
    """A hairline ring (too few lit pixels) has no usable colour: no class, so no
    match - the tile costs a tap, never a wrong rarity. Both rings lie clear of the
    crop, so the shifted tile's art scores as a copy and its plus state reads equal:
    only the unreadable border (hue None) stands between it and a false copy."""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, clear=True)
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3), ring=(93, 96), clear=True)
    a, b = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 1))
    assert calibrate.match_module_crop(b, a)[0] >= calibrate.COPY_MATCH          # the art alone says: a copy
    assert calibrate._tile_plus(c, COLS[0], ROW0) is calibrate._tile_plus(c, COLS[1], ROW0) is False
    assert calibrate._ring_hue(c, COLS[1], ROW0) is None                         # the ring is the only thing unreadable
    reads = {(0, 0): ("Module A", "epic"), (0, 1): ("Module A", "epic")}
    assert _Walk(tmp_path, monkeypatch, c, reads).run().opened == [(0, 0), (0, 1)]


@pytest.mark.parametrize("rarity,plus", [("rare", 0), ("rare+", 4)])
def test_copies_of_a_rare_tile_the_ring_classifier_missed_are_each_opened_and_counted(tmp_path, monkeypatch, rarity, plus):
    """A Rare frame whose ring just misses the blue band is tapped (tile_is_blue
    fails safe); the panel reads Rare. A below-Epic read is never put on the list of
    modules seen, so its shifted copies are not matched to it: each costs its own tap,
    is counted below Epic and is not recorded (no copies row, no cut)."""
    from interactions.shatter import tile_is_blue
    c = _canvas()
    _paint(c, 0, 0, 1, 108, plus=plus)                                 # just outside shatter.BLUE_HUE (88, 104)
    _paint(c, 0, 1, 1, 108, shift=(5, 3), plus=plus)
    _paint(c, 0, 2, 1, 108, shift=(-4, 2), plus=plus)
    assert not any(tile_is_blue(c, COLS[i], ROW0) for i in range(3))
    w = _Walk(tmp_path, monkeypatch, c, {(0, i): ("Module A", rarity) for i in range(3)}).run()
    assert w.opened == [(0, 0), (0, 1), (0, 2)]
    assert w.copies == [] and w.slugs == []
    assert w.cal.player["modules_below_epic"] == 3
    assert [kw for k, kw in w.events if k == "calibrate_modules_below_epic"] == [{"count": 3, "opened": 3, "opened_below_epic": 3}]


def test_an_epic_tile_whose_rarity_was_misread_below_epic_costs_only_that_tile(tmp_path, monkeypatch):
    """One OCR misread of a panel's rarity ('rare' for an Epic tile) loses that tile
    (counted below Epic, as the task wants) and nothing more: its shifted Epic copies
    are not matched to the misread tile, so they are opened, read as Epic and recorded
    - the module stays in the grid and its template is cut from a copy."""
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3))
    _paint(c, 0, 2, 1, EPIC, shift=(-4, 2))
    reads = {(0, 0): ("Module A", "rare"), (0, 1): ("Module A", "epic"), (0, 2): ("Module A", "epic")}
    w = _Walk(tmp_path, monkeypatch, c, reads).run()
    assert w.opened == [(0, 0), (0, 1)]                                # (0, 2) is a copy of the Epic read at (0, 1)
    assert w.slugs == ["module_a"]
    assert {(cp["row"], cp["col"]): cp["rarity"] for cp in w.copies} == {(0, 1): "epic", (0, 2): "epic"}
    assert w.cal.player["modules_below_epic"] == 1
    assert [kw for k, kw in w.events if k == "calibrate_modules_below_epic"] == [{"count": 1, "opened": 2, "opened_below_epic": 1}]


def test_resumed_entry_matches_a_shifted_copy_once_its_rarity_colour_is_known(tmp_path, monkeypatch):
    """The saved crop of an earlier run has no colour of its own: it is read as
    the colour of the rarity it was saved at, learned from a tile of that
    rarity opened in THIS run. Module B opens first and teaches Epic; the
    shifted copy of the resumed module is then recognised without a tap."""
    c = _canvas()
    _paint(c, 0, 0, 2, EPIC)                                           # B: unseen, opened
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3))                             # the resumed module, shifted
    _paint(c, 0, 2, 1, LEGENDARY, shift=(-3, 2), clear=True)                    # same art, another border: opened
    reads = {(0, 0): ("Module B", "epic"), (0, 2): ("Module A", "legendary")}
    w = _Walk(tmp_path, monkeypatch, c, reads, resume=(1, "epic")).run()
    assert w.opened == [(0, 0), (0, 2)]
    assert [(cp["row"], cp["col"]) for cp in w.copies if cp["slug"] == "resumed"] == [(0, 1)]


def test_resumed_entry_with_an_unlearned_colour_costs_one_tap_then_matches(tmp_path, monkeypatch):
    """No Epic tile has been opened yet, so the saved crop's colour is unknown:
    the first copy of it is opened (time only) and the next one is skipped."""
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, shift=(5, 3))
    _paint(c, 0, 1, 1, EPIC, shift=(-4, 2))
    reads = {(0, 0): ("Module A", "epic")}
    w = _Walk(tmp_path, monkeypatch, c, reads, resume=(1, "epic")).run()
    assert w.opened == [(0, 0)]
    assert len(w.copies) == 2


def test_resumed_entry_pixel_identical_to_a_tile_matches_before_any_colour_is_learned(tmp_path, monkeypatch):
    """Identical pixels (the crop's own border corners included) need no colour
    proof: an unchanged inventory re-run still skips its resumed tiles at once."""
    c = _canvas()
    _paint(c, 0, 2, 1, EPIC)
    w = _Walk(tmp_path, monkeypatch, c, {}, resume=(1, "epic")).run()
    assert w.opened == [] and [(cp["row"], cp["col"]) for cp in w.copies] == [(0, 2)]


def test_ring_hue_reads_the_frame_colour_and_none_when_unreadable():
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, LEGENDARY)
    _paint(c, 0, 2, 1, EPIC, ring=(93, 96))
    assert abs(calibrate._ring_hue(c, COLS[0], ROW0) - EPIC) <= 1
    assert abs(calibrate._ring_hue(c, COLS[1], ROW0) - LEGENDARY) <= 1
    assert calibrate._ring_hue(c, COLS[2], ROW0) is None               # too few lit pixels
    assert calibrate._ring_hue(c, 40, ROW0) is None                    # ring would leave the frame


@pytest.mark.parametrize("a,b,same", [(148, 150, True), (146, 150, True), (176, 3, True),   # hue wraps at 180
                                      (148, 171, False), (16, 171, False), (148, 16, False),
                                      (None, 148, False), (148, None, False)])
def test_same_border_is_a_circular_hue_gap(a, b, same):
    from player import calibrate
    assert calibrate._same_border(a, b) is same


# ------------------------------------------------- Epic vs Epic+ (same ring colour)
def test_fixture_the_plus_markers_are_outside_the_crop_so_the_crop_score_cannot_tell_plus_apart():
    """Premise of the plus tests, for this synthetic fixture (diamond-frame
    markers outside the crop): an Epic and an Epic+ tile of one module hold the
    same crop and the same ring colour. Real circular / triangular frames carry
    their markers inside the crop; the inside-crop tests below cover those."""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3), plus=4)
    a, b = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 1))
    assert calibrate.match_module_crop(b, a)[0] >= calibrate.COPY_MATCH
    assert abs(calibrate._ring_hue(c, COLS[0], ROW0) - calibrate._ring_hue(c, COLS[1], ROW0)) <= 1


def test_the_plus_twin_of_a_module_is_opened_not_taken_for_its_copy(tmp_path, monkeypatch):
    """The same module at Epic and at Epic+: same art, same ring colour, the
    crop cannot tell them apart. Only the markers can, and a tile whose plus
    state differs from the seen copy's must be opened so it is recorded at its
    own rarity - shifted or not (the unshifted twin is pixel-identical in the crop)."""
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)                                           # A at Epic: opened
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3), plus=4)                     # A at Epic+, shifted: opened
    _paint(c, 0, 2, 1, EPIC, plus=4)                                   # A at Epic+, on the lattice: a copy of the one above
    _paint(c, 0, 3, 1, EPIC, shift=(-4, 2))                            # A at Epic again: a copy of the first
    _paint(c, 2, 0, 1, EPIC, plus=4, shift=(3, -2))                    # A at Epic+ again, a row down: a copy
    reads = {(0, 0): ("Module A", "epic"), (0, 1): ("Module A", "epic+")}
    w = _Walk(tmp_path, monkeypatch, c, reads).run()
    assert w.opened == [(0, 0), (0, 1)]
    assert {(cp["row"], cp["col"]): cp["rarity"] for cp in w.copies} == {
        (0, 0): "epic", (0, 1): "epic+", (0, 2): "epic+", (0, 3): "epic", (1, 0): "epic+"}


def test_a_plain_tile_is_not_taken_for_the_plus_copy_either(tmp_path, monkeypatch):
    """The other direction: Epic+ is seen first, the plain Epic twin must open."""
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, plus=4)
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3))
    reads = {(0, 0): ("Module A", "epic+"), (0, 1): ("Module A", "epic")}
    assert _Walk(tmp_path, monkeypatch, c, reads).run().opened == [(0, 0), (0, 1)]


def test_a_tile_with_unreadable_plus_markers_is_opened(tmp_path, monkeypatch):
    """One or two markers are neither a plus nor a plain frame (a marker is
    hidden or the art is pink): no plus state, so no match - a tap, never a
    wrong rarity."""
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, plus=4)
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3), plus=2)
    reads = {(0, 0): ("Module A", "epic+"), (0, 1): ("Module A", "epic+")}
    assert _Walk(tmp_path, monkeypatch, c, reads).run().opened == [(0, 0), (0, 1)]


def test_resumed_plain_entry_does_not_claim_a_plus_tile_and_the_reverse(tmp_path, monkeypatch):
    """A resumed saved crop carries its rarity: Epic saved, the user upgraded it
    to Epic+ since - the re-run must open that tile (the likeliest real case),
    even where the crop is pixel-identical (the unshifted tile)."""
    for plus, saved_rarity, expect_opened in [(4, "epic", [(0, 2)]), (0, "epic+", [(0, 2)]), (4, "epic+", []), (0, "epic", [])]:
        c = _canvas()
        _paint(c, 0, 2, 1, EPIC, plus=plus)
        w = _Walk(tmp_path, monkeypatch, c, {(0, 2): ("Module A", "epic+" if plus else "epic")}, resume=(1, saved_rarity)).run()
        assert w.opened == expect_opened, (plus, saved_rarity)


def test_tile_plus_counts_tip_markers_not_pink_art():
    """_tile_plus: >= 3 marker blobs is a plus, none a plain frame, one or two
    unreadable. Pink art (a long line, a speck, a big blob) is not a marker."""
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, EPIC, plus=4)
    _paint(c, 0, 2, 1, EPIC, plus=3)
    _paint(c, 0, 3, 1, EPIC, plus=2)
    cy, cx = ROW0, COLS[4]
    _paint(c, 0, 4, 1, EPIC)
    c[cy - 40:cy - 38, cx - 30:cx + 10] = MARKER                       # a pink line in the art: 2 x 40
    c[cy + 20:cy + 23, cx + 20:cx + 23] = MARKER                       # a speck: 3 x 3
    c[cy + 30:cy + 60, cx - 40:cx - 10] = MARKER                       # a big blob: 30 x 30
    assert [calibrate._tile_plus(c, x, ROW0) for x in COLS] == [False, True, True, None, False]
    assert calibrate._tile_plus(c, 40, ROW0) is None                   # the tile leaves the frame


# ------------------------------------------------- the copy threshold has both bounds
@pytest.mark.parametrize("w,lo,hi", [(0.0, 0.73, 0.77), (0.2, 0.82, 0.85), (0.3, 0.86, 0.89)])
def test_a_look_alike_module_scoring_well_below_a_copy_is_opened(tmp_path, monkeypatch, w, lo, hi):
    """Real near misses: another module in the same colour scored at most 0.735
    and the same art at another rarity 0.825-0.892 on run 2's frames. The
    synthetic look-alikes (a mix of two arts) score in the same range - a
    COPY_MATCH lowered to 0.75 would take them for copies."""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, EPIC, shift=(4, 3), art=_variant(1, 2, w=w))
    a, b = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 1))
    assert lo <= calibrate.match_module_crop(b, a)[0] <= hi
    reads = {(0, 0): ("Module A", "epic"), (0, 1): ("Module B", "epic")}
    assert _Walk(tmp_path, monkeypatch, c, reads).run().opened == [(0, 0), (0, 1)]


def test_a_noisy_genuine_copy_at_the_worst_measured_score_is_still_skipped(tmp_path, monkeypatch):
    """The lowest genuine copy pair on run 2's saved grid frames scored 0.972
    (re-measured: 24 tiles each against the first tile of its slug and rarity, the
    comparison the walk makes; 60 unordered pairs; the Rare pairs, which the walk
    never compares since a below-Epic read is not kept for matching, go down to
    0.942). A synthetic copy with noise scoring about
    that must not be tapped: a COPY_MATCH raised to 0.99 would quietly undo the
    speed-up."""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, EPIC, shift=(4, 3), art=_variant(1, noise=40))
    a, b = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 1))
    assert 0.96 <= calibrate.match_module_crop(b, a)[0] <= 0.98       # measured 0.971
    reads = {(0, 0): ("Module A", "epic")}
    assert _Walk(tmp_path, monkeypatch, c, reads).run().opened == [(0, 0)]


def test_the_look_alike_just_under_the_threshold_is_opened(tmp_path, monkeypatch):
    """Pins the margin between the highest real non-copy score (0.892) and
    COPY_MATCH: a look-alike scoring about 0.92 must be opened, so a threshold
    lowered to 0.90 (where the real non-copies still separate) is caught here."""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC)
    _paint(c, 0, 1, 1, EPIC, shift=(4, 3), art=_variant(1, 2, w=0.45))
    a, b = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 1))
    assert 0.91 <= calibrate.match_module_crop(b, a)[0] <= 0.93
    reads = {(0, 0): ("Module A", "epic"), (0, 1): ("Module B", "epic")}
    assert _Walk(tmp_path, monkeypatch, c, reads).run().opened == [(0, 0), (0, 1)]


# ------------------------------------------------- the border colour gate on its own
def test_the_border_gate_alone_keeps_the_same_art_in_another_colour_apart(tmp_path, monkeypatch):
    """Every ring lies clear of the crop, so the crop score is a copy score for
    all four tiles: only the border colour tells the Legendary tile from the Epic
    ones. Its own shifted copy is then recognised by that colour."""
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, clear=True)                               # A at Epic: opened
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3), clear=True)                 # A at Epic again: a copy
    _paint(c, 0, 2, 1, LEGENDARY, shift=(-4, 2), clear=True)           # A's art, Legendary frame: opened
    _paint(c, 2, 0, 1, LEGENDARY, shift=(3, -3), clear=True)           # a copy of the Legendary one, a row down
    reads = {(0, 0): ("Module A", "epic"), (0, 2): ("Module A", "legendary")}
    w = _Walk(tmp_path, monkeypatch, c, reads).run()
    assert w.opened == [(0, 0), (0, 2)]
    assert {(cp["row"], cp["col"]): cp["rarity"] for cp in w.copies} == {
        (0, 0): "epic", (0, 1): "epic", (0, 2): "legendary", (1, 0): "legendary"}


# ------------------------------------------------- '+' markers as the real frames carry them
TRIANGLE = [(-42, -23), (50, -22), (5, 69)]                          # 3 markers inside the crop (Antimatter Reactor, run 2)
CIRCLE = [(-56, -56), (56, -56), (-56, 56), (56, 56)]                # 4 markers on the crop's diagonals (Blitzer, run 2)


@pytest.mark.parametrize("markers", [TRIANGLE, CIRCLE], ids=["triangle-3", "circle-4"])
def test_tile_plus_reads_markers_inside_the_crop(markers):
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, markers=markers)
    _paint(c, 0, 1, 1, EPIC, markers=markers[:2])                      # two of them hidden
    _paint(c, 0, 2, 1, EPIC)
    assert [calibrate._tile_plus(c, COLS[i], ROW0) for i in range(3)] == [True, None, False]


@pytest.mark.parametrize("markers", [TRIANGLE, CIRCLE], ids=["triangle-3", "circle-4"])
def test_a_plus_tile_with_its_markers_inside_the_crop_is_a_copy_only_of_its_own_plus_state(tmp_path, monkeypatch, markers):
    """The markers are part of the crop here, so the crop score alone may or may
    not tell Epic from Epic+: the plus state decides. A shifted copy with the same
    markers is skipped; the plain twin and a copy with two markers hidden (no
    readable plus state) are opened."""
    from interactions import inventory
    from player import calibrate
    c = _canvas()
    _paint(c, 0, 0, 1, EPIC, markers=markers)                          # A at Epic+: opened
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3), markers=markers)            # A at Epic+ again: a copy
    _paint(c, 0, 2, 1, EPIC, shift=(-4, 2), markers=markers[:2])       # two markers hidden: opened
    _paint(c, 0, 3, 1, EPIC, shift=(3, -3))                            # the plain twin: opened
    a, twin = (inventory._tile_icon(c, COLS[i], ROW0) for i in (0, 3))
    assert calibrate.match_module_crop(twin, a)[0] >= calibrate.COPY_MATCH   # the crop cannot reject the twin
    reads = {(0, 0): ("Module A", "epic+"), (0, 2): ("Module A", "epic+"), (0, 3): ("Module A", "epic")}
    w = _Walk(tmp_path, monkeypatch, c, reads).run()
    assert w.opened == [(0, 0), (0, 2), (0, 3)]
    assert {(cp["row"], cp["col"]): cp["rarity"] for cp in w.copies} == {
        (0, 0): "epic+", (0, 1): "epic+", (0, 2): "epic+", (0, 3): "epic"}


# ------------------------------------------------- a saved crop of another size
def test_a_resumed_crop_of_another_size_is_never_taken_for_a_copy(tmp_path, monkeypatch):
    """A saved crop cut at another tile size (another emulator resolution) fits
    inside the tile, so a template match would score it ~1.0 at any shift. Sizes
    that differ give no verdict: the tile is opened. B teaches the Epic colour
    first, so only the size check stands between the crop and a false copy."""
    c = _canvas()
    _paint(c, 0, 0, 2, EPIC)                                           # B: opened, teaches Epic
    _paint(c, 0, 1, 1, EPIC, shift=(5, 3))                             # the resumed module (120 px crop), shifted
    reads = {(0, 0): ("Module B", "epic"), (0, 1): ("Module A", "epic")}
    w = _Walk(tmp_path, monkeypatch, c, reads, resume=(1, "epic"), saved_trim=15).run()
    assert w.opened == [(0, 0), (0, 1)]
