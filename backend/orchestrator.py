"""Main loop implementing the active preset (default: Normal Run).

Normal Run policy:
  - Death dialog -> screenshot (science log) -> tap RETRY -> reset run state.
  - Shopping priorities (see config presets.normal_run.shopping):
      utility Enemy Level Skips first, then defense Death Defy / Land Mine
      Radius once + Health/Health Regen best-cost, then the attack sequence.
      Sweeps run aggressively early in a run, then on shop_interval_sec.
  - Fleet marks (2495 + 1000k): toggle Chain Lightning ON pre_mark_waves
    before the mark, OFF off_after_waves after it.
  - Gem CLAIM: tap 3-10 s (randomized) after detection.
  - Nuke / Demon Mode: NEVER before a Second Wind proc. After a SW proc,
    watch the wall overheal; if it declines despite x10 regen -> Nuke;
    still declining dm_confirm_sec later -> Demon Mode.
"""
import datetime
from scheduling import daystate
import json
import math
import random
import time
import traceback

import psutil

import settings
from settings import CONFIG
from device import capture
from vision import wave_reader
from vision import detect
from runtime import logger
from device import act
from interactions import shopper
from interactions import missions
from vision import screen
from interactions import store
from scheduling import runflag
from runtime import runlog


def preset() -> dict:
    """Active preset; supports single-level inheritance via 'base'."""
    p = CONFIG["presets"][CONFIG["preset"]]
    if "base" not in p:
        return p
    base = CONFIG["presets"][p["base"]]
    merged = {}
    for k, v in base.items():
        if isinstance(v, dict):
            merged[k] = {**v, **p.get(k, {})}
        else:
            merged[k] = p.get(k, v)
    for k, v in p.items():
        if k not in merged and k != "base":
            merged[k] = v
    return merged


# P0 (2026-08-18): daily_state IO delegates to daystate.py (single writer,
# absolute path, atomic saves).
#
# v29 (2026-08-27): the store's Ad Gem is no longer a once-daily claim - it
# can be collected up to 60 times per day (6 gems each, 360/day), and the
# game resets that cap at 00:00 UTC. So the once-a-day 4-5 AM scheme became
# a UTC-keyed counter: the collection visits keep going all day, paced by
# the caller's jittered retry clock, and stop dead at the cap.
AD_GEMS_DAILY_CAP = 60          # game constant, not a preference


def free_gems_due() -> bool:
    """May another Ad-Gem claim be attempted right now? (v29: 60/UTC-day.)"""
    count = daystate.get_utc_today("ad_gems_claimed_utc")
    if count >= AD_GEMS_DAILY_CAP:
        if not daystate.flag_today("ad_gems_cap_logged"):
            daystate.mark_today("ad_gems_cap_logged")
            logger.event("ad_gems_cap_reached", count=count,
                         cap=AD_GEMS_DAILY_CAP)
        return False
    return True


# quest_scan_due()/quest_scan_mark_done() removed: superseded by chores.py,
# which owns every between-run chore and one daily-state scheme for all of them.


def free_gems_mark_claimed():
    count = daystate.bump_utc_today("ad_gems_claimed_utc")
    logger.event("ad_gems_claimed", count=count, cap=AD_GEMS_DAILY_CAP)
    # legacy daily flag kept for the dashboard's daily-state display
    daystate.set_raw("free_gems_claimed", datetime.date.today().isoformat())


def marks():
    f = CONFIG["fleet"]
    return [f["first_wave"] + i * f["interval"] for i in range(20)]


# consecutive badge-free frames before the Second Wind window counts as closed.
# The loop runs at ~1.4 fps under load, so 3 frames is ~2s - long enough to
# ride out a glyph obscured by combat, short enough that the wall watch opens
# well inside the window that follows.
SW_CLOSE_FRAMES = 3


class RunState:
    def __init__(self):
        self.tracker = wave_reader.WaveTracker()
        self.sw_proc_count = 0
        self.sw_floater_seen = False
        self.sw_miss = 0              # consecutive frames without the badge
                                      # (debounces the window CLOSE)
        self.post_sw_until = 0.0      # monotonic deadline for wall watching
        self.sw_immune_until = 0.0    # abilities are HELD until this:
                                      # the Second Wind immunity window
        self.nuke_fired_at = 0.0
        self.dm_fired = False
        self.dm_seen_at = None        # last MATCHED DM center THIS run: the
                                      # rescue burst's only fallback point
                                      # (row order is per-run; a stale or
                                      # fixed point taps the wrong ability)
        self.nuked_marks: set[int] = set()   # fleet marks already nuked/passed
        self.fleet_try_at = 0.0              # throttle for fleet-nuke retries
        self.wave_hwm: int | None = None     # highest confirmed wave THIS run
                                             # (the unseen-boundary detector)
        self.last_fire = {"nuke": 0.0, "demon_mode": 0.0}
        self.wall_prev: tuple[float, float] | None = None   # (t, fill)
        self.wall_last: tuple[float, float] | None = None   # rolling PRE-PROC
                                      # wall reading: during a Second Wind the
                                      # bar shows the pink immunity countdown,
                                      # so proc-time judgements must use the
                                      # last value from BEFORE the badge
        self.cl_on = False
        self.cl_normalized = False    # force CL OFF at run start (may have died with it ON)
        self.uw_normalized = False    # preset uw_wanted enforced at run start
        self.uw_done: set[str] = set()   # weapons already verified this run
        self.uw_fails = 0             # consecutive uw_toggle failures
        self.uw_next_try = 0.0        # backoff deadline: NEVER hammer the UW
                                      # tab every frame (it drags the panel)
        self.cl_offsets: dict[int, tuple[int, int]] = {}  # mark -> (on_before, off_after)
        self.cl_always_above: int | None = None   # rolled per run from a range
        self.cl_blocked_logged = False   # edge-log for "toggle wanted but gated"
        self.gem_due: tuple[float, tuple[int, int]] | None = None
        self.last_shop = 0.0
        self.sprint_prev: bool | None = None
        self.no_wave = 0              # consecutive frames without a wave read
        self.dead_frames = 0          # debounce: consecutive death-screen frames
        self.wave_seen = None         # last tracked wave + when it changed:
        self.wave_seen_at = 0.0       # a frozen counter means every wave-driven
        self.wave_stall_logged = False  # rule has silently stopped firing
        self.menu_logged = False      # edge-log for leaving the battle screen
        self.off_battle_since = 0.0   # when we left the battle screen
        self.recover_try = 0.0        # throttle for stuck-popup recovery taps
        self.sprint_end_try = 0.0     # throttle for end-intro-sprint attempts
        self.sprint_ended = False     # the sprint has been ended this run
        self.bot_left_battle = False  # WE navigated off the battle screen
                                      # (a reward flow). False means the
                                      # human opened the menu: hands off.
        self.shop_done_logged = False  # edge-log for "everything maxed"
        self.mission = missions.Mission()
        self.quest_due: tuple[str, float] | None = None  # (badge, visit time)
        self.menu_open_try = 0.0      # cooldown for side-menu open taps
        self.menu_closed_frames = 0   # debounce: collapsed-toggle sightings
        self.menu_open_frames = 0     # debounce: stable-open before flows
        self.last_visit: dict[str, float] = {}   # badge -> last flow start
        self.await_guild_result = False  # a guild flow is running/finishing
        self.pending_store = False    # store check due (guild claim landed)
        self.free_gems_try_at = 0.0   # throttle for free-gems claim retries
        # ---- Tier B rule interpreter state (profiles; see eval_rules).
        # Lives on RunState because "at most once per RUN" is exactly what a
        # fresh RunState after every death already means.
        self.rules_fired: set[int] = set()      # rule index -> already fired
        self.rule_next: dict[int, float] = {}   # index -> refire floor
        # PER RULE INDEX, never shared: one bad switch_cards rule must not
        # disable an unrelated one that works.
        self.rule_cards_tries: dict[int, int] = {}   # index -> attempts
        self.rules_cards_off: set[int] = set()       # ...indices given up on
        # ---- P4 per-rule trigger memory. All PER RULE INDEX for the same
        # reason as the card-switch state above: two rules watching the same
        # bar have their own falling counters, and one must never consume the
        # other's. Reset with the RunState, so every run starts blind.
        self.rule_bar_prev: dict = {}         # key -> previous bar reading
        self.rule_bar_falling: dict[int, int] = {}   # index -> falling samples
        self.rule_marks: dict[int, set[int]] = {}    # index -> marks retired
        self.rule_log_at: dict = {}           # (index, why) -> last debug line
        # ---- P6 per-run state.
        self.max_wave_done = False            # the ONE max_wave attempt
        self.in_run_done: set = set()         # in_run_actions already applied
        self.in_run_off: set = set()          # ...and ones given up on
        self.in_run_tries: dict = {}          # id -> attempts (same ledger
                                              # shape as rule_cards_tries)
        self.shop = shopper.Shopper(preset())


FORCE_PRESENT = 0.85    # template score required to tap without a ready check

# Fixed points for the rescue burst, used when there is no time to match:
# Demon Mode's slot center (fires logged at x 81-89, y 1490-1497 across the
# 2026-08-15 benches) and the Yes button of the END INTRO SPRINT confirm
# dialog (center of the ROI its template is matched in). A blind tap that
# misses is caught by the post-burst verify loop, never left unhandled.
# (RESCUE_DM_PT removed 2026-08-29: the fixed burst fallback blind-tapped
# the Nuke - the burst now falls back to the run's last MATCHED DM center)
RESCUE_YES_PT = (730, 1450)


def fire_button(frame, name: str, reason: str,
                require_ready: bool = True) -> bool:
    """Tap an ability and CONFIRM it actually fired.

    Returns True only on a confirmed fire, because callers retire a fleet
    mark on success - an unconfirmed tap would burn the mark without ever
    nuking. Firing puts the button on cooldown, which dims its border, so a
    border that stays bright means the tap did nothing (already on cooldown,
    or the tap missed).

    require_ready=False is for the RESCUE path, and it exists because the
    ready test is not trustworthy. `ready` is mean brightness of a band around
    the button, and that band is mostly BATTLEFIELD: measured on live wave-1060
    frames, a dim button over bright red stripes read val=123 ("ready") while a
    vividly-lit button over a dark field read val=112 ("not ready"). A
    tournament field is dark, so the only rescue ability the preset has would
    have been refused a tap at the exact moment it was needed.

    Tapping anyway is safe and strictly better: the centre comes from a
    template match at >=0.85 (measured 0.94-0.96 live), so the tap lands on the
    button whatever its state, and a tap on a cooling-down ability is a no-op.
    Confirmation still runs - an unconfirmed rescue simply gets retried under
    the caller's refire floor instead of being silently skipped.
    """
    st = detect.button_state(frame, name)
    if not (st.present and st.center):
        return False
    if not st.ready:
        if require_ready or st.score < FORCE_PRESENT:
            return False
        logger.event("fire_forced", button=name, reason=reason,
                     score=round(st.score, 3),
                     border=detect.button_border_val(frame, name))
    before = detect.button_border_val(frame, name)
    try:
        ev = act.tap(*st.center, reason=reason)
    except act.TapRefused as e:
        logger.event("tap_refused", button=name, error=str(e))
        return False
    time.sleep(0.8)                      # let the cooldown state render
    after = detect.button_border_val(capture.grab(), name)
    fired = after is None or (before is not None and after < before * 0.75)
    logger.event("fire", button=name, confirmed=fired,
                 border_before=round(before or 0, 1),
                 border_after=(round(after, 1) if after is not None else None),
                 **ev)
    return fired


def end_intro_sprint(rs: "RunState", why: str, fast: bool = False) -> bool:
    """Tap the Intro Sprint indicator and confirm END INTRO SPRINT EARLY.

    Abilities cannot be used while the Intro Sprint is running. That is what
    killed the first tournament run: Demon Mode was never usable, the tower
    died at wave 1100, and nothing in the log said why.

    Every step is verified. The indicator is only tapped if it is FOUND, and
    Yes is only tapped if the confirm dialog actually came up - if the first
    tap landed on something else, this stops rather than pressing Yes into
    whatever happens to be on screen.
    """
    now = time.monotonic()
    if not fast and now - rs.sprint_end_try < 30:
        return False
    rs.sprint_end_try = now
    frame = capture.grab()
    pt = detect.find_intro_sprint(frame)
    if pt is None:
        logger.event("intro_sprint_end", why=why, result="indicator not found")
        return False
    try:
        act.tap(*pt, reason="end_intro_sprint", instant=fast)
    except act.TapRefused as e:
        logger.event("intro_sprint_end", why=why, error=str(e))
        return False
    # `fast` is the Second Wind emergency (user, 2026-08-15: "no waiting, no
    # jitter"): the rescue has to finish INSIDE the ~10s immunity window, so
    # the dialog is polled for instead of slept at, and every tap is instant.
    if fast:
        sc = None
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            frame = capture.grab()
            sc = screen.identify(frame)
            if sc.name == "intro_sprint_end":
                break
            time.sleep(0.1)
    else:
        time.sleep(1.0)
        frame = capture.grab()
        sc = screen.identify(frame)
    if sc is None or sc.name != "intro_sprint_end":
        logger.event("intro_sprint_end", why=why, result="no confirm dialog",
                     screen=(sc.name if sc else "none"),
                     shot=logger.shot(frame, "intro_sprint_nodialog"))
        return False
    score, pt_yes = screen._match(frame, "home/intro_sprint_yes.png",
                                  ((1380, 1520), (560, 900)))
    if score < 0.90:
        logger.event("intro_sprint_end", why=why, result="Yes button not found")
        return False
    try:
        act.tap(*pt_yes, reason="end_intro_sprint_yes", instant=fast)
    except act.TapRefused as e:
        logger.event("intro_sprint_end", why=why, error=str(e))
        return False
    time.sleep(0.4 if fast else 1.2)
    logger.event("intro_sprint_end", why=why, result="ended",
                 shot=logger.shot(capture.grab(), "intro_sprint_ended"))
    return True


def _in_ability_row(pt: tuple[int, int], margin: int = 25) -> bool:
    """Is this point on (or near) the Nuke / Demon Mode buttons? Nothing
    speculative may ever be tapped there."""
    x, y, w, h = CONFIG["rois"]["ability_row"]
    return (x - margin <= pt[0] <= x + w + margin
            and y - margin <= pt[1] <= y + h + margin)


