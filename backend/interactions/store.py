"""Guild store: priority-ladder purchasing with guild coins.

User spec: always save toward the NEXT item on the ladder -
  Epic Relic > Blue Relic > missing Guardian Chip > Background > Banner >
  Menu > Tower Skin > Guardians > module shard groups (4) > square upgrade
  > gems
Never buy a lower-priority item while saving for a higher one.

Non-blocking flow (Mission-compatible generator): guild tile -> Store tab ->
scan pages -> resolve the ladder -> buy the target if affordable (instant
taps; store widgets are the same style as the guild milestones). All taps by
FIXED AREAS or matched-template centers; vision only for state.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

from vision import detect
from runtime import logger
from vision import ocr
from interactions.missions import bail, find_tile
from interactions.missions import reward_flow, reward_screen

STORE_TAB = (791, 314)
COIN_ROI = (108, 160, 1000, 1080)    # guild-coin balance digits (icon excluded)
LADDER = ["epic_relic", "blue_relic", "chip", "background", "banner",
          "menu", "tower_skin", "guardian", "shards", "squares", "gems"]
# These are equal-priority alternatives, not interchangeable identities. Once
# one concrete card is chosen, only that card can authorize the purchase.
INTERCHANGEABLE = frozenset({'chip', 'shards'})
MAX_PAGES = 8
IDENTITY_MATCH = .98


@dataclass(frozen=True, eq=False)
class StoreItem:
    category: str | None
    status: str
    point: tuple[int, int]
    price: float | None
    identity: np.ndarray
    page: int = 0


class StoreEvidenceError(RuntimeError):
    """Incomplete or conflicting store evidence cannot authorize spending."""


@dataclass
class StoreScan:
    items: list[StoreItem]
    partial_controls: list[tuple[str, int, int, float | None]]
    partial_panels: list[tuple[int, int, int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class StoreCatalogue:
    items: list[StoreItem]
    complete_sections: frozenset[str]

_HEADERS = {
    "currencies": "icons/store_currencies.png",
    "cosmetics": "icons/store_cosmetics.png",
    "relics": "icons/store_relics.png",
    "tower_guardian": "icons/store_tower_guardian.png",
}
_TITLES = {
    "tower_skin": "icons/title_tower.png",
    "background": "icons/title_background.png",
    "menu": "icons/title_menu.png",
    "banner": "icons/title_banner.png",
    "guardian": "icons/title_guardian.png",
}
_CATEGORY_SECTION = {
    'epic_relic': 'relics', 'blue_relic': 'relics', 'chip': 'tower_guardian',
    **{category: 'cosmetics' for category in _TITLES},
    'shards': 'currencies', 'squares': 'currencies', 'gems': 'currencies',
}


def read_balance(frame):
    y0, y1, x0, x1 = COIN_ROI
    return ocr.read_amount(frame[y0:y1, x0:x1], thresh=180, font="store")


def _price_buttons(frame):
    """Price pills anywhere below the tab bar, found by their COIN ICON
    (pill border color varies with affordability - cyan/grey - but the coin
    is always there; shard pills use a smaller coin -> multi-scale).
    Returns [(cx, cy, price|None)] where (cx,cy) is the pill's tap point."""
    tpl0 = cv2.cvtColor(detect._tpl("icons/guild_coin.png"), cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    coins = []
    for scale in (1.0, 0.8, 0.65):
        tpl = cv2.resize(tpl0, None, fx=scale, fy=scale) if scale != 1.0 else tpl0
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res > 0.72)
        for x, y in zip(xs, ys):
            cx = int(x + tpl.shape[1] // 2)
            cy = int(y + tpl.shape[0] // 2)
            if cy < 380:
                continue                     # top-bar balance coin
            if all(abs(cx - a) > 50 or abs(cy - b) > 40 for a, b, _ in coins):
                coins.append((cx, cy, scale))
    out = []
    for cx, cy, scale in coins:
        w = int(250 * scale)
        hh = int(35 * scale) + 5
        strip = frame[cy - hh:cy + hh, max(0, cx - w):cx - int(25 * scale)]
        price = ocr.read_amount(strip, thresh=180, font="store") \
            if strip.size else None
        out.append((cx - 40, cy, price, scale))  # tap point: pill body
    return out


def _owned_spots(frame):
    tpl = cv2.cvtColor(detect._tpl("buttons/store_owned.png"),
                       cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res > 0.8)
    spots = []
    for x, y in zip(xs, ys):
        cx = int(x + tpl.shape[1] // 2)
        cy = int(y + tpl.shape[0] // 2)
        if all(abs(cx - a) > 60 or abs(cy - b) > 40 for a, b in spots):
            spots.append((cx, cy))
    return spots


def _header_ys(frame):
    out = {}
    for name, tpl in _HEADERS.items():
        hit, _, loc = detect._match(frame, tpl, 0.75)
        if hit:
            out[name] = loc[1]
    return out


def _content_band(frame):
    from player.bootstrap_layout import manifest
    guild = manifest()['screens']['guild']
    top = max(y + h for _, y, _, h in guild['selected_tab']['rects'])
    bottom = next(t['rect'][1] for t in guild['targets']
                  if t['rel'] == 'buttons/return_to_game.png')
    return top, min(bottom, frame.shape[0])


def _panel_mask(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    return ((v > 200) & ((s < 65) | ((h > 80) & (h < 110)))).astype(np.uint8)


def _partial_panels(frame, bounds=None):
    """Keep boundary-crossing panel outlines even before their control appears.

    A wide top edge with clipped sides can be only a few pixels tall. Long
    isolated sides also matter when both horizontal edges are out of view.
    Neither kind is subject to the complete-card minimum height/area filter.
    """
    top, bottom = _content_band(frame) if bounds is None else bounds
    mask = _panel_mask(frame)[top:bottom]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    minimum_width = frame.shape[1] * .15
    minimum_side = (_content_band(frame)[1] - _content_band(frame)[0]) * .08
    result = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        crossing = y == 0 or y+h == mask.shape[0] or x == 0 or x+w == mask.shape[1]
        if crossing and (w >= minimum_width or h >= minimum_side and w <= h * .1):
            result.append((x, y+top, w, h))
    return result


def _card_rects(frame, bounds=None):
    """Whole outlined store panels, never a guessed area above a price.

    The captured store has closed rectangular cyan/white panel outlines;
    clipped panels have no closed contour. Nested artwork/control rectangles
    are discarded in favour of their enclosing panel. No pixels are saved.
    """
    top, native_bottom = _content_band(frame)
    minimum_height = (native_bottom - top) * .08
    if bounds is not None:
        top, bottom = bounds
    else:
        bottom = native_bottom
    contours, _ = cv2.findContours(_panel_mask(frame), cv2.RETR_LIST,
                                  cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for contour in contours:
        x, y, w, height = cv2.boundingRect(contour)
        if (w < frame.shape[1] * .15 or height < minimum_height
                or y <= top or y + height >= bottom):
            continue
        corners = cv2.approxPolyDP(contour, .015 * cv2.arcLength(contour, True), True)
        if len(corners) == 4 and cv2.contourArea(contour) >= .93 * w * height:
            rects.append((x, y, w, height))
    panels = []
    for rect in sorted(rects, key=lambda r: r[2] * r[3], reverse=True):
        x, y, w, h = rect
        if not any(a <= x and b <= y and x + w <= a + c and y + h <= b + d
                   for a, b, c, d in panels):
            panels.append(rect)
    return sorted(panels, key=lambda r: (r[1], r[0]))


def _relic_color(identity):
    hsv = cv2.cvtColor(identity, cv2.COLOR_BGR2HSV)
    mag = (cv2.inRange(hsv, (140, 90, 130), (175, 255, 255)) > 0).mean()
    cyn = (cv2.inRange(hsv, (85, 90, 130), (105, 255, 255)) > 0).mean()
    if max(mag, cyn) < .005 or abs(mag - cyn) < .005:
        return None
    return "epic_relic" if mag > cyn else "blue_relic"


def scan_frame(frame, page=0, *, bounds=None):
    """Keep every observed card, its concrete identity and its current control.

    Clipped-card controls remain explicit until the complete scan proves they
    belong to a fully observed card. A missing category is retained as unknown,
    never silently skipped in favour of cheaper merchandise.
    """
    headers = _header_ys(frame)
    controls = [('price', x, y, price, scale)
                for x, y, price, scale in _price_buttons(frame)]
    controls += [('owned', x, y, None, None) for x, y in _owned_spots(frame)]
    panels = _card_rects(frame, bounds)
    # Both states use the same exclusion band, so a price -> OWNED transition
    # cannot change the pixels used as the item's identity.
    control_height = max(detect._tpl('icons/guild_coin.png').shape[0],
                         detect._tpl('buttons/store_owned.png').shape[0])
    found = []
    partial = []

    def section_of(y):
        best, besty = None, -1
        for name, hy in headers.items():
            if hy < y and hy > besty:
                best, besty = name, hy
        return best

    def title_of(rect):
        x, y, w, h = rect
        matches = set()
        for cat, tpl_name in _TITLES.items():
            tpl = detect._tpl(tpl_name)
            if tpl.shape[0] > h or tpl.shape[1] > w:
                continue
            # Match inside this complete card; a different card's first title
            # hit is never evidence about this card.
            scores = cv2.matchTemplate(frame[y:y+h, x:x+w], tpl, cv2.TM_CCOEFF_NORMED)
            if float(scores.max()) >= .75:
                matches.add(cat)
        if len(matches) > 1:
            raise StoreEvidenceError('conflicting card titles')
        return next(iter(matches), None)

    grouped = {}
    for status, cx, cy, price, scale in controls:
        owners = [r for r in panels if r[0] < cx < r[0]+r[2] and r[1] < cy < r[1]+r[3]]
        if not owners:
            partial.append((status, cx, cy, price))
            continue
        if len(owners) != 1:
            raise StoreEvidenceError('purchase/owned control has no unique card')
        rect = owners[0]
        if rect in grouped:
            raise StoreEvidenceError('multiple purchase/owned controls in one card')
        grouped[rect] = (status, cx, cy, price, scale)
    if len(grouped) != len(panels):
        raise StoreEvidenceError('complete card has no recognized price/owned state')
    for rect, (status, cx, cy, price, scale) in grouped.items():
        x, y, w, h = rect
        # The control must occupy the lower part of the observed panel. Its
        # glyph height defines what to exclude; no generic icon offset is used.
        if cy <= y + h // 2:
            raise StoreEvidenceError('control is not below the card identity')
        inset = max(2, round(min(w, h) * .03))
        end = cy - control_height
        identity = frame[y+inset:end, x+inset:x+w-inset].copy()
        if (identity.size == 0 or identity.shape[0] < h // 3
                or float(cv2.cvtColor(identity, cv2.COLOR_BGR2GRAY).std()) < 10):
            raise StoreEvidenceError('card identity is incomplete or uninformative')
        sec = section_of(y)
        if sec == "relics":
            cat = _relic_color(identity)
        elif sec == "tower_guardian":
            cat = "chip"
        elif sec == 'currencies' and scale is not None:
            cat = 'shards' if scale < .95 else ('gems' if cx < frame.shape[1]//2 else 'squares')
        else:
            cat = title_of(rect)
        found.append(StoreItem(cat, status, (cx, cy), price, identity, page))
    return StoreScan(found, partial, _partial_panels(frame, bounds))


def _same_item(a: StoreItem, b: StoreItem) -> bool:
    if a.identity.shape != b.identity.shape:
        return False
    score = float(cv2.matchTemplate(a.identity, b.identity, cv2.TM_CCOEFF_NORMED)[0, 0])
    return score >= IDENTITY_MATCH


def _target(catalogue: StoreCatalogue):
    if any(item.category is None and item.status == 'price' for item in catalogue.items):
        raise StoreEvidenceError('unclassified merchandise could outrank the target')
    for rank, category in enumerate(LADDER):
        items = [i for i in catalogue.items if i.category == category and i.status == 'price']
        if not items:
            continue
        required = {_CATEGORY_SECTION[higher] for higher in LADDER[:rank]}
        if not required <= catalogue.complete_sections:
            raise StoreEvidenceError('higher-priority merchandise coverage is not proven; final store extent unknown')
        if len(items) != 1 and category not in INTERCHANGEABLE:
            raise StoreEvidenceError('multiple distinct items in a unique ladder category')
        return min(items, key=lambda i: (i.page, i.point[1], i.point[0]))
    if catalogue.complete_sections != frozenset(_HEADERS):
        raise StoreEvidenceError('unseen final merchandise cannot be declared owned or unavailable')
    return None


def _catalogue(frame, top):
    """Only successive observed headers establish a section's full interval.

    No calibrated end/extent exists for Tower Guardian. No-motion, closed rows
    or clean margins cannot certify its unseen remainder. Lower-priority
    purchases therefore remain unavailable until genuine end evidence exists;
    a positively observed chip can still outrank them without proving absence.
    """
    headers = _header_ys(frame)
    expected = list(_HEADERS)
    if set(headers) != set(expected) or sorted(headers, key=headers.get) != expected:
        raise StoreEvidenceError('store section coverage/order is not proven')
    positions = [headers[name] for name in expected]
    if len(set(positions)) != len(positions):
        raise StoreEvidenceError('store section intervals overlap')
    bounds = (top, frame.shape[0])
    complete = scan_frame(frame, bounds=bounds)
    final_start = positions[-1]
    if (any(y < final_start for _, _, y, _ in complete.partial_controls)
            or any(y < final_start for _, y, _, _ in complete.partial_panels)):
        raise StoreEvidenceError('clipped merchandise invalidates bounded section coverage')
    for x, y, w, h in _card_rects(frame, bounds):
        section = next((name for name in reversed(expected) if headers[name] < y), None)
        if section is None:
            raise StoreEvidenceError('card is outside the recognized store sections')
        index = expected.index(section)
        if index+1 < len(expected) and y+h >= positions[index+1]:
            raise StoreEvidenceError('card crosses a recognized section boundary')
    if any(_same_item(a, b) for n, a in enumerate(complete.items) for b in complete.items[n+1:]):
        raise StoreEvidenceError('distinct store positions have indistinguishable identities')
    return StoreCatalogue(complete.items, frozenset(expected[:-1]))


def _stable(a, b):
    if a.shape != b.shape:
        return False
    top, bottom = _content_band(a)
    # The fixed balance/header are outside the scrolling body.
    return float(cv2.absdiff(a[top:bottom], b[top:bottom]).mean()) < 1.0


def _scroll_distance(before, after):
    """Measure overlap; counting gestures cannot prove catalogue coverage."""
    top, bottom = _content_band(before)
    height = (bottom - top) // 6
    offsets = []
    for y in range(top + height, bottom - height, height):
        strip = before[y:y+height]
        if float(strip.std()) < 10:
            continue
        scores = cv2.matchTemplate(after[top:bottom], strip, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(scores)
        if score < IDENTITY_MATCH:
            continue
        offset = y - top - loc[1]
        # Ignore ambiguous repeated rows even when their best match is perfect.
        scores[max(0, loc[1]-3):loc[1]+4] = -1
        if float(scores.max()) >= IDENTITY_MATCH:
            continue
        offsets.append(offset)
    if len(offsets) < 2 or max(offsets)-min(offsets) > 3 or min(offsets) <= 0:
        raise StoreEvidenceError('store page overlap not uniquely measured')
    return round(sum(offsets) / len(offsets))


def _settled(guard, frame):
    for _ in range(6):
        following = yield from guard.wait()
        if _stable(frame, following):
            return following
        frame = following
    raise StoreEvidenceError('store viewport did not settle')


def _top(guard, frame):
    for _ in range(4):
        previous = frame
        guard.swipe(frame, 540, 700, 540, 1900, 400,
                    screen='guild_store', reason='store scroll')
        frame = yield from guard.wait()
        frame = yield from _settled(guard, frame)
        if _stable(previous, frame):
            if 'currencies' not in _header_ys(frame):
                raise StoreEvidenceError('stationary viewport does not prove the store top')
            return frame
    raise StoreEvidenceError('store top not proven within scroll bound')


def _walk(guard, frame):
    """Stitch measured overlapping views in memory; inspect physical cards once.

    This also makes a panel cut across two views observable as a whole. No
    visual-identity deduplication can silently merge two distinct purchases.
    """
    top, bottom = _content_band(frame)
    mosaic = frame[:bottom].copy()
    for page in range(MAX_PAGES):
        scanned = scan_frame(frame, page)
        observed = scanned.items
        if any(_same_item(a, b) for n, a in enumerate(observed) for b in observed[n+1:]):
            raise StoreEvidenceError('two visible cards have indistinguishable identities')
        previous = frame
        guard.swipe(frame, 540, 1900, 540, 700, 400,
                    screen='guild_store', reason='store scroll')
        frame = yield from guard.wait()
        frame = yield from _settled(guard, frame)
        if _stable(previous, frame):
            return frame, _catalogue(mosaic, top)
        distance = _scroll_distance(previous, frame)
        # The overlapping body must agree, including card controls: a price or
        # ownership change mid-scan makes the stitched catalogue untrustworthy.
        overlap = bottom - top - distance
        if overlap <= 0 or float(cv2.absdiff(previous[top+distance:bottom],
                                            frame[top:bottom-distance]).mean()) >= 1.0:
            raise StoreEvidenceError('store changed or overlap was lost during scan')
        mosaic = np.concatenate((mosaic, frame[bottom-distance:bottom]), axis=0)
    raise StoreEvidenceError('store end not proven within page bound')


def _locate(guard, frame, target):
    frame = yield from _top(guard, frame)
    for page in range(MAX_PAGES):
        matches = [i for i in scan_frame(frame, page).items if _same_item(target, i)]
        if len(matches) > 1:
            raise StoreEvidenceError('target identity is ambiguous on current viewport')
        if matches:
            item = matches[0]
            if item.category is not None and item.category != target.category:
                raise StoreEvidenceError('target category changed')
            return frame, item
        previous = frame
        guard.swipe(frame, 540, 1900, 540, 700, 400,
                    screen='guild_store', reason='store scroll')
        frame = yield from guard.wait()
        frame = yield from _settled(guard, frame)
        if _stable(previous, frame):
            break
    raise StoreEvidenceError('selected item no longer found')


def _purchase(guard, frame, target):
    """One attempt; a later guild-claim visit is a new policy decision.

    Transport errors and ambiguous outcomes never dispatch another purchase.
    Only this concrete card becoming owned AND the exact debit prove success.
    Repeatable merchandise without that item-specific transition stays unknown.
    """
    frame, current = yield from _locate(guard, frame, target)
    balance = read_balance(frame)
    if (current.status != 'price' or current.price is None
            or current.price != target.price or current.price <= 0):
        raise StoreEvidenceError('target availability or price changed/unreadable')
    if balance is None or balance < current.price:
        logger.event('store_saving', target=target.category, price=current.price,
                     balance=balance, reason='unreadable_or_insufficient_balance')
        return frame
    confirmed = False
    reason = 'interrupted_after_purchase_attempt'
    try:
        # Record intent before dispatch: an exception cannot prove whether an
        # input reached the game, so it must not authorize a second tap.
        logger.event('store_purchase_attempt', item=target.category, price=current.price,
                     balance_before=balance, point=list(current.point))
        guard.tap(frame, *current.point, f'store_buy_{target.category}',
                  screen='guild_store', rewards=True)
        frame = yield from guard.wait()
        frame = yield from guard.wait()
        for _ in range(6):
            name = reward_screen(frame)
            if name == 'guild_store':
                break
            if name == 'reward':
                guard.dismiss(frame, 'guild_store')
            frame = yield from guard.wait()
        else:
            reason = 'reward_confirmation_timeout'
            return frame
        frame = yield from _settled(guard, frame)
        frame, after = yield from _locate(guard, frame, target)
        after_balance = read_balance(frame)
        if after.status == 'owned' and after_balance == balance - current.price:
            logger.event('store_buy', item=target.category, price=current.price,
                         balance_before=balance, balance_after=after_balance,
                         proof='same_item_owned_and_exact_debit',
                         shot=logger.shot(frame, f'store_bought_{target.category}'))
            confirmed = True
        else:
            reason = 'item_transition_or_exact_debit_not_proven'
        return frame
    finally:
        if not confirmed:
            logger.event('store_purchase_unverified', item=target.category,
                         price=current.price, balance_before=balance,
                         outcome='unknown', reason=reason, retry=False)


@reward_flow
def store_flow(guard):
    """Scan the guild store and buy the ladder target if affordable."""
    frame = yield from guard.wait()
    point = find_tile(frame, "icons/tile_guild.png")
    if point is None:
        logger.event('store_error', stage='guild_icon_unavailable')
        return
    guard.tap(frame, *point, 'guild_open', screen='battle_menu', destination='guild_entry')
    frame = yield from guard.wait()
    ok = False
    for _ in range(4):
        if detect._match(frame, "icons/guild_header.png", 0.75)[0]:
            ok = True
            break
        frame = yield from guard.wait()
    if not ok:
        logger.event("mission_error", stage="store_open",
                     shot=logger.shot(frame, "store_open_fail"))
        bail(frame, "store_open", guard)
        return
    guard.tap(frame, *STORE_TAB, 'store_tab', screen=reward_screen(frame), destination='guild_store')
    frame = yield from guard.wait()
    frame = yield from guard.wait()

    try:
        frame = yield from _top(guard, frame)
        frame, catalogue = yield from _walk(guard, frame)
        target = _target(catalogue)
        if target is None:
            logger.event('store_done', balance=read_balance(frame), result='no_priced_target')
        else:
            frame = yield from _purchase(guard, frame, target)
    except StoreEvidenceError as exc:
        # Helpers may have yielded a newer frame before rejecting evidence.
        # Only the guard's latest frame can authorize an exit.
        frame = guard.frame
        logger.event('store_refused', reason=str(exc),
                     shot=logger.shot(frame, 'store_evidence_refused') if frame is not None else None)
    if frame is None or reward_screen(frame) != 'guild_store':
        return

    for _ in range(4):
        guard.exit(frame)
        frame = yield from guard.wait()
        if not detect._match(frame, "icons/guild_header.png", 0.75)[0]:
            return
    logger.event("mission_error", stage="store_return",
                 shot=logger.shot(frame, "store_return_fail"))
