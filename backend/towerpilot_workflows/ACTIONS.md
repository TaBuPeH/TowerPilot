# Timed actions: the contract, the history and the rules

> **Status:** Active (item 6: the pilot, and the control surface generated from the catalog)
> **Type:** Rules
> **Created:** 2026-10-22
> **Updated:** 2026-10-27 (item 7, A3b kind B r18e; item 7, A3b kind A; item 7, A3a; item 6, A2-7: what a page reads)
> **Owner:** Backend
> **Spec:** [roadmap-items-6-7/spec.md](../logs/reviews/roadmap-items-6-7/spec.md)
> **Tags:** actions, scheduling, history, gems, shopping, harvest

## What it is

One contract for a timed action, called by the drivers that already exist. A driver asks a schedule policy "is this due?",
runs its own handler behind its own guards, and records the execution. There is no scheduler, no resident thread and no
second input owner: the device lease and the operations ledger stay the only owners of input and of recorded state.

| Concern | Owner | Imports |
|---|---|---|
| Definitions, schedule policies, field schema, catalog | `backend/towerpilot_workflows/action_contract.py` | stdlib only |
| The execution table and its transitions | `backend/towerpilot_workflows/actions.py` (`Actions`, a subclass of `Operations`) | the contract, the ledger |
| Host adapter and the `runtime.actions` projection | `backend/runtime/action_session.py` (`ActionSession`) | no device, adb or capture module |
| The bindings a driver registers (control, Run now, routine status) | `backend/runtime/action_bindings.py` | no device, adb, capture or settings module |
| Saved settings: the adapters the definitions name | `backend/player/saved_controls.py` (read by `player/run_controls.py` and `control/run_workspace.py`) | profile and gather modules |
| Control validation (`FIELDS`, documents) generated from the catalog | `backend/towerpilot_workflows/control_settings.py` | the contract |
| Dashboard query and the refresher's settlement | `backend/control/actions.py` | the above |

## The rules

1. **The policy decides "due" and "next".** `StartToStart` (ad gems), `AppearanceDriven` (floating gems: an attempt opens when the host recognizes the gem, with spacing only after input that was
   not confirmed), `AfterCompletion` (shopping) and
   `Backoff` (harvest launch attempts) are the only expressions of those cadences. A policy reads no clock, random source or
   file: the host passes `now` and draws its own random numbers, in the order the old code did.
2. **The host owns the state.** Reset scope does not change: gem windows and shopping reset per process, the harvest circuit is
   read from the operations rows.
3. **One writer.** `Actions.begin` / `finish` (each a `BEGIN IMMEDIATE` compare-and-set), `record_check`, `record_terminal` and
   settlement are the only writers of `action_executions`.
4. **Recording never changes behaviour.** A failure to record is counted (`ActionSession.unrecorded`) and logged, never raised.
5. **An execution is only as proven as its evidence.** A window or sweep that sent input and proved nothing is
   `failed` with effect `unconfirmed`; effect `confirmed` needs a reason the definition lists as evidence (`effect_of`). A battle ad
   whose offer vanished after the tap is `succeeded` with effect `unconfirmed` (no balance was read); only a Home balance rise
   (`balance_increased`) confirms it. A collector launch is `succeeded/launched` with effect `unconfirmed`: starting a process
   does not show the collector ran. Unknown is `null`, never zero.
6. **Settlement needs an exact fence.** An open row is closed `interrupted` only when its operation is terminal, its battle ended,
   or its battle moved to another generation. It keeps `finished_at` and `duration_ms` null. Settlement runs from the existing
   refresher, from a driver's own finalization, and as a courtesy in `begin` for the same instance. A GET never settles.
7. **Coalescing is narrow.** Only an automatic, no-input check that ended `refused` or `no_op` shares a row, with the open aggregate
   of the identical scope. A started, manual or key-linked execution is its own row.
