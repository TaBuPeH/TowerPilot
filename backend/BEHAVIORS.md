# In-Run Behaviors

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-08-24
> **Updated:** 2026-09-05
> **Tags:** orchestrator, behaviors, rules, policies, configuration

Every click the autopilot makes during a live run, what triggers it, why it
exists, and where its knobs live. This is the map of the **in-run behavior
layer** - the concurrent "flowing clicks" that run alongside a battle:
claiming gems, collecting rewards, toggling ultimate weapons, firing
abilities, cancelling the sprint, detecting death.

The architecture is **declare-then-execute, in four latency rings**:

| ring | runs | what belongs here |
|---|---|---|
| Tier A rescue watch | sub-second (greedy loop) | the one thing that outruns the main loop: the wall/hp collapse rescue |
| watch_frame | every captured frame (~1.5/s) | death, Second Wind, fleet nuke, rescue trigger, gem claim |
| main loop | once per pass (~1s) | Tier B rules (IFTTT), UW choreography, reward flows, shopping |
| run boundary / day | between runs | death rules, chores (quest scan, shatter), day plan |

Behaviors are **not** hardcoded per user - they compile from a per-player
profile ([profiles/SCHEMA.md](profiles/SCHEMA.md)): `policies` (uw, rescue,
gather, shopping, chores) + per-blueprint knobs + composable `rules`. The
compiler refuses anything the account cannot do (**capability gating**) and
anything no code reads (**no accepted-but-ignored keys**).

---

## The if-this-then-that layer (Tier B rules)

`rescue_policies.<name>.rules` is an ordered list of `{when, do}` rules,
compiled into `preset["rules"]` and interpreted once per main-loop pass
(`orchestrator.eval_rules`). One action per tick, per-rule `repeat` /
`refire_sec`, per-rule state keyed by index so one broken rule cannot
silence another.

**Triggers (`when`):** `bar` (hp|wall, below, falling_samples, deadband) ·
`wall_collapse` (from_above) · `fleet_mark` (after_waves, window_waves) ·
`wave_at_least` · `wave_between` · `second_wind` (state:
open|closed|after_immunity|any, min_procs) · `death_screen`.

**Actions (`do`):** `burst` (cancel sprint → confirm → Demon Mode) ·
`fire` (button, require_ready, throttle) · `toggle_uw` (weapon, want_on) ·
`cancel_sprint` · `surrender_retry` · `stop_after_run` · `switch_cards`
(**refused everywhere** - no verified route from a battle to the cards
screen exists yet; the enablement artifacts are listed in
`orchestrator.run_in_run_actions`).

Three locks make `surrender_retry` unreachable from a tournament (blueprint
kind, `tournament_setup`, the on-screen trophy badge) - a tournament run is
never cancelled, the ticket escalates in gem cost.

The **Tier A** slots are the same vocabulary compiled into the flat
`abilities` dict and hoisted to locals in the greedy wall watch
(`orchestrator._fast_wall_watch`) - the wall goes from full to dead in ~2s,
inside one main-loop pass, so only this ring is a *rescue*; a Tier B rule
watching the same bar is an *observation* (stop, log, toggle). The compiled
`latency` field on every rule records which one it became.

---

## Behavior catalog

### Death detection and restart
Death needs the dialog match AND an unreadable wave counter AND a 2-frame
debounce (the dialog covers the wave box - a readable wave means alive).
Then: collect run stats (`runlog`), run death-phase rules (only
`stop_after_run` is legal - the stats dialog is not a battlefield), honor a
pending stop request (the ONE free moment to stop), then restart -
`restart_via_home: true` + `tier` re-enters via Home so the tier can be
SET; otherwise a plain RETRY. *Knobs:* blueprint `restart_via_home`,
`tier`; `death_screen` rules.

### Rescue (the "am I dying" watch)
`rescue_bar` picks the bar: `wall` (hands the watch to Tier A),
anything-else = **tower HP** - accounts with no wall watch
`bar: hp, below: X` instead, and the compiler *refuses* wall triggers when
`player.wall` is false. `dm_below` / `nuke_below` are the thresholds;
`falling_samples` + `deadband` distinguish "is falling" from "is low";
`wall_collapse.from_above` catches a drain too fast to sample.
The burst taps sprint-cancel → Yes → Demon Mode at fixed points
(`burst_require_match` forbids the blind fallback tap; `burst_retaps`
confirms), because the button-readiness test lies over a lit battlefield -
exactly when a rescue is needed. `refire_guard_sec` floors repeat fires.
*Why:* the T19 bench (full → broken between two samples) and the wave-1120
tournament loss (watch window closed 5s before the wall crossed 5%).

