"""HUD Ad Gems are separate from free Store gems and orbiting diamonds.

The same offer also shows at the top of the left column on the Home screen. A
harvesting collector claims it there through the same windows, response delay,
recognition and input guards; only the screen check and the proof differ: Home
needs the exact Home screen, and only a stable, exact +6 on the Home gem
counter confirms a Home claim.

Response delay. A visible offer waits a delay drawn from the person's range,
then is verified on a fresh frame before any input. Two rules keep that wait
from costing the whole window:
- The delay is clamped to the time left in the window, including the first
  draw (_latest_response_due): the due time leaves one poll interval plus one
  recognition budget before the deadline, so the poll that finds it due can
  still verify and tap. With less than that left, the offer is attempted at
  once; the existing deadline guards still refuse a verification that cannot
  finish in time.
- An attempt that finds the offer moved sent no input and spent nothing: the
  chosen due time is kept (_held_response) and applies again at the next
  visible read, which is also a fresh recognition. Only a sent tap, an absent
  offer, a changed screen or the window's end ends that schedule.
"""
import time
import re
import random
import math
import traceback
import uuid
import subprocess
import cv2
from device import act, capture, overlays
from vision import wave_reader, screen, ocr, textocr
from runtime import logger
from runtime.stage_clock import clock
from interactions.tourney import Abort
from player.gather_settings import AD_GEM_FIELDS, resolve_ad_gem_options
from towerpilot_workflows.action_contract import StartToStart, effect_of
import settings

TEMPLATE = 'buttons/ad_gems_claim.png'
EDGE_INSET_PX = AD_GEM_FIELDS['edge_inset_px']['default']
OBSERVATION_TIMEOUT_SEC = 2.0
HOME_REWARD = 6                  # the amount find_claim_rect verified beside CLAIM
HOME_CONFIRMATION_SEC = 3.0      # Home counter reading after the tap; reads only, never input
HOME_CONFIRMATION_GAP_SEC = 0.5
# Under load one read (capture, exact-Home check, counter) took about 2 s, so
# 3 s held one or two reads and a real +6 went unconfirmed (r15d). Reading
# goes on to this many reads, within HOME_CONFIRMATION_MAX_SEC.
HOME_CONFIRMATION_MIN_READS = 3
HOME_CONFIRMATION_MAX_SEC = 10.0


class RecognitionIncomplete(Exception):
    """The bounded search cannot establish either presence or absence."""


def _remaining(deadline):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise RecognitionIncomplete('recognition_deadline')
    return remaining

def _six_amount(patch):
    """Read the amount beside the gem, excluding the icon and button border."""
    h,w=patch.shape[:2]
    amount=patch[int(h*.10):int(h*.52),int(w*.5):int(w*.8)]
    mask=cv2.inRange(amount,(180,180,180),(255,255,255))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    glyphs=[]
    for contour in contours:
        x,y,gw,gh=cv2.boundingRect(contour)
        if gh>=20 and gw>=6 and x>0 and y>0 and x+gw<mask.shape[1] and y+gh<mask.shape[0]:
            glyphs.append(mask[y:y+gh,x:x+gw])
    if len(glyphs)!=1:return False
    scores={}
    for d in range(10):
        path=settings.template_path(f'digits/{d}.png')
        if not path.exists():return False
        template=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
        if template is None:return False
        resized=cv2.resize(glyphs[0],(template.shape[1],template.shape[0]))
        scores[d]=float(cv2.matchTemplate(resized,template,cv2.TM_CCOEFF_NORMED)[0,0])
    return scores[6]>=.8 and scores[6]-max(v for k,v in scores.items() if k!=6)>=.1


