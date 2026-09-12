---
name: verify-against-ground-truth
description: >-
  Documentation (plans, architecture docs, audits, status matrices, READMEs,
  memories) is a guide, never the source of truth — the source code is. Use when
  relying on any load-bearing doc claim (a file path, flow, contract, field, or
  done/complete status) during research OR implementation, when implementing
  from a plan older than ~a day (re-scan current code first), and before
  replicating a pattern across multiple targets (pilot + test one first).
---

# Verify Against Ground Truth

**Documentation describes; the source code decides. Prove every load-bearing claim in source before you rely on it — even if that means reading a dozen modules to confirm one flow. A doc is a map, not the territory; a refactor moves the territory and rarely back-fills the map.**

## Rules

1. **Prove load-bearing claims in source.** Before relying on any claim from a plan, an architecture doc, an audit, a status matrix, a README, or memory, open the source and confirm it. A claim is **load-bearing** when your next action breaks if it is wrong — file paths, flows, contracts, field names, and "done / complete" statuses. Skim the rest; verify these.
2. **Code wins disagreements — then fix the doc.** The source is canonical. When code and any doc disagree, the code is right; correct the stale doc in the same change or file a pending task so the next reader is not misled.
3. **Re-scan stale plans before implementing.** If the plan / spec you are about to execute is older than ~24h, re-read the current target code first — refactors move code underneath the plan, and paths the plan names may no longer exist.
4. **Research and implementation get equal rigor.** Misinformation in research → bad plan → bad implementation. Verify a "how does X work" answer against source exactly as hard as code you are about to ship.
5. **Conform, never invent.** Match the working sibling code and the loaded skills — never reconstruct a pattern from memory or from the doc alone.
6. **Pilot → test → catalogue → replicate.** Implement ONE instance end-to-end, prove your expectations with a real test, write down the actual steps, and only then fan the pattern out. An untested instance is a paper claim, not a done item.

## When to verify (decision table)

| Situation | Action |
|---|---|
| Doc names a file / module / package path you will open or import | Confirm the path resolves in source (`Glob` / `ls`) before relying on it |
| Doc claims a flow goes A → B → C | Trace each hop in source before asserting it or building on it |
| Doc / status column says "done / complete / fixed" | Grep the actual implementation — statuses lie in **both** directions (matrices over-claim, audit `[ ]` boxes go stale after the fix ships) |
| Plan / spec is older than ~24h | Re-scan the current target code before writing anything |
| About to replicate a pattern across N targets | Pilot 1 + prove it with a test, then fan out the rest |
| Doc is orientation only — no action depends on the detail | Read for the map; no verification needed |

## Anti-Patterns

| Temptation | Why it fails | Do this instead |
|---|---|---|
| Cite an architecture doc's flow in a plan without opening the source | Docs drift and disagree with each other; you build on a false premise that surfaces only at runtime | Trace the flow in source; cite `file:line` (Rule 1) |
| Trust a status matrix's "done" column | Status docs over-claim — work is routinely "documented but deferred" | Grep the implementation before treating it as done (Rule 1) |
| Trust an audit's unchecked `[ ]` boxes as current state | Audit checkboxes go stale after the fix ships — they under-claim | Verify against **today's** code; the code is the tiebreaker (Rule 2) |
| Implement straight from a day-old plan | The refactor moved the code; the plan's paths may not exist | Re-scan target code first (Rule 3) |
| Fan a pattern to N targets after coding 1 from docs alone | Only piloted + tested instances are trustworthy; the rest copy the first instance's bugs | Pilot 1, test, fix the template, then replicate (Rule 6) |
| Follow a doc's file path without checking it resolves | Reorgs / refactors move paths | Confirm the path on disk before searching/importing (Rule 1) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Implemented per the plan, but the path / module does not exist | Trusted a stale doc path | Locate the real path in source (`Glob` / `Grep`), implement against it, update the plan (Rule 2) |
| Two docs about the same work disagree on status | Neither was back-filled after the fix shipped | Read the code — it is the tiebreaker; reconcile both docs to it (Rule 2) |
| Flow "works in the doc" but breaks at runtime | The doc described intended, not actual, behavior | Trace each hop in source; instrument with unique debug-log markers and read a live run |
| Pattern replicated to N targets, several broken | Fanned out from an un-piloted instance | Pilot + test instance 1, fix the template, re-apply (Rule 6) |
| A "done" item has no test or run exercising it | It is a paper claim, not verified work | Exercise it before marking done (Rule 6) |

## Verification

Before reporting research findings or shipping an implementation, self-check:

1. For every doc claim my next action depended on, did I open the source and confirm it (path resolves, flow matches, status is real)?
2. If the plan was older than ~24h, did I re-scan the current target code before implementing?
3. When code and a doc disagreed, did the code win — and did I correct or flag the stale doc?
4. Did I pilot one instance and prove it before replicating to N targets?
5. Are my conclusions cited to `file:line` in source, not to a doc paragraph?

## See Also

- [source-code-first](../source-code-first/SKILL.md) — search source before compiled/vendored copies (WHERE to look; this skill is WHETHER to trust the doc at all)
- [correct-fix-over-easy-fix](../correct-fix-over-easy-fix/SKILL.md) — verify the root cause, not the symptom
- [single-source-of-truth](../single-source-of-truth/SKILL.md) — code is the SSOT; docs are caches
- [verify-never-infer](../verify-never-infer/SKILL.md) — never state an unverified claim as fact