def restart_from_home(frame, tier: int | None) -> bool:
    """Death -> HOME -> set the tier -> BATTLE, instead of tapping RETRY.

    RETRY re-enters on whatever tier the dead run was using, so it can never
    CHANGE tier; the user wants the farm loop pinned to a tier it sets itself,
    which means going out to Home first.

    This deliberately reverses a long-standing rule. The HOME button on the
    death dialog used to be fenced off from this handler entirely, because
    pressing it dropped out of the farm loop and left the bot idling on the
    home screen forever. That fence is only safe to remove because the tap is
    now half of a complete sequence that ends in a running battle - and if any
    step of it fails, this returns False and the caller falls back to RETRY
    rather than leaving the account parked on Home.

    Returns True only when a run is actually going again.
    """
    if not tier:
        logger.event("restart_home", ok=False, reason="no tier configured")
        return False
    from interactions import tourney
    from flows import shard
    hit = tourney.find(frame, "home/game_stats_home.png", 0.85)
    if not hit:
        logger.event("restart_home", ok=False, reason="HOME button not found")
        return False
    try:
        act.tap(*hit[0], reason="game stats: HOME")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if tourney.on_home(capture.grab()):
                break
            time.sleep(0.5)
        else:
            logger.event("restart_home", ok=False, reason="home never appeared")
            return False
        # We are on Home with no run going - the one moment in the whole day
        # when a look at the quest board costs nothing and risks nothing. Done
        # here rather than on a timer precisely so it can never interrupt a
        # live run. It returns 0 on any hitch instead of raising, so a bad
        # scan cannot stop the farm loop.
        # Between-run chores. One dispatcher, one daily-state scheme - this
        # used to call questscan directly and mark it under its own key, which
        # meant two "is it due" implementations disagreeing about the same
        # chore. chores.run_due() takes at most ONE per gap so a restart never
        # becomes a ten-minute menu expedition, and it never raises.
        from scheduling import chores
        if chores.run_due() and not tourney.on_home(capture.grab()):
            after = capture.grab()
            if tourney.live_run(after):
                # Someone started a run while the chores had the menus.
                # NEVER end it (user, 2026-09-05); report failure so the
                # caller holds and the observe loop adopts the run.
                logger.event("restart_home", ok=False,
                             reason="run in progress after chores",
                             wave=wave_reader.read_wave(after))
                return False
            tourney.ensure_home()
        shard.set_tier(tier)
        # Dissonance event (2026-08-31): a dissonant blueprint re-enters
        # through the event's own dialog - the plain BATTLE button would
        # start a NORMAL run and silently farm the wrong mode.
        d_tab = preset().get("dissonant_tab")
        if d_tab:
            shard.start_dissonant(d_tab)
        else:
            shard.start_battle()
    except (tourney.Abort, act.TapRefused) as e:
        logger.event("restart_home", ok=False, error=str(e))
        return False
    logger.event("restart_home", ok=True, tier=tier,
                 **({"dissonant": d_tab} if d_tab else {}))
    return True


def cl_window(rs: "RunState", wave: int) -> bool:
    cl = preset()["chain_lightning"]
    # A blueprint may compile Chain Lightning OUT entirely (`enabled: false`)
    # - the account may not even own it. No legacy preset has the key, so the
    # default leaves every existing run untouched.
    if cl.get("enabled", True) is False:
        return False
    above = cl.get("always_on_above")
    if above is not None:
        if rs.cl_always_above is None:
            # rolled once per run: a fixed switch-over wave every single run
            # is a bot tell, same reasoning as the per-mark CL offsets
            rs.cl_always_above = (random.randint(*above)
                                  if isinstance(above, (list, tuple))
                                  else int(above))
            logger.event("cl_always_above", wave=rs.cl_always_above)
        if wave >= rs.cl_always_above:
            return True               # late run: leave CL on permanently
    # NO MARK SCHEDULE -> NO MARKS LOOP. The loop below does
    # random.randint(*cl["pre_mark_waves"]) unconditionally, so a policy
    # compiled with mode always_on / off_until_wave / off (both offsets null,
    # or the keys simply absent) would raise on the first fleet mark of every
    # run. Absent and None are both "no schedule"; either offset missing is
    # enough to disqualify, since the loop needs both.
    if cl.get("pre_mark_waves") is None or cl.get("off_after_waves") is None:
        return False
    for m in marks():
        if m not in rs.cl_offsets:
            rs.cl_offsets[m] = (random.randint(*cl["pre_mark_waves"]),
                                random.randint(*cl["off_after_waves"]))
        on_before, off_after = rs.cl_offsets[m]
        if m - on_before <= wave <= m + off_after:
            return True
    return False


RULE_REFIRE_SEC = 5.0     # floor between two firings of the SAME rule
RULE_LOG_EVERY_SEC = 30.0  # per (rule, reason) rate limit for the debug lines

# Every spelling of "how long before this rule may fire again", in precedence
# order. `refire_sec` is the compiler's - it already ranked the three schema
# spellings once, so the runtime does not have to - and the rest are the raw
# shapes P3 read. ONE table, so the admission check and the reader can never
# disagree about which keys exist.
_COOLDOWN_KEYS = (("rule", "refire_sec"), ("rule", "cooldown_sec"),
                  ("rule", "throttle_sec"), ("rule", "refire_guard_sec"),
                  ("action", "throttle_sec"), ("action", "refire_guard_sec"))

# ---- TIER B VOCABULARY (P4). What the main-loop evaluator can EVALUATE and
# what it can EXECUTE, as two explicit tables. A name that is in the schema but
# in neither table is retired and logged, never silently skipped: the whole
# point of profiles is that a rule the player wrote either runs or says why not.
RULE_TRIGGERS = ("wave_at_least", "wave_between", "bar", "wall_collapse",
                 "fleet_mark", "second_wind")
RULE_ACTIONS = ("fire", "burst", "cancel_sprint", "stop_after_run",
                "switch_cards", "toggle_uw", "surrender_retry")
# THE DEATH SCREEN IS ITS OWN PHASE, not a trigger the observe loop can see:
# by the time the dialog exists the loop has already returned "dead" and handed
# over. A death rule is recognised by the compiler's own `latency` field and
# runs from run_death_rules() below.
RULE_DEATH_TRIGGERS = ("death_screen",)
# ...and ONE action may fire there: the one that touches no screen at all.
#
# (Codex P4 #3.) The stats dialog has no ability row, no sprint and no wall, so
# fire/burst/cancel_sprint were never candidates. `switch_cards` and
# `surrender_retry` looked like they were, and are not:
#   * loadout.apply_cards starts at tourney.open_nav, which navigates FROM THE
#     HOME SCREEN. Reaching Home from the stats dialog is restart_from_home's
#     verified HOME-button-then-poll sequence - and that function does not stop
#     at Home, it ends in a running battle, so a card swap cannot ride it and
#     there is no other primitive that gets back to the handler's restart path
#     afterwards. Building one means either a new blind tap sequence or
#     refactoring the death path, neither of which is a rule interpreter's job.
#   * shard.abandon_run surrenders a LIVE battle (side menu -> EXIT BATTLE).
#     On a stats dialog there is nothing to surrender and no such button; it
#     would hunt, fail and Abort.
# So both are refused with a message naming the reason. That is deliberately
# NARROWER than playerprofile.DEATH_SCREEN_ACTIONS: a compiled rule of that
# kind is retired LOUDLY here rather than tapping into a menu.
RULE_DEATH_ACTIONS = ("stop_after_run",)
# (There is deliberately no "acting actions" table any more. Whether an action
# touched the screen is REPORTED BY THE ACTION, from where it actually knows -
# see _rule_act's two return values - rather than predicted from its name.)
RULE_BUTTONS = ("nuke", "demon_mode")
RULE_BARS = ("hp", "wall")
RULE_SW_STATES = (None, True, "any", "open", "closed", "after_immunity")

_KNOWN_TRIGGERS = set(RULE_TRIGGERS) | set(RULE_DEATH_TRIGGERS)
_KNOWN_ACTIONS = set(RULE_ACTIONS)

# The profile layer's spawn-time capability re-check. Resolved BY NAME because
# it ships with the compiler half of P4; see _gate_preset.
RULE_GATE_HELPERS = ("check_capabilities", "gate_rules", "check_rule_gating")


def _rule_mem(rs: "RunState", name: str) -> dict:
    """Per-rule scratch dict on the RunState, created on first use.

    getattr/setattr rather than a bare attribute so the evaluator also works
    against a RunState stand-in that predates these fields - the memory is an
    optimisation of the interpreter, never part of the run's contract.
    """
    m = getattr(rs, name, None)
    if m is None:
        m = {}
        setattr(rs, name, m)
    return m


def _rule_retire(rs: "RunState", k) -> None:
    """RETIRE a rule for the rest of this process.

    Distinct from `rules_fired`, which `repeat: true` is allowed to override.
    Retirement is for rules that can NEVER run - an unreadable vocabulary, an
    action barred from this phase, a tournament surrender - and nothing
    overrides it. Kept as a dict key set (via _rule_mem) so a RunState that
    predates the field still works.
    """
    _rule_mem(rs, "rules_retired")[k] = True
    rs.rules_fired.add(k)


def _rule_id(rule: dict, i: int) -> str:
    """The rule's identity in logs and tap reasons.

    The compiler emits a stable `id` ("<policy>#<index in the policy>", so it
    does not move when an earlier rule is absorbed into Tier A). `rule<index>`
    is what P3 shipped, what the tests pin and what every `rule0` tap reason in
    the live logs says, so it stays the fallback rather than being replaced.
    """
    rid = rule.get("id")
    return rid if isinstance(rid, str) and rid else f"rule{i}"


def _rule_key(rule: dict, i: int):
    """The key this rule's per-run state hangs on.

    THE COMPILED `id` WHEN THERE IS ONE - it is stable across a recompile in a
    way the list index is not, and the compiler's contract says the interpreter
    keys on it. Falling back to the integer index keeps every P3-shaped rule
    (and every test that inspects `rs.rule_next[0]`) working unchanged.
    """
    rid = rule.get("id")
    return rid if isinstance(rid, str) and rid else i


def _rule_when(rule: dict) -> tuple[str | None, dict, bool]:
    """(trigger name, params, normalized?) from a rule's `when`.

    The compiler emits a NORMALIZED spec - {"kind": "bar", "bar": "hp",
    "below": 0.3, "falling_samples": 0, "deadband": 0.0} - TOTAL AND EXPLICIT,
    every numeric parameter present. P3 emitted the raw schema block
    ({"bar": "hp", "below": 0.3}); the dashboard preview and hand-written
    presets still produce it.

    THE THIRD RETURN VALUE IS NOT DECORATION. Which shape a rule is in decides
    whether a missing number is an error or a documented legacy behaviour:
    a normalized rule that omits one is a compiler bug and is refused (Codex
    P4 #4 - a compiler default of 1 against a runtime default of 0 meant a
    compiled hp rule that sat below its threshold for three passes never
    fired), while a raw rule that omits one gets exactly what the P3 evaluator
    did with it and nothing else.
    """
    when = rule.get("when")
    if not isinstance(when, dict):
        return None, {}, False
    name = when.get("kind") or when.get("trigger")
    if isinstance(name, str):
        p = when.get("params")
        # normalized form: params are FLAT siblings of `kind` unless the
        # compiler nests them
        return name, (dict(p) if isinstance(p, dict)
                      else {k: v for k, v in when.items()
                            if k not in ("kind", "trigger")}), True
    names = [k for k in when if k in _KNOWN_TRIGGERS]
    if not names:
        return None, {}, False
    name = names[0]
    v = when[name]
    if name == "bar":
        # `bar`'s params are SIBLINGS of the trigger key, not nested - the one
        # exception in the schema, and playerprofile._trigger normalizes it the
        # same way.
        p = {k: val for k, val in when.items() if k != "bar"}
        p["bar"] = v
    elif isinstance(v, dict):
        p = dict(v)
    else:
        p = {"value": v}
    return name, p, False


def _rule_do(rule: dict) -> tuple[str | None, dict]:
    """(action name, params) from a rule's `do`, in EITHER compiled shape."""
    do = rule.get("do")
    if not isinstance(do, dict):
        return None, {}
    name = do.get("kind") or do.get("action")
    if isinstance(name, str):
        p = do.get("params")
        return name, (dict(p) if isinstance(p, dict)
                      else {k: v for k, v in do.items()
                            if k not in ("kind", "action")})
    names = [k for k in do if k in _KNOWN_ACTIONS]
    if not names:
        return None, {}
    name = names[0]
    v = do[name]
    if isinstance(v, dict):
        p = dict(v)
    elif isinstance(v, str):
        # the shorthand forms P3's evaluator already accepted
        p = ({"button": v} if name == "fire" else
             {"preset": v} if name == "switch_cards" else {})
    else:
        p = {}
    return name, p


def _rule_phase(rule: dict, t_name) -> str:
    """'death' or 'battle' - WHERE this rule runs.

    The compiler states it outright: `latency: "death_handler"` for a
    death_screen rule, "main_loop" for everything else. Dispatching on that
    field rather than on the trigger name is deliberate - it is the compiler's
    own declaration of where it expects the rule to be executed, so a rule that
    names the death screen WITHOUT claiming the death phase is a rule of an
    unknown shape, and the observe loop refuses it loudly instead of guessing.
    """
    return "death" if rule.get("latency") == "death_handler" else "battle"


def _rule_admits(rule: dict, t_name, t_p: dict, normalized: bool,
                 a_name, a_p: dict, phase: str = "battle") -> str | None:
    """None if this rule can run in `phase`, otherwise WHY it never will.

    Checked BEFORE the trigger is evaluated, and that order is deliberate: a
    rule whose action this loop cannot execute must be retired the first time
    it is seen, not the first time its trigger happens to be true - otherwise
    an unrunnable rule sits silent for hours and only announces itself at the
    worst possible moment.
    """
    if t_name is None:
        return "no known trigger in `when`"
    if a_name is None:
        return "no known action in `do`"
    bad = _rule_admits_cooldown(rule, a_p)
    if bad:
        return bad
    if phase == "death":
        if t_name not in RULE_DEATH_TRIGGERS:
            return (f"{t_name!r} is not a death-screen trigger "
                    f"(latency said death_handler)")
        if a_name not in RULE_DEATH_ACTIONS:
            return (f"{a_name!r} may not run on the death screen. The stats "
                    f"dialog has no ability row, no sprint and no wall, and "
                    f"every navigation off it that this runtime owns is the "
                    f"death handler's own restart path - so anything else is "
                    f"a blind tap into a menu "
                    f"(allowed: {', '.join(RULE_DEATH_ACTIONS)})")
        return _rule_admits_action(a_name, a_p)
    if t_name in RULE_DEATH_TRIGGERS:
        return (f"{t_name!r} is never true on an in-battle pass - the death "
                f"handler in main() owns that screen, and this rule did not "
                f"declare `latency: death_handler`")
    if t_name not in RULE_TRIGGERS:
        return f"unknown trigger {t_name!r} (Tier B: {', '.join(RULE_TRIGGERS)})"
    if t_name == "bar":
        if t_p.get("bar") not in RULE_BARS:
            return f"unknown bar {t_p.get('bar')!r}"
        if t_p.get("below") is None:
            return "a `bar` rule with no `below` threshold can never fire"
        # NO RUNTIME DEFAULTS FOR THE BAR NUMBERS (Codex P4 #4). The compiler
        # emits `falling_samples` and `deadband` on every compiled bar rule,
        # explicitly, because a default here and a different default there is
        # invisible until a rescue does not happen: the reproduced case was a
        # compiled hp rule sitting under its threshold for three straight
        # passes without firing, while the raw-shaped twin fired at once. A
        # normalized rule that omits either number is a COMPILER BUG, and the
        # only safe thing to do with one is refuse it out loud.
        for key in ("falling_samples", "deadband"):
            if normalized and t_p.get(key) is None:
                return (f"compiled `bar` rule carries no {key!r} - the "
                        f"compiler must state every bar number explicitly "
                        f"(a plain threshold is falling_samples 0, deadband "
                        f"0.0); the runtime supplies no default")
            if t_p.get(key) is not None and not _finite(t_p[key]):
                return f"{key} must be a finite number, got {t_p[key]!r}"
    if t_name == "wall_collapse":
        if t_p.get("from_above") is None:
            return "wall_collapse with no `from_above` can never fire"
        if not _finite(t_p["from_above"]):
            return f"from_above must be finite, got {t_p['from_above']!r}"
    if t_name == "wave_between" and _wave_window(t_p) is None:
        return "wave_between needs two wave numbers"
    if t_name == "second_wind" and t_p.get("state", t_p.get("value")) \
            not in RULE_SW_STATES:
        return (f"unknown second_wind state "
                f"{t_p.get('state', t_p.get('value'))!r}")
    return _rule_admits_action(a_name, a_p)


