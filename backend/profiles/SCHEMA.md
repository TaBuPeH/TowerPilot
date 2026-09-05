# Profile schema (v1) - the contract for P2 builders

> **Status:** Active
> **Type:** Knowledge
> **Updated:** 2026-09-06
> **Tags:** profiles, schema, validator, presets

One YAML file per player: `profiles/<name>.yaml`. Four top-level sections.
`config.yaml` stays the MACHINE file (adb, instances, rois, tabs, tap,
screen, loop, logging, fleet, loadouts for now). `config.yaml` gains
`active_profile: <name>`; absent key or file = legacy behavior bit-for-bit.

The scanner writes `profiles/<name>.draft.yaml` with only `player:`.

## player  (seeded by scan.py; human-confirmed)
```yaml
player:
  scanned_at: ISO
  uws: {chain_lightning: true, death_wave: true, golden_tower: true,
        poison_swamp: true, black_hole: true, spotlight: true,
        smart_missiles: false, inner_land_mines: false, chronofield: false}
  abilities: {nuke: true, demon_mode: true}
  abilities_verified: false       # see below - gates every rescue policy
  card_presets: [farm_deck, tourney_deck]   # the tab names as YOU typed them
  guardians: [ally, attack, bounty, fetch, scout, summon]
  modules_equipped: [...]         # slugs
  modules_in_grid: [...]
  wall: true
  max_tier: 19
  # v29 preset capabilities - names as the user renamed them in-game
  global_presets: [Farm Build, Tourney Build]   # as you named them
  category_presets:
    workshop: [Preset 1, Preset 2]
    modules: [Preset 1, Preset 2]
    guardians: [Preset 1, Preset 2]
    bots: [Preset 1, Preset 2]
```
Extra scanner keys (`*_evidence`, `*_note`, `guardians_equipped`,
`guardian_slots_used`, `cards_current`) are informational; the compiler
ignores unknown `player.*` keys.

`abilities_verified` must be the boolean `true` before any blueprint may
reference a rescue policy. `abilities` alone is not evidence: the migrator
infers it from what config.yaml's loadouts imply and has never looked at
the account, and a fabricated `true` is indistinguishable from a scanned
one at compile time. It matters because a burst taps the fixed Demon Mode
coordinate blind when no glyph matches - so an ability that is merely
IMPLIED must not arm one. Raise it with `scan.py --battle`, with
`tools/migrate_profile.py --assert-abilities-verified` (which also stamps
`abilities_verified_by: operator`), or by hand after confirming in the
dashboard. Absent, `false`, or a truthy non-boolean (`"true"`, `1`) all
read as unverified. Shard and quest blueprints tap no rescue ability and
stay runnable without it.

## blueprints  (kind decides which runner + which fields are legal)
```yaml
blueprints:
  coin_default:
    kind: coin                    # -> orchestrator.py --preset bp_coin_default
    loadout: coin_farm            # config.yaml loadouts key, or `as_is`
    tier: 14
    restart_via_home: true
    shop_interval_sec: 90
    shopping: default_sweep       # policies.shopping_lists key
    policies: {uw: farm_cl_choreo, rescue: high_tier_wall, gather: all_on}
  shard_daily:
    kind: shard                   # -> flows/shard.py --loops <remaining> --tier 18
    loadout: shard_farm
    tier: 18
    count: 100                    # SHARD ONLY - this is the --loops value
    policies: {gather: gems_only}
  tourney_main:
    kind: tournament              # -> orchestrator.py --preset bp_tourney_main
    loadout: tourney_1
    gem_entry_max: 10
    tier: null
    policies: {uw: tourney_cl, rescue: tournament_any_falling, gather: gems_only}
  quest_sm:
    kind: uw_grant_quest          # -> flows/quest_sm.py
    loadout: coin_farm
    tier: 1
    grant_targets: [smart_missiles]
    reroll_at_wave: 1000
    ride_to_wave: 6500
    rides: 1
    uw_setup: {chain_lightning: false, death_wave: false, poison_swamp: false,
               golden_tower: true, black_hole: true, spotlight: true}
  quest_ilm:
    kind: cycle_quest             # -> flows/quest_ilm.py
    loadout: inner_land_mines_quest
    tier: 1
    cycle_sec: 25
    cycles: 40
```

### P6 - the run knobs (accepted, compiled, consumed)
Three fields were in the schema and read by nothing, so the compiler
refused them on the key EXISTING, whatever its value. P6 landed their
readers and they are ordinary fields now.

```yaml
blueprints:
  coin_capped:
    kind: coin
    loadout: coin_farm
    tier: 14
    cancel_sprint: true           # end the intro sprint once, at run start
    max_wave: 5000                # surrender at this wave (coin only)
    policies: {gather: gems_only}
  tourney_swap:
    kind: tournament
    loadout: tourney_1
    tier: null
    in_run_actions: []            # the ONLY accepted value today - see below
    policies: {gather: gems_only}
```

* **`cancel_sprint`** (coin, bool) - the intro sprint LOCKS THE ABILITY
  ROW, so a blueprint whose rescue needs its abilities from wave 1 says so
  and `orchestrator.apply_cancel_sprint` ends it once, through the verified
  end-sprint flow. Compiles ALWAYS, `false` when unstated.
* **`max_wave`** (coin, int >= 1) - end the run at this wave through
  `shard.abandon_run`, the same guarded chokepoint the `surrender_retry`
  action uses. ONE ATTEMPT per run; if the surrender aborts, the fallback
  is the runflag and the runner leaves at its own death handler. Compiles
  ALWAYS, `null` when unstated, and `null` is the value the runtime tests
  for - it means NO CAP, not "unwired". COIN ONLY, and the runtime checks
  the kind again at the call site: a tournament run is never cancelled.
* **`in_run_actions`** (tournament, list) - see below.

Both coin knobs are refused on every other kind: `flows/shard.py` has its own
sprint handling, and a wave cap on a tournament blueprint is a surrender
the hard rule forbids.

### in_run_actions - EMPTY ONLY, pending a verified route
**A non-empty schedule is REFUSED**, at validation and at compile, for
exactly the reason `switch_cards` is refused in a rescue rule: there is no
verified route from a live battle to the cards screen, and the runtime
refuses to walk one inside a PAID entry (`orchestrator.run_in_run_actions`, which
is also where the three things that would enable it are written down).

