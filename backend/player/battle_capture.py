"""OCR-labeled capture of the HUD digit font (digits/0-9.png) and upgrade-panel
stat labels (stats/<slug>.png) from a live battle - no manual cropping, no model.

The number font and stat labels are rendered text (TextMeshPro), so they are not
in the installed APK's sprite objects (extraction finds none) - the game screen
is their only source. This reads that screen: deterministic contour segmentation
of the wave box + Windows OCR to LABEL each glyph by the number the OCR read, so
a glyph only becomes digits/<d>.png when the whole number's glyph count lines up
and the counter sits where the HUD draws it. Nothing is guessed.

These are the account's own pixels: written under its git-ignored template dir,
never shipped, and - like every calibration writer - an existing template is
kept, not replaced, unless overwrite is asked for.
"""
import difflib
import os
import re
import cv2
import numpy as np

DIGIT_MIN_H, DIGIT_MIN_W = 28, 6
FIRST_DIGIT_X = (10, 24)          # wave_reader's measured layout proof

# Upgrade-panel stat boxes: white-bordered cells in a 2-column grid, the name on
# the left and a value/price box on the right (interactions/shopper._find_stat).
STAT_BORDER_THRESH = 190
STAT_MIN_W, STAT_MIN_H, STAT_MAX_H = 380, 130, 260
STAT_LABEL_LEFT_FRAC = 0.50       # include long names; the value plaque starts beyond halfway


def glyph_boxes(gray, *, min_h=DIGIT_MIN_H, min_w=DIGIT_MIN_W):
    """Left-to-right bounding boxes of the white glyphs on the dark HUD box -
    the same threshold/segmentation wave_reader uses to READ them."""
    _, bw = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in cnts]
    boxes = [b for b in boxes if b[3] >= min_h and b[2] >= min_w]
    boxes.sort(key=lambda b: b[0])
    return boxes


def digit_glyphs(wave_crop, number):
    """{digit_char: native glyph BGR} for this wave-box crop, or {} when the
    reading cannot be trusted to label glyphs: empty OCR, a glyph count that
    disagrees with the number's length, or a first glyph that is not where the
    wave counter starts (a stray bracket / a non-counter box)."""
    # This ROI contains only the numeric wave field (plus a clipped edge of
    # its label). Windows OCR often spells a round zero as capital O.
    # Glyph count and the native starting position still have to agree.
    number = re.sub(r"\D", "", (number or "").replace('O', '0'))
    if not number:
        return {}
    gray = cv2.cvtColor(wave_crop, cv2.COLOR_BGR2GRAY)
    boxes = glyph_boxes(gray)
    if not boxes or len(boxes) != len(number):
        return {}
    if not (FIRST_DIGIT_X[0] <= boxes[0][0] <= FIRST_DIGIT_X[1]):
        return {}
    return {ch: wave_crop[y:y+h, x:x+w].copy()
            for (x, y, w, h), ch in zip(boxes, number)}


def capture_digits(frames, ocr_number, wave_roi):
    """Accumulate one clean crop per digit 0-9 across `frames`.

    frames: iterable of native BGR frames. ocr_number(bgr_crop) -> string.
    A digit is fixed on its first confident sighting; stops early once all ten
    are in hand. Returns {digit_char: glyph_bgr}.
    """
    x, y, w, h = wave_roi
    got = {}
    for f in frames:
        crop = f[y:y + h, x:x + w]
        for ch, glyph in digit_glyphs(crop, ocr_number(crop)).items():
            got.setdefault(ch, glyph)
        if len(got) == 10:
            break
    return got


