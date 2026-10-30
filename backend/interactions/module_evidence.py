"""Read module UI, equipment and inventory from calibrated native geometry.

Normal runners never call calibration drivers or write template pixels.
"""
import cv2
import numpy as np

from device import act, capture
from player.bootstrap_layout import manifest
from vision import detect, pills


def ui_version(frame):
    """Positive v29 tab geometry; absence is unknown, never legacy evidence."""
    from interactions import tourney
    if not tourney.find(frame, 'modules/buy_module.png'):
        return None
    tabs = pills.pills(frame, *manifest()['tab_bands']['modules'])
    if tabs and sum(tab['state'] == 'green' for tab in tabs) == 1:
        return 'v29'
    return None


def require_screen(frame, version=None):
    from interactions import inventory, tourney
    if (not tourney.find(frame, 'modules/buy_module.png')
            or inventory._panel_open(frame)
            or version == 'v29' and ui_version(frame) != 'v29'):
        raise tourney.Abort('module inventory screen is unknown or interrupted')


def _slots(frame):
    """The existing ring-occupancy test, using the current bound manifest."""
    spec = manifest()['module_slots']
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lit = (hsv[..., 1] > 90) & (hsv[..., 2] > 120)
    categories = ('cannon', 'armor', 'generator', 'core')
    result = []
    for kind in ('large', 'small'):
        radius = spec['radius'][kind]
        for category, (cx, cy) in zip(categories, spec[kind]):
            ring = np.zeros(frame.shape[:2], np.uint8)
            cv2.circle(ring, (cx, cy), radius, 255, 10)
            pixels = ring > 0
            occupancy = float(lit[pixels].mean()) if pixels.any() else 0
            half = int(radius * .62)
            if cy-half < 0 or cx-half < 0 or cy+half > frame.shape[0] or cx+half > frame.shape[1]:
                continue
            result.append(dict(kind=kind, category=category, occupied=occupancy >= pills.RING_OCCUPIED,
                               crop=frame[cy-half:cy+half, cx-half:cx+half]))
    return result


def verify_v29(name, frame):
    """True only for a unique positive occupied category-slot identity.

    The v29 application contract equips by category, ignoring old role labels.
    Matching a tile somewhere in the inventory is never equipped evidence.
    """
    from interactions import tourney
    category = tourney.MODULE_CATEGORY.get(name)
    if category is None:
        return None
    try:
        template = detect._tpl(f'modules/equipped/{name}.png')
        threshold = .88  # existing dedicated-equipped-template threshold
        scales = (1.0,)
    except detect.TemplateMissing:
        try:
            template = detect._tpl(f'modules/{name}.png')
        except detect.TemplateMissing:
            return None
        h, w = template.shape[:2]
        template = template[int(h*.25):int(h*.65), int(w*.25):int(w*.75)]
        threshold = tourney.STRICT
        scales = np.arange(.45, 1.15, .05)
    if template.size == 0 or float(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY).std()) < 10:
        return None
    hits = []
    for slot in _slots(frame):
        if not slot['occupied']:
            continue
        crop = slot['crop']
        score = 0.0
        for scale in scales:
            scaled = cv2.resize(template, None, fx=float(scale), fy=float(scale))
            if scaled.shape[0] > crop.shape[0] or scaled.shape[1] > crop.shape[1]:
                continue
            score = max(score, float(cv2.matchTemplate(crop, scaled, cv2.TM_CCOEFF_NORMED).max()))
        if score >= threshold:
            hits.append(slot)
    if len(hits) > 1:
        return None
    if not hits:
        return False
    slot = hits[0]
    return slot['kind'] == 'large' and category == slot['category']


def settled(version=None):
    from interactions import inventory, tourney
    inventory.configure_geometry()
    frame, quiet = inventory.settle_status()
    require_screen(frame, version)
    if not quiet:
        raise tourney.Abort('module inventory did not settle')
    return frame


