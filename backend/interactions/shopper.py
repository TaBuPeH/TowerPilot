"""In-run workshop shopping and UW toggling by named stat.

Stat label templates: templates/stats/<name>.png (cropped from native frames,
the white label text on the dark stat box, e.g. 'Critical Factor').
UW toggle pills: templates/uw/toggle_on.png, templates/uw/toggle_off.png,
UW name labels: templates/uw/<weapon>.png (e.g. chain_lightning.png).
"""
import random
import re
import math
import subprocess
import time
import traceback
import uuid

import cv2
import numpy as np  # noqa: F401 - used by phase correlation

from settings import CONFIG, adb_args, input_args, run_hidden
from device import capture
from vision import detect
from vision import wave_reader
from device import act
from runtime import logger
from vision import ocr
from towerpilot_workflows.action_contract import AfterCompletion

_SCROLL_STEPS = 4          # max downward scroll steps while hunting (big strides)

# Stats that are endless money sinks: NEVER buy them, and NEVER let them keep
# a sweep alive - a tab whose only remaining buyable is one of these counts
# as complete (user: "if there is Defense Absolute that needs clicks just
# ignore it and consider everything complete").
IGNORE_STATS = {"defense_absolute"}
_TOP_RESETS = 3            # upward swipes to guarantee panel is at the top
_SETTLE_SEC = 0.25         # UI settle time after tab tap / scroll
_CONTINUATION_PURCHASES = 4  # then return to idle so other routines can navigate
_CONTINUATION_SEC = 20.0     # checked between actions, never discards confirmation


def _quest_frame(guard=None):
    frame = capture.grab()
    return guard(frame, "battle") if guard is not None else frame


def _tap_tab(name: str, *, guard=None) -> bool:
    """Ensure the panel is open on the given tab.

    CRITICAL: tapping the tab that is ALREADY open CLOSES the panel. So we
    read the current tab from the header color first and only tap when a
    change is needed; 'unknown' (panel closed / mid-animation) -> tap opens it.
    Verified with retries because a mistap can close the panel.
    """
    pt = CONFIG["tabs"].get(name)
    if not pt:
        logger.event("shop_error", error=f"tab '{name}' not calibrated")
        return False
    for _ in range(2):
        frame = _quest_frame(guard)
        if detect.panel_tab(frame) == name:
            return True
        # TOWER ON SCREEN OR HANDS OFF: the tab strip only exists in battle,
        # and the wave counter is the proof the battle is what is on screen.
        # Without this, a normalization that believed a menu was a fresh run
        # tapped the UW tab position into a human's BATTLE HISTORY screen
        # (2026-09-04 08:50 and 08:53) - unlogged, because the tap's event
        # dict was dropped here.
        if wave_reader.read_wave(frame) is None:
            logger.event("tab_refused", tab=name, reason="no wave on screen")
            return False
        try:
            ev = act.tap(pt[0], pt[1], reason=f"tab_{name}")
            logger.event("tab_tap", tab=name, **ev)
        except act.TapRefused as e:
            logger.event("tap_refused", button=f"tab_{name}", error=str(e))
            return False
        time.sleep(_SETTLE_SEC)
    return detect.panel_tab(_quest_frame(guard)) == name


def _swipe(down: bool, *, guard=None):
    """Scroll panel content one step, organically randomized per swipe:
    x drifts +/-60px, endpoints +/-40px, duration 250-450ms, and start/end
    x differ slightly so the stroke is never perfectly vertical."""
    if guard is not None:
        _quest_frame(guard)
    box = CONFIG["rois"]["upgrade_panel"]
    x0 = box[0] + box[2] // 2 + random.randint(-60, 60)
    x1 = x0 + random.randint(-25, 25)
    a = box[1] + int(box[3] * 0.80) + random.randint(-30, 30)
    b = box[1] + int(box[3] * 0.20) + random.randint(-30, 30)
    y0, y1 = (a, b) if down else (b, a)
    # measured from the user's recorded panel scrolls: 160-300ms strokes
    dur = random.randint(150, 300)
    act.swipe(x0, y0, x1, y1, dur, reason="panel scroll")
    # short settle only - the next frame's ~350ms capture time absorbs the
    # rest of the fling animation before any analysis happens
    time.sleep(0.12 + random.uniform(0.0, 0.08))


def _swipe_panel_down(*, guard=None):
    _swipe(down=True, **({"guard": guard} if guard is not None else {}))


def _panel_gray(frame=None):
    frame = frame if frame is not None else capture.grab()
    return cv2.cvtColor(capture.roi(frame, "upgrade_panel"), cv2.COLOR_BGR2GRAY)


def _scroll_to_top(*, guard=None):
    """Swipe up until the content stops SHIFTING - measured structurally via
    phase correlation, which ignores ticking numbers/prices (raw frame-diff
    is fooled by them and kept swiping at the top)."""
    opts = {"guard": guard} if guard is not None else {}
    prev = np.float32(_panel_gray() if guard is None else _panel_gray(_quest_frame(guard)))
    for _ in range(_TOP_RESETS):
        _swipe(down=False, **opts)
        cur = np.float32(_panel_gray() if guard is None else _panel_gray(_quest_frame(guard)))
        (_, dy), _ = cv2.phaseCorrelate(prev, cur)
        diff = float(np.mean(cv2.absdiff(prev, cur)))
        if abs(dy) < 15 and diff < 6.0:   # content froze -> already at top
            return
        prev = cur