def _finite(v) -> bool:
    """A real number, not NaN and not an infinity. NaN loses every comparison
    (so a NaN cooldown never blocks anything) and an infinity wins every one
    (so it blocks forever) - both are silent, which is what makes them worth
    refusing rather than clamping."""
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _rule_admits_cooldown(rule: dict, a_p: dict) -> str | None:
    """Every cooldown spelling this rule carries must be a finite number."""
    for src, key in _COOLDOWN_KEYS:
        v = (rule if src == "rule" else a_p).get(key)
        if v is not None and not _finite(v):
            return (f"{key} must be a finite number of seconds, got {v!r} "
                    f"(NaN never blocks, infinity blocks forever)")
    return None


def _rule_admits_action(a_name: str, a_p: dict) -> str | None:
    """None if the action itself is executable, otherwise why not. Split out
    because the death phase runs the same action checks under a different
    trigger set."""
    if a_name not in RULE_ACTIONS:
        return f"unknown action {a_name!r} (Tier B: {', '.join(RULE_ACTIONS)})"
    if a_name in ("fire", "burst") and _rule_button(a_p) not in RULE_BUTTONS:
        return f"unknown button {_rule_button(a_p)!r}"
    if a_name == "switch_cards" and not isinstance(a_p.get("preset"), str):
        return "switch_cards needs a card preset name"
    if a_name == "switch_cards":
        # NOT EXECUTABLE FROM A LIVE BATTLE, in any phase (Codex P6 #2, applied
        # to its twin). The audit found the in-run schedule doing this and it
        # is the same call from the same place: loadout.apply_cards opens with
        # a FIXED tap on the bottom nav row and returns by polling for HOME,
        # both written for a game sitting at Home. From a battle the opening
        # tap cannot be verified at all - no template of the in-battle nav row
        # exists - and a tap into an unknown screen is what CLAUDE.md #3/#6
        # forbid. This runs on coin farms AND tournaments, so it could blind-
        # tap a paid entry too.
        #
        # Retired HERE rather than refused at execution time so it is dropped
        # the first time the rule is seen, with a reason, instead of at
        # whatever wave its trigger first happens to be true.
        return ("switch_cards cannot run from a live battle: the only route to "
                "the cards screen (loadout.apply_cards -> tourney.open_nav) "
                "starts from HOME with an unverifiable nav-row tap and returns "
                "by polling for HOME. See orchestrator.run_in_run_actions for what "
                "would make it real")
    if a_name == "toggle_uw" and not isinstance(
            a_p.get("weapon", a_p.get("uw")), str):
        return "toggle_uw needs a weapon name"
    return None


def _rule_button(a_p: dict):
    """The ability a fire/burst rule names. `button` is the compiled key for
    both; `fire` is the raw schema's name inside a burst block."""
    return a_p.get("button", a_p.get("fire"))


def _wave_window(p: dict) -> tuple[float, float] | None:
    """(lo, hi) from a wave_between trigger, in any of the shapes a compiler
    might reasonably emit: [lo, hi], {from, to}, {min, max}, {at_least,
    below}."""
    v = p.get("value")
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = v
    else:
        lo = p.get("from", p.get("min", p.get("at_least")))
        hi = p.get("to", p.get("max", p.get("below")))
    try:
        return (float(lo), float(hi)) if lo is not None and hi is not None \
            else None
    except (TypeError, ValueError):
        return None


def _bar_read(frame, bar: str, cache: dict) -> tuple[float, str]:
    """(fill, state) for one bar, read AT MOST ONCE PER PASS.

    REUSES THE EXISTING HELPERS AND ADDS NO OCR. detect.hp_fill and
    detect.wall_overheal are the same column readers watch_frame and the fast
    watch already use; the cache is what keeps five bar rules from costing five
    reads of the same frame.
    """
    if bar not in cache:
        cache[bar] = ((detect.hp_fill(frame), "normal") if bar == "hp"
                      else detect.wall_overheal(frame))
    return cache[bar]


def _trigger_fires(rs: "RunState", frame, wave, i: int, name: str, p: dict,
                   cache: dict) -> tuple[bool, dict]:
    """(fired?, trigger snapshot for the log). Cheap by construction: wave
    comparisons and rs state cost nothing, and the two bar readers are the ones
    the loop already runs, behind a one-read-per-pass cache."""
    if name == "wave_at_least":
        n = p.get("value", p.get("wave"))
        snap = {"wave": wave, "at_least": n}
        return (wave is not None and n is not None and wave >= n), snap
    if name == "wave_between":
        win = _wave_window(p)
        snap = {"wave": wave, "between": list(win) if win else None}
        return (wave is not None and win is not None
                and win[0] <= wave <= win[1]), snap
    if name == "bar":
        bar = p.get("bar")
        fill, state = _bar_read(frame, bar, cache)
        prev = _rule_mem(rs, "rule_bar_prev").get(i)
        falls = _rule_mem(rs, "rule_bar_falling")
        # DIRECTION, when the rule asks for it. NO DEFAULTS: a compiled rule
        # always states both numbers (admission refuses one that does not), and
        # a raw P3-shaped rule that states neither gets 0 / 0.0, which is the
        # P3 evaluator's behaviour EXACTLY - it compared the level and nothing
        # else (`if detect.hp_fill(frame) >= below: continue`).
        deadband = float(p.get("deadband") or 0.0)
        need = int(p.get("falling_samples") or 0)
        if state == "immune":
            # (Codex P4 #7.) THE HISTORY DIES WITH THE PROC. During a Second
            # Wind the bar ROI shows the pink immunity countdown, and after it
            # the wall REGROWS FROM ZERO - so a fall recorded before the proc
            # paired with a rebuild sample after it reads as a fresh drain and
            # fires the rescue on a wall that is coming back on its own.
            # Reproduced by the audit; the fix is to forget, not to smooth.
            falls[i] = 0
            _rule_mem(rs, "rule_bar_prev").pop(i, None)
        else:
            if prev is not None:
                if fill < prev - deadband:
                    falls[i] = falls.get(i, 0) + 1
                elif fill > prev + deadband:
                    falls[i] = 0        # rising: it is recovering, stand by
            _rule_mem(rs, "rule_bar_prev")[i] = fill
        snap = {"bar": bar, "fill": round(fill, 3), "state": state,
                "falling": falls.get(i, 0), "below": p.get("below")}
        if state == "immune":
            # the pink Second Wind countdown occupies the wall ROI - it is not
            # a wall reading, and treating it as one fires on every proc
            return False, snap
        if state == "rebuilding":
            return falls.get(i, 0) >= need, snap    # broken IS below any level
        return (fill < float(p["below"]) and falls.get(i, 0) >= need), snap
    if name == "wall_collapse":
        fill, state = _bar_read(frame, "wall", cache)
        mem = _rule_mem(rs, "rule_bar_prev")
        key = ("collapse", i)
        prev = mem.get(key)
        mem[key] = None if state == "immune" else fill
        snap = {"bar": "wall", "fill": round(fill, 3), "state": state,
                "prev": prev, "from_above": p.get("from_above")}
        return (state == "rebuilding" and prev is not None
                and prev > float(p["from_above"])), snap
    if name == "fleet_mark":
        after = p.get("after_waves", 1)
        window = p.get("window_waves", 60)
        done = _rule_mem(rs, "rule_marks").setdefault(i, set())
        snap = {"wave": wave, "after_waves": after, "window_waves": window}
        if wave is None:
            return False, snap
        for m in marks():
            if m in done:
                continue
            if wave > m + window:
                done.add(m)             # missed it - retire, never fire late
                continue
            snap["mark"] = m
            return wave >= m + after, snap    # only the nearest pending mark
        return False, snap
    if name == "second_wind":
        want = p.get("state", p.get("value"))
        need = p.get("min_procs")
        need = 1 if need is None else int(need)
        snap = {"procs": rs.sw_proc_count, "open": bool(rs.sw_floater_seen),
                "state": want}
        if rs.sw_proc_count < need:
            return False, snap
        if want in (None, True, "any"):
            return True, snap
        if want == "open":
            return bool(rs.sw_floater_seen), snap
        if want == "closed":
            return not rs.sw_floater_seen, snap
        return (not rs.sw_floater_seen
                and time.monotonic() >= rs.sw_immune_until), snap
    return False, {}


def _rule_cooldown(rule: dict, a_p: dict) -> float:
    """The refire floor for one rule.

    THE RULE'S OWN FIELD FIRST - `refire_sec` is what the compiler emits (it
    already ranked the three spellings once, so the runtime does not have to),
    with `cooldown_sec`/`throttle_sec` accepted as aliases - then the action's
    own throttle_sec / refire_guard_sec, which is the only shape P3 emitted,
    then the module default. First key that carries a value wins; `is not
    None`, never `or`, so a compiled 0 means "no floor" rather than silently
    restoring the 5s default.

    Non-finite values never reach here - _rule_admits_cooldown retires the rule
    before it can run - but the isfinite test is repeated rather than assumed,
    because this is the function a future caller will reach for directly.
    """
    for src, key in _COOLDOWN_KEYS:
        v = (rule if src == "rule" else a_p).get(key)
        if v is not None and _finite(v):
            return float(v)
    return RULE_REFIRE_SEC


def _rule_suppressed(rs: "RunState", i: int, rid: str, why: str, now: float,
                     **extra) -> None:
    """A fire that did NOT happen, at debug level and RATE LIMITED.

    The observe loop runs ~1.4 times a second: a line per pass per rule turns
    the events file into something nobody reads, which is the same as not
    logging at all. One line per (rule, reason) per RULE_LOG_EVERY_SEC keeps
    the seam visible without drowning it.
    """
    mem = _rule_mem(rs, "rule_log_at")
    key = (i, why)
    if now - mem.get(key, -1e9) < RULE_LOG_EVERY_SEC:
        return
    mem[key] = now
    logger.event("rule_suppressed", index=i, id=rid, why=why, level="debug",
                 **extra)


def _rule_switch_cards(rs: "RunState", cards: str, i: int) -> tuple[bool, bool]:
    """switch_cards action: apply a card preset mid-run.

    loadout.apply_cards NAVIGATES OFF THE BATTLE SCREEN, so rs.bot_left_battle
    is set BEFORE the first tap - that flag is what tells the off-battle
    handler this menu is ours to clean up (the no-clicking-in-menus rule).
    One retry, then the rule is disabled for the rest of the run rather than
    walking the game through the card screens every second.

    Returns (touched_screen, succeeded). The disabled short-circuit is the one
    path that provably touches nothing; once apply_cards is entered it has
    already navigated, so a FAILURE still counts as contact.
    """
    if i in rs.rules_cards_off:
        return False, False
    from interactions import loadout
    rs.bot_left_battle = True
    try:
        loadout.apply_cards(cards)
        return True, True
    except Exception as e:                              # noqa: BLE001
        tries = rs.rule_cards_tries.get(i, 0) + 1
        rs.rule_cards_tries[i] = tries
        if tries >= 2:
            rs.rules_cards_off.add(i)
        logger.event("rule_switch_cards_failed", index=i, cards=cards,
                     tries=tries, disabled=i in rs.rules_cards_off,
                     error=str(e)[:120])
        return True, False


def _rule_toggle_uw(rs: "RunState", p: dict) -> tuple[bool, bool]:
    """toggle_uw action: an ultimate weapon on or off, mid-run.

    THROUGH shopper.uw_toggle AND NOTHING ELSE. That helper opens the UW tab,
    hunts the weapon by name, READS THE PILL, taps, and reads it back - the
    verify-after-write path the CL choreography and the run-start uw_wanted
    normalization already use. A coordinate tap on that panel is how the wrong
    weapon gets switched off for a whole run with the log claiming success.

    Returns (touched_screen, succeeded) - and touched is unconditionally True,
    because uw_toggle taps the tab strip before it can discover anything.
    """
    weapon = p.get("weapon", p.get("uw"))
    want = p.get("on")
    if want is None:
        want = p.get("want_on", True)
    ok = bool(shopper.uw_toggle(weapon, bool(want)))
    if ok and weapon == "chain_lightning":
        # keep the choreography's own bookkeeping honest - otherwise the next
        # pass sees rs.cl_on disagreeing with the panel and toggles it back
        rs.cl_on = bool(want)
    return True, ok


def _tournament_locked(frame) -> str | None:
    """Which lock forbids ending this run early, or None if none does.

    THE ABSOLUTE RULE (CLAUDE.md #2, user 2026-08-15): "you NEVER cancel a
    tournament run EVER". A ticket purchase auto-starts the run and the gem
    cost escalates 10 -> 20 -> 30, so a surrender there is unrecoverable.

    FOUR INDEPENDENT LOCKS, any one of which refuses - three from config and
    one from the screen itself, so a mislabelled preset cannot get past a
    correctly-read trophy badge and vice versa. Every early-exit path in this
    file goes through this one function: the Tier B `surrender_retry` action
    and the P6 `max_wave` knob asked the same question separately until they
    could drift apart, and this is the answer both now read.
    """
    p = preset()
    if p.get("kind") == "tournament":
        return "blueprint kind"
    if p.get("tournament_setup"):
        return "tournament_setup"
    if CONFIG.get("preset") == "tournament":
        return "legacy tournament preset"
    if screen.in_tournament(frame):
        return "trophy badge on screen"
    return None


def max_wave_reached(rs: "RunState", frame, wave: int | None) -> bool:
    """coin `max_wave`: end the run at wave N, through the guarded flow.

    ONE ATTEMPT PER RUN, whatever happens. The surrender goes through
    shard.abandon_run - the same chokepoint the Tier B action uses, carrying
    its own tournament guard - and if that aborts, the fallback is the runflag
    rather than a second try: the runner then leaves at its own death handler,
    which is later than asked but never wrong. Retrying a failed surrender
    means walking the game through the exit menus every pass with a live run
    on screen.

    A TOURNAMENT CAN NEVER REACH THE SURRENDER. `max_wave` is a coin-blueprint
    key and the compiler refuses it elsewhere, but the runtime refuses too:
    the compiled preset outlives its validation, and this is the one place a
    wrong `kind` would spend a ticket. Returns True only when the run really
    was ended.
    """
    limit = preset().get("max_wave")
    if limit is None or wave is None or wave < limit or rs.max_wave_done:
        return False
    rs.max_wave_done = True
    lock = _tournament_locked(frame)
    if lock:
        logger.event("max_wave_refused", wave=wave, limit=limit, lock=lock,
                     why="a TOURNAMENT run is never cancelled",
                     shot=logger.shot(frame, "max_wave_refused"))
        return False
    from flows import shard
    rs.bot_left_battle = True       # the exit menus are OURS to clean up
    try:
        shard.abandon_run()
        logger.event("max_wave", wave=wave, limit=limit, result="surrendered")
        return True
    except Exception as e:                              # noqa: BLE001
        runflag.request(f"max_wave_{limit}")
        logger.event("max_wave_fallback", wave=wave, limit=limit,
                     error=str(e)[:160], fallback="stop_after_run",
                     why="one attempt only - the runner leaves at its own "
                         "death handler instead of re-walking the exit menus")
        return False


