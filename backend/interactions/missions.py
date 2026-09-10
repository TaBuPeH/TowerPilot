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


def find_tile(frame, tpl_rel: str, fallback=None) -> tuple[int, int] | None:
    """Identify a currently visible menu icon. Legacy coordinates are ignored.

    The menu has no stable row or ordering. Missing or ambiguous artwork is
    unavailable, never permission to click its old position.
    """
    from player.bootstrap_layout import manifest
    from player.clicker import locate
    if not detect.side_menu_open(frame):
        return None
    try:
        tpl = detect._tpl(tpl_rel)
    except detect.TemplateMissing:
        from vision.installed_art import _images
        from player import accounts, asset_verify
        import settings
        definition = manifest().get('asset_bindings', {}).get(tpl_rel)
        if definition is None:
            return None
        folder = str(accounts.calibration_dir(settings.ROOT, settings.CONFIG))
        for image in _images(folder, tpl_rel):
            hit = asset_verify.locate(image, frame, definition['search'])
            if not hit:
                hit = asset_verify.locate_silhouette(image, frame, definition['search'], white=True)
            if not hit and definition.get('outline_shape'):
                hit = asset_verify.locate_outline(image, frame, definition['search'])
            if hit:
                x,y,w,h = hit['rect']
                return x+w//2, y+h//2
        return None
    search = manifest()['side_menu']['column']
    # Notification counters overlap the tile border, not its central glyph.
    # Keep the native center fixed and match the glyph inside that border.
    inset = int(min(tpl.shape[:2]) * .18)
    glyph = tpl[inset:-inset, inset:-inset] if inset else tpl
    hit = locate(frame, glyph, search, search=search)
    if not hit['ok']:
        return None
    x, y, w, h = hit['rect']
    return x + w//2, y + h//2


def _tile_badge(frame, tpl_rel, fallback, lo, hi) -> bool:
    """Check the number badge at the located tile's top-left corner."""
    point = find_tile(frame, tpl_rel)
    if point is None:
        return False
    cx, cy = point
    cell = frame[max(0, cy - 65):cy - 15, max(0, cx - 65):max(0,cx - 15)]
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    if isinstance(lo, tuple):
        mask = cv2.inRange(hsv, lo, hi)
    else:                                    # list of (lo, hi) band pairs
        mask = None
        for l, h in zip(lo, hi):
            m = cv2.inRange(hsv, l, h)
            mask = m if mask is None else (mask | m)
    return bool((mask > 0).mean() > 0.01)


def quests_badge(frame) -> bool:
    """Red number badge on the quests tile = daily rewards waiting."""
    return _tile_badge(frame, "icons/tile_quests.png", QUESTS_TILE,
                       [(0, 150, 120), (170, 150, 120)],
                       [(10, 255, 255), (180, 255, 255)])


def guild_badge(frame) -> bool:
    """Purple number badge on the guild banner tile = guild reward waiting."""
    return _tile_badge(frame, "icons/tile_guild.png", GUILD_TILE,
                       (115, 80, 120), (140, 255, 255))


def events_badge(frame) -> bool:
    return _tile_badge(frame, 'icons/tile_events.png', None,
                       (115, 80, 120), (140, 255, 255))


def missions_screen(frame) -> bool:
    hit, _, _ = detect._match(frame, "icons/daily_missions.png", 0.75)
    return hit


def find_claim(frame):
    if not missions_screen(frame):
        return None
    from interactions.event_rewards import claim_buttons
    points = claim_buttons(frame)
    return points[0] if points else None


