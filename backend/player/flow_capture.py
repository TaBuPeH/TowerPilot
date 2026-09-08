"""Consented, human-started capture of the controls that only exist mid-action.

The starter scan (player/bootstrap.py) walks Home and the still menus and cuts
everything it can while touching nothing that changes state. What it cannot
reach that way is every control that only appears DURING an action: the intro
sprint dialog, the end-of-run exit dialogs and GAME STATS screen, the reward
skip, and the event/store/guild claim controls a menu walk surfaces. Those are
this module's job.

Doctrine (why this is allowed to do what the rest of setup refuses):

  * It is HUMAN-STARTED and CONSENTED. It runs only inside the starter scan,
    only when navigation is allowed, and only after the dashboard's popup has
    told the user it will start and cancel a battle and click around the
    profile. The scan never does this on its own.
  * It is TEMPLATE-FREE. Every control is found by its on-screen TEXT
    (vision.textocr, via player.battle_capture) and the number font
    (contour segmentation). That is the whole point: these runtime templates
    do not exist yet, so nothing here may depend on them.
  * It NEVER TOUCHES A TOURNAMENT. It starts a NORMAL run from the Home BATTLE
    button, never the tournament tile; it refuses the moment in_tournament
    reads true; and a tournament ticket auto-starts an escalating-gem run, so
    that boundary is absolute (CLAUDE.md rules 2-3).
  * It ENDS ONLY THE RUN IT ITSELF STARTED. It will not start a battle over a
    run already on screen, and it will not end someone else's run - exactly the
    fence tourney.ensure_home / shard.abandon_run draw. A normal run costs
    nothing to start or surrender; only tournament entries cost gems.
  * It ABORTS INTO SAFETY. An unrecognised screen stops the flow and logs a
    shot; it never blind-taps. Each flow is isolated - one failing leaves the
    others and the rest of the scan intact - and each returns to Home.

Everything it writes is the account's own local pixels, declared writable in the
bootstrap manifest's dynamic_targets and gated through calibrate.write_template
like every other captured template; nothing ships (CLAUDE.md rules 6, 8).
"""
import time

import cv2
import numpy as np


# ---------------------------------------------------------------- pure helpers
# Kept free of I/O so the decision points a wrong reading could make dangerous
# are unit-testable without a device.

def _norm(text) -> str:
    return " ".join(str(text or "").upper().split())


def exit_dialog_kind(texts) -> str | None:
    """Which confirm dialog is up, from the OCR lines on it.

    'surrender' - the "EXIT BATTLE / What would you like to do?" dialog with
                  Surrender and Go Home (answer Surrender: Go Home only HIDES
                  the run, it does not end it - tourney.end_round's rule).
    'end_round' - the plain "END ROUND?" Yes/No confirm.
    None        - neither is unambiguously on screen; caller must not tap.
    """
    joined = " || ".join(_norm(t) for t in texts)
    if "SURRENDER" in joined or "GO HOME" in joined or "WHAT WOULD YOU LIKE" in joined:
        return "surrender"
    # The END ROUND dialog's Yes/No buttons are the same stylised text Windows
    # OCR misses, so key on its QUESTION ("Are you sure you want to end the
    # round?"), not the buttons - the earlier "END ROUND"+Yes/No form still works.
    if (("ARE YOU SURE" in joined and "ROUND" in joined)
            or ("END ROUND" in joined and ("YES" in joined or "NO" in joined))):
        return "end_round"
    return None


def has_text(texts, needle) -> bool:
    n = _norm(needle)
    return any(n in _norm(t) for t in texts)


def exit_label(texts) -> str | None:
    """The in-run side menu's exit control, from the OCR lines: 'END ROUND'
    (high tiers) or 'EXIT BATTLE' (low tiers), or None when the menu is not
    open. Its presence IS the menu-open signal - so a caller can tell an open
    menu from a closed one without a template, and never taps the toggle on an
    already-open menu (which would close it: tapping an open control shuts it)."""
    if has_text(texts, "END ROUND"):
        return "END ROUND"
    if has_text(texts, "EXIT BATTLE"):
        return "EXIT BATTLE"
    return None


def can_start_battle(*, on_home: bool, wave, side_menu: bool,
                     game_stats: bool, tournament: bool) -> tuple[bool, str]:
    """The gate before starting a calibration battle. Returns (ok, why-not).

    Refuses over any run/aftermath already on screen (never start a battle on
    top of someone's run, never end a run we did not start) and over a
    tournament (never, ever). Only a clean Home passes.
    """
    if tournament:
        return False, "a tournament is on screen - never touched by setup"
    if wave is not None:
        return False, "a run is already in progress - setup never ends a run it did not start"
    if side_menu or game_stats:
        return False, "a run's menu or stats screen is up - not starting a battle over it"
    if not on_home:
        return False, "not on the Home screen"
    return True, ""


# ---------------------------------------------------------------- I/O plumbing

def _read(frame, scale: float = 2.0):
    from vision import textocr
    return textocr.read_lines(frame, scale)


def _lines_text(frame, scale: float = 2.0):
    return [t for _y, _x, t in _read(frame, scale)]


class _Flow:
    """Thin adapter over the bootstrap Scanner: its device handles, its
    progress/skip bookkeeping, its Calibration for verified writes."""

    def __init__(self, scanner):
        self.s = scanner
        self.cal = scanner.cal

    # -- device
    def grab(self):
        return self.s.grab()

    def tap(self, x, y, reason):
        self.s.check_stop()
        return self.s.tap(int(x), int(y), reason=f"flow: {reason}")

    def pause(self, seconds):
        self.s.pause(seconds)

    def progress(self, message, **extra):
        self.s.progress(message, **extra)

    def skip(self, target, reason):
        self.s.skipped.append({"target": target, "reason": reason})
        from runtime import logger
        logger.event("flow_skip", target=target, reason=reason)

    # -- guards
    def frame(self):
        return self.grab()

    def wave(self, frame):
        from vision import wave_reader
        try:
            return wave_reader.read_wave(frame)
        except RuntimeError:
            # No digit font yet (a fresh account): the wave is simply
            # UNREADABLE here, not an error. Capture must proceed - the digits
            # are cut from the live counter by capture_hud_digits - rather than
            # let the whole battle stage abort before it can bootstrap them.
            return None

    def on_home(self, frame):
        from player.bootstrap import screen_matches
        return screen_matches(frame, _read(frame, 1), "home")

    def side_menu(self, frame):
        from vision import detect
        return detect.side_menu_open(frame)

    def in_tournament(self, frame):
        # Template-based (tourney/in_tournament.png). Missing during bring-up ->
        # reads False; the can_start_battle gate and "only end our own run"
        # rule are what actually keep this off tournaments, so a blind guard
        # here is defence in depth, not the load-bearing wall.
        from interactions import tourney
        return tourney.in_tournament(frame)

    def game_stats(self, frame):
        return has_text(_lines_text(frame), "GAME STATS")

    # -- capture
    def locate(self, frame, region, text, *, scale=2, cutoff=0.72):
        from player import battle_capture as bc
        return bc.locate_text(frame, region, text,
                              lambda c, s: _read(c, s), scale=scale, cutoff=cutoff)

    def capture(self, rel, text, region, size, *, name=None, anchor=(0.12, 0.30),
                frame=None, extra=None):
        """Find `text` in `region`, cut a `size` template around it, verify and
        write it through Calibration.cut (bootstrap allowlist). Returns the
        cut entry dict, or None when the text is not on screen / not verified."""
        from player import battle_capture as bc
        frame = self.grab() if frame is None else frame
        crop = bc.capture_by_text(frame, text, region, size,
                                  lambda c, s: _read(c, s), anchor=anchor)
        if crop is None:
            self.skip(rel, f"'{text}' not visible where expected")
            return None
        entry = self.cal.cut("bootstrap", rel, crop, frame, name or text,
                             dict(extra or {}, source="flow_ocr"))
        if not entry.get("verified"):
            self.skip(rel, entry.get("reason") or "capture not verified")
            return None
        self.progress(f"Captured {rel}")
        return entry

    def cut_crop(self, rel, crop, frame, name, extra=None, *, unique=True):
        """Verify+write an already-cut crop (a glyph/button found by position,
        not text). `unique=False` for look-alike sibling buttons (Yes/No, MORE
        STATS/PERKS) whose identity is their side of the row. Returns entry|None."""
        if crop is None or getattr(crop, "size", 0) == 0:
            self.skip(rel, "empty crop")
            return None
        entry = self.cal.cut("bootstrap", rel, crop, frame, name,
                             dict(extra or {}, source="flow_position"), unique=unique)
        if not entry.get("verified"):
            self.skip(rel, entry.get("reason") or "capture not verified")
            return None
        self.progress(f"Captured {rel}")
        return entry

    def poll(self, predicate, *, timeout=8.0, interval=0.4):
        """Grab until predicate(frame) is truthy; return (frame, value) or the
        last (frame, None). check_stop each pass."""
        deadline = time.monotonic() + timeout
        frame = self.grab()
        while True:
            self.s.check_stop()
            value = predicate(frame)
            if value:
                return frame, value
            if time.monotonic() >= deadline:
                return frame, None
            self.pause(interval)
            frame = self.grab()


