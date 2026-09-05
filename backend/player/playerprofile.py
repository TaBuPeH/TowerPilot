"""Player profile: load, validate and COMPILE `profiles/<name>.yaml` into the
flat preset dicts every other module already knows how to read.

Named `playerprofile`, not `profile`, because `profile` IS A STDLIB MODULE (the
deterministic profiler). A local `profile.py` shadows it, `import profile`
still succeeds, and the failure surfaces somewhere else entirely: cProfile does
`import profile as _pyprofile` and dies with AttributeError on line 24. Costing
someone an afternoon to a name collision is not worth six saved characters.

WHY this module exists
----------------------
`config.yaml` is the MACHINE file - adb ports, ROIs, tab strips, loadouts.
Everything in it is true of the emulator, not of the player. The presets block,
though, had quietly become the PLAYER file: which ultimate weapons to enforce,
when Chain Lightning may be on, what the rescue does, which tier to farm. That
is per-account knowledge, it is written in a language (`dm_below`, `nuke_on_fleet`)
that only makes sense if you already know the orchestrator's internals, and none of it
is checkable - a preset naming a card layout the account does not own fails
silently, mid-run, after the loadout step has already burned a run.

A profile says the same things in the player's vocabulary ("after a Second Wind,
if the wall falls, burst") and this module is the ONE place that translates. Two
consequences follow, and they are the whole point:

  * VALIDATION HAPPENS ONCE, AT STARTUP, AGAINST `player.*`. Every capability a
    blueprint leans on - UWs, cards, guardians, modules, the wall bar, the tier
    cap - is checked against what the scanner actually saw on the account. A
    profile that asks for something the player does not have is refused at
    launch with a message naming the path, instead of at wave 1 with a tap into
    a button that isn't there.
  * THE COMPILED OUTPUT IS EXACTLY TODAY'S PRESET SHAPE. `compile_preset()`
    returns the post-merge shape of `orchestrator.preset()` (flat, no `base:` key), so
    orchestrator/shopper/act read it without knowing profiles exist. Profiles are a
    front end, not a rewrite.

Layering rule: this module imports `settings` and NOTHING else from the
autopilot. It must stay loadable by the dashboard, the tray and the scanner,
none of which can afford to drag in capture/act/orchestrator (and their opencv/adb
weight) just to list what a profile contains. It performs no IO other than
reading the profile YAML - it never writes, never taps, never touches the net.

Execution tiers (see profiles/SCHEMA.md "RULES")
-----------------------------------------------
A rescue policy is a list of when/do rules, but they cannot all run at the same
speed. The wall can go from full to dead in about two seconds, so the rules that
guard it are compiled DOWN into flat scalars (`dm_below`, `falling_samples`,
`deadband`...) that the greedy wall watch hoists to locals once at entry - no
dict walking, no rule interpretation, per sample. That is Tier A. Everything
else becomes `preset["rules"]`, evaluated once per ~1s main-loop pass by a small
evaluator. Tier B rules cost a dict walk each; at 1 Hz nobody cares.

Tier B rules are NORMALIZED at compile time (P4), not handed over raw: every
compiled rule carries `{id, when, do, repeat, refire_sec, latency, requires}`,
each of `when`/`do` a mapping with an explicit `kind` and every parameter
present as a number or a bool. The runtime therefore never parses a string,
never dispatches on key presence (which is how `{stop_after_run: false}` would
stop a run), and never has to know a default. See profiles/SCHEMA.md, "RULES",
for the field-by-field contract - it is what the main-loop interpreter reads.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import yaml

from settings import CONFIG, ROOT

# Profiles live next to the schema that describes them. Kept as a module global
# rather than inlined so tests can point it at a tmp_path without writing into
# the user's real profiles directory.
PROFILES_DIR = ROOT / "profiles"

# One profile per PROCESS, same rule as settings.select_instance(): the compiled
# presets are installed into the shared CONFIG dict, so a second profile would
# silently overwrite the first for every module that already holds a reference.
PROFILE: dict | None = None


class ProfileError(Exception):
    """A profile could not be loaded, is invalid, or asks for a capability the
    player does not have. Always carries a message a human can act on."""


# ---------------------------------------------------------------- vocabulary
#
# These tables ARE the schema's "known names" clause. Anything not listed here
# is a typo as far as the compiler is concerned, and a typo in a rescue rule is
# a rescue that silently never fires - the exact failure mode profiles exist to
# kill. Hence: unknown name -> refusal, never a shrug.

# The kinds this compiler knows deeply (per-kind semantic validation and
# compile branches below). Extension kinds come from the FLOW REGISTRY
# (flows/*.py): a new flow file makes its kind legal here and compilable on
# the generic path, without touching this module.
_BUILTIN_KINDS = ("coin", "shard", "tournament", "uw_grant_quest",
                  "cycle_quest")
import flows as _flows_registry  # noqa: E402 - ast-based, imports no runner

KINDS = _BUILTIN_KINDS + tuple(k for k in _flows_registry.kinds()
                               if k not in _BUILTIN_KINDS)

# The one `loadout` value that is not a config.yaml key: "run whatever is
# already equipped, change nothing".
#
# It exists because `loadout` is REQUIRED and every loadout in config.yaml
# names cards, guardians or modules - so a fresh or sandbox account (no card
# presets, one module) cannot name any of them without a profile that lies
# about what it owns. Absent cannot mean this: an omitted key is a forgotten
# key, which is why it is refused. A word has to be written down.
#
# COIN ONLY, because coin is the one kind whose runner equips nothing on its
# own: orchestrator.py never calls loadout.apply() - the scheduler does, before it
# hands over. Every other kind's runner applies something (tourney.py's three
# pre-battle swaps, quest_sm/quest_ilm's `_preset().get("loadout") or <own
# default>`, which would silently substitute a real build rather than skip),
# so `as_is` there would not mean what it says.
LOADOUT_AS_IS = "as_is"

UW_NAMES = ("chain_lightning", "death_wave", "golden_tower", "poison_swamp",
            "black_hole", "spotlight", "smart_missiles", "inner_land_mines",
            "chronofield")

BAR_NAMES = ("wall", "hp")

# Dissonance event (2026-08-31): the tab a dissonant run disables at entry.
# Only tiles with harvested templates can actually be selected - an
# unharvested one fails closed via TemplateMissing at run start.
DISSONANT_TABS = ("attack", "defense", "utility", "ultimate_weapons")

# Buttons a `fire` action may name. Deliberately short: these are the two
# abilities the orchestrator owns taps for.
BUTTONS = ("nuke", "demon_mode")

CL_MODES = ("always_on", "fleet_marks", "off_until_wave", "off")

SHOP_TABS = ("attack", "defense", "utility")
SHOP_MODES = ("repeat", "once", "best_cost", "clicks")

# The shopping vocabulary is not a list kept in this file - it is whatever the
# ENGINE can actually recognize on the workshop panel, i.e. the label templates
# shopper._find_stat() matches against. Deriving it from the directory means a
# newly cropped template is offerable the moment it lands, and (the reason that
# matters) a stat with no template can never be written into a profile: it would
# compile fine, then be silently unbuyable for the rest of the run.
_STAT_TEMPLATES_DIR = ROOT / "templates" / "stats"
# ...minus the chrome glyphs that live in the same folder. `max_label` is the
# "MAX" badge the sweep reads off a maxed row, not something anyone can buy.
_NON_STAT_TEMPLATES = ("max_label",)
_STATS_CACHE: set[str] | None = None

# trigger name -> (required params, optional params). `bar` is the odd one out:
# its params sit as SIBLINGS of the trigger key (`{bar: wall, below: 0.02}`)
# because the schema reads better that way, while every other trigger nests
# them (`{wall_collapse: {from_above: 0.3}}`). _trigger() normalizes both.
TRIGGERS = {
    "bar":           (("below",), ("falling_samples", "deadband")),
    "wall_collapse": (("from_above",), ()),
    "fleet_mark":    ((), ("after_waves", "window_waves")),
    "wave_at_least": ((), ()),          # scalar: {wave_at_least: 4000}
    "wave_between":  ((), ()),          # pair:   {wave_between: [1000, 2000]}
    "second_wind":   (("state",), ("min_procs",)),
    "death_screen":  ((), ()),          # flag: {death_screen: true}
}

# `second_wind.state`, and each one is a question the RunState can already
# answer - no new detection, which is why they are only these four.
#   open            a proc is running and the floater is on screen
#   closed          a proc has happened and the floater is gone
#   after_immunity  ...and the post-proc immunity window has expired
#   any             at least one proc this run (pair with min_procs)
SW_STATES = ("open", "closed", "after_immunity", "any")

ACTIONS = {
    # A burst takes BOTH gates because it has two execution sites that gate
    # differently - see _ABILITY_DEFAULTS. `require_match` is the wall watch's
    # (raw taps, no readiness exists); `require_ready` is the hp path's (a real
    # fire_button readiness test). Which one is live depends on the policy's
    # rescue bar, and the other is refused rather than dropped.
    "burst":           (("fire",), ("cancel_sprint", "retaps",
                                    "require_match", "require_ready")),
    "fire":            (("button",), ("require_ready", "throttle_sec",
                                      "refire_guard_sec")),
    "cancel_sprint":   ((), ()),        # flag
    "switch_cards":    (("preset",), ()),
    # A UW toggle goes through shopper.uw_toggle, which reads the pill before
    # AND after the tap - the panel scrolls, and a coordinate tap there is how
    # the wrong weapon ends up off for a whole run with the log claiming
    # success.
    #
    # `want_on`, NOT `on`, and the name is not a preference: YAML 1.1 parses
    # the bare key `on` as the BOOLEAN True, so `{weapon: x, on: false}` loads
    # as `{weapon: x, True: False}` - the parameter vanishes and the toggle
    # silently switches the weapon ON. (config.yaml already quotes `'on':` in
    # every arm block for the same reason.) It defaults to true and matches
    # uw_toggle's own keyword; the COMPILED key is `on`, where no YAML parser
    # ever sees it.
    "toggle_uw":       (("weapon",), ("want_on",)),
    "surrender_retry": ((), ()),        # flag
    "stop_after_run":  ((), ()),        # flag
}

# Actions whose whole body is a boolean. `{stop_after_run: false}` is a rule
# with its action SWITCHED OFF - and an evaluator that dispatches on key
# presence would stop the run anyway. Refused at compile time so no evaluator
# ever has to be trusted to check truthiness.
FLAG_ACTIONS = ("cancel_sprint", "surrender_retry", "stop_after_run")

# WHAT TIER B CAN EXECUTE (P4 - the composable vocabulary).
#
# P3 shipped a minimal evaluator: one trigger and four actions, everything else
# refused with "not supported until P4". P4 is the promotion - the main-loop
# interpreter evaluates the WHOLE vocabulary, so these tables are the whole
# vocabulary, and nothing in a rule is refused for lack of a runner any more.
#
# WHAT TIER A STILL BUYS, now that Tier B can express the same rules: LATENCY,
# and only latency. The wall goes from full to dead in about two seconds - a
# main-loop rule watching it samples at ~1s and cannot save it. So a wall rule
# that lands in a Tier A slot is a RESCUE, and the same rule at Tier B is an
# OBSERVATION (stop the run, swap cards, log). Both are legal; only one is a
# rescue, and the compiled `latency` field is what says which.
TIER_B_TRIGGERS = tuple(TRIGGERS)
TIER_B_BARS = BAR_NAMES
TIER_B_ACTIONS = tuple(ACTIONS)

# ...and what may fire ON THE DEATH SCREEN: writing the run flag, and nothing
# else. Three separate reasons, all of them the same reason - the stats dialog
# is not a screen anything else here knows how to stand on:
#
#   * `fire` / `burst` / `cancel_sprint` - there is no ability row, no sprint
#     button and no wall, so the tap lands at a fixed coordinate inside a
#     dialog. That is the no-clicking-in-menus rule verbatim.
#   * `switch_cards` - loadout.apply_cards navigates FROM HOME, and the stats
#     dialog has no verified route there; the death path would have to be
#     refactored to provide one. Card swaps between runs already have a home:
#     the chores registry (P5/P6).
#   * `surrender_retry` - shard.abandon_run surrenders a LIVE battle. On the
#     stats dialog the run is already over, so it is semantically null.
#
# The runtime refuses all five loudly (orchestrator.py, death phase). Accepting them
# HERE would compile a rule that validates, appears in the dashboard, and is
# retired with `rule_unsupported` the first time the player dies - the
# accepted-but-ignored shape this module exists to abolish.
DEATH_SCREEN_ACTIONS = ("stop_after_run",)

# THERE IS NO VERIFIED ROUTE FROM A LIVE BATTLE TO THE CARDS SCREEN, so nothing
# that would walk one is accepted - not the Tier B `switch_cards` action, not a
# non-empty tournament `in_run_actions` schedule.
#
# The runtime reached this conclusion first and on evidence: loadout.apply_cards
# -> tourney.open_nav opens with a FIXED tap on the bottom nav row and its
# return leg polls for HOME, both written for a game sitting at Home, and NO
# template of the in-battle nav row exists under templates/ - so from a battle
# the opening tap cannot be confirmed to be landing on anything. orchestrator retires
# `switch_cards` at admission (`_rule_admits_action`) and refuses
# `in_run_actions` outright (`run_in_run_actions`), on coin farms as well as
# tournaments.
#
# THE COMPILER HAS TO AGREE, and that is the whole reason this constant exists.
# A profile that validates a rule the runtime always retires is the
# accepted-but-ignored shape this module exists to abolish: it renders in the
# dashboard, reads as configured, and does nothing. Refusing it here moves the
# answer from "your rescue quietly did not happen" to "this profile will not
# load, and here is why".
#
# THE KEY STAYS WIRED. `in_run_actions: []` remains legal and still compiles,
# and the vocabulary still lists both, marked pending - so the day the route
# exists this is a refusal to delete, not a format to re-add.
NO_CARDS_ROUTE = (
    "no verified route from a live battle to the cards screen exists yet - "
    "loadout.apply_cards opens with a fixed, unverifiable tap on the in-battle "
    "nav row and returns by polling for HOME. The runtime refuses this, so the "
    "profile does too; the three things that would enable it are documented in "
    "orchestrator.run_in_run_actions")
# The token that marks a vocabulary entry as offered-but-not-yet-runnable, so
# the dashboard (and the tests) can key off it instead of matching prose.
PENDING_ROUTE = "PENDING VERIFIED ROUTE"

# Why each refused action cannot run there, so the message names the actual
# obstacle instead of listing what is allowed and leaving the author guessing.
_DEATH_SCREEN_WHY = {
    "fire": "there is no ability row on the stats dialog, so the tap would "
            "land at a fixed coordinate inside a menu",
    "burst": "there is no ability row and no sprint on the stats dialog, so "
             "the taps would land at fixed coordinates inside a menu",
    "cancel_sprint": "there is no sprint button on the stats dialog - the run "
                     "is already over",
    "switch_cards": "loadout.apply_cards navigates from HOME and the stats "
                    "dialog has no verified route there; between-run card "
                    "swaps belong to the chores path (P5/P6)",
    "surrender_retry": "shard.abandon_run surrenders a LIVE battle, and on "
                       "the stats dialog the run is already over - there is "
                       "nothing left to surrender",
}

# Per-rule refire floor when nothing states one. 5s is the literal the P3
# evaluator used (orchestrator.RULE_REFIRE_SEC) for exactly this purpose.
DEFAULT_RULE_REFIRE_SEC = 5.0

# THE LAST "until P4" REFUSAL, and it is not about rules at all. `grant_targets`
# is nominally generic, but flows/quest_sm.py follows Smart-Missiles choreography end
# to end and logs every grant as smart_missiles. Accepting another weapon would
# produce a run that reports success for something it never farmed, so the field
# stays pinned until the RUNNER is really generic - a flows/quest_sm.py change, not a
# vocabulary one, which is why the P4 rule work did not lift it.
P4 = "not supported until P4"
GRANT_TARGETS_SUPPORTED = ("smart_missiles",)

# THE "not consumed until P6" REFUSALS ARE GONE, and this note is what is left
# of them. Coin `cancel_sprint` / `max_wave` and tournament `in_run_actions`
# were accepted by the schema and read by nothing, so asking for the behaviour
# was an error - a dashboard that renders a toggle nothing is wired to is a
# trap, and the person most likely to trip it is the one who trusts the UI.
# P6 landed the readers (orchestrator.apply_cancel_sprint, orchestrator.max_wave_reached,
# orchestrator.run_in_run_actions), so the three fields are ordinary fields now:
# validated below, compiled EXPLICITLY, and consumed. Nothing else was ever on
# the list, so the mechanism itself retires with them.
#
# `count` on a coin blueprint is NOT one of them and did not come back - see
# _COUNT_ALTERNATIVE. There is ONE counting authority and it is the plan block.

# The v1 in-run action vocabulary: exactly one action kind, and it is a card
# swap. `in_run_actions` is a TOURNAMENT field because that is where a deck
# change mid-run is worth leaving the battle screen for (the P1 deck clears
# early waves, the P2 deck survives late ones); a coin farm that walked itself
# to the cards screen mid-run would just lose the night.
IN_RUN_ACTIONS = ("switch_cards",)
IN_RUN_ACTION_KEYS = ("at_wave",) + IN_RUN_ACTIONS
# TWO PER RUN, and the cap is not arbitrary: each action leaves the battle
# screen, walks the card menus and comes back (orchestrator.run_in_run_actions sets
# `bot_left_battle` for exactly that reason), so it is the most expensive thing
# a rule can ask for. Two is one swap-in and one swap-back, which is the whole
# use case v1 was asked for; a third is a schedule, and a schedule belongs in
# the plan.
IN_RUN_ACTIONS_MAX = 2

# plan block name -> the kinds it may launch. A `tournament` block pointing at
# a shard blueprint passes an existence check and then runs the wrong thing at
# 19:00 on a Wednesday.
BLOCK_KINDS = {
    "coin": ("coin",),
    "shards": ("shard",),
    "tournament": ("tournament",),
}
_QUEST_BLOCK_KINDS = ("uw_grant_quest", "cycle_quest")

# ---- the day plan (P5). `plan.week` maps a weekday to a named day plan;
# `default` covers every day not named.
#
# datetime.weekday() order - index IS the weekday number, so the runtime needs
# no mapping table and no locale-dependent strftime("%A").
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")
# ONE SPELLING PER DAY, and it is the full name (Codex P5, LOW - ruled). The
# schema used to take `mon`/`wed` too, which means a profile can carry BOTH
# `wednesday:` and `wed:` and something downstream has to decide which wins -
# a precedence rule nobody would ever read, guarding a collision that has no
# right answer. Refusing the alias in the SOURCE makes the collision
# unconstructible instead. (Neither spelling is a YAML 1.1 boolean, so that
# trap is not what this is about.)
WEEK_KEYS = ("default",) + WEEKDAYS
BLOCK_KEYS = ("block", "blueprint", "after", "until", "count")
DAY_MINUTES = 24 * 60

# A tournament block is ONE ENTRY PER DAY, and that is not a default anyone may
# raise: the ticket purchase auto-starts the run and the gem cost escalates
# 10 -> 20 -> 30. combo has always marked the phase done after a single entry.
TOURNEY_RUNS_PER_DAY = 1

# Key vocabularies for the "unknown key" refusals. A misspelled key in a policy
# is a setting the player believes is in effect and that nothing reads.
TOP_SECTIONS = ("player", "blueprints", "policies", "plan")
# ...and which of them a profile must actually carry. `plan` is the one that
# may be left out, and leaving it out MEANS something: no plan artifact, so the
# scheduler keeps its own constants (see the tri-state in SCHEMA.md). An EMPTY
# plan is the third state and is refused - it schedules nothing, and a
# scheduler handed nothing has no safe move.
REQUIRED_SECTIONS = ("player", "blueprints", "policies")
POLICY_SECTIONS = ("uw_policies", "rescue_policies", "gather", "shopping_lists",
                   "chores")
# `label` is the human name the dashboard shows for a policy ("Chain
# Lightning Farming Choreography"); the key stays the identifier. Legal on
# all four policy families, consumed by the dashboard UI only - the compiler
# ignores it deliberately (it is display data, not behavior).
GATHER_KEYS = ("flying_gem", "gem_delay_sec", "ad_gems", "quests_8h",
               "quest_rewards", "guild", "label")
DIRECTIVE_KEYS = ("enabled", "tab", "stats", "mode", "clicks")
CL_KEYS = ("mode", "always_on_above", "on_above", "pre_mark_waves",
           "off_after_waves")
UW_POLICY_KEYS = ("baseline", "chain_lightning", "label")
RESCUE_POLICY_KEYS = ("arm", "end_sprint_after_sw", "rules", "label")
ARM_KEYS = ("on", "watch_sec", "immunity_sec")
# `refire_sec` is the rule's own cooldown floor. It exists because `repeat` is
# live from P4 on, and a repeating rule with no floor re-fires every main-loop
# pass. A `fire` action may state the same thing as `throttle_sec` /
# `refire_guard_sec`; stating BOTH is refused rather than silently ranked.
RULE_KEYS = ("when", "do", "repeat", "refire_sec")

# Fields a blueprint may carry, per kind. `kind` picks the runner, and the
# runner is what decides which fields mean anything - `cycle_sec` on a coin
# blueprint is not a harmless extra, it is a setting the player believes is in
# effect. So unknown/out-of-kind fields are reported, not ignored.
_COMMON_FIELDS = ("kind", "label", "loadout", "tier", "policies")
_KIND_FIELDS = {
    "coin":           ("cancel_sprint", "max_wave", "dissonant_tab", "count",
                       "restart_via_home", "shop_interval_sec", "shopping"),
    "tournament":     ("gem_entry_max", "in_run_actions", "count",
                       "restart_via_home", "shop_interval_sec", "shopping"),
    # `count` lives HERE and nowhere else: it is what becomes `--loops`. It
    # stays listed on the other kinds only so that writing one gets the
    # specific "use rides / use cycles" message below instead of a bare
    # "not a legal field" (see _COUNT_ALTERNATIVE).
    "shard":          ("count",),
    "uw_grant_quest": ("grant_targets", "reroll_at_wave", "ride_to_wave",
                       "rides", "uw_setup", "count"),
    "cycle_quest":    ("cycle_sec", "cycles", "count"),
}

# Extension kinds carry the fields their FLOW spec declares - the flow file
# is the authority on what its blueprints may say.
for _k in KINDS:
    if _k not in _KIND_FIELDS:
        _KIND_FIELDS[_k] = tuple(_flows_registry.flow(_k)["blueprint_fields"])

# Each non-shard kind already has its own name for "how many", and `count` is
# not wired to any of them - it reaches only flows/shard.py's `--loops`. Naming the
# right field beats saying "no".
#
# ONE COUNTING AUTHORITY (P6 ruling). Coin gained `max_wave` and
# `cancel_sprint` in P6 but NOT `count`, and that is deliberate: "how many coin
# runs today" is a scheduling question, the plan block already answers it, and
# its answer is the one that is persisted per day in daystate. A second count
# on the blueprint would be a per-spawn cap that the plan cannot see and the
# day counter cannot reconcile - two authorities disagreeing about the same
# number, which is how a day silently runs twice or not at all.
_COUNT_ALTERNATIVE = {
    "uw_grant_quest": "use `rides`",
    "cycle_quest": "use `cycles`",
    "coin": "runs per day live on the PLAN BLOCK (`plan.days.<day>[].count`), "
            "which is where the day counter reads them",
    "tournament": "one entry per blueprint, and one tournament block per day",
}

# Defaults for the flat abilities dict. Emitted even when no rule sets them, so
# the wall watch can hoist every one to a local without a `.get()` dance and
# without ever seeing a missing key mid-rescue.
_ABILITY_DEFAULTS = {
    "hold_until_second_wind": False,
    "post_sw_watch_sec": None,
    "sw_immunity_sec": None,
    "end_sprint_after_sw": False,
    # NULL, NOT "wall". `rescue_bar` is what tells the runtime a rescue exists
    # at all: a blueprint with no rescue policy compiles to None here, and the
    # watch skips the whole block. Defaulting it to "wall" while dm_below stays
    # None is what put a valid profile into a permanent five-second crash loop
    # (`extent < None` -> TypeError -> blanket handler -> retry) - Codex #3.
    "rescue_bar": None,
    "dm_below": None,
    "nuke_below": None,
    "nuke_on_fleet": None,
    # NEW scalars (P2). They existed as hardcoded constants in the watch loop;
    # the profile is what makes them settable per blueprint.
    #
    # ALL FIVE ARE ALWAYS EXPLICIT, never None, even when no rule sets them.
    # The watch hoists them to locals at entry and uses them as bare values -
    # and more to the point, these are the RUNTIME SAFETY NET (a wall falling
    # from above 0.3 is a collapse; two falling samples outside a 0.01 deadband
    # is a real decline). A safety net that is invisible in the compiled dict
    # is one nobody remembers is there, so it is written down every time.
    "falling_samples": 2,
    "deadband": 0.01,
    "collapse_from": 0.3,
    "burst_cancel_sprint": True,
    "burst_retaps": 3,
    # Tier A `fire` parameters. They used to validate and then be DROPPED on
    # the floor, so two policies with different safety behaviour compiled to
    # byte-identical presets (Codex round 2, #4).
    #
    # These are PER SITE, not global, because legacy orchestrator.py is per site.
    # There are FOUR fire sites and they do not agree - and they are not even
    # all asking the same question:
    #
    #   orchestrator.py:500  WALL burst       raw act.tap          -> burst_require_match
    #   orchestrator.py:800  HP rescue DM     require_ready=False  -> burst_require_ready
    #   orchestrator.py:806  hp rescue nuke   (default) True       -> hp_nuke_require_ready
    #   orchestrator.py:455/737 fleet nuke    (default) True       -> nuke_on_fleet.require_ready
    #
    # THE BURST HAS TWO SITES. In _fast_wall_watch it is three instant
    # act.tap() calls off one frame - there is no fire_button and therefore no
    # readiness test at all; the only question it can answer is whether the
    # Demon Mode glyph was MATCHED, because an unmatched one falls back to the
    # fixed RESCUE_DM_PT coordinate (orchestrator.py:501). On the hp path the same
    # rule compiles into a real fire_button call, which does test readiness.
    # Same rule, same words, two different gates - so they get two keys, and
    # writing the one your rescue bar does not use is refused.
    #
    # Both default False, which is today's behaviour at both sites: an
    # unmatched icon still taps the fallback coordinate, and the hp DM still
    # fires without waiting for ready, because a missed glyph must not cost
    # the run. Set either True on an account whose abilities are not confirmed
    # (see the `abilities_verified` gate).
    "burst_require_match": False,
    "burst_require_ready": False,
    "hp_nuke_require_ready": True,
    "refire_guard_sec": 15,
}

# Defaults for the fleet-mark Nuke, matching orchestrator.py's call site.
_DEFAULT_FLEET_THROTTLE = 5.0
_DEFAULT_FLEET_REQUIRE_READY = True

# Which `fire` params each Tier A slot has somewhere to put. A param outside
# its slot's list would be validated and then dropped, which is the
# accepted-but-ignored trap again - so it is refused instead. bar_nuke takes no
# throttle: that site is rate-limited by can_fire()'s refire guard, not by a
# throttle of its own (orchestrator.py:804-806).
_TIER_A_FIRE_PARAMS = {
    "fleet":    ("throttle_sec", "require_ready", "refire_guard_sec"),
    "bar_nuke": ("require_ready", "refire_guard_sec"),
}

_DEFAULT_GEM_DELAY = [3, 10]
_DEFAULT_SHOP_INTERVAL = 90


# --------------------------------------------------------------------- load

def load(name: str) -> dict:
    """Read `profiles/<name>.yaml` and return it as a dict.

    Does NOT validate - `validate()` is separate so the dashboard can show a
    broken profile's contents next to its list of problems instead of showing
    nothing at all. Stamps `_name`/`_path` so downstream attestation logs can
    say WHICH file produced a compiled preset without threading the name
    through every call.
    """
    if not name or not isinstance(name, str):
        raise ProfileError(f"profile name must be a non-empty string, got {name!r}")
    path = Path(PROFILES_DIR) / f"{name}.yaml"
    if not path.exists():
        raise ProfileError(f"no such profile: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProfileError(f"cannot read profile {path}: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ProfileError(f"profile {path} is not valid YAML: {e}") from e
    if data is None:
        raise ProfileError(f"profile {path} is empty")
    if not isinstance(data, dict):
        raise ProfileError(f"profile {path} must be a mapping at the top level, "
                           f"got {type(data).__name__}")
    data["_name"] = name
    data["_path"] = str(path)
    return data


# ----------------------------------------------------------------- helpers

def _d(value) -> dict:
    """Treat None/missing as an empty mapping. YAML writes an omitted block as
    None, and every caller here wants 'nothing configured', not a crash."""
    return value if isinstance(value, dict) else {}


def _owned_uws(profile: dict) -> set[str]:
    return {k for k, v in _d(_d(profile.get("player")).get("uws")).items() if v}


# ------------------------------------------------------------ type checking
#
# Every consumer of a compiled preset uses these values RAW: `random.randint(*r)`,
# `random.uniform(*gem_delay_sec)`, `now - last > shop_interval_sec`,
# `extent < below`. YAML will happily hand over a string, a null or a
# three-element list for any of them, and each of those is a crash inside the
# run loop rather than a message at startup. So the shapes are pinned here.
#
# `bool` is REJECTED wherever an int is wanted. In Python `True == 1` and
# `isinstance(True, int)` is True, so `tier: true` would sail through a naive
# integer check and then be used as tier 1.

def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value) -> bool:
    """A REAL number. `float('nan')` and `float('inf')` are floats and YAML
    writes both (`.nan`, `.inf`) without complaint - and every comparison this
    codebase makes with them silently does the wrong thing (Codex P4, MEDIUM):
    `now < nan` is False forever, so a NaN cooldown is NO cooldown, and an
    infinite one suppresses the rule for the life of the run. Neither raises,
    so neither is ever noticed."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _check_bool(value, path: str, out: list[str]) -> None:
    if value is not None and not isinstance(value, bool):
        out.append(f"{path}: must be true or false, got {value!r}")


