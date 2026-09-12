---
name: no-bulk-scripts
description: >-
  Forbids writing or running scripts (PowerShell / Bash / Node / Python / inline
  `Bash` loops) that iterate over multiple repos or targets AND mutate state —
  commit, push, install, edit, migrate, rebase, run a codemod, open/merge PRs.
  Trigger when about to write or modify a script that loops over repos/projects,
  when authoring an inline `Bash` loop over multiple repos, when about to run
  an existing bulk-mutation script, OR when the user asks to "automate this
  across N repos / projects / packages". Read-only audit / search / grep /
  inventory scripts are EXEMPT (except git — see Rule 5). Single-target scripts
  (one explicit `--repo` arg per call) are EXEMPT. Cross-repo mutation work MUST
  be per-target agent fan-out with per-target intelligence.
---

# No Bulk-Mutation Scripts

**MANDATORY: NEVER write or run a script that mutates state in more than one repo or project. Cross-repo mutation work MUST be driven by per-target agent spawns (one `Agent` per target) or a repeatable per-target procedure applied with judgment between calls. Scripts cannot inspect target state, branch off, skip-on-mismatch, or prompt — agents can. One bulk-script incident dwarfs months of "saved" agent overhead.**

**MANDATORY corollary: every per-target value that determines WHETHER the script/agent mutates — destination branch, expected remote, expected version, expected file shape — MUST be passed in by the orchestrator as an explicit expectation. Auto-detection (`git symbolic-ref`, `git config`, `gh repo view`, file globs) is for VERIFICATION ONLY. If the auto-detected value disagrees with the orchestrator-supplied expectation, the agent HALTS. Scripts have no halt layer; agents do.**

## The Rules

1. **Cross-repo mutation = agent fan-out.** If the work touches >1 repo/project AND has side effects (commit, push, install, file edit, migration, rebase, codemod, `gh pr create`, `gh pr merge`), it goes through one Agent spawn per target. NEVER an internal loop in a script.
2. **Per-target pre-flight is mandatory.** Each target agent MUST verify before mutating: (a) the current branch matches the expected branch passed explicitly in the agent's prompt — never auto-detected; (b) the working tree is in the expected state; (c) any expected pre-state holds.
3. **"Auto-detect state inside a fan-out script" is FORBIDDEN.** Any helper that runs `git branch --show-current` (or any equivalent) and keys a mutating action on it across N repos is a foot-gun — this exact pattern has merged work to the wrong branch fleet-wide.
4. **Read-only scripts are EXEMPT — except git.** Non-git search/grep/audit/inventory/status scripts can iterate freely. Any `git` subcommand in a loop over multiple repo paths deserves extra scrutiny even when read-only — prefer per-target invocation.
5. **Single-target scripts are EXEMPT.** A script that operates on ONE explicit repo/target (via `--repo <path>`) is fine — the orchestrator may call it N times with per-target judgment between calls.
6. **Expected per-target values are PROMPT INPUTS.** Every destination/target/version/shape value comes from the orchestrator's prompt. The agent uses auto-detection only to VERIFY and HALT on mismatch. Pattern: `EXPECTED_BASE=develop` (from prompt) → `DETECTED=$(git symbolic-ref ...)` → `if [[ "$EXPECTED_BASE" != "$DETECTED" ]]; then HALT; fi`.
7. **Live remote queries beat local caches — always.** `refs/remotes/origin/HEAD`, `git config branch.X.remote`, and similar caches are set at clone time, never auto-refresh. Query the live source (`gh repo view --json defaultBranchRef`). Better: prompt-supplied expectation + live query to verify and halt.
8. **Dead fallbacks are worse than no fallbacks.** `X=$(primary) || X=$(secondary)` and `[ -z "$X" ] && X=$(secondary)` fire the secondary only on empty-primary, never stale-primary. Either drop the primary lookup, or run both and HALT on disagreement.

---

## Decision Table

| Pattern | Allowed? |
|---|---|
| Script loops repos, runs `grep` / file reads (no git, no mutation) | YES — read-only, non-git |
| Script loops repos, runs `git push` / `npm install` / `pip install` / `gh pr create` | NO — cross-repo mutation |
| Inline `Bash`: `for d in */; do (cd $d && git push); done` | NO — transport irrelevant, shape forbidden |
| Script `do-thing.ps1 --repo <path>`, orchestrator calls it N times | YES — single-target per call |
| Parallel agents, one per target, each with explicit expectations | YES — canonical correct pattern |
| `gh pr create` without an explicit `--base` | NO — defaults vary per-repo; always pass the literal base branch |
| Reading `refs/remotes/origin/HEAD` to decide a mutation destination | NO — stale local cache |
| Fallback pattern `X=$(local) \|\| X=$(remote)` or `[ -z "$X" ] && X=$(remote)` | NO — dead fallback; fires only on empty-primary, never stale |

---

## Pre-Write Checklist

Before writing any new script or inline `Bash` loop:

- [ ] Does it mutate state AND iterate over more than one repo/target? → **STOP. Per-target agent fan-out instead.**
- [ ] Does any loop body contain a `git` subcommand over multiple repo paths? → **STOP** — restructure as per-target calls.
- [ ] Single-target only: confirm the script takes one explicit `--repo <path>` argument; no internal loop.
- [ ] All per-target values (destination, version, shape) supplied by the orchestrator's prompt — not auto-detected?
- [ ] All remote-side facts queried live (`gh repo view`, `gh api`) — not read from `refs/remotes/origin/HEAD` or `git config` caches?
- [ ] No dead fallbacks (`X=$(local) || X=$(remote)` / `[ -z "$X" ] && X=$(remote)`)? Either drop the local lookup or run both and halt on disagreement.
- [ ] `gh pr create` / `gh pr merge` carry an explicit literal `--base`? No `--base "$VAR"`, no omitted `--base`.

---

## See Also

- [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md) — bulk script = Easy Fix; per-target agent = Correct Fix
- [`prefer-uniformity`](../prefer-uniformity/SKILL.md) — uniform application across N targets ≠ uniform execution by a script