def find_claim_rect(frame, *, deadline=None):
    """Return a verified rectangle, None for absence, or raise if incomplete.

    Each search has a local time cap; a caller's earlier deadline wins.
    No partially searched or timed-out frame can prove offer disappearance.
    """
    local_deadline = time.monotonic()+OBSERVATION_TIMEOUT_SEC
    deadline = local_deadline if deadline is None else min(deadline, local_deadline)
    _remaining(deadline)
    from player.bootstrap_layout import manifest
    rx,ry,rw,rh = manifest()['runtime_regions']['ad_gems']['search']
    rail=frame[ry:ry+rh,rx:rx+rw]
    template = cv2.imread(str(settings.template_path(TEMPLATE))) if settings.template_path(TEMPLATE).exists() else None
    if template is None:
        # Offers can be absent throughout setup (including at the daily cap).
        # Locate the cyan offer block in the manifest's left HUD rail, then
        # verify its label. Both frames in poll must independently pass.
        from vision import textocr
        hsv=cv2.cvtColor(rail,cv2.COLOR_BGR2HSV)
        # At claim time the border can turn neutral gray while CLAIM and 6
        # remain visible. Keep the masks separate: merging them connects
        # unrelated bright battle art to otherwise usable cyan borders.
        masks = (cv2.inRange(hsv,(75,60,150),(110,255,255)),
                 cv2.inRange(rail,(180,180,180),(255,255,255)))
        candidates = set()
        for index, mask in enumerate(masks):
            contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x,y,w,h = cv2.boundingRect(contour)
                if not (100 <= w <= 300 and 55 <= h <= 180):
                    continue
                # Neutral HUD bars often extend beyond the search rail.
                # Only a closed rectangular border is a neutral candidate;
                # its enclosed area, rather than stroke pixels, fills the box.
                if index == 1 and cv2.contourArea(contour) < .85*w*h:
                    continue
                candidates.add((x,y,w,h))
        candidates = sorted(candidates, key=lambda rect: (rect[1], rect[0]))
        # Battle effects and health bars can precede the offer. OCR every
        # eligible crop independently in one process, sharing the original
        # deadline; a top-to-bottom count cap starves lower HUD offers.
        crops = [rail[y:y+h,x:x+w] for x,y,w,h in candidates]
        try:
            texts = textocr.read_texts(crops, scales=(2,), timeout=_remaining(deadline),
                                       raise_on_error=True, urgent=True)
        except subprocess.TimeoutExpired as exc:
            raise RecognitionIncomplete('ocr_timeout') from exc
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise RecognitionIncomplete('ocr_error') from exc
        _remaining(deadline)
        if len(texts) != len(candidates):
            raise RecognitionIncomplete('ocr_error')
        for (x,y,w,h), text in zip(candidates, texts):
            _remaining(deadline)
            text = text.upper()
            matched = (re.search(r'\bCLAIM\b',text)
                       and (re.search(r'\b6\b',text) or _six_amount(rail[y:y+h,x:x+w])))
            _remaining(deadline)
            if matched:
                return (rx+x,ry+y,w,h)
        _remaining(deadline)
        return None
    # Left HUD column; search vertically because wall/cards change its height.
    hay=rail
    h,w=template.shape[:2]
    if h>=hay.shape[0] or w>=hay.shape[1]:return None
    _,score,_,loc=cv2.minMaxLoc(cv2.matchTemplate(hay,template,cv2.TM_CCOEFF_NORMED))
    _remaining(deadline)
    return (rx+loc[0],ry+loc[1],w,h) if score>=.92 else None


