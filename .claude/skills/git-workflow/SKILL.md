---
name: git-workflow
description: "Unified git rules — branch policy, commit discipline, push/PR discipline, and pre-flight checks. Load when staging, committing, pushing, opening a PR, or merging in this project's repos."
---

# Git Workflow — Unified Rules

**Feature branch + PR is the only path to protected branches. Never `git checkout` back to the base branch while your PR is still open. Run the pre-flight checks before every push.**

## Rules

1. **Never direct-push to protected branches** (`master` / `main` / `staging` / `develop`). Work lands only through the [Default Cycle](#the-default-cycle). "push to develop" / "work on develop" name the cycle's ship endpoint — **NOT** authorisation for `git push origin develop`. A `git worktree` is created ONLY on explicit "use worktree".
2. **Never push repos you weren't asked to touch** — infra and CI/CD repos are off-limits unless the user explicitly says so.
3. **Return to the base branch only after your work has merged.** Do not switch to it while a PR is open. Bar is *merged*, not *pushed*.
4. (AstraBit-specific no-AI-attribution rule removed in the tower copy — tower follows the Claude Code default attribution footer.)
5. **Always return the PR URL** from `git push -u`'s output. Do not switch branches afterward (Rule 3).
6. **Respect local hooks** (pre-commit format, pre-push lint, etc.). Fix hook failures; never `--no-verify` without explicit user ask.
7. **Never `git stash` in config/docs repos.** Stashes get orphaned silently. Use a WIP commit (`git commit -am "WIP — to be amended"`) instead.
8. **Lint / prettier-only fixes ship with zero workflow paperwork.** The feature branch + PR is the paper trail — never file a pending task for a format-only fix.
9. **NEVER `gh pr merge --squash` (or `--rebase`) — ALWAYS `--merge`, on every branch in every repo.** Squash/rebase rewrite SHAs and destroy cherry-pick history. Hard, blanket rule, no per-branch exception.

## Branch Policy

### The Default Cycle

Every change ships through this cycle.

```
base branch (pull)  →  feature branch  →  work + commit/push  →  PR to base  →  gh pr merge --merge  →  checkout base (pull)  →  next feature branch
```

1. `git checkout <base> && git pull` — cut from the current tip.
2. Create a feature branch: `feat/<slug>`, `fix/<ticket>-<slug>`, `chore/<slug>`.
3. Work only on that feature branch — never commit directly on protected branches.
4. Commit and push when told or when a consumer needs the change.
5. Open a PR to the base branch; capture and surface the PR URL.
6. `gh pr merge <num> --merge` — **autonomous for routine app-code PRs**; **PAUSE and confirm** for shared-contract changes and infra/CI PRs.
7. `git checkout <base> && git pull` — only after the merge lands (Rule 3).
8. Next unit of work → new feature branch; repeat from step 2.

### Push target convention

| Phrase from user | Means |
|---|---|
| "push" (no branch named) | Push the current feature branch to its remote (step 4). |
| "push to develop" / "push to main" / "push to master" | Ship endpoint of the cycle — PR → `gh pr merge --merge`. **NOT** a direct push. |
| "use worktree" | Opt-in only: run the cycle in a separate `git worktree`. |
| "force push" | Always confirm first. Never force-push to a protected branch without explicit user authorisation. |

### Protected-branch protection

Before pushing, verify the current branch is NOT a protected branch (`git branch --show-current`).

## Commit Discipline

Subject format: `<type>(<scope>): <imperative summary>` (under 72 chars). Types: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`. No AI-attribution trailers (Rule 4).

## Push & PR Discipline

`git push -u origin <branch>` prints a `Create a pull request` URL — capture and surface it. Do NOT check the base branch back out after push — stay on the feature branch until merged (Rule 3). Always `gh pr merge --merge`; never `--squash`/`--rebase` (Rule 9).

## Pre-Flight Checks

Run BEFORE every push.

1. **Lint passes.** Run the project's lint command.
2. **Diff sanity** — `git diff --stat` against the base; confirm only intended files are staged; no stray artifacts.
3. **Tests pass** on the touched area, if the project has them.

## Anti-Patterns

- **`git checkout <base>` while PR open** — stay on the feature branch until the merge lands (Rule 3).
- **"push to develop" → `git push origin develop`** — that phrase is the cycle's ship endpoint, NOT a direct push.
- **`gh pr merge --squash`** — always `--merge`; squash destroys cherry-pick history (Rule 9).
- **`git stash` in config/docs repos** — WIP commit instead (Rule 7).


## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Push rejected on a protected branch | Committed directly to a protected branch | Branch off (`git switch -c fix/<slug>`), reset the protected branch to origin, open a PR |
| Pre-push hook fails | Lint/test failure | Fix the underlying issue; never `--no-verify` without explicit user ask |
| Merge conflict on PR | Base moved underneath the branch | Merge the base into the feature branch (never rebase a pushed branch without confirming), resolve, re-push |

## Verification

Before declaring a push complete:

1. `git status` → feature branch checked out, NOT a protected branch
2. PR URL captured from push output and surfaced to the user
3. `git log -1` → subject follows the `<type>(<scope>): <summary>` format, no AI-attribution trailers

## See Also

- [no-bulk-scripts](../no-bulk-scripts/SKILL.md) — cross-repo git work is per-target agent fan-out, never a loop
- [finish-the-scope](../finish-the-scope/SKILL.md) — the branch cycle is a scope; finish it
