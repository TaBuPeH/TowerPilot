# Schedule editing rules
> **Status:** Active
> **Type:** Domain rules
> **Created:** 2026-10-22
> **Updated:** 2026-10-22

The backend profile validator owns plan validity, window semantics, priorities and tournament limits. This store owns raw drafts, target fences, explicit saves and readback reconciliation. It never calculates eligibility, remaining quota or next starts.

Drafts belong to emulator, serial, account and profile together. Each save captures the entire plan and the profile revision in one request. Changes typed while it is pending remain dirty. Saves use the existing intent machine and cannot be resent. Unknown results are checked by a later profile read; a different plan at a changed revision stays unresolved. Revision conflicts require a fresh read and explicit reconciliation before another Save.

Profile reads started before the latest accepted save answer cannot replace its source, revision, draft or validation metadata. The shared request sequence orders that evidence; profile revision hashes are opaque and cannot be sorted. Matching status observations still update independently. Each accepted save replaces the previous source snapshot, so problems, validator failures and warnings belong only to the answer that reported them.

Empty counts are omitted deliberately. Invalid numbers remain strings or their exact numeric value and go to canonical validation. Raw text remains in the draft. Empty days are sent as empty lists, never removed to activate fallback scheduling. Per-block quota and next starts have no producer and remain not reported.
