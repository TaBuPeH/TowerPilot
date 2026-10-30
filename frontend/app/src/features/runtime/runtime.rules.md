# Runtime evidence and query rules
> **Status:** Active
> **Type:** Domain rules
> **Created:** 2026-10-22
> **Updated:** 2026-10-22

`core/runtime.ts` owns Runtime read checks, evidence classification, diagnostic filter validation,
local-time conversion and export selection. Settings remain owned by the shared settings domain.

- Identity is the server's report. Missing data is not reported; unknown liveness cannot be verified.
- Source matching requires the reported comparison and both fingerprints. A source error remains visible.
- A fingerprint snapshots Python source on disk at load; it does not prove imports or game state.
- Limits are integers from 1 through 1000. Invalid raw drafts remain unchanged. Since uses browser local time.
- Refresh repeats applied filters. Applying a new query clears old-query results and fences late answers.
- Failed refresh retains same-query evidence and its reason. Export uses the retained result's filters.
- This page has no process action. Settings saves use shared context, revision and no-resend fences.
