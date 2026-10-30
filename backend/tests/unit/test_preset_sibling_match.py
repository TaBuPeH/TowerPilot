"""Look-alike preset pills: a saved tab whose own pill is gone must read as
absent, never as the sibling pill its template also matches.

The game's default names "Preset 1".."Preset 5" differ by one digit, so each
label's template scores ~0.90-0.97 on its siblings (live: self 1.0, next-best
0.893-0.969) - above the 0.90 find threshold. A sibling need not have a
template of its own (a slot unlocked after calibration, a pill the calibrator
never saw), and one label can be the start of another ("Farm" / "Farm 2").
Every frame here is synthetic (test_calibrate._pill_frame); the templates are
cut from a frame the same way the calibrator cuts them (pills.text_crop)."""
import cv2
import pytest

from test_calibrate import _pill_frame

DEFAULT = ["Preset 1", "Preset 2", "Preset 3"]
FAMILIES = ("cards/preset_", "presets/gp_", "presets/modules_",
            "presets/guardians_", "presets/workshop_", "presets/bots_")
# Preset 1 gone from the row while Preset 2 (its closest look-alike) is selected:
# renamed in the game, or the row scrolled it off the left edge.
ROWS = {
    "renamed": dict(labels=["Farm", "Preset 2", "Preset 3"], active=1),
    "scrolled": dict(labels=["Preset 2", "Preset 3"], active=0, x0=120),
}


def _row(labels, active, x0=20):
    frame, _ = _pill_frame(labels, w=200, gap=14, active=active, x0=x0)
    return frame


@pytest.fixture()
def cut(tmp_path, monkeypatch):
    """Cut a row's labels as `<prefix><slug>.png` under a tmp template root
    (the calibrator's crop and name, unbound account). Returns the rels in
    row order."""
    import settings
    from interactions import loadout
    from player.calibrate import slug
    from vision import detect, pills
    for mod in (settings, detect, loadout):
        monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(detect, "_TPL_CACHE", {})

    def write(prefix, labels=DEFAULT, trim=None):
        # trim: a hand cut drawn `trim` px INSIDE the label's white-pixel box
        # on every side (the cropper's "tight box"), instead of the margin-8 cut
        frame = _row(labels, 0)
        if trim is None:
            crops = [pills.text_crop(frame, p["rect"])[0] for p in pills.pills(frame, 330, 560)]
        else:
            crops = [pills.text_crop(frame, p["rect"], margin=0)[0][trim:-trim, trim:-trim]
                     for p in pills.pills(frame, 330, 560)]
        assert len(crops) == len(labels)
        rels = [f"{prefix}{slug(label)}.png" for label in labels]
        for rel, crop in zip(rels, crops):
            path = tmp_path / "templates" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), crop)
        return rels
    return write


@pytest.mark.parametrize("prefix", FAMILIES)
@pytest.mark.parametrize("row", sorted(ROWS))
def test_absent_own_pill_reads_absent_not_as_its_sibling(cut, tmp_path, prefix, row):
    from interactions import tourney
    one, two, three = cut(prefix)
    frame = _row(**ROWS[row])
    # The trap is real: preset_1's template clears the threshold on Preset 2.
    tpl = cv2.imread(str(tmp_path / "templates" / one))
    assert cv2.minMaxLoc(cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED))[1] >= tourney.FIND_THRESH
    assert tourney.find(frame, one) is None
    assert tourney.find_robust(frame, one) is None
    # the pills that ARE there still read as themselves, at their own place
    for rel in (two, three):
        hit = tourney.find(frame, rel)
        assert hit and hit[1] > 0.99
        assert tourney.find_robust(frame, rel)[0] == hit[0]


@pytest.mark.parametrize("prefix", FAMILIES)
def test_every_pill_still_reads_on_the_row_it_was_cut_from(cut, prefix):
    from interactions import tourney
    rels = cut(prefix)
    for active in range(3):      # each label in both outline states
        frame = _row(DEFAULT, active)
        xs = [tourney.find(frame, rel)[0][0] for rel in rels]
        assert xs == sorted(xs) and len(set(xs)) == 3


# A look-alike sibling with NO template of its own: slots 4 and 5 were never
# cut, and the cut Preset 3 was renamed. Seen through every reader.
UNCUT = dict(labels=["Preset 1", "Preset 2", "Farm", "Preset 4", "Preset 5"], active=4, x0=0)


def _uncut_row():
    frame, _ = _pill_frame(UNCUT["labels"], w=190, gap=14, active=UNCUT["active"], x0=UNCUT["x0"])
    return frame


