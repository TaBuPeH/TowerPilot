# Runs, temporary controls, upgrade queues and history
> **Status:** Deployed; control acknowledgement and ad-gem claim observed; purchases and natural end unverified
> **Type:** Architecture and delivery plan
> **Created:** 2026-10-19
> **Updated:** 2026-10-19
> **Scope:** Reliable local farming on configured emulators

## Agreed outcome

Configure farming from a saved run, freeze its settings for each observed battle,
make temporary changes during that battle, and retain configuration, activity and
observed results. Use the flat sidebar and compact inline controls. This pass
offers purchase patterns without automatic wave-based shopping stages.

Extend the existing Flask application, worker processes and SQLite operation
database. A repository migration or additional service does not resolve the
configuration and acknowledgement gaps.

| Surface | Responsibility |
| --- | --- |
| Dashboard | Current battle, routine status, seconds countdowns and temporary controls |
| Runs | Saved configurations, entry/equipment, collection and shopping defaults |
| Run history | Completed or interrupted observations, settings and recorded activity |
| Schedule | Which saved runs to launch at boundaries |
| Equipment, Collection, Abilities | Shared policy libraries |

---

## Configuration and identity

Saved configuration, driver operation and game battle have different lifetimes.
A worker can observe several battles. Constructing runtime state does not prove
battle entry, and process loss does not prove battle death.

The accepted snapshot contains the compiled preset, configuration, profile,
source revision and resolved controls. Workers consume it instead of recompiling
editable files after acceptance. The battle ledger stores an immutable envelope
and hash separately from requested and applied overrides. At repeat boundaries,
reload current saved source, pin the existing emulator/account/run, validate
compilation and readiness, then prepare a new envelope. Failure holds safely.

Saved edits require profile revision and account/emulator/profile context checks.
Editing shared gather or shopping policy from Runs clones effective values into
a run-owned policy. Live changes never write the profile. Counters and timer
anchors survive pauses and edits; new battles clear temporary overrides.

Scheduler children receive a prepared ledger record containing the parent's
exact entry configuration. Entry precedes this record, so the initial handoff is
partial adoption with unknown original start. Interrupted or ambiguous
observations remain separate and linked. Matching waves alone do not merge them.

Machine permissions, input ownership and foreground evidence remain safety
constraints independent of the frozen farming envelope.