def find_skip(frame):
    """SKIP pill on the reward listing: cyan-bordered button, upper right.

    TEMPLATE ONLY. This used to fall back to a structural search - any
    cyan-bordered pill of roughly the right shape in the upper right - and
    then SAVE whatever it found as the template. Both halves were wrong:

      * the CARDS screen puts five cyan preset tabs (labelled however the
        player named them) at exactly that size, in exactly that box, so the
        search matched them;
      * harvesting on match let a single false positive overwrite the
        detector's own ground truth - reward_skip.png became a picture of one
        of the user's card preset tabs, which would have poisoned every later run.

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
    try:
        lock = cv2.cvtColor(detect._tpl("icons/chest_lock.png"), cv2.COLOR_BGR2GRAY)
    except detect.TemplateMissing:
        logger.event("mission_chests_skipped", reason="missing chest lock recognition")
        return []
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
    _tap(x, y, reason=reason, instant=True)


def guild_flow():
    """Collect the guild contribution reward. Per user: navigation is by
    FIXED AREAS only (guild tile -> Members tab -> the 4 milestone box areas
    -> SKIP -> return); vision is used solely for screen-state checks."""
    frame = yield
    point = find_tile(frame, "icons/tile_guild.png")
    if point is None:
        logger.event('mission_error', stage='guild_icon_unavailable')
        return
    _tap(*point, "guild_open")
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
    for x, cy in before:
        if (x, cy) not in guild_claimables(frame):
            continue
        _guild_tap(x, 708, "guild_reward")
        frame = yield
        for _ in range(6):
            hit, _, _ = detect._match(frame, "icons/guild_header.png", 0.75)
            if hit:
                break
            from interactions.event_rewards import reward_dismiss
            pt = find_skip(frame) or reward_dismiss(frame)
            if not pt:
                frame = yield
                continue
            _guild_tap(*pt, "reward_skip")
            frame = yield
        if not detect._match(frame, "icons/guild_header.png", .75)[0]:
            logger.event('mission_error',stage='guild_reward_popup_not_dismissed')
            return
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
    point = find_tile(frame, "icons/tile_quests.png")
    if point is None:
        logger.event('mission_error', stage='quests_icon_unavailable')
        return
    _tap(*point, "quests_open",
         instant=True)
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
    pages = 0
    previous_page = None
    for _ in range(MAX_CLAIMS * 3 + 6):
        if not missions_screen(frame):
            # a reward popup may cover the screen - skip it
            pt = find_skip(frame)
            if pt:
                _tap(*pt, "reward_skip")
            frame = yield
            continue
        pt = find_claim(frame)
        if claimed >= MAX_CLAIMS:
            break
        if pt is None:
            # Finished quests can be below the fold. Move only the mission
            # list, keep the weekly reward track fixed, and stop at a repeated
            # page or a bounded six scrolls.
            page = cv2.resize(frame[650:2350,40:1040], (100,170))
            if pages >= 6 or (previous_page is not None and
                    np.abs(page.astype(float)-previous_page.astype(float)).mean()<1):
                break
            previous_page = page
            act.swipe(540,2100,540,950,400,reason="mission list next page")
            pages += 1
            frame = yield
            frame = yield
            continue
        previous_page = None
        _tap(*pt, "quest_claim", instant=True)
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
    """Daily free-gems claim: locate the free card, verify CLAIM REWARDS,
    collect and return. Scheduled around 01:00 UTC, with ten-minute jitter.

    on_success() is called only after the store shows the claim's cooldown.
    The orchestrator used to mark the day claimed before starting this flow, so a
    flow that bailed (menu closed, screen never appeared) still burned the
    day's claim."""
    frame = yield
    point=find_tile(frame, "icons/tile_cart.png", GEM_STORE_TILE)
    if point is None:
        logger.event('mission_error',stage='gem_store_icon_unavailable')
        return
    _tap(*point, "gem_store_open")
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

    claimed = False
    for attempt in range(3):
        hit, _, loc = detect._match(frame, "icons/free_gems.png", 0.75)
        if hit:
            tpl = detect._tpl("icons/free_gems.png")
            cx = loc[0] + tpl.shape[1] // 2 + FREE_BTN_OFFSET[0]
            cy = loc[1] + tpl.shape[0] // 2 + FREE_BTN_OFFSET[1]
            from vision import textocr
            patch=frame[max(0,cy-75):cy+75,max(0,cx-140):cx+140]
            words=' '.join(t.strip().upper() for _,_,t in textocr.read_lines(patch,2))
            if words != 'CLAIM REWARDS':
                logger.event('free_gems_unavailable',reason='Free card is on cooldown or claim not verified')
                break
            _tap(cx, cy, "free_gems_claim")
            frame = yield
            frame = yield
            from interactions.event_rewards import reward_dismiss
            for _ in range(8):
                if detect._match(frame,'icons/premium_store.png',.75)[0]: break
                pt=find_skip(frame) or reward_dismiss(frame)
                if not pt: break
                _tap(*pt,'free_gems_reward_collect')
                frame=yield
                frame = yield
            patch=frame[max(0,cy-75):cy+75,max(0,cx-140):cx+140]
            words=' '.join(t.strip().upper() for _,_,t in textocr.read_lines(patch,2))
            # Count only the observed cooldown on the free card, not a tap attempt.
            import re
            claimed=bool(detect._match(frame,'icons/premium_store.png',.75)[0]
                         and re.search(r'\d+\s*[DHMS]',words))
            if claimed and on_success: on_success()
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