8. **Reads never create anything.** Every read goes through `operations.readonly`; a checkout with no ledger answers an empty history.
9. **Controls reuse the existing routes.** A new action adds a catalog entry, a binding and (when it has saved settings) a saved
   adapter, never a route and never a dashboard branch. The single read route is `GET /api/actions/executions`. Nothing that
   dispatches a control, accepts or follows a Run now, publishes a routine or reads and writes a saved setting names an action
   (`test_action_dispatch_generated.py` checks the code, not the behaviour): each asks the catalog and the binding or adapter registered for it.
10. **Collection-only authority stays gem-only.** Harvest's overlay and guard name the two gem routines on purpose.

## Pilot coverage (this batch)

| Action | Policy | Callers recorded |
|---|---|---|
| `ad_gems` | `StartToStart` | all six `GemWatch` hosts: orchestrator, collector, shard batch, quest SM, quest ILM, harness |
| `floating_gems` | `AppearanceDriven` | the same six hosts (r14g made the tapper appearance-driven; the contract follows the shipped behaviour) |
| `shopping` | `AfterCompletion` | the orchestrator's `Shopper` (a sweep that sends input is a row; input-free sweeps are counted checks) |
| `harvest_launch` | `Backoff` | the dashboard's reconciler: every accepted launch, refusal and failure |

## What a page reads (A2-7)

A page renders any timed action from one item of `battle.runtime.actions` (built only by `ActionSession.project`) and the history reads, with no table of its own:

- **The item** names the action (`id`, `label`) and the configured instance (`instance`); `key` is the routine key, which is both the prefix of its live-control paths
  (`routines.<key>.<field>`) and its Run now token (`<key>[:<variant>]`). `descriptor.fields` lists what can be edited; `values` carries the value in force of EVERY listed field
  (the ledger's applied controls, so a control still waiting is not yet in force; a field the controls do not hold is `null`, unknown, never zero; `enabled` is the routine's own gate).
- **`last`** is the newest finished execution of the instance. `last.command_id` is the Run now command that started it (`null` for an automatic one), so one read links a command to its execution.
- **Staleness:** a publish that builds the projection stamps it `observed_at` with the epoch it writes as `runtime.server_time`; a publish that cannot build it leaves the last projection
  and its stamp behind, so a reader that sees `observed_at` older than `server_time` knows the item is not current.
- **History rows** (`GET /api/actions/executions`, and `executions.items` of `GET /api/battles/<id>`) carry `label`, the label the action has in the catalog in force (`null` for an action
  it no longer holds), added when the row is read and never stored. A battle block cut off at a page says `truncated` and hands out `next_before`, the cursor that
  `GET /api/actions/executions?battle_id=<id>&before=<next_before>` continues from (a complete block has no `next_before` key).
