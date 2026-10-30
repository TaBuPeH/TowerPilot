"""Shard farming - the human session, replayed as a loop.

Recorded from the user on 2026-08-13
(recordings/main/20260813_111519_shard_farm, 31 gestures + 46 paired frames)
and narrated by them step by step.

The idea: on Tier 18 a FLEET spawns at WAVE 95 (then every 100 waves). Killing
it pays 1650 Reroll Shards 80% of the time, or 5 module shards of one random
type the other 20% - the official table, unchanged by the v28.3 fleet
rebalance. Everything after that kill is dead weight, so the run is abandoned
immediately and restarted. One loop is roughly two minutes, most of which is
the Intro Sprint racing to wave 100.

Wave 95 is why waiting until the end of wave 101 makes the kill unconditional:
the fleet has been on the field for six waves by then, and fleets spawn even
on waves the sprint skips. It also removes any need to tell the three fleet
types apart - whichever one rolled, it is already there and it drops the same.

    SETUP (once)   end any live run -> the block's tier -> the shard_farm
                   loadout (deck, presets, modules as configured) -> BATTLE
    LOOP (forever) wave 100 -> cancel Intro Sprint
                   -> wave 101 nearly over -> NUKE
                   -> side menu -> EXIT BATTLE -> Surrender -> RETRY

RETRY restarts the same tier with the same cards and modules, which is why the
setup only runs once - the recorded session does the whole card/module dance
before the first battle and never again.

Two triggers make this work, and both are read, never timed:
  * the WAVE COUNTER for 100 - the sprint is cancelled the moment it lands
  * the WAVE PROGRESS bar (rois.wave_progress) for "101 is nearly over" -
    a clean sawtooth that ramps 0 -> ~0.85 and resets, so "almost over" is a
    threshold on it rather than a stopwatch

Shares tourney.py's navigation helpers rather than duplicating them: the
find/wait_for/require/tap_at/ensure_home/end_round machinery was built and
debugged for the tournament routine and the screens are the same.
"""
import random
import math
import sys
import time
import uuid
from pathlib import Path

# Flow files are runnable as scripts (`python flows/shard.py`) with the
# backend root as cwd - put that root on sys.path so sibling modules resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections.abc import Callable

import cv2
import numpy as np

from device import act
from device import capture
from scheduling import daystate
from vision import detect
from runtime import logger
from runtime.stage_clock import clock
from scheduling import runflag
from vision import screen
from vision import wave_reader
from vision.ocr import gem_balance_crop, read_gem_balance
from vision.orbit_gems import Glance, OrbitGem, OrbitSensing, field_region, find_orbit_gem, reference_geometry
from player.gather_settings import ORBIT_TIMING_FIELDS, validate_orbit_timing
from towerpilot_workflows.action_contract import AppearanceDriven
import settings
from settings import CONFIG

from interactions import loadout
from interactions import tourney
from interactions.tourney import (Abort, find, require, tap_at, ensure_home, on_home,
                     open_nav, return_to_game, NAV)

# What this flow is, for the registry (flows/__init__.py). The scheduler,
# compiler, tray and dashboard all read THIS instead of hardcoded tables.
FLOW = {
    "templates": ['buttons/retry.png',
     'home/game_stats_home.png',
     'home/battle_btn.png',
     'buttons/return_to_game.png'],
    "kind": "shard",
    "label": "Shard farming",
    "runner": "flows/shard.py",
    # The scheduler equips the blueprint's loadout and sets its tier before
    # this script takes over the loop.
    "handoff": "loadout",
    # The scheduler's remaining daily quota arrives here; 0 = unbounded.
    "count_arg": "--loops",
    "blueprint_args": [
        {"flag": "--tier", "fields": ["tier"]},
    ],
    "legacy_preset": "shard_farm",
    # The scheduler's handoff ends with BATTLE on screen; without this the
    # runner's own setup() walked Home over that battle and ended it - every
    # "Tier 18 / Wave 1 / Coins 0" line in the battle history (2026-09-05).
    "adopt_arg": "--no-setup",
}

# --- defaults ------------------------------------------------------------
# The tier used when nothing passes one (blueprints and presets do): 1 is
# always unlocked. The deck, presets and modules come from the `shard_farm`
# loadout in config.yaml - nothing about a build lives here.
TIER = 1

# --- chrome measured off the recording ---------------------------------
TIER_LEFT = (390, 1410)         # "<" on the home Difficulty selector
TIER_RIGHT = (700, 1405)        # ">"
TIER_BAND = ((1382, 1425), (552, 655))   # the number, arrows excluded
TIER_DIGIT_H = (26, 40)
HOME_BATTLE = (621, 2057)       # the big BATTLE button on the home screen

SPRINT_WAVE = 100               # cancel the Intro Sprint the moment this lands
NUKE_WAVE = 101                 # fire once this wave reaches the progress threshold
# Both are the TIER 18 values (first fleet at 95): run() re-derives them for
# the block's tier from the one fleet table (fleet_waves) before the loop.


def fleet_waves(tier) -> tuple[int, int]:
    """(sprint-cancel wave, Nuke wave) for `tier`: the fleet spawns at the
    tier's first fleet wave (scheduling/fleets.py), the sprint is cancelled
    five waves later and the Nuke fires as the wave after that begins - the
    T18 recipe (95 -> 100 -> 101) generalised, so a T19 block (fleet at 45)
    nukes at 51 instead of racing to 101. Unknown tier: the T18 defaults."""
    from scheduling import fleets
    first = fleets.first_wave(tier, CONFIG)
    if first is None:
        return SPRINT_WAVE, NUKE_WAVE
    return int(first) + 5, int(first) + 6
NUKE_AT_PROGRESS = 0.10         # how far through wave 101 to fire.
                                # Fires almost as soon as 101 starts. The fleet
                                # spawned back on wave 95 and has had six waves
                                # to walk in, so by the time 101 begins it is
                                # already in position - the earlier 0.85 ("the
                                # last readable frame of the wave") was buying
                                # certainty that wave 95 had already given.
                                # If yields ever drop, this is the first knob to
                                # put back up: a Nuke fired before the fleet is
                                # in range still logs fired=True but kills
                                # nothing, and the loop cannot tell the
                                # difference.
EXIT_POLL = 0.10        # how often to look for the next exit screen
ADB_MAX_FAILS = 5       # consecutive device recoveries before giving up: a
                        # dead emulator must not become an infinite retry loop
OFF_BATTLE_FRAMES = 10  # ~3s of no battle before it is treated as real
TIER_TRIES = 25    # was 12: Tier 14 -> Tier 1 (the ILM quest) needs 13 taps
                   # alone, and every miscounted arrow eats one more try
WAVE_TIMEOUT = 240.0            # generous: the sprint to wave 100 took ~77s



# Orbiting-gem arc harvest (initial reference measured 2026-09-07):
# the collectible gem (a magenta square + pink diamond) circles the tower CORE
# on a fixed path. The current frame's HUD diamond verifies the visible gem,
# whose measured radius places bounded taps on the 135deg (top-left) arc,
# clear of the ability row. A stable +2 HUD balance confirms collection;
# attempts without confirmation use a separate retry backoff. Centre is the tower core, NOT the field-ROI
# centre (which sits ~160px lower). Camera zoom shifts these per install, so all
# are overridable via gather.gem_orbit in a blueprint.
GEM_ORBIT_ANGLE = 135.0         # degrees above +x, the safe top-left quadrant
GEM_ORBIT_INTERVAL = ORBIT_TIMING_FIELDS["interval_sec"]["default"]
GEM_ORBIT_TAPS = 3              # points per set, spread over the arc
GEM_ORBIT_JITTER = 0.03         # +-3% radius/position randomisation
GEM_ORBIT_SPREAD = 6.0          # degrees between adjacent points in a set

# A readable wave can remain above a menu, and a screencap can retain a game
# frame behind another app. Require battle classification, visible foreground
# window evidence and motion around the tap arc before permitting a set.
# Guard ported from origin/develop (26d9ca6, 5f5e2b3).
ORBIT_PROBE_PAD = 60
ORBIT_MOTION_MIN = 8.0
ORBIT_MOTION_GAP = 0.4

# The game-speed widget ("- x5.0 +"), panel-open layout; rides the shifting
# bottom HUD, so capture.layout_offset applies to every y here.
SPEED_BAND = (1535, 1615)       # y-band of the label
SPEED_X = (730, 890)            # x-band of the label
SPEED_PLUS = (906, 1573)        # the '+' button
SPEED_TAPS = 6                  # x1 -> x5 is 4 presses; 6 bounds a bad read


def _in_ability_row(pt, margin: int = 25) -> bool:
    """Nothing speculative is ever tapped on the Nuke / Demon Mode buttons.

    Deliberately a copy of orchestrator._in_ability_row rather than an import: this
    module must not pull in the orchestrator's module-level state just to reuse five
    lines, and the rule is safety-critical enough to be visible where it is
    applied.
    """
    x, y, w, h = CONFIG["rois"]["ability_row"]
    y += capture.layout_offset
    return (x - margin <= pt[0] <= x + w + margin
            and y - margin <= pt[1] <= y + h + margin)


