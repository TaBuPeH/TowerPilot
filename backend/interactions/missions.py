"""Daily-mission / weekly-chest reward collection.

Flow (user spec): the in-run side menu stays open; a RED badge on the quests
tile means rewards are ready. Within a random 1-10 min the autopilot visits:
quests tile -> DAILY MISSIONS screen -> CLAIM every finished quest -> tap any
claimable weekly chest (every 5 missions, thresholds 5..35) -> SKIP the
reward listing popup -> "Tap To Return To Game".

Non-blocking like Shopper: a generator advanced one action per observed
frame via step(frame). The battle keeps running while menus are open, so the
flow is kept short and every tap is preceded by a screen-state check.
"""
import random
import time

import cv2
import numpy as np

from settings import CONFIG, ROOT, input_args, run_hidden
from device import capture
from vision import detect
from device import act
from runtime import logger

last_guild_claims = 0           # milestones claimed by the last guild_flow

QUESTS_TILE = (915, 180)        # fallback: checkbox tile row on TEST-1
GUILD_TILE = (908, 486)         # fallback: banner tile row on TEST-1
MEMBERS_TAB = (152, 314)        # Members tab on the guild screen
RETURN_STRIP = (540, 2455)      # "Tap To Return To Game" bottom strip
CHEST_BAND = (280, 470)         # y-range of the weekly chest track
GUILD_BAND = (630, 790)         # y-range of the guild contribution track
GUILD_SLOTS = [141, 374, 693, 1014]   # fixed milestone box centers (100..750)
MAX_CLAIMS = 8                  # standing-quest cap (2 new / 8h, max 8)


def bail(frame, reason: str):
    """Recovery tap when a flow's expected screen never appeared. Only taps
    the return strip when we are actually OFF the battle screen - in battle
    that strip is the shop tab area and must not be touched."""
    from vision import wave_reader
    if wave_reader.read_wave(frame) is None:
        act.tap(*RETURN_STRIP, reason=f"bail_{reason}", instant=True)


def find_tile(frame, tpl_rel: str, fallback) -> tuple[int, int]:
    """Locate a side-menu tile by its ICON (both emulators are clones - the
    icons are identical but the tile ROWS differ per account/tier, so fixed
    rows are wrong; the icon is the ground truth). Search is limited to the
    open menu's tile column. Falls back to the given fixed point on a miss."""
    col = frame[0:820, 840:1080]
    hit, _, loc = detect._match(col, tpl_rel, 0.70)
    if not hit:
        return fallback
    tpl = detect._tpl(tpl_rel)
    return (840 + loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)


def _tile_badge(frame, tpl_rel, fallback, lo, hi) -> bool:
    """Badge check around a located tile: the number badge sits on the tile's
    top-right corner, so scan the tile cell plus a margin."""
    cx, cy = find_tile(frame, tpl_rel, fallback)
    cell = frame[max(0, cy - 55):cy + 55, max(0, cx - 60):min(1080, cx + 70)]
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    if isinstance(lo, tuple):
        mask = cv2.inRange(hsv, lo, hi)
    else:                                    # list of (lo, hi) band pairs
        mask = None
        for l, h in zip(lo, hi):
            m = cv2.inRange(hsv, l, h)
            mask = m if mask is None else (mask | m)
    return (mask > 0).mean() > 0.01


def quests_badge(frame) -> bool:
    """Red number badge on the quests tile = daily rewards waiting."""
    return _tile_badge(frame, "icons/tile_quests.png", QUESTS_TILE,
                       [(0, 150, 120), (170, 150, 120)],
                       [(10, 255, 255), (180, 255, 255)])


def guild_badge(frame) -> bool:
    """Purple number badge on the guild banner tile = guild reward waiting."""
    return _tile_badge(frame, "icons/tile_guild.png", GUILD_TILE,
                       (115, 80, 120), (140, 255, 255))


def missions_screen(frame) -> bool:
    hit, _, _ = detect._match(frame, "icons/daily_missions.png", 0.75)
    return hit


def find_claim(frame):
    hit, _, loc = detect._match(frame, "buttons/quest_claim.png", 0.75)
    if not hit:
        return None
    tpl = detect._tpl("buttons/quest_claim.png")
    return (loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)


