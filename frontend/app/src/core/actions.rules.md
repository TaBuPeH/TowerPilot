# Timed action rules
> **Status:** Active
> **Created:** 2026-10-23
> **Updated:** 2026-10-24 (AC5 reading; AC4 vocabulary consolidation)

`actions.ts` owns what the app knows about a timed action (brief
`backend/logs/reviews/ui-pages/a2-7-actions-brief.md`, refactor R1). Consumers call it; no other
module names a routine in a list, a field order or a name lookup.

- **Projection views.** `actionsView(battle)` reads `battle.runtime.actions` into plain values:
  the envelope (`schema_version`, `observed_at`, `control_revision`, `context`) and each item's
  `id`, `instance`, `label` and descriptor fields. It returns null when the battle publishes no
  `actions` key. Server text (labels) stays raw; only the selectors turn it into `ServerText`.
- **Routine key.** An item's controls live at `routines.<key>.<field>` and its Run now token is
  `<key>[:<variant>]`. The key is the published `key`; without one it is `id` only when the
  item is its action's single instance (`instance === id`, gap G2), otherwise null and the item's
  controls cannot be addressed.
- **Compatibility list (S5).** `COMPAT_ROUTINES` (Ad gems, Floating gems, Shopping, in the
  Dashboard's row order) and `COMPAT_FIELDS` (the editor's field order) are the one copy of the
  pre-A2 routine and field lists. A driver that never built the projection runs pre-A2 code,
  which can publish only these. Root decision Q1 keeps them until every driver publishes
  actions (end of roadmap item 7); AC12 deletes them. `compatClaimLine` keeps the per-routine
  confirmed-result line of those rows until C1 reads `item.last`.
- **Shopping predicates (S2).** `isShoppingAction` (the shopping action or a variant) and
  `isShoppingVariant` (`shopping:<section>[:<upgrade>]`) are the one test for the shopping
  queue's own rules (queue gate, Try once, command states). An action named `shopping<x>`
  without the colon is not shopping.
- **Field order.** A live editor row lists a routine's fields in its item's live descriptor
  order, else in `COMPAT_FIELDS` order, then every other reported field in its reported order
  (`liveFieldOrder`, `orderedFields`); the saved editor uses `COMPAT_FIELDS` until the saved read
  carries descriptors (B12).
- **Live bounds (R2).** `liveConstraints` gives a battle's live number fields the bounds of
  their addressable item's descriptor (`min`, `max`; null is no bound); every other path keeps
  the saved run's `constraints` as read, and a battle without a projection uses only those. On
  every recorded pair in the landed checkout the two sources are equal, so the switch
  changed no recorded output (`tests/unit/core/actions.test.ts`). With a projection the live editor's
  bounds are known before the saved run is read (`hasLiveBounds`), so `boundsKnown` and its
  notice follow both sources.
- **Name and label lookups.** `actionName` and `fieldLabel` take the caller's code-keyed copy
  table and its fallback entry (`copy.actions`); a code read only as an OWN key of the table
  (`core/read.ts` `ownOr`), so an inherited name such as `constructor` is never a name.
- **Reading (AC5, brief section 6 rules 5 and 6).** `actionsReading(read, elapsedSec)` takes the
  answered body of GET /api/battles/active (`{server_time, battle}`) and the monotonic seconds since
  it arrived. In order: the battle report must be fresh by EV-9 (`core/battle` `battleReportAge`,
  the one 0..`BATTLE_REPORT_MAX_AGE_SEC` rule, shared with `freshBattleReport`), else `unknown`
  with its reason (`battle_report_old`, `battle_time_invalid`); no `runtime.actions` is `absent`
  (S5 rows); a `schema_version` other than 1 is `read_only` `action_schema_newer`; an envelope
  `context` whose `device_key`, `account`, `instance_name` or `profile` differs from the battle
  context's `device_key`, `account`, `instance` or `profile` (or is missing) is `read_only`
  `action_context_mismatch`; an `observed_at` that is not finite or is later than
  `runtime.server_time` is `unknown` `action_report_time_invalid`; `observed_at ===
  runtime.server_time` is `current` (a publish that builds the projection writes the same epoch to
  both); an earlier one is `old` with `ageSec = server_time - observed_at + elapsed`. The 15 s
  rule alone calls the recorded stale case fresh (the projection is 2 s behind), so the equality
  is required. No number of its own. `actionControlsBlocked(reading)` is the reason code that
  disables Run now and live edits (root decision Q2): `action_report_old` for `old`, the reason
  for `unknown` and `read_only`, null for `current` and `absent` (their own gates apply).
  Tests: `tests/unit/core/actionsReading.test.ts` over every r17b projection case.
