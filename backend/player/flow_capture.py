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
  * It ABORTS INTO SAFETY. An unrecognised screen stops setup and revokes run
    ownership; it never blind-taps. Successful flows return to verified Home.

Everything it writes is the account's own local pixels, declared writable in the
bootstrap manifest's dynamic_targets and gated through calibrate.write_template
like every other captured template; nothing ships (CLAUDE.md rules 6, 8).
"""
import time
import uuid
import inspect
import math
import hashlib
from contextlib import contextmanager
from functools import wraps

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

def _read(frame, scale: float = 2.0, **kwargs):
    from vision import textocr
    return textocr.read_lines(frame, scale, **kwargs)


def _lines_text(frame, scale: float = 2.0):
    return [t for _y, _x, t in _read(frame, scale)]


class _Flow:
    """Thin adapter over the bootstrap Scanner: its device handles, its
    progress/skip bookkeeping, its Calibration for verified writes."""

    def __init__(self, scanner):
        self.s = scanner
        self.cal = scanner.cal
        from runtime.operations import environment_operation
        _, operation_id = environment_operation()
        # Setup battles are recorded under an accepted ledger operation so the
        # sealed outcome is what every later run/collector admission reads.
        self.ledger_operation = operation_id is not None
        self.operation_id = operation_id or uuid.uuid4().hex
        # Persisted records are evidence, never authority to adopt an old run.
        self._owned_battle_id = None
        self._sprint_proof = None
        self._ocr_budget = None
        self._frame_observed_at = None

    def ocr_deadline(self, deadline=None):
        """Effective absolute read deadline; adapters may add their phase limit."""
        limits = [value for value in (deadline, self._ocr_budget) if value is not None]
        if any(not math.isfinite(value) for value in limits):
            raise ValueError('OCR deadline must be finite')
        return min(limits) if limits else None

    def read_remaining(self, deadline=None):
        self.s.check_stop()
        limit = self.ocr_deadline(deadline)
        if limit is None:
            return None
        remaining = limit - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Flow read deadline expired; no further I/O')
        return remaining

    @contextmanager
    def read_budget(self, deadline):
        previous = self._ocr_budget
        self._ocr_budget = self.ocr_deadline(deadline)
        try:
            yield
        finally:
            self._ocr_budget = previous

    def read_lines(self, frame, scale=2.0, *, deadline=None):
        limit = self.ocr_deadline(deadline)
        remaining = self.read_remaining(limit)
        if remaining is None:
            return _read(frame, scale)
        rows = _read(frame, scale, timeout=remaining, raise_on_error=True)
        self.read_remaining(limit)
        return rows

    def lines_text(self, frame, scale=2.0, *, deadline=None):
        return [text for _y, _x, text in self.read_lines(frame, scale, deadline=deadline)]

    def read_texts(self, crops, scales=(2.0, 3.0), *, timeout=None,
                   deadline=None, raise_on_error=True):
        from vision import textocr
        limit = self.ocr_deadline(deadline)
        remaining = self.read_remaining(limit)
        kwargs = {'scales': scales, 'raise_on_error': raise_on_error or limit is not None}
        if remaining is not None:
            kwargs['timeout'] = min(timeout, remaining) if timeout is not None else remaining
        elif timeout is not None:
            kwargs['timeout'] = timeout
        values = textocr.read_texts(crops, **kwargs)
        self.read_remaining(limit)
        return values

    def read_text(self, frame, scales=(2.0, 3.0, 4.0)):
        from vision import textocr
        if self.ocr_deadline() is None:
            return textocr.read_text(frame, scales=scales)
        return self.read_texts([frame], scales=scales)[0]

    def read_tier(self, frame):
        if self.ocr_deadline() is None:
            return read_tier(frame)
        return read_tier(frame, read_lines=self.read_lines)

    # -- device
    def grab(self):
        limit = self.ocr_deadline()
        self.read_remaining(limit)
        self._frame_observed_at = time.monotonic()
        # Legacy injected readers remain valid without a deadline. A bounded
        # capture must propagate its limit rather than start unbounded I/O.
        if limit is not None:
            parameters = inspect.signature(self.s.grab).parameters
            supports_deadline = ('deadline' in parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()))
            if not supports_deadline:
                raise RuntimeError('Bounded capture reader requires deadline support')
        kwargs = {'deadline': limit} if limit is not None else {}
        frame = self.s.grab(**kwargs)
        self.read_remaining(limit)
        return frame

    def tap(self, x, y, reason):
        self.s.check_stop()
        if reason == "open intro sprint dialog":
            point, deadline = self.sprint_dispatch(None, (x, y))
            return self.s.tap(*point, reason=f"flow: {reason}", instant=True,
                              deadline=deadline)
        return self.s.tap(int(x), int(y), reason=f"flow: {reason}", instant=True)

    def sprint_dispatch(self, frame, requested_point, *, grab=None):
        """Consume stable Sprint proof and recheck a fresh frame before input.

        Input adapters must call this for the Sprint-open intent and carry the
        returned monotonic deadline through their own last dispatch guard.
        An adapter may supply its guarded raw reader to avoid repeating OCR
        inside grab(); capture and validation still share the same time budget.
        """
        self.s.check_stop()
        proof = self._sprint_proof
        if proof is None or proof[2] != self._owned_battle_id:
            raise RuntimeError('No stable Intro Sprint control proof; no input')
        self._sprint_proof = None  # any dispatch attempt consumes this proof
        if not _owns_setup_battle(self):
            raise RuntimeError('Intro Sprint lost active setup battle ownership; no input')
        if tuple(requested_point) != proof[1]:
            raise RuntimeError('Intro Sprint requested point changed; no input')
        scene = proof[3]
        if (scene['operation_id'] != self.operation_id or scene['battle_id'] != self._owned_battle_id
                or (frame is not None and not _same_sprint_scene(scene, frame))):
            raise RuntimeError('Intro Sprint scene changed before dispatch; no input')
        captured_at = time.monotonic()
        limits = [captured_at + 2.0, scene['expires_at']]
        outer = self.ocr_deadline()
        if outer is not None:
            limits.append(outer)
        deadline = min(limits)
        with self.read_budget(deadline):
            self.read_remaining(deadline)
            reader = grab or self.grab
            parameters = inspect.signature(reader).parameters
            kwargs = {'deadline': deadline} if 'deadline' in parameters else {}
            fresh = reader(**kwargs)
            self.read_remaining(deadline)
        # Classification already established the owned scene. This final fence
        # is pixels only: no OCR, wave fallback or template lookup can block it.
        if not _same_sprint_scene(scene, fresh):
            raise RuntimeError('Intro Sprint scene changed before dispatch; no input')
        current = _sprint_glyph(fresh)
        if not _same_sprint_control(proof[:2], current):
            raise RuntimeError('Intro Sprint control changed before dispatch; no input')
        self.s.check_stop()
        if time.monotonic() >= deadline:
            raise TimeoutError('Fresh Intro Sprint proof expired; no input')
        return current[1], deadline

    def pause(self, seconds):
        self.s.pause(seconds)

    def progress(self, message, **extra):
        self.s.progress(message, **extra)

    def skip(self, target, reason, *, optional=False):
        """Record a capture that did not happen. `optional` marks a target the
        game only shows sometimes (a CLAIM button with no finished quest): it
        is listed for the person but never turns the scan into needs_attention,
        and a runner captures it later when the screen shows it. Never for a
        target some run kind is BLOCKED on (readiness): that gap is the scan's
        to report. A scan that tries a target again (a setup surrenders up to
        four times) lists it once: an entry for the target stands unless it is
        optional and this one is not. flow_skip is logged either way."""
        entry = {"target": target, "reason": reason}
        if optional:
            entry["optional"] = True
        if not any(s.get("target") == target and (optional or not s.get("optional")) for s in self.s.skipped):
            self.s.skipped.append(entry)
        from runtime import logger
        logger.event("flow_skip", target=target, reason=reason, optional=optional)

    def withdraw_skips(self, rel):
        """Drop every skip this scan filed for `rel`: a later pass has cut it, so
        the image is no longer missing and the rows would only hold the scan in
        needs_attention (and tell the person a cut image is still absent)."""
        self.s.skipped[:] = [s for s in self.s.skipped if s.get("target") != rel]

    # -- guards
    def frame(self):
        return self.grab()

    def wave(self, frame):
        from vision import wave_reader
        try:
            wave = wave_reader.read_wave(frame)
            if wave is not None:
                return wave
        except RuntimeError:
            pass
        # No digit font yet (a fresh account): the wave is simply
        # UNREADABLE here, not an error. Capture must proceed - the digits
        # are cut from the live counter by capture_hud_digits - rather than
        # let the whole battle stage abort before it can bootstrap them.
        import re
        from settings import CONFIG
        x,y,w,h = CONFIG['rois']['wave_box']
        text = ' '.join(t for _,_,t in self.read_lines(frame[y:y+h,x:x+w], 2))
        from player.battle_capture import digit_glyphs
        crop = frame[y:y+h,x:x+w]
        if not digit_glyphs(crop, text):
            context = ' '.join(t for _,_,t in self.read_lines(frame[max(0,y-12):y+h+12,max(0,x-140):x+w], 2))
            found = re.search(r'\bWave\s*(\d+)\b', context, re.I)
            text = found.group(1) if found else ''
        return int(re.sub(r'\D','',text.replace('O','0'))) if digit_glyphs(crop,text) else None

    def on_home(self, frame):
        from player.bootstrap import screen_matches
        return screen_matches(frame, self.read_lines(frame, 1), "home")

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
        return has_text(self.lines_text(frame), "GAME STATS")

    # -- capture
    def locate(self, frame, region, text, *, scale=2, cutoff=0.72):
        from player import battle_capture as bc
        return bc.locate_text(frame, region, text,
                              self.read_lines, scale=scale, cutoff=cutoff)

    def capture(self, rel, text, region, size, *, name=None, anchor=(0.12, 0.30),
                frame=None, extra=None, optional=False):
        """Find `text` in `region`, cut a `size` template around it, verify and
        write it through Calibration.cut (bootstrap allowlist). Returns the
        cut entry dict, or None when the text is not on screen / not verified.
        `optional`: the text is only on screen sometimes (see skip)."""
        from player import battle_capture as bc
        frame = self.grab() if frame is None else frame
        crop = bc.capture_by_text(frame, text, region, size,
                                  self.read_lines, anchor=anchor)
        if crop is None:
            self.skip(rel, f"'{text}' not visible where expected"
                      + (" (only shown while something is claimable)" if optional else ""),
                      optional=optional)
            return None
        entry = self.cal.cut("bootstrap", rel, crop, frame, name or text,
                             dict(extra or {}, source="flow_ocr"))
        if not entry.get("verified"):
            self.skip(rel, entry.get("reason") or "capture not verified")
            return None
        self.withdraw_skips(rel)
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
        self.withdraw_skips(rel)
        self.progress(f"Captured {rel}")
        return entry

    def poll(self, predicate, *, timeout=8.0, interval=0.4, strict=False):
        """Grab until predicate(frame) is truthy; return (frame, value) or the
        last (frame, None). check_stop each pass."""
        local = time.monotonic() + timeout
        outer = self.ocr_deadline()
        bounded = strict or outer is not None
        deadline = min(local, outer) if outer is not None else local
        with self.read_budget(deadline if bounded else None):
            frame = self.grab()
            while True:
                self.s.check_stop()
                value = predicate(frame)
                if bounded:
                    self.read_remaining(deadline)
                if value:
                    return frame, value
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return frame, None
                self.pause(min(interval, remaining))
                if time.monotonic() >= deadline:
                    return frame, None
                frame = self.grab()


# ---------------------------------------------------------------- regions
# Native 1080x2560 bands. Generous on purpose: locate() searches inside them by
# OCR and returns None (never a blind tap) when the text is absent.
from player.bootstrap_layout import manifest, reference_manifest

R_HOME_BATTLE = tuple(manifest()["flow_regions"]["home_battle"])     # the Home BATTLE button (~621,2057)
R_SPRINT = tuple(manifest()["flow_regions"]["sprint"])             # intro-sprint icon rail (detect.SPRINT_BAND)
R_DIALOG = tuple(manifest()["flow_regions"]["dialog"])           # dialog titles ~y1024/1083, buttons ~y1377/1437
R_EXIT_BTN = tuple(manifest()["flow_regions"]["exit_btn"])         # in-run side menu (END ROUND / EXIT BATTLE ~888,786)
R_STATS_HDR = tuple(manifest()["flow_regions"]["stats_hdr"])         # GAME STATS header (~333,752)
R_STATS_BTNS = tuple(manifest()["flow_regions"]["stats_btns"])     # MORE STATS/PERKS ~y1343, HOME ~y1721
R_UW_PANEL = tuple(manifest()["flow_regions"]["uw_panel"])         # in-run upgrade panel (CONFIG upgrade_panel)
R_FULL = tuple(manifest()["flow_regions"]["full"])

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
R_STATS_ROW = tuple(manifest()["flow_regions"]["stats_row"])     # MORE STATS / PERKS band on the GAME STATS screen
R_DIALOG_ROW = tuple(manifest()["flow_regions"]["dialog_row"])    # No / Yes band on a confirm dialog
R_HOME_ROW = tuple(manifest()["flow_regions"]["home_row"])      # RETRY / HOME band on the GAME STATS screen


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
    """One clock ring with hands and an upper-right red X in its native slot.

    Currency highlights and battle effects can be white with red nearby. They
    are not control identity. Multiple complete candidates are ambiguity, never
    absence; the dialog remains the final proof before any template is written.
    """
    geometry = manifest()
    x, y, w, h = geometry['flow_regions']['sprint']
    sx, sy, sw, sh = geometry['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    reference = reference_manifest()['interface']['screens']['battle']['controls']['intro_sprint']['reference_rect']
    scale = sh / reference[3]
    if frame.shape[:2] != (geometry['layout']['height'], geometry['layout']['width']):
        raise RuntimeError('Intro Sprint frame geometry changed; no input')
    sub = frame[y:y + h, x:x + w]
    b, g, r = (sub[:, :, 0].astype(int), sub[:, :, 1].astype(int),
               sub[:, :, 2].astype(int))
    white = (sub.min(axis=2) > 185).astype("uint8") * 255
    red = (r > 140) & (r - g > 60) & (r - b > 60)
    cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in cnts:
        bx, by, bw_, bh_ = cv2.boundingRect(c)
        if not (30 * scale <= bw_ <= 100 * scale and 30 * scale <= bh_ <= 100 * scale):
            continue
        cx, cy = bx + bw_ / 2, by + bh_ - bw_ / 2
        if not (sx <= x + cx < sx + sw and sy <= y + cy < sy + sh):
            continue
        if not .75 <= bw_ / bh_ <= 1.3:
            continue
        # A clock's circular outline must cover most angular sectors. A gem's
        # diamond edges and isolated effect fragments do not form that ring.
        yy, xx = np.indices(white.shape)
        radius = np.hypot(xx - cx, yy - cy) / bw_
        ring = (white > 0) & (radius >= .42) & (radius <= .52)
        angles = ((np.arctan2(yy - cy, xx - cx) + np.pi) * 16 / np.pi).astype(int) % 32
        if len(np.unique(angles[ring])) < 28:
            continue
        # Hands or a centre pin distinguish a clock from an empty effect ring.
        if np.count_nonzero((white > 0) & (radius < .22)) < 3 * scale * scale:
            continue
        # A hollow face with sparse hands is required, not a filled disk.
        interior = radius < .35
        if np.mean((white > 0)[interior]) > .65:
            continue
        ex0, ey0 = max(0, int(bx + bw_ * .55)), max(0, int(by - bh_ * .25))
        ex1, ey1 = min(w, int(bx + bw_ * 1.3)), min(h, int(by + bh_ * .55))
        marks, _ = cv2.findContours(red[ey0:ey1, ex0:ex1].astype('uint8'),
                                    cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        crosses = []
        for mark in marks:
            mx, my, mw, mh = cv2.boundingRect(mark)
            mask = red[ey0+my:ey0+my+mh, ex0+mx:ex0+mx+mw]
            if not (.65 <= mw / mh <= 1.5 and mask.sum() >= 20 * scale * scale):
                continue
            ry, rx = np.nonzero(mask)
            u, v = rx / max(1, mw-1), ry / max(1, mh-1)
            diagonal = (np.abs(u-v) <= .2) | (np.abs(u+v-1) <= .2)
            arms = [(u < .35) & (v < .35), (u > .65) & (v < .35),
                    (u < .35) & (v > .65), (u > .65) & (v > .65)]
            if diagonal.mean() >= .8 and all(arm.sum() >= .04 * len(u) for arm in arms):
                crosses.append((ex0+mx, ey0+my, mw, mh))
        if not crosses:
            continue
        if len(crosses) != 1:
            raise RuntimeError('Ambiguous Intro Sprint red X; no input')
        mx, my, mw, mh = crosses[0]
        pad = max(1, round(4 * scale))
        x0, y0 = max(0, min(bx, mx)-pad), max(0, min(by, my)-pad)
        x1, y1 = min(w, max(bx+bw_, mx+mw)+pad), min(h, max(by+bh_, my+mh)+pad)
        candidates.append((sub[y0:y1, x0:x1].copy(), (x + round(cx), y + round(cy))))
    if not candidates:
        return None, None
    if len(candidates) != 1:
        raise RuntimeError('Ambiguous Intro Sprint controls; no input')
    return candidates[0]


def _same_sprint_control(first, second):
    a, point_a = first
    b, point_b = second
    if point_a is None or point_b is None or point_a != point_b or a.shape != b.shape:
        return False
    # Compare the glyph, not the animated background behind the rail.
    def ink(crop):
        red = crop[:, :, 2].astype(int)
        return ((crop.min(axis=2) > 185)
                | ((red > 140) & (red-crop[:, :, 1] > 60) & (red-crop[:, :, 0] > 60)))

    mask = ink(a) | ink(b)
    if not mask.any():
        return False
    return bool(np.mean(np.max(np.abs(a.astype(int)-b.astype(int)), axis=2)[mask] <= 25) >= .97)


def _require_sprint_battle(flow, frame, *, allow_results=False):
    flow.s.check_stop()
    if not _owns_setup_battle(flow):
        raise RuntimeError('Intro Sprint requires this operation\'s active setup battle')
    texts = flow.lines_text(frame)
    if flow.in_tournament(frame) or has_text(texts, 'TOURNAMENT') or has_text(texts, 'ABORT'):
        raise RuntimeError('Intro Sprint requires a recognized normal battle; no input')
    if has_text(texts, 'GAME STATS'):
        if allow_results:
            flow.read_remaining()
            return {'kind': 'results'}
        raise RuntimeError('Intro Sprint requires a recognized normal battle; no input')
    if flow.wave(frame) is None or exit_label(texts) or exit_dialog_kind(texts):
        raise RuntimeError('Intro Sprint requires a recognized normal battle; no input')
    flow.read_remaining()
    geometry = manifest()
    if frame.shape[:2] != (geometry['layout']['height'], geometry['layout']['width']):
        raise RuntimeError('Intro Sprint scene geometry changed; no input')
    rectangles = [target['rect'] for target in geometry['hud']['battle']['targets']]
    rectangles.append(geometry['interface']['screens']['battle']['controls']['attack']['reference_rect'])
    anchors = []
    for rect in rectangles:
        x, y, w, h = rect
        crop = frame[y:y+h, x:x+w]
        if crop.shape[:2] != (h, w) or crop.size == 0:
            raise RuntimeError('Intro Sprint scene anchor is unreadable; no input')
        anchors.append((tuple(rect), crop.copy()))
    flow.s.check_stop()
    return {'kind': 'battle', 'operation_id': flow.operation_id, 'battle_id': flow._owned_battle_id,
            'frame_sha256': hashlib.sha256(frame.tobytes()).hexdigest(), 'shape': frame.shape,
            'observed_at': flow._frame_observed_at, 'expires_at': flow.ocr_deadline(),
            'anchors': anchors}


def _same_sprint_scene(scene, frame):
    if frame.shape != scene['shape'] or scene['kind'] != 'battle' or not scene['anchors']:
        return False
    for (x, y, w, h), old in scene['anchors']:
        fresh = frame[y:y+h, x:x+w]
        if old.shape != fresh.shape or old.std() < 2 or fresh.std() < 2:
            return False
        score = float(cv2.matchTemplate(fresh, old, cv2.TM_CCOEFF_NORMED)[0, 0])
        if not math.isfinite(score) or score < .97 or not .9 <= fresh.mean() / old.mean() <= 1.1:
            return False
    return True


def capture_intro_sprint(flow: _Flow) -> None:
    """Tap the intro-sprint icon, capture the END INTRO SPRINT EARLY dialog and
    its confirm, then end the sprint so the owned run can be surrendered.

    No icon is an optional capture gap. A missing dialog after a proven icon
    tap stops setup before any further input."""
    previous = None
    scene = None
    flow._sprint_proof = None
    finished_without_sprint = object()

    def stable_control(frame):
        nonlocal previous, scene
        observed = _require_sprint_battle(flow, frame, allow_results=previous is None)
        if observed['kind'] == 'results':
            return finished_without_sprint
        current = _sprint_glyph(frame)
        if previous is not None:
            if not _same_sprint_control(previous, current):
                raise RuntimeError('Intro Sprint identity changed during observation; no input')
            scene = observed
            return current
        if current[1] is not None:
            previous = current
        return None

    frame, glyph = flow.poll(stable_control, timeout=10.0, interval=0.5, strict=True)
    if glyph is finished_without_sprint:
        _skip_missing(flow, "home/intro_sprint_dialog.png",
                      "Owned run reached GAME STATS without an observed Intro Sprint", optional=True)
        return
    icon_crop, centre = glyph or (None, None)
    icon_frame = frame                       # the glyph is proven on THIS frame
    if centre is None:
        if previous is not None:
            raise RuntimeError('Intro Sprint control did not settle before timeout; no input')
        _skip_missing(flow, "home/intro_sprint_dialog.png",
                      "Intro Sprint was not observed within 10 seconds of this run; "
                      "availability is unverified and no cards were changed", optional=True)
        return
    flow._sprint_proof = (icon_crop, centre, flow._owned_battle_id, scene)
    flow.progress("Intro sprint: opening its dialog")
    flow.tap(centre[0], centre[1], "open intro sprint dialog")
    frame, ok = flow.poll(lambda f: has_text(flow.lines_text(f), "INTRO SPRINT")
                          or has_text(flow.lines_text(f), "END INTRO"), timeout=5.0, strict=True)
    if not ok:
        flow.skip("home/intro_sprint_dialog.png",
                  "tapped the sprint rail but no dialog appeared")
        raise RuntimeError('Intro Sprint dialog was not verified after its tap; no further input sent')
    # the tap proved the glyph is the icon: now it may be written (self-matched
    # against the frame it was read from - the icon stays visible behind the
    # dialog, but verify against its own frame)
    flow.cut_crop("icons/intro_sprint.png", icon_crop, icon_frame, "Intro Sprint",
                  {"where": "left rail"})
    with flow.read_budget(time.monotonic() + 5.0):
        flow.capture("home/intro_sprint_dialog.png", "END INTRO SPRINT", R_DIALOG,
                     (560, 130), name="End Intro Sprint Early", frame=frame,
                     # Keep the complete two-line title. The old 84px left /
                     # 52px top padding clipped its right and bottom edges.
                     anchor=(0.015, 0.04))
    # Yes is the RIGHT button of the dialog's two-button row - captured by
    # position (sibling of No, unique=False), not by OCR. END the sprint (tap
    # Yes): a cancelled sprint fast-forwards waves for minutes and a round CANNOT
    # be ended mid-sprint, so a calibration run that only cancelled could never
    # be surrendered (seen live: it ran to wave 300+ and end_round found no exit
    # button). After Yes, wait for the stopwatch to clear so the run is normal.
    clear_frames = 0

    def settled_clear(frame):
        nonlocal clear_frames
        clear_frames = clear_frames + 1 if _sprint_finished(flow, frame) else 0
        return clear_frames >= 2

    _left, right = _row_buttons(frame, *R_DIALOG_ROW)
    if right:
        flow.cut_crop("home/intro_sprint_yes.png", _button_crop(frame, right), frame,
                      "Intro sprint yes", {"side": "right"}, unique=False)
        flow.tap(right[0] + right[2] // 2, right[1] + right[3] // 2,
                 "end intro sprint early")
        _, ended = flow.poll(settled_clear, timeout=10.0, strict=True)
        if not ended:
            raise RuntimeError('Could not verify the battle after ending Intro Sprint')
    else:
        with flow.read_budget(time.monotonic() + 5.0):
            yes = flow.locate(frame, R_DIALOG, "YES")
        if yes:
            flow.tap(yes[0], yes[1], "end intro sprint early")
            _, ended = flow.poll(settled_clear, timeout=10.0, strict=True)
            if not ended:
                raise RuntimeError('Could not verify the battle after ending Intro Sprint')
        flow.skip("home/intro_sprint_yes.png", "dialog button row not found")
        if not yes:
            raise RuntimeError('Intro Sprint confirmation could not be located; no further input sent')


def _sprint_finished(flow, frame):
    scene = _require_sprint_battle(flow, frame, allow_results=True)
    return scene['kind'] == 'results' or _sprint_glyph(frame)[1] is None


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
    from vision import detect
    if detect.panel_tab(frame) != 'uw':
        flow.tap(*CONFIG['tabs']['uw'], 'open Ultimate Weapons')
        flow.pause(.8)
    if detect.panel_tab(flow.grab()) != 'uw':
        flow.skip("uw/black_hole.png", "could not open the UW tab")
        raise RuntimeError('Ultimate Weapons panel was not verified after its tap')
    shopper._scroll_to_top()
    previous = None
    for page in range(6):
        flow.s.check_stop()
        frame = flow.grab()
        x,y,w,h = R_UW_PANEL
        names = {_norm(t) for _,_,t in flow.read_lines(frame[y:y+h,x:x+w], 2)}
        signature = frozenset(text for _,text,_ in UW_WEAPONS if _norm(text) in names)
        if page and signature == previous:
            break
        previous = signature
        flow.progress(f'Ultimate Weapons: reading page {page+1}')
        for rel, text, size in UW_WEAPONS:
            if text in signature:
                flow.capture(rel, text, R_UW_PANEL, size, name=text, frame=frame,
                             anchor=(0.08, 0.35))
        f1 = flow.grab(); flow.pause(.4); f2 = flow.grab()
        capture_uw_switches(getattr(flow, 'cal', None), f1, f2)
        shopper._swipe(down=True)
        flow.pause(.6)
    # every owned weapon has an ON/OFF switch: cut one of each state by its
    # artwork (not unique on screen by design)
    capture_missing_off_state(flow)
    capture_artwork_targets(flow, UW_SWITCH_TARGETS, unique=False, label="the weapon switches")
    try:
        f1 = flow.grab(); flow.pause(0.4); f2 = flow.grab()
        capture_uw_switches(getattr(flow, "cal", None), f1, f2)
    except Exception as e:                       # noqa: BLE001 - isolate
        from runtime import logger
        logger.event("flow_uw_switch_error", error=str(e)[:200])
        raise


# ---------------------------------------------------------------- end the run

def _clean_home(flow, frame) -> tuple[bool, str]:
    return can_start_battle(on_home=flow.on_home(frame), wave=flow.wave(frame),
                            side_menu=flow.side_menu(frame), game_stats=flow.game_stats(frame),
                            tournament=flow.in_tournament(frame))


def _save_setup_battle(flow, **changes) -> dict:
    from player.calibrate import _state_save, _state_load
    from runtime import logger
    battle = flow.s.state['setup_battle']
    previous = dict(battle)
    battle.update(changes)
    try:
        _state_save(flow.cal.p, flow.s.state)
        if _state_load(flow.cal.p).get('setup_battle') != battle:
            raise RuntimeError('Setup battle ownership could not be read back')
    except BaseException:
        battle.clear()
        battle.update(previous)
        raise
    logger.event('setup_battle', **battle)
    return battle


def _owns_setup_battle(flow) -> bool:
    battle = flow.s.state.get('setup_battle') or {}
    return bool(flow._owned_battle_id and battle.get('id') == flow._owned_battle_id
                and battle.get('operation_id') == flow.operation_id
                and battle.get('status') in ('active', 'ending'))


def mark_setup_unknown(flow, error) -> None:
    """Revoke input authority after interruption; persist without touching the game."""
    from player.calibrate import record_final_screen
    battle = flow.s.state.get('setup_battle') or {}
    if (flow._owned_battle_id and battle.get('id') == flow._owned_battle_id
            and battle.get('operation_id') == flow.operation_id
            and battle.get('status') != 'finished'):
        _save_setup_battle(flow, status='unknown', reason=str(error))
    record_final_screen(flow.cal.p, flow.s.state, 'unknown', verified=False, error=error)


def _setup_lifecycle(function):
    """An interrupted pass has no recovery-input path, including Stop and Abort."""
    @wraps(function)
    def guarded(flow, *args, **kwargs):
        try:
            action = {'_start_setup_run': 'setup.battle.start',
                      'end_run_and_capture': 'setup.battle.end',
                      'capture_game_stats': 'setup.battle.results'}.get(function.__name__)
            if action:
                from runtime import logger
                with logger.attempt(action, setup_operation_id=flow.operation_id,
                                    setup_battle_id=flow._owned_battle_id) as attempt:
                    result = function(flow, *args, **kwargs)
                    attempt.finish('refused' if result is False else 'verified',
                                   setup_battle_id=flow._owned_battle_id)
                    return result
            return function(flow, *args, **kwargs)
        except BaseException as error:
            mark_setup_unknown(flow, error)
            raise
    return guarded


@_setup_lifecycle
def _start_setup_run(flow, reason):
    """Own only the normal run this operation taps from freshly proven clean Home."""
    flow.s.check_stop()
    if not getattr(flow, 'ledger_operation', False):
        # Without an accepted operation the outcome would never be sealed in
        # the ledger, so an interrupted battle would be invisible to the setup
        # guard. Refuse before any frame or input.
        raise RuntimeError('No setup battle started: setup battles run only as a dashboard-accepted '
                           'operation (Full setup), so their outcome is recorded')
    frame = flow.grab()
    ok, why = _clean_home(flow, frame)
    if not ok:
        raise RuntimeError(f'No setup battle started: {why}')
    previous = flow.s.state.get('setup_battle') or {}
    if previous and previous.get('status') != 'finished':
        raise RuntimeError('An earlier setup battle has no verified finish; no further battle will start')
    point = flow.locate(frame, R_HOME_BATTLE, 'BATTLE')
    if not point:
        raise RuntimeError('Home BATTLE button not found; no setup battle started')
    flow.s.state.pop('final_screen', None)
    flow._owned_battle_id = uuid.uuid4().hex
    flow.s.state['setup_battle'] = dict(id=flow._owned_battle_id, status='starting',
        started_at=time.time(), operation_id=flow.operation_id, reason=reason)
    _save_setup_battle(flow)
    flow.tap(*point, reason)
    frame, started = flow.poll(lambda f: flow.wave(f) is not None
                               or _sprint_glyph(f)[1] is not None or flow.side_menu(f)
                               or flow.game_stats(f), timeout=25.0, interval=.6)
    if not started or flow.in_tournament(frame):
        raise RuntimeError('Setup battle start was not verified as a normal run; no further input sent')
    _save_setup_battle(flow, status='active')
    return frame


@_setup_lifecycle
def _complete_setup_battle(flow) -> bool:
    from player.calibrate import record_final_screen
    flow.s.check_stop()
    frame = flow.grab()
    ok, why = _clean_home(flow, frame)
    if not _owns_setup_battle(flow) or not ok:
        raise RuntimeError(f'Setup battle final Home is unverified: {why or "run ownership lost"}')
    # record_final_screen persists and reads back this exact operation's proof.
    record_final_screen(flow.cal.p, flow.s.state, 'home', verified=True)
    _save_setup_battle(flow, status='finished', finished_at=time.time())
    return True


@_setup_lifecycle
def _finish_setup_run(flow) -> bool:
    if not _owns_setup_battle(flow):
        raise RuntimeError('Setup cannot finish a run it did not start in this operation')
    frame = flow.grab()
    if flow.in_tournament(frame):
        raise RuntimeError('A tournament is on screen; setup will send no further input')
    if _clean_home(flow, frame)[0]:
        return _complete_setup_battle(flow)
    if not flow.game_stats(frame) and _sprint_glyph(frame)[1] is not None:
        capture_intro_sprint(flow)
        frame = flow.grab()
    reached = capture_game_stats(flow) if flow.game_stats(frame) else end_run_and_capture(flow)
    if not reached:
        raise RuntimeError('Setup battle did not return Home; no further battle will start')
    return True

def capture_missing_off_state(flow):
    """Exercise a verified switch only in the setup-owned battle, then restore."""
    import settings
    if settings.template_path('uw/toggle_off.png').exists():
        return
    from interactions import shopper
    shopper._scroll_to_top()
    labels = {rel: cv2.imread(str(settings.template_path(rel)))
              for rel, _, _ in UW_WEAPONS if settings.template_path(rel).exists()}
    first = find_uw_switches(flow.grab(), labels)
    flow.pause(.3)
    second = find_uw_switches(flow.grab(), labels)
    candidates = [row for row in first if row[0] == 'on' and row in second]
    if not candidates:
        return
    _, (x,y,w,h), name = candidates[0]
    flow.progress('Verifying weapon OFF state, then restoring ON')
    flow.tap(x+w//2,y+h//2,'setup: capture OFF switch')
    flow.pause(.5)
    a=flow.grab(); flow.pause(.3); b=flow.grab()
    capture_uw_switches(flow.cal,a,b)
    rows=find_uw_switches(flow.grab(),labels)
    off=next((r for state,r,n in rows if n==name and state=='off'),None)
    if off:
        x,y,w,h=off
        flow.tap(x+w//2,y+h//2,'setup: restore weapon ON')
        flow.pause(.5)
    if not any(state=='on' and n==name for state,_,n in find_uw_switches(flow.grab(),labels)):
        raise RuntimeError('Could not confirm weapon restored ON; inspect equipment before farming')

def _skip_missing(flow: _Flow, rel: str, reason: str, *, optional: bool = False) -> None:
    """flow.skip(rel, ...) unless the image is already on disk (nothing is
    missing). flow.skip lists the image once however many passes file it, and
    a later pass that cuts it withdraws the row (flow.withdraw_skips)."""
    if _have_templates([rel]):
        return
    flow.skip(rel, reason, optional=optional)


def _skip_end_round_yes(flow: _Flow, why: str) -> None:
    """Record that home/end_round_yes.png was not cut, as a NON-optional skip
    (readiness blocks every surrendering run on it), through _skip_missing."""
    _skip_missing(flow, "home/end_round_yes.png",
                  f"{why}. Shard, quest and max-wave runs stay blocked until it exists; a later setup "
                  "cuts it only from an END ROUND confirm its own run shows")


@_setup_lifecycle
def end_run_and_capture(flow: _Flow) -> bool:
    """End only this operation's setup battle; unknown screens revoke authority."""
    if not _owns_setup_battle(flow):
        mark_setup_unknown(flow, 'Refused to end an unowned or interrupted setup battle')
        return False
    flow.s.check_stop()
    frame = flow.grab()
    if (flow.in_tournament(frame) or flow.on_home(frame)
            or not (flow.game_stats(frame) or flow.wave(frame) is not None
                    or flow.side_menu(frame) or exit_label(flow.lines_text(frame))
                    or _sprint_glyph(frame)[1] is not None)):
        mark_setup_unknown(flow, 'Refused to end a tournament or unrecognized battle screen')
        return False
    _save_setup_battle(flow, status='ending', end_started_at=time.time())
    if flow.game_stats(frame):
        reached = capture_game_stats(flow)
    else:
        reached = _end_owned_run_and_capture(flow, frame)
    if not reached:
        mark_setup_unknown(flow, 'Could not verify Home after ending the setup battle')
    return bool(reached)