class GemWatch:
    """Collect Ad Gems and orbiting diamonds using their separate policies."""

    def __init__(self, gather=None, *, guard=None, session=None):
        """session: the host's `ActionSession`. Every host passes one (or None when it runs outside a ledger
        operation), so each window is recorded from one construction point."""
        self.guard = guard
        self.session = session
        opts = {"guard": guard} if guard is not None else {}
        self.orbit = GemOrbitTapper(**gem_orbit_opts(gather), **opts,
                                    recorder=None if session is None else session.recorder("floating_gems"))
        from interactions.ad_gems import AdGemCollector
        if gather is None:
            gather = (CONFIG.get("presets", {}).get(CONFIG.get("preset")) or {}).get("gather") or {}
        self.ad_gems = AdGemCollector(gather.get("ad_gems", True),
                                      delay=gather.get("gem_delay_sec", (3, 10)),
                                      options=gather.get("ad_gem_options"), **opts,
                                      recorder=None if session is None else session.recorder("ad_gems"))

    def busy(self):
        """What a host that wants to stop must wait out, as reason codes; empty at a safe point."""
        return [why for why in (self.ad_gems.busy(), self.orbit.busy()) if why]

    def poll(self, frame, reads=None):
        """Return the latest frame; callers must discard an older decision frame.
        reads: the host's `FrameReads` of `frame`; each owner reuses it only for that same frame object."""
        if self.guard is not None:
            frame = self.guard(frame, "battle")
        frame = self.ad_gems.poll(frame, reads=reads)
        if self.guard is not None:
            frame = self.guard(frame, "battle")
        # Idle orbit sensing reuses this frame; only a recognized appearance
        # opens the existing guarded capture/claim path.
        return self.orbit.poll(frame, reads=reads)


def _orbit_points(center, radius, angle, taps, jitter, spread):
    """The tap points for one harvest set: `taps` points spread symmetrically
    over the arc around `angle`, each with +-`jitter` radius and a small angular
    wobble. Screen y is DOWN, so `angle` degrees measured up from +x maps to
    (cos, -sin). Pure + deterministic-shaped (randomness only in the wobble) so
    it is unit-testable."""
    import math
    cx, cy = center
    pts = []
    for k in range(taps):
        a = angle + spread * (k - (taps - 1) / 2.0) + (random.uniform(-2.0, 2.0) if jitter else 0.0)
        r = radius * (1.0 + random.uniform(-jitter, jitter))
        x = cx + r * math.cos(math.radians(a))
        y = cy - r * math.sin(math.radians(a))           # up-left => smaller y
        pts.append((int(round(x)), int(round(y))))
    return pts


def _probe_box(points, pad: int = ORBIT_PROBE_PAD) -> tuple[int, int, int, int]:
    """(y0, y1, x0, x1) around the tap points: what must be moving."""
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return (max(0, min(ys) - pad), max(ys) + pad,
            max(0, min(xs) - pad), max(xs) + pad)


def battlefield_live(points, frame=None, *, guard=None) -> tuple[bool, str]:
    """Boolean guard contract for callers that do not consume its latest frame."""
    ok, why, _ = battlefield_observation(points, frame, guard=guard)
    return ok, why


def battlefield_observation(points: list[tuple[int, int]], frame: np.ndarray | None = None,
                            *, guard: Callable[[np.ndarray, str], np.ndarray] | None = None
                            ) -> tuple[bool, str, np.ndarray | None]:
    """Is the tower on screen with nothing over `points`, RIGHT NOW?

    Returns (ok, why, verified_frame); refusals expose no usable frame.
    `why` is logged on a refusal so a run that collects
    nothing says which gate stopped it rather than going quiet. See the note
    above GEM_ORBIT_* for why each of the three checks is here.
    """
    frame = capture.grab() if frame is None else frame
    if guard is not None:
        frame = guard(frame, "battle")
    if not points:
        return False, "no_points", None
    if wave_reader.read_wave(frame) is None:
        return False, "no_wave", None
    layout_offset = capture.layout_offset
    name = screen.identify(frame).name
    if name != "battle":
        return False, f"screen:{name}", None
    try:
        from device import overlays
        wins = overlays.windows(settings.instance()["serial"], visible_only=True)
    except Exception as exc:                      # noqa: BLE001
        # The window list is EVIDENCE, and evidence that cannot be read is not
        # permission: an adb hiccup must not turn into a blind tap.
        return False, f"windows_unreadable:{type(exc).__name__}", None
    # IN FRONT, not merely present. `dumpsys window windows` lists front to
    # back, and a backgrounded activity stays in it: the Play Store sat at #1
    # behind the game (#0) all day on 2026-10-13 while the game played
    # normally, so "is it in the list" would refuse every set forever. What
    # matters is whether anything that is not system chrome is ABOVE the game.
    blocker = overlays.foreground_blocker(wins)
    if blocker:
        return False, blocker, None
    y0, y1, x0, x1 = _probe_box(points)
    time.sleep(ORBIT_MOTION_GAP)
    after = capture.grab()
    if guard is not None:
        after = guard(after, "battle")
    if wave_reader.read_wave(after) is None:
        return False, "no_wave", None
    if capture.layout_offset != layout_offset:
        return False, "layout_changed", None
    name = screen.identify(after).name
    if name != "battle":
        return False, f"screen:{name}", None
    if frame.shape != after.shape or not (0 <= y0 < y1 <= frame.shape[0]
                                         and 0 <= x0 < x1 <= frame.shape[1]):
        return False, "invalid_probe", None
    motion = float(cv2.absdiff(frame[y0:y1, x0:x1],
                               after[y0:y1, x0:x1]).mean())
    if motion < ORBIT_MOTION_MIN:
        return False, f"static:{motion:.1f}", None
    return True, "live", after

ORBIT_SENSING_BEAT_SEC = 60     # idle sensing is logged this often (battle time), only while it senses