# ---------------------------------------------------------------- regions
# Native 1080x2560 bands. Generous on purpose: locate() searches inside them by
# OCR and returns None (never a blind tap) when the text is absent.
R_HOME_BATTLE = (250, 1950, 620, 240)     # the Home BATTLE button (~621,2057)
R_SPRINT = (0, 170, 170, 620)             # intro-sprint icon rail (detect.SPRINT_BAND)
R_DIALOG = (40, 980, 1010, 980)           # dialog titles ~y1024/1083, buttons ~y1377/1437
R_EXIT_BTN = (560, 480, 520, 940)         # in-run side menu (END ROUND / EXIT BATTLE ~888,786)
R_STATS_HDR = (60, 150, 960, 900)         # GAME STATS header (~333,752)
R_STATS_BTNS = (40, 1260, 1000, 1000)     # MORE STATS/PERKS ~y1343, HOME ~y1721
R_UW_PANEL = (0, 1800, 1080, 660)         # in-run upgrade panel (CONFIG upgrade_panel)
R_FULL = (0, 0, 1080, 2560)

# The Ultimate Weapon labels the game prints in the panel's UW tab, by the name
# it shows. capture_uw_weapons cuts the OWNED ones (the only rows normally on
# screen); an UNOWNED weapon has no row until the Tier-1 perk-roll lottery grants
# it (see _uw_lottery_pass), and _uw_remaining drives both off what is on disk.
UW_WEAPONS = (
    ("uw/black_hole.png", "Black Hole", (210, 55)),
    ("uw/chain_lightning.png", "Chain Lightning", (290, 55)),
    ("uw/chronofield.png", "Chrono Field", (250, 58)),
    ("uw/death_wave.png", "Death Wave", (235, 55)),
    ("uw/golden_tower.png", "Golden Tower", (270, 55)),
    # "Inner Land Mines" is the one name long enough to WRAP to two panel lines
    # ("Inner Land" / "Mines"); the crop must be tall enough to hold both. With
    # the UW anchor (ay=0.35) the box reaches 0.65*h below the text, so h=190
    # covers the ~120px two-line label (a 100px box clipped "Mines").
    ("uw/inner_land_mines.png", "Inner Land Mines", (300, 190)),
    ("uw/poison_swamp.png", "Poison Swamp", (280, 55)),
    ("uw/smart_missiles.png", "Smart Missiles", (340, 60)),
    ("uw/spotlight.png", "Spotlight", (185, 55)),
)

# Confirm dialogs and the GAME STATS screen draw two equal buttons side by side
# (No | Yes, MORE STATS | PERKS). Their identity is which SIDE they sit on, not
# a globally unique face, so they are captured by position (left/right) and
# written with unique=False. Measured centres: left x~352, right x~727.
R_STATS_ROW = (1310, 1450)     # MORE STATS / PERKS band on the GAME STATS screen
R_DIALOG_ROW = (1380, 1510)    # No / Yes band on a confirm dialog
R_HOME_ROW = (1680, 1790)      # RETRY / HOME band on the GAME STATS screen


def _row_buttons(frame, y_lo, y_hi, *, thresh=90):
    """(left_box, right_box) of the two side-by-side bright buttons in the
    horizontal band, each (x, y, w, h) in full-frame coords, or (None, None).
    Deterministic - no OCR - so it survives the stylised button text Windows
    OCR reads only intermittently.

    `thresh`: the RETRY / HOME pair on GAME STATS is dark-filled with only a
    green glow outline - at 90 the band yields nothing (measured on a saved
    frame, 2026-09-08); 60 catches the glow and returns both boxes."""
    sub = frame[y_lo:y_hi]
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((9, 25), np.uint8))
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [(x, y_lo + y, w, h) for x, y, w, h in map(cv2.boundingRect, cnts)
             if w > 140 and 45 < h < 170]
    if len(boxes) < 2:
        return None, None
    boxes.sort(key=lambda b: b[0])
    return boxes[0], boxes[-1]


def _button_crop(frame, box, *, pad_x=8, pad_y=24):
    """A padded crop of a detected button box, so the template is the whole
    button (the bright threshold catches the text, not the dim outline)."""
    x, y, w, h = box
    y0 = max(0, y - pad_y)
    y1 = min(frame.shape[0], y + h + pad_y)
    x0 = max(0, x - pad_x)
    x1 = min(frame.shape[1], x + w + pad_x)
    return frame[y0:y1, x0:x1].copy()


# ---------------------------------------------------------------- intro sprint