def apply_cancel_sprint(rs: "RunState") -> bool:
    """coin `cancel_sprint`: end the intro sprint once, on request.

    The sprint LOCKS THE ABILITY ROW, so a blueprint that wants its abilities
    usable from the start says so and this ends it. Through end_intro_sprint,
    which verifies every step (indicator found, confirm dialog up, Yes matched)
    and carries its own 30s retry floor - so "once" is the successful end, and
    a run with no sprint left simply logs "indicator not found" and changes
    nothing. Absent key (every legacy preset) never calls it at all.
    """
    if not preset().get("cancel_sprint") or rs.sprint_ended:
        return False
    if end_intro_sprint(rs, "preset_cancel_sprint"):
        rs.sprint_ended = True
        logger.event("cancel_sprint", wave=rs.tracker.last, result="ended")
        return True
    return False


def run_in_run_actions(rs: "RunState", frame, wave: int | None) -> bool:
    """Tournament `in_run_actions`: DISABLED AT RUNTIME. Refuses, loudly.

    Compiled shape (ordered): [{id, at_wave, switch_cards, requires}]. The
    intent is a scheduled deck change inside a tournament run. It is not
    implemented, because it cannot be implemented SAFELY from what this
    codebase can currently see, and the run it would gamble on is a paid one.

    WHY THERE IS NO ROUTE (Codex P6 #2, and it was right). The only way to the
    cards screen in this codebase is loadout.apply_cards -> tourney.open_nav,
    whose first act is a FIXED tap on the bottom nav row at (448, 2470), and
    whose return leg (tourney.return_to_game) polls for `on_home` - the HOME
    screen, not the battle. Both ends are written for a game sitting at Home.
    Called from a live battle that first tap is unverified: no template of the
    in-battle nav row exists under templates/, so nothing can confirm the row
    is even drawn there before the tap lands, and a tap into an unknown screen
    during a tournament is precisely what CLAUDE.md #3 and #6 forbid. This is
    the same reasoning that already refused `switch_cards` in the death phase
    (see RULE_DEATH_ACTIONS above) - P6 wired up the in-run path without
    re-asking the question, and the two answers have to agree.

    WHAT WOULD MAKE IT REAL, so the next person does not re-derive this:
      1. a template of the cards icon in the nav row AS IT LOOKS DURING A RUN,
         cut from a live tournament battle frame, so the row can be CONFIRMED
         present before anything is tapped;
      2. a return leg that verifies the BATTLE (screen.identify -> battle, wave
         readable again) instead of Home;
      3. one observed live excursion, tournament-side, before it is trusted.
    All three need a device and a real event. Until then this refuses.

    A refusal costs a deck that stays as it was entered. A blind tap costs the
    entry. The compiled key stays supported end-to-end (the compiler emits it,
    the plan gates on it) so nothing has to be unwound to turn it on later.
    """
    p = preset()
    actions = p.get("in_run_actions") or []
    if not actions or wave is None:
        return False
    # ONCE PER RUN, not once per pass: at ~1.4fps a per-pass log would bury
    # the events file in the time it takes to read one wave.
    if "disabled" not in rs.in_run_off:
        rs.in_run_off.add("disabled")
        logger.event("in_run_action_failed", id=None, wave=wave,
                     count=len(actions), disabled=True,
                     kind=p.get("kind"),
                     error="in_run_actions is disabled at runtime: there is "
                           "no verified route from a live battle to the cards "
                           "screen and back (nav-row tap is unverifiable, "
                           "return leg checks Home). Refusing rather than "
                           "blind-tapping inside a paid run.")
    return False


def _rule_surrender(rs: "RunState", frame, i: int, rid: str) -> tuple[bool, bool]:
    """surrender_retry action: abandon the live run - through the ONE guarded
    flow, and never on a tournament.

    THE ABSOLUTE RULE (CLAUDE.md #2, user 2026-08-15): "you NEVER cancel a
    tournament run EVER". A ticket purchase auto-starts the run and the gem
    cost escalates 10 -> 20 -> 30, so a surrender there is unrecoverable - the
    reason tourney.end_round and shard.abandon_run both carry the guard.

    THREE INDEPENDENT LOCKS, any one of which refuses:
      1. the compiled blueprint says tournament (`kind`, `tournament_setup`)
      2. the legacy `tournament` preset is bound
      3. screen.in_tournament(frame) - the trophy badge, i.e. the screen itself
    ...and then shard.abandon_run's own guard re-reads the screen at the
    chokepoint. A refusal RETIRES the rule for the run: on a tournament there
    is no later moment at which surrendering becomes acceptable.

    The abandon flow navigates off the battle screen, so rs.bot_left_battle is
    set first - the flag that tells the off-battle handler this menu is ours.

    Returns (touched_screen, succeeded). A refusal touches NOTHING - that is
    the whole point of it, and the test asserts zero taps on all three locks.
    """
    lock = _tournament_locked(frame)
    if lock:
        _rule_retire(rs, i)
        logger.event("rule_refused", index=i, id=rid, action="surrender_retry",
                     why="a TOURNAMENT run is never cancelled", lock=lock,
                     shot=logger.shot(frame, "rule_surrender_refused"))
        return False, False
    from flows import shard
    rs.bot_left_battle = True
    try:
        shard.abandon_run()
        return True, True
    except Exception as e:                              # noqa: BLE001
        logger.event("rule_surrender_failed", index=i, id=rid,
                     error=str(e)[:120])
        return True, False


def _rule_act(rs: "RunState", frame, i: int, rid: str, name: str,
              p: dict) -> tuple[bool, bool]:
    """Execute one rule's action -> (touched_screen, succeeded).

    Every path is an EXISTING verified helper: fire_button (tap + border
    re-read), end_intro_sprint (every step verified), shopper.uw_toggle (pill
    read before and after), loadout.apply_cards, shard.abandon_run (tournament
    guard), runflag (no screen contact at all). Nothing here taps a raw
    coordinate.

    TWO RESULTS, NOT ONE (Codex P4 #6). `succeeded` decides whether the rule is
    retired for the run; `touched_screen` decides whether the per-tick budget
    is spent - and those are different questions, because a fire that tapped
    and did not confirm, or a card swap that navigated and then threw, has
    already moved the game out from under the frame the next rule would judge.
    Where contact cannot be proven either way the answer is CONSERVATIVE: any
    helper that can tap counts as contact once entered. Over-spending the
    budget costs one pass; under-spending it costs a tap into an unknown
    screen.
    """
    if name == "fire":
        return True, bool(fire_button(frame, _rule_button(p), rid,
                                      require_ready=bool(
                                          p.get("require_ready", False))))
    if name == "burst":
        # A TIER B BURST IS NOT THE TIER A BURST, and must not pretend to be.
        # _fast_wall_watch's burst is three instant taps off ONE frame at ~3Hz
        # because it is racing a sub-second wall drain; that is the only thing
        # that justifies its last-matched-position fallback. At 1.4fps there is
        # no such race and no such excuse, so the same intent is expressed
        # through the verified helpers: cancel the sprint (it locks the
        # ability row), then fire the ability with a confirmed tap. The
        # compiler only routes a burst here when it could NOT claim a Tier A
        # slot - `arm: always`, or a second bar rule - i.e. exactly the cases
        # with no wall-collapse deadline attached.
        if p.get("cancel_sprint", True) and not rs.sprint_ended:
            # ABORT ON A FAILED CANCEL (Codex P4 #5). end_intro_sprint returns
            # False after it has already tapped the indicator - the confirm
            # dialog may be half-drawn over the ability row - so firing on the
            # frame in hand would put the ability tap into that dialog. Stop
            # and log; the wall is not on a clock at Tier B, and the next pass
            # re-evaluates against a screen that has settled.
            if not end_intro_sprint(rs, f"{rid}_sprint"):
                logger.event("rule_burst_aborted", index=i, id=rid,
                             why="intro sprint not cancelled - the ability row"
                                 " is locked and the confirm dialog may be up")
                return True, False
            rs.sprint_ended = True
        # RECAPTURE AND RE-VERIFY BEFORE THE ABILITY TAP. The frame this rule
        # was judged on is now seconds old and a whole dialog out of date; a
        # readable wave counter is the tower-on-screen proof every other tap
        # site in this file uses.
        f2 = capture.grab()
        if wave_reader.read_wave(f2) is None:
            logger.event("rule_burst_aborted", index=i, id=rid,
                         why="no wave counter after the sprint cancel - not "
                             "looking at a live battle, so nothing is tapped",
                         shot=logger.shot(f2, "rule_burst_aborted"))
            return True, False
        # RETAPS, honoured (Codex P4 medium): fire_button already taps AND
        # confirms via a border re-read, so a "retap" is a bounded retry of
        # that whole verified cycle - each attempt on a FRESH frame, never a
        # blind second tap at the same coordinate off a stale one.
        ok = False
        for attempt in range(max(1, int(p.get("retaps") or 1))):
            if attempt:
                f2 = capture.grab()
                if wave_reader.read_wave(f2) is None:
                    break               # death dialog reads dark: NO taps
            ok = bool(fire_button(f2, _rule_button(p), f"{rid}_{attempt + 1}",
                                  require_ready=bool(p.get("require_ready",
                                                           False))))
            if ok:
                break
        if ok and _rule_button(p) == "demon_mode":
            rs.dm_fired = True
            rs.last_fire["demon_mode"] = time.monotonic()
        return True, ok
    if name == "cancel_sprint":
        ok = end_intro_sprint(rs, rid)
        rs.sprint_ended = rs.sprint_ended or bool(ok)
        return True, bool(ok)
    if name == "stop_after_run":
        # NOT an interrupt: the runner still leaves at its death handler, at a
        # run boundary (the runflag contract). No rule ends a live run. The one
        # action in the vocabulary that touches no screen at all, which is also
        # why it is the only one allowed in the death phase.
        runflag.request(rid)
        return False, True
    if name == "switch_cards":
        return _rule_switch_cards(rs, p["preset"], i)
    if name == "toggle_uw":
        return _rule_toggle_uw(rs, p)
    if name == "surrender_retry":
        return _rule_surrender(rs, frame, i, rid)
    return False, False


def eval_rules(rs: "RunState", frame, wave: int | None) -> None:
    """TIER B rules (SCHEMA.md): one pass of the main loop, ~1.4fps.

    Tier A - the wall-bar rescue burst - is compiled into abilities{} and runs
    inside _fast_wall_watch; nothing here is sub-second and nothing here may
    pretend to be. `death_screen` is deliberately NOT evaluated: the death
    handler in main() owns that path, and this function is only ever called on
    an in-battle pass, so the trigger is unreachable here by construction.

    Rules are evaluated IN COMPILED ORDER and fire at most once per run unless
    they say `repeat: true`. Legacy presets carry no `rules` key, so this
    returns on line one.

    ONE SCREEN-TOUCHING ACTION PER TICK, and the reason is not tidiness:
      * act.tap enforces a rate cap, so two taps in one pass either queue or
        get refused - the second rule would lose either way, silently;
      * every acting rule CHANGES THE SCREEN (a card swap leaves the battle
        entirely, a UW toggle drags the panel open), so the frame the second
        rule would judge against is already stale - it was captured before the
        first action ran, and acting on it is the blind-tap-into-an-unknown-
        screen failure the hard rules exist to prevent.
    The budget is spent on CONTACT, not on success (Codex P4 #6): a fire that
    tapped without confirming, or a card swap that navigated and then threw,
    has already moved the game - so the next rule's frame is stale whether or
    not the first one "worked". A rule that merely EVALUATED spends nothing,
    which is what keeps a broken rule from silencing the ones below it: its own
    per-rule ledger disables it after two attempts (audit NEW#7), and from then
    on it costs no contact and its neighbours run.
    """
    _run_rules(rs, frame, wave, "battle")


def run_death_rules(rs: "RunState", frame) -> bool:
    """The DEATH PHASE of the same interpreter, called once from the death
    handler after the run log is collected and before the restart.

    Separate from eval_rules because the two phases see different screens: on
    the stats dialog there is no ability row, no sprint and no wall, so only
    RULE_DEATH_ACTIONS may run (see that constant - it is exactly one action,
    and it touches nothing) and the whole bar/wave vocabulary is meaningless.
    The death handler keeps RETRY, the restart and the screen.

    Returns True if a rule touched the screen. Nothing in RULE_DEATH_ACTIONS
    can today, so it is always False - the contract is kept live because the
    caller re-grabs on it, and that is what makes narrowing the action set the
    safe default rather than a thing to remember.
    """
    return _run_rules(rs, frame, rs.tracker.last, "death")


def _run_rules(rs: "RunState", frame, wave: int | None, phase: str) -> bool:
    """One pass of the interpreter in `phase` (see eval_rules for the
    contract). Returns True if a screen-touching action actually ran."""
    rules = preset().get("rules") or []
    if not rules:
        return False
    now = time.monotonic()
    cache: dict = {}            # one bar read per pass, shared by every rule
    touched = False             # the one-CONTACT-per-tick budget
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        # PER-RULE STATE KEY: the compiled `id` when there is one, the index
        # otherwise. Never the loop variable directly - two recompiles can
        # reorder the list, and a rule's "already fired this run" must follow
        # the rule, not the slot it happened to sit in.
        k = _rule_key(rule, i)
        # RETIRED BEATS REPEAT (Codex P4 #8). `rules_fired` means "done for
        # now" and `repeat: true` overrides it; RETIRED means "this rule can
        # never run in this process", and nothing overrides that. Before the
        # split, an unsupported rule marked `repeat` re-announced itself on
        # every single pass, and a refused tournament surrender came back the
        # moment its cooldown expired.
        if k in _rule_mem(rs, "rules_retired"):
            continue
        if k in rs.rules_fired and not rule.get("repeat"):
            continue
        rid = _rule_id(rule, i)
        when = rule.get("when") or {}
        do = rule.get("do") or {}
        t_name, t_p, normalized = _rule_when(rule)
        a_name, a_p = _rule_do(rule)
        # A rule belonging to the OTHER phase is skipped in silence: it is not
        # unsupported, it is simply not this screen's turn.
        if _rule_phase(rule, t_name) != phase:
            continue
        # ---- ADMISSION, before anything is read or evaluated. A rule this
        # loop can never run is retired ON SIGHT and logged once - never
        # silently skipped, and never left to announce itself mid-collapse.
        why = _rule_admits(rule, t_name, t_p, normalized, a_name, a_p, phase)
        if why:
            _rule_retire(rs, k)
            logger.event("rule_unsupported", index=i, id=rid, when=when,
                         do=do, phase=phase, why=why)
            continue
        if now < rs.rule_next.get(k, 0.0):
            _rule_suppressed(rs, k, rid, "cooldown", now,
                             wait=round(rs.rule_next[k] - now, 1))
            continue                    # per-rule refire guard
        if phase == "death":
            fires, snap = True, {"phase": "death", "wave": wave}
        else:
            fires, snap = _trigger_fires(rs, frame, wave, k, t_name, t_p,
                                         cache)
        if not fires:
            continue                    # the ordinary case: no log, no cost
        if touched:
            # deferred, NOT dropped: no rule_next stamp and no rules_fired, so
            # the next pass re-evaluates it against a fresh frame
            _rule_suppressed(rs, k, rid, "one_action_per_tick", now,
                             trigger=snap)
            continue
        contact, ok = _rule_act(rs, frame, k, rid, a_name, a_p)
        rs.rule_next[k] = now + _rule_cooldown(rule, a_p)
        if ok and t_name == "fleet_mark" and "mark" in snap:
            _rule_mem(rs, "rule_marks").setdefault(k, set()).add(snap["mark"])
            if a_name in ("fire", "burst") and _rule_button(a_p) == "nuke":
                # share the ledger with the Tier A schedule so one mark is
                # never nuked twice, once from each tier
                rs.nuked_marks.add(snap["mark"])
        logger.event("rule_fire", index=i, id=rid, when=when, do=do, wave=wave,
                     latency=rule.get("latency"), phase=phase, trigger=snap,
                     action=a_name, result=ok, ok=ok, touched=contact)
        if ok:
            rs.rules_fired.add(k)
        if contact:
            touched = True              # the budget is spent on CONTACT
    return touched