The current lifecycle integration covers the generic farm/tournament orchestrator
and collector-only driver. Standalone shard/quest batches expose saved collection
settings only; they do not yet provide live controls or per-battle history.
Unsupported gates are disabled in the editor. See the
[supported-driver review](RUN_WORKSPACE_REVIEW.md#supported-driver-limitation).

---

## Live controls and manual actions

Controls cover ad gems, floating gems, shopping, rewards, abilities, rescue and
repeat where supported by the driver. Numeric edits autosave after valid input;
live changes remain Pending until driver acknowledgement. Reset inherits frozen
battle settings, not newly edited saved defaults.

| Behavior | Contract |
| --- | --- |
| Timers | Publish driver deadlines, observation time and blockers; render seconds |
| Disable/re-enable | Preserve elapsed time, counters and daily state |
| Rewards | Prevent new visits; drain an already-owned navigation flow safely |
| Ability/rescue gates | Stop future decisions at checkpoints; reobserve for reconciliation |
| Repeat | Change the next boundary decision; never end a live battle |
| Run now / Try once | Bypass schedule only; retain recognition, reserve and ownership checks |

Commands carry operation, battle, generation, expected revision and idempotency
key. Conflicting manual actions serialize. Acceptance, start and actual outcome
are distinct. Expired commands cannot act on a new battle. Boundary classification
precedes input-producing work.

| HTTP surface | Purpose |
| --- | --- |
| `GET/POST /api/run-controls` | Saved controls with revision/context fencing |
| `GET /api/battles/active` | Applied/requested controls, capabilities, deadlines and commands |
| `GET /api/battles` and `GET /api/battles/{id}` | History and event detail |
| `POST /api/operations/{id}/live-controls` | Battle-scoped settings request |
| `POST /api/operations/{id}/actions` | Idempotent manual action |

---

## Upgrade selection and purchase patterns

Attack, Defense and Utility reuse Cards chip styling. Selection contributes to
one draggable priority queue; page membership does not partition execution order.
Selected chips keep attempt limits and Try once visible. Keyboard move buttons
provide an alternative to drag. Weighted mode adds a per-turn amount. Blank
attempt limits mean unlimited attempts, not confirmed purchase counts.

| Pattern | Behavior |
| --- | --- |
| Single focus | Highest-priority eligible affordable upgrade, reconsidered each purchase |
| Round robin | Rotate through eligible affordable entries after an attempt |
| Weighted split | Configured batches, such as three Damage and two Health; only confirmed purchases advance quotas |

Patterns and amounts can change live without changing saved defaults. Skip
temporarily unaffordable entries without accumulating catch-up debt. No automatic
stage scheduler or wave-condition editor is included in this pass.

An unset cash floor blocks buying; zero is explicit. User-specific floors remain
local. Each purchase requires fresh cash/quote, verified target/tab and verified
single quantity or verified total cost. Bulk/MAX totals remain unverified.
Strict quantity OCR accepts complete `x1`/`1x` tokens and does not repair ambiguous
`XIO`. Weighted confirmation requires fresh same-tab single-buy evidence of
increased price and a cash decrease consistent with the previous quote. Income
can obscure it. Unknown outcomes terminate after a bounded interval, preserve
attempt counts and do not advance quotas.

The text-only catalog's known section/order list remains provisional until
verified against account UI. Recognition support is distinct from ownership and
learning. Defense Absolute remains an explicit visible default exclusion. No
captured game images enter git.

Legacy shopping remains explicitly unprotected by the cash floor until converted.
Conversion is a reviewable draft preserving selections, cadence and once/per-pass
intent where representable. Recurring click counts become per-turn weights;
confirmation semantics differ and are disclosed. New and old executors never run
simultaneously for one preset.

---

## History and evidence

The ledger stores observation boundaries, commands and acknowledgements. Runtime
events include battle ID, driver segment and envelope hash. Persist a boundary
before optional result capture and repeat entry. Screenshots/copied statistics
remain in ignored local artifact storage.

Clipboard capture records freshness without destroying unrelated clipboard
content. Validate structure and available identifiers before marking stats
verified. Missing, stale, ambiguous and interrupted data stay explicit. Last
observed wave differs from a verified final result. Adoption does not establish
original equipment or start provenance. Never force a death to verify history.

---

## Broad assessment and verification

Interview answers fix the compact chip layout, always-visible selected controls,
global queue, configurable floor and three purchase patterns. The useful outcome
is understandable and reliable behavior while farming, beyond styling alone.

| Framing | Disposition | Reason and evidence gate |
| --- | --- | --- |
| Ambition | Apply | Reuse the existing UI system with explicit queue and acknowledged state; keep the local architecture |
| Altitude | Apply | Make farming behavior easy to change and understand; verify saved/live isolation through the API |
| Depth | Apply | Cash, quantity, freshness and acknowledgements need source evidence, transactional tests and independent review |
| Range | Apply | Use weighted scheduling and editable queue conventions; verify skips, quotas and keyboard ordering |
| Challenge | Apply | Browser overflow/focus checks test density; real captures test quantity and confirmation assumptions |
| Stretch | Defer | A complete verified catalog in one screen needs account-wide UI evidence; revisit after current observations |

The smaller alternative was a styling-only chip replacement. It would win if the
existing executor already established queue semantics, cash protection and live
acknowledgement. Source inspection did not establish those prerequisites.

Acceptance requires compilation, offline backend/frontend suites, independent
skill review, real browser/API checks and bounded authorized farm observation.
Tests cover snapshot stability, stale commands, revisions, shared-policy
isolation, selection, reserve/quantity blockers, pause counters and history.
Live evidence must establish actual driver acknowledgement; mocked tests cannot
establish collected gems or completed purchases. If no natural end occurs during
the bounded check, report that limitation without forcing a game boundary.

---

## Verification result

Recorded offline verification passed 1,984 backend tests with three skips for
absent optional local profile fixtures, and 266 frontend tests with no failures.
The ignored local command outputs are `backend/logs/chip_ad_pytest_verified.txt`
and `backend/logs/priority_split_node_verified.txt`. Independent source findings
and corrections are recorded in the [workspace review](RUN_WORKSPACE_REVIEW.md).
Passing these tests does not establish live collection or purchasing behavior.

The final browser check at a 1280 by 800 viewport measured a 953px-wide
workshop host. Selector and queue chips shared a measured size of
296.828125 by 36px; the queue appeared left of the upgrade selectors, with no
workshop or page horizontal overflow. Current battle controls occupied the
full-width article. The ignored measurement record is
`backend/logs/priority_split_browser_verified.json`. These layout observations
do not establish purchase execution.

The deployed driver adopted an ongoing battle. A temporary shopping-interval
edit and Reset both reached the applied revision; Reset restored the frozen
value. The earlier manual ad-gem command completed with an unconfirmed timeout,
and collector windows returned to their pause periods. These observations
establish acknowledgement and scheduling, not a successful claim.

A later observation after correcting the ad-offer movement check recorded an
actual `ad_gems_tap`, `ad_gems_claim` with `confirmed: true`, and a window ending
with reason `confirmed`. A saved follow-up frame showed the claim absent and
a higher gem balance than the saved before frame, corroborating the claim
without establishing the source of every balance change. The correction in
[ad_gems.py](../backend/interactions/ad_gems.py) compares rectangle centers,
preserving the existing 12px movement guard while tolerating contour-size
variation. Identity-specific logs, frames and verification records remain in
ignored local storage.

Live shopping produced no purchase attempts during the recorded bounded check;
the observed stop reason was `panel_changed`. The captured workshop also showed
bulk quantity. Saved before/after frames passed offline tab detection, so those
captures do not explain the intervening panel change or justify loosening the
tab or quantity guard. Purchases, weighted confirmation and a natural battle
completion remain unverified live. No battle was ended to manufacture history
evidence.