class GemOrbitTapper:
    """Recognized appearances open bounded attempts; stable +2 confirms a claim.

    Gem recognition never proves a reward. Each set retains the battlefield, window,
    motion, layout and ability-row guards. The observe loop runs between sets.

    Idle sensing is counted (`sensing()`): every frame its host hands over while the tapper is enabled, idle and
    free to look is tallied with what the recognizer made of it. The tally reads nothing: the frame is the one
    the host already judged part of a battle (the collector hands over only frames its own `live` check passed),
    and the tally is of the search that already runs. A finder that starts rejecting gems at the glyph check shows
    there as frames and near misses rising with nothing recognized (control/harvest.py decides what that means).
    Known blind spot: a gem rejected earlier (size, shape or pink border, or an unreadable HUD diamond) leaves
    only quiet frames, the same as an empty battle; only the frame counts and `reasons` show it.
    """

    def __init__(self, enabled=False, center=None,
                 radius=None, angle=GEM_ORBIT_ANGLE,
                 interval=GEM_ORBIT_INTERVAL, taps=GEM_ORBIT_TAPS,
                 jitter=GEM_ORBIT_JITTER, spread=GEM_ORBIT_SPREAD,
                 retry_interval_sec=None, attempt_timeout_sec=None,
                 retry_backoff_sec=None, guard=None, recorder=None):
        self.guard = guard
        self.recorder = recorder
        self.manual_command_id = None
        self._window_taps = 0
        self._schedule = AppearanceDriven()
        timing = dict(interval_sec=interval, retry_interval_sec=retry_interval_sec,
                      attempt_timeout_sec=attempt_timeout_sec, retry_backoff_sec=retry_backoff_sec)
        timing = {key: ORBIT_TIMING_FIELDS[key]['default'] if value is None else value
                  for key, value in timing.items()}
        errors = validate_orbit_timing(timing)
        if errors:
            raise ValueError('; '.join(errors))
        self.enabled = bool(enabled)
        self.center = tuple(center) if center is not None else None
        self.radius = float(radius) if radius is not None else None
        self.angle = float(angle)
        self.interval = float(timing['interval_sec'])
        self.retry_interval = float(timing['retry_interval_sec'])
        self.attempt_timeout = float(timing['attempt_timeout_sec'])
        self.retry_backoff = float(timing['retry_backoff_sec'])
        self.taps = int(taps)
        self.jitter = float(jitter)
        self.spread = float(spread)
        self.next_at = 0.0
        self.next_window_at = 0.0
        self._last_skip = None
        self.last_refusal = None
        self.last_window_started_at = None
        self._timeout_ready_at = 0.0
        self._failed_retry_anchor = None
        self.manual_requested = False
        self.manual_active = False
        self.last_outcome = None
        self.window_id = None
        self.attempt_id = None
        self._sensing = OrbitSensing()
        self._beat_started_at = None
        self._beat_due = False
        self._reset_attempt()

    def configure(self, values):
        from player.run_controls import validate_patch
        validate_patch({'routines': {'floating_gems': values}})
        if values.get('enabled') is not None:
            self.enabled = values['enabled']
        if values.get('interval_sec') is not None:
            self.interval = values['interval_sec']
            if self.last_window_started_at is not None and (
                    self.attempt_started is not None or not self.last_outcome
                    or not self.last_outcome['confirmed']):
                anchor = self.last_window_started_at
                if self.attempt_started is None and self.last_outcome is not None:
                    anchor = self._failed_retry_anchor
                self._set_retry_spacing(anchor)
        if values.get('window_sec') is not None:
            self.attempt_timeout = values['window_sec']
            if self.attempt_started is not None:
                self.window_ends_at = self._schedule.window_end(self.attempt_started, self.attempt_timeout, self.window_ends_at)
                self.next_at = min(self.next_at, self.window_ends_at)
        if values.get('tap_interval_sec') is not None:
            self.retry_interval = values['tap_interval_sec']
        self._expire_attempt(None)

    def request_now(self):
        if self.manual_requested or self.attempt_started is not None:
            return False
        self.manual_requested = True
        return True

    def busy(self):
        """Why the host must not stop now, or None at a safe point: a recognized appearance's attempt window is open
        (taps may have been sent and the +2 not yet confirmed). The tap burst itself runs inside poll(), so it never
        spans a host's loop iteration. An attempt past its input window is expired here (no input, no capture): off
        the battle screen nothing else polls the tapper, and an expired attempt must not hold the host forever."""
        if self.attempt_started is not None:
            self._expire_attempt(None)
        return 'orbit_attempt' if self.attempt_started is not None else None

    def status(self):
        now, epoch = time.monotonic(), time.time()
        return dict(enabled=self.enabled, state='active' if self.attempt_started is not None else 'scheduled' if self.enabled else 'off',
                    next_due_at=epoch+self.next_window_at-now if self.next_window_at else epoch,
                    window_ends_at=None if self.attempt_started is None else epoch+self.window_ends_at-now,
                    manual_pending=self.manual_requested, manual_active=self.manual_active,
                    window_id=self.window_id, attempt_id=self.attempt_id,
                    last_outcome=self.last_outcome, last_refusal=self.last_refusal,
                    server_time=epoch)

    def sensing(self):
        """What idle sensing has seen so far (OrbitSensing.snapshot), None before its first frame."""
        return self._sensing.snapshot()

    def _sense(self, frame):
        """One idle search for an appearance, counted. Returns the gem or None, exactly as find_orbit_gem would."""
        glance = Glance()
        with clock.stage('orbit.search'):
            gem = find_orbit_gem(frame, layout_offset=capture.layout_offset, glance=glance)
        self._sensing.record(glance, gem is not None)
        now = time.monotonic()
        if self._beat_started_at is None:
            self._beat_started_at = now
        elif now - self._beat_started_at >= ORBIT_SENSING_BEAT_SEC:
            self._beat_started_at, self._beat_due = now, True
        return gem

    def _epoch(self, value):
        return None if value is None else time.time() + value - time.monotonic()

    def _record_open(self, manual, scheduled_for):
        """Open this attempt's execution. Recording never changes what the tapper does."""
        if self.recorder is None:
            return
        command_id, self.manual_command_id = (self.manual_command_id if manual else None), None
        self._window_taps = 0
        self.recorder.opened(trigger='manual' if manual else 'automatic', occurrence_key=f'window:{self.window_id}',
                             scheduled_for=scheduled_for, command_id=command_id, refs=dict(window_id=self.window_id))

    def _record_close(self, reason):
        """A confirmed balance increase is a success; taps nobody could confirm are `failed/unconfirmed`; an attempt that
        sent no tap is a no-op carrying the last refusal that stopped it."""
        if self.recorder is None or self.recorder.execution_id is None:
            return
        taps, self._window_taps = self._window_taps, 0
        refs = dict(window_id=self.window_id)
        if reason == 'confirmed':
            self.recorder.closed(result='succeeded', reason='balance_increased', effect='confirmed', attempts=taps,
                                 confirmed=1, evidence=['balance_increased'], refs=refs)
        elif taps:
            self.recorder.closed(result='failed', reason='attempt_timeout', effect='unconfirmed', attempts=taps, refs=refs)
        else:
            self.recorder.closed(result='no_op', reason=(self.last_refusal or {}).get('reason') or 'no_taps', effect='none', refs=refs)

    def _reset_attempt(self):
        self.balance_before = None
        self.attempt_started = None
        self.sent_tap = False
        self._attempt_had_input = False
        self.manual_active = False
        self._balance_diagnostic_written = False
        self._gem_observation_states = set()
        self._gem_diagnostic_stages = set()
        self._input_ready_at = 0.0
        self._confirmation_ends_at = 0.0

    def _balance_unreadable(self, frame, stage):
        # Keep the exact supplied pixels, once per window; never capture again
        # here or turn an unknown reading into evidence of a reward.
        if not self._balance_diagnostic_written:
            self._balance_diagnostic_written = True
            crop = gem_balance_crop(frame)
            filename = logger.shot(crop, 'gem_balance_unreadable') if crop is not None else None
            logger.event('gem_orbit_balance_unreadable', stage=stage,
                         counter_frame=filename, before=self.balance_before,
                         tap_sent=self.sent_tap)
        self._backoff('balance_unreadable')

    def _pending_balance(self) -> int | None:
        return self.balance_before if self.sent_tap else None

    def _is_collection_balance(self, value: int | None) -> bool:
        baseline = self._pending_balance()
        return baseline is not None and value == baseline + 2

    def _wait_to_retry(self) -> None:
        self.next_at = time.monotonic() + self.retry_interval
        self._input_ready_at = self.next_at

    def _backoff(self, reason):
        self.last_refusal = dict(reason=reason, observed_at=time.time(),
                                 window_id=self.window_id, attempt_id=self.attempt_id)
        if reason != self._last_skip:
            logger.event("gem_orbit_skip", reason=reason)
            self._last_skip = reason
        now = time.monotonic()
        if reason in ('balance_unreadable', 'gem_not_detected'):
            # A refused observation dispatched no input. Retry sensing on the
            # next supplied frame once actual input pacing permits another set.
            # Preserve pending proof; never invent a counter or gem reading.
            self.next_at = min(max(now, self._input_ready_at), self.window_ends_at)
        else:
            self.balance_before = None
            self.sent_tap = False
            self.next_at = min(now + self.retry_backoff, self.window_ends_at)
            self._input_ready_at = self.next_at

    def _orbit_geometry(self) -> tuple[tuple[float, float], float]:
        center, radius = reference_geometry()
        center = center if self.center is None else self.center
        radius = radius if self.radius is None else self.radius
        return (center[0], center[1] + capture.layout_offset / 2), radius

    def points(self, radius: float | None = None) -> list[tuple[int, int]]:
        center, configured_radius = self._orbit_geometry()
        radius = configured_radius if radius is None else radius
        return _orbit_points(center, radius, self.angle, self.taps,
                             self.jitter, self.spread)

    def _record_gem_unknown(self, frame: np.ndarray, stage: str) -> None:
        if stage not in self._gem_diagnostic_stages:
            self._gem_diagnostic_stages.add(stage)
            fx, fy, fw, fh = field_region(capture.layout_offset)
            crop = (frame[fy:fy+fh, fx:fx+fw]
                    if getattr(frame, 'ndim', None) == 3
                    and 0 <= fx < fx+fw <= frame.shape[1]
                    and 0 <= fy < fy+fh <= frame.shape[0] else None)
            filename = logger.shot(crop, 'gem_not_detected') if crop is not None else None
            logger.event('gem_orbit_recognition_unknown', stage=stage, field_frame=filename)

    def _gem_geometry(self, frame, stage: str) -> tuple[OrbitGem, float] | None:
        gem = find_orbit_gem(frame, layout_offset=capture.layout_offset)
        visible = gem is not None
        center, configured_radius = self._orbit_geometry()
        radius = math.dist(gem.center, center) if visible else None
        if visible not in self._gem_observation_states:
            self._gem_observation_states.add(visible)
            logger.event('gem_orbit_geometry', stage=stage,
                         recognition='visible' if visible else 'not_detected',
                         orbit_center=center, configured_radius=configured_radius,
                         measured_radius=radius,
                         gem_center=gem.center if visible else None,
                         gem_rect=gem.rect if visible else None,
                         score=gem.score if visible else None)
        if not visible:
            self._record_gem_unknown(frame, stage)
            self._backoff('gem_not_detected')
            return None
        return gem, radius

    def _input_window_closed(self) -> bool:
        return self.attempt_started is not None and time.monotonic() >= self.window_ends_at

    def _expire_attempt(self, balance: int | None) -> bool:
        if not self._input_window_closed():
            return False
        # Input still ends at window_ends_at. A recently dispatched tap keeps
        # only its existing retry period to supply stable reward evidence.
        # This cutoff is frozen at dispatch; unreadable pixels cannot extend it.
        if self._pending_balance() is not None and time.monotonic() < self._confirmation_ends_at:
            return False
        with logger.context(window_id=self.window_id, attempt_id=self.attempt_id):
            logger.event("gem_orbit_claim", confirmed=False, reason="attempt_timeout",
                         before=self.balance_before, after=balance)
        self.last_outcome = dict(confirmed=False, reason='attempt_timeout', observed_at=time.time(),
                                 window_id=self.window_id, attempt_id=self.attempt_id)
        self._failed_retry_anchor = self._schedule.retry_anchor(self.attempt_started, self._attempt_had_input)
        input_ready_at = self._input_ready_at
        self._reset_attempt()
        # Only real dispatched input consumes the long retry spacing. An
        # optical refusal still keeps safety backoff and prior input pacing.
        self._timeout_ready_at = self._schedule.safety_floor(time.monotonic(), self.retry_backoff, input_ready_at)
        self._set_retry_spacing(self._failed_retry_anchor)
        self._record_close('timeout')
        return True

    def _set_retry_spacing(self, anchor: float | None) -> None:
        """Keep the timeout safety floor separate from editable retry spacing."""
        self.next_window_at = self._schedule.spacing_floor(anchor, self.interval, self._timeout_ready_at)
        if self.attempt_started is None:
            self.next_at = self.next_window_at

    def poll(self, frame, reads=None):
        candidate = self.window_id if self.attempt_started is not None else uuid.uuid4().hex
        attempt_id = uuid.uuid4().hex
        with logger.context(window_id=candidate, attempt_id=attempt_id):
            result = self._poll(frame, reads)
        if self._beat_due:                  # outside the window context: the beat belongs to no window
            self._beat_due = False
            logger.event('gem_orbit_sensing', **self._sensing.snapshot())
        return result

    def _poll(self, frame, reads=None):
        read_wave = wave_reader.read_wave if reads is None else reads.wave
        identify = screen.identify if reads is None else reads.screen
        if self.guard is not None:
            frame = self.guard(frame, "battle")
        if self._expire_attempt(None):
            return frame
        manual = self.manual_requested
        if not manual and not self.manual_active and not self.enabled:
            return frame
        confirmation_only = self._input_window_closed() or (not manual and time.monotonic() < self.next_at)
        if confirmation_only and not (self.attempt_started is not None
                                      and self._pending_balance() is not None):
            return frame
        if self.attempt_started is None:
            # UNKNOWN is not absence. It authorizes neither an attempt nor
            # more captures: inspect only the loop's already supplied pixels.
            # An explicit manual request retains its bounded seeking attempt.
            with clock.stage('orbit.appearance'):    # includes orbit.search and the reads a hit triggers
                appearance = manual or (
                    self._sense(frame) is not None
                    and read_wave(frame) is not None
                    and identify(frame).name == 'battle')
            decision, _ = self._schedule.evaluate(
                time.monotonic(), dict(next_window_at=self.next_window_at, window_open=False),
                dict(manual=manual, appearance=appearance))
            if decision.kind != 'due':
                return frame
            scheduled_for = self._epoch(self.next_window_at) if self.next_window_at else None
            window = self._schedule.open_window(time.monotonic(), self.attempt_timeout)
            self.attempt_started = window['started_at']
            self._timeout_ready_at = 0.0
            self._failed_retry_anchor = None
            self.window_id = logger.current_context()['window_id']
            self.last_window_started_at = self.attempt_started
            self.manual_requested = False
            self.manual_active = manual
            self._set_retry_spacing(self.attempt_started)
            self.window_ends_at = window['deadline']
            logger.event("gem_orbit_window", duration_sec=window['duration'],
                         interval_sec=self.interval)
            self._record_open(manual, scheduled_for)
        self.attempt_id = logger.current_context()['attempt_id']
        if read_wave(frame) is None:
            self._backoff("no_wave")
            return frame
        prior_frame = frame
        prior_balance = read_gem_balance(frame)
        # Backoff paces input, not proof of an already dispatched tap. Inspect
        # supplied frames without more I/O until a readable change warrants
        # the same fresh-pair validation used on a normal collection poll.
        if confirmation_only and (prior_balance is None or prior_balance == self.balance_before):
            return frame
        if prior_balance is None and self.balance_before is None:
            # No pending baseline can be confirmed or contradicted by another
            # reading. Keep these pixels and retry on the next supplied frame.
            self._balance_unreadable(prior_frame, 'prior')
            return frame
        frame = capture.grab()
        if self.guard is not None:
            frame = self.guard(frame, "battle")
        if wave_reader.read_wave(frame) is None or screen.identify(frame).name != "battle":
            self._backoff("not_in_battle")
            return frame
        balance = read_gem_balance(frame)
        if self._expire_attempt(balance):
            return frame
        # Unknown or not-yet-stable pairs must not hide a readable conflicting
        # amount. Only the baseline and a dispatched tap's expected reward are
        # compatible; confirming that reward still requires both fresh reads.
        if self.balance_before is not None and any(
                value is not None and value != self.balance_before
                and not self._is_collection_balance(value)
                for value in (prior_balance, balance)):
            self._backoff('balance_changed_without_confirmation')
            return frame
        if balance is None or prior_balance is None:
            self._balance_unreadable(prior_frame if prior_balance is None else frame,
                                     'prior' if prior_balance is None else 'fresh')
            return frame
        if self.balance_before is not None and balance != self.balance_before:
            if self._is_collection_balance(balance):
                if prior_balance == balance:
                    # A confirmed collectible is gone. Resume appearance
                    # sensing after input pacing, without another timed window.
                    ready_at = self._schedule.resume_at(time.monotonic(), self._input_ready_at)
                    pause = ready_at - time.monotonic()
                    logger.event("gem_orbit_claim", confirmed=True,
                                 before=self.balance_before, after=balance, pause_sec=round(pause, 3))
                    self.last_outcome = dict(confirmed=True, reason='balance_increased', observed_at=time.time(),
                                             window_id=self.window_id, attempt_id=self.attempt_id)
                    self._reset_attempt()
                    self.next_at = self.next_window_at = ready_at
                    self._record_close('confirmed')
                else:
                    # Let the HUD settle, without sending another tap.
                    self._wait_to_retry()
                return frame
            self._backoff("balance_changed_without_confirmation")
            return frame
        if balance != prior_balance:
            self._backoff("balance_unstable")
            return frame
        if confirmation_only or self._input_window_closed():
            return frame
        layout_offset = capture.layout_offset
        geometry = self._gem_geometry(frame, 'fresh')
        if geometry is None:
            return frame
        _, measured_radius = geometry
        points = self.points(measured_radius)
        fx, fy, fw, fh = field_region(layout_offset)
        if any(not (fx <= x < fx+fw and fy <= y < fy+fh) for x, y in points):
            self._backoff('invalid_orbit_arc')
            return frame
        ok, why, verified_frame = battlefield_observation(points, frame,
                                    **({"guard": self.guard} if self.guard is not None else {}))
        if not ok or verified_frame is None:
            self._backoff(why if not ok else 'battle_frame_unreadable')
            return frame
        self._last_skip = None
        if self.balance_before is None:
            self.balance_before = balance
        for index, (x, y) in enumerate(points):
            # The motion probe's final frame is already fresh. Keep its
            # pixels for the first input decision instead of discarding them
            # and requiring another independent clean observation.
            if index:
                # The rate cap paces each tap. Wait it out BEFORE capturing so
                # the frame that aims this tap is not older than the wait.
                act.wait_for_tap_slot()
            frame = verified_frame if index == 0 else capture.grab()
            motion_balance = read_gem_balance(frame) if index == 0 else None
            motion_unknown = (
                index == 0
                and motion_balance == self.balance_before
                and find_orbit_gem(frame, layout_offset=layout_offset) is None
            )
            if motion_unknown:
                self._record_gem_unknown(frame, 'motion')
            if index == 0 and (motion_balance is None or motion_unknown):
                # Retain the original fresh-capture opportunity when the
                # motion frame cannot supply optical evidence for input.
                # A readable changed balance must reach the conflict/reward
                # handling below; a missing gem must never hide it.
                frame = capture.grab()
            if self.guard is not None:
                frame = self.guard(frame, "battle")
            if wave_reader.read_wave(frame) is None or screen.identify(frame).name != "battle":
                self._backoff("not_in_battle")
                return frame
            if capture.layout_offset != layout_offset:
                self._backoff("layout_changed")
                return frame
            balance = read_gem_balance(frame)
            if self._expire_attempt(balance):
                return frame
            if balance != self.balance_before:
                if self._is_collection_balance(balance):
                    self._wait_to_retry()
                elif balance is None:
                    self._balance_unreadable(frame, 'pre_tap')
                else:
                    self._backoff("balance_changed_without_confirmation")
                return frame
            if self._input_window_closed():
                return frame
            if _in_ability_row((x, y)):
                self._backoff("ability_row")
                return frame
            geometry = self._gem_geometry(frame, 'pre_tap')
            if geometry is None:
                return frame
            gem, current_radius = geometry
            # Camera/layout changes cannot turn an old arc into a new target.
            # Keep radius error well inside the recognized square's half-width.
            if abs(current_radius-measured_radius) > .15*min(gem.rect[2:]):
                self._backoff('gem_geometry_changed')
                return frame
            try:
                event = act.tap(x, y, reason="gem_orbit", deadline=self.window_ends_at)
                logger.event("gem_orbit_tap", gem_center=gem.center,
                             measured_radius=current_radius, **event)
                dispatched = not event.get('dry_run', False)
                self.sent_tap = self.sent_tap or dispatched
                self._attempt_had_input = self._attempt_had_input or dispatched
                self._window_taps += 1 if dispatched else 0
                # Interrupted sets must retain pacing from their last input,
                # while the remaining taps inside this guarded set stay paced
                # by the shared input boundary as before.
                self._wait_to_retry()
                if dispatched:
                    self._confirmation_ends_at = self._input_ready_at
            except act.TapRefused as exc:
                logger.event("tap_refused", button="gem_orbit", error=str(exc))
                self._backoff("tap_refused")
                return frame
        self._wait_to_retry()
        frame = capture.grab()  # no caller acts on a frame from before the last tap
        return self.guard(frame, "battle") if self.guard is not None else frame