`in_run_actions: []` stays legal and still compiles - the key is wired end
to end, so turning the feature on is a change to one function rather than
a format to re-add. Everything below is the shape it will take, and is
still validated in full: an author who writes a schedule gets it fully
diagnosed alongside the refusal, so the list is correct on the day the
route exists.

An ORDERED list of card swaps inside one tournament run, written FLAT:

```
in_run_actions:
  - {at_wave: 1, switch_cards: tourney_deck}
  - {at_wave: 1500, switch_cards: farm_deck}
```

`switch_cards` is the card-preset NAME as a bare string - not the rules
section's `do:` block. The two look alike and are not: a rescue rule is
evaluated against the SCREEN, while an in-run action fires blind at a
number on the wave counter and walks the game off the battle screen to do
it. That is worth doing for a deck change (the P1 deck clears early waves,
the P2 deck survives late ones) and has not been asked for by anything
else, so the v1 vocabulary is exactly `switch_cards` and a `do:` key is
refused by name.

Rules, all refused loudly:

| rule | why |
|---|---|
| **non-empty at all** | no verified route from a live battle to the cards screen; the runtime refuses it, so the profile does too |
| tournament kind only | orchestrator refuses them on any other preset at runtime; a coin farm walking to the cards screen mid-run loses the night |
| `at_wave` required, integer >= 1 | it is the only thing the action fires on |
| `switch_cards` required, on `player.card_presets` | a name that is not there parks the run in the card menus; a MISSING inventory is a refusal, not a pass |
| strictly ascending | the runtime walks the list in order, one action per pass, so an out-of-order wave is either dead or fires at the wrong wave |
| at most 2 | each action leaves the battle screen and comes back; two is a swap and a swap-back, and more is a schedule - which belongs in the plan |
| no other keys | `do`, `once`, `repeat` are the rules vocabulary, not this one |

Compiled (the contract with `orchestrator.run_in_run_actions`), always present on
a tournament preset and `[]` today - the shape below is what it will hold
when the route is verified:

```
in_run_actions: [{"id": "in_run#0", "at_wave": 1500,
                  "switch_cards": "farm_deck",
                  "requires": {"abilities": [], "wall": false,
                               "card_presets": ["farm_deck"], "uws": []}}]
```

`id` is `in_run#<index>` and is STABLE - the runtime keys "already fired"
and "gave up on" on it for the life of the run, and a mid-tournament
restart recompiles the preset. `requires` travels with the action for the
same reason a rule's does: `required_capabilities()` reads the COMPILED
preset, and a tournament preset can have no rules at all, so a gate that
walked only `rules[]` would wave through the one card preset whose swap
happens mid-run on a paid ticket.

### loadout: `as_is` - equip nothing (coin only)
`loadout` is REQUIRED, and every loadout in `config.yaml` names cards,
guardians or modules. A fresh or sandbox account (no card presets, one
module) therefore cannot name any of them without a `player` section that
lies about what it owns - and an omitted key cannot mean "equip nothing",
because an omitted key is a FORGOTTEN key, which is why it is refused.
So the intent gets a word: `loadout: as_is` = "run whatever is already
equipped, change nothing". It skips the ownership check and compiles to
`loadout: null`, never to the string - the compiled key is a loadouts key,
and a sentinel sitting there would be looked up by the first consumer that
trusts the field.

COIN ONLY. Coin is the one kind whose runner equips nothing by itself:
`orchestrator.py` never calls `loadout.apply()` - the scheduler does, before it
hands over. Every other kind's runner applies something of its own
(tourney.py's three pre-battle swaps; quest_sm/quest_ilm read
`.get("loadout") or <their own default>`, which would substitute a real
build rather than skip), so `as_is` there would not mean what it says and
is refused.

### v29 presets - loadout bodies that select instead of equip
Game v29 added renameable **Global Presets** (picked on the home screen,
applied wholesale at battle entry) and per-category **Preset 1/2** tabs
(Workshop/Modules/Guardians/Bots; cards were already presets). Loadout
bodies in `config.yaml` gain two new forms, both validated against the
`player` capability fields above:

```yaml
loadouts:
  coin_farm:
    global_preset: Farm Build     # EXCLUSIVE - no other keys allowed
  shard_farm:
    cards: shard_deck
    module_preset: Tourney Mods   # per-category selection by name
  inner_land_mines_quest:
    cards: farm_deck
    guardian_preset: Farm Guards  # guild > Guardian tab row
```

- `global_preset: <name>` must be a member of `player.global_presets`, and
  the body may carry NOTHING else - the game applies all five categories at
  battle entry, so any extra key would be silently wiped (refused, not
  warned).
- `<category>_preset: <name>` (`module_preset`, `guardian_preset`,
  `workshop_preset`, `bot_preset`) must be a member of
  `player.category_presets.<cat>`. Selections run BEFORE any manual
  equipment in the same body. Wired categories: modules (bottom-nav
  screen) and guardians (guild > Guardian tab row); workshop/bots abort
  until their navigation is added. Preset names on the tab rows are
  user-renameable IN-GAME - a rename invalidates the harvested tab
  templates and the recorded names (this account renamed modules and
  guardians to Farm/Tourney on 2026-08-27), so keep
  `player.category_presets` in sync with the screens.
- **None-before-legacy**: any body that is NOT a `global_preset` body ends
  by setting the home-screen picker to **None** (when the account has
  global presets at all) - otherwise battle entry re-applies the
  still-selected global preset over what was just hand-equipped.
- **Corruption advisory** (warning, not refusal): v29 presets AUTO-SAVE. A
  manual `modules:`/`guardians:` list on a category that HAS presets, with
  no `<cat>_preset` selected first, permanently rewrites whichever preset
  is active. `warnings()` flags it; adding the `<cat>_preset` key declares
  which preset the mutation may land in and silences the advisory.
  Accounts with no presets in a category (lab not researched) keep the
  pre-v29 behavior unchanged, silently.
- Tournament setups under a `global_preset` body skip `card_tweaks()` (the
  preset re-applies the saved deck at entry) - fold deck tweaks into the
  in-game card preset instead.
- **`cards_restore: <preset>`** (shard/quest blocks): the deck re-selected
  on the cards screen when the block ends. A following `global_preset`
  block applies only at battle entry and would leave the specialized deck
  (the shard deck) selected - and the SELECTED preset is where later card
  mutations land. Same ownership rule as `cards`; refused inside
  `global_preset` bodies; failure degrades loudly (`shard_cards_restore`
  event), never crashes a completed block.
