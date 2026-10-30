# Learning and preparation flow contract
> **Status:** Implemented; live emulator acceptance not exercised
> **Type:** Domain rules and verification record
> **Updated:** 2026-10-24

## Ownership

The browser projects preparation state; the operation ledger owns accepted
commands, immutable targets, lifecycle revisions and outcomes. Source is the
authority for the contracts described here.

| Source | Responsibility |
| --- | --- |
| [Recognition rules](../frontend/app/src/core/recognition.ts) ([rules document](../frontend/app/src/core/recognition.rules.md)) | Scan choices, launch and stop guards, operation evidence and the run query |
| [Recognition writes](../frontend/app/src/api/intents/recognition.ts) | Launch, observe, apply and Stop: one record per click, body and idempotency key frozen at the click |
| [Followed operation](../frontend/app/src/store/selectors/recognitionOperation.ts) | The operation the page follows in this context, kept across a reload |
| [Recognition page](../frontend/app/src/features/recognition/RecognitionPage.tsx) | The page the person uses: preparation, consent and status |
| [Operation adapter](../backend/control/operations.py) | Target validation, acceptance, worker resolution and revision checks |
| [Operation ledger](../backend/towerpilot_workflows/operations.py) | Transactional lifecycle and idempotency records |

The browser side is the app's Recognition page; the previous dashboard's
learning modules were retired by plan R17. The rest of this section describes
the contract as it was written for those modules.

Views consume `S.learningFlow`. Browser snapshots include the flow, phase,
context, selected run, operation ID and revision, reason, observation freshness,
last confirmed check time, busy state and the most recent 40 transitions. Browser
storage retains operation references and the last observed outcome; it does not
establish worker success. History remains local to the page.

---

## Flows

Screen learning follows:

`confirming -> checking -> starting -> waiting_for_worker -> running -> applying -> checking_readiness -> ready | needs_attention`

Run preparation checks the existing operation and the selected run's backend
requirements. A completed operation awaiting application is applied by ID and
revision. Saved observations without an operation require the explicit
`saved_observations` request. Preparation branches are:

| Branch | Behavior |
| --- | --- |
| `ready` | No capture worker starts |
| `editing_equipment` | Open the selected run's equipment editor |
| `capture_required` | Show the requested manual capture |
| `needs_attention` | Readiness was not explicitly confirmed |
| `confirming -> starting -> waiting_for_worker -> running -> applying -> checking_readiness` | Consent permits battle-only preparation |

**Use discovered presets in my profile** checks the original operation before
applying. Reviewing legacy observations starts no worker and removes the legacy
`finishFullSetup` marker only after successful application and readiness checking.
Unscoped or timestamp-only browser records cannot authorize automatic application.

The [Recognition rules](../frontend/app/src/core/recognition.ts) are executable authority.
Completion may precede the first observed running state. Restoration, waiting to
apply, interruption, cancellation, failure and supersession remain distinct from
readiness. Screens keeps guided preparation visible and places targeted scans,
manual acquisition and developer tools in advanced disclosures. The seven
existing top-level sections remain.

---

## Operation and recovery contract

| Request | Identity and validation |
| --- | --- |
| `POST /api/calibrate/start` or `/observe` | Send `idempotency_key`, selected run and `expected_context`; acceptance returns `operation.id` and `operation.revision` |
| `GET /api/calibrate/status?operation_id=...` | Observe the original operation target, including after reload |
| `POST /api/calibrate/apply` | Send `operation_id` and `expected_revision`; application validates the original target and revisions |
| `POST /api/calibrate/stop` | Send the observed operation ID and revision; a delayed request cannot select a newer operation |
| Tracked runner Stop | `POST /api/operations/<id>/cancel` with the rendered revision; PID cancellation is reserved for untracked legacy workers |
| Finish current run / clear stop flag | Send the original tracked operation ID and revision to `/api/control`; the server resolves its original instance |
| Legacy observation review | Send `saved_observations:true` explicitly; tracked observations require their operation ID |

The immutable target includes instance, account, transport serial, rendering,
profile, selected run, configuration revision and profile revision. Browser
acceptance sends its expected instance/account/serial/rendering/profile so a
selection change before acceptance is refused.

Lifecycle statuses are `accepted`, `running`, `cancelling`,
`restoration_required`, `awaiting_apply`, `applying`, `succeeded`, `failed`,
`interrupted` and `cancelled`. Only `awaiting_apply` authorizes automatic
application. A confirmed `succeeded` operation resumes by checking readiness
without repeating application, including a running or waiting browser that observes
application completed by another tab. A busy apply retains its reference until a later
observation; an apply failure preserves discoveries for recovery.

Revisions order observations independently of HTTP response order. Request
generations and context checks reject stale page reads, while conditional browser
storage replacement preserves another tab's newer request. A definitive launch
refusal restores the previous reference; an uncertain network result retains the
idempotency reference. The no-ID status read can recover acceptance when its
idempotency key matches, then subsequent reads use that ID.

Status failure preserves the last confirmed phase and marks observation freshness
unavailable. It does not infer idle or success from a missing process. Readiness
must be explicitly `true`, fresh and associated with the current selection before
Start is enabled. Start checks requirements again before dispatch, including the
day plan through `/api/runs`. Stop controls remain available when status is
unconfirmed; the server still validates their operation revision.

The diamond collector uses the same expected-target fields and shares Start's
single-flight guard. An uncertain retry for the same context and run preserves its
idempotency key. Screen analysis stores its own context-scoped operation reference,
resumes status by ID, and requests the completed frame by ID and revision. Context
changes and reversed responses cannot publish a different analysis target.

Browser guards cannot retract HTTP already dispatched. Server revision checks,
transactional acceptance and device leases remain responsible for side effects;
this contract makes no exactly-once claim about game input.

---

## Game requirements

| Step | Game requirement |
| --- | --- |
| Read saved data, apply discoveries, check readiness, edit equipment in the dashboard | No game input for that data step; preserve another active operation |
| Consent to learning or battle preparation | Game open at Home without a live battle or tournament |
| Starting, waiting, learning, preparation, restoration | Keep the game running and leave the emulator untouched |
| Manual image capture | Open the requested game screen |

---

## Verification

Current checks: from `frontend/app`, `npm test` (Recognition unit and component tests
under `tests/unit/core/` and `tests/unit/features/recognition/`) and `npm run e2e`
(`tests/e2e/recognition*.spec.ts`, fixture backend). History (2026-10-19, the retired
`frontend/tests` node suite): that session's offline run passed 195 tests. Coverage includes reversed observations,
request ordering, profile/account switches, reload with the same operation,
revision-bound apply and Stop, duplicate completion, restoration, busy apply,
application failure, lost acceptance responses and another tab replacing a
reference. These are fake-I/O contract tests, not live emulator evidence.

The offline harness (`frontend/tests/fixtures/learning-harness.html`, retired with that suite) loaded the
production learning modules with simulated ID/revision adapters. Its source was
updated for this contract; browser execution was not exercised in this session.
No live dashboard, emulator or game action was started, stopped or reloaded for
this frontend implementation. Live device acceptance remains unverified.