def _end_owned_run_and_capture(flow: _Flow, frame) -> bool:
    """Surrender the calibration run, capturing the exit dialog, GAME STATS
    screen and reward skip on the way to Home. Returns True on Home.

    This is a template-free twin of tourney.end_round, driven by OCR because
    the very templates end_round matches on are what it is capturing. It keeps
    end_round's two load-bearing rules: refuse the whole thing if a tournament
    run is on screen, and answer Surrender (never Go Home) on the exit dialog.
    """
    from settings import CONFIG
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
    label = exit_label(flow.lines_text(frame))
    for _attempt in range(4):
        if label:
            break
        if (flow.in_tournament(frame) or flow.side_menu(frame)
                or flow.game_stats(frame) or flow.on_home(frame)
                or (flow.wave(frame) is None and _sprint_glyph(frame)[1] is None)):
            return False
        flow.tap(tog[0], tog[1], "open side menu")
        frame, label = flow.poll(lambda f: exit_label(flow.lines_text(f)), timeout=4.0)
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
        # Reuse the full-frame label evidence rather than losing the same
        # text when Windows OCR segments the smaller region differently.
        for y, x, text in flow.read_lines(frame, 2):
            if _norm(text) == label and R_EXIT_BTN[0] <= x/2 < R_EXIT_BTN[0]+R_EXIT_BTN[2] and R_EXIT_BTN[1] <= y/2 < R_EXIT_BTN[1]+R_EXIT_BTN[3]:
                exit_pt = (int(x/2)+20, int(y/2)+12)
                break
    if not exit_pt:
        flow.skip("buttons/end_round.png", f"{label} vanished before tapping")
        return False
    if label == 'EXIT BATTLE':
        # The full-frame text proved this control. Keep a native label crop
        # even if OCR could not resegment it in the narrower search band.
        x, y = max(0, exit_pt[0]-26), max(0, exit_pt[1]-18)
        flow.cut_crop('buttons/exit_battle.png', frame[y:y+50,x:x+190].copy(), frame,
                      'Exit battle', {'rect':[x,y,190,50], 'screen':'battle_menu'})
        _skip_missing(flow, "buttons/end_round.png", "the side menu showed EXIT BATTLE, not END ROUND", optional=True)
    flow.tap(exit_pt[0], exit_pt[1], f"tap {label}")

    # the confirm dialog: capture it, then confirm. The two buttons are captured
    # by POSITION (Windows OCR reads their faces only intermittently). Which side
    # confirms differs by variant: Surrender is the LEFT button of the EXIT
    # BATTLE dialog (Go Home is right and only HIDES the run); Yes is the RIGHT
    # button of the END ROUND dialog.
    frame, kind = flow.poll(lambda f: exit_dialog_kind(flow.lines_text(f)), timeout=8.0)
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
        # the control that could not be found is the missing image - unless that
        # image is on disk, when only this dialog went unanswered (the event below
        # and the caller's "did not return Home" error are then the record)
        from runtime import logger
        logger.event("flow_confirm_not_locatable", kind=kind)
        if kind == "surrender":
            _skip_missing(flow, "home/surrender.png", "surrender confirm button not locatable")
        else:
            _skip_end_round_yes(flow, "end_round confirm button not locatable")
        return False
    flow.tap(confirm[0], confirm[1], f"confirm exit ({kind})")
    if kind == "surrender" and not left:
        _skip_missing(flow, "home/surrender.png", "the Surrender button was found by text only, "
                                                  "so no image was cut")
    if kind == "end_round" and not right:
        # tapped by its text, so no template was cut from it
        _skip_end_round_yes(flow, "the END ROUND confirm's Yes button was found by text only, "
                                  "so no image was cut")

    # Surrender is followed by a SECOND confirm - "END ROUND / Are you sure you
    # want to end the round?" (No / Yes) - the same dialog END ROUND opens
    # directly while the intro sprint still runs (frames 2026-09-08). Both
    # flow runs before this missed it: they polled for GAME STATS, the top-tier
    # tower died inside the poll and the stats came up by themselves, so
    # home/end_round_dialog.png was never cut and the Yes never tapped.
    if kind == "surrender":
        def _next(f):
            t = flow.lines_text(f)
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
            if not right:
                _skip_end_round_yes(flow, "the END ROUND confirm after Surrender came up but its Yes button "
                                          + ("was found by text only, so no image was cut" if yes
                                             else "was not locatable"))
        else:
            # No END ROUND confirm was recognised: record why the two were not
            # cut. On a timeout, save the frame - an END ROUND confirm OCR
            # missed and a slow end screen both look like this to the poll.
            if nxt:
                why = "the run's end screen came up (GAME STATS / SKIP)"
            else:
                from runtime import logger
                why = f"nothing recognised within 6 s, frame {logger.shot(frame, 'flow_no_end_round_confirm')}"
            _skip_missing(flow, "home/end_round_dialog.png", f"no END ROUND confirm after Surrender: {why}", optional=True)
            _skip_end_round_yes(flow, f"no END ROUND confirm after Surrender: {why}")

    # a reward animation can sit in front of the stats screen
    frame, _ = flow.poll(lambda f: has_text(flow.lines_text(f), "GAME STATS")
                        or has_text(flow.lines_text(f), "SKIP"), timeout=10.0)
    if has_text(flow.lines_text(frame), "SKIP") and not flow.game_stats(frame):
        flow.capture("buttons/reward_skip.png", "SKIP", R_FULL, (253, 102),
                     name="Reward skip", frame=frame, anchor=(0.2, 0.35))
        skip_pt = flow.locate(frame, R_FULL, "SKIP")
        if skip_pt:
            flow.tap(skip_pt[0], skip_pt[1], "reward skip")

    return capture_game_stats(flow)


