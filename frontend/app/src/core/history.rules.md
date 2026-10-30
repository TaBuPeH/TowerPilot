# Historical battle evidence
> **Status:** Active
> **Type:** Domain rules
> **Updated:** 2026-10-22

[history.ts](history.ts) owns evidence checks for the read-only History page.

- Pages contain twenty records and use one extra server row to establish Next. There is no total count.
- A prepared record establishes no start; an interrupted observer establishes no defeat. Display recorded outcomes and origin without inferring earlier history.
- Stats capture preserves failed, pending, missing and unverified lifecycle states separately. Verified stats additionally require the server's verified, fresh, structured, matching-tier and consistent-wave flags, the same battle and envelope, and an observed entry. Evidence flags never upgrade another lifecycle state to verified. Keep the original status and any reason/error visible; an unknown status remains unknown.
- Collection counts come from the server's sampled evidence only. Require its generation and observation bounds to match the battle, valid nonnegative counters, and valid recorded confirmation times. Positive counters also require the corresponding channel's confirmation; an empty confirmation map supports only zero counters. Withhold counts with invalid provenance. Never calculate a battle gem total or a balance increase from ad claims.
- Missing values remain unknown; numeric zero remains zero. Recorded gaps and unknown server strings remain visible.
- **Action executions** ([historyActions.ts](historyActions.ts), A2-7 AC4) come from the `executions` block of the battle detail and, for older rows, `GET /api/actions/executions?battle_id=&before=` read with the block's `next_before`. A history is a read, not a status:
  - `items: null` never reads as "nothing ran": `recorded_before_action_history` and `newer_schema` have their own sentences, any other reason is unreadable and keeps its code, a missing `executions` member is "not reported". Only a list that is present and empty reads as an empty history.
  - A `running` row is "open when read" with the read time. It has no effect and no duration. A refresher-settled row (`interrupted`) has no finish time and no duration and keeps its reason.
  - Only the server's `effect` says what an action did (confirmed, unconfirmed, unknown, none); a result or state never stands for it. Duration shows only for a monotonic duration on a row that is neither open nor an aggregate. An aggregate shows its count with the first and last check time. The row states both facts (`open`, `showsDuration`); the page only renders them. A duration reads in whole seconds through the catalogue's one duration rule (`copy/format.ts` `durationText`).
  - Coverage (`full`, `since_adoption`, `unknown`) is shown on every row. A word this build does not know shows as sent with "Not recognised".
  - The action name is the `label` when present; with no label the action code is shown in plain monospace (root decision Q4).
  - `truncated` without a cursor says older rows exist and offers no read. A page that names another battle or emulator is discarded. A page that carries a `reason` is refused and keeps that code, shown under Why next to the failure. The older pages are cleared when the detail is read again, a record is selected, the filter or page changes, or the target changes. Rows are keyed by id, so one listed twice shows once.
  - The gem evidence rule above stays gem-only: the sampled-collection checks read `ad_gems` and `floating_gems` confirmations and no execution row feeds them.

[store/history.ts](../store/history.ts) owns query lifecycle: one read per slot, independent list/detail retries, target and request fencing, same-query last-loaded retention, and clearing on target change.