def _fast_wall_watch(rs: "RunState", ab: dict) -> str:
    """Sub-second wall sampling while a rescue is plausibly imminent.

    Grab -> classify columns -> decide, nothing else - ~3 samples/second,
    with the raw screencap (~0.35s) as the floor. Exists because the wall's
    final drain is FASTER than a full observe pass: with wave OCR, the badge
    match, gem search and a shopper step in the loop, three straight T19
    benches read [full] then [broken] with no value in between, so no
    threshold on the main loop's readings could ever trigger in time.

    The rescue combo fires INLINE at the first sight of the overheal under
    the threshold or a broken wall: Intro Sprint cancelled first (it locks
    the ability row), Demon Mode immediately after, instant taps throughout
    (user: "a combination of clicks that are very fast"). Before any tap the
    wave counter must read - a dark bar is also what the DEATH DIALOG looks
    like from this ROI, and the no-clicks-off-battle rule outranks any
    rescue.

    Returns the reason control went back to the main loop: 'fired' |
    'immune' (new proc) | 'off' (not in battle) | 'timeout'.

    THERE IS NO FAT-WALL STANDDOWN AND NO SHORT DEADLINE. Both existed until
    2026-08-16 and together they lost tournament run 3 at wave 1390: the
    wall read a fat 1.0 four samples in a row, the watch handed control
    back for a main-loop pass (~4s with the shopper in it), and the entire
    collapse - full wall to dead tower - fit inside that blind window. The
    end-game drain is FASTER than a main-loop pass, so once the post-SW
    watch opens, this loop keeps the wall in sight until it fires, a new
    proc lands, or the run ends. Gems and shop sweeps are worthless in the
    minute this loop exists to win; a fat wall merely slows sampling down.
    The 30-min ceiling is a runaway stop, not a design parameter.
    """
    thresh = ab["dm_below"]
    # HOISTED ONCE, READ NEVER AGAIN (SCHEMA.md, Tier A): the per-sample body
    # of this loop is the one place in the autopilot where a dict lookup is
    # too slow to be tasteful - the wall collapse it is racing is shorter than
    # a main-loop pass. Defaults are TODAY'S LITERALS, so a preset that
    # carries none of these keys samples exactly as it did before profiles.
    def _pol(key, default):
        # `is None`, never `or`: a compiled 0 is a LEGITIMATE value (deadband 0
        # = every decrease counts; falling_samples 0 = fire on the first
        # sample) and `or` would silently restore the legacy literal instead.
        v = ab.get(key)
        return default if v is None else v

    falling_samples = int(_pol("falling_samples", 2))
    deadband = float(_pol("deadband", 0.01))
    # No wall_collapse rule keeps the literal that has been in this loop since
    # 2026-08-16 - the collapse catch is a safety net, and a net is not
    # something to remove because nobody asked for it.
    collapse_from = float(_pol("collapse_from", 0.3))
    burst_cancel_sprint = bool(_pol("burst_cancel_sprint", True))
    burst_retaps = int(_pol("burst_retaps", 3))
    # abilities.burst_require_MATCH, and the name is the whole point: the
    # burst has no readiness test to honour - it is raw instant taps by
    # design - so this is not fire_button's `require_ready`. It means "only
    # tap a glyph you can actually see": ON, Demon Mode is tapped only at a
    # MATCHED template centre, never at the fixed fallback coordinate.
    # Default False is exactly today's behaviour.
    burst_require_match = bool(_pol("burst_require_match", False))
    unmatched_logged = 0.0        # rate limit for rescue_dm_unmatched: the
                                  # suppressed path keeps sampling at 3Hz and
                                  # a line per sample is a log storm
    # THE FLEET SCHEDULE IS HOISTED TOO. Everything the per-sample body reads
    # must be a local: this loop is racing a wall drain that is faster than a
    # main-loop pass, and "just one dict lookup" is how that invariant dies.
    fleet_cfg = ab.get("nuke_on_fleet")
    _fleet = fleet_cfg or {}
    after = _fleet.get("after_waves", 1)
    window = _fleet.get("window_waves", 60)
    # nuke_on_fleet.throttle_sec / .require_ready, defaulting to what both
    # fleet sites hardcode today: a 5s retry floor and a Nuke that IS gated
    # on the readiness test.
    throttle = _fleet.get("throttle_sec")
    throttle = 5.0 if throttle is None else float(throttle)
    fleet_ready = bool(_fleet.get("require_ready", True))
    # TWO DEADLINES. The 30-min ceiling is the runaway stop. The preset's
    # post_sw_watch_sec window is the DESIGN deadline and lives in
    # rs.post_sw_until (inf for null = watch for the rest of the run, the
    # tournament ruling above). Until 2026-09-04 only the ENTRY gate in
    # watch_frame read the window: a farm preset with post_sw_watch_sec 30
    # entered the watch and then held the main loop - no gems, no CL, no
    # heartbeat - for the full 30 minutes after every first Second Wind
    # (coin runs 2026-09-03 08:41 and 2026-09-04 04:58, both read as hangs).
    # Only honoured under hold_until_second_wind: without the hold the
    # window is never armed and post_sw_until stays 0.0.
    hold = bool(ab.get("hold_until_second_wind", True))
    deadline = time.monotonic() + 1800.0
    if hold:
        deadline = min(deadline, rs.post_sw_until)
    fat = 0
    # DIRECTION IS THE TRIGGER, NOT THE LEVEL (user, 2026-08-15): after every
    # proc the wall REGROWS from zero, so "extent < 20%" is true during every
    # harmless rebuild, and the 'Rebuilding' banner is what a normal post-proc
    # regrow looks like. Firing there spends Demon Mode on a wall that is
    # coming back on its own. The rescue is for the wall going DOWN: two
    # consecutive falling samples below the threshold, or a fat wall that
    # turned into the broken banner inside one sampling gap.
    prev = None
    falling = 0
    sample = 0
    no_wave = 0
    while time.monotonic() < deadline:
        frame = capture.grab()
        sample += 1
        # OFF-BATTLE ESCAPE (2026-08-16): the watch holds for minutes now,
        # and menus and death dialogs read as an endless dark 'rebuilding'
        # wall - without this check the loop spins its 3Hz captures against
        # the HOME SCREEN until the runaway cap (observed live: a false SW
        # proc off menu art opened the watch with no battle on screen). The
        # wave counter is the tower-on-screen proof, same as the danger
        # path; checked every 8th sample to keep the fast path fast.
        if sample % 8 == 0:
            w = wave_reader.read_wave(frame)
            if w is None:
                no_wave += 1
                if no_wave >= 2:
                    return "off"
            else:
                no_wave = 0
                rs.tracker.update(w)    # a stale tracker here logged a
                                        # 5495-fleet death as "wave 5420"
                # FLEET MARKS DON'T PAUSE FOR THE WATCH (2026-08-18): the
                # x495 fleet lands inside the post-SW watch on T14, the
                # main-loop nuke schedule is starved while this loop owns
                # the process, and an unnuked fleet drains the wall faster
                # than the rescue burst can answer - three straight runs
                # died at the 5495 mark this way. Same trigger and throttle
                # as the main loop, run off this loop's own wave reads
                # (~3s cadence at 8 samples/read).
                wv = rs.tracker.last
                # fleet_cfg / after / window / throttle / fleet_ready are all
                # hoisted at watch entry: the per-sample body of this loop
                # reads LOCALS ONLY, which is the whole reason it can keep up
                # with a drain that outruns a main-loop pass.
                if fleet_cfg and wv:
                    now_m = time.monotonic()
                    for m in marks():
                        if m in rs.nuked_marks:
                            continue
                        if wv > m + window:
                            rs.nuked_marks.add(m)
                            continue
                        if wv >= m + after and now_m >= rs.fleet_try_at:
                            rs.fleet_try_at = now_m + throttle
                            if fire_button(frame, "nuke", f"fleet_{m}",
                                           require_ready=fleet_ready):
                                rs.nuked_marks.add(m)
                                logger.event(
                                    "fleet_nuke", mark=m, wave=wv,
                                    fast_watch=True,
                                    shot=logger.shot(frame,
                                                     f"fleet_nuke_w{wv}"))
                        break           # only the nearest pending mark
        ext, state = detect.wall_overheal(frame)
        if state == "immune":
            return "immune"             # a new proc - the main loop owns it
        if state == "normal":
            if prev is not None:
                if ext < prev - deadband:
                    falling += 1
                elif ext > prev + deadband:
                    falling = 0         # rising: it is recovering, stand by
            danger = ext < thresh and falling >= falling_samples
            collapsed = False
            prev = ext
        else:                           # 'rebuilding'
            danger = falling >= falling_samples   # drained, then it broke
            # A wall with real substance one sample ago that now shows the
            # broken banner collapsed inside one gap. Fixed 0.3, NOT the
            # preset threshold: tournament runs dm_below at 1.0 (any-falling
            # trigger, the pool is too deep for a level to mean anything)
            # and prev > 1.0 can never be true.
            collapsed = prev is not None and prev > collapse_from
            danger = danger or collapsed
        if danger:
            if wave_reader.read_wave(frame) is None:
                return "off"            # death dialog reads dark too: NO taps
            # THE BURST (user, 2026-08-15 final ruling): "the first click
            # done on the Intro Sprint and cancelling it must immediately
            # after that do the click to do Demon mode - no more checks."
            # Three instant taps off the ONE frame already in hand - sprint
            # indicator, Yes at its fixed dialog spot, Demon Mode - with
            # nothing between them but the dialog's render time. The old
            # path's dialog polling and settle sleeps put the confirmed DM
            # 3.4s after detection (run 6), and the wall broke first anyway.
            # ALL verification is after the burst, never between its taps.
            # (Codex pass 4) THE last_fire STAMP USED TO BE HERE, before
            # the burst was even decided - so a burst that ended up tapping
            # nothing still started the 15s refire guard, and 15s of a T19
            # drain is the whole run. It now happens at the DM tap itself.
            sprint_pt = (None if rs.sprint_ended or not burst_cancel_sprint
                         else detect.find_intro_sprint(frame))
            dm = detect.button_state(frame, "demon_mode")
            matched = bool(dm.present and dm.center)
            if matched:
                # remember where DM REALLY is this run - the only sanctioned
                # burst fallback (below)
                rs.dm_seen_at = dm.center
            # burst_require_match ON = NEVER TAP A BUTTON WE CANNOT SEE.
            # The fallback is the run's own LAST MATCHED DM position, never
            # a fixed constant (2026-08-29): the ability-row order is chosen
            # per run, and the old fixed RESCUE_DM_PT (85,1493) blind-tapped
            # the NUKE in the user's tournament at a 96.6% wall wobble - DM
            # had been matched at (242,1493) thirteen waves earlier, so the
            # remembered point would have hit. Before the first sighting of
            # the run there is no fallback at all: None means "not this
            # frame" - it never means "give up".
            dm_pt = (dm.center if matched else
                     (None if burst_require_match else rs.dm_seen_at))
            # NO NUKE IN THE BURST (user, 2026-08-18: "remove the Nuke from
            # the loop at all"). The Nuke belongs to the fleet-mark schedule
            # alone - which this watch now runs itself - so the rescue is
            # Demon Mode only. The old second click ("we might have a Nuke",
            # 2026-08-15) could burn the Nuke into a pre-fleet drain and
            # leave it cooling down exactly when the mark check needed it.
            try:
                if sprint_pt is not None:
                    act.tap(*sprint_pt, reason="rescue_sprint", instant=True)
                    time.sleep(0.35)        # dialog render - the only wait
                    act.tap(*RESCUE_YES_PT, reason="rescue_sprint_yes",
                            instant=True)
                    rs.sprint_ended = True
            except act.TapRefused as e:
                logger.event("rescue_burst", error=str(e))
            # ---- NO GLYPH IN STRICT MODE: KEEP WATCHING, DO NOT RETIRE.
            # The wall is still draining, so the one thing this must not do is
            # END THE WATCH: returning here would hand back to a main loop
            # whose refire guard then sits out the collapse - one missed
            # template match costing the run. Cancelling the sprint just above
            # is often exactly what REVEALS the button (the ability row is
            # dimmed while the sprint runs), so the next sample gets a fresh
            # glyph read and bursts then. Nothing above this line stamps
            # rs.last_fire or sets rs.dm_fired, so a suppressed burst costs
            # one sample and nothing else.
            if dm_pt is None:
                if time.monotonic() - unmatched_logged > 2.0:
                    unmatched_logged = time.monotonic()   # 3Hz sampling would
                    logger.event(                         # otherwise storm
                        "rescue_dm_unmatched", wave=rs.tracker.last,
                        fill=round(ext, 3), state=state,
                        sprint_cancelled=sprint_pt is not None,
                        shot=logger.shot(frame, "rescue_dm_unmatched"))
                continue
            try:
                # 70-120ms jitter before the DM click (user, 2026-08-15):
                # a human-scale beat between the Yes and the ability tap -
                # the zero-gap burst may be part of why the game shed it.
                time.sleep(random.uniform(0.070, 0.120))
                act.tap(*dm_pt, reason=f"rescue_dm_{ext:.3f}", instant=True)
                # STAMPED ON THE REAL TAP, never before the decision.
                rs.last_fire["demon_mode"] = time.monotonic()
            except act.TapRefused as e:
                logger.event("rescue_burst", error=str(e))
            logger.event("rescue", bar="wall", fill=round(ext, 3),
                         state=state, wave=rs.tracker.last,
                         sw_count=rs.sw_proc_count, fast_watch=True,
                         burst=True, sprint=sprint_pt is not None,
                         # instant taps emit no tap events - record where the
                         # DM click went and whether the glyph matched, or a
                         # failed rescue cannot be diagnosed (2026-08-18: the
                         # 5495-fleet postmortem had no tap coordinates at all)
                         dm_at=(list(dm_pt) if dm_pt else None),
                         dm_matched=bool(dm.present),
                         shot=logger.shot(frame, f"rescue_w{rs.tracker.last}"))
            # AFTER the burst: verify, and RETAP while DM still reads ready.
            # The wave-30 wall break is the laggiest moment of a T19 run and
            # the 2026-08-15 bench dropped the DM tap there three runs out of
            # four - button READY (~210) before AND after. A retap into a DM
            # that did fire is a cooldown no-op - harmless. The blind Yes can
            # also have missed, leaving the confirm dialog up: that retaps
            # the real matched Yes, then DM again.
            fired = False
            for attempt in range(1, burst_retaps + 1):
                if dm_pt is None:
                    break       # unreachable now that a suppressed burst
                                # continues the watch - kept as the second
                                # lock on "never confirm a tap we never made"
                f2 = capture.grab()
                sc = screen.identify(f2)
                if sc is not None and sc.name == "intro_sprint_end":
                    s, py = screen._match(f2, "home/intro_sprint_yes.png",
                                          ((1380, 1520), (560, 900)))
                    if s >= 0.85:
                        act.tap(*py, reason="rescue_sprint_yes_retry",
                                instant=True)
                    act.tap(*dm_pt, reason=f"rescue_dm_retap{attempt}",
                            instant=True)
                    continue
                if wave_reader.read_wave(f2) is None:
                    break                   # death dialog reads dark: NO taps
                bv = detect.button_border_val(f2, "demon_mode")
                if bv is not None and bv < 150:
                    fired = True            # dimmed (active ~100): it took
                    break
                try:
                    act.tap(*dm_pt, reason=f"rescue_dm_retap{attempt}",
                            instant=True)
                except act.TapRefused as e:
                    logger.event("rescue_burst", error=str(e))
                    break
            if not fired and dm_pt is not None:
                # Last look, ~1s later: dimmed (<150, active ~100) = fired.
                #
                # THE `dm_pt is not None` HALF IS A SAFETY CATCH, NOT TIDINESS.
                # When burst_require_match suppressed the tap, NOTHING of ours
                # touched that button - so a dimmed border here belongs to
                # somebody else: a Demon Mode still running from an earlier
                # proc, or an ability on cooldown. Reading it as our fire sets
                # rs.dm_fired, which retires the rescue for the whole rest of
                # the run: the tower would then die waiting on a Demon Mode
                # that was never cast, and the log would say it fired.
                # A border that cannot be read is only a fire if we are still
                # IN BATTLE - the death dialog also hides the button, and
                # counting that as "confirmed" is how run 3 logged a fire the
                # tower never got.
                time.sleep(1.0)
                f2 = capture.grab()
                bv = detect.button_border_val(f2, "demon_mode")
                fired = ((bv is not None and bv < 150) or
                         (bv is None and wave_reader.read_wave(f2) is not None))
            logger.event("rescue_confirm", button="demon_mode",
                         confirmed=fired,
                         ms=round((time.monotonic()
                                   - rs.last_fire["demon_mode"]) * 1000))
            if fired:
                rs.dm_fired = True
                rs.last_fire["demon_mode"] = time.monotonic()
            return "fired"
        # A fat wall does NOT end the watch (2026-08-16, tournament wave 1390:
        # the standdown here is what the fatal collapse hid behind). It only
        # slows the sampling - the collapse from >=0.5 to broken still takes
        # over a second, so a half-rate poll cannot miss the WHOLE drain, and
        # every capture skipped is emulator FPS given back to the game.
        fat = fat + 1 if (state == "normal" and ext >= 0.5) else 0
        if fat >= 4:
            time.sleep(0.35)
    return "timeout"