- **Item rows (AC1, brief section 6 rules 1, 2 and 6).** This battle renders each item from its
  own fields, in server order, with no branch per action.
  - `actionNext(item, now)` decides the Next cell, in this order: `disabled` is Off; `running` is
    open with the seconds to `active.window_ends_at`; then a blocker (`eligibility.blocker`) comes
    before the state; `due` is Due; `waiting` counts down to `next.due_at` (Next not reported
    when null); `yielded`, `blocked`, `unknown` read as their word. `now` is
    `runtime.server_time` plus the time since the read arrived (F-050/F-055). Only a `current`
    reading shows this cell; `old` shows last known with its age, any other reading not current.
  - `actionOutcome(last)` reads `last.result` and `last.effect` against `ACTION_RESULTS` and
    `ACTION_EFFECTS`. Only the effect `confirmed` reads as confirmed (rule 2); no count is read
    from an action row. An `unconfirmed` effect with the reason `offer_disappeared` (a battle ad whose
    offer was gone after the tap, no balance read) reads `Offer gone after tap` (`effectWord`), and so does
    the compatibility line of the same claim (`compatClaimLine`). The server decides the effect; these only word it.
  - Codes (`ActionCode`): `known`, `unreported` (absent or null: nothing was sent) or
    `unrecognised` (the value as sent). Each cell shows its own unrecognised code under Why: the
    state in Next, a field type in its Settings line (`fieldType`), the result and effect in
    Last result. `unrecognisedCode(item)` (an absent state counts, an absent result or effect
    does not) disables that item's controls (`action_code_unrecognised`).
  - `settingFields(item)` lists the descriptor fields except the switch `enabled`, whose value
    is the item's own `enabled`; `fieldValue` reads a field's value in force from `values.<path>`
    (B2), else `battle.effective.routines.<key>.<path>` (gap G1).
- **Run now (AC3, rule 3).** `itemRunNowRefusal(item)` is the item's own refusal: no routine key
  (`control_path_unpublished`), an unrecognised code (`action_code_unrecognised`), or a
  descriptor whose `capabilities.run_now` is not `true` (a reason string is shown as sent,
  `server_reason`; anything else `run_now_unsupported`). `runNowReason` is the one order of a
  This battle Run now or Try once reason: the battle-wide `actionGate` reasons
  (`report_unavailable`, `battle_not_active`, `no_tracked_battle`), then `actionControlsBlocked`
  of the reading (the S5 rows pass no reading), then the item's refusal, then any other gate
  reason, then `sending`. Admission of the token stays `actionGate` (`battle.capabilities.actions`)
  and settlement stays `core/routines`. A Run now command's outcome is the item whose
  `last.command_id` names it, once the command is terminal (`commandViewTerminal`).
- **History vocabulary (AC4).** [historyActions.ts](historyActions.ts) imports `ACTION_RESULTS`
  and `ACTION_EFFECTS` from this owner. Its execution states add `running` to the finished
  results; its existing result reader also accepts that word. Live `ACTION_STATES` remain a
  distinct vocabulary. History's exported state and effect types derive from these lists.
- **Enforcement.** `tests/unit/core/actionsEnforcement.test.ts` fails when a quoted `ad_gems`,
  `floating_gems` or `shopping` appears in `src` outside this module and the S1 to S4 allowlist,
  or when an allowed file's count changes. `tests/unit/store/actionsDump.test.tsx` holds the deliberate
  current-behavior regression dump over every recorded battle and saved read.
