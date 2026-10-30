# Observing collection, failures and stream performance
> **Status:** Active
> **Type:** User guide
> **Created:** 2026-10-19
> **Updated:** 2026-10-24
> **Tags:** diagnostics, collection, streaming

## Understand an Ad Gem wait

Open **Dashboard → Current battle → Ad gems**. **First observed** is the local
time and age of a recognized offer, not its actual spawn time. When the preceding
usable check showed absence, **Appearance bounds** gives the interval after that
check and by the first sighting. A missing battle frame, recognition error,
disabled collector or observation lapse makes visibility **Unknown**. A later
sighting starts a new observation segment; it cannot prove continuity across the gap.

Enabled collectors passively check battle frames already supplied by the driver
between collection windows, throttled by the configured offer-check interval.
These checks take no extra screenshots and send no input. The configured
collection interval, window limit and response delay remain in force; see
[collection timing and settings](SETTINGS.md#saving-and-applying-changes).
Seeing an offer during the pause does not open a window or start its response delay.

| Evidence | How to use it |
|---|---|
| Scheduled window / passive check | Distinguish waiting for permission to collect from waiting to observe again. |
| Response countdown / window close | See when the chosen delay is due and how much collection time remains. |
| Schedule wait / response delay / verification | Separate time waiting for a window, the selected response delay, and fresh checks through the attempt's outcome. |
| Latest failed attempt | Read its reason and time, even while the next window is scheduled. |
| Attempts / confirmed / skips / missed windows | Compare outcomes within this collector. An attempt includes verification and may stop before any tap. |

Expand **Observation details** for raw state, including the offer rectangle,
timestamps, identifiers and failure diagnostics. **Inspect events** opens Runtime
with the battle, operation and offer filters and a start time before the first
sighting. Offer history can span multiple windows. A dispatched tap, dry run or
screen change alone does not prove a reward; inspect the confirmation outcome.

Source: [Ad Gem collector](../backend/interactions/ad_gems.py) and
[live controls](../frontend/app/src/features/runs/RunEditor.tsx) with
[Battle details](../frontend/app/src/features/runs/BattleDetails.tsx) on the Runs page.

---

## Follow an operation through its events

Identifiers connect related records; not every record carries every identifier.

| Identifier | Scope |
|---|---|
| `request_id` | One API request; returned in response headers and shown with browser API failures. |
| `trace_id` | Correlation across a request and work that inherits its context. |
| `operation_id` | A managed operation and its associated driver work. |
| `battle_id` | The recorded battle, across its driver segments. |
| `command_id` | A live control/action command and its acknowledgement records. |
| `window_id` | One collector window. |
| `offer_id` | One observed Ad Gem offer segment. |
| `attempt_id` / `claim_attempt_id` | One invocation or claim verification attempt. Runtime's Attempt ID filter also matches `claim_attempt_id`. |

Pair start records with their terminal outcomes. An accepted request or started
attempt does not establish completion. Exceptions retain their type, message and
traceback where recorded; domain refusals and unconfirmed claims retain their
specific outcome.

Source: [diagnostic writer](../backend/runtime/diagnostics.py),
[command acknowledgements](../backend/runtime/battle_session.py), and
[dashboard request handling](../backend/dashboard.py).

---

## Inspect and export raw diagnostics

Open **Runtime → Raw diagnostics**. Filters match exact instance, operation,
battle, command, trace, request, attempt, window, offer and event-kind values.
The Instance filter starts blank so dashboard request records and emulator events
can be followed together. Choose an instance explicitly to restrict the sources.
Combine filters to narrow results. Choose **Since (local time)**, set the maximum
returned records, and press **Apply filters**. **Failures only** includes recorded
errors, refusals, skips and unconfirmed outcomes; it is not a proof of overall success.

Expand a row for its original JSON and log file/offset metadata. **Refresh** uses
the applied filters; drafts stay in their inputs until applied. Auto refresh runs
every five seconds only while this panel and browser tab are visible.
**Export filtered JSON** downloads the returned records, the filters that produced
them, scan coverage and dashboard writer health in the browser.

Every result is a **bounded scan**, with files/bytes scanned, matched and returned
counts, truncation, malformed-record counts and read errors. Expand the coverage
details for exact bounds and errors. Empty results mean no matches in the scanned
records; they do not establish that no failures occurred outside those bounds.

Writer health reports write-error counts and the latest storage error for the
**current dashboard process only**, identified by its PID/process ID. It does not
report the health of runner or other process writers. Missing health is unknown;
even a zero count cannot establish that all expected events reached disk.

Source: [bounded event queries](../backend/control/diagnostics.py) and
[Runtime viewer](../frontend/app/src/features/runtime/DiagnosticsSection.tsx).

---

## Set stream quality and compare measured FPS

The Dashboard's **Quality** selector defaults to **Native**. It controls image
resolution and JPEG compression independently of the FPS cap. FPS and quality
selections are remembered in this browser when local storage is available.

| Quality | Resolution | JPEG quality |
|---|---|---|
| Native | Full emulator resolution | 90 |
| Balanced | 65% of each dimension | 85 |
| Low bandwidth | 40% of each dimension | 70 |

Native preserves small text and game details; it sends larger frames. The status
line shows the delivered dimensions and JPEG quality so you can verify the feed.
Changing this selector does not alter the emulator's display settings or calibration.

Dashboard's stream selector requests **0.5, 1, 2 or 4 FPS**. The target is a cap,
not guaranteed throughput. The displayed delivered rate measures completed frames
at the server; it does not measure browser paint rate. Capture/encode times,
error count and the latest error help explain a lower rate.

Streaming uses raw screencaps through the adb socket and encodes JPEG on the host,
avoiding a device PNG encode/decode round trip. The server caps requests at 4 FPS.
Changing FPS or quality replaces the stream request; ordinary status ticks retain
the selected settings. Switching pages, hiding the browser tab or turning streaming
off releases the stream request.

Source: [stream server](../backend/dashboard.py) and
[stream controls](../frontend/app/src/features/dashboard/Preview.tsx) (the Dashboard preview).