def _find_stat(frame, stat: str):
    """Locate a stat box by its label template within the panel.

    Returns (price_center, state, price) where state is one of:
      'buy'   - blue box, price OCR'd from the "$ NNN" line (None if unreadable)
      'maxed' - gold box / auto-upgraded: PERMANENTLY done for this run
      'poor'  - grey box, not affordable right now: recheck next sweep
    (None, None, None) when the label is not on this screen.
    """
    box = CONFIG["rois"]["upgrade_panel"]
    panel = capture.roi(frame, "upgrade_panel")
    hit, score, loc = detect._match(panel, f"stats/{stat}.png", 0.70)
    if not hit:
        return None, None, None
    tpl = detect._tpl(f"stats/{stat}.png")
    lx, ly = loc
    # price box: to the right of the label, same row band
    x0 = min(panel.shape[1] - 1, lx + tpl.shape[1] + 10)
    x1 = min(panel.shape[1], x0 + 230)   # stop before the next column's box
    y0 = max(0, ly - 20)
    y1 = min(panel.shape[0], ly + tpl.shape[0] + 60)
    region = panel[y0:y1, x0:x1]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    # box color IS the state: blue = buyable, gold = maxed ("Max" plaque),
    # neither = grey/unaffordable (measured: maxed gold 10-16%, buyable 0%)
    blue = (cv2.inRange(hsv, (95, 120, 120), (115, 255, 255)) > 0).mean()
    gold = (cv2.inRange(hsv, (10, 60, 60), (35, 255, 255)) > 0).mean()
    if blue > 0.05:
        state = "buy"
    elif gold > 0.05:
        state = "maxed"
    else:
        state = "poor"
    price = None
    if state == "buy":
        # the "$ NNN" line occupies the lower part of the price box
        h = y1 - y0
        price = ocr.read_amount(region[int(h * 0.55):, :], thresh=170)
    center = (box[0] + (x0 + x1) // 2, box[1] + (y0 + y1) // 2)
    return center, state, price


def read_cash(frame) -> float | None:
    return ocr.read_amount(capture.roi(frame, "cash_box"))


def _quantity_token(text: str) -> int | None:
    """Parse a complete quantity token without repairing ambiguous OCR glyphs."""
    match = re.fullmatch(r"(?:[xX×]([1-9][0-9]*)|([1-9][0-9]*)[xX×])", text.strip())
    return int(match.group(1) or match.group(2)) if match else None


def _read_purchase_quantity(frame) -> int | None:
    """Read the header's observed quantity control; disagreement is unknown.

    The rightmost quarter of the panel header is evidenced by the read-only
    native capture (Utility header x10). The existing panel ROI includes this
    strip. No label, numeric policy or blurry x10 prefix establishes 1x.
    """
    from vision import textocr
    if detect.panel_tab(frame) not in ("attack", "defense", "utility"):
        return None
    panel = capture.roi(frame, "upgrade_panel")
    if panel.size == 0:
        return None
    header = panel[:max(20, panel.shape[0] // 12), panel.shape[1] * 3 // 4:]
    values = []
    for scale in (2.0, 3.0):
        # read_lines intentionally bypasses read_text's l/I -> 1 repair.
        lines = textocr.read_lines(header, scale=scale, timeout=0.75)
        token = " ".join(text for _, _, text in lines)
        value = _quantity_token(token)
        if value is None:
            return None
        values.append(value)
    return values[0] if len(set(values)) == 1 else None


def _purchase_quote(frame, stat: str) -> dict:
    """Observe same-frame quote and cash; bulk price totals remain unverified."""
    center, state, price = _find_stat(frame, stat)
    return dict(center=center, state=state, price=price, cash=read_cash(frame),
                quantity=_read_purchase_quantity(frame) if state == "buy" else None,
                total_price_verified=False,
                observed_at=time.monotonic())


def _confirmed_purchase(before: dict, after: dict) -> bool:
    """Conservative two-indicator evidence under a freshly observed 1x mode.

    Price must increase and cash must fall by at most the quoted purchase cost.
    Income can mask that cash fall; unreadable/bulk/maxed evidence stays unknown.
    This records observable evidence, not exact accounting of simultaneous income.
    """
    values = (before.get('price'), after.get('price'), before.get('cash'), after.get('cash'))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        return False
    if type(before.get('quantity')) is not int or before['quantity'] != 1 or type(after.get('quantity')) is not int or after['quantity'] != 1:
        return False
    age = after.get('observed_at', 0) - before.get('observed_at', 0)
    if not 0 < age <= 15:
        return False
    delta = before['cash'] - after['cash']
    return before['price'] > 0 and after['price'] > before['price'] and 0 < delta <= before['price']


def _affordable_clicks(cash: float | None, price: float | None,
                       cap: int = 10) -> int:
    """How many taps a human would fire given the cash/price gap.
    Big gap -> random 5-15 burst (humans don't count exact clicks);
    moderate gap -> proportional, capped. Unknown numbers -> 2."""
    if not cash or not price or price <= 0:
        return 2
    ratio = cash / price
    if ratio < 1:
        return 0                     # impossible price - don't waste taps
    n = int(ratio * 0.8)
    if n >= cap:
        return random.randint(5, 15)
    return max(1, n)


def _click_burst(center: tuple[int, int], stat: str, clicks: int, *, observer=None) -> int:
    """Burst-tap an already-located price box - NO per-click capture.
    Tapping a box that went grey mid-burst is a harmless no-op in-game."""
    done = 0
    for i in range(clicks):
        try:
            if observer is not None:
                observer('attempts')
            ev = act.tap(*center, reason=f"buy_{stat}")
            if observer is not None:
                observer('dry_run' if ev.get('dry_run', False) else 'dispatched')
            logger.event("shop", stat=stat, **ev)
            done += 1
        except act.TapRefused as e:
            if observer is not None:
                observer('failed' if e.__cause__ is not None else 'refused', error=e)
            logger.event("tap_refused", button=stat, error=str(e))
            break
        if i + 1 < clicks:
            time.sleep(random.uniform(0.15, 0.35))
    return done




class Shopper:
    """NON-BLOCKING incremental shopping.

    The orchestrator's observe loop drives a sweep one small action at a time via
    step(frame): a step is at most one tab tap, one swipe, or one <=4-click
    chunk. Between every step the main loop captures a fresh frame and runs
    ALL monitoring (death/rescue/gems/...), so shopping - the least important
    job - can never blind the important ones. abort() drops a sweep instantly
    (death, left the battle screen, etc.). Version-2 while_affordable plans
    confirm each single 1x purchase, yielding a new monitor frame before the
    next quote. Windows stop after four confirmed purchases or 20 seconds
    between actions, then expose an idle checkpoint to other routines. Only
    confirmed progress allows an immediate next window; all blockers retain
    the configured interval as their idle backoff.
    """

    def __init__(self, preset: dict):
        from player.shopping_plan import normalize_plan
        self.recorder = None              # the host's execution recorder; None records nothing
        self.manual_command_id = None
        self._schedule = AfterCompletion()
        self._sweep_open = False
        self.directives = preset.get("shopping", [])
        self.plan = normalize_plan(preset["shopping_plan"]) if preset.get("shopping_plan") else None
        self.attempts: dict[str, int] = {}
        self._manual = None
        self._manual_outcome = None
        self._last_decision = {"action": "wait", "reason": "not_started"}
        self._wave = None
        self._pending_confirmation = None
        self._last_confirmation = None
        self._continuation_ready = False
        self._confirmed_in_sweep = 0
        self.done_once: set[str] = set()
        self.maxed: set[str] = set()     # gold "Max" boxes: done for this run
        self._gen = None
        self._sweep_id = None
        self._sweep_context = {}
        self._sweep_started = None
        self._sweep_ended = True
        self._sweep_counts = {}
        self._sweep_manual = None
        self._last_sweep = None

    def reset(self):
        self.attempts.clear()
        self._manual = None
        self._manual_outcome = None
        self._pending_confirmation = None
        self._last_confirmation = None
        self._continuation_ready = False
        self.done_once.clear()
        self.maxed.clear()

    @property
    def active(self) -> bool:
        return self._gen is not None

    @property
    def finished(self) -> bool:
        """True when EVERY wanted stat is maxed/auto-upgraded (or once-bought)
        -> no more shopping this run at all (stop scrolling, stop clicking)."""
        if self.plan is not None:
            return False  # Future wave phases/live edits may create more work.
        for d in self.directives:
            for stat in d["stats"]:
                if stat in IGNORE_STATS:
                    continue
                if stat in self.maxed:
                    continue
                if d["mode"] == "once" and stat in self.done_once:
                    continue
                return False
        return True

    def start(self):
        if self._gen is None:
            self._continuation_ready = False
            self._confirmed_in_sweep = 0
            self._sweep_id = uuid.uuid4().hex
            self._sweep_context = {**logger.current_context(), 'sweep_id': self._sweep_id}
            self._sweep_started = time.monotonic()
            self._sweep_ended = False
            self._sweep_counts = {'attempts': 0, 'dispatched': 0, 'dry_run': 0}
            self._sweep_manual = dict(self._manual) if self._manual is not None else None
            self._sweep_section = (self._sweep_manual or {}).get('section')
            self._sweep_upgrade = (self._sweep_manual or {}).get('upgrade')
            self._sweep_error = None
            self._last_decision = {'action': 'scan', 'reason': 'sweep_started'}
            self._gen = self._queue_sweep_gen() if self.plan is not None else self._sweep_gen()
            with logger.context(**self._sweep_context):
                logger.event('shop_sweep_start', manual=self._sweep_manual is not None,
                             selection=self._sweep_manual, wave=self._wave,
                             plan_version=2 if self.plan is not None else 1)
                try:
                    next(self._gen)              # prime to the first yield
                except StopIteration:
                    self._end_sweep()
                    raise
                except BaseException as exc:
                    self._end_sweep('error', exc)
                    raise

    def _record_open(self):
        """The sweep's execution begins with its first purchase tap: until then a sweep sent no input and is only a check."""
        manual = self._sweep_manual is not None
        command_id, self.manual_command_id = (self.manual_command_id if manual else None), None
        self._sweep_open = True
        self.recorder.opened(trigger='manual' if manual else 'automatic', occurrence_key=f'sweep:{self._sweep_id}',
                             command_id=command_id, refs=dict(sweep_id=self._sweep_id))

    def _record_end(self, outcome, fields):
        """Close the sweep's execution. A sweep that sent input is its own row, with the purchases the HUD confirmed; one that
        sent none is a no-input check (shared by identical automatic ones), or a one-shot row when it failed."""
        if self.recorder is None:
            return
        dispatched, reason, refs = self._sweep_counts.get('dispatched', 0), fields['reason'], dict(sweep_id=self._sweep_id)
        if self._sweep_open:
            self._sweep_open = False
            if outcome == 'confirmed':
                self.recorder.closed(result='succeeded', reason=reason, effect='confirmed', attempts=dispatched,
                                     confirmed=self._confirmed_in_sweep, evidence=['confirmed_purchase'], refs=refs)
            elif outcome == 'aborted':
                self.recorder.closed(result='cancelled', reason=reason, effect='confirmed' if self._confirmed_in_sweep else 'unconfirmed',
                                     attempts=dispatched, confirmed=self._confirmed_in_sweep, refs=refs)
            elif outcome in ('failed', 'error'):
                self.recorder.closed(result='failed', reason=reason, effect='unknown', attempts=dispatched,
                                     confirmed=self._confirmed_in_sweep, refs=refs)
            else:
                self.recorder.closed(result='failed', reason=reason, effect='unconfirmed', attempts=dispatched,
                                     confirmed=self._confirmed_in_sweep, refs=refs)
            return
        manual = self._sweep_manual is not None
        command_id, self.manual_command_id = (self.manual_command_id if manual else None), None
        if outcome in ('failed', 'error'):
            self.recorder.record(result='failed', reason=reason, trigger='manual' if manual else 'automatic',
                                 occurrence_key=f'sweep:{self._sweep_id}', command_id=command_id, refs=refs)
        elif manual:
            self.recorder.record(result='refused' if outcome == 'refused' else 'no_op', reason=reason, trigger='manual',
                                 occurrence_key=f'sweep:{self._sweep_id}', command_id=command_id, refs=refs)
        else:
            self.recorder.check(result='refused' if outcome == 'refused' else 'no_op', reason=reason)

    def _record_attempt(self, outcome, *, error=None):
        self._sweep_counts[outcome] = self._sweep_counts.get(outcome, 0) + 1
        if outcome == 'dispatched' and self.recorder is not None and not self._sweep_open:
            self._record_open()
        if error is not None:
            self._sweep_error = error
            self._last_decision = {'action': 'wait',
                                   'reason': 'tap_failed' if outcome == 'failed' else 'tap_refused',
                                   'section': self._sweep_section, 'upgrade': self._sweep_upgrade}

    def _end_sweep(self, outcome=None, error=None):
        if self._sweep_ended:
            return
        self._sweep_ended = True
        error = error if error is not None else self._sweep_error
        decision = dict(self._last_decision)
        if outcome is None:
            confirmed_all = (self._confirmed_in_sweep > 0
                             and self._confirmed_in_sweep == self._sweep_counts['dispatched']
                             and not any(self._sweep_counts.get(key) for key in ('dry_run', 'failed', 'refused')))
            outcome = ('confirmed' if confirmed_all or decision.get('action') == 'confirmed' else
                       'unconfirmed' if self._sweep_counts['dispatched'] else
                       'failed' if self._sweep_counts.get('failed') else
                       'refused' if self._sweep_counts.get('refused') else
                       'refused' if decision.get('reason') == 'tap_refused' else
                       'dry_run' if self._sweep_counts['dry_run'] else
                       'skipped' if self.plan is not None else 'completed')
        fields = {'outcome': outcome, 'reason': decision.get('reason', 'sweep_complete'),
                  'decision': decision, 'purchase_taps': dict(self._sweep_counts),
                  'count_semantics': 'tap_invocations_not_confirmed_purchases',
                  'duration_ms': round((time.monotonic() - self._sweep_started) * 1000, 3),
                  'wave': self._wave, 'manual': self._sweep_manual is not None,
                  'confirmed_purchases': self._confirmed_in_sweep,
                  'selection': self._sweep_manual, 'section': decision.get('section', self._sweep_section),
                  'upgrade': decision.get('upgrade', self._sweep_upgrade)}
        if error is not None:
            fields.update(exception_type=type(error).__name__, exception_message=str(error),
                          traceback=''.join(traceback.format_exception(type(error), error, error.__traceback__)))
        self._last_sweep = {'sweep_id': self._sweep_id, **fields}
        logger.event('shop_sweep_end', **fields)
        self._record_end(outcome, fields)

    def step(self, frame, *, wave=None, after_intro_sprint: bool = False):
        """Advance the sweep one action. No-op when inactive."""
        if self._gen is None:
            return
        with logger.context(**self._sweep_context):
            try:
                if self.plan is not None:
                    self._wave = wave if wave is not None else wave_reader.read_wave(frame)
                    if self._wave is None or not after_intro_sprint:
                        self._last_decision = {"action": "wait", "reason": "unknown_wave" if self._wave is None else "intro_sprint"}
                        self.abort()
                        return
                    if (self.plan['cadence'] == 'while_affordable' and self._sweep_manual is None
                            and self._pending_confirmation is None
                            and time.monotonic() - self._sweep_started >= _CONTINUATION_SEC):
                        self._continuation_ready = self._confirmed_in_sweep > 0
                        self._last_decision = {'action': 'wait', 'reason': 'continuation_time_budget'}
                        self._gen.close()
                        self._gen = None
                        self._end_sweep()
                        return
                self._gen.send(frame)
            except StopIteration:
                self._gen = None
                self._end_sweep()
            except BaseException as exc:
                self._end_sweep('error', exc)
                raise

    def abort(self):
        self._continuation_ready = False
        manual_pending = self._manual is not None
        if self._gen is not None:
            with logger.context(**self._sweep_context):
                try:
                    self._gen.close()
                except BaseException as exc:
                    self._end_sweep('error', exc)
                    raise
                self._gen = None
                logger.event("sweep_abort")
                self._end_sweep('aborted')
        if manual_pending:
            self._manual_outcome = {"state": "aborted", "attempts": (self._manual_outcome or {}).get("attempts", 0),
                                    "outcome": self._last_decision}
            self._manual = None

    def update_plan(self, plan: dict) -> None:
        """Replace rules at a checkpoint without resetting battle attempts."""
        from player.shopping_plan import normalize_plan
        normalized = normalize_plan(plan)
        self.abort()
        self.plan = normalized

    def request_once(self, section: str | None = None, upgrade: str | None = None) -> bool:
        from player.shopping_plan import catalog
        if self.plan is None or self.active or self._manual is not None:
            return False
        if section is not None and section not in ("attack", "defense", "utility"):
            return False
        if upgrade is not None and not any(c["id"] == upgrade and (section is None or c["section"] == section) for c in catalog()):
            return False
        self._manual = {"section": section, "upgrade": upgrade}
        self._manual_outcome = {"state": "requested", "attempts": 0}
        self.start()
        return True

    def status(self) -> dict:
        from copy import deepcopy
        return deepcopy({"version": 2 if self.plan is not None else 1,
                         "active": self.active, "attempts": self.attempts,
                         "decision": self._last_decision, "manual": self._manual_outcome,
                         "sweep_id": self._sweep_id, "last_sweep": self._last_sweep,
                         "count_semantics": "tap_attempts", "confirmed_purchases": None,
                         "quantity_observation_supported": self.plan is not None,
                         "reserve_supported": self.plan is not None,
                         "reserve_enforcement": "requires_fresh_1x_quote" if self.plan is not None else "legacy_unprotected",
                         "pending_confirmation": self._pending_confirmation,
                         "last_confirmation": self._last_confirmation,
                         "cadence": self.plan['cadence'] if self.plan else 'single_attempt',
                         "continuation_ready": self._continuation_ready,
                         "continuation_budget": {'purchases': _CONTINUATION_PURCHASES,
                                                 'seconds': _CONTINUATION_SEC},
                         "cash_reserve": self.plan.get("cash_reserve") if self.plan else None})

    def due(self, now: float, last_shop: float, interval: float) -> bool:
        """Only confirmed progress can bypass the configured idle backoff."""
        decision, _ = self._schedule.evaluate(now, dict(last_completed_at=last_shop),
                                              dict(interval=interval, continuation_ready=self._continuation_ready))
        return decision.kind == 'due'

    def _queue_sweep_gen(self):
        """Bound sustained buying; every attempt and confirmation yields frames."""
        continuous = self.plan['cadence'] == 'while_affordable' and self._manual is None
        for _ in range(_CONTINUATION_PURCHASES if continuous else 1):
            confirmed_before = self._confirmed_in_sweep
            yield from self._queue_attempt_gen(require_confirmation=continuous)
            if not continuous or self._confirmed_in_sweep == confirmed_before:
                return
            if time.monotonic() - self._sweep_started >= _CONTINUATION_SEC:
                break
        self._continuation_ready = continuous and self._confirmed_in_sweep > 0

    def _queue_attempt_gen(self, *, require_confirmation=False):
        """Global queue order drives navigation; each pass has a fixed scan cap."""
        from player.shopping_plan import queue_candidates, decide
        manual = self._manual
        frame = yield
        options = manual or {}
        observations = {}
        if manual:
            self._manual_outcome = {"state": "started", "attempts": 0}
        try:
            if not self.plan["enabled"] and not manual:
                self._last_decision = {"action": "wait", "reason": "disabled"}
                return
            if self._pending_confirmation is not None:
                for _ in range(2):
                    if self._reconcile_confirmation(frame):
                        return
                    frame = yield
                self._last_decision = {"action": "wait", "reason": "purchase_confirmation_unknown"}
                return
            rows = queue_candidates(self.plan, self._wave, self.attempts, **options)
            for row in rows:
                if row["id"] in self.maxed:
                    observations[row["id"]] = dict(state="maxed", observed_at=time.monotonic())
            # Bound a pass independently of catalog/queue size. Subsequent passes
            # may retry a blocker; they never silently reorder the global queue.
            scans = 0
            while True:
                decision = decide(self.plan, self._wave, observations, self.attempts,
                                  time.monotonic(), manual=manual is not None, **options)
                self._last_decision = decision
                if decision["action"] != "scan":
                    return
                if scans >= 48:
                    self._last_decision = {"action": "wait", "reason": "scan_budget_exhausted"}
                    return
                scans += 1
                tab, stat = decision["section"], decision["upgrade"]
                self._sweep_section, self._sweep_upgrade = tab, stat
                point = CONFIG["tabs"].get(tab)
                if not point:
                    self._last_decision = {"action": "wait", "reason": "tab_not_calibrated"}
                    return
                for _ in range(3):
                    if detect.panel_tab(frame) == tab:
                        break
                    act.tap(*point, reason=f"tab_{tab}")
                    frame = yield
                if detect.panel_tab(frame) != tab:
                    self._last_decision = {"action": "wait", "reason": "tab_not_open"}
                    return
                for _ in range(_TOP_RESETS):
                    if detect.panel_tab(frame) != tab:
                        self._last_decision = {"action": "wait", "reason": "panel_changed"}
                        return
                    _swipe(down=False)
                    frame = yield
                for page in range(_SCROLL_STEPS + 1):
                    if detect.panel_tab(frame) != tab:
                        self._last_decision = {"action": "wait", "reason": "panel_changed"}
                        return
                    quote = _purchase_quote(frame, stat)
                    if quote["center"] is not None:
                        observations[stat] = quote
                        if quote["state"] == "maxed":
                            self.maxed.add(stat)
                        break
                    if page < _SCROLL_STEPS:
                        _swipe(down=True)
                        frame = yield
                else:
                    self._last_decision = {"action": "wait", "reason": "target_not_found", "upgrade": stat}
                    return
                # Recompute all eligibility against current wave and observations.
                # There is no yield between this quote, decision and single tap.
                decision = decide(self.plan, self._wave, observations, self.attempts,
                                  time.monotonic(), manual=manual is not None, **options)
                self._last_decision = decision
                if decision["action"] == "tap":
                    if decision["upgrade"] != stat:
                        self._last_decision = {"action": "wait", "reason": "target_requires_fresh_quote"}
                        return
                    self._record_attempt('attempts')
                    event = act.tap(*quote["center"], reason=f"buy_{stat}")
                    self._record_attempt('dry_run' if event.get('dry_run', False) else 'dispatched')
                    if not event.get("dry_run", False):
                        key = "queue/" + stat
                        self.attempts[key] = self.attempts.get(key, 0) + 1
                        if self.plan["queue_mode"] == "round_robin":
                            self.attempts["__queue_cursor"] = (decision["index"] + 1) % max(1, len(self.plan["queue"]))
                        if require_confirmation or self.plan["queue_mode"] == "weighted_split":
                            self._pending_confirmation = {"upgrade": stat, "section": tab, "before": quote}
                    logger.event("shop_plan_attempt", decision=decision, quote=quote,
                                 outcome="unconfirmed", **event)
                    if manual:
                        self._manual_outcome["attempts"] = 0 if event.get("dry_run", False) else 1
                    if self._pending_confirmation is not None:
                        for _ in range(2):
                            frame = yield
                            if self._reconcile_confirmation(frame):
                                return
                        self._last_decision = {"action": "wait", "reason": "purchase_confirmation_unknown"}
                    return
                if decision["action"] != "scan":
                    return
        except act.TapRefused as exc:
            self._record_attempt('failed' if exc.__cause__ is not None else 'refused', error=exc)
            logger.event("tap_refused", button="shopping_queue", error=str(exc))
        finally:
            if manual:
                self._manual_outcome.update(state="completed", outcome=self._last_decision)
                self._manual = None

    def _reconcile_confirmation(self, frame) -> bool:
        from player.shopping_plan import record_confirmed_purchase
        pending = self._pending_confirmation
        if pending is None:
            return False
        if time.monotonic() - pending["before"]["observed_at"] > 15:
            self._last_confirmation = {"upgrade": pending["upgrade"],
                                       "outcome": "terminal_unconfirmed", "reason": "evidence_expired"}
            logger.event("shop_plan_confirmation", **self._last_confirmation)
            self._pending_confirmation = None
            self._last_decision = {"action": "wait", "reason": "purchase_confirmation_expired",
                                   "upgrade": pending["upgrade"]}
            return True  # End this pass; only a subsequent scheduled pass may buy.
        if detect.panel_tab(frame) != pending["section"]:
            return False
        after = _purchase_quote(frame, pending["upgrade"])
        if after["center"] is None or not _confirmed_purchase(pending["before"], after):
            return False
        record_confirmed_purchase(self.plan, self.attempts, pending["upgrade"])
        self._confirmed_in_sweep += 1
        logger.event("shop_plan_confirmation", upgrade=pending["upgrade"],
                     before=pending["before"], after=after,
                     evidence="1x_price_increased_and_cash_decreased")
        self._pending_confirmation = None
        self._last_confirmation = {"upgrade": pending["upgrade"], "outcome": "confirmed",
                                   "reason": "price_and_cash_evidence"}
        self._last_decision = {"action": "confirmed", "upgrade": pending["upgrade"],
                               "reason": "price_and_cash_evidence"}
        return True

    def _sweep_gen(self):
        """One SCAN PASS per tab, yielding between actions. Every `yield`
        hands control back to the observe loop and receives a fresh frame."""
        tabs_in_order = []
        for d in self.directives:
            if d["tab"] not in tabs_in_order:
                tabs_in_order.append(d["tab"])
        for tab in tabs_in_order:
            self._sweep_section = tab
            remaining: dict[str, tuple[str, int]] = {}
            for d in self.directives:
                if d["tab"] != tab:
                    continue
                for stat in d["stats"]:
                    if stat in IGNORE_STATS:
                        continue          # endless sink: never buy
                    if stat in self.maxed:
                        continue          # gold box seen: done for this run
                    if d["mode"] == "once" and stat in self.done_once:
                        continue
                    clicks = {"repeat": 4, "best_cost": 2, "once": 1}.get(d["mode"]) \
                        or d.get("clicks", 1)
                    if d["mode"] == "clicks":
                        clicks = d.get("clicks", 1)
                    remaining[stat] = (d["mode"], clicks)
            if not remaining:
                continue

            # ---- open the tab: one verified tap per frame.
            # CRITICAL: tapping the already-open tab CLOSES the panel, so we
            # only tap when the header color says we are elsewhere.
            pt = CONFIG["tabs"].get(tab)
            if not pt:
                logger.event("shop_error", error=f"tab '{tab}' not calibrated")
                continue
            opened = False
            for _ in range(3):
                frame = yield
                if detect.panel_tab(frame) == tab:
                    opened = True
                    break
                try:
                    act.tap(pt[0], pt[1], reason=f"tab_{tab}")
                except act.TapRefused as e:
                    logger.event("tap_refused", button=f"tab_{tab}", error=str(e))
            if not opened:
                frame = yield
                if detect.panel_tab(frame) != tab:
                    logger.event("shop_error", error=f"could not open tab '{tab}'")
                    continue

            # ---- scroll to top: one swipe per frame until content freezes
            prev = None
            for _ in range(_TOP_RESETS):
                frame = yield
                g = np.float32(_panel_gray(frame))
                if prev is not None:
                    (_, dy), _ = cv2.phaseCorrelate(prev, g)
                    diff = float(np.mean(cv2.absdiff(prev, g)))
                    if abs(dy) < 15 and diff < 6.0:
                        break        # content froze -> at the top
                prev = g
                _swipe(down=False)

            # ---- scan pass: at each stop recognize ALL remaining stats from
            # the single frame, click-burst the affordable ones in chunks
            bought, grey, skipped = [], [], []
            prev_gray = None
            for _ in range(_SCROLL_STEPS + 1):
                frame = yield
                cash = read_cash(frame)
                g = np.float32(_panel_gray(frame))
                if prev_gray is not None:
                    (_, dy), _ = cv2.phaseCorrelate(prev_gray, g)
                    diff = float(np.mean(cv2.absdiff(prev_gray, g)))
                    # repetitive row pitch can alias phase-correlation to ~0 on
                    # a real scroll; only trust "bottom" when pixels also froze
                    if abs(dy) < 15 and diff < 6.0:
                        break        # last swipe moved nothing -> bottom
                for stat in list(remaining):
                    self._sweep_upgrade = stat
                    center, state, price = _find_stat(frame, stat)
                    if center is None:
                        continue                     # not on this screen
                    mode, clicks = remaining.pop(stat)
                    if state == "maxed":
                        self.maxed.add(stat)         # never scan again this run
                        grey.append(stat)
                        continue
                    if state == "poor":
                        skipped.append(stat)         # recheck next sweep
                        continue
                    if mode in ("repeat", "best_cost"):
                        # budget-driven: hammer proportional to cash/price gap
                        target = _affordable_clicks(cash, price)
                        if target == 0:
                            skipped.append(f"{stat}@{price and int(price)}")
                            continue
                        if cash and price:
                            cash -= target * price   # rough running budget
                    else:
                        target = clicks
                    # click in <=2-tap chunks, yielding between chunks so
                    # monitoring keeps its ~0.5s cadence inside long bursts
                    done = 0
                    while done < target:
                        n = _click_burst(center, stat, min(2, target - done), observer=self._record_attempt)
                        done += n
                        if n == 0:
                            break                    # tap refused - stop here
                        if done < target:
                            yield
                    bought.append(f"{stat}x{done}")
                    if done and mode == "once":
                        self.done_once.add(stat)
                if not remaining:
                    break
                prev_gray = g
                _swipe(down=True)
            logger.event("sweep_tab", tab=tab, bought=bought, grey=grey,
                         too_expensive=skipped, not_found=list(remaining))


def uw_toggle(weapon: str, want_on: bool, *, guard=None) -> bool:
    """Set a UW's ON/OFF toggle, verifying state before and after."""
    opts = {"guard": guard} if guard is not None else {}
    if not _tap_tab("uw", **opts):
        logger.event("uw_fail", weapon=weapon, stage="open_uw_tab")
        return False
    if not _hunt_uw(weapon, **opts):
        logger.event("uw_fail", weapon=weapon, stage="hunt",
                     error=f"uw '{weapon}' not found")
        return False
    frame = _quest_frame(guard)
    state = _uw_state(frame, weapon)
    if state is None:
        # The hunt stops at the FIRST sight of the name, which for the
        # bottom row (Black Hole / Spotlight, 2026-08-16) is a half-scrolled
        # position with the pill still below the screen edge. One more
        # swipe brings the full row into view; only then is the pill
        # genuinely unreadable.
        _swipe_panel_down(**opts)
        time.sleep(_SETTLE_SEC)
        frame = _quest_frame(guard)
        state = _uw_state(frame, weapon)
    if state is None:
        # pill unreadable - previously returned False with NO log, so a CL
        # toggle could fail silently forever
        logger.event("uw_fail", weapon=weapon, stage="read_pill",
                     shot=logger.shot(frame, f"uw_pill_{weapon}"))
        return False
    if state == want_on:
        return True                      # already in the wanted state
    pt = _uw_toggle_center(frame, weapon)
    if pt is None:
        logger.event("uw_fail", weapon=weapon, stage="locate_toggle")
        return False
    try:
        ev = act.tap(*pt, reason=f"uw_{weapon}_{'on' if want_on else 'off'}")
        logger.event("uw_toggle", weapon=weapon, want_on=want_on, **ev)
    except act.TapRefused as e:
        logger.event("tap_refused", button=f"uw_{weapon}", error=str(e))
        return False
    time.sleep(_SETTLE_SEC)
    return _uw_state(_quest_frame(guard), weapon) == want_on


def _hunt_uw(weapon: str, *, guard=None) -> bool:
    opts = {"guard": guard} if guard is not None else {}
    _scroll_to_top(**opts)
    prev_gray = None
    for _ in range(_SCROLL_STEPS + 1):
        frame = _quest_frame(guard)
        g = np.float32(cv2.cvtColor(
            capture.roi(frame, "upgrade_panel"), cv2.COLOR_BGR2GRAY))
        if prev_gray is not None:
            (_, dy), _ = cv2.phaseCorrelate(prev_gray, g)
            diff = float(np.mean(cv2.absdiff(prev_gray, g)))
            if abs(dy) < 15 and diff < 6.0:
                return False         # bottom reached without a match
        hit, _, _ = detect._match(capture.roi(frame, "upgrade_panel"),
                                  f"uw/{weapon}.png", 0.70)
        if hit:
            return True
        prev_gray = g
        _swipe_panel_down(**opts)
    return False


def _uw_box(frame, weapon):
    panel = capture.roi(frame, "upgrade_panel")
    hit, _, loc = detect._match(panel, f"uw/{weapon}.png", 0.70)
    return (panel, loc) if hit else (panel, None)


def _uw_state(frame, weapon) -> bool | None:
    """True=ON, False=OFF, None=can't tell. Pill sits below-left of the name."""
    panel, loc = _uw_box(frame, weapon)
    if loc is None:
        return None
    tpl = detect._tpl(f"uw/{weapon}.png")
    # Slice from the FULL FRAME, not the panel ROI: the bottom UW row
    # (Black Hole / Spotlight, 2026-08-16) matches its name inside the ROI
    # while its pill sits below the ROI's lower edge - the ROI slice came
    # back empty and the pair read as unreadable forever.
    box = CONFIG["rois"]["upgrade_panel"]
    x0, y0 = box[0] + loc[0], box[1] + loc[1] + tpl.shape[0]
    region = frame[y0:min(y0 + 130, CONFIG["rois"]["upgrade_panel"][1] + CONFIG["rois"]["upgrade_panel"][3]), x0:x0 + 180]
    # comparative read: absolute thresholds are brittle across accounts (the
    # battle glow bleeds through the card differently) - the pill is always
    # one of the two, so take the better match above a loose floor
    _, s_on, _ = detect._match(region, "uw/toggle_on.png", 0.99)
    _, s_off, _ = detect._match(region, "uw/toggle_off.png", 0.99)
    if max(s_on, s_off) < 0.55:
        return None
    return s_on > s_off


def _uw_toggle_center(frame, weapon):
    panel, loc = _uw_box(frame, weapon)
    if loc is None:
        return None
    box = CONFIG["rois"]["upgrade_panel"]
    tpl = detect._tpl(f"uw/{weapon}.png")
    x0, y0 = loc[0], loc[1] + tpl.shape[0]
    # Full-frame slice for the same reason as _uw_state: the bottom row's
    # pill lives below the panel ROI's lower edge.
    region = frame[box[1] + y0:min(box[1] + y0 + 130, box[1] + box[3]), box[0] + x0:box[0] + x0 + 180]
    # Comparative, exactly like _uw_state: an absolute 0.75 threshold here was
    # the reason Chain Lightning could never be switched ON - the OFF pill
    # scores ~0.705 on Main, so the toggle was never located and the tap never
    # happened. The pill is always one of the two; take the better match.
    best_score, best_loc, best_rel = 0.0, None, None
    for rel in ("uw/toggle_on.png", "uw/toggle_off.png"):
        _, score, tloc = detect._match(region, rel, 0.99)
        if score > best_score:
            best_score, best_loc, best_rel = score, tloc, rel
    if best_loc is None or best_score < 0.55:
        return None
    t = detect._tpl(best_rel)
    return (box[0] + x0 + best_loc[0] + t.shape[1] // 2,
            box[1] + y0 + best_loc[1] + t.shape[0] // 2)