### Second Wind gating
`arm.on: second_wind` holds abilities until after a Second Wind proc's
immunity **observed off the badge**, not timed (`sw_immunity_sec` is only a
backstop); `watch_sec: null` watches until the run ends, and a finite
`watch_sec` bounds the greedy wall watch itself, not only its entry (until
2026-09-04 the loop ran on to its 30-min runaway ceiling, starving gems, CL
and the heartbeat - it read as a hang). `arm.on: always`
is the no-Second-Wind account: always watching, refire-guarded. Nothing is
fired *at* the proc - the sprint keeps earning waves until death is
actually imminent. `min_procs` on `second_wind` triggers handles "improved
Second Wind" variants (act only from proc N on). *Why:* every unconfirmed
Demon Mode in the early logs was a tap into the sprint's ability-row lock;
a proc-time cancel threw away sprint waves on walls that rebuilt to full.

### Intro sprint
Three configurable exits, all verified taps (an account with NO sprint just
logs "indicator not found" and nothing changes): `cancel_sprint` (once, at
run start - P6), `end_sprint_after_sw` (at the Second Wind close: measured
5.5s → 0.9s rescue improvement), and the burst's own first tap
(`burst.cancel_sprint` - the sprint locks the ability row, so the rescue
must clear it itself).

### Fleet-mark Nuke (schedule, not emergency)
Marks at `fleet.first_wave + i*interval`. Fires on the first wave observed
in `[mark+after_waves, mark+window_waves]` - wave *skips* mean `mark+1` is
often never displayed. `after_waves: 3` lets the 1/5-speed movers walk into
the blast (one survived an on-time nuke and killed the run at wave 3516).
`throttle_sec` paces retries; `require_ready` is per-site. Deliberately not
Second-Wind-gated. *Knobs:* `fleet_mark` rule / `nuke_on_fleet`.

### Ultimate-weapon management
Three mechanisms, one owner (`shopper.uw_toggle`, verified tap + re-read,
exponential backoff, give-up-after-3 so a failing panel never drags a whole
run): **(1)** `uw_wanted` baseline - enforced once per run at wave-1
normalization, because quest presets flip toggles and the next farm run
inherits them (a whole night once ran without Death Wave + Poison Swamp);
**(2)** Chain Lightning choreography (`chain_lightning.mode`: always_on |
fleet_marks | off_until_wave | off, with per-run randomized latch wave and
per-mark on/off offsets so no two runs toggle on the same wave);
**(3)** wave-scheduled `toggle_uw` rules for everything else.

### Gem claiming
`gather.flying_gem` gates it; `gem_delay_sec` randomizes a human 3-10s
reach; the claim fires only on a *fresh* detection (the gem orbits - a
remembered position is stale), with one last-resort stale tap that is
refused outright over the ability row (it would fire Nuke/Demon Mode).
**Every** battle loop claims gems - orchestrator, shard, the harness runs
and both quest runners (ILM polls frames through its cycle wait instead of
sleeping; the SM ride polls each second). Quest batches used to be the
blind spot: whole ILM batches ran with the circling gem unclaimed
(2026-08-28).

### Reward collection (side menu, quests, guild, free gems)
The side menu is kept open during runs (rewards live there) via a
double-debounced, match-located tap - a blind coordinate tap once opened
the guild store. Badge-driven flows visit within a randomized
`missions.visit_delay_sec` window ("wander over", not pounce), back off 25+
minutes when a visit claimed nothing, and the guild *store* is only checked
after a guild claim actually landed. Ad-gems (v29): claimable up to **60
times per UTC day** (6 gems each) - claims are paced by a jittered 10-25
minute retry clock, counted per confirmed claim in a UTC-keyed daystate
counter, and stop dead at the cap (`ad_gems_cap_reached` logged once/day;
the cap resets at 00:00 UTC, which is 3 AM local - a local-date counter
would drift by those hours daily). *Knobs:* `gather.quests_8h`,
`quest_rewards`, `guild`, `ad_gems`. All flows navigate menus **the bot
opened**; recovery taps are gated on `bot_left_battle` - a human-opened
menu is never touched.

### Workshop shopping
An ordered directive list IS the priority (`shopping_lists`, per-directive
`enabled`, modes repeat/once/best_cost/clicks); sweep cadence is
sprint-aware (20s while the sprint makes waves cheap, `shop_interval_sec`
after). One small action per frame; any sweep aborts instantly when the
tower leaves the screen.