def _kick_adb():
    """Bring the device back after a capture failure: adbclient.recover over
    the server socket (host:connect for a missing device; for an offline one
    host:disconnect first, but only after adbclient._OFFLINE_STALE_S offline
    in this process, otherwise just the connect), and `adb start-server`
    through run_hidden only when no server listens - the sanctioned daemon-lifecycle
    spawn (CLAUDE.md), window-suppressed. Logs the state it ends on.

    Never kill-server (architecture review R15): the first version killed
    the server on every capture failure, which drops MuMu's TCP device with
    it and bounces the daemon for every other client."""
    from settings import CONFIG as _C, run_hidden
    from device import adbclient
    exe = _C["adb"]["exe"]
    serial = _C["instances"][_C["active_instance"]]["serial"]

    def start_server():
        try:
            run_hidden([exe, "start-server"], capture_output=True, timeout=20)
        except Exception:                       # noqa: BLE001 - recover re-reads the state
            pass
    logger.event("shard_adb_recover", state=adbclient.recover(serial, start_server))


def read_tier(frame) -> int | None:
    """The Difficulty number on the home screen, or None.

    Read rather than counted: the recorded session overshot to Tier 17 and
    corrected back, so replaying a fixed number of arrow taps would land
    somewhere different every time.
    """
    import cv2
    (y0, y1), (x0, x1) = TIER_BAND
    sub = frame[y0:y1, x0:x1]
    if sub.size == 0:
        return None
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = sorted(b for b in map(cv2.boundingRect, contours)
                   if TIER_DIGIT_H[0] <= b[3] <= TIER_DIGIT_H[1] and b[2] >= 5)
    if not boxes:
        return None
    tpls = wave_reader._load_templates()
    digits = []
    for x, y, w, h in boxes:
        glyph = bw[y:y + h, x:x + w]
        best, score = None, 0.55
        for d, tpl in tpls.items():
            g = cv2.resize(glyph, (tpl.shape[1], tpl.shape[0]))
            s = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)[0][0]
            if s > score:
                best, score = d, s
        if best is None:
            return None
        digits.append(best)
    return int("".join(map(str, digits)))