def _check_pos_int(value, path: str, out: list[str], *,
                   allow_zero: bool = False) -> None:
    floor = 0 if allow_zero else 1
    if value is None:
        return
    if not _is_int(value) or value < floor:
        out.append(f"{path}: must be an integer >= {floor}, got {value!r}")


def _check_pos_num(value, path: str, out: list[str]) -> None:
    if value is None:
        return
    if not _is_num(value) or value <= 0:
        out.append(f"{path}: must be a positive number, got {value!r}")


def _check_unit(value, path: str, out: list[str]) -> None:
    """A bar threshold: a fraction of a bar, so 0..1 inclusive. 40 is not 40%
    and would make the rescue fire on the very first sample of every run."""
    if value is None:
        return
    if not _is_num(value) or not 0 <= value <= 1:
        out.append(f"{path}: must be a fraction between 0 and 1 (a share of "
                   f"the bar, not a percentage), got {value!r}")


def _check_range(value, path: str, out: list[str], *,
                 required: bool = False) -> None:
    """A [lo, hi] wave/second pair that gets splatted into random.randint().

    NON-NEGATIVE INTEGERS, both bounds. `random.randint(5.5, 25)` does not
    round - it raises ValueError("non-integer arg 1"), inside cl_window(), on
    the wave-1 normalization of every run. A float here validated cleanly and
    crashed at runtime until Codex found it (round 2, #3). Same rule for
    gem_delay_sec: it is splatted the same way by consumers that take it off
    the `gather` dict rather than the flat preset key.
    """
    if value is None:
        if required:
            out.append(f"{path}: required, a [low, high] pair of "
                       f"non-negative integers")
        return
    if not isinstance(value, list) or len(value) != 2:
        out.append(f"{path}: must be a [low, high] pair, got {value!r}")
        return
    lo, hi = value
    if not _is_int(lo) or not _is_int(hi):
        out.append(f"{path}: both bounds must be integers - random.randint() "
                   f"raises on a float, it does not round - got {value!r}")
    elif lo < 0 or hi < 0:
        out.append(f"{path}: bounds must be non-negative, got {value!r}")
    elif lo > hi:
        out.append(f"{path}: low bound {lo} is above high bound {hi}")


def _check_label(body: dict, path: str, out: list[str]) -> None:
    """Optional display name, uniform across the policy families. None is
    absent; an empty or non-string label is refused rather than shown blank."""
    label = body.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip()):
        out.append(f"{path}.label: must be a non-empty display string "
                   f"(it names the policy in menus), got {label!r}")


def _check_keys(body: dict, known, path: str, out: list[str]) -> None:
    for key in body:
        # A BOOLEAN KEY IS ALWAYS YAML 1.1 EATING A WORD, never a typo: `on:`,
        # `off:`, `yes:`, `no:` load as True/False. It matters most in `arm`,
        # where an unquoted `on: second_wind` becomes `{True: 'second_wind'}` -
        # arm.on is then absent, the policy is silently UNARMED, and the rescue
        # that was configured never runs. "unknown key True" sends the author
        # hunting for a typo that is not there, so the message names the cause.
        if isinstance(key, bool):
            out.append(f"{path}: YAML read the key `on`/`off`/`yes`/`no` as "
                       f"the boolean {key!r} (YAML 1.1 rules), so the setting "
                       f"was lost. Quote it: `'on': ...` "
                       f"(known: {', '.join(sorted(known))})")
            continue
        if key not in known:
            out.append(f"{path}.{key}: unknown key "
                       f"(known: {', '.join(sorted(known))})")


def shop_stats(refresh: bool = False) -> set[str]:
    """Stat names the workshop sweep can actually find on screen, from
    `templates/stats/*.png`. Cached: validate() asks per directive and this is
    a directory listing, not a fact that changes mid-process. The dashboard
    calls it with refresh=True after cropping a new template."""
    global _STATS_CACHE
    if _STATS_CACHE is None or refresh:
        try:
            names = {p.stem for p in Path(_STAT_TEMPLATES_DIR).glob("*.png")}
        except OSError:
            names = set()
        _STATS_CACHE = names - set(_NON_STAT_TEMPLATES)
    return _STATS_CACHE


def _shopping_parts(entry) -> tuple[bool, list]:
    """(master_enabled, directives) for a shopping list in EITHER shape.

    The v1 shape was a bare list, and profiles written against it must keep
    working - a schema change that invalidates the file a player already wrote
    is a schema change that gets reverted. Bare list = enabled, all directives
    enabled; the migrator is what makes the flags explicit, not the loader.
    """
    if isinstance(entry, list):
        return True, entry
    if isinstance(entry, dict):
        directives = entry.get("directives")
        return (bool(entry.get("enabled", True)),
                directives if isinstance(directives, list) else [])
    return True, []


def _trigger(when, path: str) -> tuple[str, dict]:
    """(trigger_name, normalized params) for one rule's `when` block.

    Raises ProfileError naming `path` on anything unrecognizable - a `when` the
    compiler cannot read is a rule that would never fire, which must never be
    mistaken for a rule that decided not to.
    """
    if not isinstance(when, dict):
        raise ProfileError(f"{path}.when: must be a mapping, got "
                           f"{type(when).__name__}")
    names = [k for k in when if k in TRIGGERS]
    if not names:
        raise ProfileError(f"{path}.when: no known trigger in "
                           f"{sorted(when)} (known: {', '.join(sorted(TRIGGERS))})")
    if len(names) > 1:
        raise ProfileError(f"{path}.when: {len(names)} triggers in one rule "
                           f"({', '.join(sorted(names))}) - split them into "
                           f"separate rules")
    name = names[0]
    value = when[name]
    if name == "bar":
        params = {k: v for k, v in when.items() if k != "bar"}
        params["bar"] = value
    elif name in ("wave_at_least", "wave_between"):
        params = {"value": value}
    elif isinstance(value, dict):
        params = dict(value)
    else:
        params = {}

    required, optional = TRIGGERS[name]
    known = set(required) | set(optional) | ({"bar"} if name == "bar" else set())
    if name == "wave_at_least":
        known |= {"value"}
        if not isinstance(value, (int, float)):
            raise ProfileError(f"{path}.when.wave_at_least: expected a wave "
                               f"number, got {value!r}")
    if name == "wave_between":
        known |= {"value"}
        # A WINDOW IS TWO NUMBERS. One number is a threshold and belongs to
        # wave_at_least; three is a typo that would silently window the first
        # two. _check_range does the ordering and the integer check.
        if not isinstance(value, list) or len(value) != 2:
            raise ProfileError(f"{path}.when.wave_between: expected a "
                               f"[low, high] pair of waves, got {value!r}")
    for key in params:
        if key not in known:
            raise ProfileError(f"{path}.when.{name}: unknown parameter "
                               f"'{key}' (known: {', '.join(sorted(known))})")
    for key in required:
        if params.get(key) is None:
            raise ProfileError(f"{path}.when.{name}: missing required "
                               f"parameter '{key}'")
    if name == "bar" and params["bar"] not in BAR_NAMES:
        raise ProfileError(f"{path}.when.bar: unknown bar {params['bar']!r} "
                           f"(known: {', '.join(BAR_NAMES)})")
    # A FLAG TRIGGER MUST BE EXACTLY `true`, for the same reason a flag action
    # must: `{death_screen: false}` reads as "this rule is switched off" and
    # compiles to a rule that fires on every death, because the trigger is
    # recognised by its NAME. One spelling, checked once.
    if name == "death_screen" and value is not True:
        raise ProfileError(f"{path}.when.death_screen: flag trigger must be "
                           f"exactly `true`, got {value!r}. Delete the rule "
                           f"instead of switching its own trigger off")
    return name, params


def _action(do, path: str) -> tuple[str, dict]:
    """(action_name, normalized params) for one rule's `do` block. Same
    refuse-on-unknown contract as _trigger()."""
    if not isinstance(do, dict):
        raise ProfileError(f"{path}.do: must be a mapping, got "
                           f"{type(do).__name__}")
    names = [k for k in do if k in ACTIONS]
    if not names:
        raise ProfileError(f"{path}.do: no known action in {sorted(do)} "
                           f"(known: {', '.join(sorted(ACTIONS))})")
    if len(names) > 1:
        raise ProfileError(f"{path}.do: {len(names)} actions in one rule "
                           f"({', '.join(sorted(names))}) - one action per rule")
    name = names[0]
    value = do[name]
    params = dict(value) if isinstance(value, dict) else {}

    required, optional = ACTIONS[name]
    known = set(required) | set(optional)
    for key in params:
        # A BOOLEAN KEY IS ALWAYS YAML 1.1 EATING A WORD. `on:`/`off:` (and
        # `yes:`/`no:`) load as True/False, so the parameter disappears and its
        # value lands under a key nothing reads. Saying "unknown parameter
        # 'True'" sends the author hunting for a typo that is not there.
        if isinstance(key, bool):
            raise ProfileError(
                f"{path}.do.{name}: YAML read the key `on`/`off`/`yes`/`no` as "
                f"the boolean {key!r} (YAML 1.1 rules), so the parameter was "
                f"lost. Write `want_on: true/false`"
                + (f" (known: {', '.join(sorted(known))})" if known else ""))
        if key not in known:
            raise ProfileError(f"{path}.do.{name}: unknown parameter '{key}'"
                               + (f" (known: {', '.join(sorted(known))})"
                                  if known else ""))
    for key in required:
        if params.get(key) is None:
            raise ProfileError(f"{path}.do.{name}: missing required "
                               f"parameter '{key}'")
    button = params.get("button") if name == "fire" else params.get("fire")
    if name in ("fire", "burst") and button not in BUTTONS:
        raise ProfileError(f"{path}.do.{name}: unknown button {button!r} "
                           f"(known: {', '.join(BUTTONS)})")
    # A FLAG ACTION MUST BE EXACTLY `true`. `{stop_after_run: false}` reads as
    # "this rule does nothing", but an evaluator that dispatches on key
    # presence stops the run regardless. `null` and `{}` are the same hazard
    # wearing a different hat - YAML writes `stop_after_run:` with no value as
    # None, which looks deliberate and means nothing. One spelling, checked
    # once, so no evaluator ever has to be trusted to test truthiness.
    if name in FLAG_ACTIONS and do[name] is not True:
        raise ProfileError(f"{path}.do.{name}: flag action must be exactly "
                           f"`true`, got {do[name]!r}. Delete the rule instead "
                           f"of switching its own action off")
    return name, params


def _rules_of(policy: dict) -> list:
    rules = policy.get("rules")
    return rules if isinstance(rules, list) else []


def _classify_rules(policy: dict, path: str) -> list[dict]:
    """One entry per rule: its trigger, action and the Tier A slot it claims.

    THE SINGLE SOURCE OF THE SPLIT. validate() has to know which tier a rule
    lands in (Tier B can only execute part of the vocabulary today) and
    compile_preset() has to perform the split. Two implementations of "which
    slot does this rule take" would drift, and the drift would show up as a
    rule the validator blessed and the compiler quietly retired.

    Raises ProfileError on unreadable vocabulary, exactly like _trigger()/
    _action(); validate() catches, compile lets it fly.
    """
    on_sw, _, _ = _arm(policy)
    # PRE-PASS: does this policy watch the WALL? It decides two of the four
    # slots, and it has to be known before the first rule is placed - a
    # `wall_collapse` rule written above the `bar: wall` rule is the same
    # policy as one written below it. Deliberately tolerant (plain lookups, no
    # _trigger): a rule that cannot be read is reported by the loop below.
    wall_rescue = any(isinstance(_d(r).get("when"), dict)
                      and _d(r)["when"].get("bar") == "wall"
                      for r in _rules_of(policy))
    taken: set[str] = set()
    out: list[dict] = []
    for i, rule in enumerate(_rules_of(policy)):
        rpath = f"{path}.rules[{i}]"
        rule = _d(rule)
        trig, tp = _trigger(rule.get("when"), rpath)
        act, ap = _action(rule.get("do"), rpath)
        slot = None
        if on_sw:
            if trig == "bar" and act == "burst":
                slot = "bar_burst"
            elif trig == "bar" and act == "fire" and ap.get("button") == "nuke" \
                    and tp.get("bar") == "hp":
                # `nuke_below` IS THE hp BRANCH's key. The wall branch hands
                # the whole rescue to the fast watch, which fires Demon Mode
                # through the burst and never consults a nuke threshold - so a
                # wall-bar nuke does not belong in this slot. It is not refused
                # any more: it falls to Tier B, where the main loop reads the
                # wall itself and can fire the Nuke.
                slot = "bar_nuke"
            elif trig == "wall_collapse" and act == "burst" and wall_rescue:
                # Same shape: only a WALL rescue enters the watch that hoists
                # collapse_from. In any other policy the collapse rule is a
                # main-loop rule, which reads the wall for itself.
                slot = "collapse"
            elif trig == "fleet_mark" and act == "fire" \
                    and ap.get("button") == "nuke":
                slot = "fleet"
        if slot is not None and slot in taken:
            slot = None                 # the slot is spoken for: spills to B
        if slot is not None:
            taken.add(slot)
        out.append({"index": i, "path": rpath, "rule": rule,
                    "trigger": trig, "tparams": tp,
                    "action": act, "aparams": ap, "slot": slot})
    return out


