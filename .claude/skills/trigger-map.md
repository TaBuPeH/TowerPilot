# Skill & Knowledge Trigger Map

> **Status:** Active
> **Type:** Skill index
> **Created:** 2026-08-18
> **Updated:** 2026-08-18

**Always consult the matching skill when your task matches a trigger below.** Match your task against the tables and load every matching skill before writing code.

## Coverage Policy

**Every skill in `.claude/skills/` MUST be accounted for in this file.** A skill is either:
1. Listed in a trigger section below with a concrete trigger condition, OR
2. Listed in the "User-Invoked Only" section with a reason it must not auto-load.

Skills not appearing in either list are **dark** — they exist on disk but Claude won't find them by trigger. Dark skills are a workflow bug; add or mark them.

## User-Invoked Only

Skills in this table MUST NOT be auto-loaded by trigger inference. Load them only when the user explicitly invokes them.

| Skill | Reason it must not auto-load |
|---|---|
| `skill-writing-guide` | Opinionated rubric editor — auto-loading in any skill-editing session would mis-apply the rubric to unrelated work. The skill itself carries an explicit "DO NOT auto-load" warning. |
| `tighten-types` | Explicit-invocation workflow (`disable-model-invocation: true`) — a whole-file type-tightening pass, run only when asked. |
| `hypothesis-tests` | Explicit-invocation workflow (`disable-model-invocation: true`) — property-based test authoring pass, run only when asked. |

## Python & ADB Craft Triggers (imported 2026-08-18, slim set)

| Context / Trigger | Load |
|---|---|
| Any device-I/O work: taps/swipes/keyevents, screenshots, device offline / unauthorized / not-found errors, adbclient.py or capture.py changes, emulator connect/boot issues | `skills/adb-craft` |
| "Slow", "profile", "bottleneck", "memory growth", or hot-loop work in detectors/capture | `skills/python-performance-optimization` |
| Writing or debugging tests, fixtures, conftest.py, pytest config | `skills/pytest-practices` |
| Adding or reviewing type hints; TypeVar / Protocol / TypedDict / mypy / pyright | `skills/python-typing-ops` |
| Code review, refactoring, error-handling design, "why is this failing silently" | `skills/python-code-quality` |
| Choosing or debugging blur / edge / threshold / contrast steps in detector preprocessing; flaky template matches | `skills/opencv-image-processing` |

## Cross-Cutting Triggers

| Context / Trigger | Load |
|---|---|
| Reporting any factual claim to the user — values, counts, file paths, line numbers, behaviors, states, version numbers, "X works" / "X returns Y" assertions, verification tables, status reports — i.e. ANY sentence whose truth depends on the current state of the code, data, or running processes | `skills/verify-never-infer` (MANDATORY; sub-agents must be told to load it explicitly — skills don't inherit) |
| Writing or editing any `.md` file | `skills/markdown-format` |
| Writing or editing CLAUDE.md / memory files, authoring or reviewing any skill SKILL.md, creating a knowledge doc, answering "where does X live?" questions, or suspecting a rule is duplicated across two surfaces | `skills/single-source-of-truth` |
| Fixing any bug, behavioral issue, or refactoring code | `skills/correct-fix-over-easy-fix` |
| Rolling out a shared pattern across multiple modules / entities / specs (migration, schema field, lint rule, logging convention, naming convention) — the "should we skip this where it's not strictly necessary?" decision | `skills/prefer-uniformity` |
| Sketching a solution that introduces a NEW service / background job / side-channel / in-memory cache; OR adding a component to handle a corner case the existing surface could absorb; OR designing a single-target special path | `skills/simplest-solution-first` (pair with `skills/prefer-uniformity` — minimum SCOPE + uniform APPLICATION) |
| Executing a multi-step scope (plan, todo list, phase rollout) and noticing a "more interesting / urgent" problem mid-execution — temptation to pivot or punt remaining items | `skills/finish-the-scope` |
| Waiting on any observable condition (log line, data change, process/deploy/CI state, another process's output file, an operator action) — temptation to ask the user to watch / click / "tell me when ready", or to write a `sleep`-and-poll chain | `skills/observe-never-await` |
| Importing/using ANY type, class, function, or field — verify it is not deprecated | `skills/respect-deprecation` |
| Locating a symbol ("where is X defined"), finding callers/callees ("who calls X"), assessing blast radius of a change, or hunting nested-loop/recursion hot paths | `skills/codebase-memory` (query the cbm MCP graph BEFORE blind grep, when available; pair with `skills/source-code-first` — cbm LOCATES, source is TRUTH) |
| Investigating logic, tracing execution, or exploring the codebase (search source before build output / installed packages) | `skills/source-code-first` |
| Relying on any load-bearing claim from a plan / architecture doc / audit / status matrix / README / memory (a file path, flow, contract, field, or "done / complete" status) during research OR implementation; implementing from a plan older than ~a day; or before replicating a pattern across multiple targets | `skills/verify-against-ground-truth` (pair with `skills/source-code-first` — WHERE to look vs WHETHER to trust the doc) |
| About to write or modify a script that loops over multiple repos/targets and mutates state, about to author an inline `Bash` loop over repos, or user asks to "automate this across N repos / projects / packages" | `skills/no-bulk-scripts` |
| Creating deferred/pending task documents; deferring any discovered work | `skills/pending-tasks` |
| Staging, committing, pushing, opening a PR, or merging | `skills/git-workflow` |
| After completing any substantial implementation task (feature, bugfix, refactor) | `skills/skill-compliance` |
| A `USAGE-LIMIT GATE` deny on an Agent dispatch; "we ran out of session limit" / pausing-resuming around the Claude 5h/7d usage windows; installing or reverting the guard on a machine | `skills/usage-limit-guard` |
| User asks to be grilled, stress-tested, or interviewed about a plan/design ("grill me", "poke holes in this", "stress-test this design") | `skills/grill-me` |
| User asks for ultra-compressed responses ("caveman mode", "talk like caveman", "use caveman", "less tokens", "be brief", `/caveman`) | `skills/caveman` |