- **Manual `modules:` on v29** goes through the redesigned screen's detail
  dialog (inventory icon -> single Equip button; the category picks the
  slot, so the plan's slot names are ignored there). The equip mutates the
  ACTIVE module preset permanently, so quest loadouts pair it with
  `modules_restore: [[<module>, primary]]` - the module(s) the quest
  runner re-equips after its last cycle to undo the displacement. Same
  ownership rules as `modules`; refused inside `global_preset` bodies;
  covered by the corruption advisory when no `module_preset` is declared.

### count is shard-only - ONE COUNTING AUTHORITY
`count` reaches exactly one consumer - `flows/shard.py --loops`. On any other
kind it is refused, because every other kind already has its own name for
"how many": `rides` for `uw_grant_quest`, `cycles` for `cycle_quest`.

COIN DID NOT GET ONE IN P6, and that was the ruling, not an oversight.
"How many coin runs today" is a SCHEDULING question: the plan block
already answers it (`plan.days.<day>[].count`), and its answer is the one
persisted per day in `daystate`, so an aborted day resumes where it
stopped. A second count on the blueprint would be a per-spawn cap that the
plan cannot see and the day counter cannot reconcile - two authorities
disagreeing about one number, which is how a day silently runs twice or
not at all. The refusal names the plan block rather than merely saying no.

A tournament blueprint is one entry, and a tournament BLOCK is one per day
for the same reason (the ticket auto-starts the run; 10 -> 20 -> 30 gems).

## policies
```yaml
policies:
  uw_policies:
    farm_cl_choreo:
      baseline: {death_wave: true, golden_tower: true, poison_swamp: true,
                 black_hole: true, spotlight: true}      # -> preset uw_wanted
      chain_lightning:
        mode: fleet_marks         # always_on | fleet_marks | off_until_wave | off
        always_on_above: [4080, 4120]
        pre_mark_waves: [5, 25]
        off_after_waves: [53, 72]
    tourney_cl:
      baseline: {}
      chain_lightning: {mode: off_until_wave, on_above: [500, 550]}
  rescue_policies:                # see RULES below
    high_tier_wall: {...}
    tournament_any_falling: {...}
  gather:
    all_on:  {flying_gem: true, gem_delay_sec: [3, 10], ad_gems: true,
              quests_8h: true, quest_rewards: true, guild: true}
    gems_only: {flying_gem: true, gem_delay_sec: [3, 10], ad_gems: false,
                quests_8h: false, quest_rewards: false, guild: false}
  shopping_lists:
    default_sweep: [ ...verbatim presets.normal_run.shopping... ]
  chores:
    - {name: quest_scan, enabled: true}
    - {name: shatter, enabled: false}     # this player keeps their blues
```

### LABEL - the human name, on every policy family
Every policy in `gather`, `uw_policies`, `rescue_policies` and
`shopping_lists` may carry `label: <non-empty string>` - the display name
the dashboard shows ("Chain Lightning Farming Choreography") while the KEY
stays the identifier other settings refer to. Consumed by the dashboard UI
only; the compiler deliberately ignores it (display data, not behavior).
An empty or non-string label is refused rather than rendered blank.

### CHORES - the between-run registry, per-profile opt-out
CONSUMED by `chores.run_due()` (the orchestrator's restart-from-home path):
the list is an OPT-OUT surface, not an allowlist. A chore the list does not
name stays enabled - a chore added to the registry later does not silently
stop running for profiles written before it existed. A disabled chore is
skipped WITHOUT being marked done, so re-enabling it later the same day
lets it run in the next gap. Unknown names are refused at validation
(`chore_names` in vocab() is the legal set, priority order). With a profile
bound but its policy unreadable, run_due() skips ALL chores and logs
`chores_skipped` - running a chore the player disabled (shattering their
modules) is the harm; a deferred nice-to-have is not. Legacy path (no
`active_profile`): everything enabled, bit-for-bit.

### SHOPPING - per-user enable/disable + priority (user, 2026-08-18)
A shopping list is an ORDERED list of directives; list order IS priority
(the sweep walks it top to bottom, exactly as `shopper.Shopper` does
today). Every directive carries `enabled` so a user can switch any item
off without deleting it. Vocabulary is derived from the engine's stat
templates (`templates/stats/*.png`, 12 today: bounce_shot_range,
critical_factor, damage, damage_per_meter, death_defy,
enemy_attack_level_skip, enemy_health_level_skip, health, health_regen,
land_mine_radius, super_crit_chance, super_crit_mult) - the dashboard
offers exactly those, grouped by tab (attack / defense / utility).
```yaml
shopping_lists:
  default_sweep:
    enabled: true                     # master switch: false = no shopping at all
    directives:
      - {enabled: true, tab: utility, stats: [enemy_attack_level_skip, enemy_health_level_skip], mode: repeat}
      - {enabled: true, tab: defense, stats: [death_defy, land_mine_radius], mode: once}
      - {enabled: true, tab: defense, stats: [health, health_regen], mode: best_cost}
      - {enabled: false, tab: attack, stats: [critical_factor], mode: once}   # user turned off
      - {enabled: true, tab: attack, stats: [damage], mode: clicks, clicks: 3}
```
Modes (existing shopper semantics, unchanged): `repeat` (buy while
affordable, every sweep), `once` (one purchase per run), `best_cost`
(cheapest-first burst), `clicks` (fixed N clicks). Compiler output: the
flat preset `shopping` key = the enabled directives in order (disabled
ones dropped; `enabled` keys stripped) so `Shopper.__init__` needs no
change; master `enabled: false` -> `shopping: []` (Shopper.finished is
then True immediately - no panel visits). Legacy lists without `enabled`
keys are treated as all-enabled (migrator emits explicit `enabled: true`).
```yaml
# (end of policies section)
```

### RULES - rescue policy vocabulary (composable; two execution tiers)
```yaml
rescue_policies:
  high_tier_wall:
    arm: {'on': second_wind, immunity_sec: null, watch_sec: 30}  # or: always
    #    ^^^^ QUOTED. Bare `on:` is the boolean true in YAML 1.1, so an
    #    unquoted key here vanishes and the policy is silently UNARMED.
    end_sprint_after_sw: false
    rules:
      - when: {bar: wall, below: 0.02, falling_samples: 2, deadband: 0.01}
        do:   {burst: {cancel_sprint: true, fire: demon_mode, retaps: 3}}
      - when: {wall_collapse: {from_above: 0.3}}
        do:   {burst: {cancel_sprint: true, fire: demon_mode, retaps: 3}}
      - when: {fleet_mark: {after_waves: 3, window_waves: 60}}
        do:   {fire: {button: nuke, throttle_sec: 5}}
```
A rule is `{when, do}` plus two optional siblings, `repeat` (default
false = fire at most once per run) and `refire_sec` (the rule's own
cooldown floor; default 5.0). The composable half, all Tier B:
```yaml
      - when: {wave_between: [1000, 2000]}
        do:   {toggle_uw: {weapon: chain_lightning, want_on: false}}
      - when: {second_wind: {state: after_immunity, min_procs: 2}}
        do:   {cancel_sprint: true}
      - when: {bar: hp, below: 0.25, falling_samples: 2, deadband: 0.02}
        do:   {burst: {fire: demon_mode}}      # no retaps: Tier A's alone
        repeat: true
        refire_sec: 20
      - when: {death_screen: true}
        do:   {stop_after_run: true}          # the only death-phase action
```

Triggers: `bar` (wall|hp; below; falling_samples; deadband),
`wall_collapse` (from_above), `fleet_mark` (after_waves, window_waves),
`wave_at_least` (N), `wave_between` ([lo, hi]), `second_wind` (state:
open|closed|after_immunity|any; min_procs), `death_screen`.
Actions: `burst` (fixed 3-tap sequence: sprint -> Yes -> DM; fire,
cancel_sprint, retaps, require_match, require_ready), `fire`
(button, require_ready, throttle_sec, refire_guard_sec), `toggle_uw`
(weapon, want_on), `cancel_sprint`, `switch_cards` (preset - **refused, see
below**), `surrender_retry`, `stop_after_run`.

### `switch_cards` is refused in EVERY phase (pending a verified route)
There is no verified route from a live battle to the cards screen.
`loadout.apply_cards` -> `tourney.open_nav` opens with a FIXED tap on the
bottom nav row and its return leg polls for HOME - both written for a game
sitting at Home - and no template of the in-battle nav row exists under
`templates/`, so from a battle nothing can confirm that first tap is
landing on anything. A tap into an unknown screen is what CLAUDE.md #3
and #6 forbid, and this runs on coin farms as well as on paid tournament
entries.

The runtime retires the action the first time it SEES the rule
(`orchestrator._rule_admits_action`), in every phase. So the compiler refuses it
too, at validation AND at compile: a rule the runtime always retires is
the accepted-but-ignored shape this schema exists to abolish - it renders
in the dashboard, reads as configured, and does nothing. The death-phase
refusal below is the same obstacle, one screen later, and is unchanged.

The word STAYS IN THE VOCABULARY, marked `PENDING VERIFIED ROUTE` in
`vocab()`, so the editor shows the truth rather than hiding the feature.
What would make it real is written down once, in
`orchestrator.run_in_run_actions`: a template of the nav row as it looks DURING a
run, a return leg that verifies the battle instead of Home, and one
observed live excursion.

`toggle_uw` takes **`want_on`**, not `on`: YAML 1.1 parses a bare `on:`
key as the boolean `true`, so `{weapon: x, on: false}` loads as
`{weapon: x, True: false}` - the parameter disappears and the toggle
switches the weapon ON. (`config.yaml` quotes `'on':` in every `arm`
block for the same reason.) It compiles to the key `on`, which no YAML
parser ever sees.

**Tier A (compiled into the greedy wall watch, sub-second):** at most one
`bar` rule + one `bar`+`fire: nuke` + one `wall_collapse` + one
`fleet_mark`, all under `arm.on: second_wind`. Compiler emits the flat
`abilities` dict orchestrator.py reads today: hold_until_second_wind,
post_sw_watch_sec, sw_immunity_sec, end_sprint_after_sw, rescue_bar,
dm_below, nuke_below, nuke_on_fleet {after_waves, window_waves,
throttle_sec, require_ready}, plus the scalars falling_samples, deadband,
collapse_from, burst_cancel_sprint, burst_retaps, burst_require_match,
burst_require_ready, hp_nuke_require_ready, refire_guard_sec (hoisted to
locals at watch entry - no per-sample interpretation).
**Tier B (main observe loop, ~1s):** everything else -> compiled into
`preset["rules"]`, an ORDERED list of normalized rule dicts, interpreted
once per main-loop pass. Shape below. Latency class per rule surfaced to
the dashboard.

**WHAT TIER A BUYS, NOW THAT TIER B RUNS EVERYTHING (P4).** The main-loop
interpreter evaluates the whole vocabulary, wall bar included, so a rule
is never refused for lack of a runner. What the tiers differ in is
LATENCY, and for the wall that difference is the whole game: it goes from
full to dead in about two seconds, so a ~1s rule watching it is an
OBSERVATION (stop the run, swap cards, log) and only the Tier A slot is a
RESCUE. The compiled `latency` field records which one a rule became, and
the four Tier A slots are the only place `arm.on: second_wind` matters.

Slot rules that follow from what orchestrator.py actually reads:
  * the `bar`+`fire: nuke` slot takes **`bar: hp` only** - `nuke_below` is
    read on the hp branch, while a wall rescue is handed wholesale to the
    fast watch, which fires Demon Mode via the burst. A `bar: wall` +
    `fire: nuke` rule is therefore a Tier B rule, not a refusal.
  * the `wall_collapse` slot exists only in a policy that also has a
    `bar: wall` rule - `collapse_from` is hoisted by that watch and read
    nowhere else. Elsewhere the collapse rule is a Tier B rule.

Compiler REFUSES: a wall trigger (`bar: wall` / `wall_collapse`) with
player.wall false; bar:wall + bar:hp in one policy (the Tier A rescue
watches exactly one bar); unknown trigger/action names; unknown weapon,
button, card preset or Second Wind state; missing required params.

It also refuses every rule that would compile to something NO CODE READS -
"accepted but ignored" is the failure mode profiles exist to abolish, so a
rule the compiler keeps is a rule that runs:
  * Tier A `fire` params with no compiled home: `throttle_sec` anywhere but
    a `fleet_mark` rule (the rescue nuke is rate-limited by the refire
    guard, not a throttle of its own).
  * `require_match` / `retaps` on a TIER B `burst` - `require_match`
    gates the wall watch's fixed-coordinate fallback tap and `retaps` is
    its blind-fire confirmation loop; a main-loop burst goes through
    `fire_button`, which has neither. `require_ready` is that site's gate
    (and the mirror image holds in Tier A: see the table below).
  * `falling_samples` / `deadband` on a Tier A `bar`+`fire: nuke` rule -
    the watch hoists those from the bar+`burst` rule, and the threshold
    nuke reads a level only.
  * **every action but `stop_after_run` under `death_screen`.** The stats
    dialog is not a battlefield and it is not Home either, and the runtime
    refuses all five loudly, so accepting one here would compile a rule
    that validates, lists in the dashboard, and is retired with
    `rule_unsupported` the first time the player dies. The message names
    the obstacle, per action:
      * `fire` / `burst` / `cancel_sprint` - no ability row, no sprint, no
        wall, so the tap lands at a fixed coordinate inside a menu.
      * `switch_cards` - `loadout.apply_cards` navigates FROM HOME and the
        stats dialog has no verified route there. Between-run card swaps
        belong to the chores path (P5/P6), not to a death rule.
      * `surrender_retry` - `shard.abandon_run` surrenders a LIVE battle;
        on the stats dialog the run is already over, so it is null.
    `stop_after_run` survives because it writes the run flag and touches
    no screen at all.
  * `surrender_retry` reachable from a **tournament** blueprint - a
    tournament run is never cancelled (the entry is paid for and the next
    costs more). Third lock, after tourney.end_round and shard.abandon_run.
  * A rule-level `refire_sec` next to a `fire` action's `throttle_sec` /
    `refire_guard_sec` - two spellings of one compiled floor, so one of
    them would be dropped.
  * `repeat` / `refire_sec` on a TIER A rule - the fast watch keeps no
    per-rule bookkeeping (it re-decides from the hoisted scalars every
    sample and rate-limits through `refire_guard_sec` / the fleet
    `throttle_sec`), so both are main-loop-only fields.
  * A flag TRIGGER that is not exactly `true` (`{death_screen: false}`
    reads as "switched off" and fires on every death - the trigger is
    recognised by its name).
  * A flag action that is not exactly `true` (`{stop_after_run: false}`
    reads as "does nothing" but fires under presence-dispatch).

### The compiled Tier B rule - the interpreter's contract
`preset["rules"]` is a list, IN POLICY ORDER, of dicts shaped exactly:

```python
{"id": "high_tier_wall#3",                  # str, stable; see below
 "when": {"kind": "wave_at_least", ...},    # trigger spec, always a mapping
 "do":   {"kind": "stop_after_run", ...},   # action spec, always a mapping
 "repeat": False,                           # bool, never absent
 "refire_sec": 5.0,                         # float, never absent, > 0
 "latency": "main_loop",                    # or "death_handler"
 "requires": {"abilities": ["demon_mode"],  # list[str], may be empty
              "wall": False,                # bool
              "card_presets": [],           # list[str], may be empty
              "uws": []}}                   # list[str], may be empty
```

Invariants the interpreter may rely on, because the compiler enforces
them:
  * **THE RUNTIME APPLIES NO DEFAULTS.** Every key is present on every
    rule, with a real number; absence is an ADMISSION ERROR at the
    interpreter (retire the rule and log it), never a shrug and a fallback.
    The compiler is the single source of truth for every default in this
    shape. This is not style: the compiler read an unstated
    `falling_samples` as 1 while the evaluator read a missing key as 0, and
    a compiled `bar: hp, below: 0.3` rule sat under its threshold for three
    passes without firing while the hand-written equivalent fired at once.
  * **Every number is finite.** NaN and infinity are refused at validation
    AND at compile - `now < nan` is False forever (no cooldown at all) and
    `now < inf` is True forever (the rule never fires again), and neither
    raises anywhere.
  * **Dispatch on `kind`, never on key presence.** Presence-dispatch is
    what would make `{stop_after_run: false}` stop a run.
  * **Numbers are numbers.** Nothing needs parsing, no value is a string
    except `kind`, `bar`, `button`, `preset`, `weapon`, `state` and `id`.
  * **`id` is stable**: `<rescue policy name>#<index of the rule within
    that policy>`. It does not move when an earlier rule is absorbed into
    Tier A, and two blueprints sharing a policy log the same id. Key
    per-run state (fired / next-allowed / retry counts) on it.
  * **`repeat: false` means at most once per RUN** - state resets with the
    run, not with the process. `refire_sec` is the floor between two
    firings of the SAME rule and applies to repeat and retry alike.
  * **`latency: "death_handler"`** rules (every `death_screen` rule, and
    only those) are NOT evaluated by the observe loop - that loop has
    exited by the time the screen exists. They belong to the death path,
    and their `do.kind` is always `stop_after_run`: the compiler emits no
    other action at this latency, so the death phase needs exactly one
    branch and no screen-touching code.

Trigger specs (`when`):

| kind | fields |
|---|---|
| `wave_at_least` | `wave` (int) - fire when the tracked wave >= wave |
| `wave_between` | `value` ([lo, hi] ints, inclusive) |
| `bar` | `bar` ("hp"\|"wall"), `below` (0..1), `falling_samples` (int, default 0), `deadband` (float, default 0.0) |
| `wall_collapse` | `from_above` (0..1) - the PREVIOUS sample was above this |
| `fleet_mark` | `after_waves` (int, default 1), `window_waves` (int, default 60) |
| `second_wind` | `state` ("open"\|"closed"\|"after_immunity"\|"any"), `min_procs` (int, default 1) |
| `death_screen` | none |

A rule that states only `below` is a LEVEL question ("hp is under 30%"),
so it compiles to `falling_samples: 0, deadband: 0.0` - no fall required,
which is what the shipped evaluator did. `falling_samples > 0` turns it
into a DIRECTION question: the interpreter keeps the previous fill per
rule id and counts consecutive drops bigger than `deadband` (a rise
resets it). Tier A's 2 / 0.01 belong to the 3Hz wall watch, where two
samples cost 300ms rather than two seconds.

Action specs (`do`):

| kind | fields |
|---|---|
| `fire` | `button` ("nuke"\|"demon_mode"), `require_ready` (bool, default false) |
| `burst` | `button`, `cancel_sprint` (bool, default true), `require_ready` (bool, default false) |
| `switch_cards` | `preset` (str) |
| `toggle_uw` | `weapon` (UW name), `on` (bool, default true) |
| `cancel_sprint`, `surrender_retry`, `stop_after_run` | none |

`fire`'s `throttle_sec` / `refire_guard_sec` are NOT emitted in the action
spec: at Tier B they ARE the rule's cooldown, so they compile into
`refire_sec` (rule-level `refire_sec` first, then `throttle_sec`, then
`refire_guard_sec`, then 5.0). A Tier B `burst` runs through `fire_button`
like the hp-path rescue - `require_ready` gates it, there is no
fixed-coordinate fallback to gate with `require_match`, and NO `retaps`:
`fire_button` confirms its own tap, so the retap loop is Tier A's alone
and stating one here is refused rather than compiled into silence.

### Ownership gating
`requires` is derived from the rule's trigger and action, never declared:
a `fire`/`burst` requires its button, a `switch_cards` requires its card
preset, a `toggle_uw` requires its weapon, a wall trigger requires the
wall. `validate()` refuses a profile whose `player.*` does not back every
rule of a policy a blueprint references (and, as before, refuses any
rescue at all unless `player.abilities_verified` is exactly `true`).

Because a compiled preset outlives its validation - it is installed into
`CONFIG["presets"]`, listed in the tray and launched hours later, possibly
after a scan rewrote `player.*` - playerprofile exports the same check
over the COMPILED artefact:

```python
playerprofile.required_capabilities(compiled)  # -> {abilities, wall, card_presets, uws}
playerprofile.check_capabilities(compiled)     # -> list[str]; [] = runnable
```

`check_capabilities` reads Tier A out of `abilities{}` (`rescue_bar: wall`
-> the wall, `dm_below` -> Demon Mode, `nuke_below`/`nuke_on_fleet` ->
Nuke) and Tier B out of `rules[].requires`, then compares against
`player.*` (the bound profile's by default, so a runner can call it with
one argument at spawn). **Tier A is why the spawn gate must check EVERY
compiled preset, not only the ones with rules**: the golden farm presets
carry `rules: []` and still tap Demon Mode and the Nuke.

IT FAILS CLOSED, and only for compiled presets. A preset carrying the
compiler's `_source` stamp with no `player` section to check against is a
REFUSAL naming that fact, and so is a requirement whose inventory list is
missing (`player.card_presets: null`) rather than merely lacking the
entry - "never scanned" is exactly the account where a wrong tap is most
likely. A LEGACY preset (no `_source`) is exempt and always returns `[]`;
nothing in it was derived from `player.*`.

The fire gate is compiled PER SITE, because orchestrator.py's four fire sites
disagree - and they are not all asking the same question:

| site | rule | param | compiled key | default |
|---|---|---|---|---|
| wall burst (fast watch) | `bar: wall` + `burst` | `require_match` | `abilities.burst_require_match` | `false` |
| hp burst / Demon Mode | `bar: hp` + `burst` | `require_ready` | `abilities.burst_require_ready` | `false` |
| hp threshold nuke | `bar: hp` + `fire` | `require_ready` | `abilities.hp_nuke_require_ready` | `true` |
| fleet-mark nuke | `fleet_mark` + `fire` | `require_ready` | `abilities.nuke_on_fleet.require_ready` | `true` |

**READINESS vs MATCH.** Three of the four sites call `fire_button`, which
tests whether the button reads ready - they can afford to wait for a
button they can see. The wall burst cannot ask that at all: inside
`_fast_wall_watch` it is three instant `act.tap()` calls off one frame,
with no `fire_button` anywhere. Its only question is whether the Demon
Mode glyph was MATCHED, because an unmatched one falls back to the fixed
`RESCUE_DM_PT` coordinate.

**The same `burst` rule therefore has two gates, and which one is live
depends on the policy's rescue bar.** Writing the other is refused:
"require_ready gates the hp-path Demon Mode; require_match gates the wall
burst's fallback tap".

Both burst gates default `false`, which is today's behaviour at both
sites: an unmatched icon still taps the fallback coordinate, and the hp
Demon Mode still fires without waiting for ready, because a missed glyph
must not cost the run. Set either `true` on an account whose abilities are
not confirmed (see `abilities_verified`). There is no flat key for any of
this: one would have to give a single answer to four different questions.

## plan  (P5 - what combo.due() walks instead of its constants)
```yaml
plan:
  week: {default: farm_day, wednesday: tourney_day, saturday: tourney_day}
  days:
    farm_day:
      - {block: shards, blueprint: shard_daily, after: "08:00", count: 100}
      - {block: coin,   blueprint: coin_default}          # last = filler
    tourney_day:
      - {block: tournament, blueprint: tourney_main, after: "19:00", count: 1}
      - {block: shards,     blueprint: shard_daily,  after: "08:00", count: 100}
      - {block: coin,       blueprint: coin_default}
```
`week` maps a weekday to a named day plan; `default` covers every day not
named. ONE SPELLING PER DAY - the full lowercase name (`monday` ...
`sunday`), never `mon`/`wed`/`0`: a profile that could carry both
`wednesday:` and `wed:` needs a precedence rule nobody would ever read,
guarding a collision with no right answer, so the alias is refused in the
source and the collision is unconstructible. A week with no `default` must
name all seven, or the unnamed days would have nothing to run: refused.

A block is `{block, blueprint}` plus three optional gates:

| key | meaning |
|---|---|
| `block` | the scheduler category (`coin`/`shards`/`tournament`/`quest*`) - it must match the blueprint's kind, so a `tournament` block cannot point at a shard blueprint |
| `blueprint` | must exist; its `kind` decides the runner |
| `after` | `"HH:MM"`, inclusive. Omitted = 00:00 |
| `until` | `"HH:MM"`, EXCLUSIVE. Omitted = end of the local day |
| `count` | runs of this block today. Omitted = unbounded |

**ORDER IS PRIORITY, not clock order.** The scheduler takes the FIRST block
whose window is open and whose runs are not spent. A block with no `after`,
no `until` and no `count` is eligible at every minute, so the last such block
is the day's filler - and any block written below it can never run (a
`warnings()` advisory, not a refusal: a dead block costs nothing at runtime,
unlike a dead rescue rule, which costs the run it was written to save).

That ladder is exactly combo's hand-written one: the tournament outranks the
shard block because it is the one thing with a CLOSING WINDOW - an entry
missed is gone, where shard runs are only ever deferred.

### Edge semantics, decided and pinned
  * **Eligible** = `after_min <= now < until_min` AND the block's run count
    for today is below `count`. `until` is exclusive so 08:00-19:00 and
    19:00-onwards do not both own 19:00.
  * **A window that never opens is refused** (`until` <= `after`). A block
    that should run across midnight belongs to BOTH days, so write it in
    both - a wrapping window would silently own no minute at all.
  * **Count exhausted -> the next eligible block**, which is how "100 shard
    runs, then coin for the rest of the day" is spelled. The counter is
    persisted per BLOCK ID (`daystate`, date-scoped), so an aborted day
    resumes where it stopped instead of restarting the block. This
    generalizes the single `shard_runs` counter combo keeps today.
  * **The day boundary is the local date change**, i.e. daystate's own scope.
    A run in flight at midnight is not interrupted; the next decision after
    it uses the new day's plan and the new day's (empty) counters.
  * **A tournament block is ONE ENTRY PER DAY**, always - and that is
    counted over the DAY, not over the block: `count` may be omitted or `1`,
    anything higher is refused, and a SECOND tournament block on the same
    resolved weekday is refused too (by `validate()` and again by
    `compile_plan()`, which raises naming both ids - two blocks would buy two
    tickets). The ticket purchase
    auto-starts the run and the gem cost escalates 10 -> 20 -> 30, so this is
    not a default anyone may raise. It is also what combo does today - it
    marks the phase done after a single entry.
  * **Plan `count` is not blueprint `count`.** The blueprint-level one is
    shard-only and becomes `flows/shard.py --loops`; this one is "runs of this
    block per day" and is legal on every kind, because the plan is where "how
    much of my day" belongs.

### The plan is a TRI-STATE, and the third state does not exist
| profile | `compile_plan()` | `CONFIG["plan"]` | the scheduler |
|---|---|---|---|
| no `plan` section (or no `active_profile`) | `None` | absent | runs its own constants, and says so once |
| `plan` with blocks | `{"week": {...}}` | the compiled week | runs the plan |
| `plan` that resolves to no blocks | `ProfileError` | never installed | - |

**ABSENCE PROPAGATES AS ABSENCE.** `compile_plan()` returns `None` for a
rules-only profile - never an all-empty week. An empty week is
indistinguishable from a plan that WAS authored and came out empty, which the
scheduler is right to treat as a defect and hold on; returning one for a
profile that never had a plan idled the farm instead of running the constants
it never meant to replace. So an empty week is not a legal return value at
all: a caller holding a dict knows every day was resolved, and a caller
holding `None` knows there was nothing to resolve.

The last row is the point. An empty plan is REFUSED at validate AND at
compile - `plan: {}`, a `days:` with nothing in it, day plans that are all
empty lists, or a `week:` whose references resolve to none of them, all with
the same message: *"a plan with no blocks schedules nothing - remove the plan
section to use the legacy constants, or add blocks"*. The check is the
RESOLUTION, not the spelling, which is the only way to catch the last shape.

It is refused because it is the one state nobody can act on. A missing plan
means "use the constants" and a plan with blocks means "use the plan"; a plan
that resolves to nothing means neither, so a scheduler handed one either
idles a tower all day or quietly falls back to the constants the player
thought they had replaced. Both are worse than a refusal at load, and both
are silent.

Which is also why `plan` is the one OPTIONAL top-level section: leaving it
out has to be sayable, or "remove the plan section" is not an instruction
anyone can follow.

### The compiled plan - the scheduler's contract
`compile_plan(profile) -> dict`, also installed as `CONFIG["plan"]` by
`materialize()` (in place, same discipline as the presets - combo holds its
CONFIG reference from import time). No `active_profile`, or a profile with no
`plan` section, -> no `plan` key at all; re-materializing a profile that
dropped the section drops the artefact too, so `"plan" in CONFIG` is a
complete answer rather than a hint.

```python
{"week": {"monday": [ ...ordered blocks... ], ..., "sunday": [...]}}
```

Keys are the weekday names in `datetime.weekday()` order, so the runtime
indexes `WEEKDAYS[now.weekday()]` - no mapping table, no locale-dependent
`strftime("%A")`. `week`/`days` are resolved away: they are how a human
avoids writing the same day seven times, not something a scheduler should
dereference on a poll. Each block is exactly:

```python
{"id": "monday#0",               # str, STABLE: the daystate counter key
 "day_plan": "farm_day",         # which named plan this day resolved to
 "block": "shards",              # scheduler category (combo's phase name)
 "blueprint": "shard_daily",     # profile blueprint
 "preset": "bp_shard_daily",     # CONFIG["presets"] key to launch
 "kind": "shard",                # blueprint kind (decides the runner)
 "after_min": 480,               # int minutes since local midnight
 "until_min": 1440,              # int, EXCLUSIVE (1440 = end of day)
 "after": "08:00",               # SOURCE ECHO - display only, may be null
 "until": None,                  # SOURCE ECHO - display only, may be null
 "count": 100}                   # int runs today, or None = unbounded
```

`after_min`/`until_min` are AUTHORITATIVE; `after`/`until` are the source
echo, for logs, the dashboard and a reader that would rather see a clock.
They are compiled from the same field in the same place and pinned equal by
test, so they cannot drift - but a DECISION must be made on the minutes,
because `after: null` means "from midnight" and resolving that at runtime is
the runtime applying a default, which is the one thing this shape exists to
abolish.

Invariants the scheduler may rely on:
  * **Every key is present on every block.** THE RUNTIME APPLIES NO
    DEFAULTS - absence is an admission error, never a fallback. Same rule as
    the Tier B rules, for the same reason: a default in two places drifts.
  * **Clocks are integers**, parsed once. Nothing at runtime parses `"HH:MM"`.
  * **`id` is `<weekday>#<index>`** and does not move when another day is
    edited. It names the WEEKDAY, not the day plan, so two weekdays sharing a
    plan still count their runs separately - which is what "Wednesday's
    tournament is done" has to mean.
  * **IDENTITY FOR CONTINUITY IS NOT THE ID.** The id is a per-day COUNTER
    KEY, and it is weekday-prefixed on purpose, so it changes at every
    midnight by design - Tuesday's count is not Wednesday's. A runner in
    flight across the boundary must therefore be recognised by
    `(preset, kind, count-bounds)`, not by `id`: same preset, same kind, and
    a count window it is still inside means the SAME WORK, and killing it to
    respawn the identical runner is a lost run. `tools/plan_sim.py` prints an
    id change at 00:00 as "day boundary, same preset" and does not count it as
    drift; a PRESET that moves under a steady block name is drift, and is.
  * **`runner`/`runner_args` are NOT here.** They live on the compiled preset
    (`CONFIG["presets"][block["preset"]]`), one source, no drift.

`tools/plan_sim.py` walks the week and diffs the compiled plan against
combo.py's constants, read out of its source rather than retyped. It probes
`(minute, block, id, preset)` - a name-only diff cannot see the right block
name backed by the wrong preset - and takes `--minutes` for a full
1440x7 walk (the 15-minute default grid cannot see a 07:59 drift) and
`--fill block=HH:MM` to put the counter switchover on the clock as well. An
empty diff on `profiles/default.yaml` is P5's core verification, and
`tests/golden_default_plan.json` freezes that plan.

## Compilation contract (profile.py)
`compile_preset(profile, blueprint_name) -> dict` produces a FLAT preset
dict shaped exactly like today's `orchestrator.preset()` OUTPUT (post-merge, no
`base:` key). `materialize(profile)` installs every blueprint into
`CONFIG["presets"]["bp_<name>"]` in place (same dict object - every module
holds a reference), plus `runner`/`runner_args` derived from kind for the
tray/dashboard. Called once right after `settings.select_instance()`.
Ownership gating: loadout cards/guardians/modules and policy UWs must be
in `player.*` -> refuse with a message naming the missing capability.