def set_tier(want: int = TIER):
    """Nudge the Difficulty selector until it reads `want`.

    BURST-THEN-VERIFY (user, 2026-08-28: "make the clicks between tiers
    way faster, like 140 msec"): the old loop paid a ~350ms frame grab per
    single step, ~1.5s per tier. Now the delta is read once and fired as a
    burst of instant taps - act's rate cap + jitter paces them at roughly
    the asked-for 140ms - then ONE grab verifies; any missed or extra step
    falls back to the original verified single-step loop.
    """
    for attempt in range(TIER_TRIES):
        frame = capture.grab()
        if not on_home(frame):
            raise Abort("not on the home screen while setting the tier")
        cur = read_tier(frame)
        if cur is None:
            logger.shot(frame, "shard_tier_unreadable")
            raise Abort("cannot read the Difficulty tier")
        if cur == want:
            logger.event("shard_tier", tier=cur, set=True)
            return frame
        steps = abs(want - cur)
        pt = TIER_RIGHT if cur < want else TIER_LEFT
        if attempt == 0 and steps > 1:
            # instant taps log no events (act contract) - one event for
            # the whole burst instead
            logger.event("shard_tier_burst", tier=cur, want=want,
                         steps=steps)
            for _ in range(steps):
                act.tap(*pt, reason=f"tier burst -> {want}", instant=True)
            continue
        tap_at(pt, f"tier {cur} -> {want}")
    logger.shot(capture.grab(), "shard_tier_stuck")
    raise Abort(f"could not reach Tier {want}")


def preset_active(frame, pt) -> bool:
    """Is the preset tab under `pt` the selected one?

    The selected tab is outlined in GREEN, the others in CYAN - and that is the
    only difference, the label art is identical either way. Measured across the
    five tabs of a recorded frame with the fifth tab selected:

        tab 1       green 0.000  cyan 0.077
        tab 2       green 0.000  cyan 0.056
        tab 3       green 0.000  cyan 0.058
        tab 4       green 0.000  cyan 0.055
        tab 5       green 0.030  cyan 0.000      <- selected

    Cleanly separated, so it is decided on which hue is present rather than on
    a threshold either one has to clear.
    """
    import cv2
    x0, x1 = max(0, pt[0] - 100), pt[0] + 100
    y0, y1 = max(0, pt[1] - 44), pt[1] + 44
    box = frame[y0:y1, x0:x1]
    if box.size == 0:
        return False
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 110) & (v > 140)
    green = float(((h > 40) & (h < 80) & lit).mean())
    cyan = float(((h > 85) & (h < 105) & lit).mean())
    return green > cyan


def gem_orbit_opts(gather=None) -> dict:
    """GemOrbitTapper kwargs from the active blueprint's gather.gem_orbit policy.
    Off unless a compiled preset sets gather.gem_orbit.enabled true; geometry
    defaults to the measured module constants, each overridable per-account."""
    name = CONFIG.get("preset") or ""
    o = (gather or {}).get("gem_orbit") or {}
    if gather is None and name.startswith("bp_"):
        o = ((CONFIG["presets"].get(name) or {}).get("gather") or {}).get("gem_orbit") or {}
    kw = {"enabled": bool(o.get("enabled", False))}
    if "center" in o:
        kw["center"] = tuple(o["center"])
    for src, dst in (("radius", "radius"), ("angle_deg", "angle"),
                     ("interval_sec", "interval"), ("taps", "taps"),
                     ("jitter_frac", "jitter"), ("spread_deg", "spread"),
                     ("retry_interval_sec", "retry_interval_sec"),
                     ("attempt_timeout_sec", "attempt_timeout_sec"),
                     ("retry_backoff_sec", "retry_backoff_sec")):
        if src in o:
            kw[dst] = o[src]
    return kw


def configured_loadout():
    """Use the selected blueprint's equipment for setup AND restoration."""
    preset = CONFIG.get("preset", "")
    if preset.startswith("bp_"):
        name = (CONFIG.get("presets", {}).get(preset) or {}).get("loadout")
        if not name:
            raise Abort("Shard blueprint has no equipment loadout")
        return name
    return "shard_farm"


def setup(tier: int | None = None):
    """Everything that happens once, before the first battle.

    The equipment goes through loadout.apply - ONE code path for the
    standalone launch and combo's handoff alike. The old per-key helpers
    read `spec("shard_farm")["modules"]` directly and crashed with a
    KeyError when the v29 body replaced that list with `module_preset:`
    (this silently killed the whole 78-run block on 2026-08-27; found via
    the runner_crashed event the next morning)."""
    equipment = configured_loadout()
    loadout.spec(equipment)  # Validate before navigating or changing tier.
    tier = tier or TIER
    ensure_home()
    set_tier(tier)
    loadout.apply(equipment)
    frame = capture.grab()
    if not on_home(frame):
        ensure_home()
    start_battle()
    logger.event("shard_setup", stage="done", tier=tier)