def _arm(policy: dict) -> tuple[bool, object, object]:
    """(armed_on_second_wind, watch_sec, immunity_sec) from a rescue policy.

    `arm: always` is the shorthand for "no gate"; a mapping carries the Second
    Wind gate plus its two clocks. A MISSING `watch_sec` compiles to None, i.e.
    watch for the rest of the run - the fixed 30s window is what lost the
    wave-1120 tournament, so "unspecified" must mean forever, not 30.
    """
    arm = policy.get("arm")
    if isinstance(arm, dict):
        return (arm.get("on") == "second_wind",
                arm.get("watch_sec"), arm.get("immunity_sec"))
    return False, None, None


# ------------------------------------------------------------------ validate

def validate(profile: dict) -> list[str]:
    """Every problem with `profile`, as human-readable strings naming the
    offending path. Empty list = the profile is safe to run on THIS account.

    Collects instead of raising on the first fault on purpose: a freshly hand-
    written profile usually has three or four things wrong with it, and fixing
    them one launch at a time is how a player gives up on the feature.
    """
    problems: list[str] = []
    if not isinstance(profile, dict):
        return [f"profile: must be a mapping, got {type(profile).__name__}"]

    # THREE SECTIONS ARE REQUIRED. A profile missing `player` cannot be
    # ownership-gated at all, and "no player section" must never read as "no
    # restrictions" - that is the difference between refusing a build and
    # blind-tapping an ability the account does not have.
    #
    # `plan` is the exception, and its absence is a DECISION rather than an
    # omission: no plan section -> no compiled plan -> the scheduler runs its
    # own constants. That is the legacy leg of the tri-state, and it is what
    # makes "remove the plan section" an answer a player can act on.
    for section in TOP_SECTIONS:
        if profile.get(section) is None:
            if section in REQUIRED_SECTIONS:
                problems.append(f"{section}: required section is missing")
        elif not isinstance(profile[section], dict):
            problems.append(f"{section}: must be a mapping, got "
                            f"{type(profile[section]).__name__}")

    player = _d(profile.get("player"))
    policies = _d(profile.get("policies"))
    _check_keys(policies, POLICY_SECTIONS, "policies", problems)
    uw_policies = _d(policies.get("uw_policies"))
    rescue_policies = _d(policies.get("rescue_policies"))
    gathers = _d(policies.get("gather"))
    shopping_lists = _d(policies.get("shopping_lists"))
    owned = _owned_uws(profile)

    for name, body in uw_policies.items():
        problems += _validate_uw_policy(f"policies.uw_policies.{name}",
                                        _d(body), owned)
    for name, body in rescue_policies.items():
        problems += _validate_rescue_policy(f"policies.rescue_policies.{name}",
                                            _d(body), player)
    for name, body in gathers.items():
        problems += _validate_gather(f"policies.gather.{name}", body)
    for name, body in shopping_lists.items():
        problems += _validate_shopping_list(
            f"policies.shopping_lists.{name}", body)
    problems += _validate_chores(policies.get("chores"))

    blueprints = _d(profile.get("blueprints"))
    if isinstance(profile.get("blueprints"), dict) and not blueprints:
        problems.append("blueprints: empty - a profile with no blueprint "
                        "cannot run anything")
    for name, body in blueprints.items():
        problems += _validate_blueprint(f"blueprints.{name}", _d(body), player,
                                        uw_policies, rescue_policies, gathers,
                                        shopping_lists, owned)

    if profile.get("plan") is not None:
        problems += _validate_plan(_d(profile.get("plan")), blueprints)
    return problems


def warnings(profile: dict) -> list[str]:
    """Advisories that must never block a launch. EMPTY BY CONSTRUCTION today.

    This used to carry the not-yet-consumed P5/P6 fields, on the theory that a
    null placeholder was harmless. It is not: the dashboard renders `max_wave:
    null` as a setting that is merely switched off rather than one nothing
    reads, which invites exactly the edit that silently does nothing. Those
    became validate() errors, and then P6 landed their readers and they became
    ordinary fields - `max_wave: null` now really does mean "no cap". Either
    way the lesson stands: a warning nobody must act on is a warning people
    learn to scroll past.

    Kept as the hook it is: the dashboard calls it, and the next genuinely
    advisory finding - accepted, working, but worth a second look - belongs
    here rather than being promoted into a refusal.

    P5 filled it: a plan block that can never run. That one really is
    advisory - a dead block costs nothing at runtime (the day runs the block
    above it), unlike a dead rescue rule, which costs the run it was written
    to save. It is reported rather than refused for exactly that reason.
    """
    if not isinstance(profile, dict):
        return []
    return (_plan_warnings(_d(profile.get("plan")))
            + loadout_corruption_warnings(profile)
            + _uw_ownership_warnings(profile))


def _uw_ownership_warnings(profile: dict) -> list[str]:
    """Advisory (2026-08-29): a UW baseline set true for a weapon this
    account does not own. Legal by design - presets name any UW and travel
    between accounts; _compile_uw_wanted drops unowned entries - but worth
    a second look, because on THIS account the switch does nothing."""
    owned = _owned_uws(profile)
    out = []
    for name, body in _d(_d(profile.get("policies")).get("uw_policies")).items():
        for uw, want in _d(_d(body).get("baseline")).items():
            if want is True and uw in UW_NAMES and uw not in owned:
                out.append(
                    f"policies.uw_policies.{name}.baseline.{uw}: set true, "
                    f"but this account does not own it (player.uws.{uw} is "
                    f"not true) - it is skipped here and applies only on "
                    f"accounts that own it")
    return out


def loadout_corruption_warnings(profile: dict) -> list[str]:
    """v29 advisory: a loadout that hand-equips a category the account has
    presets for, WITHOUT selecting a preset first, mutates whichever preset
    happens to be active - usually the farming one - and nothing restores
    preset contents. Advisory rather than refusal: the exposure only costs
    anything when that loadout actually runs, and refusing would brick a
    profile over quest loadouts that are parked. The fix it names is one
    line: add a <category>_preset selection to the loadout body."""
    out: list[str] = []
    player = _d(profile.get("player"))
    cat_presets = _d(player.get("category_presets"))
    loadouts = _d(CONFIG.get("loadouts"))
    seen: set[str] = set()
    for bp in _d(profile.get("blueprints")).values():
        name = _d(bp).get("loadout")
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        lo = _d(loadouts.get(name))
        if lo.get("global_preset"):
            continue
        for body_key, cat, sel_key in (("modules", "modules", "module_preset"),
                                       ("modules_restore", "modules",
                                        "module_preset"),
                                       ("guardians", "guardians",
                                        "guardian_preset")):
            lst = lo.get(body_key)
            if (isinstance(lst, list) and lst and not lo.get(sel_key)
                    and isinstance(cat_presets.get(cat), list)
                    and cat_presets.get(cat)):
                out.append(
                    f"loadout {name!r} hand-equips {cat} without selecting a "
                    f"{cat} preset first - on v29 the swap permanently "
                    f"rewrites whichever {cat} preset is active (presets "
                    f"auto-save; nothing restores them). Add "
                    f"`{sel_key}: <name>` to the loadout to make the "
                    f"mutation land in a preset you chose")
    return out


def blueprint_kind(profile: dict, name: str) -> str | None:
    """The `kind` of one blueprint, or None if there is no such blueprint.

    Exists so a RUNNER can refuse work that is not its own before it captures a
    frame or taps anything: `flows/quest_sm.py --preset bp_tourney_main` would
    otherwise find a readable battle and surrender a live tournament (Codex
    #5). Cheap, total, and importable without dragging in capture/act.
    """
    bp = _d(profile.get("blueprints")).get(name)
    if bp is None and name.startswith("bp_"):
        bp = _d(profile.get("blueprints")).get(name[3:])
    return _d(bp).get("kind") if bp is not None else None


def _validate_gather(path: str, body) -> list[str]:
    if not isinstance(body, dict):
        return [f"{path}: must be a mapping"]
    out: list[str] = []
    _check_keys(body, GATHER_KEYS, path, out)
    _check_label(body, path, out)
    for key in ("flying_gem", "ad_gems", "quests_8h", "quest_rewards", "guild"):
        _check_bool(body.get(key), f"{path}.{key}", out)
    # REQUIRED, not merely well-shaped. orchestrator does
    # `random.uniform(*preset()["gem_delay_sec"])` and shard does `tuple(...)`
    # on the gather value directly, so a null reaches a splat on the first gem
    # of the run. The compiler substitutes a default for the flat preset key,
    # but the `gather` dict is passed through verbatim to those consumers.
    _check_range(body.get("gem_delay_sec"), f"{path}.gem_delay_sec", out,
                 required=True)
    return out


def _chore_names() -> tuple[str, ...]:
    """The registry's chore names, in priority order. chores.py is the ONE
    home of the registry (its imports are lazy precisely so this stays a
    light import from the dashboard's vocab path)."""
    from scheduling import chores
    return tuple(name for name, _, _ in chores.CHORES)


def _validate_chores(chores) -> list[str]:
    if chores is None:
        return []
    if not isinstance(chores, list):
        return ["policies.chores: must be a list of {name, enabled}"]
    out: list[str] = []
    known = _chore_names()
    for i, item in enumerate(chores):
        cpath = f"policies.chores[{i}]"
        if not isinstance(item, dict):
            out.append(f"{cpath}: must be a mapping")
            continue
        _check_keys(item, ("name", "enabled"), cpath, out)
        if not item.get("name"):
            out.append(f"{cpath}.name: required")
        elif item["name"] not in known:
            # Unknown name -> refusal, never a shrug: a typo here is a chore
            # the player believes is switched off and that keeps running.
            out.append(f"{cpath}.name: unknown chore {item['name']!r} "
                       f"(known: {', '.join(known)})")
        _check_bool(item.get("enabled"), f"{cpath}.enabled", out)
    return out


def _validate_uw_policy(path: str, body: dict, owned: set[str]) -> list[str]:
    out: list[str] = []
    _check_keys(body, UW_POLICY_KEYS, path, out)
    _check_label(body, path, out)
    baseline = body.get("baseline")
    if baseline is not None and not isinstance(baseline, dict):
        out.append(f"{path}.baseline: must be a mapping of weapon -> true/false")
        baseline = None
    for uw, want in _d(baseline).items():
        if uw not in UW_NAMES:
            out.append(f"{path}.baseline.{uw}: unknown ultimate weapon "
                       f"(known: {', '.join(UW_NAMES)})")
            continue
        _check_bool(want, f"{path}.baseline.{uw}", out)
        # `true` for an UNOWNED weapon is legal (2026-08-29, user: presets
        # name any UW, ownership decides what applies): _compile_uw_wanted
        # drops unowned entries, so the normalizer never hunts a missing
        # panel row. Surfaced as an advisory in warnings(), never a refusal.
    cl = body.get("chain_lightning")
    if cl is None:
        return out
    if not isinstance(cl, dict):
        out.append(f"{path}.chain_lightning: must be a mapping")
        return out
    _check_keys(cl, CL_KEYS, f"{path}.chain_lightning", out)
    mode = cl.get("mode")
    if mode not in CL_MODES:
        out.append(f"{path}.chain_lightning.mode: unknown mode {mode!r} "
                   f"(known: {', '.join(CL_MODES)})")
        return out
    if mode != "off" and "chain_lightning" not in owned:
        out.append(f"{path}.chain_lightning: mode {mode!r} needs Chain "
                   f"Lightning, which the player does not own "
                   f"(player.uws.chain_lightning is not true)")
    # Every one of these is splatted into random.randint() by cl_window().
    for key in ("always_on_above", "on_above", "pre_mark_waves",
                "off_after_waves"):
        _check_range(cl.get(key), f"{path}.chain_lightning.{key}", out)
    if mode == "fleet_marks":
        _check_range(cl.get("pre_mark_waves"),
                     f"{path}.chain_lightning.pre_mark_waves", out,
                     required=True)
        _check_range(cl.get("off_after_waves"),
                     f"{path}.chain_lightning.off_after_waves", out,
                     required=True)
    if mode == "off_until_wave":
        _check_range(cl.get("on_above"), f"{path}.chain_lightning.on_above",
                     out, required=True)
    return out


def _validate_shopping_list(path: str, body) -> list[str]:
    """Vocabulary check on one shopping list.

    List ORDER is priority - the sweep walks it top to bottom - so a directive
    is never silently reordered or dropped here; the only thing that removes one
    is its own `enabled: false`. What IS refused is a stat the engine has no
    template for: the sweep would scroll the whole panel looking for a label
    that cannot match, find nothing, and report the run as "shopping complete".
    """
    if not isinstance(body, (list, dict)):
        return [f"{path}: must be a list of directives, or a mapping "
                f"{{enabled, directives}}"]
    out: list[str] = []
    if isinstance(body, dict):
        _check_keys(body, ("enabled", "directives", "label"), path, out)
        _check_label(body, path, out)
        _check_bool(body.get("enabled"), f"{path}.enabled", out)
        if body.get("directives") is not None and \
                not isinstance(body["directives"], list):
            return out + [f"{path}.directives: must be a list"]
    known = shop_stats()
    _, directives = _shopping_parts(body)
    for i, d in enumerate(directives):
        dpath = f"{path}.directives[{i}]" if isinstance(body, dict) \
            else f"{path}[{i}]"
        if not isinstance(d, dict):
            out.append(f"{dpath}: must be a mapping")
            continue
        _check_keys(d, DIRECTIVE_KEYS, dpath, out)
        _check_bool(d.get("enabled"), f"{dpath}.enabled", out)
        _check_pos_int(d.get("clicks"), f"{dpath}.clicks", out)
        tab = d.get("tab")
        if tab not in SHOP_TABS:
            out.append(f"{dpath}.tab: unknown tab {tab!r} "
                       f"(known: {', '.join(SHOP_TABS)})")
        mode = d.get("mode")
        if mode not in SHOP_MODES:
            out.append(f"{dpath}.mode: unknown mode {mode!r} "
                       f"(known: {', '.join(SHOP_MODES)})")
        elif mode == "clicks" and d.get("clicks") is None:
            out.append(f"{dpath}.clicks: required when mode is 'clicks'")
        elif mode != "clicks" and d.get("clicks") is not None:
            out.append(f"{dpath}.clicks: only meaningful when mode is "
                       f"'clicks' (mode here is {mode!r})")
        stats = d.get("stats")
        if not isinstance(stats, list) or not stats:
            out.append(f"{dpath}.stats: required, a non-empty list of stat "
                       f"names")
            continue
        for stat in stats:
            if stat not in known:
                out.append(f"{dpath}.stats: unknown stat {stat!r} - no "
                           f"templates/stats/{stat}.png, so the sweep could "
                           f"never find it (known: "
                           f"{', '.join(sorted(known)) or 'none'})")
    return out


def _validate_rescue_policy(path: str, body: dict, player: dict) -> list[str]:
    """Structural + vocabulary check on one rescue policy, plus the two hard
    refusals the schema calls out: a wall rule on an account with no wall, and
    wall+hp mixed in one policy (they are different bars with different
    thresholds - the watch reads ONE of them, so a mix is silently half dead)."""
    out: list[str] = []
    _check_keys(body, RESCUE_POLICY_KEYS, path, out)
    _check_label(body, path, out)
    _check_bool(body.get("end_sprint_after_sw"),
                f"{path}.end_sprint_after_sw", out)
    arm = body.get("arm")
    if arm is not None and not isinstance(arm, dict) and arm != "always":
        out.append(f"{path}.arm: expected 'always' or a mapping "
                   f"{{on, watch_sec, immunity_sec}}, got {arm!r}")
    if isinstance(arm, dict):
        _check_keys(arm, ARM_KEYS, f"{path}.arm", out)
        if arm.get("on") not in (None, "second_wind"):
            out.append(f"{path}.arm.on: unknown arm trigger {arm['on']!r} "
                       f"(known: second_wind)")
        _check_pos_num(arm.get("watch_sec"), f"{path}.arm.watch_sec", out)
        _check_pos_num(arm.get("immunity_sec"), f"{path}.arm.immunity_sec", out)
    bars_used: set[str] = set()
    uses_collapse = False
    rules = body.get("rules")
    if rules is not None and not isinstance(rules, list):
        out.append(f"{path}.rules: must be a list of {{when, do}} rules")
        return out
    for i, rule in enumerate(_rules_of(body)):
        rpath = f"{path}.rules[{i}]"
        if not isinstance(rule, dict):
            out.append(f"{rpath}: must be a mapping with 'when' and 'do'")
            continue
        _check_keys(rule, RULE_KEYS, rpath, out)
        if "when" not in rule:
            out.append(f"{rpath}: missing 'when'")
        if "do" not in rule:
            out.append(f"{rpath}: missing 'do'")
        # `repeat` is live from P4 on (it compiles to the rule's own `repeat`
        # flag), and `refire_sec` is its floor. Both are shapes, not switches:
        # a truthy string here would reach the interpreter as one.
        _check_bool(rule.get("repeat"), f"{rpath}.repeat", out)
        _check_pos_num(rule.get("refire_sec"), f"{rpath}.refire_sec", out)
        try:
            name, params = _trigger(rule.get("when"), rpath)
            if name == "bar":
                bars_used.add(params["bar"])
            if name == "wall_collapse":
                uses_collapse = True
            out += _check_trigger_values(name, params, rpath)
        except ProfileError as e:
            out.append(str(e))
        try:
            name, params = _action(rule.get("do"), rpath)
            out += _check_action_values(name, params, rpath)
        except ProfileError as e:
            out.append(str(e))

    # ---- what each tier can actually execute (see TIER_B_* above)
    try:
        for entry in _classify_rules(body, path):
            if entry["slot"] is None:
                out += _refuse_unsupported_tier_b(entry)
                out += _check_tier_b_params(entry)
                continue
            out += _check_tier_a_burst_gate(entry, bars_used)
            # `repeat` / `refire_sec` ARE TIER B FIELDS. The fast watch has no
            # per-rule bookkeeping at all - it re-decides from the hoisted
            # scalars on every sample and rate-limits through refire_guard_sec
            # (rescue) or nuke_on_fleet.throttle_sec (fleet). So on a Tier A
            # rule both compile to nothing, which is the trap this whole
            # function exists to shut.
            for field in ("repeat", "refire_sec"):
                if entry["rule"].get(field) is None:
                    continue
                out.append(
                    f"{entry['path']}.{field}: nothing compiles it on a Tier A "
                    f"'{entry['slot']}' rule - the wall watch re-decides every "
                    f"sample and rate-limits through "
                    f"{'refire_guard_sec' if entry['slot'] != 'fleet' else 'throttle_sec'}"
                    f". `{field}` is a main-loop (Tier B) field")
            # THE THRESHOLD NUKE SLOT READS A LEVEL, NOTHING ELSE. Only the
            # bar/BURST slot compiles falling_samples/deadband (they are the
            # wall watch's own sampling scalars); on a bar/nuke rule they would
            # validate and then be dropped - the accepted-but-ignored trap.
            if entry["slot"] == "bar_nuke":
                for param in ("falling_samples", "deadband"):
                    if entry["tparams"].get(param) is None:
                        continue
                    out.append(
                        f"{entry['path']}.when.bar.{param}: nothing compiles "
                        f"it on a threshold-nuke rule - the sampling scalars "
                        f"belong to the bar+`burst` rule, which is what the "
                        f"watch hoists them for")
            # Tier A: the wall watch runs it - but each slot compiles only the
            # `fire` params it has a home for. A param outside that list would
            # be validated and then dropped, which is the accepted-but-ignored
            # trap all over again.
            if entry["action"] != "fire":
                continue
            homed = _TIER_A_FIRE_PARAMS.get(entry["slot"], ())
            for param in ("throttle_sec", "require_ready", "refire_guard_sec"):
                if entry["aparams"].get(param) is None or param in homed:
                    continue
                out.append(
                    f"{entry['path']}.do.fire.{param}: nothing compiles it on "
                    f"a Tier A '{entry['slot']}' rule, so it would be dropped "
                    f"silently. Only these are compiled here: "
                    f"{', '.join(homed) or 'none'}")
    except ProfileError:
        pass                                    # already reported above

    # NOTE (P4): `arm: always` and an absent `arm` are both first-class now -
    # they mean NO Tier A, every rule evaluated by the main loop. The Second
    # Wind gate is about the sub-second WATCH, not about whether rules run at
    # all. A wall rule under `arm: always` is legal and is an OBSERVATION at
    # ~1s rather than a rescue; the compiled `latency` field says which.

    # THE ARMED-BUT-EMPTY WATCH. `arm.on: second_wind` is what opens the
    # sub-second wall watch, and that loop reads the threshold as a bare value
    # (`ext < dm_below`) on every sample. Only a `bar` rule with a `burst`
    # action fills it. So a policy that arms the watch and then gives it
    # nothing to compare against does not degrade - it raises TypeError inside
    # the rescue, at the exact moment the rescue was needed.
    armed = isinstance(arm, dict) and arm.get("on") == "second_wind"
    has_burst_bar = any(
        isinstance(r, dict)
        and isinstance(r.get("when"), dict) and "bar" in r["when"]
        and isinstance(r.get("do"), dict) and "burst" in r["do"]
        for r in _rules_of(body))
    if armed and not has_burst_bar:
        out.append(f"{path}: arms the post-Second-Wind watch "
                   f"(arm.on: second_wind) but has no `bar` rule with a "
                   f"`burst` action, so nothing sets dm_below - the watch "
                   f"would compare every wall sample against null")

    # THE WALL HAS TO EXIST. `collapse_from` is hoisted by _fast_wall_watch and
    # a Tier B wall rule reads detect.wall_overheal for itself - both are
    # meaningless on an account with no wall bar, and "no bar" does not read as
    # "empty bar", it reads as whatever else is in that ROI.
    if (("wall" in bars_used) or uses_collapse) and not player.get("wall"):
        out.append(f"{path}: watches the wall (`bar: wall` / `wall_collapse`), "
                   f"but the player has no wall (player.wall is not true) - "
                   f"there is no bar to watch")
    if bars_used == {"wall", "hp"}:
        out.append(f"{path}: mixes `bar: wall` and `bar: hp` in one policy - "
                   f"the rescue watches exactly one bar, pick one")
    return out