@_setup_lifecycle
def capture_game_stats(flow: _Flow) -> bool:
    if not _owns_setup_battle(flow):
        mark_setup_unknown(flow, 'Refused GAME STATS navigation for an unowned setup battle')
        return False
    reached = _capture_owned_game_stats(flow)
    if reached:
        return _complete_setup_battle(flow)
    mark_setup_unknown(flow, 'GAME STATS did not return to verified Home')
    return False


def _capture_owned_game_stats(flow: _Flow) -> bool:
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
        if flow.in_tournament(frame) or not flow.game_stats(frame):
            return False
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


def read_tier(frame, *, read_lines=None):
    """The Home difficulty tier as an int ('Tier 14' -> 14), or None."""
    import re
    for _y, _x, t in (read_lines or _read)(frame, 2):
        m = re.search(r"[Tt]ier\s*(\d+)", t or "")
        if m:
            return int(m.group(1))
    return None


# The '<' / '>' difficulty arrows flanking 'Tier N' on Home (measured 2026-09-07)
def tier_point(direction):
    spec = manifest()['interface']['screens']['home']['controls'][f'difficulty_{direction}']
    x,y,w,h = spec['reference_rect']
    return x+w//2, y+h//2



# The battle HUD's two bars at native 1080x2560 (config.example.yaml's wall_bar
# example; hp_bar is the shipped default). The wall bar renders ONLY for an
# account that has a wall, so a teal/purple fill there - with the HP bar lit
# beside it as proof the HUD is on screen - IS the wall. Measured 2026-09-08 on
# real frames: wall 0.65-0.68 lit teal, HP 0.75-0.78; menus, dialogs and GAME
# STATS under 0.1. Positive-only: nothing here ever records "no wall".
WALL_BAR_ROI = tuple(manifest()["flow_regions"]["wall_bar"])
HP_BAR_ROI = tuple(manifest()["flow_regions"]["hp_bar"])
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
            raise
        result[spec["rel"]] = "not_seen" if len(scanner.skipped) > before else "verified"
    from runtime import logger
    logger.event("flow_hud_targets", screen=screen, result=result)
    try:
        result.update(scanner.learned_cuts(screen, first, second))
    except Exception as e:                      # noqa: BLE001
        logger.event("flow_learned_error", screen=screen, error=str(e)[:200])
        raise
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
            raise RuntimeError('Side menu was not verified after its tap; no further input sent')
    flow.pause(0.3)
    follow = flow.grab()
    rels = [rel for rel, d in manifest().get("asset_bindings", {}).items() if d.get("screen") == "battle_menu"]
    result = {}
    try:
        result.update(scanner.verify_asset_rels([r for r in rels if r not in _have_templates(rels)], frame, follow))
    except Exception as e:                      # noqa: BLE001 - isolate
        # type and verify_asset_rels' note naming the target it raised on come
        # BEFORE the message: the log cuts at 200 characters and OpenCV
        # messages alone run past 160
        logger.event("flow_side_menu_error",
                     error=(" ".join([type(e).__name__, *getattr(e, "__notes__", [])]) + f": {e}")[:200])
        raise
    result.update(capture_hud_targets(flow, "battle_menu"))
    if was_shut:
        flow.tap(*point, "close the side menu")
        flow.pause(0.8)
        if not icon_present(flow.grab(), shut):
            logger.event("flow_side_menu", opened=True, closed=False)
            raise RuntimeError('Side menu did not close; no further input sent')
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
        raise
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
        raise
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
        frame = flow.grab()
        if not _clean_home(flow, frame)[0]:
            raise RuntimeError('Difficulty selection lost clean Home; no further input sent')
        cur = flow.read_tier(frame)
        if cur is None:
            return None
        if cur == target:
            return cur
        flow.progress(f'Selecting setup tier: {cur} → {target}')
        if cur == prev:
            stall += 1
            if stall >= 3:                     # arrow no longer moves it - capped
                return cur
        else:
            stall = 0
        prev = cur
        flow.tap(*tier_point('right' if cur < target else 'left'), f"tier {cur}->{target}")
        flow.pause(0.5)
    return flow.read_tier(flow.grab())


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