def watch_frame(rs: "RunState", frame) -> str | None:
    """Constant monitoring, run on EVERY captured frame.

    Returns a status the main loop acts on:
      'dead'   - death screen showing (only the RETRY click is allowed)
      'menu'   - tower NOT on screen (some other menu): NO clicking at all,
                 any active sweep must be aborted immediately
      None     - in battle, all systems go
    The battle-presence signal is the wave counter: no readable wave number
    on 2 consecutive frames means we are not looking at a run."""
    now = time.monotonic()
    # death needs BOTH dialog signals (see detect.death_screen) AND a 2-frame
    # debounce AND an unreadable wave counter: the dialog covers the wave box,
    # so a readable wave means the tower is alive no matter what matched.
    dead, _ = detect.death_screen(frame)
    if dead and wave_reader.read_wave(frame) is None:
        rs.dead_frames += 1
        if rs.dead_frames >= 2:
            return "dead"
        return "menu"                # hands off until it is confirmed
    rs.dead_frames = 0

    raw = wave_reader.read_wave(frame)
    rs.tracker.update(raw)
    rs.no_wave = 0 if raw is not None else rs.no_wave + 1
    if rs.no_wave >= 2:
        return "menu"                # tower not on screen -> hands off

    # ---- stall alarm: the tracked wave must keep moving while in battle.
    # A frozen counter is silent - fleet nukes and the CL latch simply never
    # fire - so surface it loudly instead of waiting for someone to notice.
    if rs.tracker.last != rs.wave_seen:
        rs.wave_seen = rs.tracker.last
        rs.wave_seen_at = now
        rs.wave_stall_logged = False
    elif (rs.wave_seen is not None and not rs.wave_stall_logged
          and now - rs.wave_seen_at > 300):
        # `wave_seen is not None`: a fresh RunState has wave_seen_at 0.0, and
        # an unreadable first frame logged a 378876-second stall (2026-09-04).
        rs.wave_stall_logged = True
        logger.event("wave_stalled", wave=rs.tracker.last, raw=raw,
                     seconds=int(now - rs.wave_seen_at),
                     shot=logger.shot(frame, "wave_stalled"))

    # ---- Second Wind odometer (edge detection on the floater)
    # ---- Second Wind window, read off the BADGE above the ability row.
    # The user's rule is "Demon Mode after the Second Wind immunity
    # disappears", and the badge says exactly when that is - so the end of the
    # window is OBSERVED, not timed. sw_immunity_sec is only a backstop for the
    # case where the badge somehow never clears; counting seconds would drift
    # against a window that is ~9.9s in practice, not the 8s it advertises.
    sw, sw_score = detect.second_wind_badge(frame)
    _ab = preset()["abilities"]
    # NO WALL, NO WALL READS (acct2 live test, 2026-08-19): wall_bar: null is
    # the documented "this account has no wall" state, but this rolling
    # reading ran unconditionally and threw CaptureError EVERY TICK on a
    # wall-less account - and the tick died before the rule interpreter ran.
    # Main (wall_bar set per-instance) takes the identical path as before.
    _has_wall = CONFIG["rois"].get("wall_bar") is not None
    # keep a rolling pre-proc wall reading current (see RunState.wall_last)
    if _has_wall and not rs.sw_floater_seen and raw is not None:
        if detect.wall_state(frame) == "normal":
            rs.wall_last = (now, detect.wall_fill(frame))
    # DEBOUNCED CLOSE. The badge is a small glyph with the battlefield drawn
    # over it, so a single frame can miss it; an undebounced close was logging
    # a 10s window as four windows in three seconds. Opening stays instant -
    # the proc is the thing being waited for and must never be reported late.
    if sw:
        rs.sw_miss = 0
    else:
        rs.sw_miss += 1
    closed = rs.sw_miss >= SW_CLOSE_FRAMES

    # A PROC NEEDS A BATTLE. Menu and post-run artwork out-scores the live
    # badge against the template (false proc at 0.996 on the tournament
    # screen vs 0.791 for the real wave-1370 one, 2026-08-16), and a false
    # proc arms the wall watch on a screen with no wall. Same
    # tower-on-screen proof as everything else: a readable wave counter.
    if sw and not rs.sw_floater_seen and raw is None:
        sw = False
    if sw and not rs.sw_floater_seen:                    # window OPENED
        rs.sw_proc_count += 1
        rs.sw_immune_until = now + (_ab.get("sw_immunity_sec") or 0)
        rs.post_sw_until = 0.0                # nothing to watch yet
        rs.nuke_fired_at, rs.dm_fired, rs.wall_prev = 0.0, False, None
        rs.dm_seen_at = None      # new run, new ability-row order
        logger.event("second_wind", phase="open", wave=rs.tracker.last,
                     count=rs.sw_proc_count, score=round(sw_score, 3),
                     wall=detect.wall_state(frame) if _has_wall else "absent",
                     shot=logger.shot(frame, f"sw_w{rs.tracker.last}"))
        rs.sw_floater_seen = True
        # NOTHING IS FIRED OR CANCELLED AT THE PROC (user, final ruling
        # 2026-08-15, third iteration): the sprint keeps earning waves until
        # death is actually imminent. The entire response lives in the
        # dm_below rescue below - when the wall drops under the threshold
        # AFTER the immunity, it cancels the sprint and fires Demon Mode
        # back-to-back, fast path. Cancelling here, at the proc, threw away
        # sprint waves on a wall that often rebuilds to full.
    elif rs.sw_floater_seen and closed:                   # window CLOSED
        rs.sw_immune_until = 0.0
        # post_sw_watch_sec: null means WATCH FOR THE REST OF THE RUN. A fixed
        # window is a farm-run idea and it is what lost the tournament at wave
        # 1120: the badge cleared, the 30s expired, and the wall crossed 5%
        # five seconds later with the watch already shut. The user's rule has
        # no clock in it - Demon Mode goes when the wall drops, however long
        # after the immunity that happens.
        watch = _ab.get("post_sw_watch_sec")
        rs.post_sw_until = float("inf") if watch is None else now + watch
        logger.event("second_wind", phase="closed", wave=rs.tracker.last,
                     count=rs.sw_proc_count, watch_sec=watch,
                     wall=round(detect.wall_fill(frame), 3) if _has_wall
                     else "absent")
        rs.sw_floater_seen = False
        # END THE INTRO SPRINT NOW, not during the rescue. Measured on the
        # wave-1110 run: the sprint was still running the whole way, Demon
        # Mode was therefore a no-op, and discovering that mid-rescue cost
        # 5.5s - tap, wait, fail the confirm, tap the indicator, wait, verify
        # the dialog, tap Yes, re-tap - out of a 5.6s margin between the wall
        # crossing 5% and the tower dying. It fired 1.8s before death.
        # Doing it here makes the rescue a single ~0.9s tap. The sprint's
        # value is spent by the first Second Wind anyway (wave 1060+), and
        # end_intro_sprint verifies every step, so a run with no sprint left
        # simply logs "indicator not found" and changes nothing.
        if _ab.get("end_sprint_after_sw") and not rs.sprint_ended:
            if end_intro_sprint(rs, "post_second_wind"):
                rs.sprint_ended = True

    ab = preset()["abilities"]

    # ---- proactive fleet Nuke (high-tier farm). Fleets are what a Nuke is
    # worth spending on, so it fires just after each fleet spawns instead of
    # being hoarded as a rescue. Deliberately NOT gated on Second Wind: this
    # is a schedule, not an emergency.
    #
    # Wave SKIPS mean mark+1 is frequently never displayed (the counter can
    # jump several waves at once), so the trigger is "first wave observed
    # inside [mark+after, mark+window]" rather than an equality test.
    fleet_cfg = ab.get("nuke_on_fleet")
    wave_now = rs.tracker.last
    if fleet_cfg and wave_now:
        after = fleet_cfg.get("after_waves", 1)
        window = fleet_cfg.get("window_waves", 60)
        # nuke_on_fleet.throttle_sec / .require_ready. PER SITE, not one flat
        # ability flag: the fleet Nuke has always gone through the readiness
        # test and the rescue burst has always bypassed it (see fire_button's
        # docstring on why that test is not trustworthy over a bright field).
        fleet_throttle = fleet_cfg.get("throttle_sec")
        fleet_throttle = (5.0 if fleet_throttle is None
                          else float(fleet_throttle))
        fleet_ready = bool(fleet_cfg.get("require_ready", True))
        for m in marks():
            if m in rs.nuked_marks:
                continue
            if wave_now > m + window:
                # already past it (e.g. the orchestrator started mid-run) - retire
                # the mark silently so it can never fire late
                rs.nuked_marks.add(m)
                continue
            if wave_now >= m + after and now >= rs.fleet_try_at:
                # THROTTLED: a confirmed fire costs ~1.1s (tap + settle +
                # re-read). Retrying every frame for a 60-wave window would
                # cripple the observe loop while the nuke is on cooldown.
                rs.fleet_try_at = now + fleet_throttle
                if fire_button(frame, "nuke", f"fleet_{m}",
                               require_ready=fleet_ready):
                    rs.nuked_marks.add(m)
                    logger.event("fleet_nuke", mark=m, wave=wave_now,
                                 shot=logger.shot(frame, f"fleet_nuke_w{wave_now}"))
            break                    # only the nearest pending mark matters

    # ---- rescue trigger: preset-defined bar + threshold.
    # normal_run: SW-gated Demon Mode on wall overheal (Nuke is on the fleet
    # schedule above). low_tier_farm: always watching, tower HP, refire-guarded.
    # NO RESCUE AT ALL when the blueprint compiled no rescue policy
    # (`rescue_bar: null`). Legacy presets always carry "wall", so nothing
    # below moves for them - but without this a rescue-less profile walks
    # into _fast_wall_watch and evaluates `ext < None`, which the main loop's
    # blanket handler turns into a `crash` event every five seconds forever.
    rescue_bar = ab.get("rescue_bar", "wall")
    hold = ab.get("hold_until_second_wind", True)
    # Held while the badge is UP (an ability spent inside the immunity is
    # wasted - nothing can hurt the tower), and again once the watch window
    # after it has elapsed. sw_immune_until is the backstop: if the badge were
    # ever to stick, it still expires and the watch opens.
    watching = ((rs.sw_proc_count > 0
                 and not rs.sw_floater_seen          # immunity has ended
                 and now >= rs.sw_immune_until
                 and now < rs.post_sw_until)
                if hold else True)
    # `raw is not None` is the tower-on-screen proof. detect.bar_fill measures
    # whatever is at the bar ROI without checking a bar is there: on the home
    # screen the wall ROI reads 0.073 and on the death screen 0.000, both under
    # every rescue threshold. One unreadable frame is not enough for the
    # no_wave>=2 'menu' bail above, so the rescue needs its own gate.
    if rescue_bar is not None and watching and raw is not None:
        # WALL PRESETS READ COLOR, NOT BRIGHTNESS (user, 2026-08-15): the
        # overheal is the PURPLE run from the bar's left edge, and "under
        # 20%" means the column at 20% of the width has turned teal. The
        # brightness fill this replaces could not tell overheal from base
        # health, and its trigger reading on the T19 bench turned out to be
        # the already-broken 'Rebuilding' banner - the drain fit entirely
        # inside one sampling gap. 'rebuilding' therefore triggers OUTRIGHT:
        # it is the one state a slow sample can never miss.
        # abilities.refire_guard_sec, defaulting to the 15s literal it
        # replaces. `is None`, not `or`: a compiled 0 means "no floor".
        _guard = ab.get("refire_guard_sec")
        refire_guard = 15 if _guard is None else _guard
        # THREE FIRE SITES, THREE EXPLICIT KEYS, each defaulting to what
        # that site does today: the rescue burst has ALWAYS tapped without the
        # readiness test (fire_button's docstring explains why the test lies
        # over a lit battlefield - exactly when a rescue is needed), while the
        # hp-path Nuke has always gone through it. The fleet Nuke's own key
        # lives on nuke_on_fleet, where the rest of its schedule is.
        burst_ready = bool(ab.get("burst_require_ready", False))
        hp_nuke_ready = bool(ab.get("hp_nuke_require_ready", True))

        def can_fire(name: str, fired_flag) -> bool:
            # Once per proc AND never twice inside refire_guard_sec. The badge
            # cycles far faster than the design assumed - measured at ~18s
            # between windows on a Tier 19 run - so "once per proc" alone let
            # a rescue retry every cycle, each attempt costing a tap plus a
            # confirm re-read while the ability was still on cooldown.
            if hold:
                return (not fired_flag
                        and now - rs.last_fire[name] > refire_guard)
            return now - rs.last_fire[name] > refire_guard   # refire guard

        if rescue_bar == "wall":
            # THE WALL IS NOT WATCHED FROM THIS LOOP. A full observe pass
            # costs 0.7-2s, and the T19 collapse fits inside one pass: three
            # straight benches read [full] then [broken], never a value in
            # between - the <20% crossing was unobservable by construction.
            # While the watch is open the orchestrator drops everything else and
            # samples ONLY the wall, ~3x per second, firing the combo inline.
            if can_fire("demon_mode", rs.dm_fired):
                _fast_wall_watch(rs, ab)
        else:
            fill = detect.hp_fill(frame)
            nuke_below = ab.get("nuke_below")
            if fill < ab["dm_below"]:
                if can_fire("demon_mode", rs.dm_fired):
                    logger.event("rescue", bar="hp", fill=round(fill, 3),
                                 wave=rs.tracker.last,
                                 sw_count=rs.sw_proc_count,
                                 shot=logger.shot(frame,
                                                  f"rescue_w{rs.tracker.last}"))
                    rs.last_fire["demon_mode"] = now
                    if fire_button(frame, "demon_mode", f"rescue_{fill:.3f}",
                                   require_ready=burst_ready):
                        rs.dm_fired = True
                        rs.last_fire["demon_mode"] = now
            if nuke_below is not None and fill < nuke_below:
                if can_fire("nuke", rs.nuke_fired_at) and \
                        fire_button(frame, "nuke", f"rescue_{fill:.3f}",
                                    require_ready=hp_nuke_ready):
                    rs.nuke_fired_at = now
                    rs.last_fire["nuke"] = now
    # not watching: abilities are HELD (never fired pre-SW)

    # ---- gem CLAIM with human delay (3-10 s)
    # gather.flying_gem: a blueprint may switch gem collection off. Absent
    # (every legacy preset) = True. When off, `gem` is never truthy, so the
    # entire claim block below - detection, delay, stale tap - stays inert.
    gem = (detect.floating_gem(frame)
           if preset().get("gather", {}).get("flying_gem", True) else None)
    if gem and rs.gem_due is None:
        delay = random.uniform(*preset()["gem_delay_sec"])
        rs.gem_due = (now + delay, gem)
        logger.event("gem_seen", wave=rs.tracker.last, delay=round(delay, 1))
    if rs.gem_due and now >= rs.gem_due[0]:
        # fire ONLY on a fresh detection - the orbiting gem moves, so
        # a remembered position is stale; grace-extend up to 10s
        if gem:
            try:
                ev = act.tap(*gem, reason="gem_claim")
                logger.event("gem", wave=rs.tracker.last, **ev)
            except act.TapRefused as e:
                logger.event("tap_refused", button="gem", error=str(e))
            rs.gem_due = None
        elif now - rs.gem_due[0] > 10:
            # Last resort before giving up: tap where it was last seen. A
            # settled CLAIM box does not move, and a stale tap on empty field
            # is harmless - EXCEPT over the ability row, where it would fire
            # Nuke or Demon Mode, so that area is refused outright.
            pt = rs.gem_due[1]
            if not _in_ability_row(pt):
                try:
                    ev = act.tap(*pt, reason="gem_claim_stale")
                    logger.event("gem_stale_try", wave=rs.tracker.last, **ev)
                except act.TapRefused as e:
                    logger.event("tap_refused", button="gem", error=str(e))
            else:
                logger.event("gem_stale_skipped", wave=rs.tracker.last,
                             reason="over_ability_row", x=pt[0], y=pt[1])
            logger.event("gem_lost", wave=rs.tracker.last)
            rs.gem_due = None
    return False