## vocab() - the editor's value spaces (P6)
`playerprofile.vocab()` returns ONE JSON-able dict of every value space an
editor needs. The dashboard imports it and jsonifies it: no Flask here, no
CONFIG, no bound profile, and no I/O beyond the cached
`templates/stats/*.png` listing that `shop_stats` is derived from.

IT IS DERIVED, NEVER RETYPED. Every enum is the same tuple the validator
checks against, so a dropdown cannot go stale behind the compiler - the
failure it replaces is an editor offering four rule actions after the
vocabulary grew to eight.

### Shape
Every node - section, field and nested field alike - is a SPEC:

```
{"type": "enum|int|float|bool|str|list|object",
 "values": [...] | null,        # enum members, or [true] for a flag
 "range": [low, high] | null,   # INCLUSIVE, null for an open end
 "doc": "one line"}
```

All four keys are always present, `null` where the constraint does not
apply, so a renderer can subscript them instead of guessing which keys this
particular field brought. `type: "object"` is the nesting case and adds
exactly two keys, `fields` (name -> spec) and `required` (the ones the
author cannot omit); it is the only type word that is not a leaf type, and
it exists so that every node in the tree answers to `["type"]`. A range
that cannot be written as an inclusive pair (a float that must be strictly
positive) states `[null, null]` and says so in `doc` rather than claiming a
bound the validator does not actually accept.