### Preset selection (v29 - the pre-run equip layer)
Loadouts now prefer *selecting* over *equipping*:
[interactions/presets.py](interactions/presets.py) drives the home-screen
Global Preset picker and the per-category Preset 1/2 tabs with the same
verified-tap discipline as cards (locate template, comparative
green-vs-cyan active check, tap, re-grab, re-verify; Abort on mismatch).
A `global_preset` loadout body does nothing else; every hand-assembled
body ends with the picker on **None** so battle entry cannot re-apply a
stale preset over it. Manual equips on a category that has presets
permanently rewrite the active preset (v29 presets auto-save) - the
profile validator warns unless the loadout declares its `<cat>_preset`
target first. Preset names are account data (user-renameable), so their
templates are per-account, like card presets. See
[profiles/SCHEMA.md](profiles/SCHEMA.md) "v29 presets" for the body
shapes and validator rules.

### Between-run chores
[scheduling/chores.py](scheduling/chores.py) registry (quest_scan, shatter,
housekeeping): once per day,
only from Home, at most one per gap, never fatal. Per-profile opt-out via
`policies.chores` - an **opt-out list**, unknown names refused, disabled
chores skipped without being marked (re-enable works same-day), and an
unreadable policy skips all chores (fail-closed: shattering modules the
player wanted kept is the harm; a deferred chore is not).

### Screen ownership (the "why not" behaviors)
No readable wave counter on 2 frames = not our screen = no clicking at all;
an Abort means the screen was not what the code expected - stop and log,
never blind-tap. "Readable" includes a layout proof: the counter's first
digit sits at x 16-18 of the wave box and no glyph may be cut by the box's
edge (`wave_reader.FIRST_DIGIT_X`) - a Battle History date scrolled into the
box read as wave 9 four times, faked a run boundary, and the new-run UW-tab
tap landed on a human's menu (2026-09-04). The panel-tab helper now refuses
to tap without a readable wave and logs every tab tap (`tab_tap`). A LIVE RUN
IS NEVER ENDED, whoever started it (user, 2026-09-05): `tourney.ensure_home`
holds on a live battle (`tourney_home_hold`, 8h runaway ceiling) instead of
walking it out through END ROUND; only the shard loop's own surrender and a
configured `max_wave` rule end a run, and combo spawns the shard runner with
`--no-setup` so it adopts the battle the handoff started rather than ending
it (the "Tier 18 / Wave 1 / Coins 0" history lines). A frozen wave counter raises a loud `wave_stalled` (every
wave-driven rule above has silently stopped firing). Stuck-recovery taps
fire only on screens a bot flow can strand itself on.

---

## Worked examples (the variance cases)

| "A user who..." | Configure |
|---|---|
| ...doesn't want modules shattered | `policies.chores: [{name: shatter, enabled: false}]` |
| ...doesn't want quests collected mid-run | `gather: {quests_8h: false, quest_rewards: false, guild: false}` (or `gems_only`) |
| ...has no Intro Sprint but has Demon Mode | nothing - every sprint tap verifies first and no-ops when absent; leave `cancel_sprint` unset |
| ...has no Wall | rescue rules on `bar: hp, below: 0.25` - the compiler refuses wall triggers outright when `player.wall` is false |
| ...has no Second Wind | `arm: {'on': always}` - rescue fires on threshold alone, refire-guarded |
| ...has improved Second Wind (regen changes when DM is worth it) | `arm: {'on': second_wind, immunity_sec: ..., watch_sec: null}` + `second_wind: {state: after_immunity, min_procs: N}` rules + a lower `below` |

> [!important] Capability gating is the other half
> A rule that taps Demon Mode on an account whose ability row was never
> scanned is a blind tap at a fixed coordinate. `player.abilities_verified`
> gates every rescue policy; the compiler and the spawn-time gate both
> refuse, naming the missing capability.

---

## Why the modules are shaped this way

The *actions* live in one file per concern, foldered by type
([interactions/](interactions/) for menu flows, [scheduling/](scheduling/)
for time, [device/](device/) and [vision/](vision/) for the emulator and
the pixels, [runtime/](runtime/) for plumbing); the
*variance* lives in data (policies/rules/gather/chores, compiled per
player); what remains in [orchestrator.py](orchestrator.py) is scheduling -
which behavior may act on this frame - plus the two hot paths
(`watch_frame`, `_fast_wall_watch`) whose sub-second timing is the reason
the rescue works at all. Physically extracting those hot paths (or the
main loop's glue) into more files would not add one configurable knob, but
would put the least-tested, most timing-sensitive code through an untested
move - the suite pins the *watch's* internal structure (config reads are
banned inside the sampling loop; policies must be hoisted) precisely
because latency regressions there lose real runs. **New behavior therefore
enters as data first** (a rule, a policy, a chore registry entry, a flow
spec - see [flows/README.md](flows/README.md)), and only earns new engine
code when the vocabulary genuinely cannot express it.
