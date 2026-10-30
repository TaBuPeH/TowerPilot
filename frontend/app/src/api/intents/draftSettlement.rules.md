# Submitted draft retention
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-23
> **Updated:** 2026-10-23

`draftSettlement.ts` owns the cleanup decision for submitted drafts. A matching read
can report applied while `lateAnswerPossible` remains true. Keep the raw proposal
and its original revision durable during that interval. For profile patches, a
definite refusal takes precedence over read settlement and keeps the draft
available for correction. Other write kinds retain their existing read-settlement
rules, including the saved-run committed-save refusal.
Final success, or positive read settlement after the answer can no longer arrive,
permits cleanup of submitted values only; subsequent edits stay.

Schedule persists its raw proposal and revision with its draft. Collection and Runs
entry keep those values in intent metadata and their persisted draft scopes.
Abilities persists its editor and frozen submission. Stores preserve their own raw
draft types and call this shared decision before replacing or clearing them.