Unknown sections render as generic editors - which is the point. This
module keeps growing sections, and the dashboard must not need a release to
show them.

### The sections
`playerprofile.VOCAB_SECTIONS` is the one list of them, in order:

| section | type | what it is |
|---|---|---|
| `kinds` | enum | blueprint kind: it picks the runner and decides which fields are legal |
| `blueprint_fields` | object | the legal source fields of a blueprint, PER KIND (see below) |
| `loadout_specials` | enum | `loadout` values that are not a `config.yaml` loadouts key (`as_is`) |
| `bar_names` | enum | the two bars a rule can watch |
| `buttons` | enum | abilities the orchestrator owns taps for |
| `sw_states` | enum | Second Wind states the RunState can already answer |
| `cl_modes` | enum | `chain_lightning.mode` |
| `uw_names` | enum | every ultimate weapon in the game - editors list ALL of them; `player.uws` ownership only decides what applies on an account |
| `gather_keys` | object | `policies.gather` fields |
| `shop_tabs` | enum | workshop panel the sweep opens |
| `shop_modes` | enum | how a shopping directive spends |
| `shop_stats` | enum | stats the sweep can FIND on screen, from the template library |
| `rule_triggers` | object | rescue-rule `when` vocabulary, per trigger, with its params |
| `rule_actions` | object | rescue-rule `do` vocabulary, per action, with its params |
| `death_screen_actions` | enum | the only actions a `death_screen` rule may take |
| `in_run_action_kinds` | enum | tournament `in_run_actions` vocabulary (v1) |
| `weekdays` | enum | `plan.week` keys, in `datetime.weekday()` order |
| `block_fields` | object | one plan block |
| `plan_tri_state` | str | the plan tri-state, as one doc line |
| `chore_names` | enum | between-run chores a profile may switch off via `policies.chores` |

