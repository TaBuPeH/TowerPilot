import numpy as np
from player import battle_capture as bc


def test_digit_glyphs_labels_each_glyph_by_the_ocr_number():
    crop = np.zeros((45, 170, 3), np.uint8)
    for x in (16, 40, 66):                     # three glyphs, first where the HUD draws it
        crop[6:6+32, x:x+20] = 255
    got = bc.digit_glyphs(crop, "160")
    assert set(got) == {"1", "6", "0"}
    assert all(g.size for g in got.values())


def test_digit_glyphs_refuses_when_it_cannot_label_safely():
    crop = np.zeros((45, 170, 3), np.uint8)
    for x in (16, 40, 66):
        crop[6:6+32, x:x+20] = 255
    assert bc.digit_glyphs(crop, "16") == {}    # glyph count != number length
    assert bc.digit_glyphs(crop, "") == {}      # nothing OCR'd
    off = np.zeros((45, 170, 3), np.uint8)      # first glyph not at the counter's start
    for x in (40, 66, 96):
        off[6:6+32, x:x+20] = 255
    assert bc.digit_glyphs(off, "160") == {}


def test_capture_digits_accumulates_across_frames():
    def make(marker, xs):
        f = np.zeros((100, 200, 3), np.uint8)
        for x in xs:                            # two glyphs, first at the HUD start
            f[10:10+32, x:x+18] = 255
        f[0, 0] = marker                        # a corner marker the fake OCR keys on
        return f
    codes = {12: "12", 34: "34"}
    frames = [make(12, (16, 40)), make(34, (16, 40))]
    got = bc.capture_digits(frames, lambda crop: codes[int(crop[0, 0, 0])], (0, 0, 200, 100))
    assert set(got) == {"1", "2", "3", "4"}


def test_locate_text_maps_upscaled_ocr_coords_back_to_the_frame():
    frame = np.zeros((200, 300, 3), np.uint8)
    rl = lambda crop, scale: [(40, 60, "EXIT BATTLE"), (10, 10, "other")]
    # region (100,50,150,120), default scale 2: frame_x=100+60/2, frame_y=50+40/2
    assert bc.locate_text(frame, (100, 50, 150, 120), "exit battle", rl) == (130, 70)
    assert bc.locate_text(frame, (100, 50, 150, 120), "nothing", lambda c, s: [(40, 60, "xyz")]) is None


def test_capture_by_text_cuts_requested_size_around_the_label():
    frame = np.zeros((400, 400, 3), np.uint8)
    crop = bc.capture_by_text(frame, "retry", (0, 0, 400, 400), (100, 50),
                              lambda c, s: [(60, 80, "RETRY")], anchor=(0.2, 0.4))
    assert crop is not None and crop.shape[:2] == (50, 100)
    assert bc.capture_by_text(frame, "retry", (0, 0, 400, 400), (100, 50),
                              lambda c, s: [(60, 80, "unrelated")]) is None


def test_stat_slug_and_fuzzy_match():
    assert bc.stat_slug("Damage / Meter") == "damage_per_meter"
    assert bc.stat_slug("Health Regen") == "health_regen"
    assert bc.stat_slug("Super Crit Mult") == "super_crit_mult"
    wanted = {"enemy_attack_level_skip", "super_crit_mult", "damage"}
    assert bc.match_stat("Enemy Attack Leve Skip", wanted) == "enemy_attack_level_skip"
    assert bc.match_stat("Damage", wanted) == "damage"
    assert bc.match_stat("Totally Unrelated", wanted) is None