def find_claim(frame):
    """Return the center for consumers that only need offer recognition."""
    rect = find_claim_rect(frame)
    return (rect[0]+rect[2]//2, rect[1]+rect[3]//2) if rect else None


def _center_displacement(first, second):
    """Measure HUD movement without treating contour-size noise as movement."""
    return sum(abs((first[axis] + first[axis+2]//2)
                   - (second[axis] + second[axis+2]//2)) for axis in (0, 1))


def _claim_point(rect, rng, edge_inset_px=EDGE_INSET_PX):
    """Sample the interior, reserving room for shared jitter and press drift."""
    x, y, width, height = rect
    screen_size = settings.CONFIG['screen']
    left, top = max(0, x), max(0, y)
    right = min(screen_size['width'], x+width)-1
    bottom = min(screen_size['height'], y+height)-1
    margin = edge_inset_px + settings.CONFIG['tap']['jitter_px'] + act.TAP_DRIFT_PX
    if right-left < 2*margin or bottom-top < 2*margin:
        return None
    return (rng.randint(left+margin, right-margin),
            rng.randint(top+margin, bottom-margin))

class AdGemCollector:
    def __init__(self, enabled=True, delay=(3.0, 10.0), rng=None, options=None, *, guard=None, recorder=None):
        self.guard = guard
        self.recorder = recorder
        self.manual_command_id = None
        self._window_marks = None
        self._window_observation = None   # reads taken while the current window is open (see _count_read)
        self._schedule = StartToStart()
        self.enabled=enabled
        self.next_at=0.0
        self.next_window_at = 0.0
        self.window_deadline = None
        self.options = resolve_ad_gem_options(options)
        self.delay = tuple(float(value) for value in delay)
        if (len(self.delay) != 2 or not all(math.isfinite(value) for value in self.delay)
                or not 0 <= self.delay[0] <= self.delay[1]):
            raise ValueError('Ad Gem delay must be a finite, nonnegative ordered range')
        self.rng = rng if rng is not None else random
        self.offer_seen_at = None
        self.claim_at = None
        self._held_response = None       # (offer_seen_at, claim_at) of an attempt that found the offer moved
        self.window_started_at = None
        self.last_window_started_at = None
        self.manual_requested = False
        self.manual_active = False
        self.last_outcome = None
        self.next_observation_at = 0.0
        self.visibility = 'unknown'
        self.visibility_reason = 'not_observed'
        self.first_seen_at = None
        self.last_seen_at = None
        self.last_absent_at = None
        self.last_observed_at = None
        self.appearance_after_at = None
        self.claim_rect = None
        self.window_id = None
        self.offer_id = None
        self.claim_attempt_id = None
        self.blocker = None
        self.last_failed_attempt = None
        self.counters = dict(checks=0, seen=0, skips=0, attempts=0,
                             confirmed=0, windows_missed=0)
        self.durations = dict(scheduling_wait_sec=None, response_delay_sec=None,
                              verification_sec=None, observed_wait_sec=None)
        self.surface = 'battle'          # 'home' only while the host feeds exact Home frames
        self._window_home = False         # a Home attempt happened in the current window
        self._home_diagnostic_written = False
        self._trace_id = uuid.uuid4().hex[:12]
        self._window_count = self._offer_count = 0
        self._attempt_started_at = None
        self._attempt_timing = {}
        self._last_frame_evidence = {}

    def _epoch(self, value):
        return None if value is None else time.time() + value-time.monotonic()

    def busy(self):
        """Why the host must not stop now, or None at a safe point: a claim attempt is running, or an offer was seen
        and its claim is scheduled in the open window (leaving would lose that window's gems). An open window with no
        offer seen is not busy. A window past its deadline is closed here (no input, no capture): off the battle
        screen nothing else polls this routine, and its pending claim must not hold the host forever."""
        if self.window_deadline is not None and self.claim_attempt_id is None and time.monotonic() >= self.window_deadline:
            self._close_window()
        if self.claim_attempt_id is not None:
            return 'ad_claim_attempt'
        if self.claim_at is not None or self._held_response is not None:
            return 'ad_claim_pending'
        return None

    def _context(self):
        return dict(window_id=self.window_id, offer_id=self.offer_id,
                    claim_attempt_id=self.claim_attempt_id)

    def mark_unknown(self, reason='not_battle', *, keep_response=False):
        """Invalidate visibility without reading the device or claiming absence.

        keep_response keeps a visible offer's response schedule when nothing
        suggests the screen changed (no read was possible). It never permits
        input: a tap still needs a visible read, then a fresh verified capture.
        """
        if self.visibility != 'unknown' or self.visibility_reason != reason:
            self.visibility = 'unknown'
            self.visibility_reason = reason
            logger.event('ad_gems_offer_unknown', reason=reason,
                         last_observed_at=self._epoch(self.last_observed_at),
                         last_seen_at=self._epoch(self.last_seen_at), **self._context())
        if not keep_response:
            self._forget_offer()

    def _expire_observation(self, now):
        if (self.last_observed_at is not None
                and now-self.last_observed_at > self.options['poll_sec'][1]*2
                and self.visibility != 'unknown'):
            self.mark_unknown('observations_lapsed', keep_response=True)

    def _observe(self, frame, *, deadline=None, reads=None):
        """Recognize only the supplied frame; callers own all capture decisions. `reads` (FrameReads) lets the host's
        wave and screen readings of this very frame stand in for a second read."""
        read_wave = wave_reader.read_wave if reads is None else reads.wave
        identify = screen.identify if reads is None else reads.screen
        now = time.monotonic()
        deadline = min(now+OBSERVATION_TIMEOUT_SEC, deadline) if deadline is not None else now+OBSERVATION_TIMEOUT_SEC
        self.counters['checks'] += 1
        textocr.take_timing()          # a failure below owns only the OCR time of THIS read
        spent = {}                     # seconds of each non-OCR read of THIS observation, for an incomplete one
        try:
            _remaining(deadline)
            with clock.stage('ad.wave', into=spent):
                wave = read_wave(frame)
            _remaining(deadline)
            if self.surface == 'home':
                # Exactly Home: no wave, and no menu, dialog, store or overlay.
                with clock.stage('ad.screen', into=spent):
                    name = identify(frame).name if wave is None else None
                expected, missing = wave is None and name == 'home', 'not_home'
            else:
                with clock.stage('ad.screen', into=spent):
                    name = None if wave is None else identify(frame).name
                expected, missing = wave is not None and name == 'battle', 'not_battle'
            self._last_frame_evidence = dict(wave=wave, screen=name)
            _remaining(deadline)
            if not expected:
                self.mark_unknown(missing)
                return None
            with clock.stage('ad.claim', into=spent):
                point = find_claim_rect(frame, deadline=deadline)
            _remaining(deadline)
        except RecognitionIncomplete as exc:
            # An unfinished read (OCR timeout or error, spent budget) says
            # nothing about the screen, like a lapse: keep a visible offer's
            # response schedule. Only an absent read or a changed screen resets it.
            self.mark_unknown(str(exc), keep_response=True)
            self._count_read('incomplete')
            logger.event('ad_gems_recognition_incomplete', reason=str(exc),
                         duration_sec=round(time.monotonic()-now, 3), stages=textocr.take_timing(),
                         read_stages=spent, **self._context())
            return None
        self._count_read('absent' if point is None else 'visible')
        previous = self.visibility
        self.last_observed_at = now
        self.visibility_reason = 'home_frame' if self.surface == 'home' else 'battle_frame'
        self.claim_rect = point
        if point is None:
            self.visibility = 'absent'
            self.last_absent_at = now
            if previous == 'visible':
                logger.event('ad_gems_offer_lost',
                             first_seen_at=self._epoch(self.first_seen_at),
                             last_seen_at=self._epoch(self.last_seen_at),
                             waited_sec=round(now-self.first_seen_at, 3), **self._context())
            self._forget_offer()
            return None
        self.visibility = 'visible'
        self.last_seen_at = now
        if previous != 'visible':
            self._offer_count += 1
            self.offer_id = f'{self._trace_id}:offer:{self._offer_count}'
            self.first_seen_at = now
            self.appearance_after_at = self.last_absent_at if previous == 'absent' else None
            self.counters['seen'] += 1
            logger.event('ad_gems_offer_seen', claim_rect=point,
                         first_seen_at=self._epoch(now),
                         appearance_after_at=self._epoch(self.appearance_after_at),
                         appearance_bound_sec=None if self.appearance_after_at is None else
                         round(now-self.appearance_after_at, 3),
                         collection_active=self.window_deadline is not None,
                         next_window_at=self._epoch(self.next_window_at), **self._context())
        return point

    def _count_read(self, outcome):
        """Count one offer read taken while a window is open: `reads` are the searches tried, `incomplete` those that
        could not establish presence or absence, `absent` those that completed and found no offer (the rest saw it).
        A window with few completed reads says nothing about the offer; a recognizer that broke looks like many
        completed, all-absent reads, which is why the counts are recorded and the reason is not renamed."""
        observation = self._window_observation
        if observation is None:
            return
        observation['reads'] += 1
        if outcome in ('incomplete', 'absent'):
            observation[outcome] += 1

    def _finish_attempt(self, outcome, reason, **fields):
        now = time.monotonic()
        self.durations.update(self._attempt_timing,
                              verification_sec=round(now-self._attempt_started_at, 3),
                              observed_wait_sec=None if self.first_seen_at is None else
                              round(now-self.first_seen_at, 3))
        result = dict(outcome=outcome, reason=reason, confirmed=outcome == 'confirmed',
                      observed_at=time.time(), **self._context(),
                      **self.durations, **fields)
        self.last_outcome = result
        if outcome == 'confirmed':
            self.counters['confirmed'] += 1
            self.blocker = None
        else:
            self.last_failed_attempt = dict(result)
            self.blocker = reason
            if outcome in ('skipped', 'tap_refused', 'error'):
                self.counters['skips'] += 1
        logger.event('ad_gems_attempt_finished', **result)

    def _skip(self, reason, **fields):
        logger.event('ad_gems_skip', reason=reason, **self._context(), **fields)
        self._finish_attempt('skipped', reason, **fields)

    def configure(self, values):
        """Install live settings without resetting the schedule or offering a new window."""
        from player.run_controls import validate_patch
        validate_patch({'routines': {'ad_gems': values}})
        if values.get('enabled') is not None:
            self.enabled = values['enabled']
        if values.get('interval_sec') is not None:
            interval = values['interval_sec']
            self.options['confirmed_cooldown_sec'] = [interval, interval]
            if self.last_window_started_at is not None:
                self.next_window_at = StartToStart.reanchor(self.last_window_started_at, interval)
                if self.window_deadline is None:
                    self.next_at = self.next_window_at
        if values.get('window_sec') is not None:
            self.options['attempt_timeout_sec'] = values['window_sec']
            if self.window_deadline is not None:
                self.window_deadline = StartToStart.window_end(self.window_started_at, values['window_sec'], self.window_deadline)
                self.next_at = min(self.next_at, self.window_deadline)
                self._clamp_pending_response()
        if self.window_deadline is not None and time.monotonic() >= self.window_deadline:
            self._close_window()

    def request_now(self):
        if self.manual_requested or self.window_deadline is not None:
            return False
        self.manual_requested = True
        return True

    def status(self):
        now, epoch = time.monotonic(), time.time()
        if not self.enabled and not self.manual_active and not self.manual_requested:
            self.mark_unknown('disabled')
        self._expire_observation(now)
        blocker = self.blocker
        if self.visibility == 'unknown':
            blocker = self.visibility_reason
        elif not self.enabled and not self.manual_active:
            blocker = 'disabled'
        elif self.window_deadline is None:
            blocker = 'scheduled_pause'
        elif self.claim_at is not None and now < self.claim_at:
            blocker = 'response_delay'
        elif self.visibility == 'absent':
            blocker = 'offer_absent'
        elif self.claim_at is not None and now < self.next_at:
            blocker = 'next_check'
        return dict(enabled=self.enabled, state='active' if self.window_deadline is not None else 'scheduled' if self.enabled else 'off',
                    next_due_at=epoch + self.next_window_at-now if self.next_window_at else epoch,
                    window_ends_at=None if self.window_deadline is None else epoch+self.window_deadline-now,
                    manual_pending=self.manual_requested, manual_active=self.manual_active,
                    last_outcome=self.last_outcome, server_time=epoch,
                    visibility=self.visibility, visibility_reason=self.visibility_reason,
                    first_seen_at=self._epoch(self.first_seen_at),
                    last_seen_at=self._epoch(self.last_seen_at),
                    last_absent_at=self._epoch(self.last_absent_at),
                    last_observed_at=self._epoch(self.last_observed_at),
                    appearance_after_at=self._epoch(self.appearance_after_at),
                    observation_age_sec=None if self.last_observed_at is None else round(now-self.last_observed_at, 3),
                    stale_after_sec=self.options['poll_sec'][1]*2,
                    claim_rect=self.claim_rect,
                    response_due_at=self._epoch(self.claim_at),
                    response_started_at=self._epoch(self.offer_seen_at),
                    next_check_at=self._epoch(self.next_observation_at),
                    window_started_at=self._epoch(self.window_started_at),
                    blocker=blocker, last_failed_attempt=self.last_failed_attempt,
                    counters=dict(self.counters), durations=dict(self.durations), **self._context())

    def _forget_offer(self):
        self.offer_seen_at = None
        self.claim_at = None
        self._held_response = None

    def _latest_response_due(self):
        """The latest monotonic due time that still leaves a real chance to attempt before the window deadline.

        The first poll at or after the due time can come one poll interval late (the upper
        bound of poll_sec), and its fresh recognition may use the whole observation budget;
        a due time later than that is rejected by the expiry guards after the window is spent.
        """
        return self.window_deadline-self.options['poll_sec'][1]-OBSERVATION_TIMEOUT_SEC

    def _draw_response_delay(self, now):
        """One draw from the person's range, clamped to the window; returns (delay, drawn)."""
        drawn = self.rng.uniform(*self.delay)
        return min(drawn, max(0.0, self._latest_response_due()-now)), drawn

    def _clamp_pending_response(self):
        """A window shortened while a response waits must not leave its due time past the new deadline."""
        latest = max(time.monotonic(), self._latest_response_due())
        if self.claim_at is not None:
            self.claim_at = min(self.claim_at, latest)
        if self._held_response is not None:
            seen, due = self._held_response
            self._held_response = (seen, min(due, latest))

    def _close_window(self, reason='timeout'):
        if reason == 'timeout':
            self.counters['windows_missed'] += 1
        logger.event('ad_gems_window_ended', reason=reason,
                     pause_sec=round(max(0, self.next_window_at-time.monotonic()), 3),
                     duration_sec=round(time.monotonic()-self.window_started_at, 3),
                     counters=dict(self.counters), last_failed_attempt=self.last_failed_attempt,
                     observation=None if self._window_observation is None else dict(self._window_observation),
                     response_due_at=self._epoch(self.claim_at), **self._context())
        self.window_deadline = None
        self.window_started_at = None
        self.manual_active = False
        if reason != 'confirmed':
            self.last_outcome = dict(confirmed=False, reason=reason, observed_at=time.time(), **self._context())
        self._forget_offer()
        self.next_at = self.next_window_at
        self._record_close(reason)
        self._window_observation = None

    def _record_open(self, manual, scheduled_for):
        """Open this window's execution. Recording never changes what the collector does."""
        if self.recorder is None:
            return
        command_id, self.manual_command_id = (self.manual_command_id if manual else None), None
        self._window_marks = dict(attempts=self.counters['attempts'], confirmed=self.counters['confirmed'],
                                  seen=self.counters['seen'])
        self.recorder.opened(trigger='manual' if manual else 'automatic', occurrence_key=f'window:{self.window_id}',
                             scheduled_for=scheduled_for, command_id=command_id, refs=dict(window_id=self.window_id))

    def _record_close(self, reason):
        """Close it with what the window actually did: a confirmed claim, an attempt nobody could confirm, or
        nothing to claim. A window that sent input and proved nothing is `failed/unconfirmed`, never a success."""
        if self.recorder is None or self._window_marks is None:
            return
        marks, self._window_marks = self._window_marks, None
        attempts = self.counters['attempts'] - marks['attempts']
        confirmed = self.counters['confirmed'] - marks['confirmed']
        refs = dict(window_id=self.window_id)
        if self._window_home:
            refs['surface'] = 'home'
        if self._window_observation is not None:
            refs['observation'] = dict(self._window_observation)
        if confirmed or reason == 'confirmed':
            proof = (self.last_outcome or {}).get('reason') or 'confirmed'
            self.recorder.closed(result='succeeded', reason=proof,
                                 effect=effect_of('ad_gems', proof), attempts=attempts, confirmed=max(confirmed, 1),
                                 evidence=['balance_increased' if proof == 'balance_increased' else 'offer_disappeared'],
                                 refs=refs)
        elif attempts:
            failure = self.last_failed_attempt or {}
            self.recorder.closed(result='refused' if failure.get('outcome') == 'tap_refused' else 'failed',
                                 reason=failure.get('reason') or reason, effect='unconfirmed', attempts=attempts, refs=refs)
        else:
            self.recorder.closed(result='no_op', reason='no_offer_seen' if self.counters['seen'] == marks['seen'] else 'offer_not_claimed',
                                 effect='none', refs=refs)

    def _record_missed(self, count, scheduled_for):
        """Windows the loop never opened (it was busy, or away) leave one row, so the ledger can tell them from a window
        that opened and found no offer. Recording never changes what the collector does."""
        if self.recorder is None:
            return
        refs = dict(missed=count)
        if self.surface == 'home':
            refs['surface'] = 'home'
        self.recorder.record(result='no_op', reason='window_missed', effect='none', scheduled_for=scheduled_for, refs=refs)

    def _window_expired(self):
        if time.monotonic() >= self.window_deadline:
            self._close_window()
            return True
        return False

    def poll(self, frame, reads=None):
        """Observe carried battle frames even between collection windows.

        Passive checks never capture or input. Only the original collection
        window and response deadline authorize fresh verification and a tap.
        """
        self.leave_home()
        return self._poll(frame, reads)

    def poll_home(self, frame, reads=None):
        """Observe and claim the offer on the exact Home screen.

        Only the harvesting collector calls this, for a frame it identified as
        Home while harvesting still asks for it and no battle is active. The
        routine must be enabled: a disabled routine never acts here, even for a
        pending Run now; with it enabled, a pending Run now opens its window here
        as it would in battle. Windows, response delay, pacing and input guards
        are the battle ones.
        """
        if not self.enabled:
            self.mark_unknown('disabled')
            return frame
        with logger.context(surface='home'):
            if self.surface != 'home':
                self.mark_unknown('surface_changed')     # nothing seen in battle carries over to Home
                self.surface = 'home'
            return self._poll(frame, reads)

    def leave_home(self, reason='left_home'):
        """The host saw something other than the exact Home screen (a person
        navigating, a battle starting): forget the Home offer and close a window
        a Home observation was using, so Home input resumes only in a later
        window. No-op unless Home was being observed."""
        if self.surface != 'home':
            return
        with logger.context(surface='home'):
            self.surface = 'battle'
            self.mark_unknown(reason)
            if self.window_deadline is not None:
                self._close_window(reason)

    def _poll(self, frame, reads=None):
        if self.guard is not None:
            frame = self.guard(frame, self.surface)
        now = time.monotonic()
        if self.window_deadline is not None and now >= self.window_deadline:
            self._close_window()
        manual = self.manual_requested
        if not manual and not self.manual_active and not self.enabled:
            self.mark_unknown('disabled')
            return frame
        self._expire_observation(now)
        action_due = manual or now >= self.next_at
        if action_due and self.window_deadline is None:
            # Pausing does not award a missed window on re-enable: the policy advances over wholly elapsed
            # windows, preserving the previous anchor. The interval of a NEW window is drawn only when one opens.
            lower = self.options['confirmed_cooldown_sec'][0]
            decision, schedule = self._schedule.evaluate(
                now, dict(next_window_at=self.next_window_at, window_open=False),
                dict(manual=manual, interval=lower, skip_duration=min(self.options['attempt_timeout_sec'], lower)))
            if decision.kind == 'skip_missed':
                self._record_missed(decision.missed, self._epoch(self.next_window_at) if self.next_window_at else None)
                self.next_window_at = schedule['next_window_at']
                self.next_at = self.next_window_at
                self.counters['windows_missed'] += decision.missed
                logger.event('ad_gems_windows_missed', count=decision.missed,
                             next_window_at=self._epoch(self.next_window_at), **self._context())
                action_due = False
            elif decision.kind == 'due':
                scheduled_for = self._epoch(self.next_window_at) if self.next_window_at else None
                interval = self.rng.uniform(*self.options['confirmed_cooldown_sec'])
                window, schedule = self._schedule.open_window(now, schedule, interval, self.options['attempt_timeout_sec'])
                self.window_started_at = self.last_window_started_at = now
                self.manual_requested = False
                self.manual_active = manual
                self.next_window_at = schedule['next_window_at']
                duration = window['duration']
                self.window_deadline = window['deadline']
                self._window_count += 1
                self.window_id = f'{self._trace_id}:window:{self._window_count}'
                self.claim_attempt_id = None
                self.blocker = None
                self._window_home = False
                self._window_observation = dict(reads=0, incomplete=0, absent=0)
                self._home_diagnostic_written = False
                logger.event('ad_gems_window', duration_sec=duration, interval_sec=interval,
                             window_deadline=self._epoch(self.window_deadline),
                             next_window_at=self._epoch(self.next_window_at), **self._context())
                self._record_open(manual, scheduled_for)
        action_due = action_due and self.window_deadline is not None
        if not action_due and now < self.next_observation_at:
            return frame
        next_check = now+self.rng.uniform(*self.options['poll_sec'])
        self.next_observation_at = next_check
        if action_due:
            self.next_at = min(self.window_deadline, next_check)
        try:
            point = self._observe(frame, deadline=self.window_deadline, reads=reads)
        except Exception as exc:
            self.mark_unknown('recognition_error')
            logger.event('ad_gems_observation_error', error=str(exc),
                         error_type=type(exc).__name__, traceback=traceback.format_exc(),
                         **self._context())
            return frame
        if self.window_deadline is not None and self._window_expired():
            return frame
        now = time.monotonic()
        if not action_due or point is None:
            return frame
        if self.claim_at is None and self._held_response is not None:
            # The offer only moved under an earlier attempt: its chosen due time stands, and this read
            # is the fresh look at the new position; the attempt below verifies again before any input.
            self.offer_seen_at, self.claim_at = self._held_response
            self._held_response = None
            logger.event('ad_gems_response_kept', claim_rect=point,
                         response_due_at=self._epoch(self.claim_at),
                         response_overdue_sec=round(max(0, now-self.claim_at), 3), **self._context())
        elif self.claim_at is None:
            self.offer_seen_at = now
            delay, drawn = self._draw_response_delay(now)
            self.claim_at = now + delay
            self.durations = dict(scheduling_wait_sec=round(max(0, now-self.first_seen_at), 3),
                                  response_delay_sec=round(self.claim_at-now, 3),
                                  verification_sec=None, observed_wait_sec=round(max(0, now-self.first_seen_at), 3))
            clamped = dict(response_delay_drawn_sec=round(drawn, 3), response_clamped=True) if delay < drawn else {}
            logger.event('ad_gems_response_scheduled', claim_rect=point,
                         response_due_at=self._epoch(self.claim_at),
                         **self.durations, **clamped, **self._context())
        if now < self.claim_at:
            # Do not block farming while an offer waits. A later poll must
            # observe the offer again, then freshly verify it before input.
            self.next_at = min(self.next_at, self.claim_at)
            return frame
        offer_wait = now - self.offer_seen_at
        self._attempt_started_at = time.monotonic()
        self.counters['attempts'] += 1
        self.claim_attempt_id = f'{self._trace_id}:attempt:{self.counters["attempts"]}'
        self._attempt_timing = dict(self.durations)
        self._attempt_timing['response_wait_sec'] = round(offer_wait, 3)
        self._attempt_timing['response_overdue_sec'] = round(max(0, now-self.claim_at), 3)
        logger.event('ad_gems_attempt', claim_rect=point, **self._attempt_timing,
                     **self._context())
        response = (self.offer_seen_at, self.claim_at)
        self._forget_offer()
        try:
            with logger.context(**self._context()):
                return self._claim(frame, point, offer_wait, response)
        finally:
            # Current correlation belongs only to this synchronous attempt.
            # Terminal events and historical outcomes already hold its IDs.
            self.claim_attempt_id = None
            self._attempt_started_at = None

    def _claim(self, frame, point, offer_wait, response):
        """Keep all pre-input failures visible, with their complete diagnostics."""
        try:
            return self._verify_and_claim(frame, point, offer_wait, response)
        except Abort as exc:
            self.mark_unknown('ownership_lost')
            self._finish_attempt('interrupted', 'ownership_lost', error=str(exc),
                                 stage=self._attempt_stage, error_type=type(exc).__name__,
                                 traceback=traceback.format_exc())
            raise
        except Exception as exc:
            # No recovery input: capture/recognizer/foreground errors fail closed.
            self.mark_unknown('claim_verification_error')
            self._finish_attempt('error', 'claim_verification_error', error=str(exc),
                                 stage=self._attempt_stage, error_type=type(exc).__name__,
                                 traceback=traceback.format_exc())
            return self._attempt_latest_frame

    def _verify_and_claim(self, frame, point, offer_wait, response):
        self._attempt_latest_frame = frame
        # Recheck before touching a shifting HUD, not a remembered position.
        self._attempt_stage = 'fresh_capture'
        home = self.surface == 'home'
        if home:
            self._window_home = True
        fresh=capture.grab()
        if self.guard is not None:
            fresh = self.guard(fresh, self.surface)
        self._attempt_latest_frame = fresh
        self._attempt_stage = 'fresh_recognition'
        current = self._observe(fresh, deadline=self.window_deadline)
        if self.visibility == 'unknown':
            left = self.visibility_reason == ('not_home' if home else 'not_battle')
            self._skip(('fresh_frame_not_home' if home else 'fresh_frame_not_battle') if left
                       else 'fresh_recognition_incomplete',
                       recognition_reason=self.visibility_reason, **self._last_frame_evidence)
            if home and left:
                self.leave_home()             # a person navigating: nothing more in this window
            elif time.monotonic() >= self.window_deadline:
                self._close_window()
            return fresh
        if current is None:
            self._skip('fresh_offer_missing')
            return fresh
        # The cyan contour grows/shrinks with effects behind the HUD. Keep
        # the original 12px CENTER movement guard; summing all four rectangle
        # fields counted that harmless shape noise as movement and restarted
        # the entire response delay until the collection window expired.
        if _center_displacement(current, point)>12:
            self._held_response = response       # no input was sent: the chosen due time stands (see the module docstring)
            self._skip('offer_moved', observed_rect=point, fresh_rect=current,
                       center_displacement_px=_center_displacement(current, point))
            return fresh
        baseline = None
        if home:
            # A Home claim is proven only by the counter, so input needs a
            # readable baseline that the decision frame and the fresh frame agree on.
            self._attempt_stage = 'home_balance'
            reads = [ocr.read_gem_balance(image, ocr.HOME_GEM_BALANCE_REGION) for image in (frame, fresh)]
            if None in reads:
                self._home_balance_unreadable(fresh if reads[1] is None else frame, 'before_tap')
                self._skip('balance_unreadable', balance_reads=reads)
                return fresh
            if reads[0] != reads[1]:
                self._skip('balance_unstable', balance_reads=reads)
                return fresh
            baseline = reads[1]
        try:
            self._attempt_stage = 'foreground_evidence'
            visible_windows = overlays.windows(settings.instance()['serial'], visible_only=True)
            blocker = overlays.foreground_blocker(visible_windows)
        except Exception as exc:  # Missing window evidence must never permit a tap.
            self._skip('windows_unreadable', error=str(exc),
                       error_type=type(exc).__name__, traceback=traceback.format_exc())
            return fresh
        if blocker:
            self._skip(blocker, visible_windows=visible_windows)
            return fresh
        target = _claim_point(current, self.rng, self.options['edge_inset_px'])
        if target is None:
            self._skip('offer_too_small_for_safe_inset', claim_rect=current)
            return fresh
        # Capture, recognition and window-owner I/O may consume the remaining window.
        if time.monotonic() >= self.window_deadline:
            self._skip('window_expired_during_verification')
            self._close_window()
            return fresh
        self._attempt_timing['verification_before_tap_sec'] = round(time.monotonic()-self._attempt_started_at, 3)
        self._attempt_stage = 'claim_input'
        try:
            event=act.tap(*target,reason='HUD Ad Gems claim', deadline=self.window_deadline)
        except act.TapRefused as exc:
            logger.event('tap_refused',button='ad_gems',error=str(exc), **self._context())
            self._finish_attempt('tap_refused', 'tap_refused', error=str(exc),
                                 error_type=type(exc).__name__, traceback=traceback.format_exc())
            return fresh
        logger.event('ad_gems_tap',offer_wait_sec=round(offer_wait, 3),
                     claim_rect=current, edge_inset_px=self.options['edge_inset_px'],
                     verification_before_tap_sec=self._attempt_timing['verification_before_tap_sec'],
                     **self._context(), **event)
        if event.get('dry_run'):
            self._finish_attempt('dry_run', 'dry_run')
            self.next_at = min(self.window_deadline, time.monotonic()
                               + self.rng.uniform(*self.options['retry_cooldown_sec']))
            return fresh
        if home:
            return self._confirm_home(baseline, fresh)
        time.sleep(.35)
        self._attempt_stage = 'confirmation_capture'
        after=capture.grab()
        if self.guard is not None:
            after = self.guard(after, "battle")
        self._attempt_latest_frame = after
        # A menu/ad/death replacing the HUD is not evidence of a reward.
        self._attempt_stage = 'confirmation_recognition'
        self._observe(after)
        vanished = self.visibility == 'absent'
        reason = ('offer_disappeared' if vanished else
                  ('post_claim_not_battle' if self.visibility_reason == 'not_battle'
                   else 'post_claim_recognition_incomplete')
                  if self.visibility == 'unknown' else 'offer_still_visible')
        logger.event('ad_gems_claim',confirmed=vanished, reason=reason, **self._context())
        self._finish_attempt('confirmed' if vanished else 'not_confirmed', reason)
        if vanished:
            self._close_window('confirmed')
        elif not self._window_expired():
            self.next_at=min(self.window_deadline, time.monotonic()
                             + self.rng.uniform(*self.options['retry_cooldown_sec']))
        return after

    def _home_balance_unreadable(self, frame, stage):
        """Keep the exact counter pixels once per window; never capture again or write a template."""
        if self._home_diagnostic_written:
            return
        self._home_diagnostic_written = True
        crop = ocr.gem_balance_crop(frame, ocr.HOME_GEM_BALANCE_REGION)
        filename = logger.shot(crop, 'home_gem_balance_unreadable') if crop is not None else None
        logger.event('ad_gems_home_balance_unreadable', stage=stage, counter_frame=filename, **self._context())

    def _confirm_home(self, baseline, latest):
        """Read the Home counter after the tap, without input, for at most
        HOME_CONFIRMATION_SEC (at least HOME_CONFIRMATION_MIN_READS reads, never
        past HOME_CONFIRMATION_MAX_SEC). Only two consecutive exact baseline+6 reads on the
        exact Home screen confirm. Any other readable amount is a conflict; a
        screen that is not Home, or an unreadable or unchanged counter, proves
        nothing. Leaving Home or a conflict ends the window."""
        expected = baseline + HOME_REWARD
        started = time.monotonic()
        ends_at, gives_up_at = started + HOME_CONFIRMATION_SEC, started + HOME_CONFIRMATION_MAX_SEC
        reads, after, reason = [], latest, None
        while reason is None:
            time.sleep(.35 if not reads else HOME_CONFIRMATION_GAP_SEC)
            self._attempt_stage = 'confirmation_capture'
            after = capture.grab()
            if self.guard is not None:
                after = self.guard(after, 'home')
            self._attempt_latest_frame = after
            self._attempt_stage = 'confirmation_recognition'
            wave = wave_reader.read_wave(after)
            if wave is not None or screen.identify(after).name != 'home':
                reason = 'post_claim_not_home'
                break
            value = ocr.read_gem_balance(after, ocr.HOME_GEM_BALANCE_REGION)
            reads.append(value)
            if value is not None and value not in (baseline, expected):
                reason = 'balance_changed_without_confirmation'
            elif value == expected and reads[-2:] == [expected, expected]:
                reason = 'balance_increased'
            elif time.monotonic() >= ends_at and (len(reads) >= HOME_CONFIRMATION_MIN_READS
                                                  or time.monotonic() >= gives_up_at):
                reason = ('post_claim_balance_unreadable' if value is None else
                          'balance_unchanged' if value == baseline else 'balance_unstable')
        confirmed = reason == 'balance_increased'
        if not confirmed and reads and reads[-1] is None:
            self._home_balance_unreadable(after, 'confirmation')
        logger.event('ad_gems_claim', confirmed=confirmed, reason=reason, before=baseline,
                     after=reads[-1] if reads else None, reads=reads, **self._context())
        self._finish_attempt('confirmed' if confirmed else 'not_confirmed', reason,
                             before=baseline, after=reads[-1] if reads else None)
        if confirmed:
            self._close_window('confirmed')
        elif reason == 'post_claim_not_home':
            self.leave_home()
        elif reason == 'balance_changed_without_confirmation':
            self._close_window(reason)
        elif not self._window_expired():
            self.next_at = min(self.window_deadline, time.monotonic()
                               + self.rng.uniform(*self.options['retry_cooldown_sec']))
        return after