def capture_missing_end_round(frame) -> bool:
    """Runner-side opportunistic capture of the side menu's END ROUND button.

    detect.side_menu_open PROVES the menu is open by this button (EXIT BATTLE
    on low tiers, END ROUND on high ones), and every reward flow - the 8h
    quests, guild, event missions - waits for that proof. Full setup only cuts
    END ROUND on the tier it surrendered its own run at; an account farming
    a higher tier than setup saw never has it, the menu never reads open, and
    the quest flow never runs (tier-14 farm, 2026-09-12). Same rules as
    capture_missing_uw_labels: sanctioned writer, MISSING target only, never
    replaces a file; one disk stat once it exists. The caller rate-limits the
    OCR."""
    rel = "buttons/end_round.png"
    import settings
    from player import battle_capture as bc
    from runtime import logger
    try:
        if settings.template_path(rel).exists():
            return False
    except (OSError, KeyError, ValueError):
        return False
    crop = bc.capture_by_text(frame, "END ROUND", R_EXIT_BTN, (154, 45),
                              lambda c, s: _read(c, s), anchor=(0.1, 0.2))
    if crop is None:
        return False
    if bc.write_template(rel, crop) != "written":
        return False
    logger.event("end_round_captured", rel=rel, via="run_subroutine")
    return True


