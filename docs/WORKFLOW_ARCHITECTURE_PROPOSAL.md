# Reliable local workflows
> **Status:** Implemented; offline and farming verification recorded below
> **Type:** Architecture decision and delivery record
> **Updated:** 2026-10-24
> **Scope:** One local Windows application managing the user's emulator(s)

## Decision

Keep one launcher, local API and release, with isolated device workers. Establish explicit feature boundaries and durable operation identity before a broad repository move. Add installable packages where they enforce those boundaries; separate deployment is not a goal.

The user selected reliability for their local emulator(s) as the next iteration's priority. Three independent Sol reviews covered frontend/setup, backend lifecycle, and architecture. The existing [learning modules](LEARNING_FLOWS.md) are the first migration seam, not work to discard.

---

## Baseline evidence that determined the order

This table records the pre-implementation review. Current lifecycle behavior is
documented in [Learning and preparation flows](LEARNING_FLOWS.md).

| Current source | Finding | Consequence for the proposal |
|---|---|---|
| [Calibration start/status/apply](../backend/dashboard.py) | Start returns phases without an operation ID; status combines current-context files, process discovery and recent events; apply reads the active profile. | Make acceptance, progress and application refer to one immutable operation target. |
| [Learning controller](../frontend/webui/learning-controller.js) | Browser generations/context guards protect awaits, but worker matching uses timestamps. | Retain the controller, replace timestamp correlation with server-issued identity and revision. |
| [Overview controls](../frontend/webui/index.html) | Run eligibility uses `!r || r.ready`, allowing an absent readiness entry. | Unknown readiness must remain unknown and block Start; stopping the driver must remain available. |
| [Runner discovery](../backend/runtime/runners.py) | Ownership checks enumerate processes. | Process discovery remains useful diagnostics but does not provide atomic device reservation. |
| [Runtime settings](../backend/settings.py) | Import loads machine configuration; device modules deliberately assume one instance per process. | Keep device workers isolated and keep pure application contracts free of runtime settings imports. |
| [Atomic files](../backend/runtime/files.py) | Atomic replacement protects an individual report from partial reads. | Preserve artifact writers; introduce transactions for lifecycle acceptance and idempotency, not a second copy of every artifact. |

These were source findings. Concurrent-start races and miscorrelated outcomes were failure hypotheses to test, not reproduced live incidents from the council review.

---

## Implementation boundaries

The local application now has a pure, installable
[`towerpilot_workflows`](../backend/towerpilot_workflows/operations.py) package
for transactional operation identity. The
[`control.operations`](../backend/control/operations.py) adapter validates and
launches commands; the [worker bootstrap](../backend/runtime/operation_worker.py)
restores their accepted context. Existing domain artifacts remain in their
account-specific locations, with hashes recorded by the ledger.

[`runtime.device_lease`](../backend/runtime/device_lease.py) owns transport
exclusion across processes and checkouts, including scheduler child delegation.
Input commands retain that authority through the socket call. Process discovery
still provides diagnostics and conservative protection around legacy workers.

The core package alone is independently installable. The control adapter,
device workers and dashboard remain part of the existing local release. No
service split or repository-wide directory migration is required to enforce
the core import boundary. Tests prohibit runtime/settings/device imports in
the core; the package can be built with:

```powershell
.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir "$env:TEMP/towerpilot-workflow-wheels"
```

Contract tests use temporary ledgers and artifacts; device-ownership tests use
isolated OS locks and subprocesses. Live farming verification does not exercise
setup or destructive crash scenarios on the user's battle.

The installed wheel was exercised from a temporary directory outside the
checkout: import produced no settings/device imports or files, and repeated
acceptance returned the same operation. An existing farm was adopted by a
tracked worker; restarting the dashboard recovered that same operation and
worker without stopping the battle. Ad collection and orbit input were observed
in that worker's new log. This does not validate telemetry avoidance or the
entire setup journey on a live emulator.

Integration verification on 2026-10-19: the full backend suite passed 1,746
tests with three skips; the frontend suite passed 195 tests. Independent review
found and checked fixes for completion observed from another browser tab,
stale stop markers, stale gem frames, cross-operation scan/analysis artifacts,
analysis failure outcomes and auxiliary browser launch identity. Setup and
crash scenarios use temporary fixtures; the full live setup journey was not
repeated over the active farm. A live duplicate-start probe was blocked by
automatic approval review; concurrency exclusion was verified offline.

---

## Council comparison

| Option | Position | Main trade-off |
|---|---|---|
| Feature modules and operation contracts in the current deployment | First delivery | Lowest disruption; needs enforceable boundaries to avoid a permanent half-migration. |
| Explicit package/workspace monorepo, same deployment | Introduce where isolation tests require it | Stronger imports and independent tests; requires deliberate packaging/path changes. |
| Separately deployable UI, API and worker services | No current requirement | Adds startup, compatibility and connection failure modes to a local product. |
| Broad rewrite | Not recommended | Recreates consent, restoration and live-run protections while also migrating behavior. |

The frontend review favored preserving the seven sections and the existing controller split. The backend review placed exclusive device ownership and durable lifecycle ahead of layout changes. The architecture critic's strongest objection was that extracting files without import restrictions can merely spread global state across more files.