def find_skip(frame):
    """SKIP pill on the reward listing: cyan-bordered button, upper right.

    TEMPLATE ONLY. This used to fall back to a structural search - any
    cyan-bordered pill of roughly the right shape in the upper right - and
    then SAVE whatever it found as the template. Both halves were wrong:

      * the CARDS screen puts five cyan preset tabs ("Main Farm", "Att Farm",
        "18v300", ...) at exactly that size, in exactly that box, so the
        search matched them;
      * harvesting on match let a single false positive overwrite the
        detector's own ground truth - reward_skip.png became a picture of the
        user's "18v300" preset tab, which would have poisoned every later run.

    A detector must never rewrite the thing it is measured against. If the
    template is missing, this returns None and the caller does nothing, which
    is the correct failure.
    """
    hit, _, loc = detect._match(frame, "buttons/reward_skip.png", 0.75)
    if not hit:
        return None
    tpl = detect._tpl("buttons/reward_skip.png")
    return (loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)


def claimable_chests(frame):
    """Chest tiles on the weekly track that are neither locked (padlock) nor
    already claimed (big green check) -> tap targets. Track may be slid; only
    the visible window is scanned (the claimable chest sits at the progress
    edge, which the game keeps in view)."""
    band = frame[CHEST_BAND[0]:CHEST_BAND[1], 0:1080]
    lock = cv2.cvtColor(detect._tpl("icons/chest_lock.png"), cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(gray, lock, cv2.TM_CCOEFF_NORMED)
    locked_x = []
    r = res.copy()
    while True:
        _, mx, _, ml = cv2.minMaxLoc(r)
        if mx < 0.75:
            break
        locked_x.append(ml[0] + lock.shape[1] // 2)
        x0 = max(0, ml[0] - 60)
        r[:, x0:ml[0] + 60] = 0
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (50, 120, 120), (75, 255, 255))
    # A claimable chest GLOWS (bright magenta/white); claimed ones are grey
    # with a green check and locked ones are grey with a padlock.
    #
    # The old sliding-window version scanned for "bright and not green" in
    # 40px steps and kept the FIRST hit of each cluster, which returned the
    # left and right EDGES of the glow (650 and 810 for a chest centred at
    # 736) - both taps landed between chests and claimed nothing. Use blob
    # centroids instead.
    bright = ((hsv[..., 2] > 190) & (green == 0)).astype(np.uint8)
    # the horizontal progress bar spans the whole width and would fuse every
    # chest into one blob - drop rows that are bright nearly all the way across
    bright[bright.mean(axis=1) > 0.35, :] = 0
    bright = cv2.morphologyEx(bright * 255, cv2.MORPH_CLOSE,
                              np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    # the bar cuts a chest into stacked halves - cluster blobs by x, then take
    # the area-weighted centroid of each cluster
    clusters: list[list[float]] = []          # [area, sum(a*x), sum(a*y)]
    for c in contours:
        area = cv2.contourArea(c)
        if area < 250:
            continue
        m = cv2.moments(c)
        if not m["m00"]:
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        for cl in clusters:
            if abs(cl[1] / cl[0] - cx) < 80:
                cl[0] += area
                cl[1] += area * cx
                cl[2] += area * cy
                break
        else:
            clusters.append([area, area * cx, area * cy])
    targets = []
    for area, sx, sy in clusters:
        if area < 1500:            # residual glow around a claimed chest
            continue
        x = int(sx / area)
        if any(abs(x - lx) < 90 for lx in locked_x):
            continue               # padlocked slot
        # a CLAIMED chest still glows, so test its cell for the green check
        # (excluding green from the mask alone is not enough)
        cell = band[:, max(0, x - 70):min(1080, x + 70)]
        if (cv2.inRange(cv2.cvtColor(cell, cv2.COLOR_BGR2HSV),
                        (50, 120, 120), (75, 255, 255)) > 0).mean() > 0.015:
            continue
        targets.append((x, CHEST_BAND[0] + int(sy / area)))
    return sorted(targets)


def guild_claimables(frame):
    """Claimable milestone boxes on the guild contribution track: magenta
    glowing boxes WITHOUT a padlock (locked) or green check (claimed)."""
    # milestones sit at FIXED positions with fixed icons (user-confirmed):
    # 100 / 250 / 500 / 750 boxes, centers y~708
    lock = cv2.cvtColor(detect._tpl("icons/chest_lock.png"), cv2.COLOR_BGR2GRAY)
    targets = []
    for x in GUILD_SLOTS:
        cell = frame[650:770, max(0, x - 70):x + 70]
        hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
        if (cv2.inRange(hsv, (50, 120, 120), (75, 255, 255)) > 0).mean() > 0.02:
            continue                           # green check = already claimed
        cg = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        if cv2.matchTemplate(cg, lock, cv2.TM_CCOEFF_NORMED).max() > 0.7:
            continue                           # padlock = locked
        magenta = (cv2.inRange(hsv, (140, 80, 120), (175, 255, 255)) > 0).mean()
        if magenta > 0.02:                     # box actually rendered here
            targets.append((x, 708))
    return targets


class Mission:
    """Reward-collection runner: holds ONE active flow generator, advanced
    one action per step(frame). start() takes a generator function."""

    def __init__(self):
        self._gen = None

    @property
    def active(self) -> bool:
        return self._gen is not None

    def start(self, flow):
        if self._gen is None:
            self._gen = flow()
            next(self._gen)

    def step(self, frame):
        if self._gen is None:
            return
        try:
            self._gen.send(frame)
        except StopIteration:
            self._gen = None

    def abort(self):
        self._gen = None


SKIP_AREA = (897, 375)          # SKIP button on the reward listing (fixed)
LEAVE_ZONE = (850, 430, 1080, 560)   # "Leave" guild button - NEVER tap here


def _tap(x, y, reason, instant=True):
    """Mission-flow tap that is actually LOGGED. Flow taps used to call
    act.tap directly with no logger.event, so after a failed reward run there
    was no record of what had been clicked - impossible to debug."""
    try:
        ev = act.tap(x, y, reason=reason, instant=instant)
        logger.event("flow_tap", **ev)
    except act.TapRefused as e:
        logger.event("tap_refused", button=reason, error=str(e))


def _guild_tap(x, y, reason):
    """Fixed-AREA tap for guild screens (plain instant tap - the milestone
    widgets ignore held swipe-taps). Hard-blocks the Leave button zone."""
    if LEAVE_ZONE[0] <= x <= LEAVE_ZONE[2] and LEAVE_ZONE[1] <= y <= LEAVE_ZONE[3]:
        logger.event("tap_blocked", reason=reason, x=x, y=y)
        return
    act.tap(x, y, reason=reason, instant=True)


def guild_flow():
    """Collect the guild contribution reward. Per user: navigation is by
    FIXED AREAS only (guild tile -> Members tab -> the 4 milestone box areas
    -> SKIP -> return); vision is used solely for screen-state checks."""
    frame = yield
    _tap(*find_tile(frame, "icons/tile_guild.png", GUILD_TILE), "guild_open")
    frame = yield
    opened = False
    for _ in range(4):
        hit, _, _ = detect._match(frame, "icons/guild_header.png", 0.75)
        if hit:
            opened = True
            break
        frame = yield
    if not opened:
        logger.event("mission_error", stage="guild_open",
                     shot=logger.shot(frame, "guild_open_fail"))
        bail(frame, "guild_open")
        return

    _guild_tap(*MEMBERS_TAB, "guild_members_tab")
    frame = yield
    frame = yield                            # let the tab content render

    # tap each milestone box AREA (claimed/locked boxes ignore the tap);
    # after each, clear any reward listing via SKIP (template, else area)
    before = guild_claimables(frame)
    for x in GUILD_SLOTS:
        _guild_tap(x, 708, "guild_reward")
        frame = yield
        for _ in range(6):
            hit, _, _ = detect._match(frame, "icons/guild_header.png", 0.75)
            if hit:
                break
            pt = find_skip(frame) or SKIP_AREA
            _guild_tap(*pt, "reward_skip")
            frame = yield
    after = guild_claimables(frame)
    global last_guild_claims
    last_guild_claims = max(0, len(before) - len(after))
    logger.event("guild_done", claimable_before=len(before),
                 claimable_after=len(after),
                 shot=logger.shot(frame, "guild_done"))

    for _ in range(4):
        _tap(*RETURN_STRIP, "return_to_game")
        frame = yield
        hit, _, _ = detect._match(frame, "icons/guild_header.png", 0.75)
        if not hit:
            return
    logger.event("mission_error", stage="guild_return",
                 shot=logger.shot(frame, "guild_return_fail"))


def quest_flow():
    frame = yield
    _tap(*find_tile(frame, "icons/tile_quests.png", QUESTS_TILE), "quests_open",
         instant=False)
    frame = yield
    opened = False
    for _ in range(4):
        if missions_screen(frame):
            opened = True
            break
        frame = yield
    if not opened:
        logger.event("mission_error", stage="open",
                     shot=logger.shot(frame, "mission_open_fail"))
        bail(frame, "quests_open")
        return

    # ---- claim every finished quest
    claimed = 0
    for _ in range(MAX_CLAIMS * 3):
        if not missions_screen(frame):
            # a reward popup may cover the screen - skip it
            pt = find_skip(frame)
            if pt:
                _tap(*pt, "reward_skip")
            frame = yield
            continue
        pt = find_claim(frame)
        if pt is None or claimed >= MAX_CLAIMS:
            break
        _tap(*pt, "quest_claim", instant=False)
        claimed += 1
        frame = yield
        frame = yield          # let the card disappear / rewards land

    # ---- weekly chests: tap anything unlocked-and-unclaimed
    chests = 0
    for cx, cy in claimable_chests(frame)[:3]:
        _tap(cx, cy, "weekly_chest")
        chests += 1
        frame = yield
        frame = yield                  # the listing takes a moment to render
        # reward listing popup -> SKIP it. The popup can be several pages
        # (1/4 ...), so keep skipping until the missions screen is back.
        for _ in range(10):
            if missions_screen(frame):
                break
            _tap(*(find_skip(frame) or SKIP_AREA), "reward_skip")
            frame = yield

    logger.event("mission_done", claimed=claimed, chests=chests,
                 shot=logger.shot(frame, "mission_done"))

    # ---- back to the battle.
    # NOT "not missions_screen": a reward listing is not the missions screen
    # either, so that test used to exit the flow with the popup still up -
    # and the orchestrator then refuses to touch a non-battle screen, so it sat
    # there. Exit only once the WAVE COUNTER is readable again, dismissing
    # any listing on the way out.
    from vision import wave_reader
    for _ in range(10):
        if wave_reader.read_wave(frame) is not None:
            return                     # really back in the battle
        if not missions_screen(frame):
            pt = find_skip(frame)
            if pt:                     # a reward listing is covering us
                _tap(*pt, "reward_skip")
                frame = yield
                continue
        _tap(*RETURN_STRIP, "return_to_game")
        frame = yield
    logger.event("mission_error", stage="return",
                 shot=logger.shot(frame, "mission_return_fail"))


GEM_STORE_TILE = (910, 65)      # fallback: gold cart tile (row 1, both)
FREE_BTN_OFFSET = (0, 138)      # claim button center relative to FREE label


def free_gems_flow(on_success=None):
    """Daily free-gems claim: cart tile -> premium STORE -> one screen down
    -> tap the x15 FREE card's button (a no-op if still on cooldown) ->
    return. Runs once per day around 4-5 AM (orchestrator schedules it).

    on_success() is called ONLY after the claim button is actually tapped.
    The orchestrator used to mark the day claimed before starting this flow, so a
    flow that bailed (menu closed, screen never appeared) still burned the
    day's claim."""
    frame = yield
    _tap(*find_tile(frame, "icons/tile_cart.png", GEM_STORE_TILE), "gem_store_open")
    frame = yield
    opened = False
    # 4 -> 10 frames (2026-08-30): the 03:00 failure shot scored 1.0 on the
    # store marker - the store WAS open, it just rendered after the 4th
    # frame (00:00 UTC is the store's daily reset; content loads slowest
    # exactly then). The verification is right, the patience was not.
    for _ in range(10):
        if detect._match(frame, "icons/premium_store.png", 0.75)[0]:
            opened = True
            break
        frame = yield
    if not opened:
        logger.event("mission_error", stage="gem_store_open",
                     shot=logger.shot(frame, "gem_store_open_fail"))
        bail(frame, "gem_store_open")
        return

    import subprocess
    from settings import adb_args
    claimed = False
    for attempt in range(3):
        hit, _, loc = detect._match(frame, "icons/free_gems.png", 0.75)
        if hit:
            tpl = detect._tpl("icons/free_gems.png")
            cx = loc[0] + tpl.shape[1] // 2 + FREE_BTN_OFFSET[0]
            cy = loc[1] + tpl.shape[0] // 2 + FREE_BTN_OFFSET[1]
            _tap(cx, cy, "free_gems_claim")
            claimed = True
            if on_success:
                on_success()
            frame = yield
            frame = yield
            pt = find_skip(frame)      # just in case a listing pops
            if pt:
                _tap(*pt, "reward_skip")
                frame = yield
            break
        # not visible yet: scroll one stride down and look again
        act.swipe(540, 1800, 540, 900, 400, reason="gem store scroll")
        frame = yield
    logger.event("free_gems", clicked=claimed,
                 shot=logger.shot(frame, "free_gems"))

    for _ in range(4):
        _tap(*RETURN_STRIP, "return_to_game")
        frame = yield
        if not detect._match(frame, "icons/premium_store.png", 0.75)[0]:
            return
    logger.event("mission_error", stage="gem_store_return",
                 shot=logger.shot(frame, "gem_store_return_fail"))
