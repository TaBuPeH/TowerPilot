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
import subprocess
import time

import cv2
import numpy as np

from settings import CONFIG, ROOT, adb_args, input_args, run_hidden
from device import capture
from vision import detect
from device import act
from runtime import logger
from vision import ocr
from interactions.missions import GUILD_TILE, RETURN_STRIP, find_skip, bail, find_tile

STORE_TAB = (791, 314)
COIN_ROI = (108, 160, 1000, 1080)    # guild-coin balance digits (icon excluded)
LADDER = ["epic_relic", "blue_relic", "chip", "background", "banner",
          "menu", "tower_skin", "guardian", "shards", "squares", "gems"]

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


def _relic_color(frame, cx, cy):
    """Icon sits ~330px above the price/owned button; magenta = epic,
    cyan = blue relic."""
    icon = frame[max(0, cy - 480):cy - 90, max(0, cx - 200):cx + 200]
    hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
    mag = (cv2.inRange(hsv, (140, 90, 130), (175, 255, 255)) > 0).mean()
    cyn = (cv2.inRange(hsv, (85, 90, 130), (105, 255, 255)) > 0).mean()
    return "epic_relic" if mag > cyn else "blue_relic"


def scan_frame(frame):
    """Classify every card visible in ONE frame.
    Returns {category: ('price', cx, cy, price) | ('owned',)}."""
    headers = _header_ys(frame)
    prices = _price_buttons(frame)
    owned = _owned_spots(frame)
    found = {}

    def section_of(y):
        best, besty = None, -1
        for name, hy in headers.items():
            if hy < y and hy > besty:
                best, besty = name, hy
        return best

    def title_of(cx, cy):
        for cat, tpl_name in _TITLES.items():
            hit, _, loc = detect._match(frame, tpl_name, 0.75)
            if not hit:
                continue
            tpl = detect._tpl(tpl_name)
            tx, ty = loc[0] + tpl.shape[1] // 2, loc[1]
            if abs(tx - cx) < 280 and 0 < cy - ty < 700:
                return cat
        return None

    for cx, cy, price, scale in prices:
        sec = section_of(cy)
        if sec == "relics":
            cat = _relic_color(frame, cx, cy)
        elif sec == "tower_guardian":
            cat = "chip"
        elif sec == "currencies":
            # coin SIZE tells the row: big = gems/squares, small = shards
            if scale < 0.95:
                cat = "shards"
            else:
                cat = "gems" if cx < 540 else "squares"
        else:
            cat = title_of(cx, cy)
        if cat and cat not in found:
            found[cat] = ("price", cx, cy, price)
    for cx, cy in owned:
        sec = section_of(cy)
        if sec == "relics":
            cat = _relic_color(frame, cx, cy)
        elif sec == "tower_guardian":
            cat = "chip_owned_slot"      # a chip slot that IS owned
        else:
            cat = title_of(cx, cy)
        if cat and cat not in found:
            found[cat] = ("owned",)
    return found


def _swipe(y0, y1):
    act.swipe(540, y0, 540, y1, 400, reason="store scroll")


def store_flow():
    """Scan the guild store and buy the ladder target if affordable."""
    frame = yield
    act.tap(*find_tile(frame, "icons/tile_guild.png", GUILD_TILE),
            reason="guild_open", instant=True)
    frame = yield
    ok = False
    for _ in range(4):
        if detect._match(frame, "icons/guild_header.png", 0.75)[0]:
            ok = True
            break
        frame = yield
    if not ok:
        logger.event("mission_error", stage="store_open",
                     shot=logger.shot(frame, "store_open_fail"))
        bail(frame, "store_open")
        return
    act.tap(*STORE_TAB, reason="store_tab", instant=True)
    frame = yield
    frame = yield

    # scroll to top first
    for _ in range(4):
        _swipe(700, 1900)
        frame = yield

    balance = read_balance(frame)
    state: dict = {}
    # walk down: scan, stride, scan ... (3 strides covers the whole store)
    for i in range(4):
        for cat, info in scan_frame(frame).items():
            if cat not in state or state[cat][0] == "owned":
                state[cat] = info
        if i < 3:
            _swipe(1900, 700)
            frame = yield

    # chips: any priced chip means one is missing; owned slots alone = all owned
    if "chip" not in state and "chip_owned_slot" in state:
        state["chip"] = ("owned",)
    state.pop("chip_owned_slot", None)

    target, info = None, None
    for cat in LADDER:
        st = state.get(cat)
        if st is None:
            continue                      # not seen this week - skip
        if st[0] == "owned":
            continue
        target, info = cat, st
        break

    if target is None:
        logger.event("store_done", balance=balance, result="all_owned")
    else:
        _, cx, cy, price = info
        if balance is not None and price is not None and balance >= price:
            act.tap(cx, cy, reason=f"store_buy_{target}", instant=True)
            frame = yield
            frame = yield
            # confirmation / reward listing handling: skip what we know,
            # screenshot what we don't
            for _ in range(6):
                if detect._match(frame, "icons/guild_header.png", 0.75)[0]:
                    break
                pt = find_skip(frame)
                if pt:
                    act.tap(*pt, reason="reward_skip", instant=True)
                else:
                    logger.shot(frame, f"store_confirm_{target}")
                frame = yield
            logger.event("store_buy", item=target, price=price,
                         balance_before=balance,
                         shot=logger.shot(frame, f"store_bought_{target}"))
        else:
            logger.event("store_saving", target=target, price=price,
                         balance=balance)

    for _ in range(4):
        act.tap(*RETURN_STRIP, reason="return_to_game", instant=True)
        frame = yield
        if not detect._match(frame, "icons/guild_header.png", 0.75)[0]:
            return
    logger.event("mission_error", stage="store_return",
                 shot=logger.shot(frame, "store_return_fail"))
