# Run workspace independent review
> **Status:** Source and focused integration findings resolved; supported-driver limitation recorded
> **Type:** Review
> **Created:** 2026-10-19
> **Updated:** 2026-10-19
> **Scope:** Saved and live run controls, battle ledger, shopping queue, history
> **Verification:** Source review; no emulator input or process changes

## Findings checklist

No HIGH finding identified in the inspected workspace changes. The three MEDIUM findings below have source corrections and regression tests inspected. Test execution remains part of the parent's implementation verification.

- [x] **MEDIUM — Weighted shopping could permanently stop after unknown confirmation.** [shopper.py:483](../backend/interactions/shopper.py#L483). A pending quote could remain indefinitely although confirmation rejected evidence older than 15 seconds. Re-read after implementation-agent correction: `_reconcile_confirmation` now records `terminal_unconfirmed`, clears pending evidence after 15 seconds, and ends that pass without advancing confirmed quota. Concrete correction present:

  ```python
  if time.monotonic() - pending["before"]["observed_at"] > 15:
      self._pending_confirmation = None
      # Record terminal_unconfirmed; end this pass without another purchase.
      return True
  ```

- [x] **MEDIUM — Queue-only edits bypassed the frozen shopping master gate.** [battle_session.py:137](../backend/runtime/battle_session.py#L137). The previous shopping-plan branch consulted only explicit routine overrides before assigning `rs.shopping_enabled = plan['enabled']`. With frozen `routines.shopping.enabled=False`, replacing an enabled plan could turn scheduled shopping on while the applied routine control remained false. Re-read the correction: the requested routine gate now owns both installed runtime and effective plan enablement. Concrete correction present:

  ```python
  enabled_gate = self.battle['requested_controls']['routines']['shopping']['enabled']
  plan['enabled'] = enabled_gate
  rs.shopping_enabled = enabled_gate
  ```

  Inspected `test_queue_edit_cannot_enable_frozen_disabled_shopping`: queue-only revision is acknowledged while runtime, effective plan and applied routine gate stay disabled.

- [x] **MEDIUM — Accepted child configuration could be overwritten by a different snapshot's defaults.** [settings.py:113](../backend/settings.py#L113), [settings.py:84](../backend/settings.py#L84), [settings.py:91](../backend/settings.py#L91). The accepted-source branch installed its snapshot, then `bind_device` restored account and override blocks from `_ACCOUNT_DEFAULTS` / `_DEFAULTS`. [restore_worker_context](../backend/runtime/operations.py#L18) resets those caches from the operation context before direct worker entry, protecting normal direct launches from import-time divergence; a delegated child's later battle envelope can differ from that parent context. Re-read the correction: accepted selection now pins both cache baselines to the exact frozen snapshot before binding. The added `test_accepted_device_binding_uses_frozen_defaults_not_import_time_values` exercises the real bind path with conflicting caches. Concrete correction present:

  ```python
  CONFIG.clear()
  CONFIG.update(frozen)
  _DEFAULTS = {key: copy.deepcopy(frozen[key]) for key in _OVERRIDABLE if key in frozen}
  _ACCOUNT_DEFAULTS = {key: copy.deepcopy(frozen.get(key)) for key in
                       ('loadouts', 'active_profile', 'tourney_card_tweaks')}
  bind_device(name)
  ```

  Regression: provide accepted `rois`/`loop` and distinct import-cache values, call the real accepted selection path, and assert the runtime configuration retains accepted values.

---

## Supported-driver limitation

`combo._spawn` now creates prepared battle records only for the generic `flows.ENGINE` driver. Unsupported shard/quest children have inherited battle identifiers removed and emit `combo_driver_capability` with history and live controls marked unsupported. Inspected `test_unsupported_shard_driver_never_creates_batch_as_battle`, which verifies no ledger row is created for the shard batch. This avoids representing a batch as one battle; it does not deliver per-battle shard/quest history or controls. The parent must disclose that supported-driver limitation when reporting completion.

---

## Source inspection coverage

Ratings concern the inspected workspace additions and their integration paths. Existing large modules were inspected through their modified sections and relevant callers; this is not a whole-repository audit. PASS means no additional actionable source finding in those inspected sections, and does not assert live verification.

| Source | Rating | Evidence / scope |
|---|---|---|
| `backend/towerpilot_workflows/battles.py` | PASS | Transactional identity, capability admission, revision checks, acknowledgement, expiry, history |
| `backend/towerpilot_workflows/control_settings.py` | PASS | Strict scalar validation, sparse overrides, immutable merge |
| `backend/towerpilot_workflows/shopping_plan.py` | PASS | Queue normalization, reserve and quantity admission, confirmed weighted progress |
| `backend/player/run_controls.py` | PASS | Saved control resolution and immutable materialization |
| `backend/player/shopping_plan.py` | PASS | Shared pure-contract adapter |
| `backend/player/playerprofile.py` | PASS | Blueprint gates, queue compile and dissonance filtering changes |
| `backend/control/battles.py` | PASS | Command admission, operation fencing, public snapshots; parent corrected final-wave mapping |
| `backend/control/run_workspace.py` | PASS | Saved revision caller boundary, run-owned policy cloning, conversion review |
| `backend/control/operations.py` | PASS | Launch snapshot handoff and public-request filtering |
| `backend/runtime/battle_session.py` | PASS after correction | Queue replacement retains frozen/requested shopping master gate |
| `backend/runtime/battle_source.py` | PASS | Accepted envelope checks and validated boundary source reload |
| `backend/runtime/operation_worker.py` | PASS | Frozen-operation profile-revision bypass and worker bootstrap |
| `backend/runtime/logger.py` | PASS | Battle identity context attached to driver events |
| `backend/runtime/runlog.py` | PASS | Capture provenance distinguishes fresh verified and unverified copied results |
| `backend/orchestrator.py` | PASS | Boundary classification before checkpoints, independent gates, shopping admission and repeat reload additions |
| `backend/gem_collector.py` | PASS | Adopted collector-only capability surface and conservative boundary records |
| `backend/settings.py` | PASS after correction | Accepted snapshot now owns binding cache baselines |
| `backend/interactions/shopper.py` | PASS after correction | Same-frame cash/quote and strict quantity gate; bounded terminal confirmation correction inspected |
| `backend/interactions/ad_gems.py` | PASS | Scheduled/manual windows, deadline and foreground checks |
| `backend/flows/shard.py` | PASS within inspected collector changes | Collector windows and surrender callback; unsupported lifecycle limitation above |
| `backend/scheduling/combo.py` | PASS within delegated snapshot additions | Child envelope and generation fencing |
| `backend/vision/textocr.py` | PASS | Explicit OCR timeout forwarding |
| `frontend/dashboard.py` | PASS | Saved context/revision transaction, active/history/detail endpoints, accepted envelope construction |
| `frontend/webui/run-workspace.js` | PASS | Draft/pending/applied separation, identity refresh, queue UI and command busy state |
| `frontend/webui/run-workspace.css` | PASS | Compact cards, page groups, global queue responsive layout |
| `frontend/webui/index.html` | PASS | Shared workspace mounted in overview, runs and history |

---

## Applicable conventions and test review

Read the project rules and trigger map, mandatory `verify-never-infer`, and requested `skill-compliance`, `python-code-quality`, `correct-fix-over-easy-fix`, `prefer-uniformity`, `source-code-first`, `pytest-practices`, and `markdown-format` guidance. Source-first investigation and concrete file-linked findings were used. No production edits were made by this reviewer.

Inspected `test_battles.py`, `test_battle_session.py`, `test_battle_source.py`, `test_battle_handoff.py`, `test_battle_api.py`, `test_battle_runlog.py`, `test_battle_capture.py`, `test_run_workspace.py`, `test_run_workspace_context.py`, `test_shopping_plan.py`, and `frontend/tests/run-workspace.test.cjs`. Tests use temporary persistence, scoped monkeypatches and behavior assertions. Corrections include regression coverage described above. This reviewer did not independently run tests or exercise live game behavior; test results belong to the parent implementation verification.

The potential history timestamp mismatch was withdrawn after reading `api_battle_detail`, which explicitly adapts ledger `timestamp` to frontend `t`. No unrelated pre-existing issue is counted in these findings.

---

## Focused final integration review

An independent focused source pass re-read `backend/runtime/battle_source.py`, `backend/runtime/battle_session.py`, `backend/settings.py`, the workspace control adapters, the dashboard workspace/launch routes, and `frontend/webui/run-workspace.js`. Relevant shard/quest and scheduler consumers were traced. The earlier queue master-gate, accepted-cache-baseline and weighted-confirmation-expiry corrections remain present in source. No emulator input, process restart or production source edit was performed by this reviewer.

- [x] **MEDIUM — Editing again before driver acknowledgement leaves an unsaved draft behind a persistent conflict.** [run-workspace.js:334](../frontend/webui/run-workspace.js#L334), [run-workspace.js:354](../frontend/webui/run-workspace.js#L354), [run-workspace.js:409](../frontend/webui/run-workspace.js#L409), [battles.py:215](../backend/towerpilot_workflows/battles.py#L215). `flush` checks `sending` but permits another live write while the previous control revision is pending. The ledger correctly refuses it with `Previous control revision is still pending`; the catch restores the draft and sets `model.error`. A later acknowledgement poll updates the snapshot but never drains that draft or clears the transient conflict. A standalone Node reproduction using the existing test host pattern sent interval 60, then interval 70 before acknowledgement: after the first revision became applied, write count remained 2, the displayed value remained 70, and the conflict remained visible although the installed value was 60. Concrete correction: defer the next live flush while a revision is unacknowledged and drain the retained draft after an acknowledgement poll, for example:

  ```javascript
  if (model.scope === 'live' && unacknowledged(model)) return;
  // After refreshing the live model in poll:
  if (!unacknowledged(live) && !live.error && Object.keys(live.draft).length)
    flush(liveHost, live);
  ```

  Correction verified: `flush` now defers while `unacknowledged(model)`; an acknowledgement transition in `poll` drains the retained draft. Current-binding checks fence old timers and responses after battle, empty-active-state or account changes. Independently ran `node --test frontend/tests/run-workspace.test.cjs`: 26 passed, 0 failed. New regressions cover accepted and in-flight edits, invalid input, obsolete bindings and the escaped capability notice. The new source and regression tests satisfy the applicable source-first, verification, explicit failure handling and scoped-test conventions inspected in this focused pass; no additional actionable violation was identified.

- [x] **MEDIUM — Saved shard/quest controls advertise gates those drivers do not consume.** [run_workspace.py:38](../backend/control/run_workspace.py#L38), [shard.py:1066](../backend/flows/shard.py#L1066), [shard.py:1078](../backend/flows/shard.py#L1078), [shard.py:1113](../backend/flows/shard.py#L1113). `_capabilities` excludes shopping for unsupported kinds but advertises rewards, abilities, rescue and repeat for every blueprint. `update` accepts these writes, and the saved response reports the selected values. The standalone shard driver does not read `automation`: `one_loop` still invokes `fire_nuke`, and its batch loop uses the configured loop count and runflag. The quest driver likewise has no automation-gate consumer. A temporary in-memory profile passed through `run_workspace.update` with shard abilities/repeat disabled returned both capabilities as supported and both settings as false. This makes the saved UI claim behavior beyond the already-documented missing live/history lifecycle. Concrete correction: derive saved controls from the actual driver, preserving collector settings for standalone shard/quest drivers and refusing their unimplemented gates, for example:

  ```python
  supported_routines = set(run_controls.FIELDS) if flows.script(bp['kind']) == flows.ENGINE else {'ad_gems', 'floating_gems'}
  # Build capabilities only from supported_routines; update already checks them.
  ```

  Correction verified: `_capabilities` now permits only `ad_gems` and `floating_gems` outside coin/tournament drivers; reads include the support limitation notice. `test_saved_capabilities_do_not_advertise_unsupported_runtime_gates` covers admission and rejection. Independently executed `.venv/Scripts/python.exe -m pytest backend/tests/test_run_workspace.py backend/tests/test_battle_source.py backend/tests/test_battle_handoff.py backend/tests/test_battle_api.py -q`; command completed successfully. UI `inputHTML` disables paths absent from capabilities.

The reproductions above ran against the source before their corrections without this reviewer adding tests or modifying runtime files. The first Python attempt used the system interpreter and failed to import PyYAML; rerunning with `.venv/Scripts/python.exe` produced the saved-capability result described above. Existing and new tests were read for coverage, and the focused commands recorded above were independently executed; the parent owns the full final suite run. No additional HIGH finding was identified in this bounded pass.