@pytest.mark.parametrize("prefix", FAMILIES)
def test_a_sibling_without_a_template_cannot_stand_in_for_an_absent_pill(cut, tmp_path, prefix):
    from interactions import tourney
    three = cut(prefix)[2]
    frame = _uncut_row()
    tpl = cv2.imread(str(tmp_path / "templates" / three))
    assert cv2.minMaxLoc(cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED))[1] >= tourney.FIND_THRESH
    assert tourney.find(frame, three) is None
    assert tourney.find_robust(frame, three) is None


def test_current_cards_and_apply_cards_refuse_an_uncut_look_alike(cut, monkeypatch, tmp_path):
    from interactions import loadout, tourney
    import runtime.logger as lg
    cut("cards/preset_")
    frame = _uncut_row()
    assert loadout.current_cards(frame) is None
    taps = []
    monkeypatch.setattr(tourney, "open_nav", lambda *a: None)
    monkeypatch.setattr(tourney, "wait_for", lambda rel, timeout=6.0, thresh=tourney.FIND_THRESH:
                        (frame, (tourney.find(frame, rel, thresh) or [None])[0]))
    monkeypatch.setattr(tourney, "tap_at", lambda pt, reason: taps.append(reason))
    monkeypatch.setattr(tourney, "return_to_game", lambda *a: None)
    monkeypatch.setattr(lg, "shot", lambda *a, **kw: str(tmp_path / "shot.png"))
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    with pytest.raises(tourney.Abort, match="not found"):
        loadout.apply_cards("preset_3")
    assert taps == []


# One label is the start of another: "Farm" sits whole inside "Farm 2" and
# "Farm Deck" and matches there at ~1.0. Only the WHOLE label is its pill.
PREFIX_CASES = {
    "farm_2": (["Farm", "Farm 2"], ["Push", "Farm 2"]),
    "farm_deck": (["Farm", "Farm Deck"], ["Farm Deck", "Other"]),
}


@pytest.mark.parametrize("case", sorted(PREFIX_CASES))
@pytest.mark.parametrize("prefix", FAMILIES)
def test_a_label_inside_a_longer_label_is_not_that_pill(cut, case, prefix):
    from interactions import tourney
    cut_labels, shown = PREFIX_CASES[case]
    farm, longer = cut(prefix, cut_labels)
    frame = _row(shown, shown.index(cut_labels[1]))
    assert tourney.find(frame, farm) is None
    assert tourney.find_robust(frame, farm) is None
    assert tourney.find(frame, longer)[1] > 0.99


def test_a_label_is_found_on_its_own_pill_when_a_longer_one_comes_first(cut, tmp_path):
    # "Farm" scores 1.0 inside "Farm 2"; its own pill, drawn a touch softer
    # (a rendering difference), scores less - the first best spot is the wrong one.
    from interactions import tourney
    farm, _ = cut("cards/preset_", ["Farm", "Farm 2"])
    frame, rects = _pill_frame(["Farm 2", "Farm"], w=200, gap=14, active=1)
    x, y, w, h = rects[1]
    frame[y:y + h, x:x + w] = cv2.GaussianBlur(frame[y:y + h, x:x + w], (3, 3), 0)
    tpl = cv2.imread(str(tmp_path / "templates" / farm))
    best = cv2.minMaxLoc(cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED))[3]
    assert best[0] < rects[0][0] + rects[0][2]
    for reader in (tourney.find, tourney.find_robust):
        (cx, cy), score = reader(frame, farm)
        assert x < cx < x + w and score >= tourney.FIND_THRESH


@pytest.mark.parametrize("case", sorted(PREFIX_CASES))
def test_current_cards_names_the_longer_tab_not_its_prefix(cut, case):
    from interactions import loadout
    cut_labels, shown = PREFIX_CASES[case]
    cut("cards/preset_", cut_labels)
    from player.calibrate import slug
    assert loadout.current_cards(_row(shown, shown.index(cut_labels[1]))) == slug(cut_labels[1])


# The card scan reads tabs with find_robust, whose partial-cell score lifts a
# sibling below the whole-template threshold over it.
def test_scan_does_not_list_a_preset_from_an_uncut_sibling(cut, monkeypatch):
    from device import capture
    from interactions import tourney
    from player import scan
    import runtime.logger as lg
    cut("cards/preset_", ["Preset 1", "Farm"])
    frame = _row(["Farm", "Preset 4"], 1)
    monkeypatch.setattr(tourney, "open_nav", lambda *a: None)
    monkeypatch.setattr(tourney, "return_to_game", lambda *a: None)
    monkeypatch.setattr(capture, "grab", lambda: frame)
    monkeypatch.setattr(scan, "_evidence", lambda p, f, name: name)
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    out = scan.phase_cards({})
    assert out["card_presets"] == ["farm"]
    assert out["cards_current"] is None


@pytest.mark.parametrize("row", sorted(ROWS))
def test_current_cards_names_the_selected_tab_not_a_missing_look_alike(cut, row):
    from interactions import loadout
    cut("cards/preset_")
    assert loadout.current_cards(_row(**ROWS[row])) == "preset_2"