# no grant by this wave -> the run's perk roll is spent; reroll for a fresh one
# (flows/quest_sm.py RESTART_AT_WAVE, user-tuned 2026-08-16).
UW_REROLL_WAVE = 1000


@_setup_lifecycle
def _start_lottery_run(flow) -> bool:
    """From Home, start a NORMAL run and leave the Intro Sprint RUNNING - its
    per-wave perk roll is the whole point here (a battle pass, by contrast, ENDS
    the sprint so it can surrender). Ensures max speed so the rolls fly.
    An unverified start raises and revokes ownership."""
    from flows import shard
    _start_setup_run(flow, 'start UW lottery run')
    flow.s.check_stop()
    shard.wait_for_wave(1)
    flow.s.check_stop()
    shard.ensure_max_speed()
    return True


def _reroll_lottery_run(flow) -> bool:
    """Finish the owned run on verified Home before owning the next lottery run."""
    _finish_setup_run(flow)
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
    shopper._scroll_to_top()
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


@_setup_lifecycle
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
          no grant by wave ~1000 -> finish on verified Home, start a new run
        until every target is captured (or max_minutes, if set).

    Chrono Field is owned now, so every grant is one of the two real targets.
    `max_minutes=None` runs until both land, relying on the dashboard's stop
    (check_stop) as the escape hatch. Successful completion restores the tier;
    an interruption leaves the screen as-is."""
    import time
    from runtime import logger
    if not _uw_remaining():
        return
    if set_tier(flow, 1) != 1:
        raise RuntimeError('Could not select Tier 1; no setup lottery battle started')
    if not _start_lottery_run(flow):
        flow.skip("uw/smart_missiles.png", "could not start the Tier-1 lottery run")
        raise RuntimeError('Could not start the Tier-1 lottery run')
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
            raise RuntimeError('A tournament is on screen; lottery capture stopped')
        # tower died / between runs -> reroll to a fresh Tier-1 sprint
        if flow.game_stats(f) or _clean_home(flow, f)[0]:
            rerolls += 1
            logger.event("flow_uw_lottery", stage="restart_dead", rerolls=rerolls)
            if not _reroll_lottery_run(flow):
                raise RuntimeError('Could not verify the next setup lottery run')
            continue
        if flow.wave(f) is None and not flow.side_menu(f):
            raise RuntimeError('Lottery battle screen is unrecognized; no further input sent')
        got = _sweep_uw_for_targets(flow)
        if not _uw_remaining():
            break
        if got:                                  # this run's single grant is used
            rerolls += 1
            logger.event("flow_uw_lottery", stage="reroll_after_grant",
                         rerolls=rerolls,
                         remaining=[r for r, _, _ in _uw_remaining()])
            if not _reroll_lottery_run(flow):
                raise RuntimeError('Could not verify the next setup lottery run')
            continue
        w = flow.wave(flow.grab())
        if w is not None and w >= UW_REROLL_WAVE:
            rerolls += 1
            logger.event("flow_uw_lottery", stage="reroll", at_wave=w,
                         rerolls=rerolls,
                         remaining=[r for r, _, _ in _uw_remaining()])
            if not _reroll_lottery_run(flow):
                raise RuntimeError('Could not verify the next setup lottery run')
        else:
            flow.pause(15)                       # let the sprint roll more perks
    # Only a verified exit completes the lottery. Failure propagates with the
    # screen left as-is; no runner recovery helper may hide a failed surrender.
    _finish_setup_run(flow)
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
    def ocr(crop):
        text = flow.read_text(crop, scales=(2.0,))
        if not bc.digit_glyphs(crop, text):
            # The clipped tail of "Wave" makes Windows miss 10/20/30 at
            # 2x. Read only the number at 3x/4x, preserving native crops.
            text = flow.read_text(crop[:,10:], scales=(3.0,4.0))
        return text
    got: dict = {d: None for d in '0123456789' if settings.template_path(f'digits/{d}.png').exists()}
    written = []
    deadline = time.monotonic() + 180            # generous; stops early at 10/10
    last_progress = 0.0
    while time.monotonic() < deadline and len(got) < 10:
        flow.s.check_stop()
        f = flow.grab()
        if flow.game_stats(f):                   # the run ended - counter is gone
            break
        crop = f[y:y + h, x:x + w]
        number = ocr(crop)
        if not bc.digit_glyphs(crop, number):
            # An isolated digit is often omitted by Windows OCR. Give it the
            # neighbouring Wave label, but still cut only the native glyphs.
            import re
            context = flow.read_text(f[max(0,y-12):y+h+12,max(0,x-140):x+w], scales=(2.0,3.0))
            found = re.search(r'\bWave\s*(\d+)\b', context, re.I)
            if found:
                number = found.group(1)
        for ch, glyph in bc.digit_glyphs(crop, number).items():
            if ch not in got:
                rel = f'digits/{ch}.png'
                if bc.write_template(rel, glyph) == 'written':
                    written.append(rel)
                got[ch] = glyph
        if time.monotonic() - last_progress >= 5:
            flow.progress(f"Reading wave counter: {len(got)}/10 digits saved; waiting for {', '.join(d for d in '0123456789' if d not in got)}; up to {max(0, int(deadline-time.monotonic()))}s remaining")
            last_progress = time.monotonic()
        flow.pause(0.3)
    if written:
        # wave_reader caches whatever it loaded (a PARTIAL dict survives the
        # "incomplete" raise) - drop it so the fresh font is read from disk.
        from vision import wave_reader
        wave_reader._templates = None
        logger.event("hud_digits_captured", rels=sorted(written), seen=len(got))
    flow.progress(f"Captured {len(written)} HUD digit(s) from the wave counter")
    return written


def verify_saved_upgrade_labels(flow, wanted=None):
    """Verify saved stat identities without device input or starting a run.

    Shared by acquisition and the fully populated HUD pass. File existence
    alone never clears a previous skip or makes a saved stat semantically valid.
    Returns (verified images by slug, invalid evidence by relative path).
    """
    import hashlib
    import settings
    from player import accounts, battle_capture as bc
    from vision import textocr
    from runtime import logger
    if wanted is None:
        wanted = {r[6:-4] for r in accounts.generic_names() if r.startswith('stats/') and r.endswith('.png')}
    with logger.attempt('setup_upgrade_verification', requested=len(wanted)) as attempt:
        existing, hashes = {}, {}
        for name in sorted(wanted):
            path = settings.template_path(f'stats/{name}.png')
            if path.exists():
                data = path.read_bytes()
                hashes[name] = hashlib.sha256(data).hexdigest()
                existing[name] = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        verification = bc.verify_stat_labels(existing, lambda crops: flow.read_texts(
            crops, scales=(2.0,3.0), raise_on_error=True))
        invalid = {}
        for name, evidence in verification.items():
            rel = f'stats/{name}.png'
            logger.event('setup_upgrade_template_verification', rel=rel,
                         image_sha256=hashes[name], **evidence)
            previous = next((entry for entry in flow.cal.entries if entry.get('rel') == rel), {})
            if evidence['status'] == 'verified' and previous.get('status') != 'stale':
                flow.withdraw_skips(rel)
            else:
                reason = (f"Saved upgrade label {evidence['status']}: expected {name}, "
                          f"read {evidence['text']!r}; existing file kept")
                if evidence['status'] == 'verified':
                    # Correct OCR is not fresh recognition proof for an image
                    # already rejected. Only a verified cut can restore that.
                    reason = (f"Saved upgrade label reads {evidence['text']!r}, but recognition "
                              "is still stale; verify its appearance on a fresh frame")
                invalid[rel] = dict(evidence, status=(evidence['status'] if evidence['status'] != 'verified'
                                                     else 'recognition_unverified'))
                entry = dict(previous, rel=rel, phase=previous.get('phase', 'bootstrap'),
                             status='stale', verified=False, reason=reason, t=time.time(),
                             image_sha256=hashes[name],
                             semantic_verification=dict(evidence, image_sha256=hashes[name]))
                flow.cal.entries = [e for e in flow.cal.entries if e.get('rel') != rel] + [entry]
                flow.skip(rel, reason)
        if invalid:
            # Persist the rejection where readiness and the repair UI read it;
            # an event/skip alone leaves the previous verified report in force.
            if flow.cal.p.get('report'):
                flow.cal.save_report()
            flow.progress('Saved upgrade labels need attention: ' + ', '.join(sorted(invalid)))
        known = {name: image for name,image in existing.items()
                 if verification[name]['status'] == 'verified'
                 and f'stats/{name}.png' not in invalid}
        attempt.finish('incomplete' if invalid else 'verified_existing' if existing else 'none_present',
                       checked=len(verification), invalid=invalid,
                       missing=sorted(f'stats/{name}.png' for name in wanted - existing.keys()))
        return known, invalid


REPAIRABLE_STATUSES = ('mismatch', 'recognition_unverified')     # a saved image that reads as another stat, or was rejected before: a candidate can be kept for these
NO_CANDIDATE_PRODUCER = ('stats/max_label.png',)    # the Max plaque is read only while its file is missing (capture_max_plaque) and expects the text 'max', not its own slug: no candidate path exists for it


def _repairable(invalid):
    """The relative paths of `invalid` (verify_saved_upgrade_labels) a repair candidate can be produced for: a saved image that reads as another stat or was rejected before.
    An unreadable or blank saved image is not one, and neither is a name with no candidate producer (NO_CANDIDATE_PRODUCER): counting those would start a Tier-1 run and walk the
    whole panel for a candidate that cannot be made."""
    return sorted(rel for rel, evidence in invalid.items()
                  if rel not in NO_CANDIDATE_PRODUCER and evidence.get('status') in REPAIRABLE_STATUSES)


def _record_repair_candidate(flow, rel, crop, frame, point, texts, first_grab_at, second_grab_at):
    """Keep a CANDIDATE for a saved stat image that reads as another stat: the crop, the frame it was cut from and what was read, under the calibration's
    evidence folder. Never a template: nothing here writes, replaces or removes a file under templates/ (CLAUDE.md rule 6); the person approves any replacement
    and the sanctioned writer performs it. The entry of `rel` stays stale and carries the record, so Apply, readiness and the Recognition page still see the saved
    image as wrong until it is replaced. `first_grab_at` and `second_grab_at` are the times of the two independent grabs the candidate was confirmed on.
    Returns the record, or None when there is no evidence folder or the candidate files could not be written and read back (an event says why): no record is kept then."""
    from pathlib import Path
    from runtime import logger
    folder = flow.cal.p.get('evidence')
    if not folder:
        return None

    def failed(reason):
        logger.event('setup_repair_candidate_failed', rel=rel, reason=reason)
        return None

    ok, encoded = cv2.imencode('.png', crop)
    if not ok:
        return failed('the candidate crop did not encode as a PNG')
    data = encoded.tobytes()
    digest = hashlib.sha256(data).hexdigest()
    base = Path(folder) / 'repair_candidates' / Path(rel).parent
    stem = f'{Path(rel).stem}-{digest[:12]}'
    crop_path, source_path = base / f'{stem}.png', base / f'{stem}-source.png'
    try:
        base.mkdir(parents=True, exist_ok=True)
        crop_path.write_bytes(data)
        source_written = cv2.imwrite(str(source_path), frame)        # False, not an exception, when the frame cannot be written
    except (OSError, cv2.error) as error:
        return failed(f'the candidate files could not be written: {error!r}')
    if not source_written or not source_path.is_file() or crop_path.stat().st_size != len(data):
        return failed('the candidate crop or its source frame is not on disk as written')
    record = dict(rel=rel, sha256=digest, path=str(crop_path), source_frame=str(source_path), screen='battle',
                  rect=[int(point[0]), int(point[1]), int(crop.shape[1]), int(crop.shape[0])], semantic_texts=list(texts), grabs=2,
                  grabbed_at=[first_grab_at, second_grab_at], at=time.time())
    flow.cal.entries = [dict(entry, repair_candidate=record) if entry.get('rel') == rel else entry for entry in flow.cal.entries]
    if flow.cal.p.get('report'):
        flow.cal.save_report()
    logger.event('setup_repair_candidate', **{key: value for key, value in record.items() if key != 'at'})
    return record


def capture_upgrade_labels(flow):
    """Read native upgrade rows without purchasing upgrades.

    A saved stat image that reads as ANOTHER stat (a rejected image, `invalid`) is not cut again, but the panel is read for its row: the label is a repair
    CANDIDATE only when its row is found by OCR and the full label reads as the expected stat (`stat_slug` equal) in the cut, the cut is found again on a second,
    independent grab, and the cropped label itself reads the same. It is kept as evidence (see _record_repair_candidate), never written as a template.
    The Max plaque (stats/max_label.png) has no candidate producer (NO_CANDIDATE_PRODUCER): a wrong saved plaque is not repairable here, starts no run and extends no walk.

    The offline saved-label check below (verify_saved_upgrade_labels: saved crops only, one OCR launch, the device is not touched) also runs once in `_hud_digit_pass`'s
    gate, so a pass that starts the run performs it twice; both runs are read-only on the device."""
    import settings
    from player import battle_capture as bc, accounts
    from interactions import shopper
    from vision import detect, textocr
    from runtime import logger
    wanted = {r[6:-4] for r in accounts.generic_names() if r.startswith('stats/') and r.endswith('.png')}
    missing = lambda: sorted(f'stats/{name}.png' for name in wanted
                             if not settings.template_path(f'stats/{name}.png').exists())
    with logger.attempt('setup_upgrade_labels', requested=len(wanted)) as attempt:
        initial_missing = missing()
        known, invalid = verify_saved_upgrade_labels(flow, wanted)
        known.pop('max_label', None)
        repairable = _repairable(invalid)
        if not initial_missing and not repairable:
            attempt.finish('incomplete' if invalid else 'verified_existing', missing=[], invalid=invalid, written=[])
            return
        written = []
        candidates = {}
        unresolved = lambda: [rel for rel in repairable if rel[6:-4] not in candidates]      # rejected saved images no candidate was kept for yet
        for tab in ('attack','defense','utility'):
            flow.s.check_stop()
            frame = flow.grab()
            if flow.wave(frame) is None:
                raise RuntimeError('Battle HUD no longer visible while reading upgrade labels')
            if detect.panel_tab(frame) != tab:
                flow.tap(*settings.CONFIG['tabs'][tab], f'open {tab} upgrades for capture')
                flow.pause(.8)
            if detect.panel_tab(flow.grab()) != tab:
                raise RuntimeError(f'Could not verify {tab} upgrade panel')
            shopper._scroll_to_top()
            previous = None
            for page in range(8):
                flow.s.check_stop()
                flow.progress(f'Reading {tab} upgrades: page {page+1}')
                with logger.attempt('setup_upgrade_page', tab=tab, page=page+1) as page_attempt:
                    frame = flow.grab()
                    first_grab_at = time.time()
                    x,y,w,h=settings.CONFIG['rois']['upgrade_panel']
                    panel=frame[y:y+h,x:x+w]
                    def read_many(crops):
                        texts = flow.read_texts(crops, scales=(2.0,3.0), raise_on_error=True)
                        for text in texts:
                            logger.event('setup_upgrade_label', tab=tab, page=page+1, text=text)
                        return texts
                    cuts=bc.capture_stats(panel,None,wanted,read_many=read_many,known=known)
                    signature=frozenset(cuts)
                    if page and signature and signature == previous:
                        page_attempt.finish('end_of_panel', labels=sorted(cuts))
                        break
                    previous=signature
                    if 'max_label' in wanted and not settings.template_path('stats/max_label.png').exists():
                        maximum=bc.capture_max_plaque(panel,None,read_many=read_many)
                        if maximum is not None: cuts['max_label']=maximum
                    pending = {name:crop for name,crop in cuts.items()
                               if not settings.template_path(f'stats/{name}.png').exists()}
                    repair = {name:crop for name,crop in cuts.items()
                              if f'stats/{name}.png' in repairable and name not in candidates}
                    page_written, page_candidates = [], []
                    if pending or repair:
                        flow.pause(.3)
                        follow=flow.grab()
                        second_grab_at = time.time()
                        for name,crop in repair.items():
                            _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(follow,crop,cv2.TM_CCOEFF_NORMED))
                            if score < .98: continue                  # the second grab does not show the same label
                            texts=read_many([crop])
                            if bc.stat_slug(texts[0]) != name: continue   # the cropped label does not read as the expected stat
                            if _record_repair_candidate(flow,f'stats/{name}.png',crop,frame,point,texts,first_grab_at,second_grab_at) is not None:
                                candidates[name]=True
                                page_candidates.append(f'stats/{name}.png')
                        for name,crop in pending.items():
                            rel=f'stats/{name}.png'
                            _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(follow,crop,cv2.TM_CCOEFF_NORMED))
                            if score < .98: continue
                            flow.cut_crop(rel,crop,frame,name,{'rect':[point[0],point[1],crop.shape[1],crop.shape[0]],'screen':'battle'},unique=False)
                            if settings.template_path(rel).exists():
                                page_written.append(rel)
                                written.append(rel)
                                if name != 'max_label': known[name] = crop
                    page_attempt.finish('observed', labels=sorted(cuts), written=page_written, candidates=page_candidates)
                if not missing() and not unresolved():
                    attempt.finish('incomplete' if invalid else 'complete',
                                   missing=[], invalid=invalid, written=written, candidates=sorted(f'stats/{n}.png' for n in candidates))
                    return
                # Overlapping strokes keep wrapped labels from remaining
                # clipped at successive page boundaries.
                from device import act
                act.swipe(x+w//2, y+int(h*.75), x+w//2, y+int(h*.35), 600,
                          reason='setup overlapping upgrade scroll')
                flow.pause(.6)
        remaining = missing()
        for rel in remaining:
            flow.skip(rel, 'Upgrade label was not read and independently confirmed on a fresh frame')
        attempt.finish('incomplete' if remaining or invalid else 'complete', missing=remaining,
                       invalid=invalid, written=written, initial_missing=initial_missing,
                       candidates=sorted(f'stats/{n}.png' for n in candidates), unresolved=unresolved())


@_setup_lifecycle
def _hud_digit_pass(flow: _Flow) -> None:
    """Capture the HUD wave font at TIER 1, the one digit source that does not
    itself need digits (wave_reader refuses to read without the font, and
    read_tier/set_tier here are OCR-based, so Tier 1 is reachable blank).

    A HIGH-tier tower dies at wave ~3 - too few waves to show every glyph - so
    this runs its OWN Tier-1 run where the maxed tower SURVIVES and the counter
    climbs through all ten. Guarded on a missing font: a no-op once it is known
    and every saved stat label reads as itself (a saved label that reads as another stat starts the run too, for a repair candidate).

    That gate re-arms on every consented Full setup while a saved label still reads as another stat, also after a repair candidate was recorded: the person approves the
    candidate (root decision D8), and until a template is replaced the mismatch persists, so the next setup starts the Tier-1 run again (each start is gated by that setup's
    popup consent and a clean Home, rule 3). A name with no candidate producer (the Max plaque) never re-arms it. The gate's saved-label check is the offline,
    device-free `verify_saved_upgrade_labels`; capture_upgrade_labels repeats it inside the run, so a run-starting pass performs it twice.

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
    from player.readiness import requirements
    from player import playerprofile
    bodies = list(settings.CONFIG.get('presets', {}).values())
    profile_name = settings.CONFIG.get('active_profile')
    if profile_name:
        profile = playerprofile.load(profile_name)
        for name in profile.get('blueprints', {}):
            try:
                bodies.append(playerprofile.compile_preset(profile, name))
            except playerprofile.ProfileError:
                continue  # An unconfigured run cannot require a setup capture.
    required_stats = {rel for body in bodies
                      if isinstance(body, dict)
                      for item in requirements(settings.CONFIG, body)
                      for rel in item['alternatives'] if rel.startswith('stats/')}
    stats_missing = any(not settings.template_path(rel).exists() for rel in required_stats)
    # A saved stat image that reads as another stat needs the in-run panel too (a repair candidate, capture_upgrade_labels); the check reads saved crops only.
    _, stats_invalid = verify_saved_upgrade_labels(flow)
    if digits_ok and not uw_never and not hud_missing and not stats_missing and not _repairable(stats_invalid):
        return
    if set_tier(flow, 1) != 1:
        raise RuntimeError('Could not select Tier 1; no setup battle started')
    flow.progress("Digit font: starting a Tier-1 run to read the wave counter")
    _start_setup_run(flow, 'start Tier-1 digit run')
    capture_intro_sprint(flow)               # capture the dialog, then end the sprint
    _record_wall(flow, flow.grab())           # the HUD proves a wall (positive-only)
    capture_artwork_targets(flow, HUD_ABILITY_TARGETS, label="the ability buttons")
    capture_hud_targets(flow, "battle")          # the cart tile, the hamburger, learned HUD spots
    written = capture_hud_digits(flow)
    capture_upgrade_labels(flow)
    # The OWNED Ultimate Weapon labels live here too: the tower SURVIVES at
    # Tier 1, so the UW-tab sweep cannot race a death - at the account's top
    # tier (the END ROUND pass) it did, and the surrender must come first.
    capture_uw_weapons(flow)
    # the side menu's tiles (quests checkbox, the X) - the tower survives here
    capture_side_menu(flow)
    flow.progress('Digit pass done; ending the Tier-1 run')
    _finish_setup_run(flow)
    logger.event("flow_digits", result="done", written=written, home=True)


