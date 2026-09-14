"""Perk bans - the Home Perks dialog's BAN PERKS tab, driven from a blueprint.

A run type may carry `perk_bans`: fragments of the perks' own wording that
must be banned while that run type plays (a dissonance run wants a different
ban list than the farm - user, 2026-09-14: "modified banned Perks ... a change
of Perks"). The dialog is only reachable from Home, so `ensure_bans` runs where
every coin-kind run is entered (`flows/shard.enter_run`), never during a run.

How the screen is read, and why nothing here is a blind tap:

* Navigation uses the Prepare-recognition mapping templates of the manifest
  routes "Perk settings" (Home -> Perks), "Banned perks" (the BAN PERKS pill)
  and "Perk settings close" (the X). A control that does not match is an
  Abort, and every destination is verified by `bootstrap.recognize_screen`.
* The tab lists BANNED perks first, then an "Available Perks" header, then
  the rest. Rows are read by Windows OCR; a perk's numbers vary with lab
  levels, so matching strips digits and punctuation (`normalize`). A tap lands
  on the OCR'd text of the row it means to toggle, and the next read must show
  that text on the other side of the header or the flow aborts.
* The list scrolls; the flow scrolls down page by page until every wanted
  perk is banned, then back to the top to read the final banned section and
  refuse to close on a mismatch.

The applied set is remembered per account (daystate), so a run type whose
bans are already in place pays nothing at its next restart. `perk_bans: null`
(or absent) never opens the dialog; `perk_bans: []` clears every ban.
"""
import json
import re
import time

from device import act
from device import capture
from runtime import logger
from scheduling import daystate
from vision import detect
import settings

ROUTES = {"open": "Perk settings", "ban_tab": "Banned perks",
          "close": "Perk settings close"}
PERKS_SCREENS = ("perks_hub", "perks_first", "perks_ban", "perks_priority")
LIST_TOP = 640            # below PERKS / the pills / the tab's hint text
LIST_BOTTOM = 2380        # above the dialog's bottom edge
ROW_GAP = 90              # OCR lines closer than this belong to one row
TAP_X = 540
OCR_SCALE = 2
AVAILABLE_HEADER = "availableperks"
MAX_SCROLLS = 12
MAX_TOGGLES = 40
SCROLL_DOWN = (540, 2100, 540, 1000)
SCROLL_UP = (540, 1000, 540, 2100)
SETTLE = 0.8


class PerkAbort(Exception):
    """The Perks dialog was not what the code expected (hard rule 7)."""


def normalize(text) -> str:
    """Letters only, lower case: 'x1.98 coins, but tower max health -70.0%'
    -> 'coinsbuttowermaxhealth'. The numbers are lab-dependent and the OCR
    mangles punctuation; the words are what identify a perk."""
    low = str(text or "").lower()
    low = re.sub(r"x(?=\d)|(?<=\d)x", "", low)      # the x1.98 / 2x multiplier marks
    return re.sub(r"[^a-z]+", "", low)


def wanted_set(bans) -> list[str]:
    return sorted({normalize(b) for b in (bans or ()) if normalize(b)})


def route_template(route_name: str) -> str:
    """The mapping template Prepare recognition cut for a manifest route."""
    from player.bootstrap_layout import manifest
    m = manifest()
    for i, route in enumerate(m["routes"]):
        if route.get("name") == route_name:
            return f"mapping/v{m['version']}/route_{i}.png"
    raise KeyError(route_name)


def templates() -> tuple[str, ...]:
    return tuple(route_template(name) for name in ROUTES.values())


def _key() -> str:
    inst = settings.instance()
    return "perk_bans:" + str(inst.get("account") or settings.CONFIG["active_instance"])


def _match(norm: str, wanted) -> str | None:
    for w in wanted:
        if w and w in norm:
            return w
    return None


def read_rows(frame, read=None):
    """The visible list as rows in reading order plus the y of the
    'Available Perks' header (None when it is off screen). Each row:
    text, norm, y (native, inside the row), x."""
    if read is None:
        from vision import textocr
        read = lambda crop: textocr.read_lines(crop, OCR_SCALE)  # noqa: E731
    crop = frame[LIST_TOP:LIST_BOTTOM, 0:frame.shape[1]]
    lines = []
    for y, x, text in read(crop):
        if not str(text).strip():
            continue
        lines.append((LIST_TOP + int(y / OCR_SCALE), int(x / OCR_SCALE), str(text).strip()))
    lines.sort()
    header_y = None
    rows = []
    for y, x, text in lines:
        if AVAILABLE_HEADER in normalize(text):
            header_y = y
            continue
        if rows and y - rows[-1]["y_last"] < ROW_GAP:
            rows[-1]["text"] += " " + text
            rows[-1]["y_last"] = y
        else:
            rows.append({"text": text, "y_first": y, "y_last": y, "x": x})
    for r in rows:
        r["y"] = (r["y_first"] + r["y_last"]) // 2 + 12
        r["norm"] = normalize(r["text"])
    return rows, header_y


def _banned(rows, header_y, header_seen: bool):
    """Rows above the header are banned. With the header off screen the page
    is either still inside the banned section (never scrolled past the
    header yet) or entirely available (scrolled past it)."""
    for r in rows:
        r["banned"] = (r["y"] < header_y) if header_y is not None else not header_seen
    return rows


def _tap_template(route_name: str, frame, reason: str):
    rel = route_template(route_name)
    hit, score, loc = detect._match(frame, rel, 0.85)
    if not hit:
        raise PerkAbort(f"{route_name!r} control not recognised ({rel}, score {score:.2f})")
    tpl = detect._tpl(rel)
    act.tap(loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2,
            reason=reason, instant=True)


