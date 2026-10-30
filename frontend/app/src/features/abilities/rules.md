# Abilities editor rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-22
> **Updated:** 2026-10-24

## Ownership

The backend profile validator owns policy validity, ownership at binding, arming and cooldown semantics. The browser edits saved configuration only. Vocabulary comes from the backend; unsupported rules remain exact and read-only until explicit deletion.

The shared [settings owner](../../core/settingsFields.ts) and [profile-patch owner](../../core/profilePatch.ts) classify saves and read settlement. The [Abilities store](../../store/abilities.ts) owns target fencing, raw drafts and the explicit save lifecycle. The [draft module](../../store/abilitiesDraft.ts) preserves numeric text and stable rule identities. Components display those results and edit draft structure; they never derive fleet marks or next-fire times.

---

## Save contract

Each emulator, account and profile has its own persisted drafts and frozen submission. One request at a time serializes the profile and settings revision domains. Every save is an explicit click through the existing non-resendable write journal. An uncertain answer blocks further writes until a later read settles it. Reload restores uncertainty without sending a request.

An unchanged read leaves an uncertain save Unknown. It cannot release the frozen submission through revision adoption: the original request could still commit. The editor says it is sending only after the write journal accepts a request. A blocked send keeps the draft and says that nothing was sent.

Reads check configuration before and after the profile, settings and vocabulary reads. Late answers from older epochs or superseded requests cannot overwrite the current target. An older read cannot replace a newer write answer, and an older write answer cannot replace a newer revision already read. A changed revision requires an explicit review and adoption; raw drafts stay intact.

Routine reads preserve unchanged raw numeric text and hidden mode/rule branches. Cancel explicitly discards that history. A confirmed deletion keeps later typing under the deleted plan's original target as a draft for explicit recreation; reload never recreates it. Backend problem paths feed the focused error summary and field associations. Each problem remains until its submitted value changes; the browser does not reproduce the validator's numerical rules.

Refresh reconciles unchanged fields individually. An exact saved rule keeps its identity and hidden history when the server reorders it. A changed rule cannot be associated safely without a backend identifier; the editor retains the draft and exposes a conflict. Unfinished new-plan identifiers use the shared draft persistence under their original target and policy family, with an explicit discard control.

UW and rescue policies use the shared Profiles editing target and `editTargetScope(runtime, target)` for source reads, revisions, drafts, identifiers and submissions. A pinned target needs no active profile or active settings snapshot. Its source owns validator checks, recorded ownership and consumers. Inactive writes use the shared click-time target check, then recheck the runtime epoch and target generation before sending the target name, source revision and runtime `expected_context`. A failed check keeps the draft and shows why nothing was sent. Active writes retain their existing bodies; adding their server context fence belongs to F6-14b.

The global Demon Mode setting remains active-only. Inactive editing shows the active profile's name and a disabled action with the return-to-active reason. Active settings values never appear under the inactive editor heading. No route on this page launches a worker, controls input, fires an ability, captures a screen or ends a battle.

---

## Optional ranges and empty rules

The backend's `_validate_uw_policy` permits an absent/null Chain Lightning `always_on_above` threshold. Its fleet offsets remain required in `fleet_marks` mode, and `on_above` remains required in `off_until_wave` mode. The threshold's explicit-bound switch saves null when cleared and retains the prior raw bounds for restoration. An incomplete explicit pair stays raw and unsavable; numerical ordering and integer constraints remain backend validation.

An absent/null rescue `rules` member is a valid empty list for explicit first-rule addition. Reading it does not insert a list into the saved shape. A malformed non-list member remains exact and inspection-only.

---

## Verification

Wanted rows A1, A2, A3, A4, A5 and A7 are exercised in [component and API tests](../../../tests/unit/features/abilities/abilitiesPage.test.tsx) and [browser tests](../../../tests/e2e/abilities.spec.ts). Supplemental answers are produced by [isolated real Flask routes](../../../../../backend/tests/recording/abilities_fixture_cases.py); retired rules use local draft tests because the backend cannot produce A-01 retired-rule reads.