def _refuse_unsupported_tier_b(entry: dict) -> list[str]:
    """Refuse a Tier B rule the main-loop evaluator cannot execute.

    Never accept-and-ignore: the whole promise of a profile is that what it
    says is what runs. A rule the validator blessed, the dashboard listed and
    the evaluator has no branch for is worse than no rule at all, because the
    player stops watching for the thing it was supposed to handle.

    At P4 the interpreter runs the whole vocabulary, so what is left here is
    the one genuinely impossible COMBINATION - a battlefield action on the
    death screen - plus a guard against a future vocabulary word landing
    before its runner does.
    """
    out: list[str] = []
    path, trig, act = entry["path"], entry["trigger"], entry["action"]
    # THE TOTALITY GUARD. Both tables are the whole vocabulary at P4, so
    # neither branch fires today - and that is the point: the day a word is
    # added to TRIGGERS/ACTIONS before the interpreter can run it, this is what
    # refuses it instead of shipping a rule that never fires.
    if trig == "bar" and entry["tparams"].get("bar") not in TIER_B_BARS:
        out.append(f"{path}.when.bar: `bar: {entry['tparams'].get('bar')}` has "
                   f"no main-loop evaluator (main-loop bars: "
                   f"{', '.join(TIER_B_BARS)})")
    elif trig != "bar" and trig not in TIER_B_TRIGGERS:
        out.append(f"{path}.when.{trig}: has no main-loop evaluator, and this "
                   f"rule is not in a Tier A slot (main-loop triggers: "
                   f"{', '.join(TIER_B_TRIGGERS)})")
    if act not in TIER_B_ACTIONS:
        out.append(f"{path}.do.{act}: has no main-loop executor "
                   f"(main-loop actions: {', '.join(sorted(TIER_B_ACTIONS))})")
    # THE DEATH SCREEN IS NOT A BATTLEFIELD, AND IT IS NOT HOME EITHER. The
    # runtime refuses every action but `stop_after_run` there, so accepting one
    # here would compile a rule that validates, shows up in the dashboard, and
    # is retired with `rule_unsupported` the first time the player dies.
    if trig == "death_screen" and act not in DEATH_SCREEN_ACTIONS:
        out.append(f"{path}.do.{act}: cannot run on the death screen - "
                   f"{_DEATH_SCREEN_WHY.get(act, 'the stats dialog cannot run it')}"
                   f". Death rules may only "
                   f"{', '.join(f'`{a}`' for a in DEATH_SCREEN_ACTIONS)}")
    # ...AND A LIVE BATTLE IS NOT HOME. Same obstacle as the death-screen
    # refusal above, one screen earlier: orchestrator retires `switch_cards` the first
    # time it SEES the rule, in every phase, so a compiler that still accepted
    # it would ship a rule that renders, reads as configured and never runs.
    # This is a main-loop refusal because main-loop is where the rule would be:
    # no Tier A slot takes `switch_cards` (they take `burst` and `fire`), so
    # every switch_cards rule is a main_loop rule.
    elif act == "switch_cards":
        out.append(f"{path}.do.switch_cards: {NO_CARDS_ROUTE}")
    return out


def _check_tier_b_params(entry: dict) -> list[str]:
    """Params that HAVE a home in Tier A and none at Tier B.

    `require_match` is the whole list today: it means "only tap a Demon Mode
    glyph you can actually see", and the only site that can tap one it cannot
    see is the fast wall watch's fixed-coordinate fallback. A Tier B burst goes
    through `fire_button`, which has no fallback coordinate - so the flag would
    compile into a rule nothing consults.
    """
    out: list[str] = []
    if entry["action"] == "burst" and \
            entry["aparams"].get("require_match") is not None:
        out.append(f"{entry['path']}.do.burst.require_match: not read by a "
                   f"main-loop (Tier B) burst - it gates the WALL watch's "
                   f"fallback tap, and this rule is not in the wall watch. "
                   f"`require_ready` is the gate here")
    # RETAPS ARE A TIER A CONCEPT. The fast watch fires three instant taps off
    # one frame and confirms afterwards, because it cannot afford a read
    # between them; a main-loop burst goes through fire_button, which confirms
    # the tap itself. So `retaps: 5` at Tier B compiled a number nothing read
    # and quietly became one fire (Codex P4, MEDIUM).
    if entry["action"] == "burst" and \
            entry["aparams"].get("retaps") is not None:
        out.append(f"{entry['path']}.do.burst.retaps: not read by a main-loop "
                   f"(Tier B) burst - it fires through fire_button, which "
                   f"confirms its own tap. Retaps exist for the fast wall "
                   f"watch, which fires blind off one frame")
    # ...and the two ways of spelling one cooldown. `refire_sec` is the rule's
    # own floor; a `fire` action can say the same thing as throttle_sec /
    # refire_guard_sec. Ranking them silently would drop whichever lost.
    if entry["rule"].get("refire_sec") is not None and entry["action"] == "fire":
        for param in ("throttle_sec", "refire_guard_sec"):
            if entry["aparams"].get(param) is not None:
                out.append(
                    f"{entry['path']}.refire_sec: also sets do.fire.{param} - "
                    f"both compile to the SAME per-rule refire floor, so one "
                    f"of them would be dropped. Keep one")
    return out


def _check_tier_a_burst_gate(entry: dict, bars_used: set[str]) -> list[str]:
    """THE BURST'S TWO GATES, one live per SITE.

    A wall rescue runs the burst inside `_fast_wall_watch` as raw instant taps
    (no readiness test exists there, only "was the glyph matched"); an hp rescue
    runs the same rule through `fire_button` (a real readiness test, no fallback
    coordinate). Setting the gate that site does not use compiles a flag nothing
    reads - the accepted-but-ignored trap this module exists to abolish.
    """
    if entry["action"] != "burst" or entry["slot"] not in ("bar_burst",
                                                           "collapse"):
        return []
    wall = "wall" in bars_used
    gate = "require_ready" if wall else "require_match"
    if entry["aparams"].get(gate) is None:
        return []
    return [f"{entry['path']}.do.burst.{gate}: not read by a "
            f"{'wall' if wall else 'hp'}-bar rescue - "
            f"require_ready gates the hp-path Demon Mode, "
            f"require_match gates the wall burst's fallback tap"]


def _check_trigger_values(name: str, params: dict, path: str) -> list[str]:
    out: list[str] = []
    base = f"{path}.when.{name}"
    if name == "bar":
        _check_unit(params.get("below"), f"{base}.below", out)
        _check_pos_int(params.get("falling_samples"),
                       f"{base}.falling_samples", out)
        _check_unit(params.get("deadband"), f"{base}.deadband", out)
    elif name == "wall_collapse":
        _check_unit(params.get("from_above"), f"{base}.from_above", out)
    elif name == "fleet_mark":
        _check_pos_int(params.get("after_waves"), f"{base}.after_waves", out,
                       allow_zero=True)
        _check_pos_int(params.get("window_waves"), f"{base}.window_waves", out)
    elif name == "wave_at_least":
        _check_pos_int(params.get("value"), base, out)
    elif name == "wave_between":
        _check_range(params.get("value"), base, out, required=True)
    elif name == "second_wind":
        state = params.get("state")
        if state not in SW_STATES:
            out.append(f"{base}.state: unknown Second Wind state {state!r} "
                       f"(known: {', '.join(SW_STATES)})")
        _check_pos_int(params.get("min_procs"), f"{base}.min_procs", out)
    return out


def _check_action_values(name: str, params: dict, path: str) -> list[str]:
    out: list[str] = []
    base = f"{path}.do.{name}"
    if name == "burst":
        _check_bool(params.get("cancel_sprint"), f"{base}.cancel_sprint", out)
        _check_pos_int(params.get("retaps"), f"{base}.retaps", out)
        _check_bool(params.get("require_match"), f"{base}.require_match", out)
        _check_bool(params.get("require_ready"), f"{base}.require_ready", out)
    elif name == "fire":
        _check_bool(params.get("require_ready"), f"{base}.require_ready", out)
        _check_pos_num(params.get("throttle_sec"), f"{base}.throttle_sec", out)
        _check_pos_num(params.get("refire_guard_sec"),
                       f"{base}.refire_guard_sec", out)
    elif name == "toggle_uw":
        _check_bool(params.get("want_on"), f"{base}.want_on", out)
        if params.get("weapon") not in UW_NAMES:
            out.append(f"{base}.weapon: unknown ultimate weapon "
                       f"{params.get('weapon')!r} (known: "
                       f"{', '.join(UW_NAMES)})")
    return out


def _validate_blueprint(path: str, bp: dict, player: dict, uw_policies: dict,
                        rescue_policies: dict, gathers: dict,
                        shopping_lists: dict, owned: set[str]) -> list[str]:
    out: list[str] = []
    kind = bp.get("kind")
    if kind not in KINDS:
        out.append(f"{path}.kind: unknown kind {kind!r} "
                   f"(known: {', '.join(KINDS)})")
        return out                      # every later check keys off the kind

    legal = set(_COMMON_FIELDS) | set(_KIND_FIELDS[kind])
    for key in bp:
        if key not in legal:
            out.append(f"{path}.{key}: not a legal field for kind '{kind}' "
                       f"(legal: {', '.join(sorted(legal))})")

    # ---- loadout: must exist in the MACHINE file and be runnable there
    loadout = bp.get("loadout")
    loadouts = _d(CONFIG.get("loadouts"))
    if loadout is None:
        out.append(f"{path}.loadout: required - a run must know what to equip "
                   f"(or say `{LOADOUT_AS_IS}` to equip nothing)")
    elif loadout == LOADOUT_AS_IS:
        # "Change nothing" - legal only where the runner equips nothing by
        # itself. Elsewhere it would be overridden by that runner's own
        # default, which is the opposite of what it says.
        if kind != "coin":
            out.append(f"{path}.loadout: `{LOADOUT_AS_IS}` is only legal on a "
                       f"coin blueprint - a {kind!r} runner equips something "
                       f"of its own (tournament swaps, the quest scripts' "
                       f"fallback loadout), so it would not change nothing")
    elif loadout not in loadouts:
        out.append(f"{path}.loadout: unknown loadout {loadout!r} "
                   f"(config.yaml loadouts: {', '.join(sorted(loadouts))})")
    else:
        out += _validate_loadout_ownership(path, loadout,
                                           _d(loadouts[loadout]), player)

    # ---- tier
    tier = bp.get("tier")
    max_tier = player.get("max_tier")
    if tier is not None:
        if not _is_int(tier) or tier < 1:
            out.append(f"{path}.tier: must be an integer >= 1, got {tier!r}")
        elif _is_int(max_tier) and tier > max_tier:
            out.append(f"{path}.tier: tier {tier} is above the player's "
                       f"unlocked maximum (player.max_tier = {max_tier})")
    elif kind in ("coin", "shard"):
        out.append(f"{path}.tier: required for kind '{kind}' - the runner "
                   f"sets the tier from the home screen before every run")

    # ---- numeric shapes. Each of these reaches a consumer RAW: shop_interval
    # goes into `now - last > interval`, count/rides/cycles are str()'d into an
    # argv that argparse int()s, gem caps are compared against an OCR'd price.
    _check_pos_int(bp.get("count"), f"{path}.count", out, allow_zero=True)
    _check_pos_int(bp.get("shop_interval_sec"), f"{path}.shop_interval_sec",
                   out)
    _check_pos_int(bp.get("max_wave"), f"{path}.max_wave", out)
    dt = bp.get("dissonant_tab")
    if dt is not None and dt not in DISSONANT_TABS:
        out.append(f"{path}.dissonant_tab: unknown tab {dt!r} "
                   f"(known: {', '.join(DISSONANT_TABS)})")
    _check_pos_int(bp.get("rides"), f"{path}.rides", out)
    _check_pos_int(bp.get("cycles"), f"{path}.cycles", out)
    _check_pos_int(bp.get("reroll_at_wave"), f"{path}.reroll_at_wave", out)
    _check_pos_int(bp.get("ride_to_wave"), f"{path}.ride_to_wave", out)
    _check_pos_num(bp.get("cycle_sec"), f"{path}.cycle_sec", out)
    _check_bool(bp.get("restart_via_home"), f"{path}.restart_via_home", out)
    _check_bool(bp.get("cancel_sprint"), f"{path}.cancel_sprint", out)

    # `count` compiles to nothing on any kind but shard - the other runners
    # take their repeat count from their own field, and this one would sit in
    # the profile looking like it bounded the run.
    if kind != "shard" and "count" in bp:
        out.append(f"{path}.count: count is only consumed by shard blueprints "
                   f"({_COUNT_ALTERNATIVE.get(kind, 'not applicable here')})")

    # ---- policy references
    refs = _d(bp.get("policies"))
    for slot, table, label in (("uw", uw_policies, "policies.uw_policies"),
                               ("rescue", rescue_policies,
                                "policies.rescue_policies"),
                               ("gather", gathers, "policies.gather")):
        ref = refs.get(slot)
        if ref is None:
            continue
        if ref not in table:
            out.append(f"{path}.policies.{slot}: unknown policy {ref!r} "
                       f"(defined in {label}: "
                       f"{', '.join(sorted(table)) or 'none'})")
    for slot in refs:
        if slot not in ("uw", "rescue", "gather"):
            out.append(f"{path}.policies.{slot}: unknown policy slot "
                       f"(known: uw, rescue, gather)")

    shopping = bp.get("shopping")
    if shopping is not None and shopping not in shopping_lists:
        out.append(f"{path}.shopping: unknown shopping list {shopping!r} "
                   f"(defined in policies.shopping_lists: "
                   f"{', '.join(sorted(shopping_lists)) or 'none'})")

    # ---- kind-specific ownership gating
    if kind == "uw_grant_quest":
        targets = bp.get("grant_targets") or []
        if not targets:
            out.append(f"{path}.grant_targets: required for kind "
                       f"'uw_grant_quest' - the quest needs a target weapon")
        for uw in targets:
            if uw not in UW_NAMES:
                out.append(f"{path}.grant_targets: unknown ultimate weapon "
                           f"{uw!r}")
            elif uw in owned:
                out.append(f"{path}.grant_targets: the player already owns "
                           f"{uw!r} - there is nothing to be granted")
        # flows/quest_sm.py runs Smart-Missiles choreography end to end and logs
        # every grant as smart_missiles, so any other target would produce a
        # run that reports success for a weapon it never farmed.
        if targets and list(targets) != list(GRANT_TARGETS_SUPPORTED):
            out.append(f"{path}.grant_targets: only "
                       f"{list(GRANT_TARGETS_SUPPORTED)} is {P4} - flows/quest_sm.py "
                       f"follows Smart-Missiles choreography and reports every "
                       f"grant as smart_missiles")
        for uw, want in _d(bp.get("uw_setup")).items():
            if uw not in UW_NAMES:
                out.append(f"{path}.uw_setup.{uw}: unknown ultimate weapon")
                continue
            _check_bool(want, f"{path}.uw_setup.{uw}", out)
            if want and uw not in owned:
                out.append(f"{path}.uw_setup.{uw}: cannot switch on an "
                           f"ultimate weapon the player does not own")
    if kind == "cycle_quest" and bp.get("cycles") is None:
        out.append(f"{path}.cycles: required for kind 'cycle_quest'")
    if kind == "tournament":
        cap = bp.get("gem_entry_max")
        if cap is not None and (not _is_int(cap) or cap < 0):
            out.append(f"{path}.gem_entry_max: must be a non-negative integer "
                       f"number of gems (0 = never pay), got {cap!r}")
        out += _validate_in_run_actions(path, bp, player)

    # ---- ABILITIES THE RESCUE TAPS MUST BE OWNED.
    #
    # act.py taps Demon Mode at a FIXED COORDINATE when no glyph is found, so
    # an unowned ability is not a no-op - it is a blind tap on whatever else
    # occupies that slot, fired during a rescue, on a live tower. And an
    # ABSENT `player.abilities` section must read as "unknown, refuse", never
    # "unknown, permit": permitting is exactly how a blind tap ships.
    rescue = rescue_policies.get(refs.get("rescue"))
    have_abilities = player.get("abilities")
    if rescue is not None and not isinstance(have_abilities, dict):
        out.append(f"{path}.policies.rescue: the profile has no "
                   f"`player.abilities` section, so the abilities this rescue "
                   f"taps cannot be verified - run scan.py (an unowned ability "
                   f"is tapped at a fixed coordinate, not skipped)")
        have_abilities = None
    # ...AND THE SECTION MUST BE EVIDENCE, NOT AN ASSUMPTION. The migrator
    # writes `abilities: {nuke: true, demon_mode: true}` because every loadout
    # in config.yaml implies them - it has not looked at the account. A
    # fabricated `true` is indistinguishable from a scanned one at this level,
    # so ownership is only honoured once something has actually checked
    # (Codex round 2, #1). Unverified + a rescue that taps = refuse.
    elif rescue is not None and player.get("abilities_verified") is not True:
        out.append(f"{path}.policies.rescue: ability ownership unverified - "
                   f"run `scan.py --battle`, or set "
                   f"`player.abilities_verified: true` after confirming in the "
                   f"dashboard. Until then a rescue may tap a fixed coordinate "
                   f"for an ability the account does not have")
        have_abilities = None
    for entry_path, ability in _abilities_used(_d(rescue),
                                               f"{path}.policies.rescue"):
        if have_abilities is None:
            break
        if not have_abilities.get(ability):
            out.append(f"{entry_path}: fires {ability!r}, which the player "
                       f"does not have (player.abilities.{ability} is not "
                       f"true)")

    # ---- weapons a rule toggles must be on the account
    for entry_path, uw in _uws_used(_d(rescue), f"{path}.policies.rescue"):
        if uw not in owned:
            out.append(f"{entry_path}: toggles {uw!r}, which the player does "
                       f"not own (player.uws.{uw} is not true) - the UW panel "
                       f"has no row for it")

    # ---- A TOURNAMENT RUN IS NEVER CANCELLED. Ticket purchase auto-starts the
    # run and the gem cost escalates 10 -> 20 -> 30, so a surrender is real
    # money. tourney.end_round and shard.abandon_run both carry this guard at
    # their chokepoints; refusing the rule outright is the third lock, and the
    # only one that fires before a tap rather than at it.
    if kind == "tournament":
        for i, rule in enumerate(_rules_of(_d(rescue))):
            do = _d(rule).get("do")
            if isinstance(do, dict) and "surrender_retry" in do:
                out.append(f"{path}.policies.rescue -> rules[{i}]: "
                           f"`surrender_retry` on a TOURNAMENT blueprint - a "
                           f"tournament run is never cancelled (the entry is "
                           f"already paid for and the next one costs more). "
                           f"Use a rescue policy without it here")

    # ---- cards named by rescue rules must exist on the account
    for i, rule in enumerate(_rules_of(_d(rescue))):
        do = _d(rule).get("do")
        if isinstance(do, dict) and isinstance(do.get("switch_cards"), dict):
            want = do["switch_cards"].get("preset")
            have = player.get("card_presets")
            if isinstance(have, list) and want not in have:
                out.append(f"{path}.policies.rescue -> switch_cards: card "
                           f"preset {want!r} is not on the account "
                           f"(player.card_presets)")
    return out


