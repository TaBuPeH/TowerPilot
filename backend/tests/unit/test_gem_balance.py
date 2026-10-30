"""Strict HUD reads, with optional ignored local counter-only replay fixtures."""

import paths as test_paths
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision import ocr


@pytest.fixture
def font(monkeypatch):
    glyphs = {}
    for digit in '0123456789':
        canvas = np.zeros((70, 60), np.uint8)
        cv2.putText(canvas, digit, (5, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 255, 2)
        _, canvas = cv2.threshold(canvas, 180, 255, cv2.THRESH_BINARY)
        x, y, w, h = cv2.boundingRect(cv2.findNonZero(canvas))
        glyphs[digit] = canvas[y:y+h, x:x+w]
    monkeypatch.setattr(ocr, '_load', lambda: glyphs)
    return glyphs


def hud(font, text='747'):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    x, y, _, _ = ocr.GEM_BALANCE_REGION
    x += 6
    for ch in text:
        glyph = font[ch]
        h, w = glyph.shape
        frame[y+10:y+10+h, x:x+w] = glyph[:, :, None]
        x += w+5
    return frame


def test_reads_integer_balance_using_existing_font(font):
    assert ocr.read_gem_balance(hud(font)) == 747
    assert ocr.read_gem_balance(hud(font, '1029')) == 1029


def test_missing_font_is_not_a_zero_balance(font):
    frame = hud(font)
    del font['0']
    assert ocr.read_gem_balance(frame) is None


def test_ambiguous_digit_is_not_a_collection(font):
    frame = hud(font)
    font['9'] = font['7'].copy()
    assert ocr.read_gem_balance(frame) is None


def test_wrong_resolution_blank_and_clipped_digits_fail_closed(font):
    assert ocr.read_gem_balance(np.zeros((1280, 540, 3), np.uint8)) is None
    assert ocr.read_gem_balance(np.zeros((2560, 1080, 3), np.uint8)) is None
    frame = hud(font)
    x, y, w, h = ocr.GEM_BALANCE_REGION
    frame[y:y+h, x:x+w] = 0
    glyph = font['7']
    frame[y:y+glyph.shape[0], x+6:x+6+glyph.shape[1]] = glyph[:, :, None]
    assert ocr.read_gem_balance(frame) is None


def test_detached_off_line_battle_art_is_not_a_balance_digit(font):
    frame = hud(font)
    x, y, w, h = ocr.GEM_BALANCE_REGION
    cv2.rectangle(frame, (x+w-65, y+20), (x+w-15, y+h+15), (255, 255, 255), 4)
    assert ocr.read_gem_balance(frame) == 747


@pytest.mark.parametrize('kind', ['digit', 'unknown', 'fragment'])
def test_separated_same_line_pixels_cannot_be_discarded_as_battle_art(font, kind):
    frame = hud(font)
    x, y, w, _ = ocr.GEM_BALANCE_REGION
    gx = x+w-45
    if kind == 'digit':
        glyph = font['1']
        frame[y+10:y+10+glyph.shape[0], gx:gx+glyph.shape[1]] = glyph[:, :, None]
    elif kind == 'unknown':
        cv2.rectangle(frame, (gx, y+10), (gx+20, y+43), (255, 255, 255), -1)
    else:
        cv2.rectangle(frame, (gx, y+20), (gx+3, y+25), (255, 255, 255), -1)
    assert ocr.read_gem_balance(frame) is None


@pytest.mark.parametrize('kind', ['nearby_art', 'damaged_digit', 'small_unknown'])
def test_unreadable_pixels_in_the_number_never_return_a_partial_balance(font, kind):
    frame = hud(font)
    x, y, _, h = ocr.GEM_BALANCE_REGION
    end = x+6+sum(font[ch].shape[1]+5 for ch in '747')-5
    if kind == 'nearby_art':
        cv2.rectangle(frame, (end+5, y+20), (end+30, y+h+15), (255, 255, 255), 4)
    elif kind == 'damaged_digit':
        cv2.rectangle(frame, (x+6, y+17), (x+28, y+37), (255, 255, 255), -1)
    else:
        cv2.rectangle(frame, (end+5, y+20), (end+8, y+25), (255, 255, 255), -1)
    assert ocr.read_gem_balance(frame) is None


@pytest.mark.parametrize('kind', ['overlapping_fragment', 'one_blank_bottom_row'])
def test_separated_clipped_unknowns_never_return_a_prefix(font, kind):
    frame = hud(font)
    x, y, w, h = ocr.GEM_BALANCE_REGION
    left = x+w-65
    if kind == 'overlapping_fragment':
        # A damaged final glyph can overlap the text band and join lower art.
        cv2.rectangle(frame, (left, y+14), (left+20, y+h+10), (255, 255, 255), -1)
    else:
        # The last occupied row is h-2: this is not clipped at the crop edge.
        cv2.rectangle(frame, (left, y+20), (left+50, y+h-2), (255, 255, 255), 1)
    assert ocr.read_gem_balance(frame) is None


@pytest.fixture
def local_counter(monkeypatch):
    # Captured game pixels stay ignored and local (CLAUDE.md rule 8). A release
    # runs the synthetic cases above; offline incident replay supplies this dir.
    configured = os.environ.get('TOWERPILOT_R14_FIXTURES')
    root = Path(configured) if configured else test_paths.FIXTURES / 'r14-gem-balance'
    if not configured and not root.exists():
        pytest.skip('local R14 counter crops and digit templates are not shipped')
    assert root.is_dir(), root
    monkeypatch.setattr(ocr.settings, 'template_files', lambda pattern: sorted((root / 'templates').glob(pattern)))
    monkeypatch.setattr(ocr, '_glyph_cache', {})
    return root


@pytest.mark.parametrize('filename,expected', [('before.png', 1937), ('collecting.png', 1943)])
def test_saved_r14_counter_pixels(local_counter, filename, expected):
    crop = cv2.imread(str(local_counter / filename))
    x, y, w, h = ocr.GEM_BALANCE_REGION
    assert crop is not None and crop.shape == (h, w, 3)
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[y:y+h, x:x+w] = crop
    assert ocr.read_gem_balance(frame) == expected


@pytest.mark.parametrize('filename', ['before.png', 'collecting.png'])
def test_saved_r14_damaged_first_digit_remains_unknown(local_counter, filename):
    crop = cv2.imread(str(local_counter / filename))
    assert crop is not None
    frame = np.zeros((2560, 1080, 3), np.uint8)
    x, y, w, h = ocr.GEM_BALANCE_REGION
    frame[y:y+h, x:x+w] = crop
    frame[y+10:y+49, x+5:x+20] = 255
    assert ocr.read_gem_balance(frame) is None