@_setup_lifecycle
def capture_battle_flow(flow: _Flow, observe_minutes: float | None = None,
                        *, do_lottery: bool = False) -> None:
    """The END ROUND + results battle pass, then the difficulty is restored.
    Gated on a clean Home and refused over any live or tournament run.

    Start at the highest unlocked tier read from Home. Observe optional effects
    for at most 90 seconds, then collect results or surrender the setup-owned
    battle. A separate bounded Tier-1 pass fills missing digits and menu labels
    that a short battle may never expose. Neither pass repeats the inventories.

    Finding the UNOWNED weapons is OPTIONAL and OFF here: they only render when a
    Tier-1 perk grant holds one for a run, so making setup wait for that lottery
    would block for a long time. Instead the labels are captured opportunistically
    DURING normal Tier-1 play - flows/quest_sm.py calls capture_missing_uw_labels
    each grant scan, so every SM-farm game keeps trying until they are all known.
    `do_lottery=True` opts back into the blocking in-setup lottery
    (`_uw_lottery_pass`) for anyone who wants to force it now.

    Successful setup restores the original difficulty. An interruption leaves
    the screen as-is and requires a person to resume from Home."""
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
    original_tier = flow.read_tier(frame)
    if original_tier is None:
        raise RuntimeError('Could not read the original difficulty; no setup battle started')
    known_player = getattr(getattr(flow, 'cal', None), 'player', {})
    if known_player.get('max_tier_verified_by') != 'setup':
        _battle_pass(flow, TIER_FOR_END_ROUND, "highest unlocked tier; optional effects")
    _hud_digit_pass(flow)          # the wave font, at Tier 1 (fresh account); a no-op once known
    results = ('icons/game_stats.png', 'buttons/retry.png', 'home/game_stats_home.png',
               'buttons/exit_battle.png', 'home/exit_battle_dialog.png')
    if set(results) - _have_templates(results):
        _battle_pass(flow, 1, "exit and results capture")
    if do_lottery and _uw_remaining():
        _uw_lottery_pass(flow, max_minutes=observe_minutes)
    _skip_end_round_yes(flow, "no END ROUND confirm came up in this setup's runs")
    # Restoration is a success path only. Stop/Abort must never trigger cleanup taps.
    _safe_home(flow)
    restored_frame = flow.grab()
    if flow.read_tier(restored_frame) != original_tier:
        if set_tier(flow, original_tier) != original_tier:
            raise RuntimeError('Could not restore the original difficulty; no further input sent')
        restored_frame = flow.grab()
        if not _clean_home(flow, restored_frame)[0] or flow.read_tier(restored_frame) != original_tier:
            raise RuntimeError('Could not verify restored difficulty on fresh Home; no further input sent')
        logger.event("flow_tier_restored", tier=original_tier)
    _safe_home(flow)