def is_compiled_preset() -> bool:
    """Is the bound preset one playerprofile.py compiled?

    THE MARKER IS `_source`, agreed with the compiler: compile_preset() stamps
    every output with {"profile": <name>, "blueprint": <name>} and nothing else
    in config.yaml carries the key. The `bp_` name prefix is kept as a second
    signal because materialize() installs under exactly that prefix and the
    startup attestation already keys off it - either one alone is enough, so a
    preset cannot slip past gating by losing one of them.
    """
    return (str(CONFIG.get("preset") or "").startswith("bp_")
            or isinstance(preset().get("_source"), dict))


def _gate_preset() -> bool:
    """SPAWN-TIME CAPABILITY GATE for a compiled preset. True = may run.

    "May THIS account run this preset" is the profile layer's question, not
    orchestrator.py's: it owns `player.abilities_verified`, the owned-UW list and the
    card presets that exist, and a second copy of that judgement here would
    drift from the one the compiler enforces. The helper is resolved by name
    and handed the whole compiled preset.

    IT GATES THE WHOLE PRESET, NOT THE RULES (Codex P4 #1). The first version
    ran only when `rules` was non-empty - and all three ability-using golden
    blueprints compile to `rules: []`, because their rescue lives in Tier A's
    `abilities{}`. So the exact case the gate exists for - a Demon Mode burst
    tapping a FIXED COORDINATE on an account that may not own Demon Mode -
    was the case that bypassed it. required_capabilities() reads `abilities{}`
    and `rules[].requires` together, so the whole preset is the unit.

    IT FAILS CLOSED (Codex P4 #2). For a compiled preset, an unavailable gate
    is a REFUSAL: no import, no helper, or a helper that raises all stop the
    process before the first capture. "Probably fine" is not a thing this can
    say about a fixed-coordinate ability tap. Legacy presets never reach here.
    """
    name = CONFIG.get("preset")

    def refuse(event: str, msg: str, **kw) -> bool:
        logger.event(event, preset=name, **kw)
        print(f"REFUSED: {name} is a compiled preset and {msg}")
        return False

    try:
        from player import playerprofile
    except Exception as e:                              # noqa: BLE001
        return refuse("rule_gate_unavailable",
                      f"the profile layer will not import ({e}) - refusing to "
                      f"run ungated", error=str(e)[:300])
    try:
        # getattr's default only swallows AttributeError - a module whose
        # __getattr__ raises anything else (a broken lazy import, say) would
        # otherwise take the process down HERE, which is a crash rather than a
        # refusal and reads very differently in a log at 4am.
        fn = next((getattr(playerprofile, n) for n in RULE_GATE_HELPERS
                   if callable(getattr(playerprofile, n, None))), None)
    except Exception as e:                              # noqa: BLE001
        return refuse("rule_gate_unavailable",
                      f"the profile layer is unreadable ({e}) - refusing to "
                      f"run ungated", error=str(e)[:300])
    if fn is None:
        return refuse("rule_gate_unavailable",
                      "playerprofile.py exposes no capability gate - refusing "
                      "to run ungated", tried=list(RULE_GATE_HELPERS))
    try:
        verdict = fn(preset())
    except Exception as e:                              # noqa: BLE001
        return refuse("rule_gate_refused",
                      f"the capability gate raised: {e}", error=str(e)[:300])
    problems = ([verdict] if isinstance(verdict, str)
                else list(verdict) if isinstance(verdict, (list, tuple))
                else [] if verdict is not False else ["gate returned False"])
    if problems:
        return refuse("rule_gate_refused",
                      "this account cannot run it: "
                      + "; ".join(str(p) for p in problems),
                      problems=[str(p)[:200] for p in problems])
    logger.event("rule_gate_ok", preset=name,
                 rules=len(preset().get("rules") or []),
                 helper=getattr(fn, "__name__", "?"))
    return True