The consensus survived that objection with a condition: enforce dependency isolation during extraction. Package boundaries are a means of enforcing ownership, not a phase to postpone automatically.

---

## Ownership boundaries

| Boundary | Owns | Must not own |
|---|---|---|
| Browser feature | Selection, unsaved drafts, read-only domain projection, observation freshness | Durable worker lifecycle or another feature's mutable data |
| Local API/application service | Command validation, target/revision checks, lifecycle transitions | Device-global runtime state inside request threads |
| Operation ledger | Operation identity, original target, acceptance, steps, outcomes, recovery state | Duplicate template pixels, inventory or entire calibration reports |
| Device worker | One transport's input authority, screen observation, safe side effects | Another worker's transport or a newly selected browser context |
| Domain repositories | Profile, calibration artifacts, restoration journals, configuration | Inferring success merely because a process disappeared |

Overview composes feature snapshots; it is not a universal writable store. Views dispatch named commands. Domain controllers use API adapters. Pure contracts/readiness logic accept explicit inputs. Workers retain the socket-based device boundary.

Keep Overview, Runs, Schedule, Equipment, Rewards, Screens and Settings. Simplify Screens into a guided learning/preparation flow with requirement-specific repair and an advanced area. New top-level sections need demonstrated user-navigation problems, not just more backend modules.

---

## First implementation slice

Deliver one complete journey: selected run → checked requirements → Prepare → reload/reconnect → apply discoveries → readiness → Start.

1. **Ownership prerequisite:** establish one OS-level device lease, keyed by the actual ADB server/transport, used by every exclusive device-driving entrypoint. Define supervisor-to-worker handoff. Retain conservative detection of legacy workers while migrating. A different checkout is not proof of a different device. Verify two transports can be independent without weakening same-device exclusion.
2. **Durable preparation:** persist acceptance before reporting it; return an operation ID. Use a local SQLite ledger for transactional lifecycle/idempotency, with existing files remaining authoritative artifacts. Bind worker launch and reports to the original account, instance, device, rendering, profile, selected run and relevant revisions.
3. **Explicit completion:** address status/cancel/apply by operation ID and expected revision. Distinguish capture completion, restoration required, awaiting application, readiness and interruption. Application must check the original target and revisions. A stale operation must not overwrite a later profile edit.
4. **Frontend projection:** adapt the existing learning controller to IDs and revisions. Use one ordered observation path per operation. Browser storage retains a resumable reference, not authority. Disable Start for absent, failed or stale readiness. Keep connection freshness separate from the last confirmed operation outcome.
5. **Finish the defined migration:** apply the validated lifecycle convention to the remaining long-running device operations and remove superseded timestamp/current-target paths. Extract feature routes/controllers while doing this; avoid a second competing command path.

Proposed operation fields: `id`, `kind`, `idempotency_key`, `revision`, immutable `context`, request/consent scope, lifecycle status, worker identity/attempt, current step, timestamps, versioned result references, structured error and recovery action. Worker identity includes process creation time, not PID alone.

Side-effect recovery must not promise exactly-once device actions. Record intent and reconcile the observed game screen after interruption. Step policies distinguish observation, safe retry, restore-first and human review. A missed heartbeat alone must not release ownership while a worker can still send input. A cancelled driver does not mean a game battle ended.

---

## Evidence required before expanding the pattern

Use temporary artifacts and fake workers for the initial contract tests; do not interrupt a real farm to test failure recovery.

- Two simultaneous starts for one transport produce one acceptance and one conflict; a retried request returns the original operation.
- Switching profile/account cannot reassign an operation or display another target's readiness.
- Reversed HTTP responses cannot publish an older revision; reload resumes the same operation.
- Dashboard restart and abrupt worker exit produce explicit reconciliation, never inferred success.
- Capture success followed by apply failure remains recoverable without repeating device actions.
- A delayed cancellation for operation A cannot cancel operation B.
- Restoration precedes retries, and an unrelated live battle cannot become setup-owned.
- Profile revision conflict preserves discoveries and refuses an overwrite.
- Core/control imports in a clean temporary installation produce no configuration writes, device access or checkout-path assumptions.

If the last test requires repeated path shims or exceptions, introduce installable core/control/runner packages immediately. Keep one launcher and release regardless of package layout. Browser package tooling is justified by enforced import rules and isolated feature tests, not by directory appearance alone.

---

## Six-month consequences and review triggers

Expected benefit: one lifecycle convention, attributable outcomes and recoverable setup across reloads, with device safety staying in the workers. These are intended outcomes, not measured improvements yet.

Primary risk: a permanent mixed system where some operations use IDs/leases and others use process scans/timestamps. The pilot must have an explicit completion boundary and retire its former path. A ledger becomes harmful if it duplicates domain artifacts; a central browser store becomes harmful if every feature can mutate it.

Revisit deployment separation only for a concrete remote-host or independently released-client requirement. Revisit package granularity when dependency isolation cannot be tested in the current layout. Revisit navigation after observing a user fail to locate a task in the current sections.