def _sprint_glyph(frame):
    """The intro-sprint stopwatch-with-X in the left rail, as (crop, (cx,cy)) in
    full-frame coords, or (None, None). Found structurally (no template yet):
    the icon is the only WHITE glyph in the rail - the gem is magenta, the
    buff/shield cyan - and it uniquely carries a small RED X over its top-right.
    White blob + red-X nearby is what tells it apart from the currency icons
    above it (which a plain brightness pass grabbed instead). The dialog its tap
    opens is the final confirmation before anything is written."""
    x, y, w, h = R_SPRINT
    sub = frame[y:y + h, x:x + w]
    b, g, r = (sub[:, :, 0].astype(int), sub[:, :, 1].astype(int),
               sub[:, :, 2].astype(int))
    white = (sub.min(axis=2) > 185).astype("uint8") * 255
    red = (r > 140) & (r - g > 60) & (r - b > 60)
    cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        bx, by, bw_, bh_ = cv2.boundingRect(c)
        if not (30 <= bw_ <= 100 and 30 <= bh_ <= 100):   # icons/intro_sprint ~61x64
            continue
        ex0, ey0 = max(0, bx - 10), max(0, by - 10)
        ex1, ey1 = min(w, bx + bw_ + 10), min(h, by + bh_ + 10)
        if int(red[ey0:ey1, ex0:ex1].sum()) < 20:         # no red X -> not the sprint icon
            continue
        if best is None or by < best[1]:
            best = (bx, by, bw_, bh_)
    if best is None:
        return None, None
    bx, by, bw_, bh_ = best
    # include the red X (up/right of the stopwatch body) in the template crop
    x0, y0 = max(0, bx - 4), max(0, by - 10)
    x1, y1 = min(sub.shape[1], bx + bw_ + 10), min(sub.shape[0], by + bh_ + 4)
    crop = sub[y0:y1, x0:x1].copy()
    return crop, (x + bx + bw_ // 2, y + by + bh_ // 2)


def capture_intro_sprint(flow: _Flow) -> None:
    """Tap the intro-sprint icon, capture the END INTRO SPRINT EARLY dialog and
    its confirm, then CANCEL it (the sprint keeps running - nothing spent).

    Best-effort: the icon only shows when the run started under an Intro Sprint
    card. No icon, or no dialog after tapping, means it is skipped cleanly - the
    tap lands in the empty left rail, which opens info dialogs, never a run
    action."""
    frame, glyph = flow.poll(lambda f: _sprint_glyph(f)[1] is not None,
                             timeout=10.0, interval=0.5)
    icon_crop, centre = _sprint_glyph(frame)
    icon_frame = frame                       # the glyph is proven on THIS frame
    if centre is None:
        flow.skip("home/intro_sprint_dialog.png", "no intro sprint this run")
        return
    flow.progress("Intro sprint: opening its dialog")
    flow.tap(centre[0], centre[1], "open intro sprint dialog")
    frame, ok = flow.poll(lambda f: has_text(_lines_text(f), "INTRO SPRINT")
                          or has_text(_lines_text(f), "END INTRO"), timeout=5.0)
    if not ok:
        flow.skip("home/intro_sprint_dialog.png",
                  "tapped the sprint rail but no dialog appeared")
        return
    # the tap proved the glyph is the icon: now it may be written (self-matched
    # against the frame it was read from - the icon stays visible behind the
    # dialog, but verify against its own frame)
    flow.cut_crop("icons/intro_sprint.png", icon_crop, icon_frame, "Intro Sprint",
                  {"where": "left rail"})
    flow.capture("home/intro_sprint_dialog.png", "END INTRO SPRINT", R_DIALOG,
                 (560, 130), name="End Intro Sprint Early", frame=frame,
                 anchor=(0.15, 0.4))
    # Yes is the RIGHT button of the dialog's two-button row - captured by
    # position (sibling of No, unique=False), not by OCR. END the sprint (tap
    # Yes): a cancelled sprint fast-forwards waves for minutes and a round CANNOT
    # be ended mid-sprint, so a calibration run that only cancelled could never
    # be surrendered (seen live: it ran to wave 300+ and end_round found no exit
    # button). After Yes, wait for the stopwatch to clear so the run is normal.
    _left, right = _row_buttons(frame, *R_DIALOG_ROW)
    if right:
        flow.cut_crop("home/intro_sprint_yes.png", _button_crop(frame, right), frame,
                      "Intro sprint yes", {"side": "right"}, unique=False)
        flow.tap(right[0] + right[2] // 2, right[1] + right[3] // 2,
                 "end intro sprint early")
        flow.poll(lambda f: _sprint_glyph(f)[1] is None, timeout=10.0)
    else:
        yes = flow.locate(frame, R_DIALOG, "YES")
        if yes:
            flow.tap(yes[0], yes[1], "end intro sprint early")
            flow.poll(lambda f: _sprint_glyph(f)[1] is None, timeout=10.0)
        flow.skip("home/intro_sprint_yes.png", "dialog button row not found")


# ---------------------------------------------------------------- ultimate weapons

def capture_uw_weapons(flow: _Flow) -> None:
    """Open the in-run panel's UW (ULTIMATE WEAPONS) tab and OCR-capture the
    name label of each weapon this account owns. Switching tabs spends nothing;
    it only changes what the panel shows. Refuses if the run has ended.

    The weapon icons ARE in the installed APK (asset_bindings map weapon_* to
    them), but their name label is rendered text, so the panel is its source -
    same as the wave digits and stat labels.
    """
    from settings import CONFIG
    frame = flow.grab()
    if flow.wave(frame) is None or flow.in_tournament(frame):
        return
    if not CONFIG.get("tabs", {}).get("uw"):
        return
    flow.progress("Battle: capturing Ultimate Weapon labels")
    # Tapping an already-open tab CLOSES the panel. shopper._tap_tab reads the
    # current tab from the header colour and taps only when a switch is needed
    # (and refuses off a live wave) - the tested, toggle-safe selector.
    from interactions import shopper
    if not shopper._tap_tab("uw"):
        flow.skip("uw/black_hole.png", "could not open the UW tab")
        return
    frame = flow.grab()
    for rel, text, size in UW_WEAPONS:
        flow.capture(rel, text, R_UW_PANEL, size, name=text, frame=frame,
                     anchor=(0.08, 0.35))
    # every owned weapon has an ON/OFF switch: cut one of each state by its
    # artwork (not unique on screen by design)
    capture_artwork_targets(flow, UW_SWITCH_TARGETS, unique=False, label="the weapon switches")
    try:
        f1 = flow.grab(); flow.pause(0.4); f2 = flow.grab()
        capture_uw_switches(getattr(flow, "cal", None), f1, f2)
    except Exception as e:                       # noqa: BLE001 - isolate
        from runtime import logger
        logger.event("flow_uw_switch_error", error=str(e)[:200])


# ---------------------------------------------------------------- end the run

def end_run_and_capture(flow: _Flow) -> bool:
    """Surrender the calibration run, capturing the exit dialog, GAME STATS
    screen and reward skip on the way to Home. Returns True on Home.

    This is a template-free twin of tourney.end_round, driven by OCR because
    the very templates end_round matches on are what it is capturing. It keeps
    end_round's two load-bearing rules: refuse the whole thing if a tournament
    run is on screen, and answer Surrender (never Go Home) on the exit dialog.
    """
    from settings import CONFIG
    frame = flow.grab()
    if flow.in_tournament(frame):
        from runtime import logger
        logger.shot(frame, "flow_end_run_tournament")
        flow.skip("home/exit_battle_dialog.png",
                  "a tournament run is on screen - never cancelled")
        return False

    # open the side menu and find the exit button (label is END ROUND on high
    # tiers, EXIT BATTLE on low ones - both mean the same slot). The exit label
    # being on screen IS the menu-open signal, so the toggle is tapped ONLY when
    # the menu is shut - tapping it on an open menu would close it again.
    # A single toggle tap + one OCR poll is not enough: the menu slides in and a
    # scale-2 read of END ROUND intermittently misses, and the tap itself can be
    # dropped mid-run. Retry - and stay toggle-safe by re-reading the open signal
    # each round (only tap the toggle when the menu is not already showing its
    # exit control, so an open menu is never tapped shut).
    tog = CONFIG["side_menu"]["toggle"]
    label = exit_label(_lines_text(frame))
    for _attempt in range(4):
        if label:
            break
        flow.tap(tog[0], tog[1], "open side menu")
        frame, label = flow.poll(lambda f: exit_label(_lines_text(f)), timeout=4.0)
    if not label:
        from runtime import logger
        logger.shot(frame, "flow_no_exit_button")
        flow.skip("buttons/end_round.png", "neither END ROUND nor EXIT BATTLE on the menu")
        return False
    if label == "END ROUND":
        flow.capture("buttons/end_round.png", "END ROUND", R_EXIT_BTN, (154, 45),
                     name="End round", frame=frame, anchor=(0.1, 0.2))
    exit_pt = flow.locate(frame, R_EXIT_BTN, label)
    if not exit_pt:
        flow.skip("buttons/end_round.png", f"{label} vanished before tapping")
        return False
    flow.tap(exit_pt[0], exit_pt[1], f"tap {label}")

    # the confirm dialog: capture it, then confirm. The two buttons are captured
    # by POSITION (Windows OCR reads their faces only intermittently). Which side
    # confirms differs by variant: Surrender is the LEFT button of the EXIT
    # BATTLE dialog (Go Home is right and only HIDES the run); Yes is the RIGHT
    # button of the END ROUND dialog.
    frame, kind = flow.poll(lambda f: exit_dialog_kind(_lines_text(f)), timeout=8.0)
    if not kind:
        from runtime import logger
        logger.shot(frame, "flow_no_exit_confirm")
        flow.skip("home/exit_battle_dialog.png", "no confirm dialog after the exit tap")
        return False
    if flow.in_tournament(frame):
        return False
    left, right = _row_buttons(frame, *R_DIALOG_ROW)
    if kind == "surrender":
        flow.capture("home/exit_battle_dialog.png", "EXIT BATTLE", R_DIALOG, (420, 110),
                     name="Exit battle dialog", frame=frame, anchor=(0.12, 0.4))
        if left:
            flow.cut_crop("home/surrender.png", _button_crop(frame, left), frame,
                          "Surrender", {"side": "left"}, unique=False)
        confirm = ((left[0] + left[2] // 2, left[1] + left[3] // 2) if left
                   else flow.locate(frame, R_DIALOG, "SURRENDER"))
    else:
        flow.capture("home/end_round_dialog.png", "END ROUND", R_DIALOG, (330, 70),
                     name="End round dialog", frame=frame, anchor=(0.12, 0.4))
        if right:
            flow.cut_crop("home/end_round_yes.png", _button_crop(frame, right), frame,
                          "End round yes", {"side": "right"}, unique=False)
        confirm = ((right[0] + right[2] // 2, right[1] + right[3] // 2) if right
                   else flow.locate(frame, R_DIALOG, "YES"))
    if not confirm:
        flow.skip("home/exit_battle_dialog.png", f"{kind} confirm button not locatable")
        return False
    flow.tap(confirm[0], confirm[1], f"confirm exit ({kind})")

    # Surrender is followed by a SECOND confirm - "END ROUND / Are you sure you
    # want to end the round?" (No / Yes) - the same dialog END ROUND opens
    # directly while the intro sprint still runs (frames 2026-09-08). Both
    # flow runs before this missed it: they polled for GAME STATS, the top-tier
    # tower died inside the poll and the stats came up by themselves, so
    # home/end_round_dialog.png was never cut and the Yes never tapped.
    if kind == "surrender":
        def _next(f):
            t = _lines_text(f)
            if exit_dialog_kind(t) == "end_round":
                return "end_round"
            return "done" if (has_text(t, "GAME STATS") or has_text(t, "SKIP")) else None
        frame, nxt = flow.poll(_next, timeout=6.0)
        if nxt == "end_round":
            flow.capture("home/end_round_dialog.png", "END ROUND", R_DIALOG, (330, 70),
                         name="End round dialog", frame=frame, anchor=(0.12, 0.4))
            _l, right = _row_buttons(frame, *R_DIALOG_ROW)
            if right:
                flow.cut_crop("home/end_round_yes.png", _button_crop(frame, right), frame,
                              "End round yes", {"side": "right"}, unique=False)
            yes = ((right[0] + right[2] // 2, right[1] + right[3] // 2) if right
                   else flow.locate(frame, R_DIALOG, "YES"))
            if yes:
                flow.tap(yes[0], yes[1], "confirm END ROUND (yes)")

    # a reward animation can sit in front of the stats screen
    frame, _ = flow.poll(lambda f: has_text(_lines_text(f), "GAME STATS")
                        or has_text(_lines_text(f), "SKIP"), timeout=10.0)
    if has_text(_lines_text(frame), "SKIP") and not flow.game_stats(frame):
        flow.capture("buttons/reward_skip.png", "SKIP", R_FULL, (253, 102),
                     name="Reward skip", frame=frame, anchor=(0.2, 0.35))
        skip_pt = flow.locate(frame, R_FULL, "SKIP")
        if skip_pt:
            flow.tap(skip_pt[0], skip_pt[1], "reward skip")

    return capture_game_stats(flow)


def capture_game_stats(flow: _Flow) -> bool:
    """The GAME STATS screen a finished/surrendered run leaves: capture its
    header (OCR - large, reliable), the MORE STATS / PERKS pair and the RETRY /
    HOME pair by POSITION (left/right), tap HOME, and confirm Home. Used by both
    the surrender path and the run-died-during-observation path. Returns True on
    Home. Settle first - the buttons render a beat after the header."""
    frame, ok = flow.poll(lambda f: flow.game_stats(f), timeout=12.0)
    if not ok:
        from runtime import logger
        logger.shot(flow.grab(), "flow_no_game_stats")
        frame, home = flow.poll(lambda f: flow.on_home(f), timeout=12.0)
        return bool(home)
    flow.pause(0.8)
    frame = flow.grab()
    flow.capture("icons/game_stats.png", "GAME STATS", R_STATS_HDR, (418, 55),
                 name="Game stats", frame=frame, anchor=(0.12, 0.3))
    sl, sr = _row_buttons(frame, *R_STATS_ROW)
    if sl:
        flow.cut_crop("buttons/more_stats.png", _button_crop(frame, sl), frame,
                      "More stats", {"side": "left"}, unique=False)
    else:
        flow.skip("buttons/more_stats.png", "MORE STATS / PERKS row not found")
    if sr:
        flow.cut_crop("buttons/perks.png", _button_crop(frame, sr), frame,
                      "Perks", {"side": "right"}, unique=False)
    # The RETRY / HOME pair, by POSITION like MORE STATS / PERKS above. The
    # runners need both (flows/coin.py lists them; tourney.find locates HOME by
    # home/game_stats_home.png) and NO other writer produced them, so a fresh
    # account could never become run-ready (found 2026-09-08).
    rl, rr = _row_buttons(frame, *R_HOME_ROW, thresh=60)
    if rl:
        flow.cut_crop("buttons/retry.png", _button_crop(frame, rl), frame,
                      "Retry", {"side": "left"}, unique=False)
    if rr:
        flow.cut_crop("home/game_stats_home.png", _button_crop(frame, rr), frame,
                      "Game stats home", {"side": "right"}, unique=False)
    # HOME, hardened against a DROPPED tap: BlueStacks intermittently swallows a
    # single tap, and one missed HOME here left GAME STATS on screen through the
    # bootstrap's final Home check ("Cannot verify home" abort, 2026-09-07, which
    # sank the whole starter scan). Re-detect the HOME button (the GAME STATS
    # layout is unchanged until the tap lands) and re-tap until on_home reads.
    for _ in range(4):
        flow.s.check_stop()
        _hl, hr = _row_buttons(frame, *R_HOME_ROW, thresh=60)
        home_pt = ((hr[0] + hr[2] // 2, hr[1] + hr[3] // 2) if hr
                   else flow.locate(frame, R_STATS_BTNS, "HOME"))
        if home_pt:
            flow.tap(home_pt[0], home_pt[1], "game stats: HOME")
        frame, home = flow.poll(lambda f: flow.on_home(f), timeout=4.0)
        if home:
            return True
    return False


# ------------------------------------------------------- tier + UW observation

TIER_FOR_END_ROUND = 99        # "the account's TOP tier": set_tier climbs until the
                               # selector stops moving. The exit control reads END
                               # ROUND / Yes-No only at the top tiers - Tier 19 still
                               # offered EXIT BATTLE / Surrender (live, 2026-09-08);
                               # the top tier kills the tower in seconds, which is
                               # the point: surrender at once, capture, done.


def read_tier(frame):
    """The Home difficulty tier as an int ('Tier 14' -> 14), or None."""
    import re
    for _y, _x, t in _read(frame, 2):
        m = re.search(r"[Tt]ier\s*(\d+)", t or "")
        if m:
            return int(m.group(1))
    return None


# The '<' / '>' difficulty arrows flanking 'Tier N' on Home (measured 2026-09-07)
TIER_DEC = (397, 1398)
TIER_INC = (687, 1398)


# The battle HUD's two bars at native 1080x2560 (config.example.yaml's wall_bar
# example; hp_bar is the shipped default). The wall bar renders ONLY for an
# account that has a wall, so a teal/purple fill there - with the HP bar lit
# beside it as proof the HUD is on screen - IS the wall. Measured 2026-09-08 on
# real frames: wall 0.65-0.68 lit teal, HP 0.75-0.78; menus, dialogs and GAME
# STATS under 0.1. Positive-only: nothing here ever records "no wall".
WALL_BAR_ROI = (84, 1559, 424, 44)
HP_BAR_ROI = (34, 1696, 478, 64)
BAR_LIT_MIN = 0.30


def _bar_lit_fraction(frame, box) -> float:
    """Share of a bar box painted in the game's bar colours (teal / purple)."""
    import cv2
    x, y, w, h = box
    if frame is None or frame.shape[0] < y + h or frame.shape[1] < x + w:
        return 0.0
    hsv = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
    hh, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 60) & (v > 60)
    teal = (hh >= 75) & (hh <= 105)
    purple = (hh >= 110) & (hh <= 140)
    return float((lit & (teal | purple)).mean())


def wall_bar_present(frame) -> bool:
    """True when the HUD is on screen (HP bar lit) AND the wall bar is painted."""
    return (_bar_lit_fraction(frame, HP_BAR_ROI) >= BAR_LIT_MIN
            and _bar_lit_fraction(frame, WALL_BAR_ROI) >= BAR_LIT_MIN)


def _record_wall(flow, frame) -> bool:
    """Record a wall the HUD proves: player.wall + the bar region for Apply to
    write into config rois.wall_bar (what the wall watch reads). Idempotent."""
    cal = getattr(flow, "cal", None)
    if cal is None:
        return False
    if cal.player.get("wall_verified_by") == "setup":
        return True
    if not wall_bar_present(frame):
        return False
    from runtime import logger
    cal.player.update(wall=True, wall_verified_by="setup", wall_bar=list(WALL_BAR_ROI))
    logger.event("flow_wall_detected", roi=list(WALL_BAR_ROI))
    return True


HUD_ABILITY_TARGETS = ("buttons/nuke.png", "buttons/demon_mode.png")
# HUD state badges the artwork can find while they are up: the Second Wind
# badge (Death Defied) shows for seconds, so every battle frame pair tries it
# and the observe WATCH catches it during the person's own runs. Never a
# reason to start a battle (a Tier-1 tower does not die).
HUD_STATE_TARGETS = ("floaters/second_wind.png",)
UW_SWITCH_TARGETS = ("uw/toggle_on.png", "uw/toggle_off.png")


def capture_hud_targets(flow, screen="battle") -> dict:
    """Cut the shipped manifest's fixed-rect HUD targets for `screen` (the
    cart tile and the hamburger in a run, the green X with the menu open) that
    are still missing, from two consecutive frames: colour or bar-structure
    at the native rect on both frames is the proof. Then whatever the learned
    manifest places on that screen. Positive-only."""
    scanner = getattr(flow, "s", None)
    if scanner is None or not hasattr(scanner, "cut"):
        return {}
    import settings
    from player.bootstrap_layout import screen_targets
    wanted = [t for t in screen_targets(screen) if not settings.template_path(t["rel"]).exists()]
    if not wanted:
        return capture_learned_targets(flow, screen)
    first = flow.grab()
    flow.pause(0.4)
    second = flow.grab()
    result = {}
    lines = scanner.read(first) if any(not t.get("icon") for t in wanted) else []
    for spec in wanted:
        before = len(scanner.skipped)
        try:
            scanner.cut(spec, first, second, lines, screen=screen)
        except Exception as e:                  # noqa: BLE001 - isolate
            from runtime import logger
            logger.event("flow_hud_error", target=spec["rel"], error=str(e)[:200])
            continue
        result[spec["rel"]] = "not_seen" if len(scanner.skipped) > before else "verified"
    from runtime import logger
    logger.event("flow_hud_targets", screen=screen, result=result)
    try:
        result.update(scanner.learned_cuts(screen, first, second))
    except Exception as e:                      # noqa: BLE001
        logger.event("flow_learned_error", screen=screen, error=str(e)[:200])
    return result


def capture_side_menu(flow) -> dict:
    """The in-run side menu behind the hamburger (manifest `side_menu`): its
    tiles are what the reward flows tap, and they show only while it is open.
    Found SHUT: the hamburger must read as three bars on two frames, then one
    tap opens it (a menu setup opens itself, CLAUDE.md rule 4); the column's
    artwork-bound tiles (the quests checkbox) and the green X are cut; one
    tap shuts it again. Found OPEN: read as it is and left open (the runner
    keeps it open during runs anyway). Neither: nothing tapped."""
    from player.bootstrap import icon_present
    from player.bootstrap_layout import manifest
    from runtime import logger
    sm = manifest().get("side_menu")
    scanner = getattr(flow, "s", None)
    if not sm or scanner is None or not hasattr(scanner, "verify_asset_rels"):
        return {}
    toggle = sm["toggle"]
    shut = {"rect": toggle["rect"], "hue": toggle["closed_hue"]}
    open_ = {"rect": toggle["rect"], "hue": toggle["open_hue"]}
    x, y, w, h = toggle["rect"]
    point = (x + w // 2, y + h // 2)
    frame = flow.grab()
    was_shut = icon_present(frame, shut)
    if not was_shut and not icon_present(frame, open_):
        flow.skip("icons/tile_quests.png", "Side menu toggle not lit at its native position; no tap sent")
        return {}
    if was_shut:
        flow.pause(0.3)
        follow = flow.grab()
        if not icon_present(follow, shut):
            flow.skip("icons/tile_quests.png", "Side menu toggle changed between frames; no tap sent")
            return {}
        flow.progress("Battle: opening the side menu for its tiles")
        flow.tap(*point, "open the side menu")
        flow.pause(0.8)
        frame = flow.grab()
        if not icon_present(frame, open_):
            flow.skip("icons/tile_quests.png", "Side menu did not open")
            logger.event("flow_side_menu", opened=False)
            return {}
    flow.pause(0.3)
    follow = flow.grab()
    rels = [rel for rel, d in manifest().get("asset_bindings", {}).items() if d.get("screen") == "battle_menu"]
    result = {}
    try:
        result.update(scanner.verify_asset_rels([r for r in rels if r not in _have_templates(rels)], frame, follow))
    except Exception as e:                      # noqa: BLE001 - isolate
        logger.event("flow_side_menu_error", error=str(e)[:200])
    result.update(capture_hud_targets(flow, "battle_menu"))
    if was_shut:
        flow.tap(*point, "close the side menu")
        flow.pause(0.8)
        if not icon_present(flow.grab(), shut):
            logger.event("flow_side_menu", opened=True, closed=False)
    logger.event("flow_side_menu", opened=True, was_shut=was_shut, result=result)
    return result


def capture_learned_targets(flow, screen="battle") -> dict:
    """Cut what the learned manifest places on `screen` (the HUD controls the
    person cropped from a run) from two consecutive frames - same rect, same
    size. Positive-only; a no-op without a scanner or without learned rows."""
    scanner = getattr(flow, "s", None)
    cuts = getattr(scanner, "learned_cuts", None)
    if cuts is None:
        return {}
    from player import learned
    if not learned.targets_on(scanner.cal.p, screen):
        return {}
    first = flow.grab()
    flow.pause(0.4)
    second = flow.grab()
    try:
        result = cuts(screen, first, second)
    except Exception as e:                      # noqa: BLE001 - isolate: never end the pass
        from runtime import logger
        logger.event("flow_learned_error", screen=screen, error=str(e)[:200])
        return {}
    if result:
        from runtime import logger
        logger.event("flow_learned_targets", screen=screen, result=result)
    return result


def capture_artwork_targets(flow, rels, *, unique=True, label="controls") -> dict:
    """Cut on-screen controls by finding the installed game's own artwork in
    two consecutive frames (bootstrap.Scanner.verify_asset_rels): the HUD
    ability buttons during the Tier-1 run, the UW switches with the panel
    open. Positive-only and idempotent - a target already on disk is skipped
    by the cut, one that is not on screen is just not seen. A verified
    ability button also records the ability as owned (player.abilities)."""
    scanner = getattr(flow, "s", None)
    verify = getattr(scanner, "verify_asset_rels", None)
    if verify is None:
        return {}
    wanted = [r for r in rels if r not in _have_templates(rels)]
    if not wanted:
        return {}
    flow.progress(f"Battle: locating {label} with the installed artwork")
    first = flow.grab()
    flow.pause(0.4)
    second = flow.grab()
    try:
        result = verify(wanted, first, second, unique=unique)
    except Exception as e:                      # noqa: BLE001 - isolate: never end the pass
        from runtime import logger
        logger.event("flow_artwork_error", targets=wanted, error=str(e)[:200])
        return {}
    cal = getattr(flow, "cal", None)
    for rel, state in result.items():
        if state == "verified" and rel in HUD_ABILITY_TARGETS and cal is not None:
            cal.player.setdefault("abilities", {})[rel.split("/")[-1][:-4]] = True
            cal.player["abilities_verified_by"] = "setup"
    from runtime import logger
    logger.event("flow_artwork_targets", result=result)
    return result



# The UW panel's ON/OFF switch sits in a fixed spot under each weapon's name
# label (the same 180x90 window shopper._uw_state reads): a pill about 83x44,
# teal-green when ON, dark grey with a light rim when OFF. Measured live
# 2026-09-08 on three rows (label x+18..22, y+101..103). The name labels are
# templates setup already cut, so the switches are found by POSITION off a
# verified label - no artwork needed (the APK sprite is an outline the game
# never renders as-is, and SIFT found nothing at 1x or 2x).
SWITCH_WINDOW = (0, 0, 180, 90)          # below the label's bottom-left corner
SWITCH_PAD = 4


def find_uw_switches(frame, labels) -> list:
    """[(state, rect)] for every switch found under a matched weapon label.
    `labels` = {weapon: template image}. state is "on" / "off"."""
    out = []
    for name, tpl in labels.items():
        if tpl is None or frame.shape[0] < tpl.shape[0] or frame.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, (lx, ly) = cv2.minMaxLoc(res)
        if score < 0.9:
            continue
        x0, y0 = lx + SWITCH_WINDOW[0], ly + tpl.shape[0] + SWITCH_WINDOW[1]
        region = frame[y0:y0 + SWITCH_WINDOW[3], x0:x0 + SWITCH_WINDOW[2]]
        if region.size == 0 or region.shape[0] < SWITCH_WINDOW[3]:
            continue                                  # row cut off by the tab bar
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        lit = (hsv[..., 2] > 95).astype(np.uint8)
        n, _lab, stats, _c = cv2.connectedComponentsWithStats(lit, 8)
        best = None
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            if 60 <= bw <= 130 and 30 <= bh <= 70 and 1.4 <= bw / bh <= 2.6 and (best is None or area > best[4]):
                best = (int(bx), int(by), int(bw), int(bh), int(area))
        if not best:
            continue
        bx, by, bw, bh, _a = best
        pill = cv2.cvtColor(region[by:by + bh, bx:bx + bw], cv2.COLOR_BGR2HSV)
        green = float(((pill[..., 0] > 60) & (pill[..., 0] < 105) & (pill[..., 1] > 100) & (pill[..., 2] > 110)).mean())
        rx, ry = max(0, x0 + bx - SWITCH_PAD), max(0, y0 + by - SWITCH_PAD)
        rect = [rx, ry, min(frame.shape[1] - rx, bw + 2 * SWITCH_PAD), min(frame.shape[0] - ry, bh + 2 * SWITCH_PAD)]
        out.append(("on" if green > 0.25 else "off", rect, name))
    return out


def capture_uw_switches(cal, frame, follow) -> dict:
    """Cut uw/toggle_on.png and uw/toggle_off.png by position under the owned
    weapons' verified name labels, one of each state, when the UW tab is on
    screen in both frames. Positive-only; returns {rel: verification}."""
    import settings
    from player import accounts
    wanted = [r for r in UW_SWITCH_TARGETS if not accounts.template_path(settings.ROOT, settings.CONFIG, r).is_file()]
    if not wanted or cal is None:
        return {}
    labels = {}
    for rel, _text, _size in UW_WEAPONS:
        path = accounts.template_path(settings.ROOT, settings.CONFIG, rel)
        if path.is_file():
            labels[rel.split("/")[-1][:-4]] = cv2.imread(str(path))
    if not labels:
        return {}
    first = {s: (rect, name) for s, rect, name in reversed(find_uw_switches(frame, labels))}
    second = {(s, tuple(rect)) for s, rect, _n in find_uw_switches(follow, labels)}
    out = {}
    for state, (rect, name) in first.items():
        rel = f"uw/toggle_{state}.png"
        if rel not in wanted or (state, tuple(rect)) not in second:
            continue
        x, y, w, h = rect
        crop = frame[y:y + h, x:x + w].copy()
        entry = cal.cut("bootstrap", rel, crop, frame, f"UW switch {state.upper()}",
                        {"source": "position", "verifier": f"pill under the {name} label", "rect": rect},
                        unique=False)
        out[rel] = "verified" if entry.get("verified") else "needs_attention"
    if out:
        from runtime import logger
        logger.event("flow_uw_switches", result=out)
    return out


def _have_templates(rels) -> set:
    import settings
    from player import accounts
    return {r for r in rels if accounts.template_path(settings.ROOT, settings.CONFIG, r).is_file()}


def set_tier(flow, target=TIER_FOR_END_ROUND):
    """Nudge the Home difficulty to `target` with the tier arrows, read-verified
    each step. Returns the tier actually reached (may be capped below target on
    a fresh account), or None if Home shows no tier."""
    prev, stall = None, 0
    for _ in range(60):
        flow.s.check_stop()
        cur = read_tier(flow.grab())
        if cur is None:
            return None
        if cur == target:
            return cur
        if cur == prev:
            stall += 1
            if stall >= 3:                     # arrow no longer moves it - capped
                return cur
        else:
            stall = 0
        prev = cur
        flow.tap(*(TIER_INC if cur < target else TIER_DEC), f"tier {cur}->{target}")
        flow.pause(0.5)
    return read_tier(flow.grab())


def _uw_remaining():
    """The Ultimate Weapon targets whose template is not yet on disk."""
    import settings
    from player import accounts
    out = []
    for rel, text, size in UW_WEAPONS:
        if not accounts.template_path(settings.ROOT, settings.CONFIG, rel).is_file():
            out.append((rel, text, size))
    return out


def capture_missing_uw_labels(frame) -> list[str]:
    """Opportunistic, autonomous capture of any Ultimate Weapon NAME label on
    `frame` (a UW-panel view) whose template is not yet on disk - the RUN-TIME
    'subroutine' twin of the setup lottery, meant to be called each game a runner
    already has the UW panel open (e.g. flows/quest_sm.py's grant scan) so the
    labels get discovered over normal play instead of a blocking setup pass.

    Owned weapons are always on the panel; an UNOWNED one is on it only while a
    Tier-1 perk grant holds it for the run, which is when this catches it. OCR
    finds the exact name and cuts a template, written through the sanctioned
    calibrate writer - MISSING targets only, NEVER replacing a file (CLAUDE.md
    rule 6). A near-free no-op (one disk stat per weapon) once every UW is known.
    Returns the rels written."""
    remaining = _uw_remaining()
    if not remaining:
        return []
    from player import battle_capture as bc
    from vision import textocr
    from runtime import logger
    written = []
    read = lambda c, s: textocr.read_lines(c, s)
    for rel, text, size in remaining:
        crop = bc.capture_by_text(frame, text, R_UW_PANEL, size, read,
                                  anchor=(0.08, 0.35))
        if crop is None:                          # this weapon not on the panel now
            continue
        if bc.write_template(rel, crop) == "written":
            written.append(rel)
            logger.event("uw_label_captured", rel=rel, via="run_subroutine")
    return written


# no grant by this wave -> the run's perk roll is spent; reroll for a fresh one
# (flows/quest_sm.py RESTART_AT_WAVE, user-tuned 2026-08-16).
UW_REROLL_WAVE = 1000


def _start_lottery_run(flow) -> bool:
    """From Home, start a NORMAL run and leave the Intro Sprint RUNNING - its
    per-wave perk roll is the whole point here (a battle pass, by contrast, ENDS
    the sprint so it can surrender). Ensures max speed so the rolls fly. Returns
    False if a run could not be brought up."""
    from flows import shard
    from runtime import logger
    frame = flow.grab()
    if not flow.on_home(frame):
        _safe_home(flow)
        frame = flow.grab()
    battle_pt = flow.locate(frame, R_HOME_BATTLE, "BATTLE")
    if not battle_pt:
        return False
    flow.tap(battle_pt[0], battle_pt[1], "start UW lottery run")
    frame, started = flow.poll(
        lambda f: flow.wave(f) is not None or _sprint_glyph(f)[1] is not None,
        timeout=25.0, interval=0.6)
    if not started or flow.in_tournament(frame):
        return False
    try:
        shard.wait_for_wave(1)
        shard.ensure_max_speed()
    except Exception as e:                       # noqa: BLE001 - best effort
        logger.event("flow_uw_lottery", stage="start_warn", warn=str(e)[:150])
    return True


def _reroll_lottery_run(flow) -> bool:
    """Abandon the current run and restart a fresh one via RETRY - the fast,
    in-battle reroll flows/quest_sm.py uses (it keeps the Tier and returns
    straight to a new sprint). Max speed, sprint running. Falls back to a Home
    restart if RETRY does not bring a run back."""
    from flows import shard
    from runtime import logger
    try:
        shard.abandon_run()                      # side menu -> exit -> confirm -> RETRY
        shard.wait_for_wave(1)
        shard.ensure_max_speed()
        return True
    except Exception as e:                        # noqa: BLE001
        logger.event("flow_uw_lottery", stage="reroll_fallback", error=str(e)[:150])
        _safe_home(flow)
        return _start_lottery_run(flow)


def _sweep_uw_for_targets(flow) -> bool:
    """Open the UW tab and scroll top-to-bottom, OCR-capturing any still-missing
    target label. A granted weapon shows as an extra owned row rendered exactly
    like the rest, so the same text cut that took the owned labels takes these.
    Returns True if a new label was captured this sweep."""
    from interactions import shopper
    if flow.wave(flow.grab()) is None:
        return False                             # not in a run - nothing to read
    if not shopper._tap_tab("uw"):
        return False
    try:
        shopper._scroll_to_top()
    except Exception:                            # noqa: BLE001
        pass
    before = len(_uw_remaining())
    for _ in range(6):                           # walk the scrollable UW list
        f = flow.grab()
        for rel, text, size in _uw_remaining():
            flow.capture(rel, text, R_UW_PANEL, size, name=text, frame=f,
                         anchor=(0.08, 0.35))
        if not _uw_remaining():
            break
        shopper._swipe_panel_down()
    return len(_uw_remaining()) < before


def _uw_lottery_pass(flow, max_minutes=None) -> None:
    """Capture the account's UNOWNED Ultimate Weapon labels via the Tier-1
    perk-roll lottery - the ONLY place they render.

    An unowned weapon is never in the in-run UW panel; you cannot equip what you
    do not own. But on Tier 1 the Intro Sprint's per-wave perk roll can randomly
    GRANT one of the unowned weapons for the duration of that run, and the
    granted weapon then shows as an extra panel row with its name. This is the
    mechanic flows/quest_sm.py farms; here we only need the label once:

        set Tier 1 -> start a run, sprint LEFT RUNNING (waves = rolls) ->
        every ~15s OCR-scan the UW panel for a still-missing target ->
          grant is a target  -> cut it, then reroll (one grant per run) ->
          no grant by wave ~1000 -> the roll is spent -> reroll (abandon/RETRY)
        until every target is captured (or max_minutes, if set).

    Chrono Field is owned now, so every grant is one of the two real targets.
    `max_minutes=None` runs until both land, relying on the dashboard's stop
    (check_stop) as the escape hatch. The caller's finally restores the tier."""
    import time
    from runtime import logger
    if not _uw_remaining():
        return
    set_tier(flow, 1)
    if not _start_lottery_run(flow):
        flow.skip("uw/smart_missiles.png", "could not start the Tier-1 lottery run")
        return
    flow.progress("UW lottery: Tier-1 perk-roll run - watching for a grant")
    deadline = (time.monotonic() + max_minutes * 60) if max_minutes else None
    rerolls = 0
    while _uw_remaining():
        flow.s.check_stop()
        if deadline and time.monotonic() > deadline:
            logger.event("flow_uw_lottery", result="timeout", rerolls=rerolls,
                         remaining=[r for r, _, _ in _uw_remaining()])
            break
        f = flow.grab()
        if flow.in_tournament(f):                # never our screen - bail safely
            logger.shot(f, "flow_uw_lottery_tournament")
            break
        # tower died / between runs -> reroll to a fresh Tier-1 sprint
        if flow.game_stats(f) or (flow.wave(f) is None and not flow.side_menu(f)):
            rerolls += 1
            logger.event("flow_uw_lottery", stage="restart_dead", rerolls=rerolls)
            if not _reroll_lottery_run(flow):
                break
            continue
        got = _sweep_uw_for_targets(flow)
        if not _uw_remaining():
            break
        if got:                                  # this run's single grant is used
            rerolls += 1
            logger.event("flow_uw_lottery", stage="reroll_after_grant",
                         rerolls=rerolls,
                         remaining=[r for r, _, _ in _uw_remaining()])
            if not _reroll_lottery_run(flow):
                break
            continue
        w = flow.wave(flow.grab())
        if w is not None and w >= UW_REROLL_WAVE:
            rerolls += 1
            logger.event("flow_uw_lottery", stage="reroll", at_wave=w,
                         rerolls=rerolls,
                         remaining=[r for r, _, _ in _uw_remaining()])
            if not _reroll_lottery_run(flow):
                break
        else:
            flow.pause(15)                       # let the sprint roll more perks
    # Leave the account on a clean Home. Use the OCR-based surrender (proven on
    # this layout in the END ROUND pass), NOT shard.abandon_run: on BlueStacks
    # its coordinate-based side-menu taps mis-hit the stat/perk icons and opened
    # overlays instead of Home (seen live 2026-09-07). Do NOT fall back to
    # ensure_home on failure - it HOLDS on a live run (rule 3) and would hang
    # setup; a leftover Tier-1 NORMAL run costs nothing (rule 3), so leaving it
    # beats hanging. The run-died path taps HOME off GAME STATS directly.
    try:
        f = flow.grab()
        if flow.game_stats(f):
            capture_game_stats(flow)              # already on stats -> taps HOME
        else:
            end_run_and_capture(flow)             # live run -> surrender -> HOME
    except Exception as e:                        # noqa: BLE001
        logger.event("flow_uw_lottery", stage="final_exit_error", error=str(e)[:150])
    logger.event("flow_uw_lottery", result="done", rerolls=rerolls,
                 remaining=[r for r, _, _ in _uw_remaining()])


# ---------------------------------------------------------------- battle flow

def capture_hud_digits(flow: _Flow) -> list[str]:
    """Cut the HUD wave font (digits/0-9.png) from the LIVE wave counter.

    A fresh account has no digit font, and wave_reader REFUSES to read a wave
    without it - so this is the one digit source that does not itself need
    digits. It segments the wave box exactly as wave_reader does and OCR-labels
    each glyph (battle_capture.digit_glyphs). The caller must have a Tier-1 run
    on screen (see `_hud_digit_pass`): a surviving tower's counter climbs
    1->10->20->30..., and every 0-9 shows by wave ~90.

    Proven live (2026-09-08): all ten captured in ~58s, and the freshly written
    font read a live wave back (wave_reader.read_wave -> 90). TIME-based, not
    frame-count based - the counter's milestone jumps are ~7s apart, so a
    consecutive-miss cutoff quit mid-climb; instead scan until 10/10, GAME STATS
    (the run ended), or the deadline. MISSING targets only, written through the
    sanctioned account-local writer. Returns the rels written."""
    import time
    import settings
    from player import battle_capture as bc
    from vision import textocr
    from runtime import logger
    if all(settings.template_path(f"digits/{d}.png").exists() for d in "0123456789"):
        return []
    x, y, w, h = settings.CONFIG["rois"]["wave_box"]
    ocr = lambda crop: textocr.read_text(crop)
    got: dict = {}
    deadline = time.monotonic() + 180            # generous; stops early at 10/10
    while time.monotonic() < deadline and len(got) < 10:
        flow.s.check_stop()
        f = flow.grab()
        if flow.game_stats(f):                   # the run ended - counter is gone
            break
        crop = f[y:y + h, x:x + w]
        for ch, glyph in bc.digit_glyphs(crop, ocr(crop)).items():
            got.setdefault(ch, glyph)
        flow.pause(0.3)
    written = [f"digits/{ch}.png" for ch, glyph in got.items()
               if bc.write_template(f"digits/{ch}.png", glyph) == "written"]
    if written:
        # wave_reader caches whatever it loaded (a PARTIAL dict survives the
        # "incomplete" raise) - drop it so the fresh font is read from disk.
        from vision import wave_reader
        wave_reader._templates = None
        logger.event("hud_digits_captured", rels=sorted(written), seen=len(got))
    flow.progress(f"Captured {len(written)} HUD digit(s) from the wave counter")
    return written


def _hud_digit_pass(flow: _Flow) -> None:
    """Capture the HUD wave font at TIER 1, the one digit source that does not
    itself need digits (wave_reader refuses to read without the font, and
    read_tier/set_tier here are OCR-based, so Tier 1 is reachable blank).

    A HIGH-tier tower dies at wave ~3 - too few waves to show every glyph - so
    this runs its OWN Tier-1 run where the maxed tower SURVIVES and the counter
    climbs through all ten. Guarded on a missing font: a no-op once it is known.

    Starts a normal run, captures + ENDS the intro sprint (so the run stays
    surrenderable and the counter keeps climbing at Tier 1), reads the climbing
    counter (`capture_hud_digits`), then leaves the account on a clean Home -
    surrender or, if the tower already died, HOME off GAME STATS. Like the UW
    lottery, this is a run setup STARTED and may end (CLAUDE.md rule 3)."""
    import settings
    from runtime import logger
    digits_ok = all(settings.template_path(f"digits/{d}.png").exists() for d in "0123456789")
    uw_never = len(_uw_remaining()) == len(UW_WEAPONS)   # not one UW label cut yet
    from player.bootstrap_layout import screen_targets
    hud_targets = (HUD_ABILITY_TARGETS + UW_SWITCH_TARGETS + ("icons/tile_quests.png",)
                   + tuple(t["rel"] for s in ("battle", "battle_menu") for t in screen_targets(s)))
    hud_missing = bool(set(hud_targets) - _have_templates(hud_targets))   # ability buttons / UW switches / HUD tiles
    if digits_ok and not uw_never and not hud_missing:
        return
    set_tier(flow, 1)
    frame = flow.grab()
    if not flow.on_home(frame):
        _safe_home(flow)
        frame = flow.grab()
    battle_pt = flow.locate(frame, R_HOME_BATTLE, "BATTLE")
    if not battle_pt:
        flow.skip("digits/0.png", "Home BATTLE button not found for the digit pass")
        return
    flow.progress("Digit font: starting a Tier-1 run to read the wave counter")
    flow.tap(battle_pt[0], battle_pt[1], "start Tier-1 digit run")
    frame, started = flow.poll(
        lambda f: flow.wave(f) is not None or _sprint_glyph(f)[1] is not None
                  or flow.side_menu(f),
        timeout=25.0, interval=0.6)
    if not started:
        logger.shot(frame, "flow_digits_run_did_not_start")
        flow.skip("digits/0.png", "Tier-1 digit run did not start")
        return
    if flow.in_tournament(frame):
        logger.shot(frame, "flow_digits_unexpected_tournament")
        return
    try:
        capture_intro_sprint(flow)               # capture the dialog, then end the sprint
    except Exception as e:                        # noqa: BLE001 - isolate
        logger.event("flow_digits", stage="sprint_warn", error=str(e)[:150])
    _record_wall(flow, flow.grab())           # the HUD proves a wall (positive-only)
    capture_artwork_targets(flow, HUD_ABILITY_TARGETS + HUD_STATE_TARGETS, label="the ability buttons")
    capture_hud_targets(flow, "battle")          # the cart tile, the hamburger, learned HUD spots
    written = capture_hud_digits(flow)
    # The OWNED Ultimate Weapon labels live here too: the tower SURVIVES at
    # Tier 1, so the UW-tab sweep cannot race a death - at the account's top
    # tier (the END ROUND pass) it did, and the surrender must come first.
    try:
        capture_uw_weapons(flow)
    except Exception as e:                        # noqa: BLE001 - isolate
        logger.event("flow_uw_error", error=str(e)[:200])
    # the side menu's tiles (quests checkbox, the X) - the tower survives here
    try:
        capture_side_menu(flow)
    except Exception as e:                        # noqa: BLE001 - isolate
        logger.event("flow_side_menu_error", error=str(e)[:200])
    frame = flow.grab()
    if flow.game_stats(frame):
        reached = capture_game_stats(flow)
    else:
        flow.progress("Digit pass done; ending the Tier-1 run")
        reached = end_run_and_capture(flow)
    logger.event("flow_digits", result="done", written=written, home=bool(reached))


def capture_battle_flow(flow: _Flow, observe_minutes: float | None = None,
                        *, do_lottery: bool = False) -> None:
    """The END ROUND + results battle pass, then the difficulty is restored.
    Gated on a clean Home and refused over any live or tournament run.

    Pass A (high tier, quick): the exit dialog only shows END ROUND / Yes-No at a
    high tier - below it it is Surrender / Go Home (already captured). A high-tier
    tower dies in ~30s, so this pass surrenders promptly to catch END ROUND, and
    picks up the intro-sprint dialog, the GAME STATS controls and the OWNED
    Ultimate Weapon labels on the way.

    Finding the UNOWNED weapons is OPTIONAL and OFF here: they only render when a
    Tier-1 perk grant holds one for a run, so making setup wait for that lottery
    would block for a long time. Instead the labels are captured opportunistically
    DURING normal Tier-1 play - flows/quest_sm.py calls capture_missing_uw_labels
    each grant scan, so every SM-farm game keeps trying until they are all known.
    `do_lottery=True` opts back into the blocking in-setup lottery
    (`_uw_lottery_pass`) for anyone who wants to force it now.

    The player's original difficulty is always put back at the end - setup never
    leaves the account on a harder tier than it found."""
    from runtime import logger
    frame = flow.grab()
    ok, why = can_start_battle(
        on_home=flow.on_home(frame), wave=flow.wave(frame),
        side_menu=flow.side_menu(frame), game_stats=flow.game_stats(frame),
        tournament=flow.in_tournament(frame))
    if not ok:
        logger.event("flow_battle_skipped", why=why)
        flow.progress(f"Battle capture skipped: {why}")
        flow.skip("home/intro_sprint_dialog.png", why)
        return
    original_tier = read_tier(frame)
    try:
        _hud_digit_pass(flow)          # the wave font, at Tier 1 (fresh account); a no-op once known
        _battle_pass(flow, TIER_FOR_END_ROUND, "END ROUND + results capture")
        if do_lottery and _uw_remaining():
            _uw_lottery_pass(flow, max_minutes=observe_minutes)
    finally:
        if original_tier is not None and read_tier(flow.grab()) != original_tier:
            set_tier(flow, original_tier)
            logger.event("flow_tier_restored", tier=original_tier)


def _battle_pass(flow: _Flow, tier, label: str) -> None:
    """One battle at the top tier: start, capture the intro-sprint dialog and
    end the sprint, then end the run straight away - surrendering (END ROUND
    dialog, GAME STATS with RETRY / HOME, reward skip) or, if the tower already
    died, cutting the GAME STATS screen directly. (The owned UW labels are cut in
    `_hud_digit_pass`; the UNOWNED ones only appear via the Tier-1 grant lottery,
    see `_uw_lottery_pass`.)"""
    from runtime import logger
    if tier is not None:
        reached = set_tier(flow, tier)
        # The climb to TIER_FOR_END_ROUND stops where the account's tier
        # arrows stop: that is the highest tier seen unlocked. Recorded as a
        # HINT (player.max_tier, per setup) that Apply carries into the
        # profile - never a ceiling the compiler refuses a run tier against.
        if tier == TIER_FOR_END_ROUND and reached and getattr(flow, "cal", None) is not None:
            flow.cal.player["max_tier"] = reached
            flow.cal.player["max_tier_verified_by"] = "setup"
            logger.event("flow_top_tier", tier=reached)
    frame = flow.grab()
    if not flow.on_home(frame):
        _safe_home(flow)
        frame = flow.grab()
    battle_pt = flow.locate(frame, R_HOME_BATTLE, "BATTLE")
    if not battle_pt:
        flow.skip("home/intro_sprint_dialog.png", "Home BATTLE button not found")
        return
    flow.progress(f"Battle pass ({label}): starting a Tier {read_tier(frame)} run")
    flow.tap(battle_pt[0], battle_pt[1], "start run")
    frame, started = flow.poll(
        lambda f: flow.wave(f) is not None or _sprint_glyph(f)[1] is not None
                  or flow.side_menu(f),
        timeout=25.0, interval=0.6)
    if not started:
        logger.shot(frame, "flow_run_did_not_start")
        flow.skip("home/intro_sprint_dialog.png", "run did not start")
        return
    if flow.in_tournament(frame):
        logger.shot(frame, "flow_unexpected_tournament")
        return
    _record_wall(flow, frame)
    try:
        capture_intro_sprint(flow)             # captures the dialog, then ends the sprint
    except Exception as e:                       # noqa: BLE001 - isolate
        logger.event("flow_intro_sprint_error", error=str(e)[:200])
    # Nothing else before the surrender: at the top tier the tower lives for
    # seconds, and the UW-tab sweep that used to sit here raced its death (then
    # only the plain GAME STATS path was left - no END ROUND dialog). The wave
    # font and the owned UW labels are cut in the Tier-1 _hud_digit_pass, where
    # the tower survives. ONE battle gives every results screen from here on.
    frame = flow.grab()
    _record_wall(flow, frame)
    # the top-tier tower is dying about now: the one setup moment the Second
    # Wind badge can be up (positive-only, two frames, nothing waits for it)
    capture_artwork_targets(flow, HUD_STATE_TARGETS, label="the Second Wind badge")
    capture_hud_targets(flow, "battle")
    if flow.game_stats(frame):
        flow.progress("Run ended on its own; capturing the results screens")
        reached = capture_game_stats(flow)
    else:
        flow.progress("Ending the run and capturing the results screens")
        reached = end_run_and_capture(flow)
    if not reached:
        logger.event("flow_battle_incomplete",
                     note="could not confirm Home after the run; left as-is")


# ---------------------------------------------------------------- menu extras

def _menu_capture(flow: _Flow, targets):
    """Capture each (rel, text, size, anchor) currently visible; skip the rest.
    Never taps - only reads whatever menu the caller already navigated to."""
    frame = flow.grab()
    for rel, text, size, anchor in targets:
        flow.capture(rel, text, R_FULL, size, frame=frame, anchor=anchor)


def capture_menu_extras(flow: _Flow) -> None:
    """Open Events, Store and Guild and OCR-capture the text-labelled claim and
    section controls a menu walk surfaces. Navigation reuses tourney's helpers
    (template/route based) and always returns to Home; a screen that does not
    come up is skipped, never tapped past."""
    from interactions import tourney
    from runtime import logger
    frame = flow.grab()
    if not flow.on_home(frame):
        flow.skip("icons/free_gems.png", "not on Home for the menu-extras pass")
        return

    # -- Events / missions
    try:
        hit = tourney.find(frame, "home/tile_event.png")
        if hit:
            flow.tap(hit[0][0], hit[0][1], "open events")
            f, ok = flow.poll(lambda f: has_text(_lines_text(f), "EVENT")
                              or has_text(_lines_text(f), "MISSIONS"), timeout=6.0)
            if ok:
                _menu_capture(flow, [
                    ("icons/daily_missions.png", "DAILY MISSIONS", (430, 40), (0.05, 0.3)),
                    ("buttons/quest_claim.png", "CLAIM", (160, 50), (0.2, 0.35)),
                    ("icons/event_calendar.png", "CALENDAR", (138, 124), (0.3, 0.5)),
                ])
            tourney.return_to_game("events")
    except Exception as e:                       # noqa: BLE001
        logger.event("flow_menu_events_error", error=str(e)[:200])
        _safe_home(flow)

    # -- Store
    try:
        frame = flow.grab()
        if flow.on_home(frame):
            flow.tap(tourney.NAV["shop"][0], tourney.NAV["shop"][1], "open store")
            f, ok = flow.poll(lambda f: has_text(_lines_text(f), "STORE"), timeout=6.0)
            if ok:
                _menu_capture(flow, [
                    ("icons/free_gems.png", "FREE", (130, 48), (0.2, 0.35)),
                    ("icons/store_tower_guardian.png", "TOWER", (525, 45), (0.05, 0.3)),
                    ("icons/store_relics.png", "RELICS", (207, 44), (0.1, 0.3)),
                    ("icons/store_cosmetics.png", "COSMETICS", (323, 43), (0.1, 0.3)),
                    ("buttons/store_owned.png", "OWNED", (170, 45), (0.2, 0.35)),
                ])
            tourney.return_to_game("store")
    except Exception as e:                       # noqa: BLE001
        logger.event("flow_menu_store_error", error=str(e)[:200])
        _safe_home(flow)

    # -- Guild
    try:
        frame = flow.grab()
        if flow.on_home(frame):
            hit = tourney.find(frame, "home/tile_guild.png")
            if hit:
                flow.tap(hit[0][0], hit[0][1], "open guild")
                f, ok = flow.poll(lambda f: has_text(_lines_text(f), "GUILD"), timeout=6.0)
                if ok:
                    _menu_capture(flow, [
                        ("icons/guild_coin.png", "COIN", (66, 66), (0.3, 0.5)),
                    ])
                tourney.return_to_game("guild")
    except Exception as e:                       # noqa: BLE001
        logger.event("flow_menu_guild_error", error=str(e)[:200])
        _safe_home(flow)


def _safe_home(flow: _Flow) -> None:
    from interactions import tourney
    try:
        tourney.ensure_home()
    except Exception:                            # noqa: BLE001
        pass


# ---------------------------------------------------------------- entry point

def run_flows(flow_or_scanner, *, do_menus: bool = True, do_battle: bool = True,
              do_uw_lottery: bool = False) -> None:
    """Run the consented action-capture flows as a starter-scan stage. Each is
    isolated so one failing leaves the others and the rest of the scan intact.
    Accepts a Scanner or an already-built _Flow (tests).

    `do_uw_lottery` is OFF by default: the unowned Ultimate Weapon labels are not
    hunted in setup (that lottery can block a long time), they are captured
    opportunistically during normal Tier-1 play - see capture_battle_flow and
    capture_missing_uw_labels. Pass True to force the in-setup lottery."""
    from runtime import logger
    flow = flow_or_scanner if isinstance(flow_or_scanner, _Flow) else _Flow(flow_or_scanner)
    logger.event("flow_capture", stage="begin", menus=do_menus, battle=do_battle,
                 uw_lottery=do_uw_lottery)
    if do_menus:
        try:
            flow.progress("Capturing event / store / guild controls")
            capture_menu_extras(flow)
        except Exception as e:                   # noqa: BLE001
            logger.event("flow_menu_error", error=str(e)[:200])
            _safe_home(flow)
    if do_battle:
        try:
            capture_battle_flow(flow, do_lottery=do_uw_lottery)
        except Exception as e:                   # noqa: BLE001
            logger.event("flow_battle_error", error=str(e)[:200])
            _safe_home(flow)
    logger.event("flow_capture", stage="done")