def _wait_screen(names, timeout: float = 6.0):
    from player import bootstrap
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        frame = capture.grab()
        seen = bootstrap.recognize_screen(frame)
        if seen in names:
            return frame
        time.sleep(0.5)
    raise PerkAbort(f"expected {'/'.join(names)}, saw {seen!r}")


def _read(read):
    frame = capture.grab()
    rows, header_y = read_rows(frame, read)
    return frame, rows, header_y


def apply_bans(wanted, *, read=None) -> dict:
    """From Home: make the BAN PERKS tab hold exactly `wanted` (normalized
    fragments). Returns {"banned": [row texts], "toggled": n}. Raises
    PerkAbort the moment the screen stops matching expectations; the caller
    owns the recovery."""
    from interactions import tourney
    wanted = wanted_set(wanted)
    frame = capture.grab()
    if not tourney.on_home(frame):
        raise PerkAbort("perk bans are applied from Home only")
    _tap_template(ROUTES["open"], frame, "perks_open")
    frame = _wait_screen(PERKS_SCREENS)
    _tap_template(ROUTES["ban_tab"], frame, "perks_ban_tab")
    frame = _wait_screen(("perks_ban",))
    rows, header_y = read_rows(frame, read)
    header_seen = header_y is not None
    remaining = set(wanted)
    toggled = 0
    scrolls = 0
    last_page = None
    while True:
        _banned(rows, header_y, header_seen)
        for r in rows:
            if r["banned"]:
                hit = _match(r["norm"], wanted)
                if hit is not None:
                    remaining.discard(hit)
        action = None
        for r in rows:
            hit = _match(r["norm"], wanted)
            if r["banned"] and hit is None:
                action = ("unban", r)
                break
            if not r["banned"] and hit is not None and hit in remaining:
                action = ("ban", r, hit)
                break
        if action is not None:
            if toggled >= MAX_TOGGLES:
                raise PerkAbort("too many perk toggles in one visit")
            row = action[1]
            act.tap(TAP_X, row["y"], reason=f"perk_{action[0]}", instant=True)
            time.sleep(SETTLE)
            frame, rows, header_y = _read(read)
            header_seen = header_seen or header_y is not None
            _banned(rows, header_y, header_seen)
            after = next((r for r in rows if r["norm"] == row["norm"]), None)
            if after is not None and after["banned"] == (action[0] == "unban"):
                logger.shot(frame, "perk_toggle_unconfirmed")
                raise PerkAbort(f"perk row did not toggle: {row['text']!r}")
            toggled += 1
            logger.event("perk_ban_toggled", action=action[0], text=row["text"])
            continue
        if not remaining:
            break
        page = [r["norm"] for r in rows]
        if page == last_page or scrolls >= MAX_SCROLLS:
            raise PerkAbort(f"perks not found to ban: {sorted(remaining)}")
        last_page = page
        act.swipe(*SCROLL_DOWN, 350, reason="perk_list_scroll")
        time.sleep(SETTLE)
        scrolls += 1
        frame, rows, header_y = _read(read)
        header_seen = header_seen or header_y is not None
    # back to the top: the banned section is the proof
    if scrolls:
        last = None
        for _ in range(scrolls + 2):
            act.swipe(*SCROLL_UP, 350, reason="perk_list_scroll_top")
            time.sleep(SETTLE)
            frame, rows, header_y = _read(read)
            page = [r["norm"] for r in rows]
            if page == last:
                break
            last = page
    if header_y is None:
        raise PerkAbort("the Available Perks header is not on screen at the top of the list")
    banned = [r for r in rows if r["y"] < header_y]
    unmatched = [r["text"] for r in banned if _match(r["norm"], wanted) is None]
    missing = [w for w in wanted if not any(w in r["norm"] for r in banned)]
    if unmatched or missing or len(banned) != len(wanted):
        logger.shot(frame, "perk_bans_mismatch")
        raise PerkAbort(f"banned section does not match: extra={unmatched} missing={missing} "
                        f"banned={len(banned)} wanted={len(wanted)}")
    _tap_template(ROUTES["close"], frame, "perks_close")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if tourney.on_home(capture.grab()):
            return {"banned": [r["text"] for r in banned], "toggled": toggled}
        time.sleep(0.4)
    raise PerkAbort("Home did not return after closing the Perks dialog")


def ensure_bans(bans) -> bool:
    """The entry-point hook: apply the blueprint's bans unless this app
    already applied that exact set for this account. Degrades - never
    blocks the run: a failure is logged with a screenshot, the dialog is
    closed by its own X when it is still recognised, and the caller goes on
    to enter the run (wrong bans are a worse run, a dead farm is a lost
    night - same ruling as loadout.apply's module failures)."""
    if bans is None:
        return False
    wanted = wanted_set(bans)
    token = json.dumps(wanted)
    if daystate.get_raw(_key()) == token:
        return False
    from interactions import tourney
    try:
        result = apply_bans(wanted)
    except (PerkAbort, tourney.Abort, act.TapRefused, capture.CaptureError) as e:
        frame = capture.grab()
        logger.event("perk_bans_failed", error=str(e), wanted=wanted,
                     shot=logger.shot(frame, "perk_bans_failed"))
        try:
            _tap_template(ROUTES["close"], frame, "perks_close_after_failure")
        except (PerkAbort, act.TapRefused, detect.TemplateMissing):
            pass
        return False
    daystate.set_raw(_key(), token)
    logger.event("perk_bans_applied", wanted=wanted, **result)
    return True