def start_dissonant(tab: str = "utility", settle: float = 15.0):
    """Enter a DISSONANT run (event mode, user-taught 2026-08-31): home ->
    'Dissonant Run' button -> verify the wanted upgrade tab carries the
    red X (= disabled; the selection PERSISTS between opens, so the
    common path verifies and taps nothing) -> the dialog's own BATTLE.
    Only 'utility' has a harvested tile template today - any other tab
    fails closed via TemplateMissing, never a blind tap."""
    from vision import detect
    frame, pt = require("home/dissonant_run.png", "dissonant run button")
    tap_at(pt, "open dissonant run dialog")
    frame, hit = tourney.wait_for("dialogs/dissonant_header.png", 6.0)
    if not hit:
        raise Abort("dissonant run dialog did not open")
    tile = f"dialogs/dissonant_tile_{tab}.png"
    okx, _, locx = detect._match(frame, "dialogs/dissonant_x.png", 0.85)
    oku, _, locu = detect._match(frame, tile, 0.85)
    if not oku:
        raise Abort(f"dissonant tile {tab!r} not found in the dialog")
    on_tile = (okx and abs(locx[0] - locu[0]) < 120
               and abs(locx[1] - locu[1]) < 220)
    if not on_tile:
        # X sits elsewhere (or nowhere): select the wanted tile - its
        # center is just above the label the template matched
        tap_at((locu[0] + 60, locu[1] - 60), f"dissonant: disable {tab}")
        time.sleep(0.8)
        frame = capture.grab()
        okx, _, locx = detect._match(frame, "dialogs/dissonant_x.png", 0.85)
        oku, _, locu = detect._match(frame, tile, 0.85)
        if not (okx and oku and abs(locx[0] - locu[0]) < 120
                and abs(locx[1] - locu[1]) < 220):
            logger.shot(frame, "dissonant_tab_stuck")
            raise Abort(f"could not disable {tab!r} in the dissonant dialog")
    logger.event("dissonant_entry", tab=tab)
    frame, pt = require("dialogs/dissonant_battle.png", "dissonant BATTLE")
    tap_at(pt, "dissonant run: BATTLE")
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        frame = capture.grab()
        if _in_run(frame):
            logger.event("shard_battle", started=True, attempt=1,
                         dissonant=tab)
            return frame
        time.sleep(0.4)
    logger.shot(capture.grab(), "dissonant_start_stuck")
    raise Abort("dissonant run did not start")


def enter_run(body: dict):
    """The ONE way a coin-kind run is entered from Home, for every entry
    point - a person's Start (orchestrator._prepare_coin_start), the death
    restart (orchestrator.restart_home) and the day plan's handoff
    (combo._block_handoff). Until 2026-09-14 only the restart knew about
    `dissonant_tab`; the other two tapped BATTLE and silently farmed a NORMAL
    run under a dissonance blueprint.

    Order: perk bans first (the Perks dialog is only reachable from Home and
    `perks.ensure_bans` degrades on failure), then the Dissonant Run dialog
    when the blueprint names a disabled workshop tab, else BATTLE."""
    from interactions import perks
    perks.ensure_bans(body.get("perk_bans"))
    tab = body.get("dissonant_tab")
    if tab:
        return start_dissonant(tab)
    return start_battle()


def start_battle(tries: int = 2, settle: float = 15.0, *, guard=None):
    """Tap BATTLE and WAIT for the run to actually exist.

    Returning the moment the tap lands is what killed the first forever-loop:
    the battle needs a second or two to load, and the loop's very next frame
    showed neither a wave counter nor a battle screen, so it aborted with
    'left the battle screen' before the run had even started. The tap is also
    retried once - a BATTLE tap that arrives while the nav is still settling
    is swallowed, and a swallowed tap leaves us on Home forever.
    """
    for i in range(tries):
        if guard is not None:
            frame = guard(capture.grab(), "start_home")
            ev = act.tap(*HOME_BATTLE, reason="start battle")
            guard(frame, "start_dispatched", ev)
        else:
            tap_at(HOME_BATTLE, "start battle")
        deadline = time.monotonic() + settle
        while time.monotonic() < deadline:
            frame = capture.grab()
            if guard is not None:
                frame = guard(frame, "start_wait")
            if _in_run(frame):
                logger.event("shard_battle", started=True, attempt=i + 1)
                return frame
            time.sleep(0.4)
        logger.event("shard_battle", started=False, attempt=i + 1)
    frame = capture.grab()
    logger.shot(frame, "shard_battle_never_started")
    raise Abort("BATTLE tapped but no run started")


# ----------------------------------------------------------------- the loop

def wait_for_wave(target: int, timeout: float = WAVE_TIMEOUT,
                  gems: "GemWatch | None" = None, *, guard=None,
                  recover_home: bool = True):
    """Block until the wave counter reaches `target`. Returns the frame.

    Waves SKIP during the Intro Sprint (observed 30 -> 40 -> 50), so this
    tests >= rather than ==; landing exactly on 100 is not guaranteed.
    """
    deadline = time.monotonic() + timeout
    off = 0
    while time.monotonic() < deadline:
        frame = capture.grab()
        if guard is not None:
            frame = guard(frame, "wave")
        w = wave_reader.read_wave(frame)
        if guard is not None and screen.identify(frame).name != "battle":
            w = None
        if w is not None and w >= target:
            return frame, w
        if w is None and not _in_run(frame):
            # Do NOT abort on the first odd frame. Every loop boundary crosses
            # a gap where the old run is gone and the new one has not drawn
            # yet - RETRY, the death dialog fading, the battle loading - and
            # the old code raced that gap and lost on loop 10 of 100.
            off += 1
            if off >= OFF_BATTLE_FRAMES:
                if recover_home and on_home(frame):
                    # RETRY did not take and we are parked on Home. This is a
                    # screen the routine navigated to itself, so starting the
                    # battle again is recovery, not a stray tap.
                    logger.event("shard_recover", where="home")
                    start_battle()
                    off = 0
                    continue
                logger.shot(frame, "shard_left_run")
                raise Abort("left the battle screen while waiting for a wave")
        else:
            off = 0
        if gems and w is not None:      # in-run only, never on a menu screen
            if gems.poll(frame) is not frame:
                continue                # collection observed a newer screen
        time.sleep(0.3)
    raise Abort(f"wave {target} not reached within {timeout:.0f}s")


def _in_run(frame) -> bool:
    return (wave_reader.read_wave(frame) is not None
            or screen.identify(frame).name == "battle")


def cancel_sprint(*, guard=None):
    """Tap the Intro Sprint indicator and confirm. Verified at every step."""
    frame = capture.grab()
    if guard is not None:
        frame = guard(frame, "battle")
    sc = screen.identify(frame)
    if sc.name != "intro_sprint_end":
        pt = detect.find_intro_sprint(frame)
        if pt is None:
            logger.event("shard_sprint", result="indicator not found")
            return False
        if guard is not None:
            frame = guard(frame, "sprint_begin")
        if guard is None:
            tap_at(pt, "intro sprint indicator")
        else:
            ev = act.tap(*pt, reason="intro sprint indicator")
            guard(frame, "sprint_dispatched", ev)
        frame = capture.grab()
        if guard is not None:
            frame = guard(frame, "sprint_open_wait")
        sc = screen.identify(frame)
    if sc.name != "intro_sprint_end":
        if guard is not None:
            guard(frame, "battle")
        logger.event("shard_sprint", result="no confirm dialog", screen=sc.name,
                     shot=logger.shot(frame, "shard_sprint_nodialog"))
        return False
    # The structural setup capture includes the entire button (173px high
    # on MuMu); the old 140px band could never contain that template.
    score, yes = screen._match(frame, "home/intro_sprint_yes.png",
                               ((1320, 1580), (540, 940)))
    if score < 0.90:
        if guard is not None:
            guard(frame, "sprint_confirm")
        logger.event("shard_sprint", result="Yes not found", score=round(score, 3))
        return False
    if guard is not None:
        guard(frame, "sprint_confirm")
    if guard is None:
        tap_at(yes, "intro sprint: yes")
    else:
        ev = act.tap(*yes, reason="intro sprint: yes")
        guard(frame, "sprint_confirm_dispatched", ev)
    after = capture.grab()
    if guard is not None:
        after = guard(after, "sprint_after")
    confirmed = (screen.identify(after).name != "intro_sprint_end"
                 and detect.find_intro_sprint(after) is None
                 and _in_run(after))
    logger.event("shard_sprint", result="ended" if confirmed else "unconfirmed",
                 shot=logger.shot(after, "shard_sprint_after"))
    return confirmed


def wait_for_nuke_point(timeout: float = 120.0,
                        gems: "GemWatch | None" = None):
    """Wait for wave 101 and the configured 10% progress threshold.

    Read the wave/progress bar rather than estimating elapsed time.
    """
    deadline = time.monotonic() + timeout
    best = 0.0
    while time.monotonic() < deadline:
        frame = capture.grab()
        w = wave_reader.read_wave(frame)
        p = detect.bar_fill(frame, "wave_progress")
        if w is not None and w > NUKE_WAVE:
            logger.event("shard_nuke_point", missed=True, wave=w)
            return frame            # overshot - fire immediately
        if gems and w is not None:
            if gems.poll(frame) is not frame:
                continue                # never fire Nuke from a pre-collection frame
        if w == NUKE_WAVE:
            best = max(best, p)
            if p >= NUKE_AT_PROGRESS:
                logger.event("shard_nuke_point", wave=w, progress=round(p, 3))
                return frame
        time.sleep(0.25)
    raise Abort(f"wave {NUKE_WAVE} end not detected (best progress {best:.2f})")