def stat_boxes(panel_bgr):
    """Bounding boxes of the upgrade-panel stat cells, top-to-bottom then
    left-to-right. Detected off their bright white border (no morphology - the
    border contour IS the cell)."""
    gray = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, STAT_BORDER_THRESH, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in cnts]
    boxes = [b for b in boxes
             if b[2] > STAT_MIN_W and STAT_MIN_H < b[3] < STAT_MAX_H and b[1] > 60]
    return sorted(boxes, key=lambda b: (b[1] // 80, b[0]))


def stat_slug(text):
    """'Damage / Meter' -> 'damage_per_meter', 'Health Regen' -> 'health_regen'."""
    t = re.sub(r"\s*/\s*", " per ", (text or "").strip().lower())
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def match_stat(label, wanted):
    """Map an OCR'd stat name to one of `wanted` slugs, tolerating OCR slips
    ('Enemy Attack Leve Skip' -> enemy_attack_level_skip). None if not close."""
    s = stat_slug(label)
    if s in wanted:
        return s
    hit = difflib.get_close_matches(s, list(wanted), n=1, cutoff=0.8)
    return hit[0] if hit else None


def _tighten(crop):
    """Trim a label crop to its white text (+small margin) so the template is
    the text, not the surrounding dark box."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray > 150)
    if len(xs) == 0:
        return crop
    p = 6
    y0, y1 = max(0, ys.min() - p), min(crop.shape[0], ys.max() + p)
    x0, x1 = max(0, xs.min() - p), min(crop.shape[1], xs.max() + p)
    return crop[y0:y1, x0:x1]


def capture_stats(panel_bgr, read_text, wanted):
    """{stat_slug: label_crop} for boxes on this panel whose OCR'd name maps to
    a `wanted` slug. read_text(bgr)->str. Labels are trimmed to their text."""
    out = {}
    for x, y, w, h in stat_boxes(panel_bgr):
        label = panel_bgr[y + 6:y + h - 6, x + 8:x + int(w * STAT_LABEL_LEFT_FRAC)]
        slug = match_stat(read_text(label), wanted)
        if slug and slug not in out:
            out[slug] = _tighten(label)
    return out


def capture_max_plaque(panel_bgr, read_text):
    """The grey 'Max' plaque under a maxed stat's value, or None. (stats/max_label.png)"""
    for x, y, w, h in stat_boxes(panel_bgr):
        plaque = panel_bgr[y + int(h * 0.55):y + h - 6, x + int(w * 0.55):x + w - 8]
        if read_text(plaque).strip().lower() == "max":
            return _tighten(plaque)
    return None


def locate_text(frame, region, text, read_lines, *, scale=2, cutoff=0.72):
    """Frame (x, y) of the OCR line in `region` that best matches `text`
    (fuzzy, case-insensitive), or None. read_lines(bgr, scale) -> [(y, x, text)]
    in the upscaled crop's coordinates, so we divide by scale and add the
    region's own offset to land back in the full frame."""
    rx, ry, rw, rh = region
    crop = frame[ry:ry + rh, rx:rx + rw]
    want = text.strip().lower()
    best, best_r = None, cutoff
    for vy, vx, t in read_lines(crop, scale):
        r = difflib.SequenceMatcher(None, (t or "").strip().lower(), want).ratio()
        if r >= best_r:
            best_r, best = r, (rx + int(vx / scale), ry + int(vy / scale))
    return best


def capture_by_text(frame, text, region, size, read_lines, *, anchor=(0.12, 0.30)):
    """Crop of `size` for a text target (button / header / dialog title) found by
    OCR. `anchor` places the matched text's top-left inside the crop as (ax, ay)
    fractions, so the crop extends left of and around the label. None if the text
    is not on screen."""
    loc = locate_text(frame, region, text, read_lines)
    if loc is None:
        return None
    tx, ty = loc
    w, h = size
    x = max(0, min(frame.shape[1] - w, int(tx - anchor[0] * w)))
    y = max(0, min(frame.shape[0] - h, int(ty - anchor[1] * h)))
    return frame[y:y + h, x:x + w].copy()


def write_template(rel, crop, *, overwrite=False):
    """Write an account template under its own git-ignored dir. 'written' |
    'exists' (kept) | 'empty'. Never replaces an existing file unless asked."""
    import settings
    from player import accounts
    if crop is None or getattr(crop, "size", 0) == 0:
        return "empty"
    path = accounts.template_path(settings.ROOT, settings.CONFIG, rel, write=True)
    if path.exists() and not overwrite:
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pending.png")
    if not cv2.imwrite(str(tmp), crop):
        raise OSError(f"could not write {path}")
    os.replace(tmp, path)
    return "written"