def _validate_in_run_actions(path: str, bp: dict, player: dict) -> list[str]:
    """Tournament `in_run_actions` (P6) - an ORDERED schedule of card swaps
    inside one run: `[{at_wave: 1500, switch_cards: "Tourney P2"}]`.

    THE V1 VOCABULARY IS ONE ACTION KIND and this is not a placeholder for a
    generic one. Everything else a rule can do already has a home in the rescue
    rules, which are evaluated against the screen; an in-run action is
    evaluated against the WAVE COUNTER alone, fires blind at a number, and
    walks the game off the battle screen to do it (orchestrator.run_in_run_actions).
    A card swap is worth that. Nothing else here has been asked to be.

    ASCENDING, STRICTLY. The runtime walks the list in order and fires one per
    pass, so a later action with an earlier wave can only fire after the one
    above it has - i.e. it is either a dead entry or a swap that happens at the
    wrong wave, and neither is what the author wrote down. Equal waves are the
    same trap: two swaps at the same number is one deck the run never sees.

    THE PRESET MUST BE ON THE ACCOUNT, and a missing inventory is a refusal,
    not a pass - the same ruling `switch_cards` already carries in the rules.
    loadout.apply_cards navigates the card menus and matches a name; a name
    that is not there leaves the run parked in a menu until the retry gives up.
    """
    actions = bp.get("in_run_actions")
    if actions is None:
        return []
    out: list[str] = []
    if not isinstance(actions, list):
        out.append(f"{path}.in_run_actions: must be a LIST of "
                   f"{{at_wave, switch_cards}} - the order IS the schedule")
        return out
    if not actions:
        # THE EMPTY LIST IS LEGAL AND STAYS LEGAL. It is what "the key is
        # supported, this blueprint schedules nothing" looks like, it is what
        # every tournament preset compiles to today, and keeping it accepted is
        # what makes turning the feature on a one-function change rather than a
        # format migration.
        return out
    # ...AND A NON-EMPTY ONE IS REFUSED, for the same reason the runtime
    # refuses to execute it. Everything below still runs: an author who wrote a
    # schedule gets it fully diagnosed rather than dismissed, so the list is
    # correct on the day the route exists.
    out.append(f"{path}.in_run_actions: {NO_CARDS_ROUTE}")
    if len(actions) > IN_RUN_ACTIONS_MAX:
        out.append(f"{path}.in_run_actions: {len(actions)} actions, and v1 "
                   f"takes at most {IN_RUN_ACTIONS_MAX} - each one leaves the "
                   f"battle screen for the card menus and comes back, so two "
                   f"is a swap and a swap-back; more is a schedule, and a "
                   f"schedule belongs in the plan")
    have = player.get("card_presets")
    last: int | None = None
    for i, item in enumerate(actions):
        ipath = f"{path}.in_run_actions[{i}]"
        if not isinstance(item, dict):
            out.append(f"{ipath}: must be a mapping "
                       f"({{at_wave: <wave>, switch_cards: <preset name>}}), "
                       f"got {item!r}")
            continue
        # `do:` is the RULES spelling, one section up in the schema, and it is
        # the mistake an author who just read that section will make. Name it
        # rather than reporting an unknown key and letting them hunt.
        if "do" in item:
            out.append(f"{ipath}.do: in-run actions are not rescue rules - "
                       f"there is no `do` block here. Write the action flat: "
                       f"`{{at_wave: <wave>, switch_cards: <preset name>}}`")
        _check_keys({k: v for k, v in item.items() if k != "do"},
                    IN_RUN_ACTION_KEYS, ipath, out)

        wave = item.get("at_wave")
        if wave is None:
            out.append(f"{ipath}.at_wave: required - an in-run action fires on "
                       f"the wave counter and nothing else")
        else:
            before = len(out)
            _check_pos_int(wave, f"{ipath}.at_wave", out)
            if len(out) == before:
                if last is not None and wave <= last:
                    out.append(f"{ipath}.at_wave: {wave} is not above the "
                               f"previous action's {last} - the runtime walks "
                               f"this list IN ORDER, so an out-of-order wave "
                               f"either never fires or fires at the wrong one")
                else:
                    last = wave

        cards = item.get("switch_cards")
        if cards is None:
            out.append(f"{ipath}.switch_cards: required - it is the only "
                       f"in-run action v1 knows "
                       f"(known: {', '.join(IN_RUN_ACTIONS)})")
        elif not isinstance(cards, str) or not cards:
            out.append(f"{ipath}.switch_cards: must be the NAME of a card "
                       f"preset on the account, got {cards!r}")
        elif not isinstance(have, list):
            out.append(f"{ipath}.switch_cards: this profile has no "
                       f"`player.card_presets` inventory to check {cards!r} "
                       f"against - run scan.py (a name that is not there parks "
                       f"the run in the card menus mid-tournament)")
        elif cards not in have:
            out.append(f"{ipath}.switch_cards: card preset {cards!r} is not on "
                       f"the account (player.card_presets: "
                       f"{', '.join(have) or 'none'})")
    return out


def _uws_used(policy: dict, path: str) -> list[tuple[str, str]]:
    """(rule path, weapon) for every `toggle_uw` in a rescue policy. The UW
    panel has no row for a weapon the account does not own, so the toggle
    would scroll the panel, match nothing and report success."""
    out: list[tuple[str, str]] = []
    for i, rule in enumerate(_rules_of(policy)):
        do = _d(rule).get("do")
        body = do.get("toggle_uw") if isinstance(do, dict) else None
        name = body.get("weapon") if isinstance(body, dict) else None
        if name in UW_NAMES:
            out.append((f"{path}.rules[{i}].do.toggle_uw", name))
    return out


def _abilities_used(policy: dict, path: str) -> list[tuple[str, str]]:
    """(rule path, ability name) for every `fire`/`burst` in a rescue policy."""
    out: list[tuple[str, str]] = []
    for i, rule in enumerate(_rules_of(policy)):
        do = _d(rule).get("do")
        if not isinstance(do, dict):
            continue
        for action, key in (("fire", "button"), ("burst", "fire")):
            body = do.get(action)
            name = body.get(key) if isinstance(body, dict) else None
            if name in BUTTONS:
                out.append((f"{path}.rules[{i}].do.{action}", name))
    return out


def _validate_loadout_ownership(path: str, name: str, loadout: dict,
                                player: dict) -> list[str]:
    """The gating that pays for this whole module: a loadout naming cards,
    guardians or modules the account does not have costs a full run to discover
    at runtime (loadout.apply() taps at a tile that is not there and the run
    starts with the wrong build). Checked here, before anything launches.

    A MISSING INVENTORY IS A REFUSAL, NOT A PASS. "Unknown, so permit" was the
    original reading and it is backwards: the contract says loadout contents
    "must be in player.*", and an unscanned account is precisely the one where
    a wrong tap is most likely. The fix is one scan.py run, and the message
    says so.
    """
    out: list[str] = []
    if loadout.get("defined") is False:
        out.append(f"{path}.loadout: loadout {name!r} is a placeholder "
                   f"(defined: false in config.yaml) - it equips nothing")
        return out

    # ---- v29 preset bodies ------------------------------------------------
    gp = loadout.get("global_preset")
    if gp:
        # A global preset already carries all five categories - anything else
        # in the same body would be silently ignored by the game at battle
        # entry, and silently-ignored keys are exactly what this validator
        # exists to refuse.
        extras = sorted(set(loadout) & {"cards", "cards_restore",
                                        "guardians", "modules",
                                        "modules_restore",
                                        "module_preset", "guardian_preset",
                                        "workshop_preset", "bot_preset"})
        if extras:
            out.append(f"{path}.loadout: loadout {name!r} mixes global_preset "
                       f"with {', '.join(extras)} - a global preset already "
                       f"carries all five categories, the extra keys would be "
                       f"wiped at battle entry")
        have_gp = player.get("global_presets")
        if not isinstance(have_gp, list) or not have_gp:
            out.append(f"{path}.loadout: loadout {name!r} selects global "
                       f"preset {gp!r} but player.global_presets is empty - "
                       f"either the Global Presets lab is not researched or "
                       f"the account was never scanned for preset names")
        elif gp not in have_gp:
            out.append(f"{path}.loadout: loadout {name!r} wants global preset "
                       f"{gp!r}, which is not on the account "
                       f"(player.global_presets: {', '.join(have_gp)})")
        return out

    cat_presets = player.get("category_presets") or {}
    for cat_key, cat in (("module_preset", "modules"),
                         ("guardian_preset", "guardians"),
                         ("workshop_preset", "workshop"),
                         ("bot_preset", "bots")):
        want = loadout.get(cat_key)
        if not want:
            continue
        have = cat_presets.get(cat)
        if not isinstance(have, list) or not have:
            out.append(f"{path}.loadout: loadout {name!r} selects {cat} "
                       f"preset {want!r} but player.category_presets.{cat} "
                       f"is empty - the {cat} preset lab is not researched "
                       f"or was never recorded")
        elif want not in have:
            out.append(f"{path}.loadout: loadout {name!r} wants {cat} preset "
                       f"{want!r}, which is not on the account "
                       f"(player.category_presets.{cat}: {', '.join(have)})")

    # THE CORRUPTION EXPOSURE (v29) is reported by warnings(), not refused
    # here: category presets auto-save mutations and nothing restores their
    # contents, so a manual equipment list rewrites whichever preset is
    # selected. A loadout that pairs the manual list with an explicit
    # <category>_preset selection has already answered the question (the
    # named preset IS the scratch the mutations land in - loadout.apply
    # selects it first); one that doesn't gets a standing advisory. It stays
    # advisory because refusing would brick existing profiles over loadouts
    # that are not even scheduled - see loadout_corruption_warnings().

    needs = []
    if loadout.get("cards"):
        needs.append("card_presets")
    if isinstance(loadout.get("guardians"), list):
        needs.append("guardians")
    if loadout.get("modules"):
        needs += ["modules_equipped", "modules_in_grid"]
    missing = [k for k in needs if not isinstance(player.get(k), list)]
    if missing:
        out.append(f"{path}.loadout: loadout {name!r} equips things this "
                   f"profile cannot verify - player.{', player.'.join(missing)}"
                   f" {'is' if len(missing) == 1 else 'are'} missing. Run "
                   f"scan.py to seed the inventory")
        return out

    have_cards = player.get("card_presets")
    # `cards_restore` (v29): the deck a shard/quest block re-selects when it
    # ends, so the cards screen never lingers on a specialized preset - same
    # ownership rule as `cards`.
    for key in ("cards", "cards_restore"):
        cards = loadout.get(key)
        if cards and isinstance(have_cards, list) and cards not in have_cards:
            out.append(f"{path}.loadout: loadout {name!r} wants card preset "
                       f"{cards!r} ({key}), which is not on the account "
                       f"(player.card_presets: {', '.join(have_cards)})")

    guardians = loadout.get("guardians")
    have_guardians = player.get("guardians")
    # `guardians: true` means "whatever is already equipped is fine" - nothing
    # to own-check.
    if isinstance(guardians, list) and isinstance(have_guardians, list):
        for g in guardians:
            if g not in have_guardians:
                out.append(f"{path}.loadout: loadout {name!r} wants guardian "
                           f"{g!r}, which the player does not have "
                           f"(player.guardians)")

    have_modules = None
    if isinstance(player.get("modules_equipped"), list) or \
            isinstance(player.get("modules_in_grid"), list):
        have_modules = set(player.get("modules_equipped") or []) | \
            set(player.get("modules_in_grid") or [])
    # `modules_restore` (v29): the module(s) a quest re-equips after its run
    # to undo the displacement its `modules` equip caused - same ownership
    # rule as `modules`.
    for entry in (list(loadout.get("modules") or [])
                  + list(loadout.get("modules_restore") or [])):
        slug = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
        if have_modules is not None and slug not in have_modules:
            out.append(f"{path}.loadout: loadout {name!r} wants module "
                       f"{slug!r}, which the player does not have "
                       f"(player.modules_equipped / modules_in_grid)")
    return out


def _hhmm(value, path: str, out: list[str]) -> int | None:
    """"HH:MM" -> minutes since local midnight. None when unset.

    A CLOCK IS NOT A STRING AT RUNTIME. The scheduler compares it against
    `now` on every poll, so it is parsed once here; "8:00" and "08:00" are the
    same minute, "24:00" is not a time of day (the day ends at 23:59, and a
    block that wants "the rest of the day" simply omits `until`).
    """
    if value is None:
        return None
    if not isinstance(value, str) or ":" not in value:
        out.append(f"{path}: must be a \"HH:MM\" time of day, got {value!r}")
        return None
    hh, _, mm = value.partition(":")
    if not hh.isdigit() or not mm.isdigit() or len(mm) != 2:
        out.append(f"{path}: must be a \"HH:MM\" time of day, got {value!r}")
        return None
    hours, minutes = int(hh), int(mm)
    if hours > 23 or minutes > 59:
        out.append(f"{path}: {value!r} is not a time of day "
                   f"(00:00 to 23:59; omit `until` for 'rest of the day')")
        return None
    return hours * 60 + minutes


EMPTY_PLAN = ("a plan with no blocks schedules nothing - remove the plan "
              "section to use the legacy constants, or add blocks")


def _plan_is_empty(plan: dict) -> bool:
    """True when this plan resolves to NO BLOCK on any day of the week.

    Every shape of nothing lands here, because they are all the same nothing:
    an empty `plan: {}`, a `days:` with no day plans, day plans that are all
    empty lists, and a `week:` whose references resolve to none of them. The
    check is the RESOLUTION, not the spelling - that is the only way to catch
    the last one.
    """
    days = _d(plan.get("days"))
    week = _d(plan.get("week"))
    for name in WEEKDAYS:
        blocks = days.get(week.get(name, week.get("default")))
        if isinstance(blocks, list) and blocks:
            return False
    return True


def _validate_plan(plan: dict, blueprints: dict) -> list[str]:
    out: list[str] = []
    _check_keys(plan, ("week", "days"), "plan", out)
    # AN EMPTY PLAN IS THE ONE STATE NOBODY CAN ACT ON. A missing plan means
    # "use the constants" and a plan with blocks means "use the plan"; a plan
    # that resolves to nothing means neither, and a scheduler handed nothing
    # either idles a tower all day or quietly falls back to constants the
    # player thought they had replaced. Both are worse than a refusal at load.
    if _plan_is_empty(plan):
        out.append(f"plan: {EMPTY_PLAN}")
        return out                      # every check below is per block
    days = _d(plan.get("days"))
    if plan.get("days") is not None and not isinstance(plan.get("days"), dict):
        out.append("plan.days: must be a mapping of day-plan name -> blocks")
    for day, blocks in days.items():
        if not isinstance(blocks, list):
            out.append(f"plan.days.{day}: must be a list of blocks")
            continue
        if not blocks:
            out.append(f"plan.days.{day}: empty - a day with no block is a day "
                       f"the scheduler has nothing to run")
        tourney_at: list[int] = []
        for i, block in enumerate(blocks):
            bpath = f"plan.days.{day}[{i}]"
            if not isinstance(block, dict):
                out.append(f"{bpath}: must be a mapping")
                continue
            _check_keys(block, BLOCK_KEYS, bpath, out)
            ref = block.get("blueprint")
            kind = None
            if ref is None:
                out.append(f"{bpath}.blueprint: required")
            elif ref not in blueprints:
                out.append(f"{bpath}.blueprint: unknown blueprint {ref!r} "
                           f"(defined: {', '.join(sorted(blueprints)) or 'none'})")
            else:
                kind = _d(blueprints[ref]).get("kind")
                out += _check_block_kind(bpath, block.get("block"), kind)

            # ---- the clock gate
            after = _hhmm(block.get("after"), f"{bpath}.after", out)
            until = _hhmm(block.get("until"), f"{bpath}.until", out)
            if after is not None and until is not None and until <= after:
                out.append(f"{bpath}: until {block['until']!r} is not after "
                           f"{block['after']!r} - the window never opens. A "
                           f"block that runs past midnight belongs to both "
                           f"days, so write it in both")

            # ---- runs per day. THIS IS THE PLAN-LEVEL `count`, which is a
            # different field from the blueprint-level one (that is flows/shard.py's
            # --loops). Here it means "how many runs of this block per day",
            # it is persisted per block id so an aborted day resumes, and it is
            # legal on every kind - which is what lets a plan say "100 shard
            # runs, then coin for the rest of the day".
            _check_pos_int(block.get("count"), f"{bpath}.count", out)
            if kind == "tournament":
                tourney_at.append(i)
                if block.get("count") not in (None, TOURNEY_RUNS_PER_DAY):
                    out.append(f"{bpath}.count: a tournament block is "
                               f"{TOURNEY_RUNS_PER_DAY} entry per day and "
                               f"cannot be raised - the ticket purchase "
                               f"auto-starts the run and the gem cost "
                               f"escalates 10 -> 20 -> 30 "
                               f"(got {block['count']!r})")

        # ONE TOURNAMENT BLOCK PER DAY, not one entry per block (Codex P5,
        # CRITICAL). Capping `count` at 1 caps each block on its own, so two
        # tournament blocks in a day - a second window at 21:00, a copy left
        # behind by an edit - are two paid entries, and the second costs more
        # than the first. The cap has to be counted over the DAY.
        if len(tourney_at) > 1:
            out.append(f"plan.days.{day}: {len(tourney_at)} tournament blocks "
                       f"in one day (indexes {', '.join(map(str, tourney_at))})"
                       f" - a day enters ONE tournament. Each would buy its own "
                       f"ticket, and the gem cost escalates 10 -> 20 -> 30")

    week = _d(plan.get("week"))
    if plan.get("week") is not None and not isinstance(plan.get("week"), dict):
        out.append("plan.week: must be a mapping of day -> day-plan name")
    _check_keys(week, WEEK_KEYS, "plan.week", out)
    for slot, day in week.items():
        if isinstance(slot, bool):
            continue                    # already reported by _check_keys
        if day not in days:
            out.append(f"plan.week.{slot}: unknown day plan {day!r} "
                       f"(defined in plan.days: "
                       f"{', '.join(sorted(days)) or 'none'})")
    # EVERY WEEKDAY NEEDS A PLAN. Without `default`, a day nobody named has no
    # blocks at all, and "the scheduler has nothing to run today" is not a
    # state anything downstream is written to survive.
    if days and "default" not in week:
        missing = [a for a in WEEK_KEYS[1:] if a not in week]
        if missing:
            out.append(f"plan.week: no `default` day plan, and "
                       f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
                       f"unnamed - those days would have nothing to run")
    return out


def _plan_warnings(plan: dict) -> list[str]:
    """Blocks and day plans that are legal, compiled, and can never run.

    A dead BLOCK is not a dead rescue rule: nothing fires late, nothing is
    missed, the day simply runs the block above it. So this is an advisory
    rather than a refusal - but it is still written down, because "why is my
    tournament not entering" is otherwise answered by reading the whole file.
    """
    out: list[str] = []
    for day, blocks in _d(plan.get("days")).items():
        seen_open = None
        for i, block in enumerate(blocks if isinstance(blocks, list) else []):
            if not isinstance(block, dict):
                continue
            if seen_open is not None:
                out.append(f"plan.days.{day}[{i}]: can never run - block "
                           f"[{seen_open}] above it has no `after`, no `until` "
                           f"and no `count`, so it is eligible at every minute "
                           f"of the day and always wins (order is priority)")
                continue
            if not any(block.get(k) is not None
                       for k in ("after", "until", "count")):
                seen_open = i
    referenced = set(_d(plan.get("week")).values())
    for day in _d(plan.get("days")):
        if referenced and day not in referenced:
            out.append(f"plan.days.{day}: never referenced by plan.week - it "
                       f"is written down but no day of the week runs it")
    return out


def _check_block_kind(path: str, block: str | None, kind: str | None) -> list[str]:
    """A block names what the scheduler thinks it is launching. Existence is
    not enough: a `tournament` block pointing at a shard blueprint validates,
    and then runs shard farming at 19:00 on a Wednesday instead of entering the
    tournament (Codex round 2, #5)."""
    if block is None:
        return [f"{path}.block: required - it is what the scheduler matches on"]
    allowed = BLOCK_KINDS.get(block)
    if allowed is None and block.startswith("quest"):
        allowed = _QUEST_BLOCK_KINDS
    if allowed is None:
        return [f"{path}.block: unknown block {block!r} (known: "
                f"{', '.join(sorted(BLOCK_KINDS))}, or a 'quest*' block)"]
    if kind not in allowed:
        return [f"{path}: block {block!r} runs {'/'.join(allowed)} blueprints, "
                f"but this one is kind {kind!r}"]
    return []


# -------------------------------------------------------------- compile plan