@_setup_lifecycle
def _battle_pass(flow: _Flow, tier, label: str) -> None:
    """One battle at the top tier: start, capture the intro-sprint dialog and
    end the sprint, observe optional effects briefly, then finish - surrendering (END ROUND
    dialog, GAME STATS with RETRY / HOME, reward skip) or, if the tower already
    died, cutting the GAME STATS screen directly. (The owned UW labels are cut in
    `_hud_digit_pass`; the UNOWNED ones only appear via the Tier-1 grant lottery,
    see `_uw_lottery_pass`.)"""
    from runtime import logger
    if tier is not None:
        reached = set_tier(flow, tier)
        if reached is None:
            raise RuntimeError('Could not verify setup difficulty; no battle started')
        # The climb to TIER_FOR_END_ROUND stops where the account's tier
        # arrows stop: that is the highest tier seen unlocked. Recorded as a
        # HINT (player.max_tier, per setup) that Apply carries into the
        # profile - never a ceiling the compiler refuses a run tier against.
        if tier == TIER_FOR_END_ROUND and reached and getattr(flow, "cal", None) is not None:
            flow.cal.player["max_tier"] = reached
            flow.cal.player["max_tier_verified_by"] = "setup"
            logger.event("flow_top_tier", tier=reached)
    flow.progress(f"Battle pass ({label}): starting a setup run")
    frame = _start_setup_run(flow, 'start run')
    _record_wall(flow, frame)
    capture_intro_sprint(flow)             # captures the dialog, then ends the sprint
    # Nothing else before the surrender: at the top tier the tower lives for
    # seconds, and the UW-tab sweep that used to sit here raced its death (then
    # only the plain GAME STATS path was left - no END ROUND dialog). The wave
    # font and the owned UW labels are cut in the Tier-1 _hud_digit_pass, where
    # the tower survives. ONE battle gives every results screen from here on.
    frame = flow.grab()
    _record_wall(flow, frame)
    if tier == TIER_FOR_END_ROUND:
        observe_optional_effects(flow)
        frame = flow.grab()
    # the top-tier tower is dying about now: the one setup moment the Second
    # Wind badge can be up (positive-only, two frames, nothing waits for it)
    # Second Wind is transient and optional. Observe can learn it during
    # normal play; setup must never delay surrender looking for this badge.
    capture_hud_targets(flow, "battle")
    flow.progress("Ending the setup run and capturing the results screens")
    _finish_setup_run(flow)