def fire_nuke(frame) -> bool:
    """Tap Nuke. require_ready is NOT consulted - see orchestrator.fire_button: the
    readiness test reads the battlefield behind the button, and a tap on a
    cooling ability is a harmless no-op while a refused tap loses the loop."""
    st = detect.button_state(frame, "nuke")
    if not (st.present and st.center) or st.score < 0.85:
        logger.event("shard_nuke", fired=False, score=round(st.score, 3),
                     shot=logger.shot(frame, "shard_no_nuke_btn"))
        return False
    before = detect.button_border_val(frame, "nuke")
    tap_at(st.center, "NUKE")
    after_frame = capture.grab()
    after = detect.button_border_val(after_frame, "nuke")
    fired = after is None or (before is not None and after < before * 0.75)
    logger.event("shard_nuke", fired=fired,
                 wave=wave_reader.read_wave(frame),
                 before_shot=logger.shot(frame, "shard_nuke_before"),
                 after_shot=logger.shot(after_frame, "shard_nuke_after"),
                 border_before=round(before or 0, 1),
                 border_after=(round(after, 1) if after is not None else None))
    return fired


def _confirm_exit(frame=None, *, guard=None) -> bool:
    """Tap whichever exit confirm dialog is up, if any.

    The exit button's LABEL does not predict the DIALOG: tapping END ROUND on
    a plain Tier 18 run opened the Surrender/Go Home dialog (observed live,
    2026-08-14) even though tourney runs pair END ROUND with No/Yes. So the
    answer is chosen by what is on screen, never by what was tapped.
    Surrender over Go Home always: Go Home only hides a run that is still
    going, and the next RETRY would have nothing to retry.
    """
    frame = frame if frame is not None else capture.grab()
    for rel, reason in (("home/surrender.png", "surrender"),
                        ("home/end_round_yes.png", "end round yes")):
        hit = find(frame, rel, 0.90)
        if hit:
            if guard is not None:
                guard(frame, "exit_confirm")
            # NOT instant, and SETTLE afterwards (acct2, 2026-08-19, proven
            # by an instrumented repro): at poll speed the dialog is re-
            # matched mid-fade right after being answered and the re-tap
            # lands on the TRANSITION - on this account that wedged the
            # emerging stats dialog and "GAME STATS / RETRY never appeared".
            # At a 1s cadence the same flow succeeds every time. So: full-
            # duration press, then give the transition its second before any
            # caller can re-poll.
            ev = act.tap(*hit[0], reason=reason)
            if guard is not None:
                guard(frame, "confirm_dispatched", ev)
            time.sleep(1.2)
            return True
    return False


def abandon_run(to_home: bool = False, before_restart=None, *, guard=None):
    """Side menu -> EXIT BATTLE / END ROUND -> confirm -> RETRY, at speed.

    to_home=True taps HOME instead of RETRY at the stats dialog - the
    stats-free exit for quest cycles (no runlog.collect on either path).

    Modelled on tourney.end_round, replacing a broken version whose autopsy
    is worth keeping:
      * the toggle TOGGLES. A failed exit leaves the menu open for the next
        attempt, and blind-tapping the toggle then closes it - so the menu
        state is read first and the toggle only tapped when closed.
      * the menu SLIDES IN. A frame grabbed straight after the toggle tap
        caught the animation, missed the button template, and the fixed-point
        fallback fired at a button that was not active yet - the 09:11 abort
        shot shows the settled menu with the button untouched. So the button
        is POLLED for, and there is no blind fallback.
      * the slot carries TWO labels (END ROUND / EXIT BATTLE, both observed
        on Tier 18) and the label does not predict the confirm dialog -
        see _confirm_exit.
    """
    frame = capture.grab()
    if guard is not None:
        frame = guard(frame, "exit_begin")
    # ABSOLUTE RULE (user, 2026-08-15): "you NEVER cancel a tournament run
    # EVER". tourney.end_round has carried this guard at its chokepoint ever
    # since a freshly bought 10-gem entry was surrendered at wave 1; THIS is
    # the other chokepoint. Every surrender in shard/quest land funnels
    # through here. Quest callers additionally carry process-local cycle
    # authority through every observation and exit/retry input below.
    if screen.in_tournament(frame):
        logger.shot(frame, "shard_abandon_refused")
        raise Abort("a TOURNAMENT run is on screen - automation never "
                    "cancels a tournament run")
    if not _confirm_exit(frame, guard=guard):  # leftover dialog from a failed exit
        if not detect.side_menu_open(frame):
            ev = act.tap(*CONFIG["side_menu"]["toggle"], reason="open side menu",
                         instant=True)
            if guard is not None:
                guard(frame, "menu_dispatched", ev)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            frame = capture.grab()
            if guard is not None:
                frame = guard(frame, "exit_menu")
            hit = (find(frame, "buttons/end_round.png", 0.85)
                   or find(frame, "buttons/exit_battle.png", 0.85))
            if hit:
                if guard is not None:
                    guard(frame, "exit_button")
                ev = act.tap(*hit[0], reason="exit battle", instant=True)
                if guard is not None:
                    guard(frame, "button_dispatched", ev)
                break
            time.sleep(EXIT_POLL)
        else:
            logger.shot(capture.grab(), "shard_no_exit_button")
            raise Abort("neither END ROUND nor EXIT BATTLE appeared "
                        "on the side menu")
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            frame = capture.grab()
            if guard is not None:
                frame = guard(frame, "exit_poll")
            if _confirm_exit(frame, guard=guard):
                break
            time.sleep(EXIT_POLL)
        else:
            logger.shot(capture.grab(), "shard_no_exit_confirm")
            raise Abort("no exit confirm dialog after tapping the exit button")

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        frame = capture.grab()
        if guard is not None:
            frame = guard(frame, "exit_poll")
        # A confirm tap can land during the dialog's fade-in and never
        # register (observed live on acct2, 2026-08-19: end_round_yes
        # matched at 0.99 on the timeout shot - found, tapped, swallowed).
        # The dialog still being up is proof the tap did not take; re-answer
        # it. _confirm_exit is template-gated, so this can never blind-tap.
        if _confirm_exit(frame, guard=guard):
            time.sleep(EXIT_POLL)
            continue
        dead, retry = detect.death_screen(frame)
        if dead and before_restart is not None:
            if before_restart() is False:
                return True
            before_restart = None
        if dead and to_home:
            logger.event("shard_result", shot=logger.shot(frame, "shard_result"))
            # Quest cycles (user, 2026-08-16): "just restart, don't store
            # the statistics" - so the home exit here deliberately skips
            # the runlog.collect that tourney.end_round would do.
            hit = find(frame, "home/game_stats_home.png")
            if hit:
                if guard is not None:
                    guard(frame, "exit_home")
                ev = act.tap(*hit[0], reason="game stats: HOME", instant=True)
                if guard is not None:
                    guard(frame, "home_dispatched", ev)
                # HOME fades in after the tap. Returning immediately let
                # cards_restore tap the navigation row during that fade.
                home_deadline = time.monotonic() + 8.0
                while time.monotonic() < home_deadline:
                    time.sleep(EXIT_POLL)
                    home_frame = capture.grab()
                    if guard is not None:
                        home_frame = guard(home_frame, "home_wait")
                    if (screen.identify(home_frame).name == "home" if guard is not None
                            else on_home(home_frame)):
                        time.sleep(0.5)
                        logger.event("shard_exit_home", ok=True)
                        return True
                raise Abort("HOME was tapped but the Home screen did not appear")
        elif dead and retry:
            logger.event("shard_result", shot=logger.shot(frame, "shard_result"))
            if guard is not None:
                guard(frame, "exit_retry")
            ev = act.tap(*retry, reason="RETRY", instant=True)
            if guard is not None:
                guard(frame, "retry_dispatched", ev)
            logger.event("shard_retry", ok=True)
            return True
        time.sleep(EXIT_POLL)
    logger.shot(capture.grab(), "shard_no_retry")
    raise Abort("GAME STATS / RETRY never appeared after surrendering")


def _speed_maxed(frame) -> bool:
    """Is the game-speed label reading x5.0?

    The label sits directly on the ANIMATED battlefield, so raw template
    matching is unusable - the background differs every frame. Both sides are
    binarized to the white glyphs instead (min channel > 190, which also
    drops the yellow/pink effects), and the stored template is the same mask.
    Measured: x5.0 scores 1.0, x1.0 scores 0.664 - threshold 0.85.
    """
    import cv2
    off = capture.layout_offset
    crop = frame[SPEED_BAND[0] + off:SPEED_BAND[1] + off,
                 SPEED_X[0]:SPEED_X[1]]
    if crop.size == 0:
        return False
    live = (crop.min(axis=2) > 190).astype("uint8") * 255
    from settings import ROOT
    tpl = cv2.imread(str(settings.template_path("home/speed_x5_mask.png")),
                     cv2.IMREAD_GRAYSCALE)
    if tpl is None or live.shape[0] < tpl.shape[0] or live.shape[1] < tpl.shape[1]:
        return False
    return float(cv2.matchTemplate(live, tpl, cv2.TM_CCORR_NORMED).max()) > 0.85