def compile_plan(profile: dict) -> dict | None:
    """The `plan` section -> the day schedule the runtime walks. THE CONTRACT
    WITH combo.due() (profiles/SCHEMA.md, "plan" -> compiled shape).

    RESOLVED PER WEEKDAY, not per day-plan name: `week` and `days` are how a
    human avoids writing the same day seven times, and neither is something a
    scheduler should have to dereference at 03:00 on a poll. So the output is
    seven ordered lists, keyed by the weekday names in `datetime.weekday()`
    order - no mapping table, no locale-dependent strftime.

    ORDER IS PRIORITY. The runtime takes the FIRST block whose window is open
    and whose count is not spent; a block with no window and no count is
    eligible at every minute, so the last such block is the day's filler.
    That reproduces combo's hand-written ladder exactly: the tournament
    outranks the shard block because it is the one thing with a closing
    window, and coin catches everything else.

    Every key is explicit on every block, including the ones the profile left
    out - THE RUNTIME APPLIES NO DEFAULTS, same rule as the Tier B rules.

    THE RETURN IS THE TRI-STATE, one value per state, and there is no fourth:

        no `plan` section        -> None        (absence, propagated)
        plan that resolves empty -> ProfileError
        plan with blocks         -> {"week": {...}}

    ABSENCE PROPAGATES AS ABSENCE (Codex P5c, row A). This used to answer a
    rules-only profile with an all-empty week, which is indistinguishable from
    a plan that was authored and came out empty - and the runtime is right to
    treat that as a defective artefact and HOLD. So a rules-only profile idled
    the farm instead of running the constants it never meant to replace. An
    empty week dict is therefore not a legal return value at all: the caller
    that gets a dict knows every day was resolved, and the caller that gets
    None knows there was nothing to resolve.
    """
    if profile.get("plan") is None:
        return None
    plan = _d(profile.get("plan"))
    if _plan_is_empty(plan):
        raise ProfileError(f"plan: {EMPTY_PLAN}")
    blueprints = _d(profile.get("blueprints"))
    days = _d(plan.get("days"))
    week = _d(plan.get("week"))
    out: dict[str, list[dict]] = {}
    for index, name in enumerate(WEEKDAYS):
        abbr = WEEK_KEYS[1 + index]
        day_plan = week.get(abbr, week.get("default"))
        blocks = days.get(day_plan)
        compiled = []
        for i, block in enumerate(blocks if isinstance(blocks, list) else []):
            block = _d(block)
            ref = block.get("blueprint")
            kind = _d(blueprints.get(ref)).get("kind")
            count = block.get("count")
            if kind == "tournament":
                # Never a default anyone may raise: one entry, always.
                count = TOURNEY_RUNS_PER_DAY
            # `is None`, never `or`: 00:00 is a legitimate `after` and would be
            # restored to the default by a truthiness test. Same rule as the
            # fast watch's hoist (a compiled 0 is a value, not a gap).
            after_min = _hhmm(block.get("after"), "", [])
            until_min = _hhmm(block.get("until"), "", [])
            compiled.append({
                # STABLE, and stable is the whole point: this is the daystate
                # key the per-block counter is written under, so it must not
                # move when an unrelated day is edited. Weekday + position.
                "id": f"{name}#{i}",
                "day_plan": day_plan,
                "block": block.get("block"),
                "blueprint": ref,
                # What to launch. `runner`/`runner_args` deliberately live on
                # the compiled PRESET and are not copied here - one source.
                "preset": f"bp_{ref}" if ref else None,
                "kind": kind,
                # Minutes since local midnight, parsed once. `until` is
                # EXCLUSIVE, so 08:00-19:00 and 19:00-... do not both own 19:00.
                # THESE TWO ARE AUTHORITATIVE.
                "after_min": 0 if after_min is None else after_min,
                "until_min": (DAY_MINUTES if until_min is None
                              else until_min),
                # ...and these two are the SOURCE ECHO, for logs, the
                # dashboard, and a scheduler that would rather read a clock
                # than an integer. Derived here from the same field, in the
                # same place, and pinned equal by test - never authored twice.
                # A decision must be made on the minutes: `after: null` means
                # "from midnight", and resolving that at runtime would be the
                # runtime applying a default, which is the one thing this
                # compiled shape exists to abolish.
                "after": block.get("after"),
                "until": block.get("until"),
                # null = unbounded, which is what makes a filler a filler.
                "count": count,
            })
        # THE PER-DAY TOURNAMENT CAP, enforced here too. validate() reports it,
        # but compile_plan() is reachable without validate() (the dashboard
        # previews a plan, materialize() compiles one), and two tournament
        # blocks on one weekday are two paid entries at escalating cost. Same
        # ruling as `surrender_retry` on a tournament blueprint: not a
        # validation opinion, an UNCONSTRUCTIBLE artefact.
        entries = [b["id"] for b in compiled if b["kind"] == "tournament"]
        if len(entries) > 1:
            raise ProfileError(
                f"plan: {name} has {len(entries)} tournament blocks "
                f"({', '.join(entries)}) - a day enters ONE tournament. Each "
                f"would buy its own ticket, and the gem cost escalates "
                f"10 -> 20 -> 30")
        out[name] = compiled
    return {"week": out}


# ------------------------------------------------------------------ compile

def compile_preset(profile: dict, blueprint_name: str) -> dict:
    """One blueprint -> one FLAT preset dict, shaped exactly like the RETURN
    value of `orchestrator.preset()` (post-`base:` merge, no inheritance key left).

    The whole translation lives here so that no consumer ever has to know both
    languages. orchestrator.py keeps reading `preset()["abilities"]["dm_below"]`; the
    player keeps writing "if the wall falls, burst". Nothing in between
    interprets a profile at runtime - by the time the loop starts, a profile is
    indistinguishable from a hand-written preset.
    """
    blueprints = _d(profile.get("blueprints"))
    if blueprint_name not in blueprints:
        raise ProfileError(f"blueprints.{blueprint_name}: no such blueprint "
                           f"(have: {', '.join(sorted(blueprints)) or 'none'})")
    bp = _d(blueprints[blueprint_name])
    kind = bp.get("kind")
    if kind not in KINDS:
        raise ProfileError(f"blueprints.{blueprint_name}.kind: unknown kind "
                           f"{kind!r} (known: {', '.join(KINDS)})")

    policies = _d(profile.get("policies"))
    refs = _d(bp.get("policies"))
    uw_policy = _d(_d(policies.get("uw_policies")).get(refs.get("uw")))
    rescue = _d(_d(policies.get("rescue_policies")).get(refs.get("rescue")))
    gather = _d(_d(policies.get("gather")).get(refs.get("gather")))
    shopping = _d(policies.get("shopping_lists")).get(bp.get("shopping"))

    # ---- A TOURNAMENT RUN IS NEVER CANCELLED, AND THE LOCK IS HERE TOO.
    # validate() reports this, but compile_preset() is reachable without it -
    # the dashboard previews a blueprint, materialize() compiles every one of
    # them, a test constructs one by hand - and a forbidden rule that exists in
    # CONFIG["presets"] is a forbidden rule the interpreter will be handed
    # (Codex P4, HIGH). The ticket auto-starts the run and the next entry costs
    # more, so this one is not a validation opinion: it is unconstructible.
    if kind == "tournament":
        for i, rule in enumerate(_rules_of(rescue)):
            do = _d(rule).get("do")
            if isinstance(do, dict) and "surrender_retry" in do:
                raise ProfileError(
                    f"blueprints.{blueprint_name}: rescue policy "
                    f"{refs.get('rescue')!r} rule {i} would surrender a "
                    f"TOURNAMENT run (`surrender_retry` on a tournament "
                    f"blueprint). A tournament run is never cancelled - the "
                    f"entry is already paid for and the next one costs more")

    # ---- AND NO SCHEDULE WALKS TO THE CARDS SCREEN. Same shape of lock as the
    # one above and the same reason it is here rather than only in validate():
    # compile_preset() is reachable without validation - the dashboard previews
    # a blueprint, materialize() compiles every one - and an artefact in
    # CONFIG["presets"] is an artefact the runtime will be handed. The runtime
    # would refuse it, once per run, in the log; refusing it HERE means the
    # profile never loads with a schedule its author believes in.
    #
    # The EMPTY list is not this. It compiles, as [], on every tournament
    # preset - the key stays wired end to end.
    if bp.get("in_run_actions"):
        raise ProfileError(
            f"blueprints.{blueprint_name}.in_run_actions: {NO_CARDS_ROUTE}")

    abilities, rules = _compile_rescue(
        rescue, f"blueprints.{blueprint_name}.policies.rescue",
        refs.get("rescue") or "rescue")

    # KEEP THIS DICT MINIMAL. tools/verify_profile.py deep-diffs it against the
    # merged config.yaml preset and every key with no old counterpart has to be
    # justified - which is the right pressure, because a compiled preset is
    # something orchestrator.py reads by key, not a place to park blueprint metadata.
    # `kind` and `loadout` earn their place: the tray and the scheduler pick a
    # preset out of CONFIG["presets"] and must know what to equip and which
    # runner it implies WITHOUT reaching back into the profile. Pure defaults
    # (`defined`, an unset `cancel_sprint`) do not - absent already means them.
    out = {
        "label": bp.get("label") or blueprint_name.replace("_", " ").title(),
        "kind": kind,
        # `as_is` compiles to NULL, not to the word: the compiled key is "a
        # config.yaml loadouts key", and a sentinel string sitting there would
        # be looked up by the first consumer that trusts the field. Null is
        # what "nothing to equip" has always looked like downstream (the quest
        # runners already read `.get("loadout") or <default>`).
        "loadout": (None if bp.get("loadout") == LOADOUT_AS_IS
                    else bp.get("loadout")),
        "tier": bp.get("tier"),
        "restart_via_home": bool(bp.get("restart_via_home", False)),
        "shop_interval_sec": bp.get("shop_interval_sec",
                                    _DEFAULT_SHOP_INTERVAL),
        "shopping": _compile_shopping(shopping),
        "uw_wanted": _compile_uw_wanted(uw_policy, _owned_uws(profile)),
        "chain_lightning": _compile_chain_lightning(uw_policy),
        "gem_delay_sec": list(gather.get("gem_delay_sec")
                              or _DEFAULT_GEM_DELAY),
        "gather": copy.deepcopy(gather),
        "abilities": abilities,
        "rules": rules,
        "_source": {"profile": profile.get("_name"),
                    "blueprint": blueprint_name},
    }

    # Kind-specific passthroughs. Only emitted where they MEAN something: orchestrator
    # does `preset().get("tournament_setup")`, so a coin preset carrying a false
    # one would read identically but suggest the key was considered and
    # rejected for this run, which it wasn't.
    if kind == "coin":
        # P6, AND BOTH KEYS ARE ALWAYS EMITTED. orchestrator reads them off the preset
        # (`apply_cancel_sprint`, `max_wave_reached`), and the compiler is the
        # only source of defaults in this file: `max_wave: null` IS the "no
        # cap" value the runtime tests for, and `cancel_sprint: false` is "do
        # not touch the sprint". Emitting them only when authored would put the
        # default back into the reader, which is the drift this module exists
        # to prevent - and would make "the key is missing" mean two different
        # things (unset, or a preset compiled by an older build).
        out["cancel_sprint"] = bool(bp.get("cancel_sprint", False))
        out["max_wave"] = bp.get("max_wave")
        # Always emitted, same doctrine: None IS "a normal run, enter via
        # BATTLE"; a tab name means "enter via the Dissonant Run dialog
        # with that tab disabled" (event mode, 2026-08-31).
        out["dissonant_tab"] = bp.get("dissonant_tab")
    if kind == "tournament":
        out["tournament_setup"] = True
        out["gem_entry_max"] = bp.get("gem_entry_max", 0)
        out["in_run_actions"] = _compile_in_run_actions(bp)
    # Quest bodies carry their knobs VERBATIM as well as in runner_args: the
    # runner reads argv, but the dashboard and the run log read the preset, and
    # a count that appears in only one of the two is a count someone will
    # eventually change in the wrong place.
    if kind == "uw_grant_quest":
        out["grant_targets"] = list(bp.get("grant_targets") or [])
        out["reroll_at_wave"] = bp.get("reroll_at_wave")
        out["ride_to_wave"] = bp.get("ride_to_wave")
        out["rides"] = bp.get("rides")
        out["uw_setup"] = copy.deepcopy(_d(bp.get("uw_setup")))
    if kind == "cycle_quest":
        out["cycle_sec"] = bp.get("cycle_sec")
        out["cycles"] = bp.get("cycles")

    runner, runner_args = _runner_for(kind, bp)
    out["runner"] = runner
    out["runner_args"] = runner_args
    return out


def _compile_in_run_actions(bp: dict) -> list[dict]:
    """Tournament `in_run_actions` -> the ordered schedule orchestrator walks.

    THE CONTRACT WITH orchestrator.run_in_run_actions: a list of
    `{id, at_wave, switch_cards, requires}`, in fire order. `switch_cards` is
    the card-preset NAME as a bare string, not a nested action block - the
    runtime reads `a.get("switch_cards")` and hands it straight to
    loadout.apply_cards, so a `{kind: ..., preset: ...}` dict there would be
    the accepted-but-ignored shape again.

    `id` is `in_run#<index>` and it is STABLE: the runtime keys its
    already-fired and gave-up sets on it for the life of the run, and the same
    blueprint must produce the same ids on the next spawn or a swap that
    already happened fires again after a restart.

    `requires` travels WITH the action for the same reason a rule's does - the
    compiled preset outlives its validation, and required_capabilities() reads
    the compiled artefact rather than the profile.
    """
    out: list[dict] = []
    for i, item in enumerate(bp.get("in_run_actions") or []):
        item = _d(item)
        rid = f"in_run#{i}"
        name = item.get("switch_cards")
        out.append({
            "id": rid,
            "at_wave": int(_finite(item.get("at_wave"), f"{rid}.at_wave")),
            "switch_cards": name,
            "requires": _rule_requires("wave_at_least", {}, "switch_cards",
                                       {"preset": name}),
        })
    return out


def _runner_for(kind: str, bp: dict) -> tuple[str | None, list[str] | None]:
    """Which process actually drives this blueprint.

    ANSWERED BY THE FLOW REGISTRY. `runner: None` means the ORCHESTRATOR
    runs it - the observe-decide-act loop reads the compiled preset
    directly, so there is no separate script and no argv. Script flows
    (shard, the quests, any extension) get an argv built from the blueprint
    by flows.extra_argv - the SAME builder the scheduler spawns plan blocks
    with, so the compiled runner_args and a live spawn cannot drift apart.
    """
    try:
        spec = _flows_registry.flow(kind)
    except _flows_registry.FlowError as e:
        raise ProfileError(f"no runner for kind {kind!r} ({e})") from e
    if spec["runner"] is None:
        return None, None
    return spec["runner"], _flows_registry.extra_argv(kind, bp)


def _compile_shopping(entry) -> list[dict]:
    """Shopping list -> the flat `shopping` value `shopper.Shopper.__init__`
    already reads: enabled directives, IN ORDER, with the `enabled` flag
    stripped.

    The stripping is the point. Shopper walks its directives top to bottom and
    knows nothing about switches; teaching it to skip disabled entries would put
    a per-item conditional in the sweep for a decision that was already made at
    compile time. Master `enabled: false` compiles to `[]`, which makes
    Shopper.finished true on the first check - no panel visits at all, rather
    than a sweep that opens the workshop and buys nothing.
    """
    master, directives = _shopping_parts(entry)
    if not master:
        return []
    out = []
    for d in directives:
        if not isinstance(d, dict):
            continue
        if not d.get("enabled", True):
            continue
        out.append({k: copy.deepcopy(v) for k, v in d.items()
                    if k != "enabled"})
    return out


def _compile_uw_wanted(uw_policy: dict, owned: set[str]) -> dict:
    """The once-per-run UW normalization set, FILTERED to what the account
    owns. An unowned weapon has no toggle on the UW panel, so leaving it in the
    list would make the normalizer hunt for a row that does not exist, fail its
    verification three times and give up - dragging the panel open for nothing
    at the start of every run."""
    return {uw: bool(want)
            for uw, want in _d(uw_policy.get("baseline")).items()
            if uw in owned}


def _compile_chain_lightning(uw_policy: dict) -> dict:
    """CL is the one UW the orchestrator drives DYNAMICALLY, so it gets its own block
    rather than living in uw_wanted (which is enforced once and then left
    alone). Four player-facing modes collapse onto the two knobs orchestrator.cl_window()
    actually reads: `always_on` (the baseline state to normalize to) and
    `always_on_above` (the wave past which CL is simply left on)."""
    cl = _d(uw_policy.get("chain_lightning"))
    mode = cl.get("mode")
    if not cl or mode is None:
        # No CL policy at all (shard/quest blueprints): treat as off, and say
        # so explicitly so a reader can tell "off" from "forgot".
        return {"enabled": False, "always_on": False, "always_on_above": None,
                "pre_mark_waves": None, "off_after_waves": None}
    if mode == "always_on":
        return {"always_on": True}
    if mode == "fleet_marks":
        return {"always_on": False,
                "always_on_above": cl.get("always_on_above"),
                "pre_mark_waves": cl.get("pre_mark_waves"),
                "off_after_waves": cl.get("off_after_waves")}
    if mode == "off_until_wave":
        # CL is dark until the latch wave, then on for good. NO MARK RANGES:
        # "off until wave N" has to mean off, and injecting the farm offsets
        # here (an earlier ruling of mine, reversed after Codex #12) lights CL
        # around the fleet mark at 2495 for any policy whose `on_above` sits
        # above it - `on_above: 5000` would have turned CL on at 2470.
        #
        # This is safe only because the P3 cl_window() guard treats absent
        # offsets as "no mark choreography" instead of splatting them into
        # random.randint(). That guard ships in the same P3 as the profile
        # flip; the two must not be separated.
        return {"always_on": False,
                "always_on_above": cl.get("on_above"),
                "pre_mark_waves": None,
                "off_after_waves": None}
    if mode == "off":
        return {"enabled": False, "always_on": False,
                "always_on_above": None,
                "pre_mark_waves": None, "off_after_waves": None}
    raise ProfileError(f"chain_lightning.mode: unknown mode {mode!r} "
                       f"(known: {', '.join(CL_MODES)})")


def _pick(params: dict, key: str, default):
    """`params[key]`, treating a MISSING key and an explicit null the same, and
    never treating a legitimate 0 as absent.

    `or` is the wrong tool here and has been a live bug in this codebase
    before: `deadband: 0` means "every decrease counts" and `after_waves: 0`
    means "the mark wave itself", and both would be silently replaced by the
    legacy literal.
    """
    value = params.get(key)
    return default if value is None else value