def test_card_scan_lists_only_the_presets_on_screen(cut, monkeypatch):
    from device import capture
    from interactions import tourney
    from player import scan
    import runtime.logger as lg
    cut("cards/preset_")
    frame = _row(**ROWS["renamed"])
    monkeypatch.setattr(tourney, "open_nav", lambda *a: None)
    monkeypatch.setattr(tourney, "return_to_game", lambda *a: None)
    monkeypatch.setattr(capture, "grab", lambda: frame)
    monkeypatch.setattr(scan, "_evidence", lambda p, f, name: name)
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    out = scan.phase_cards({})
    assert out["card_presets"] == ["preset_2", "preset_3"]
    assert out["cards_current"] == "preset_2"


# The dashboard cropper takes hand cuts of these names and asks for a TIGHT
# box; one that clips the label's anti-aliased edge by a pixel or two matched
# at 1.0 before the whole-label rule and must still read as its own pill.
@pytest.mark.parametrize("trim", [1, 3])
@pytest.mark.parametrize("prefix", FAMILIES)
def test_a_tight_hand_cut_still_reads_as_its_own_pill(cut, prefix, trim):
    from interactions import tourney
    rels = cut(prefix, trim=trim)
    for active in range(3):
        frame = _row(DEFAULT, active)
        for rel, label_x in zip(rels, (20, 234, 448)):
            (cx, _), score = tourney.find(frame, rel)
            assert label_x < cx < label_x + 200 and score > 0.99
    assert tourney.find_robust(frame, rels[0]) == tourney.find(frame, rels[0])
    # ...and is no looser for it: a sibling or a longer label is still refused
    assert tourney.find(_row(**ROWS["renamed"]), rels[0]) is None
    farm, _ = cut(prefix, ["Farm", "Farm 2"], trim=trim)
    assert tourney.find(_row(["Push", "Farm 2"], 1), farm) is None


# guardian_swap's v29 guard asks whether the preset TAB ROW is on screen, not
# which tab: a row whose tabs were renamed after the cut ("Farm" -> "Farm 2")
# is still the v29 screen, where a legacy slot tap is a permanent build edit.
@pytest.mark.parametrize("shown", [["Farm", "Tourney"], ["Farm 2", "Tourney 2"],
                                   ["Farming", "Push"], ["Farm Deck", "Tourney B"]])
def test_guardian_swap_refuses_any_v29_tab_row_before_a_tap(cut, monkeypatch, tmp_path, shown):
    from device import capture
    from interactions import tourney
    import runtime.logger as lg
    cut("presets/guardians_", ["Farm", "Tourney"])
    frame = _row(shown, 0)
    # a guild tile the left-rail lookup finds: any distinctive patch of the frame
    tile = tmp_path / "templates" / "home" / "tile_guild.png"
    tile.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(tile), frame[380:490, 0:240])
    taps = []
    monkeypatch.setattr(capture, "grab", lambda: frame)
    monkeypatch.setattr(tourney, "require", lambda *a, **kw: (frame, (0, 0)))
    monkeypatch.setattr(tourney, "tap_at", lambda pt, reason: taps.append(reason))
    monkeypatch.setattr(lg, "shot", lambda *a, **kw: str(tmp_path / "shot.png"))
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    with pytest.raises(tourney.Abort, match="v29 guardian screen"):
        tourney.guardian_swap()
    assert taps == ["open guild", "guardian tab"]


@pytest.mark.parametrize("category", ["global", "modules"])
def test_preset_selection_refuses_a_sibling_instead_of_tapping_it(cut, monkeypatch, tmp_path, category):
    from interactions import presets, tourney
    import runtime.logger as lg
    cut("presets/gp_" if category == "global" else f"presets/{category}_")
    frame = _row(**ROWS["renamed"])
    taps = []
    monkeypatch.setattr(tourney, "wait_for", lambda rel, timeout=6.0, thresh=tourney.FIND_THRESH:
                        (frame, (tourney.find(frame, rel, thresh) or [None])[0]))
    monkeypatch.setattr(tourney, "tap_at", lambda pt, reason: taps.append(reason))
    monkeypatch.setattr(presets, "_open_picker", lambda: frame)
    monkeypatch.setattr(presets, "_close_picker", lambda: None)
    monkeypatch.setitem(presets.CATEGORY_NAV, "modules", (lambda: None, lambda: None))
    monkeypatch.setattr(lg, "shot", lambda *a, **kw: str(tmp_path / "shot.png"))
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    with pytest.raises(tourney.Abort, match="not found"):
        if category == "global":
            presets.select_global("Preset 1")
        else:
            presets.select_category(category, "Preset 1")
    assert taps == []
    if category == "global":
        assert presets.verify_global("Preset 1") is False
        assert presets.verify_global("Preset 2") is True