def observe_optional_effects(flow, seconds=90):
    """Bounded observation of the setup-owned top-tier run, never an ability gate."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        flow.s.check_stop()
        frame = flow.grab()
        if flow.game_stats(frame) or flow.on_home(frame):
            return
        flow.progress(f'Observing highest-tier battle: optional abilities; {max(0, int(deadline-time.monotonic()))}s remaining')
        capture_artwork_targets(flow, HUD_ABILITY_TARGETS + HUD_STATE_TARGETS,
                                label='optional battle effects')
        flow.pause(.5)


# ---------------------------------------------------------------- menu extras

# Controls the game only draws while something is claimable: their absence
# is not a failed capture. The runtime finds them by shape + OCR and cuts the
# image itself when it claims one (missions.learn_claim_template).
OPTIONAL_MENU_TARGETS = frozenset({"buttons/quest_claim.png"})


def _menu_capture(flow: _Flow, targets):
    """Capture each (rel, text, size, anchor) currently visible; skip the rest.
    Never taps - only reads whatever menu the caller already navigated to."""
    frame = flow.grab()
    for rel, text, size, anchor in targets:
        flow.capture(rel, text, R_FULL, size, frame=frame, anchor=anchor,
                     optional=rel in OPTIONAL_MENU_TARGETS)


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
            f, ok = flow.poll(lambda f: has_text(flow.lines_text(f), "EVENT")
                              or has_text(flow.lines_text(f), "MISSIONS"), timeout=6.0)
            if ok:
                _menu_capture(flow, [
                    ("buttons/quest_claim.png", "CLAIM", (160, 50), (0.2, 0.35)),
                    ("icons/event_calendar.png", "CALENDAR", (138, 124), (0.3, 0.5)),
                ])
            else:
                raise RuntimeError('Event menu was not verified after its tap')
            tourney.return_to_game("events")
    except Exception as e:                       # noqa: BLE001
        logger.event("flow_menu_events_error", error=str(e)[:200])
        raise

    # -- Store
    try:
        frame = flow.grab()
        if flow.on_home(frame):
            flow.tap(tourney.NAV["shop"][0], tourney.NAV["shop"][1], "open store")
            f, ok = flow.poll(lambda f: has_text(flow.lines_text(f), "STORE"), timeout=6.0)
            if ok:
                _menu_capture(flow, [
                    ("icons/free_gems.png", "FREE", (130, 48), (0.2, 0.35)),
                    ("icons/store_tower_guardian.png", "TOWER", (525, 45), (0.05, 0.3)),
                    ("icons/store_relics.png", "RELICS", (207, 44), (0.1, 0.3)),
                    ("icons/store_cosmetics.png", "COSMETICS", (323, 43), (0.1, 0.3)),
                    ("buttons/store_owned.png", "OWNED", (170, 45), (0.2, 0.35)),
                ])
            else:
                raise RuntimeError('Store menu was not verified after its tap')
            tourney.return_to_game("store")
    except Exception as e:                       # noqa: BLE001
        logger.event("flow_menu_store_error", error=str(e)[:200])
        raise

    # -- Guild
    try:
        frame = flow.grab()
        if flow.on_home(frame):
            hit = tourney.find(frame, "home/tile_guild.png")
            if hit:
                flow.tap(hit[0][0], hit[0][1], "open guild")
                f, ok = flow.poll(lambda f: has_text(flow.lines_text(f), "GUILD"), timeout=6.0)
                if ok:
                    _menu_capture(flow, [
                        ("icons/guild_coin.png", "COIN", (66, 66), (0.3, 0.5)),
                    ])
                else:
                    raise RuntimeError('Guild menu was not verified after its tap')
                tourney.return_to_game("guild")
    except Exception as e:                       # noqa: BLE001
        logger.event("flow_menu_guild_error", error=str(e)[:200])
        raise


def _safe_home(flow: _Flow) -> None:
    # ensure_home is a runner helper that waits for live battles to finish.
    # A setup error must return promptly and honour Stop, not wait for hours.
    flow.s.check_stop()
    frame = flow.grab()
    if _clean_home(flow, frame)[0]:
        from player.calibrate import record_final_screen
        record_final_screen(flow.cal.p, flow.s.state, 'home', verified=True)
        return
    raise RuntimeError('Setup paused away from Home. Return Home before retrying; no automatic waiting or further taps.')


# ---------------------------------------------------------------- entry point

def run_flows(flow_or_scanner, *, do_menus: bool = True, do_battle: bool = True,
              do_uw_lottery: bool = False) -> None:
    """Run the consented action-capture flows as a starter-scan stage. A failing
    flow stops setup without recovery input into the unexpected screen.
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
            mark_setup_unknown(flow, e)
            raise
    if do_battle:
        try:
            capture_battle_flow(flow, do_lottery=do_uw_lottery)
        except Exception as e:                   # noqa: BLE001
            logger.event("flow_battle_error", error=str(e)[:200])
            mark_setup_unknown(flow, e)
            raise
    logger.event("flow_capture", stage="done")
