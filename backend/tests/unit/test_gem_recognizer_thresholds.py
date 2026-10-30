"""Pin the gem recognizers' safety thresholds with generated pixels (no game image).

Each rule below stops a wrong tap or a wrong balance, and each had a mutant that no
default test noticed (quality council 2026-10-24, TC-3 and GC-02). A case sits on a
chosen side of the rule AS CODED, with room to spare, so loosening or tightening the rule
turns it red.

What is NOT claimed: how much deformation live frames really show. The accept cases mean
"must not get stricter than this", the reject cases "must not get looser than this"; the
live tolerances (pink-stroke thickness, tile tilt, stretch) are unmeasured, so there is no
case that pins a live number. The orbit glyph's orientation margin (`scores[0]-scores[1]
< .1` in `find_orbit_gem`) has no case here: no generated interior was found whose best
score reaches .8 with a margin below .1, so it stays covered only by the direct
`_glyph_scores` assertions in test_orbit_gems.py.
"""
import cv2
import numpy as np
import pytest

from interactions import ad_gems
from player.bootstrap_layout import manifest
from test_gem_balance import font, hud          # noqa: F401 - digit-font fixture and counter frame
from test_orbit_gems import diamond, scene
from vision import ocr, orbit_gems

CENTER = (786, 1038)                             # where scene() puts the gem
HALF_TILE = 75                                   # scene() draws a 150 px tile around CENTER


def tile_slice():
    return (slice(CENTER[1]-HALF_TILE, CENTER[1]+HALF_TILE), slice(CENTER[0]-HALF_TILE, CENTER[0]+HALF_TILE))


def deformed(frame, stretch=1., shear=0.):
    """The gem tile after an affine stretch (x) and shear (x by y) about its centre."""
    out = frame.copy()
    matrix = np.float32([[stretch, shear, (1-stretch)*HALF_TILE-shear*HALF_TILE], [0, 1, 0]])
    out[tile_slice()] = cv2.warpAffine(frame[tile_slice()], matrix, (2*HALF_TILE, 2*HALF_TILE))
    return out


def found(frame):
    gem = orbit_gems.find_orbit_gem(frame)
    return gem is not None and bool(np.hypot(gem.center[0]-CENTER[0], gem.center[1]-CENTER[1]) <= 3)


def gem_tile(interior, *, border=(255, 220, 255), edge=(255, 40, 255)):
    """A gem-sized tile whose HUD-style interior is `interior`, with the given border colours."""
    frame = scene()
    tile = np.zeros((150, 150, 3), np.uint8)
    cv2.rectangle(tile, (37, 37), (113, 113), edge, 6)
    cv2.rectangle(tile, (37, 37), (113, 113), border, 2)
    tile[50:100, 48:102] = np.maximum(tile[50:100, 48:102], interior[:, :, None])
    frame[tile_slice()] = tile
    return frame


def with_pink_stroke(share):
    """A white square border of which only `share` of the perimeter (from the top-left corner on) is pink."""
    frame = scene()
    tile = np.zeros((150, 150, 3), np.uint8)
    cv2.rectangle(tile, (37, 37), (113, 113), (255, 255, 255), 2)
    perimeter = ([(i, 37) for i in range(37, 114)] + [(113, j) for j in range(37, 114)]
                 + [(i, 113) for i in range(113, 36, -1)] + [(37, j) for j in range(113, 36, -1)])
    for i, j in perimeter[:round(share*len(perimeter))]:
        tile[j-1:j+1, i-1:i+1] = (255, 220, 255)
    glyph = diamond()
    tile[50:100, 48:102] = np.maximum(tile[50:100, 48:102], glyph[:, :, None])
    frame[tile_slice()] = tile
    return frame


# ---- orbit_gems: pink border, aspect, right angles, glyph score ---------------------------------

def test_a_white_square_with_the_glyph_but_no_pink_border_is_not_a_gem():
    assert found(scene())                                                           # control: the same scene with its pink border
    assert not found(gem_tile(diamond(), border=(255, 255, 255), edge=(255, 255, 255)))


@pytest.mark.parametrize('share,is_gem', [(.08, True), (.03, False)])
def test_the_pink_border_pixel_floor_sits_between_a_thin_stroke_and_a_thinner_one(share, is_gem):
    """About 66 pink border pixels (share .08) read; about 25 (share .03) do not. The rule is 40; the full border has ~2000."""
    assert found(with_pink_stroke(share)) is is_gem


@pytest.mark.parametrize('angle,scale', [(0, 1.), (45, 1.), (225, .72)])
def test_a_slightly_stretched_square_is_still_a_gem_and_a_much_stretched_one_is_not(angle, scale):
    """Sides within 10 percent stay a gem (the rules allow 15, and a bounding box of 0.65 to 1.55); 30 percent do not."""
    assert found(deformed(scene(angle, scale=scale), stretch=1.10))
    assert not found(deformed(scene(angle, scale=scale), stretch=1.30))