- **Recordings** for a page's tests: `dashboard_contract_pages_v1.json` (`battle_actions_projection`, `action_executions`, `action_executions_more`) for the production actions, and the TEST-ONLY
  `synthetic_action_contract_v1.json` for an action no production file names (`synthetic_action_contract.py` generates it through the real routes with `use_catalog`; it must never be copied
  into the app's own generated contract).

## Item 7, A3a: rewards and chores

The side-menu reward upkeep and the between-run chores are migrated to the same contract. Their numbers live in the catalog and nowhere else:

| Action | Policy (shared object) | Whose state | Recorded by |
|---|---|---|---|
| `daily_missions`, `guild_progress`, `event_missions` | `Cooldown(300)` (`REWARD_CHECK`) over `global_rewards:<account>:<key>:next` | account | `runtime/flow_history.py` (a check that finds no badge is one counted no-op row; a flow is an execution) |
| `quest_visit`, `guild_visit` | `Cooldown` with a sampled delay (`VISIT_SCHEDULE`: a repeat within 1200 s of the last visit draws 1500-2400 s) | this run | the same |
| `guild_store` | `EventOnly('guild_claim')`, raised by a guild flow that claimed something | this run | the same, trigger `dependency`, `host.parent_execution` = the guild visit |
| `free_store_gems` | `DailyCalendar(utc, 01:00 +/- 600 s, retry 300 s, close on confirmed)` | account | the same, `occurrence_key` = the UTC day |
| `quest_scan`, `shatter`, `housekeeping` | `DailyCalendar(local, floor 03:00, close on attempted)` | installation | `scheduling/chores.py` through an operation-scoped session |

- **The day is the date, the hour is a floor.** A chore is closed for the local date (it rolls at local midnight); 03:00 is only the hour from which the first Home gap may run it.
- **One attempted chore per gap (C1).** A chore that raised used the gap like one that completed. Before this, a failing chore let the next one run in the same gap, and a limit of two let three run.
- **Handler completion is not a game effect.** A flow that ends normally is `succeeded` with effect `unknown`, unless the host proved one (`FlowHistory.proved`): a guild flow that claimed
  milestones (`milestones_claimed`), a free-gems flow that saw the cooldown on the card (`cooldown_shown`). An action whose definition lists evidence is `succeeded` only with it; a flow that
  completed without proof is `no_op`.
- **A daily-state write the file refuses** (its lock stayed held) is logged (`daystate_write_failed`) and kept in this process (`global_rewards._unsaved`, `chores._unsaved`), so one busy
  file does not make a check or a chore repeat on every pass. A restart forgets it, which is the old crash behaviour.
- **Unreadable inputs are `blocked`, never zero.** NaN, infinity, a negative or a non-number clock reads as `invalid_state`; a time the calendar cannot express reads the same.
- **Removed:** `global_rewards.check_due/checked/store_due/store_claimed`, the orchestrator's free-gems cap helpers and the daily-state UTC counters (nothing called them).

## Item 7, A3b kind A (r18d): declarations, no timer moves

Ten catalog entries that only DECLARE. None has a field, a live control, a binding, a host that records it, or a Run now; no driver code, timer, owner or
collection algorithm changed. `restricted` is the one flag that decides what a person may never start, and `EventOnly` the one policy for an action with no time.

| Entry | Kind | Policy / reason | What it declares |
|---|---|---|---|
| `tournament_entry`, `tournament_ticket` | restricted | `EventOnly('tournament_block' / 'ticket_dialog')` | The ticket starts a run that cannot be cancelled and its gem cost escalates. Entry happens only in `tourney.setup()` of a tournament run: from the day plan's tournament block (once a day under the `combo` day lock) or from a person's Start of a tournament preset (`dashboard.py`, `orchestrator.py` and `tourney.py` do not consult the day lock). Never Run now. |
| `in_run_cards` | restricted, retired | `EventOnly('wave_rule')` | `orchestrator.run_in_run_actions` refuses at runtime; the compiled key stays for later. |
| `surrender` | restricted | `EventOnly('guarded_exit')` | Only `shard.abandon_run` and the rule surrender end a run, never a tournament. |
| `setup_capture` | restricted | `EventOnly('setup_scan')` | Setup's own run (human-started, consented, clean Home), surrendered by the same flow. |
| `max_wave` | restricted | `EventOnly('wave_target')`, host orchestrator | One attempt per run (`rs.max_wave_done`), tournament-locked, runflag fallback. |
| `repeat_after_death` | event only | `EventOnly('death_boundary')`, host orchestrator | The death handler's restart and the `repeat` gate's boundary. The `repeat` ROUTINE (its enable flag) is not this entry: it stays a literal gate, unprojected, until its migration. |
| `sm_ride` | event only | `EventOnly('wave_target')`, host quest SM | The ride to the wave target. Its lifecycle, the 15 s scan and the 300 s decision timeout stay with `QuestRun` and are not declared here (r18g). |
| `tournament_day_marker` | state consumer | `EventOnly('manual_tournament')`, scope installation | `tools/tier_ladder` marks `combo_tournament` through `daystate.mark_today`; the combo day lock reads it. |
| `perk_ban_cache` | state consumer | `EventOnly('perk_bans_applied')`, scope account | `interactions/perks` keeps the applied ban set under `perk_bans:<account>` (the active instance name when the instance sets no account) through `daystate.get_raw` / `set_raw`. |

- **A restricted definition cannot offer Run now.** `ActionDefinition` refuses `restricted` with `capabilities.run_now is True` (and a restricted flag without a reason); `run_now_tokens`
  still refuses whatever a binding claims. `test_action_restricted_declarations.py` proves both with a binding that claims it can run.
- **Substep throttles are classified, not wrapped** (row 15): `test_action_substep_classification.py` names every `now - rs.<x> > N` throttle of the orchestrator as a parent-owned
  substep (input reaches the screen) or infrastructure (none does), and fails for one it does not know.
- **Why `repeat_after_death` and not `repeat`:** a catalog entry named like a routine key would be projected into `runtime.actions` (a new row on the Dashboard) and would move the
  `repeat` gate's controls and the order of the control table. Neither is r18d's to change.

## Item 7, A3b kind B (r18e): main-loop upkeep with a floor or a ladder

Three catalog entries (family `rules`, host orchestrator; no field, control, binding or Run now) now carry the schedule of three main-loop callers. Each caller keeps the state it
already held and asks the definition; the cadence, guards and logs are the old ones (golden traces captured on the unmigrated orchestrator, replayed by the pinned snapshot and the live code).

| Entry | Policy | Held state | Rule |
|---|---|---|---|
| `compiled_rule` | `Cooldown()`, the floor is the rule's own (`refire_sec` ... `_rule_cooldown`) | `rs.rule_next[rule key]`, `rs.rules_fired` | Due when `now >= rule_next` (the old `<` comparison inverted). Once per run unless `repeat`; RETIRED beats `repeat`; the stamp follows every CONTACT, success or not; an action that made no contact stamps too (old behaviour); ONE contact per tick: a rule deferred by it is neither stamped nor marked fired, so the next pass judges it again. |
| `demon_mode_always` | `Cooldown(15)` (`DM_ALWAYS_RETRY_SEC` is its floor) | `rs.dm_always_try_at`, `rs.dm_always_count` | Stamped before the sprint check and before the tap, as before, so an unconfirmed attempt and a sprint-blocked pass both wait the full 15 s; a no-wave or disabled pass stamps nothing. |
| `uw_toggle` | `Backoff(10, 2, 120)` | ONE `rs.uw_next_try` and `rs.uw_fails` for the three call sites of `main()` | 10, 20, 40, 80, 120, 120 s after the 1st..nth consecutive failure; a success resets the count and leaves the deadline; `uw_ok` is read once per pass, so a second toggle of the same pass still runs after a failure. The host passes its held deadline as `refusal_at` with `failures` 0 (no circuit); `Backoff.retry_at` is the one place the ladder is computed. |

History: one terminal row per CONTACT, written by the host AFTER the input returned and after its own state was updated (`orchestrator.record_upkeep`, outcome `upkeep_outcome`): confirmed is
`succeeded` with the definition's evidence, anything else `failed` with the effect unknown. A suppression (cooldown, one contact per tick), a retired or rejected rule, a pass without contact,
a disabled or no-wave Demon Mode pass and an unconfirmed Demon Mode attempt (it does not say whether it tapped) record nothing. UW toggle rows are buffered during the pass and written once at the
end of it, so no deadline or decision waits on the ledger. Nothing is written before a tap. A host with no ledger operation records nothing; a failing ledger is counted in the session and never raised.
What a row does and does not say: it is written by `ActionSession.record` after the fact, so `started_at` and `finished_at` are the time of the WRITE (for a UW toggle, the end of the pass) and
`duration_ms` is null with `duration_basis` `none`; no execution of these actions is claimed as measured, and a test pins that. The Demon Mode history lists CONFIRMED fires only: `fire_button` returns False
both for a refused tap and for a tap it could not confirm, so an unconfirmed attempt that did tap is absent, and this history is not an attempt log. Recording a start, a duration or that attempt needs
a session interface (r18h) and a three-way `fire_button` result (r18f); neither is changed here.
Nothing here moves a fast-loop timer (`rs.last_fire`, `rs.fleet_try_at`, the Second Wind deadlines stay: r18f) or the death cycle (r18g).

## Adding an action (what the contract asks, and nothing else)

1. **A definition** in the catalog (`action_contract.py`): id, label, family, the driver kinds that may run it, a schedule policy, its
   `Field`s (`live`, `saved`, bounds, unit), its `Capabilities` (`live_controls`, `pause`, `run_now`, `variants`), the name of its saved
   adapter (or none), the blueprint kinds that carry its saved settings (`saved_kinds`, none = every kind), any whole-object control it owns
   (`documents`, with a validator registered through `control_settings.register_document_validator`). A definition with `restricted` set,
   or whose `run_now` is not `True`, is never offered a Run now whatever its binding says.
2. **A binding** for each configured instance, registered by the driver (`rs.action_bindings = {routine key: binding}`; the routine key is
   the action id, or `<action>@<instance>` for one of several instances, the instance being lower-case letters, digits, `_` and `-`): how a
   value is installed (`configure`, `configure_document`), whether a request is accepted (`request_now`) and how far it has come
   (`progress()`, which acknowledges the command from THIS binding's own evidence: `pending`, `started`, `terminal`), the routine status
   (`routine`), and `busy()` while it owns the foreground. A binding that reports no `progress()` has no Run now.
3. **A recorder** call pair in the handler (`session.recorder(action, instance).opened(...)` and `.closed(...)`, or `record` / `check`): the
   execution row, its timing and its evidence. The row is written after the input, never before.
4. **A saved adapter** when the action saves numbers (`SavedAdapter`: `keys`, `read`, `apply`, `prepare`, `write`, `finish`, `extras`),
   registered by name in `player/saved_controls.py`. Its `enabled` flag needs none: blueprint `automation` is validated against the catalog.
   The profile schema is closed on purpose, so a saved number also needs its row there (a key in the policy that holds it).

`test_action_synthetic_extension.py` does exactly this for a test-local `synthetic_probe` (two instances) in a COPY of the catalog and
drives it through the real routes.

## Tests

r18e: `test_action_parity_rules.py`, `test_action_parity_demon_mode.py` and `test_action_parity_uw_retry.py` replay `backend/tests/fixtures/action_r18e/*_golden_v1.json` (captured on the pinned unmigrated orchestrator; `TP_REGENERATE_ACTION_R18E_GOLDEN=1`, unmigrated code only).

Golden traces captured before any edit pin the cadence of every pilot caller (`backend/tests/fixtures/action_golden_v1.json`,
explicit regeneration only: `TP_REGENERATE_ACTION_GOLDEN=1 python -m pytest backend/tests/unit/test_action_golden_capture.py`).
Golden traces of the control dispatch, the Run now acknowledgements, the published routines and the saved-setting transforms were captured
the same way before the dispatch was generated (`backend/tests/fixtures/action_dispatch_golden_v1.json`, `TP_REGENERATE_ACTION_DISPATCH_GOLDEN=1
python -m pytest backend/tests/unit/test_action_dispatch_capture.py`); `test_action_dispatch_parity.py` replays them.
Parity, interruption, ownership, ledger, session, route and synthetic-extension tests are the `backend/tests/unit/test_action_*.py` files;
`test_action_declared_proofs.py` holds the proofs the declaration of item 6 still needed and the map from the declaration to the tests.