def _finite_raw(value, path: str):
    """Refuse NaN/infinity, returning the value UNCHANGED - int stays int.

    Tier A scalars are copied into the abilities dict verbatim (a `throttle_sec:
    5` written as an int compiles as an int), and the golden regression pins
    that dict byte for byte. So the Tier A path gets the check without the
    cast; only the Tier B shape, whose contract says "float", converts.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise ProfileError(f"{path}: must be a finite number, got {value!r} - "
                           f"NaN disables the comparison it appears in and "
                           f"infinity freezes it")
    return value


def _finite(value, path: str) -> float:
    """float(value), REFUSING NaN and infinity.

    validate() already rejects both (see _is_num), but compile_preset() is
    reachable without it - the dashboard previews a single blueprint - and a
    non-finite number does not crash anything downstream, which is exactly the
    problem: `now < nan` is False forever (no cooldown at all) and `now < inf`
    is True forever (the rule never fires again). Both would sit in the
    compiled artefact looking like a number.
    """
    try:
        out = float(value)
    except (TypeError, ValueError) as e:
        raise ProfileError(f"{path}: expected a number, got {value!r}") from e
    if not math.isfinite(out):
        raise ProfileError(f"{path}: must be a finite number, got {value!r} - "
                           f"NaN disables the comparison it appears in and "
                           f"infinity freezes it")
    return out


def _compile_trigger(trig: str, tp: dict) -> dict:
    """One `when` block -> the normalized trigger spec the runtime reads.

    TOTAL AND EXPLICIT: every key the interpreter needs is present with a real
    number. THE RUNTIME APPLIES NO DEFAULTS AT ALL - absence is an admission
    error there, not a shrug - because a default that exists in two places is a
    default that drifts, and this one did: the compiler said an unstated
    `falling_samples` was 1 while the evaluator read a missing key as 0, so a
    compiled `bar: hp, below: 0.3` rule sat under its threshold for three
    passes and never fired (Codex P4, HIGH). THE COMPILER IS THE SINGLE SOURCE
    OF TRUTH for every default in this shape.

    `kind` is a field, not the dict's first key, because dispatching on key
    PRESENCE is what makes `{stop_after_run: false}` stop a run.
    """
    if trig == "wave_at_least":
        return {"kind": "wave_at_least",
                "wave": int(_finite(tp["value"], "when.wave_at_least"))}
    if trig == "wave_between":
        # `value` as a [lo, hi] pair, which is the first shape the runtime's
        # window reader accepts and the same shape every other wave range in a
        # profile uses (cl pre_mark_waves, off_after_waves...).
        return {"kind": "wave_between",
                "value": [int(_finite(tp["value"][0], "when.wave_between[0]")),
                          int(_finite(tp["value"][1], "when.wave_between[1]"))]}
    if trig == "second_wind":
        return {"kind": "second_wind", "state": tp["state"],
                "min_procs": int(_finite(_pick(tp, "min_procs", 1),
                                         "when.second_wind.min_procs"))}
    if trig == "bar":
        # A PLAIN THRESHOLD IS falling_samples: 0, deadband: 0.0 - "the level
        # alone decides", which is what a rule that states only `below` asks
        # for and what the shipped evaluator did. NOT 1: one required fall
        # means a bar sitting still under its threshold never fires, and "hp
        # is under 30%" is a level question, not a direction question. Tier A's
        # 2 / 0.01 belong to the 3Hz wall watch, where two samples cost 300ms.
        return {"kind": "bar", "bar": tp["bar"],
                "below": _finite(tp["below"], "when.bar.below"),
                "falling_samples": int(_finite(
                    _pick(tp, "falling_samples", 0),
                    "when.bar.falling_samples")),
                "deadband": _finite(_pick(tp, "deadband", 0.0),
                                    "when.bar.deadband")}
    if trig == "fleet_mark":
        # Same defaults the fleet block hardcodes at both of its sites.
        return {"kind": "fleet_mark",
                "after_waves": int(_finite(_pick(tp, "after_waves", 1),
                                           "when.fleet_mark.after_waves")),
                "window_waves": int(_finite(_pick(tp, "window_waves", 60),
                                            "when.fleet_mark.window_waves"))}
    if trig == "wall_collapse":
        return {"kind": "wall_collapse",
                "from_above": _finite(tp["from_above"],
                                      "when.wall_collapse.from_above")}
    return {"kind": trig}                       # death_screen: no parameters


def _compile_action(act: str, ap: dict) -> dict:
    """One `do` block -> the normalized action spec. Same contract as
    _compile_trigger: explicit `kind`, every parameter present, no strings to
    parse. The `fire`/`burst` defaults are the ones the P3 evaluator used at
    this latency, NOT the Tier A ones - a main-loop `fire` goes through
    fire_button with require_ready False, exactly as it shipped."""
    if act == "fire":
        return {"kind": "fire", "button": ap["button"],
                "require_ready": bool(_pick(ap, "require_ready", False))}
    if act == "burst":
        # NO `retaps` HERE. The Tier A burst retaps because it fires three
        # instant taps off one frame and has to confirm afterwards; the Tier B
        # burst goes through fire_button, which confirms the tap itself, so a
        # retap count has no reader at this site. Emitting one made a
        # configured `retaps: 5` silently become one fire (Codex P4, MEDIUM) -
        # it is refused at validation instead (_check_tier_b_params).
        return {"kind": "burst", "button": ap["fire"],
                "cancel_sprint": bool(_pick(
                    ap, "cancel_sprint",
                    _ABILITY_DEFAULTS["burst_cancel_sprint"])),
                # require_MATCH has no Tier B home either; require_ready is
                # this site's gate.
                "require_ready": bool(_pick(
                    ap, "require_ready",
                    _ABILITY_DEFAULTS["burst_require_ready"]))}
    if act == "switch_cards":
        return {"kind": "switch_cards", "preset": ap["preset"]}
    if act == "toggle_uw":
        # SOURCE `want_on` -> COMPILED `on`. The rename exists only to keep a
        # YAML parser away from the word (YAML 1.1 reads a bare `on` key as
        # True); the compiled dict is never parsed as YAML, and `on` is what
        # the interpreter reads.
        return {"kind": "toggle_uw", "weapon": ap["weapon"],
                "on": bool(_pick(ap, "want_on", True))}
    return {"kind": act}                        # the three flag actions


def _rule_requires(trig: str, tp: dict, act: str, ap: dict) -> dict:
    """What the PLAYER must own for this rule to be runnable, derived from the
    trigger and the action rather than declared.

    This is the data the spawn-time re-check runs on (`check_capabilities`).
    Validation already refuses a profile whose player lacks these, but a
    compiled preset outlives the validation: it is installed into CONFIG, named
    on a tray menu and launched hours later, possibly after a scan rewrote
    `player.*`. So the requirement travels WITH the rule.
    """
    abilities = []
    if act == "fire" and ap.get("button") in BUTTONS:
        abilities.append(ap["button"])
    if act == "burst" and ap.get("fire") in BUTTONS:
        abilities.append(ap["fire"])
    cards = [ap["preset"]] if act == "switch_cards" and ap.get("preset") \
        else []
    uws = [ap["weapon"]] if act == "toggle_uw" and ap.get("weapon") else []
    wall = (trig == "bar" and tp.get("bar") == "wall") or \
        trig == "wall_collapse"
    return {"abilities": abilities, "wall": wall, "card_presets": cards,
            "uws": uws}


def _compile_tier_b_rule(entry: dict, policy_name: str) -> dict:
    """One classified rule -> one compiled Tier B rule. THE CONTRACT WITH THE
    MAIN-LOOP INTERPRETER (profiles/SCHEMA.md, "RULES" -> compiled shape).

    `id` is stable: it is the policy name plus the rule's index IN THE POLICY,
    so it does not move when an earlier rule is absorbed into Tier A, and two
    blueprints sharing a policy log the same id for the same rule. The
    interpreter keys its per-run state (fired / next-allowed / retries) on it.
    """
    trig, act = entry["trigger"], entry["action"]
    tp, ap, rule = entry["tparams"], entry["aparams"], entry["rule"]
    # UNCONSTRUCTIBLE, not merely refused. Every main-loop rule is built here,
    # so this is the one place that can promise no compiled preset anywhere
    # carries a rule the runtime retires on sight. (No Tier A slot takes
    # `switch_cards` - they take `burst` and `fire` - so "main-loop rule" is
    # every switch_cards rule there is.)
    if act == "switch_cards":
        raise ProfileError(f"{entry['path']}.do.switch_cards: "
                           f"{NO_CARDS_ROUTE}")
    # ONE COOLDOWN, THREE SPELLINGS, ranked once here so the runtime never has
    # to. The rule's own `refire_sec` wins; a `fire` action may state the same
    # floor as `throttle_sec` / `refire_guard_sec` (which is what the P3
    # evaluator read); otherwise the 5s default.
    rid = f"{policy_name}#{entry['index']}"
    refire = rule.get("refire_sec")
    if refire is None and act == "fire":
        refire = _pick(ap, "throttle_sec", ap.get("refire_guard_sec"))
    return {
        "id": rid,
        "when": _compile_trigger(trig, tp),
        "do": _compile_action(act, ap),
        "repeat": bool(rule.get("repeat")),
        "refire_sec": (float(DEFAULT_RULE_REFIRE_SEC) if refire is None
                       else _finite(refire, f"{rid}.refire_sec")),
        # WHERE it runs, which is also WHEN: a death_screen rule cannot be
        # evaluated by the observe loop, because that loop has already exited
        # by the time the screen exists.
        "latency": "death_handler" if trig == "death_screen" else "main_loop",
        "requires": _rule_requires(trig, tp, act, ap),
    }


def _compile_rescue(policy: dict, path: str,
                    policy_name: str = "rescue") -> tuple[dict, list[dict]]:
    """(flat abilities dict, Tier B rules) for one rescue policy.

    THE SPLIT. Tier A has exactly four slots - one bar/burst, one bar/nuke, one
    wall_collapse, one fleet_mark - because that is what the greedy wall watch
    can express as hoisted scalars. A rule that fits takes its slot; everything
    else (extra rules, unusual actions, anything under `arm: always`) falls
    through to Tier B and is evaluated at main-loop speed. Falling through is
    NOT an error: it is the schema's stated design, and the only cost is
    latency, which the dashboard surfaces per rule.

    Tier A absorption requires `arm.on: second_wind` per the schema - the flat
    scalars ARE the post-Second-Wind watch, and there is no other place in the
    orchestrator to hoist them into.

    NO POLICY -> `rescue_bar: None`, which is the runtime's "there is no rescue
    here, skip the block" signal. It must never come back as "wall" with a null
    threshold: that pair is a TypeError inside the watch on a live tower.
    """
    abilities = dict(_ABILITY_DEFAULTS)
    tier_b: list[dict] = []
    if not policy:
        return abilities, tier_b

    on_sw, watch_sec, immunity_sec = _arm(policy)
    abilities["hold_until_second_wind"] = on_sw
    abilities["post_sw_watch_sec"] = watch_sec
    abilities["sw_immunity_sec"] = immunity_sec
    # Emitted only when the policy actually says something. orchestrator.py reads it
    # through .get(), so an absent key and an explicit false are the same
    # decision - and the compiled dict stays a record of what was CONFIGURED
    # rather than of every default that happened to apply.
    if policy.get("end_sprint_after_sw") is not None:
        abilities["end_sprint_after_sw"] = bool(policy["end_sprint_after_sw"])
    else:
        abilities.pop("end_sprint_after_sw", None)
    # FIRST STATEMENT WINS for every scalar more than one Tier A rule can
    # supply. The bar/burst and wall_collapse rules both carry burst params,
    # and letting the later one overwrite meant a collapse rule silently
    # rewrote the rescue's cancel/retap/ready behaviour (Codex round 2, #4 -
    # "two burst rules share global action scalars"). First-wins is also what
    # makes the compile order-deterministic.
    claimed: set[str] = set()

    def _set_once(key: str, value) -> None:
        if key not in claimed:
            abilities[key] = value
            claimed.add(key)
    for entry in _classify_rules(policy, path):
        slot, tp, ap = entry["slot"], entry["tparams"], entry["aparams"]
        act = entry["action"]
        if slot is None:
            tier_b.append(_compile_tier_b_rule(entry, policy_name))
            continue

        if slot == "bar_burst":
            abilities["rescue_bar"] = tp["bar"]
            abilities["dm_below"] = _finite_raw(tp["below"],
                                                f"{path}: bar.below")
            if tp.get("falling_samples") is not None:
                abilities["falling_samples"] = tp["falling_samples"]
            if tp.get("deadband") is not None:
                abilities["deadband"] = tp["deadband"]
            _set_once("burst_cancel_sprint", bool(ap.get("cancel_sprint",
                                                         True)))
            _set_once("burst_retaps", int(ap.get("retaps", 3)))
            _set_once("burst_require_match",
                      bool(ap.get("require_match", False)))
            _set_once("burst_require_ready",
                      bool(ap.get("require_ready", False)))
        elif slot == "bar_nuke":
            abilities["rescue_bar"] = tp["bar"]
            abilities["nuke_below"] = _finite_raw(tp["below"],
                                                  f"{path}: bar.below")
            # `nuke_below` is read ONLY on the hp branch (orchestrator.py:804) - the
            # wall branch hands the whole rescue to the fast wall watch - so
            # the key is named for the site that consumes it.
            _set_once("hp_nuke_require_ready",
                      bool(ap.get("require_ready", True)))
        elif slot == "collapse":
            abilities["collapse_from"] = _finite_raw(
                tp["from_above"], f"{path}: wall_collapse.from_above")
            _set_once("burst_cancel_sprint", bool(ap.get("cancel_sprint",
                                                         True)))
            _set_once("burst_retaps", int(ap.get("retaps", 3)))
            _set_once("burst_require_match",
                      bool(ap.get("require_match", False)))
            _set_once("burst_require_ready",
                      bool(ap.get("require_ready", False)))
        elif slot == "fleet":
            abilities["nuke_on_fleet"] = {
                "after_waves": tp.get("after_waves", 1),
                "window_waves": tp.get("window_waves", 60),
                # The fleet Nuke is the one Tier A slot that repeats, so it is
                # the one place a throttle means anything - and the one that
                # waits for a ready glyph (orchestrator.py:455/737 take the default).
                "throttle_sec": _finite_raw(
                    ap.get("throttle_sec", _DEFAULT_FLEET_THROTTLE),
                    f"{path}: fire.throttle_sec"),
                "require_ready": bool(ap.get("require_ready",
                                             _DEFAULT_FLEET_REQUIRE_READY))}

        # refire_guard_sec has one flat home and no legacy counterpart, so it
        # is taken from whichever Tier A `fire` rule states it first -
        # deterministic, and never silently dropped.
        if act == "fire" and ap.get("refire_guard_sec") is not None:
            _set_once("refire_guard_sec",
                      _finite_raw(ap["refire_guard_sec"],
                                  f"{path}: fire.refire_guard_sec"))

    # Same refusal validate() reports, enforced here too: compile_preset() is
    # reachable without validate() (the dashboard previews a single blueprint),
    # and a preset that crashes the rescue must not be constructible by any
    # path. A policy with no rules at all is a different thing - that is "no
    # rescue configured", and it never arms the watch.
    if abilities["hold_until_second_wind"] and abilities["dm_below"] is None:
        raise ProfileError(
            f"{path}: arms the post-Second-Wind watch but no `bar` rule with a "
            f"`burst` action sets dm_below - the watch would compare every "
            f"wall sample against null")
    return abilities, tier_b


# ------------------------------------------------------ capability re-check

def required_capabilities(compiled: dict) -> dict:
    """What the PLAYER must own for one COMPILED preset to be runnable.

    Reads the compiled artefact, not the profile: `abilities{}` for the Tier A
    rescue and `rules[].requires` for every Tier B rule. That is deliberate -
    it is the compiled preset that gets installed into CONFIG, named in the
    tray menu and launched, so the question "can this account run THIS" has to
    be answerable from it alone, hours after the profile was read.

    -> {"abilities": [sorted names], "wall": bool, "card_presets": [sorted]}

    EVERY CARRIER OF `requires` IS READ, not just the rules: P6's tournament
    `in_run_actions` name a card preset too, and a gate that walked only
    `rules[]` would wave through the one preset whose swap happens mid-run, on
    a paid ticket, hours after the profile was validated.
    """
    ab = _d(compiled.get("abilities"))
    abilities: set[str] = set()
    cards: set[str] = set()
    uws: set[str] = set()
    # Tier A. `rescue_bar` is what says a rescue exists at all; dm_below is the
    # burst's threshold (Demon Mode) and the two nuke keys are the Nuke's.
    wall = ab.get("rescue_bar") == "wall"
    if ab.get("dm_below") is not None:
        abilities.add("demon_mode")
    if ab.get("nuke_below") is not None or ab.get("nuke_on_fleet"):
        abilities.add("nuke")
    for rule in list(compiled.get("rules") or []) + \
            list(compiled.get("in_run_actions") or []):
        req = _d(_d(rule).get("requires"))
        abilities |= {a for a in req.get("abilities") or []}
        cards |= {c for c in req.get("card_presets") or []}
        uws |= {u for u in req.get("uws") or []}
        wall = wall or bool(req.get("wall"))
    return {"abilities": sorted(abilities), "wall": wall,
            "card_presets": sorted(cards), "uws": sorted(uws)}


def check_capabilities(compiled: dict, player: dict | None = None) -> list[str]:
    """Re-check a compiled preset against `player.*` AT SPAWN. Empty = runnable.

    validate() answers this question when the profile is read; this answers it
    again when a runner actually starts, which is not the same moment and not
    always the same process. A tray launch, a combo hand-off or a scan that
    rewrote the inventory can all put a compiled preset in front of a player
    section that no longer backs it, and the cost of getting it wrong is a
    rescue tapping a FIXED COORDINATE for an ability the account does not have.

    `player` defaults to the bound profile's section, so a runner can call
    `playerprofile.check_capabilities(preset())` with no arguments.

    IT FAILS CLOSED, ALWAYS, AND IT NEVER DECIDES WHAT IS LEGACY. If it cannot
    prove the account backs the preset - no `player` argument and no bound
    profile - it returns a problem saying so, whatever the preset looks like.

    That last clause is the whole fix (Codex P4b, HIGH). This used to exempt
    anything without the compiler's `_source` stamp, on the theory that a
    stampless body must be legacy. The runtime's own test for "is this
    compiled" is broader (a `bp_` NAME or the stamp), so a `bp_`-named body
    that had lost its stamp - a hand-edited CONFIG entry, a dashboard preview,
    a partially-updated preset - was compiled as far as the caller was
    concerned and legacy as far as this helper was concerned, and the gap
    returned `[]`. Two definitions of "legacy" is one too many: THE CALLER
    OWNS THAT DECISION (orchestrator does not ask about presets it considers legacy),
    and a helper that is asked must answer or refuse, never shrug.
    """
    if player is None:
        player = _d(_d(PROFILE).get("player")) if PROFILE is not None else None
    if not isinstance(player, dict) or not player:
        return [f"{_d(compiled.get('_source')).get('blueprint') or 'preset'}: "
                f"cannot be checked against this account - there is no "
                f"`player` section to check it against (no profile bound to "
                f"this process and none passed). Refusing rather than "
                f"assuming ownership"]
    need = required_capabilities(compiled)
    where = _d(compiled.get("_source")).get("blueprint") or \
        compiled.get("label") or "preset"
    out: list[str] = []
    if need["abilities"]:
        have = player.get("abilities")
        if not isinstance(have, dict):
            out.append(f"{where}: taps {', '.join(need['abilities'])}, but the "
                       f"profile has no `player.abilities` section - run "
                       f"scan.py (an unowned ability is tapped at a fixed "
                       f"coordinate, not skipped)")
        elif player.get("abilities_verified") is not True:
            out.append(f"{where}: taps {', '.join(need['abilities'])} with "
                       f"ability ownership unverified - run `scan.py --battle` "
                       f"or set `player.abilities_verified: true`")
        else:
            for name in need["abilities"]:
                if not have.get(name):
                    out.append(f"{where}: taps {name!r}, which the player does "
                               f"not have (player.abilities.{name} is not true)")
    if need["wall"] and not player.get("wall"):
        out.append(f"{where}: watches the wall bar, but the player has no wall "
                   f"(player.wall is not true) - there is no bar to watch")
    # A MISSING INVENTORY IS A REFUSAL, NOT A PASS - the same ruling
    # _validate_loadout_ownership already carries. "The account was never
    # scanned" is precisely the case where a wrong tap is most likely, so
    # `card_presets: null` must not read as "sure, that preset exists".
    have_cards = player.get("card_presets")
    if need["card_presets"] and not isinstance(have_cards, list):
        out.append(f"{where}: switches cards to "
                   f"{', '.join(need['card_presets'])}, but this profile has "
                   f"no `player.card_presets` inventory to check it against - "
                   f"run scan.py")
    elif need["card_presets"]:
        for name in need["card_presets"]:
            if name not in have_cards:
                out.append(f"{where}: switches to card preset {name!r}, which "
                           f"is not on the account (player.card_presets)")
    have_uws = player.get("uws")
    if need["uws"] and not isinstance(have_uws, dict):
        out.append(f"{where}: toggles {', '.join(need['uws'])}, but this "
                   f"profile has no `player.uws` inventory to check it "
                   f"against - run scan.py")
    else:
        owned = {k for k, v in _d(have_uws).items() if v}
        for name in need["uws"]:
            if name not in owned:
                out.append(f"{where}: toggles {name!r}, which the player does "
                           f"not own (player.uws.{name} is not true) - the "
                           f"panel has no row for it")
    return out


# --------------------------------------------------- vocabulary export (P6)

# The top-level keys of vocab(), in order, and the ONE list of them: SCHEMA.md
# quotes this tuple and a test pins the two together, so a section that is
# added without being documented fails rather than shipping unlabelled.
VOCAB_SECTIONS = (
    "kinds", "blueprint_fields", "loadout_specials", "bar_names", "buttons",
    "sw_states", "cl_modes", "uw_names", "gather_keys", "shop_tabs", "shop_modes",
    "shop_stats", "rule_triggers", "rule_actions", "death_screen_actions",
    "in_run_action_kinds", "weekdays", "block_fields", "plan_tri_state",
    "chore_names",
)


def _spec(type_: str, doc: str, *, values=None, span=None,
          fields: dict | None = None, required=None) -> dict:
    """One value space, in the shape the dashboard renders generically.

    EVERY spec carries `type`, `values`, `range` and `doc` - always all four,
    `None` where the constraint does not apply - so a renderer can subscript
    them instead of guessing which keys a particular field brought. Absent and
    "no constraint" are different answers, and only one of them is safe to
    render as a free-text box.

    `range` is [low, high], INCLUSIVE, with `None` for an open end. A spec
    whose bound cannot be written that way (a float that must be strictly
    positive) states [None, None] and says so in `doc` rather than claiming a
    bound the validator does not actually accept.

    `type: "object"` is the nesting case: the section's own fields sit in
    `fields` (name -> spec) and the ones the author cannot omit are listed in
    `required`. It is the only type word here that is not one of the leaf
    types, and it exists so that every node in the tree answers to `["type"]`.
    """
    out = {"type": type_,
           "values": list(values) if values is not None else None,
           "range": list(span) if span is not None else None,
           "doc": doc}
    if type_ == "object":
        out["fields"] = fields or {}
        out["required"] = list(required or ())
    return out


def _enum(values, doc: str) -> dict:
    return _spec("enum", doc, values=values)


# Leaf specs reused across triggers/actions, so a threshold means the same
# thing everywhere it is offered.
_UNIT = "a fraction of the bar, 0..1 - NOT a percentage"
_POSITIVE_SEC = "seconds, greater than 0"


def _blueprint_field_specs() -> dict:
    """name -> spec for every field a blueprint may carry, ANY kind.

    One spec per field NAME, because a field means the same thing wherever it
    is legal - `tier` is the tier on all five kinds. WHERE each one is legal is
    not decided here (see `_blueprint_fields`): that comes off `_COMMON_FIELDS`
    / `_KIND_FIELDS`, the same tables `_validate_blueprint` refuses against.
    """
    return {
        "kind": _enum(KINDS, "which runner drives this blueprint - it is also "
                             "the key this section is grouped under"),
        "label": _spec("str", "what the tray and the run log call it; "
                              "defaults to the blueprint name, title-cased"),
        "loadout": _spec(
            "str", f"a `config.yaml` loadouts key, or `{LOADOUT_AS_IS}` to "
                   f"equip nothing (coin only). REQUIRED - an omitted loadout "
                   f"is a forgotten one, which is why it is refused"),
        "tier": _spec(
            "int", "REQUIRED on coin and shard (the runner sets it from the "
                   "home screen). The upper bound is `player.max_tier`, which "
                   "is account data rather than vocabulary, so it is not "
                   "stated here", span=(1, None)),
        "policies": _spec(
            "object", "which named policies this blueprint runs under",
            required=(),
            fields={
                "uw": _spec("str", "a `policies.uw_policies` key"),
                "rescue": _spec("str", "a `policies.rescue_policies` key"),
                "gather": _spec("str", "a `policies.gather` key")}),
        "shopping": _spec("str", "a `policies.shopping_lists` key"),
        "restart_via_home": _spec(
            "bool", "go back to the home screen between runs (where the "
                    "between-run chores live) instead of straight to RETRY"),
        "shop_interval_sec": _spec(
            "int", "how often the workshop sweep may run, in seconds",
            span=(1, None)),
        "cancel_sprint": _spec(
            "bool", "end the intro sprint once at run start - it LOCKS THE "
                    "ABILITY ROW, so a rescue that needs abilities from wave "
                    "1 says so here"),
        "max_wave": _spec(
            "int", "surrender at this wave, through the guarded exit flow. "
                   "Unstated (null) is NO CAP, and one attempt per run",
            span=(1, None)),
        "dissonant_tab": _spec(
            "str", "Dissonance event: enter runs via the Dissonant Run "
                   "dialog with this upgrade tab disabled, instead of the "
                   "BATTLE button. Unstated (null) is a normal run. Only "
                   "tabs with harvested dialog templates can be selected",
            values=DISSONANT_TABS),
        "count": _spec(
            "int", "SHARD ONLY: it becomes `flows/shard.py --loops`. 0 means keep "
                   "going until the tray stops it. Runs-per-day for every "
                   "other kind lives on the PLAN BLOCK, which is the one "
                   "place the day counter reads", span=(0, None)),
        "gem_entry_max": _spec(
            "int", "most gems this blueprint may pay for a tournament ticket; "
                   "0 = never pay", span=(0, None)),
        "in_run_actions": _spec(
            "object", f"{PENDING_ROUTE}: a LIST of these, in fire order - "
                      f"scheduled card swaps inside one run, strictly "
                      f"ascending, at most {IN_RUN_ACTIONS_MAX}. Only the "
                      f"EMPTY list is accepted today; a schedule is refused at "
                      f"load, because there is no verified route from a live "
                      f"battle to the cards screen",
            required=IN_RUN_ACTION_KEYS,
            fields={
                "at_wave": _spec("int", "the wave it fires on", span=(1, None)),
                "switch_cards": _spec(
                    "str", "a name from `player.card_presets` - account data, "
                           "so the editor offers the profile's own list")}),
        "grant_targets": _spec(
            "list", "which weapon the grant quest farms. flows/quest_sm.py follows "
                    "Smart-Missiles choreography end to end and logs every "
                    "grant as smart_missiles, so it is the only supported "
                    "target", values=GRANT_TARGETS_SUPPORTED),
        "reroll_at_wave": _spec("int", "reroll the quest at this wave",
                                span=(1, None)),
        "ride_to_wave": _spec("int", "how deep one ride goes",
                              span=(1, None)),
        "rides": _spec("int", "how many rides - becomes `flows/quest_sm.py --rides`",
                       span=(1, None)),
        "uw_setup": _spec(
            "object", "which ultimate weapons the quest run switches on "
                      "before it starts; a weapon the account does not own "
                      "cannot be switched on",
            required=(),
            fields={uw: _spec("bool", f"{uw.replace('_', ' ')} on/off for the "
                                      f"quest run") for uw in UW_NAMES}),
        "cycle_sec": _spec("float", f"one cycle of the quest loop - "
                                    f"{_POSITIVE_SEC}"),
        "cycles": _spec("int", "how many cycles - becomes "
                               "`flows/quest_ilm.py --cycles`", span=(1, None)),
    }


def _blueprint_fields() -> dict:
    """kind -> the fields a blueprint of that kind may carry, as specs.

    PLACEMENT IS DERIVED, NEVER RETYPED. It comes off the same two tables
    `_validate_blueprint` refuses against, so a field that moves between kinds
    moves in the editor with it - and the editor cannot offer a field that
    would be refused at load, which is the whole reason this section exists
    (an editor inferring types from values renders `rides` as a bare number
    box on a coin blueprint just as happily as on a quest one).

    `count` IS THE ONE SUBTRACTION, and it is derived too: it sits in every
    kind's table so that writing it gets the specific "use rides / use cycles
    / it lives on the plan block" message instead of a bare "not a legal
    field" - but shard is the only kind that consumes it, so it is the only
    kind that offers it.
    """
    specs = _blueprint_field_specs()
    out = {}
    for kind in KINDS:
        legal = list(_COMMON_FIELDS) + list(_KIND_FIELDS[kind])
        # ...and the ones _validate_blueprint refuses a blueprint for OMITTING.
        required = ["kind", "loadout"]
        if kind in ("coin", "shard"):
            required.append("tier")
        if kind == "uw_grant_quest":
            required.append("grant_targets")
        if kind == "cycle_quest":
            required.append("cycles")
        fields = {}
        for f in legal:
            if f == "count" and kind != "shard":
                continue
            if f in specs:
                fields[f] = copy.deepcopy(specs[f])
            else:
                # An EXTENSION field, declared by the kind's flow file. The
                # flow spec is the authority on its type/doc; a bare name
                # renders as a free-text box that names its origin.
                decl = (_flows_registry.flow(kind)["blueprint_fields"] or {})
                fd = decl.get(f) if isinstance(decl, dict) else None
                fd = fd if isinstance(fd, dict) else {}
                fields[f] = _spec(
                    fd.get("type", "str"),
                    fd.get("doc", f"declared by "
                                  f"{_flows_registry.flow(kind)['file']}"),
                    values=fd.get("values"),
                    span=tuple(fd["span"]) if fd.get("span") else None)
        out[kind] = _spec(
            "object", f"one `{kind}` blueprint", required=required,
            fields=fields)
    return out


def vocab() -> dict:
    """EVERY value space an editor needs, as one JSON-able dict. No I/O beyond
    the cached stat-template listing, no Flask, no CONFIG: the dashboard
    imports this and jsonifies it.

    IT IS DERIVED, NOT RETYPED. Every enum here is the same tuple the validator
    checks against, so a vocabulary that grows in one place cannot be offered
    stale in the other - which is the failure this replaces, a dashboard
    dropdown listing four actions after the compiler learned eight.

    THE SHAPE IS UNIFORM AND THE CONSUMER IS GENERIC (see `_spec`): every node
    is a spec with `type`/`values`/`range`/`doc`, and an `object` spec nests
    its `fields`. A dashboard that meets a section it has no custom editor for
    can still render it from the type alone, which is the point - this module
    will keep growing sections, and the editor must not need a release to show
    them.

    WHAT IS NOT HERE: anything account-specific. Card presets, owned weapons,
    loadout names and module slugs are PLAYER data (`player.*`, `config.yaml`
    loadouts) - the editor reads those from the profile it is editing. This is
    the vocabulary, which is the same on every account.
    """
    triggers = {
        "bar": _spec(
            "object", "a bar under a threshold: {bar: wall, below: 0.02}",
            required=("bar", "below"),
            fields={
                "bar": _enum(BAR_NAMES, "which bar"),
                "below": _spec("float", f"fire under this level - {_UNIT}",
                               span=(0, 1)),
                "falling_samples": _spec(
                    "int", "consecutive falling samples required first "
                           "(unstated = 0: the level alone decides)",
                    span=(1, None)),
                "deadband": _spec(
                    "float", f"ignore drops smaller than this - {_UNIT}",
                    span=(0, 1))}),
        "wall_collapse": _spec(
            "object", "the wall falling FROM above a level, i.e. a collapse "
                      "rather than a slow decline",
            required=("from_above",),
            fields={"from_above": _spec(
                "float", f"it must have been above this - {_UNIT}",
                span=(0, 1))}),
        "fleet_mark": _spec(
            "object", "the waves around a fleet mark",
            required=(),
            fields={
                "after_waves": _spec("int", "waves after the mark to start",
                                     span=(0, None)),
                "window_waves": _spec("int", "how many waves the window lasts",
                                      span=(1, None))}),
        "wave_at_least": _spec("int", "scalar: {wave_at_least: 4000}",
                               span=(1, None)),
        "wave_between": _spec(
            "list", "pair: {wave_between: [1000, 2000]} - whole waves, "
                    "low <= high", span=(0, None)),
        "second_wind": _spec(
            "object", "the Second Wind proc, by state",
            required=("state",),
            fields={
                "state": _enum(SW_STATES, "which moment of the proc"),
                "min_procs": _spec("int", "procs required this run so far",
                                   span=(1, None))}),
        "death_screen": _spec(
            "bool", "flag: {death_screen: true} - fires once on the stats "
                    "dialog, and must be exactly true", values=(True,)),
    }
    actions = {
        "burst": _spec(
            "object", "the rescue burst: cancel the sprint, then fire",
            required=("fire",),
            fields={
                "fire": _enum(BUTTONS, "which ability the burst fires"),
                "cancel_sprint": _spec(
                    "bool", "end the intro sprint first (it locks the ability "
                            "row); default true"),
                "retaps": _spec(
                    "int", "extra taps off one frame - TIER A ONLY; refused on "
                           "a main-loop rule, which confirms its own tap",
                    span=(1, None)),
                "require_match": _spec(
                    "bool", "TIER A ONLY: refuse to tap when the ability glyph "
                            "was not matched (default false - an unmatched "
                            "glyph falls back to a fixed coordinate)"),
                "require_ready": _spec(
                    "bool", "wait for the ability to be off cooldown "
                            "(default false)")}),
        "fire": _spec(
            "object", "one confirmed ability tap",
            required=("button",),
            fields={
                "button": _enum(BUTTONS, "which ability"),
                "require_ready": _spec("bool", "wait for cooldown; default "
                                               "false at main-loop latency"),
                "throttle_sec": _spec("float", _POSITIVE_SEC),
                "refire_guard_sec": _spec("float", _POSITIVE_SEC)}),
        "switch_cards": _spec(
            "object", f"{PENDING_ROUTE}: apply a card preset mid-run. Offered "
                      f"so the editor shows the truth - a rule using it is "
                      f"REFUSED at load, because there is no verified route "
                      f"from a live battle to the cards screen and the runtime "
                      f"retires it on sight",
            required=("preset",),
            fields={"preset": _spec(
                "str", "a name from player.card_presets - account data, so "
                       "the editor offers the profile's own list")}),
        "toggle_uw": _spec(
            "object", "switch one ultimate weapon on or off in the UW panel",
            required=("weapon",),
            fields={
                "weapon": _enum(UW_NAMES, "which weapon"),
                # `want_on`, never `on`: YAML 1.1 reads a bare `on` key as the
                # boolean True, so an editor that writes `on` writes a rule
                # that silently switches the weapon ON.
                "want_on": _spec("bool", "true = on, false = off; default "
                                         "true. NEVER write this key as `on` "
                                         "- YAML 1.1 eats the word")}),
    }
    for flag in FLAG_ACTIONS:
        actions[flag] = _spec(
            "bool", f"flag: {{{flag}: true}} - must be exactly true, because "
                    f"`false` is a rule with its action switched off and an "
                    f"evaluator that dispatched on the key would run it anyway",
            values=(True,))
    return {
        "kinds": _enum(KINDS, "blueprint kind - it picks the runner and "
                              "decides which fields are legal"),
        "blueprint_fields": _spec(
            "object", "the legal source fields of a blueprint, PER KIND - "
                      "every field the validator accepts for that kind and no "
                      "field it refuses", required=(),
            fields=_blueprint_fields()),
        "loadout_specials": _enum(
            (LOADOUT_AS_IS,),
            "loadout values that are not a config.yaml loadouts key: "
            f"`{LOADOUT_AS_IS}` = equip nothing (coin blueprints only)"),
        "bar_names": _enum(BAR_NAMES, "the two bars a rule can watch"),
        "buttons": _enum(BUTTONS, "abilities the orchestrator owns taps for"),
        "sw_states": _enum(SW_STATES, "Second Wind states the RunState can "
                                      "already answer"),
        "cl_modes": _enum(CL_MODES, "chain_lightning choreography mode"),
        "uw_names": _enum(
            UW_NAMES,
            "every ultimate weapon in the game - editors list ALL of them "
            "(2026-08-29, user ruling): presets may name any UW; "
            "player.uws ownership only decides what applies on an account"),
        "gather_keys": _spec(
            "object", "policies.gather - what a run picks up between taps",
            required=("gem_delay_sec",),
            fields={
                "flying_gem": _spec("bool", "tap the flying gem"),
                "gem_delay_sec": _spec(
                    "list", "[low, high] whole seconds before the tap - it is "
                            "splatted into random.uniform(), so both bounds "
                            "are integers and low <= high", span=(0, None)),
                "ad_gems": _spec("bool", "watch ad-gem offers"),
                "quests_8h": _spec("bool", "claim the 8h quest chest"),
                "quest_rewards": _spec("bool", "claim finished quests"),
                "guild": _spec("bool", "guild collection")}),
        "shop_tabs": _enum(SHOP_TABS, "workshop panel the sweep opens"),
        "shop_modes": _enum(SHOP_MODES, "how a directive spends: repeat until "
                                        "unaffordable, once, cheapest-first, "
                                        "or a fixed number of clicks"),
        "shop_stats": _enum(
            sorted(shop_stats()),
            "stats the sweep can actually FIND on screen - derived from "
            "templates/stats/*.png, because a stat with no template compiles "
            "fine and is then silently unbuyable for the whole run"),
        "rule_triggers": _spec(
            "object", "rescue-rule `when` vocabulary", required=(),
            fields=triggers),
        "rule_actions": _spec(
            "object", "rescue-rule `do` vocabulary", required=(),
            fields=actions),
        "death_screen_actions": _enum(
            DEATH_SCREEN_ACTIONS,
            "the only actions a `death_screen` rule may take - the stats "
            "dialog is a menu, and everything else would tap into it"),
        "in_run_action_kinds": _enum(
            IN_RUN_ACTIONS,
            f"{PENDING_ROUTE}: tournament in_run_actions vocabulary (v1). One "
            f"action kind, written flat as {{at_wave: <wave>, switch_cards: "
            f"<preset>}}, at most {IN_RUN_ACTIONS_MAX} per blueprint, strictly "
            f"ascending - and today only the EMPTY list is accepted"),
        "weekdays": _enum(WEEKDAYS,
                          "plan.week keys, in datetime.weekday() order. Full "
                          "names only - `mon`/`0` are refused, so a profile "
                          "cannot carry two spellings of one day"),
        "block_fields": _spec(
            "object", "one plan block: plan.days.<day>[]",
            required=("block", "blueprint"),
            fields={
                "block": _enum(
                    sorted(BLOCK_KINDS),
                    "what the scheduler thinks it is launching (or any "
                    "`quest*` name for the quest runners) - it is checked "
                    "against the blueprint's kind"),
                "blueprint": _spec("str", "a key from this profile's "
                                          "`blueprints` section"),
                "after": _spec("str", "\"HH:MM\" local - the window opens; "
                                      "omit for 'from midnight'"),
                "until": _spec("str", "\"HH:MM\" local - the window closes; "
                                      "omit for 'rest of the day'. A block "
                                      "that runs past midnight belongs to "
                                      "both days, so write it in both"),
                "count": _spec(
                    "int", "runs of this block per day, persisted per block "
                           "id so an aborted day resumes. A tournament block "
                           f"is {TOURNEY_RUNS_PER_DAY} and cannot be raised",
                    span=(1, None))}),
        "plan_tri_state": _spec(
            "str",
            "the plan is a TRI-STATE: no `plan` section -> compile_plan() "
            "returns None, no CONFIG[\"plan\"], and the scheduler runs its own "
            "constants; a plan with blocks -> the compiled week; a plan that "
            "resolves to no blocks -> refused at load, because a scheduler "
            "handed nothing either idles all day or falls back to constants "
            "the player thought they had replaced"),
        "chore_names": _enum(
            _chore_names(),
            "the between-run chores (chores.py registry, priority order) a "
            "profile may switch off via policies.chores - an opt-out list: "
            "an unnamed chore stays enabled, an unknown name is refused"),
    }


# -------------------------------------------------------------- materialize

def materialize(profile: dict) -> list[str]:
    """Install every blueprint as `CONFIG["presets"]["bp_<name>"]`, returning
    the installed keys.

    MUTATES CONFIG IN PLACE, and mutates each preset body in place too. Every
    module in the autopilot did `from settings import CONFIG` at import time and
    holds a reference to that one dict; rebinding `CONFIG["presets"]` - or even
    replacing an individual preset body that something already captured - would
    leave half the process reading the old object. Same discipline as
    settings.select_instance().

    ALL-OR-NOTHING. Every body is compiled BEFORE anything is installed, so a
    blueprint that fails to compile leaves CONFIG exactly as it was. Installing
    as it went would leave the process running a half-updated profile - some
    blueprints new, some old, and nothing in the log saying which.

    NEVER `.clear()` ON A LIVE BODY. Re-materializing updates in place and then
    deletes the keys that are gone; the old code cleared first, which opens a
    window where another thread holding that dict sees an EMPTY preset and dies
    on a bare subscript (`preset()["chain_lightning"]`). The dict is never
    observably empty, and at every instant it is either fully old, fully new,
    or a superset of both - never less than one of them (Codex #11).

    Idempotent, and it OWNS the `bp_` namespace: a blueprint deleted from the
    profile has its preset removed, so a renamed blueprint cannot leave a stale
    runnable entry behind in the tray menu.
    """
    presets = CONFIG.setdefault("presets", {})
    blueprints = _d(profile.get("blueprints"))

    # Phase 1: compile everything. Any ProfileError propagates with CONFIG
    # untouched.
    bodies = {f"bp_{name}": compile_preset(profile, name)
              for name in blueprints}
    plan = compile_plan(profile)

    # Phase 2: install. Nothing below can fail.
    # THE PLAN GOES INTO CONFIG TOO, and in place for the same reason the
    # presets do: combo.py holds a reference to CONFIG from import time and
    # must be able to read the schedule without holding a profile.
    #
    # THE TRI-STATE IS DECIDED HERE. No `active_profile`, or a profile with no
    # `plan` section -> no `plan` key at all -> combo keeps its own constants.
    # A plan with blocks -> the key. There is no third case, because an empty
    # plan never compiles (compile_plan raises), so the scheduler can read
    # `"plan" in CONFIG` as a complete answer rather than a hint.
    if plan is None:                    # ...compile_plan's own answer for "no
        CONFIG.pop("plan", None)        # plan section", and a re-materialize
                                        # that dropped the section drops the
                                        # artefact with it
    else:
        existing_plan = CONFIG.get("plan")
        if isinstance(existing_plan, dict):
            existing_plan.update(plan)
            for stale in [k for k in existing_plan if k not in plan]:
                del existing_plan[stale]
        else:
            CONFIG["plan"] = plan
    for key, body in bodies.items():
        existing = presets.get(key)
        if isinstance(existing, dict):
            existing.update(body)
            for stale in [k for k in existing if k not in body]:
                del existing[stale]
        else:
            presets[key] = body

    # Phase 3: retire `bp_` presets this profile no longer defines.
    for key in [k for k in presets
                if k.startswith("bp_") and k not in bodies]:
        del presets[key]

    return list(bodies)


def select_profile(name_or_none: str | None) -> str | None:
    """Bind this PROCESS to one profile. Returns the name bound, or None.

    THE LEGACY DOOR IS THE DEFAULT. No name, or a name with no file behind it,
    and this does absolutely nothing - CONFIG keeps exactly the presets
    config.yaml shipped and the run is bit-for-bit the old behavior. That is
    deliberate: profiles are opt-in per account, and a half-migrated machine
    must never be a machine that refuses to farm.

    A profile that EXISTS but is broken is the opposite case and raises: the
    player asked for it by name, and silently running something else is the
    failure mode config.yaml's `defined: false` placeholders were invented to
    avoid.

    Called once, right after settings.select_instance().
    """
    global PROFILE
    if not name_or_none:
        return None
    path = Path(PROFILES_DIR) / f"{name_or_none}.yaml"
    if not path.exists():
        return None
    if PROFILE is not None and PROFILE.get("_name") != name_or_none:
        raise ProfileError(
            f"profile {PROFILE.get('_name')!r} is already bound to this "
            f"process - one profile per process (start another process for "
            f"{name_or_none!r})")
    prof = load(name_or_none)
    problems = validate(prof)
    if problems:
        raise ProfileError(
            f"profile {name_or_none!r} has {len(problems)} problem(s):\n  - "
            + "\n  - ".join(problems))
    materialize(prof)
    PROFILE = prof
    return name_or_none


def compiled_hash(preset_dict: dict) -> str:
    """sha256 of a canonical JSON dump of a compiled preset.

    For the startup attestation line. "Which rules is the bot ACTUALLY running
    right now" has been an unanswerable question every time a run went wrong,
    because the answer lived in whatever config.yaml said at launch. A hash in
    the log pins it: two runs with the same hash ran the same compiled policy,
    full stop, and a hash that changed between two runs says the profile was
    edited even if nobody remembers doing it.

    Canonical = sorted keys, no incidental whitespace. `default=str` keeps it
    total: an exotic scalar degrades to its repr rather than raising during a
    logging call.
    """
    blob = json.dumps(preset_dict, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