def ensure_max_speed(*, guard=None):
    """Press the speed '+' until the label reads x5.0.

    Added 2026-08-14: a run came back from RETRY at x1.0 - no tap of ours
    anywhere near the widget, cause unknown - and the Intro Sprint at
    one-fifth speed blew the 240s wave-100 timeout. One press when already
    maxed is a no-op for the game, but the normal case exits without
    tapping at all.
    """
    for i in range(SPEED_TAPS + 1):
        frame = capture.grab()
        if guard is not None:
            frame = guard(frame, "battle")
        if _speed_maxed(frame):
            if i:
                logger.event("shard_speed", presses=i, fixed=True)
            return True
        if i == SPEED_TAPS:
            break
        try:
            act.tap(SPEED_PLUS[0], SPEED_PLUS[1] + capture.layout_offset,
                    reason="speed_plus", instant=True)
        except act.TapRefused as e:
            logger.event("tap_refused", button="speed_plus", error=str(e))
            return False
        time.sleep(0.4)
    logger.event("shard_speed", presses=SPEED_TAPS, fixed=False,
                 shot=logger.shot(capture.grab(), "shard_speed_stuck"))
    return False


def one_loop(n: int, gems: "GemWatch | None" = None, last: bool = False):
    logger.event("shard_loop", n=n, stage="begin")
    # any readable wave = the battle HUD is up AND layout_offset is current -
    # both are what ensure_max_speed needs before it looks at the widget
    wait_for_wave(1, gems=gems)
    ensure_max_speed()
    frame, w = wait_for_wave(SPRINT_WAVE, gems=gems)
    logger.event("shard_loop", n=n, stage="wave", wave=w,
                 shot=logger.shot(frame, f"shard_loop_{n}_sprint"))
    if not cancel_sprint():
        raise Abort("Intro Sprint cancellation was not confirmed; leaving the battle untouched")
    frame = wait_for_nuke_point(gems=gems)
    if not fire_nuke(frame):
        raise Abort("Nuke firing was not confirmed; refusing to surrender or retry")
    time.sleep(0.4)                 # let the kill and the shard drop resolve
    # The LAST loop exits to HOME, not RETRY (2026-08-29): the final RETRY
    # used to chain a 101st run that the next handoff walked over - since
    # the orphan-adoption fix that leftover gets ADOPTED as a coin run
    # (the shard tier and deck farming as coin until it dies) or HELD ON by a
    # tournament block, and it also parked cards_restore off the nav row.
    if last:
        abandon_run(to_home=True)
    else:
        abandon_run()
    logger.event("shard_loop", n=n, stage="done")


def run(loops: int | None = None, do_setup: bool = True,
        tier: int | None = None):
    """`loops=None` runs until stopped.

    An Abort still ends the whole thing rather than being retried. That is
    deliberate and matches the rest of the autopilot: every Abort means the
    screen was not what the routine expected, and the one thing that must
    never happen is blind tapping into an unknown screen. Stopping leaves a
    logged reason and a screenshot; guessing does not.
    """
    global SPRINT_WAVE, NUKE_WAVE
    if tier:
        SPRINT_WAVE, NUKE_WAVE = fleet_waves(tier)
        logger.event("shard_fleet_waves", tier=int(tier), sprint_wave=SPRINT_WAVE,
                     nuke_wave=NUKE_WAVE)
    if do_setup:
        setup(tier)
    adb_fails = 0
    from runtime.action_session import ActionSession
    gems = GemWatch(session=ActionSession.for_operation('shard'))   # preserve collection cadence across the batch
    n = 0
    while loops is None or n < loops:
        # Honour the scheduler's stop flag at the loop boundary (2026-08-17).
        # Before this, combo's _stop_and_wait sat out its full 8h timeout
        # against a shard block - flows/shard.py never looked at the flag. Same
        # contract as orchestrator.py: leave only where stopping is free. A run is
        # already live here (chained by the previous RETRY) - close it to
        # HOME before leaving (2026-08-29): the next handoff no longer ends
        # a leftover battle, it ADOPTS it (coin) or HOLDS on it
        # (tournament - which would stall the block for hours), and the
        # cards restore below needs the nav row. Degrades loudly: a failed
        # close must not lose the stop itself - it is logged and the run
        # returns WITHOUT the cards restore, whose first act is a tap on the
        # nav row of a screen the failed close just proved unknown (rule 7).
        why = runflag.requested()
        if why is not None:
            logger.event("shard_stop_flag", after_loop=n, reason=why)
            try:
                abandon_run(to_home=True)
            except (Abort, act.TapRefused) as e:
                logger.event("shard_stop_close_fail", error=str(e))
                return n
            break
        n += 1
        try:
            one_loop(n, gems=gems,
                     last=(loops is not None and n == loops))
        except capture.CaptureError as e:
            # Treated as an adb hiccup, not a logic failure: the device or the
            # daemon dropped (the first infinite run, when every screencap
            # still spawned adb.exe, lost the daemon at loop 12). Recover the
            # device (_kick_adb) and retry the same loop rather than ending a
            # farm session over an infrastructure hiccup.
            n -= 1
            adb_fails += 1
            logger.event("shard_adb", fails=adb_fails, error=str(e)[:120])
            if adb_fails > ADB_MAX_FAILS:
                raise
            _kick_adb()
            time.sleep(5.0)
            continue
        except (Abort, act.TapRefused) as e:
            logger.event("shard_loop", n=n, stage="abort", error=str(e))
            raise
        adb_fails = 0
        # Persist today's completed-run total after EVERY loop, not at exit:
        # an abort (a human taking the screen counts) must not lose the
        # count - combo resumes the remainder from this number.
        daystate.increment_today("shard_runs")      # one locked step: a get followed by a set would lose a run another process counted in between
    # v29 (user, 2026-08-28): put the farming deck back on the cards screen
    # before leaving. The coin block's global preset only SELECTS presets at
    # battle entry - nothing else moves the cards screen off this block's
    # deck, and whichever preset stays selected is where later card
    # mutations land. Degrades loudly: a failed restore must not turn a
    # completed block into a crash.
    restore = loadout.spec(configured_loadout()).get("cards_restore")
    if restore:
        try:
            logger.event("shard_cards_restore", preset=restore,
                         result=loadout.apply_cards(restore))
        except (Abort, act.TapRefused) as e:
            logger.event("shard_cards_restore", preset=restore,
                         error=str(e))
    return n


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None,
                    help="accepted so the tray can launch this the same way "
                         "it launches orchestrator.py; the shard loop has no preset "
                         "settings of its own, the plan is in this file")
    ap.add_argument("--loops", type=int, default=1,
                    help="number of shard runs (default 1; 0 = forever)")
    ap.add_argument("--tier", type=int, default=None,
                    help=f"tier to farm on (default {TIER}); a profile block "
                         f"passes its blueprint's tier here")
    ap.add_argument("--no-setup", action="store_true",
                    help="skip tier/cards/modules - a run is already going")
    return ap.parse_args()


if __name__ == "__main__":
    import settings
    _a = _cli()
    from runtime.device_lease import prepare_instance
    prepare_instance(_a.instance)
    # ONLY a compiled blueprint is bound as the process preset. The tray
    # still passes --preset shard_farm (a runner-only entry with no settings);
    # binding that would change nothing here and could raise on a placeholder,
    # so the legacy path keeps ignoring it exactly as it always has.
    if (_a.preset or "").startswith("bp_"):
        settings.select_instance(_a.instance, _a.preset)
        # KIND BEFORE ANY CAPTURE OR TAP. This loop surrenders runs; pointed
        # at a tournament blueprint it would surrender a tournament.
        _kind = (CONFIG["presets"].get(_a.preset) or {}).get("kind")
        if _kind != "shard":
            raise SystemExit(f"REFUSED: {_a.preset} is a {_kind!r} blueprint "
                             f"- flows/shard.py runs 'shard' blueprints only "
                             f"(nothing was captured or tapped)")
    else:
        settings.select_instance(_a.instance)
    from player import readiness
    readiness.require(settings.ROOT, CONFIG, dict(CONFIG["presets"].get(CONFIG["preset"]) or {}, kind="shard"))
    print(f"shard farming on {CONFIG['active_instance']} "
          f"(loops={_a.loops or 'forever'}, dry_run={CONFIG['loop']['dry_run']})")
    try:
        done = run(loops=(None if _a.loops == 0 else _a.loops),
                   do_setup=not _a.no_setup, tier=_a.tier)
        print(f"completed {done} shard loop(s)")
    except (Abort, act.TapRefused) as e:
        # The print is invisible under pythonw - the event log IS the
        # console. A silent exit 1 closed the whole 78-run shard block on
        # 2026-08-27 with no recorded reason.
        logger.event("runner_crashed", flow="shard",
                     error=f"{type(e).__name__}: {e}")
        print(f"ABORTED: {e}")
        raise SystemExit(1)
    except SystemExit:
        raise
    except BaseException as e:      # noqa: BLE001 - logged, then re-raised
        try:
            logger.event("runner_crashed", flow="shard",
                         error=f"{type(e).__name__}: {e}")
        except Exception:           # noqa: BLE001 - never mask the original
            pass
        raise
