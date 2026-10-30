# Run control rules
> **Status:** Active
> **Created:** 2026-10-23
> **Updated:** 2026-10-24

`runControls.ts` owns saved and live routine draft checks, write eligibility and override
discovery. `routines.ts` owns live command settlement; `commands.ts` owns payload path
discovery, including a whole shopping plan as one leaf.

- RL2 offers a reset of every applied override as one request with each dotted path set to
  `null`. Include `shopping_plan` once alongside routine leaves. Use the battle, operation,
  generation and control revision that the confirmation names. A changed target invalidates
  that confirmation. Cancel sends nothing. Refusal preserves the draft and complete list.
- An accepted reset waits for a later read of the same battle, operation and generation.
  The read must list its command applied, report every reset path applied, and show each
  removed leaf absent or null under readable parent maps in both `overrides` and
  `applied_overrides`. Missing or malformed maps prove nothing. Acceptance alone
  never proves application. Retained submitted reset paths remain authoritative when either
  response omits its payload, including lost receipts and restored intents after reload.
  Identical replay does not bypass this rule.
- SQ5 context-scoped routine and shopping editors use `store/intents.ts`'s
  `hasConfirmedContext` before accepting input. A stored session context is provisional:
  config can change the context key and hide edits made under it. Saved, live and shared
  policy editors show catalogue waiting text until config confirms the context. Live Save and
  every live reset stay closed with `context_unknown` until then: a battle read can answer
  before config, and the confirming read starts a new epoch that drops it, which would close an
  open reset confirmation by itself. Saved run
  editors also wait for the initial successful run catalogue read to resolve their identity: `farm` and
  `bp_farm` resolve to the same controls read but name different persisted draft scopes.
  `selectRunsPage().saved.scopeReady` owns this saved-editor gate. A failed initial
  catalogue read keeps inputs closed, shows its failure, and offers `checkRuns` to retry.
- SQ5 numeric range rules belong to the backend. Queue drafts retain raw text and send typed
  numbers without rounding or clamping. Refused writes show exact server text beside that
  queue's Save action, preserve drafts, and never claim Saved or Applied. The queue selector
  scopes this feedback to the action's draft scope. Saved and live readback rules stay shared
  with the routine editor.

- A2-7 AC2 (brief `backend/logs/reviews/ui-pages/a2-7-actions-brief.md`): `liveControls`
  builds the live editor's fields. A path is a field only when `capabilities.controls` lists
  it. When an addressable item of `runtime.actions` describes its routine (core/actions
  `itemOfKey`), the path is a field only if that item's descriptor lists it as `live`; its kind
  is the descriptor `type` (`bool` a switch, `number` a number). Any other type is listed in
  `unrecognised`, shown with its code and never editable (a draft for it is `not_editable`).
  Bounds come from core/actions `liveConstraints`; values stay those of the battle's
  `effective` (gap G1), so drafts, Save, Discard and settlement are unchanged. A unit `s` is
  seconds (the catalogue's unit and duration note); another unit is shown as sent with no
  duration; no unit shows none. Routines no item describes (the S4 gates) and a battle without
  a projection keep `controlGroups` as before. An item without a key is `unaddressable`
  (`control_path_unpublished`), read-only.
- A2-7 AC2: a reset sends `null`, so `liveFieldNullable` offers it only where the describing
  field is `nullable` (undescribed fields as before); a single override of a field that is not
  nullable keeps its row with its reset closed (`not_nullable`), and the reset of all leaves it
  out.
- A2-7 AC2 (root decision Q2): `liveSaveGate` and `liveResetGate` take the reason code of
  core/actions `actionControlsBlocked`; after the battle-report check it closes live Save and
  every reset with that code. The shopping plan editor passes the same block
  (`selectShoppingLive`).

`recordedRunFamilies.test.ts`, the Runs component tests and the Runs browser suites cover the
`run_controls_live_reset_all_mixed` and `shopping_queue_bounds` recordings;
`tests/unit/core/actionsLiveFields.test.ts` and `tests/unit/features/runs/actionsLiveEditor.test.tsx` cover
AC2 over the landed r17b projection.