def main():
    rs = RunState()
    period = 1.0 / CONFIG["loop"]["fps"]
    logger.event("start", instance=CONFIG["active_instance"],
                 preset=CONFIG["preset"], dry_run=CONFIG["loop"]["dry_run"])

    # ---- STARTUP ATTESTATION. A compiled blueprint is invisible in the log
    # otherwise: bp_coin_default from yesterday's YAML and from today's read
    # exactly the same.
    # GATED ON THE RUNNING PRESET, not on a profile being loaded: selecting
    # legacy `normal_run` while a profile happens to be bound would otherwise
    # attest a compiled config this process is not running. It also keeps the
    # legacy path free of the import entirely. Never fatal - a farm must not
    # fail to start over a log line.
    if CONFIG["preset"].startswith("bp_"):
        try:
            from player import playerprofile
            logger.event("compiled_config", preset=CONFIG["preset"],
                         profile=CONFIG.get("active_profile"),
                         hash=playerprofile.compiled_hash(preset()))
        except Exception as e:                          # noqa: BLE001
            logger.event("compiled_config_error", error=str(e)[:200])

    # ---- CAPABILITY GATING, at the preset-resolution boundary and nowhere
    # else: before the first capture, before tournament setup, before any tap.
    # EVERY compiled preset, rules or none - the Tier A rescue in `abilities{}`
    # taps abilities too, and gating only rule-carrying presets left exactly
    # those unguarded. A legacy preset carries no `_source` and no `bp_` name,
    # so it never reaches the helper.
    if is_compiled_preset() and not _gate_preset():
        return

    # ---- one-shot pre-battle setup (Tournament preset only). It runs BEFORE
    # the observe loop and only on process start: after a death the loop's own
    # RETRY re-enters the same tournament, and re-running the setup would
    # re-equip everything mid-event.
    if preset().get("tournament_setup"):
        from interactions import tourney
        # ADOPTION (2026-08-29, user: "I am running a tournament - run my
        # safety nets"): a live battle at process start means the human
        # already set up and entered - setup would only abort at its
        # ensure_home guard (it must never walk out of a tournament).
        # Skip straight to the observe loop and guard the run that exists.
        f0 = capture.grab()
        if wave_reader.read_wave(f0) is not None or tourney._in_battle(f0):
            logger.event("tourney_setup_skipped",
                         reason="live battle adopted",
                         wave=wave_reader.read_wave(f0))
        else:
            try:
                tourney.setup()
            except (tourney.Abort, act.TapRefused) as e:
                logger.event("tourney_setup_failed", error=str(e))
                print(f"tournament setup aborted: {e}")
                return

    # ---- resource telemetry
    proc = psutil.Process()
    proc.cpu_percent()                       # prime counter
    bench = {"cap": [], "loop": [], "last_emit": time.monotonic()}
    BENCH_EVERY = CONFIG["logging"].get("bench_interval_sec", 30)

    while True:
        t0 = time.monotonic()
        try:
            frame = capture.grab()
            bench["cap"].append(time.monotonic() - t0)
            now = time.monotonic()

            # ---- constant monitoring on every frame: death / battle-presence
            # / SW / rescue / gems. Shopping only ever advances AFTER this.
            status = watch_frame(rs, frame)
            if status == "dead":
                logger.event("death", wave=rs.tracker.last,
                             shot=logger.shot(frame, f"death_w{rs.tracker.last}"))
                # ---- per-run stats+perks log (blocking is fine: tower dead)
                if detect._match(frame, "icons/game_stats.png", 0.75)[0]:
                    runlog.collect(CONFIG["active_instance"])
                    frame = capture.grab()
                # ---- Tier B DEATH-PHASE rules (latency: death_handler).
                # Here and nowhere else: the observe loop has already handed
                # over, the run log is collected, and the restart has not
                # started - so a card swap for the next run or a stop request
                # lands at the boundary instead of interrupting anything. A
                # rule that navigated leaves this frame stale.
                if run_death_rules(rs, frame):
                    frame = capture.grab()
                # THE ONE FREE MOMENT TO STOP. Combo mode switches phases "when
                # the next run is over" - here the tower is already dead and
                # the run log is already collected, so leaving costs nothing.
                # Anywhere else would abandon a live run mid-wave.
                why = runflag.requested()
                if why:
                    logger.event("stop_after_run", wave=rs.tracker.last,
                                 reason=why)
                    return
                if not (preset().get("restart_via_home")
                        and restart_from_home(frame,
                                              preset().get("tier"))):
                    # restart_from_home spent SECONDS tapping (HOME, chores,
                    # quest scan) - `frame` is history. The RETRY button must
                    # be located on the screen as it is NOW, or the tap lands
                    # on whatever screen those seconds left behind (2026-08-25:
                    # a stale retry center was tapped into the CARDS screen
                    # and unequipped a card from the player's active preset).
                    fresh = capture.grab()
                    _, retry_center = detect.death_screen(fresh)
                    if retry_center:
                        try:
                            act.tap(*retry_center, reason="auto_retry")
                            logger.event("retry")
                        except act.TapRefused as e:
                            logger.event("tap_refused", button="retry",
                                         error=str(e))
                    else:
                        logger.event(
                            "retry_skipped",
                            reason="death dialog no longer on screen after "
                                   "failed home restart - holding, the "
                                   "observe loop owns recovery",
                            shot=logger.shot(fresh, "retry_skipped"))
                rs = RunState()
                time.sleep(5)
                continue
            if status == "menu":
                if rs.mission.active:
                    rs.bot_left_battle = True
                    # deliberate reward-collection navigation owns the screen;
                    # pace flow actions 0.5-1s apart (human-like)
                    rs.mission.step(frame)
                    time.sleep(random.uniform(0.15, 0.55))
                    continue
                # tower not on screen: kill any sweep, touch NOTHING
                rs.shop.abort()
                # NAME the screen rather than just knowing "not battle" - the
                # name is what makes the recovery below safe, and it turns an
                # off_battle log line into something diagnosable.
                sc = screen.identify(frame)
                if sc.name == "tournament_stats":
                    # THE TOURNAMENT'S DEATH SCREEN. A tournament run does not
                    # end on GAME STATS/RETRY - it ends on this dialog, which
                    # the death detector does not know, so the first handled
                    # tournament (2026-08-15, wave 1010, rank 2) left the
                    # orchestrator safely wedged with the result sitting on screen.
                    # Record it, dismiss with OK (fixed dialog geometry, same
                    # as the GAME STATS buttons), and EXIT: a tournament orchestrator
                    # has nothing to restart into - "do 1 run then go back"
                    # means the supervisor takes over from the runner exiting.
                    logger.event("tournament_over", wave=rs.tracker.last,
                                 shot=logger.shot(frame, "tournament_over"))
                    for _ in range(2):
                        try:
                            act.tap(538, 1793, reason="tournament stats OK")
                        except act.TapRefused as e:
                            logger.event("tap_refused", button="tournament_ok",
                                         error=str(e))
                            break
                        time.sleep(1.2)
                        if screen.identify(capture.grab()).name != "tournament_stats":
                            break
                    return
                if not rs.menu_logged:
                    logger.event("off_battle", screen=sc.name,
                                 score=round(sc.score, 3),
                                 shot=logger.shot(frame, f"off_battle_{sc.name}"))
                    rs.menu_logged = True
                    rs.off_battle_since = now
                # ---- STUCK RECOVERY, and the rule it has to obey: when the
                # tower is not on screen, the autopilot clicks NOTHING. The
                # single exception is a mess THIS PROCESS made - a reward flow
                # that ended with a listing still up parks the game on that
                # popup forever while the run ticks away untended.
                #
                # So recovery is gated on rs.bot_left_battle: it only fires
                # when a flow of ours navigated off the battle screen. If the
                # HUMAN opened a menu, the flag is false and nothing is
                # touched. Without that gate this fired while the user was on
                # the CARDS screen and switched their active card preset.
                # ...and only on the two screens a stranded flow can leave
                # us on. On anything else - CARDS, MODULES, GUILD, a screen we
                # cannot even name - it does nothing at all.
                if (rs.bot_left_battle
                        and sc.name in screen.RECOVERABLE
                        and now - rs.off_battle_since > 45
                        and now - rs.recover_try > 10):
                    rs.recover_try = now
                    pt = missions.find_skip(frame)
                    # act.tap RAISES TapRefused when adb fails, and these two
                    # calls were the only unguarded ones in the loop: a single
                    # `adb input` returning exit 1 (it does, transiently) took
                    # the whole autopilot down mid-run. A failed recovery tap
                    # is the least important event in the system - log it and
                    # carry on.
                    try:
                        if pt:
                            act.tap(*pt, reason="recover_skip", instant=True)
                            logger.event("stuck_recover", action="skip",
                                         screen=sc.name, x=pt[0], y=pt[1])
                        else:
                            act.tap(*missions.RETURN_STRIP,
                                    reason="recover_return", instant=True)
                            logger.event("stuck_recover", action="return_strip",
                                         screen=sc.name,
                                         shot=logger.shot(frame, "stuck_recover"))
                    except act.TapRefused as e:
                        logger.event("stuck_recover", action="refused",
                                     screen=sc.name, error=str(e))
                time.sleep(period)
                continue
            rs.menu_logged = False
            rs.bot_left_battle = False   # tower on screen: slate clean
            wave = rs.tracker.last
            if wave is None:
                time.sleep(period)
                continue

            # ---- UNSEEN RUN BOUNDARY (2026-09-01, user: "why is nuke not
            # triggered?"). The death handler is not the only way a run ends:
            # the user tapped RETRY themselves while the loop was between
            # frames, so it only ever logged off_battle - and the old
            # RunState, with every fleet mark already retired in
            # rs.nuked_marks, guarded the new run for three hours and
            # silently skipped its wave-3495 fleet nuke. Waves never move
            # backwards inside a run, and the tracker accepts a backward move
            # only after `confirm` consistent frames, so a confirmed drop IS
            # a new run: renew exactly what the death handler renews. The
            # 100-wave margin leaves small tracker resync corrections alone.
            if rs.wave_hwm is not None and wave < rs.wave_hwm - 100:
                logger.event("run_boundary", reason="wave_went_backwards",
                             prev=rs.wave_hwm, wave=wave,
                             shot=logger.shot(frame, f"run_boundary_w{wave}"))
                rs = RunState()
                time.sleep(period)
                continue        # next pass reads the new run from scratch
            rs.wave_hwm = wave if rs.wave_hwm is None else max(rs.wave_hwm,
                                                               wave)

            # ---- Tier B profile rules, once per pass, AFTER watch_frame has
            # run the compiled (Tier A) ability logic for this frame.
            eval_rules(rs, frame, wave)

            # ---- P6 compiled knobs. All three are absent from every legacy
            # preset, so this whole block is three dict misses on the farm.
            apply_cancel_sprint(rs)
            if max_wave_reached(rs, frame, wave):
                # THE RUN IS OVER AND THE NEXT ONE HAS ALREADY STARTED.
                # shard.abandon_run does not stop at the stats dialog - it
                # taps RETRY itself and returns - so this is a run BOUNDARY,
                # exactly like the death handler's, and it renews exactly what
                # the death handler renews. Carrying the old RunState across
                # it capped only the first run of the session: max_wave_done
                # stayed True, and sprint_ended / the rescue and rule ledgers
                # described a tower that no longer exists.
                rs = RunState()
                time.sleep(5)       # the same post-RETRY settle as the death
                continue            # path: the new run is still fading in

            # Always False today - it refuses and says so once per run (see
            # the function). The call stays so that a profile carrying the key
            # reports itself in the events log instead of going quiet.
            if run_in_run_actions(rs, frame, wave):
                time.sleep(period)
                continue        # the cards screen moved: judge a fresh frame

            # ---- side menu upkeep + reward collection (least-priority flows)
            if rs.mission.active:
                rs.mission.step(frame)
                if not rs.mission.active and rs.await_guild_result:
                    rs.await_guild_result = False
                    # only a flow that actually CLAIMED something changes the
                    # balance - only then is a store visit worth anything
                    rs.pending_store = missions.last_guild_claims > 0
                time.sleep(random.uniform(0.15, 0.55))   # 0.5-1s step spacing
                continue                     # nothing else acts mid-flow
            # hands_off gathering (2026-09-05): when the blueprint wants
            # nothing that lives in the side menu, the menu is never opened -
            # on a human's run that tap is theirs to make, not ours.
            _g = preset().get("gather", {})
            _want_menu = any(_g.get(k, True) for k in
                             ("quests_8h", "quest_rewards", "guild", "ad_gems"))
            _menu_open = detect.side_menu_open(frame)
            if not _menu_open and not _want_menu:
                rs.menu_open_frames = 0
            elif not _menu_open:
                # keep the side menu open during runs (rewards live there).
                # VERIFIED tap only: the collapsed hamburger box must be seen
                # on 2 consecutive frames (menu slide animation shows both
                # states at once) and the tap goes to its MATCHED location -
                # a blind coordinate tap once opened the guild store instead.
                rs.menu_open_frames = 0
                # buttons/menu_closed_tile.png is the HAMBURGER tile shown when
                # the menu is shut. The old menu_collapsed.png was actually a
                # picture of the green X - the button shown when the menu is
                # already OPEN - so it never matched, the menu was never
                # opened, and every reward flow silently stopped running.
                hit, _, loc = detect._match(frame, "buttons/menu_closed_tile.png",
                                            0.75)
                rs.menu_closed_frames = rs.menu_closed_frames + 1 if hit else 0
                if hit and rs.menu_closed_frames >= 2 \
                        and now - rs.menu_open_try > 8:
                    tpl = detect._tpl("buttons/menu_closed_tile.png")
                    try:
                        ev = act.tap(loc[0] + tpl.shape[1] // 2,
                                     loc[1] + tpl.shape[0] // 2,
                                     reason="side_menu_open")
                        logger.event("side_menu", **ev)
                    except act.TapRefused as e:
                        logger.event("tap_refused", button="side_menu",
                                     error=str(e))
                    rs.menu_open_try = now
            else:
                rs.menu_closed_frames = 0
                rs.menu_open_frames += 1
                if rs.menu_open_frames < 2:
                    # menu may still be sliding in (EXIT BATTLE renders
                    # early) - let it settle before any flow taps tiles
                    badge = None
                else:
                    # gather gates. ONE visit claims the 8h quests and
                    # their rewards together, so the quest flow runs while
                    # EITHER is wanted; both absent = legacy True.
                    _g = preset().get("gather", {})
                    _want_q = (_g.get("quests_8h", True)
                               or _g.get("quest_rewards", True))
                    badge = ("quests"
                             if _want_q and missions.quests_badge(frame) else
                             "guild"
                             if _g.get("guild", True)
                             and missions.guild_badge(frame) else None)
                if badge is None:
                    rs.quest_due = None
                elif rs.quest_due is None or rs.quest_due[0] != badge:
                    # human-like: wander over within the next 1-10 minutes.
                    # If we JUST visited and the badge persists (nothing was
                    # claimable), back off instead of ping-ponging.
                    recent = now - rs.last_visit.get(badge, -1e9) < 1200
                    lo, hi = CONFIG["missions"]["visit_delay_sec"]
                    due = now + (random.uniform(1500, 2400) if recent
                                 else random.uniform(lo, hi))
                    rs.quest_due = (badge, due)
                    logger.event("quest_seen", kind_of=badge, wave=wave,
                                 due_in=round(due - now))
                elif now >= rs.quest_due[1] and not rs.shop.active:
                    rs.mission.start(missions.quest_flow
                                     if badge == "quests"
                                     else missions.guild_flow)
                    rs.mission.step(frame)
                    rs.last_visit[badge] = now
                    rs.quest_due = None
                    if badge == "guild":
                        rs.await_guild_result = True
                # ---- guild store: ONLY after a guild claim landed (the
                # balance can't change otherwise - no point checking)
                if not rs.mission.active and not rs.shop.active \
                        and rs.menu_open_frames >= 2 and rs.pending_store:
                    rs.pending_store = False
                    rs.mission.start(store.store_flow)
                    rs.mission.step(frame)
                # ---- daily free gems (premium store), around 4-5 AM
                if not rs.mission.active and not rs.shop.active \
                        and rs.menu_open_frames >= 2 \
                        and preset().get("gather", {}).get("ad_gems", True) \
                        and free_gems_due() \
                        and now >= rs.free_gems_try_at:
                    # the claim is counted by the flow ITSELF, and only once
                    # the button is really tapped. v29: the Ad Gem respawns
                    # through the day (60/UTC-day cap enforced in
                    # free_gems_due) - the jittered pace averages ~17 min
                    # between visits so no two days tick on the same clock,
                    # and a failing flow still cannot spin.
                    rs.free_gems_try_at = now + random.uniform(600, 1500)
                    rs.mission.start(lambda: missions.free_gems_flow(
                        on_success=free_gems_mark_claimed))
                    rs.mission.step(frame)

            # ---- CL normalization: first thing each run, force the preset's
            # baseline state (OFF for high-tier farm, ON for low_tier_farm) -
            # the previous run may have died in the other state
            # (deferred while a sweep is mid-pass - both drive the tab strip)
            cl_cfg = preset()["chain_lightning"]
            cl_base = bool(cl_cfg.get("always_on"))
            uw_ok = time.monotonic() >= rs.uw_next_try

            def _uw_backoff(success: bool):
                if success:
                    rs.uw_fails = 0
                    return
                rs.uw_fails += 1
                rs.uw_next_try = time.monotonic() + min(120, 10 * 2 ** (rs.uw_fails - 1))

            # Normalize to the state we actually WANT right now, not the
            # preset baseline: restarting above the latch wave used to force
            # CL off and then immediately back on - two UW panel visits, and
            # CL sat off in between.
            want_cl = (True if cl_base else
                       (cl_window(rs, wave) if wave else rs.cl_on))
            if cl_cfg.get("enabled", True) is False:
                # CL COMPILED OUT (`enabled: false`, 2026-09-05): cl_window
                # already answers False, but this normalization still opened
                # the UW panel to force CL off - on a blueprint that rides
                # along on a human's run (tourney_assist) that is a toggle
                # on THEIR weapon. Never open the panel for a disabled CL.
                if not rs.cl_normalized:
                    rs.cl_normalized = True
                    rs.cl_on = want_cl
                    logger.event("cl_disabled", wave=wave)
            elif not rs.cl_normalized and not rs.shop.active and uw_ok:
                if shopper.uw_toggle("chain_lightning", want_cl):
                    rs.cl_normalized = True
                    rs.cl_on = want_cl
                    logger.event("cl_normalized", wave=wave, state=want_cl)
                    _uw_backoff(True)
                elif rs.uw_fails >= 3:
                    # verification keeps failing - accept the tapped state and
                    # move on rather than dragging the UW panel all run
                    rs.cl_normalized = True
                    rs.cl_on = want_cl
                    logger.event("cl_normalize_giveup", wave=wave,
                                 assumed=want_cl)
                else:
                    logger.event("cl_normalize_fail", wave=wave,
                                 want=want_cl, fails=rs.uw_fails + 1)
                    _uw_backoff(False)

            # ---- preset uw_wanted: enforce the full toggle set once per run,
            # after CL settles. Quest presets (ILM/SM) flip toggles and the
            # next farm run inherits them - a night ran without DW+PS before
            # this existed (2026-08-17).
            if (rs.cl_normalized and not rs.uw_normalized
                    and not rs.shop.active and uw_ok):
                wanted = preset().get("uw_wanted") or {}
                for w, want in wanted.items():
                    if w in rs.uw_done:
                        continue
                    if shopper.uw_toggle(w, bool(want)):
                        rs.uw_done.add(w)
                        _uw_backoff(True)
                    elif rs.uw_fails >= 3:
                        rs.uw_done.add(w)   # same give-up rule as CL: never
                        logger.event("uw_normalize_giveup", weapon=w,
                                     assumed=bool(want))   # drag the panel all run
                        _uw_backoff(True)
                    else:
                        logger.event("uw_normalize_fail", weapon=w, wave=wave,
                                     fails=rs.uw_fails + 1)
                        _uw_backoff(False)
                        break               # retry after the backoff window
                if all(w in rs.uw_done for w in wanted):
                    rs.uw_normalized = True
                    if wanted:
                        logger.event("uw_normalized", wave=wave,
                                     states={w: bool(v) for w, v in wanted.items()})

            # ---- Chain Lightning: always-on preset, latch wave, or fleet marks
            if want_cl != rs.cl_on and rs.cl_normalized:
                if rs.shop.active or not uw_ok:
                    if not rs.cl_blocked_logged:
                        rs.cl_blocked_logged = True
                        logger.event("cl_deferred", wave=wave, want=want_cl,
                                     shopping=rs.shop.active, uw_ok=uw_ok)
                elif shopper.uw_toggle("chain_lightning", want_cl):
                    rs.cl_on = want_cl
                    rs.cl_blocked_logged = False
                    logger.event("cl", on=want_cl, wave=wave)
                    _uw_backoff(True)
                else:
                    logger.event("cl_toggle_fail", wave=wave, want=want_cl,
                                 fails=rs.uw_fails + 1)
                    _uw_backoff(False)   # retried after the backoff window
            elif want_cl == rs.cl_on:
                rs.cl_blocked_logged = False

            # ---- shopping: sprint LOCKS abilities, not the panel. The sprint
            # spans ~1800 waves - shop aggressively while it's active (waves
            # fly, skips are cheap), throttle once it ends.
            sprint = detect.intro_sprint_active(frame)
            if sprint != rs.sprint_prev and rs.sprint_prev is not None:
                logger.event("sprint", active=sprint, wave=wave)
            rs.sprint_prev = sprint
            interval = 20 if sprint else preset()["shop_interval_sec"]
            if rs.shop.active:
                rs.shop.step(frame)         # ONE small action per frame
                if not rs.shop.active:
                    rs.last_shop = time.monotonic()   # sweep just finished
                    if rs.shop.finished and not rs.shop_done_logged:
                        logger.event("shopping_complete", wave=wave,
                                     maxed=sorted(rs.shop.maxed))
                        rs.shop_done_logged = True
            elif now - rs.last_shop > interval and not rs.shop.finished:
                rs.shop.start()
                rs.shop.step(frame)

            # ---- science: burst shots in fleet danger windows
            f = CONFIG["fleet"]
            if any(m <= wave <= m + f["post_window"] for m in marks()):
                logger.shot(frame, f"danger_w{wave}")

        except capture.CaptureError as e:
            logger.event("capture_error", error=str(e))
            time.sleep(2)
        except Exception:
            logger.event("crash", trace=traceback.format_exc())
            time.sleep(5)

        bench["loop"].append(time.monotonic() - t0)
        if time.monotonic() - bench["last_emit"] > BENCH_EVERY and bench["loop"]:
            mem = proc.memory_info()
            logger.event(
                "bench",
                cpu_percent=proc.cpu_percent(),
                rss_mb=round(mem.rss / 1e6, 1),
                capture_ms=round(1000 * sum(bench["cap"]) / max(1, len(bench["cap"])), 1),
                loop_ms=round(1000 * sum(bench["loop"]) / len(bench["loop"]), 1),
                effective_fps=round(len(bench["loop"]) /
                                    max(1e-9, sum(bench["loop"])), 2),
                loops=len(bench["loop"]),
            )
            bench["cap"].clear()
            bench["loop"].clear()
            bench["last_emit"] = time.monotonic()

        time.sleep(max(0.0, period - (time.monotonic() - t0)))


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Tower autopilot orchestrator "
                                             "(ONE emulator instance).")
    ap.add_argument("--instance", help="instance key from config.yaml "
                                       "(default: active_instance)")
    ap.add_argument("--preset", help="preset key; overrides the instance's own")
    return ap.parse_args()


if __name__ == "__main__":
    _args = _cli()
    # bind BEFORE anything reads CONFIG-derived state (logger names its file
    # after the instance, capture resolves the display, act reads allow_taps)
    settings.select_instance(_args.instance or CONFIG["active_instance"],
                             _args.preset)
    main()