WHAT IS NOT HERE: anything account-specific. Card presets, owned weapons,
loadout names and module slugs are PLAYER data (`player.*`, `config.yaml`
loadouts) and the editor reads those from the profile it is editing. This
is the vocabulary, and the vocabulary is the same on every account.

### `blueprint_fields` - per kind, and exact in both directions
Keyed by blueprint kind; each kind is an `object` spec whose `fields` are
the source fields a blueprint of that kind may carry, and whose `required`
are the ones it is refused for OMITTING. It exists because an editor that
infers a field's type from its current value renders `rides` as a bare
number box on a coin blueprint just as happily as on a quest one - with no
range, no doc, and no idea that the field will be refused at load.

PLACEMENT IS DERIVED, NEVER RETYPED: it comes off `_COMMON_FIELDS` and
`_KIND_FIELDS`, the same two tables `_validate_blueprint` refuses against.
There is ONE subtraction, and it is derived too - `count` sits in every
kind's table so that writing it gets the specific "use `rides` / use
`cycles` / it lives on the plan block" message instead of a bare "not a
legal field", but **shard is the only kind that consumes it, so it is the
only kind that offers it**.

| kind | fields beyond `kind`, `label`, `loadout`, `tier`, `policies` | required |
|---|---|---|
| `coin` | `cancel_sprint`, `max_wave`, `restart_via_home`, `shop_interval_sec`, `shopping` | + `tier` |
| `shard` | `count` | + `tier` |
| `tournament` | `gem_entry_max`, `in_run_actions`, `restart_via_home`, `shop_interval_sec`, `shopping` | - |
| `uw_grant_quest` | `grant_targets`, `reroll_at_wave`, `ride_to_wave`, `rides`, `uw_setup` | + `grant_targets` |
| `cycle_quest` | `cycle_sec`, `cycles` | + `cycles` |

Bounds are the ones validation really enforces. Where a bound is
ACCOUNT-RELATIVE it is not stated as vocabulary: `tier` is
`[1, null]` with a doc line saying the ceiling is `player.max_tier`,
because the vocabulary is the same on every account and that number is
not. `in_run_actions` and `uw_setup` are nested `object` specs (the first
is written as a LIST of its shape, which its doc says); `policies` nests
`uw`/`rescue`/`gather`.

`tests/test_playerprofile.py` cross-checks this section against a
validation probe per kind: every listed field is fed a legal value and the
profile must validate clean, and every field the kind does NOT list is fed
one and the profile must be refused by name.