@pytest.mark.parametrize('angle,scale', [(0, 1.), (45, 1.), (225, .72)])
def test_a_slightly_sheared_square_is_still_a_gem_and_a_strongly_sheared_one_is_not(angle, scale):
    """A corner 6 degrees off square (cosine .10) stays a gem; 14 degrees (.25) does not. The rule is cosine .18."""
    assert found(deformed(scene(angle, scale=scale), shear=.10))
    assert not found(deformed(scene(angle, scale=scale), shear=.25))


@pytest.mark.parametrize('damage', ['outline_only', 'half', 'top_only'])
def test_an_interior_that_only_partly_matches_the_hud_glyph_is_not_a_gem(damage):
    glyph = diamond()
    if damage == 'outline_only':
        glyph = np.zeros_like(glyph)
        cv2.polylines(glyph, [np.array([[3, 16], [14, 3], [39, 3], [50, 16], [27, 46]], np.int32)], True, 255, 2)
    elif damage == 'half':
        glyph[:, :27] = 0
    else:
        glyph[20:, :] = 0
    assert found(gem_tile(diamond()))                                               # control: the whole glyph in the same tile
    assert not found(gem_tile(glyph))


# ---- ocr.read_gem_balance: counter regions and the .8 score floor -------------------------------

def test_the_counter_regions_are_the_measured_ones():
    """Native 1080x2560. Home: the digits start 13 px inside, right of the diamond icon (x 382-428), left of the stones icon (x >= 668)."""
    assert ocr.GEM_BALANCE_REGION == (90, 190, 240, 60)
    assert ocr.HOME_GEM_BALANCE_REGION == (434, 18, 226, 60)


def test_the_home_counter_is_read_from_the_home_region_only(font):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    x, y, _, _ = ocr.HOME_GEM_BALANCE_REGION
    x += 13
    for digit in '966':
        glyph = font[digit]
        h, w = glyph.shape
        frame[y+10:y+10+h, x:x+w] = glyph[:, :, None]
        x += w+5
    assert ocr.read_gem_balance(frame, ocr.HOME_GEM_BALANCE_REGION) == 966
    assert ocr.read_gem_balance(frame) is None                                       # the battle HUD region holds nothing


@pytest.mark.parametrize('kernel,expected', [(3, 747), (5, None), (7, None)])
def test_a_thickened_digit_that_scores_below_the_floor_is_unknown_not_a_guess(font, kernel, expected):
    frame = hud(font)
    x, y, w, h = ocr.GEM_BALANCE_REGION
    frame[y:y+h, x:x+w] = cv2.dilate(frame[y:y+h, x:x+w], np.ones((kernel, kernel), np.uint8))
    assert ocr.read_gem_balance(frame) == expected


# ---- ad_gems: the amount glyph beside CLAIM, and the template branch ----------------------------

@pytest.fixture
def digit_templates(monkeypatch, tmp_path, font):
    """The ten digit templates `_six_amount` compares against, drawn by the font fixture."""
    (tmp_path/'digits').mkdir()
    for digit, glyph in font.items():
        cv2.imwrite(str(tmp_path/'digits'/f'{digit}.png'), glyph)
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda relative: tmp_path/relative)
    return font


def amount_patch(font, digit):
    """An offer crop with `digit` where the amount is read (right half, upper band)."""
    patch = np.zeros((150, 248, 3), np.uint8)
    glyph = font[digit]
    patch[25:25+glyph.shape[0], 140:140+glyph.shape[1]] = glyph[:, :, None]
    return patch


def test_the_amount_glyph_must_be_a_six(digit_templates):
    assert ad_gems._six_amount(amount_patch(digit_templates, '6')) is True
    for wrong in '012345789':
        assert ad_gems._six_amount(amount_patch(digit_templates, wrong)) is False, wrong
    assert ad_gems._six_amount(np.zeros((150, 248, 3), np.uint8)) is False


def test_a_thickened_amount_glyph_below_the_score_floor_is_not_a_six(digit_templates):
    """A six drawn 3 px too thick still beats every other digit by .25 but scores .69, under the .8 floor."""
    thick = cv2.dilate(digit_templates['6'], np.ones((3, 3), np.uint8))
    assert ad_gems._six_amount(amount_patch({'6': digit_templates['6']}, '6')) is True      # control: the plain six
    assert ad_gems._six_amount(amount_patch({'6': thick}, '6')) is False


def test_the_claim_template_branch_finds_the_button_and_rejects_other_pixels(monkeypatch, tmp_path):
    rng = np.random.default_rng(7)
    button = rng.integers(0, 255, (60, 120, 3), dtype=np.uint8)
    (tmp_path/'buttons').mkdir()
    cv2.imwrite(str(tmp_path/ad_gems.TEMPLATE), button)
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda relative: tmp_path/relative)
    x, y, _, _ = manifest()['runtime_regions']['ad_gems']['search']
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[y+300:y+360, x+20:x+140] = button
    assert ad_gems.find_claim_rect(frame) == (x+20, y+300, 120, 60)
    assert ad_gems.find_claim_rect(rng.integers(0, 255, (2560, 1080, 3), dtype=np.uint8)) is None
    assert ad_gems.find_claim_rect(np.zeros((2560, 1080, 3), np.uint8)) is None