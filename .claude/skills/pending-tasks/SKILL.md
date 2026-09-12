---
name: pending-tasks
description: Create high-quality deferred work documents with deep context so a fresh session can execute without re-investigating. Trigger when deferring work.
---

# Pending Tasks — Deferred Work Tracking Skill

**When deferred work is identified during ANY task, capture it as a pending-task document immediately — do not ask, do not lose it. But default to folding small work inline: filing is the carve-out, not the norm.**

Store pending tasks as local markdown files in a dedicated directory (e.g. `docs/pending-tasks/` or the project's equivalent), named `[YYYY-MM-DD][Status] {Title}.task.md`.

---

## The File-Decision Rubric (MANDATORY)

**Default: FOLD INLINE.** Filing is the carve-out.

**Disambiguation test:** *Could shipping this inline produce bad / buggy code?* No → FOLD. Yes → match a MUST-FILE carve-out. *"I don't have time" / "the change is big" / "someone else would do this"* are NOT yeses.

### MUST FILE — carve-outs

1. **Architectural changes** — mutating contracts, cross-module edges, behavior-by-data routing.
2. **Information loss / irreversibility** — data drops, irreversible migrations, schema narrowing.
3. **Substantial standalone work** — a full plan is more appropriate than an inline fix.
4. **Genuine cross-boundary work** — the session LITERALLY cannot execute it.

### CAN BE FOLDED — default lane

1. Pattern rollout after a pilot
2. Lint / build errors blocking your work
3. Same-change refactors of code you just touched
4. Verification work for your own plan
5. Your own bugs in your own tests
6. Observability gaps for things you're building
7. Convention wiring deferred to land later
8. Cross-module wiring surfaced during YOUR feature testing
9. Stale paths / drift adjacent to your edit

**Runtime degradation during live verification** — investigate first, never reflexively "out of scope".

---

## Task Lifecycle

`Open → In Progress → Done | Won't Fix`. Rename the file on status change (never delete + recreate). Update the `> **Status:**` header field to match.

---

## Document Format

Blockquote metadata header (per [markdown-format](../markdown-format/SKILL.md)) — `# H1` title, then `> **Status:** / **Created:** / **Priority:** / **Tags:**` fields.

Required sections:

- **Problem** (always) — what is wrong / missing, with evidence (`file:line`, logs, run output)
- **What Needs to Happen** (always) — concrete steps, not aspirations
- **Key Files** (always) — every file a fresh session must touch or read, with why
- **Context** (always) — everything discovered during the current session that the resume session would otherwise re-investigate
- **Success Criteria** (priority ≥ medium) — how to verify it's done
- **Architectural Reasoning** (priority ≥ high or cross-module) — why this approach, alternatives rejected

## Quality Bar

**A fresh developer with zero prior context can execute without re-investigating.** If the resume session would need to redo your investigation, the capture is too shallow — paste the findings, paths, and evidence in now, while they're cheap.

## Anti-Patterns

| Wrong | Right | Why |
|---|---|---|
| Filing a pending task for a lint error blocking your own work | Fix it inline | Default lane is FOLD; filing defers trivially-inline work |
| One-line pending task ("fix the flaky detector") | Full Problem / Steps / Key Files / Context sections | Shallow captures force full re-investigation |
| Deleting and recreating the file on status change | Rename the same file | Preserves history and identity |
| Capturing the task but continuing to work on it anyway | Capture, then return to the current scope | Hidden pivot — see [finish-the-scope](../finish-the-scope/SKILL.md) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Resume session re-investigates from scratch | Context section too shallow | Paste actual findings (paths, evidence, decisions) at capture time |
| Duplicate tasks accumulate | No duplicate search before creating | Search existing task files first; >80% overlap → update the existing one |
| Tasks rot as Open forever | No review pass | Surface open tasks when relevant work is touched; close or Won't-Fix stale ones |

## Verification

1. Rubric applied — this genuinely matches a MUST-FILE carve-out (not a fold candidate).
2. All required sections present at depth.
3. Filename and `Status` header agree.

## See Also

- [finish-the-scope](../finish-the-scope/SKILL.md) — mid-scope discoveries go here, not into pivots
- [correct-fix-over-easy-fix](../correct-fix-over-easy-fix/SKILL.md) — deferred correct fixes need a tracked task
- [markdown-format](../markdown-format/SKILL.md) — document format conventions