def _grid_hits(frame, template):
    """Unique matches inside complete observed tiles, not clipped grid edges."""
    spec = manifest()['module_inventory']
    y0, y1, x0, x1 = spec['grid_search']
    spans = _row_spans(frame)
    rows = [(a+b)//2 for a, b in spans if b-a > spec['tile'] * 2/3]
    half = spec['tile']//2
    hits = []
    for cy in rows:
        for cx in spec['columns']:
            edge = spec['tile_half']
            if (cy-edge < spec['grid_clear'][0] or cy+edge > spec['grid_clear'][1]
                    or cy-edge < 0 or cy+edge > frame.shape[0]
                    or cx-edge < 0 or cx+edge > frame.shape[1]):
                continue
            tile = frame[cy-half:cy+half, cx-half:cx+half]
            if template.shape[0] > tile.shape[0] or template.shape[1] > tile.shape[1]:
                continue
            scores = cv2.matchTemplate(tile, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(scores)
            if score >= .95:
                hits.append((cx-half+loc[0]+template.shape[1]//2,
                             cy-half+loc[1]+template.shape[0]//2))
    return hits


def _row_spans(frame):
    spec = manifest()['module_inventory']
    y0, y1, x0, x1 = spec['grid_search']
    return pills.grid_row_spans(frame, y0, y1, x0, x1,
                                min_lit=max(1, round(40 * frame.shape[1] / 1080)))


def _at_top(frame):
    gap = manifest()['module_inventory']['grid_gap_y']
    return not any(a <= gap < b for a, b in _row_spans(frame))


def _scroll_distance(before, after):
    spec = manifest()['module_inventory']
    top, bottom, left, right = spec['grid_search']
    if float(cv2.absdiff(before[top:bottom, left:right], after[top:bottom, left:right]).mean()) < .5:
        return 0
    rows = [(a, b) for a, b in _row_spans(before) if b-a > spec['tile'] * 2/3]
    offsets = []
    for a, b in rows[-2:]:
        strip = before[a:b, left:right]
        scores = cv2.matchTemplate(after[top:bottom, left:right], strip, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(scores)
        if score >= .9:
            offsets.append(a-top-loc[1])
    if not offsets or min(offsets) <= 0 or max(offsets)-min(offsets) > 3:
        return None
    return offsets[-1]


def find_inventory(name, version=None):
    """Bounded settled scan and a separate current-frame target acquisition."""
    from interactions import inventory, tourney
    try:
        template = detect._tpl(f'modules/{name}.png')
    except detect.TemplateMissing as exc:
        raise tourney.Abort(f'no inventory template for module {name}') from exc
    if float(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY).std()) < 10:
        raise tourney.Abort(f'module {name} inventory template is not discriminating')
    spec = manifest()['module_inventory']
    frame = settled(version)
    for _ in range(5):
        if _at_top(frame):
            break
        require_screen(frame, version)
        act.swipe(spec['swipe_x'], spec['grid_band'][0],
                  spec['swipe_x'], spec['grid_band'][1], 180,
                  reason='module inventory to top')
        frame = settled(version)
    if not _at_top(frame):
        raise tourney.Abort('module inventory top not proven')
    for _ in range(inventory.MAX_PAGES):
        hits = _grid_hits(frame, template)
        if len(hits) > 1:
            raise tourney.Abort(f'module {name} inventory identity is ambiguous')
        if hits:
            current = settled(version)
            fresh = _grid_hits(current, template)
            if len(fresh) != 1 or max(abs(a-b) for a, b in zip(fresh[0], hits[0])) > 3:
                raise tourney.Abort(f'module {name} moved or changed before opening')
            return current, fresh[0]
        require_screen(frame, version)
        previous = frame
        y0, y1 = spec['page_drag']
        act.swipe(spec['swipe_x'], y0, spec['swipe_x'], y1, 1500,
                  reason='module inventory page')
        frame = settled(version)
        distance = _scroll_distance(previous, frame)
        if distance is None:
            raise tourney.Abort('module inventory page overlap was lost')
        if distance == 0:
            return frame, None
    raise tourney.Abort('module inventory scan bound reached before proving the end')


def require_legacy_dialog(frame):
    """Only a positive owned legacy control can select legacy choreography."""
    from interactions import inventory, tourney
    if (not inventory._panel_open(frame)
            or not tourney.find(frame, 'modules/equip_btn.png', tourney.STRICT)
            or tourney.find(frame, 'modules/v29_equip_btn.png', tourney.STRICT)
            or ui_version(frame) == 'v29'):
        raise tourney.Abort('legacy module Equip dialog is not positively recognized')
