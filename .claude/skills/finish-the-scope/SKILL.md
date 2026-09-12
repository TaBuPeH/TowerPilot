---
name: finish-the-scope
description: "Always finish the current scope before pivoting. New discoveries mid-execution become pending tasks — never reasons to abandon the work in progress. Triggers when the agent notices a more interesting / more urgent / harder problem mid-task and is tempted to suggest punting the remaining scope. Also triggers when an agent finds itself authoring an option list that includes 'skip the rest of the current plan and do this instead'."
---

# Finish the Scope

**MANDATORY: When you start a defined scope (plan, multi-step task list, todo set), finish it. New discoveries that surface during execution become pending tasks. They do NOT become reasons to pivot. Mid-scope pivots dilute work, create stale plans, and fragment audit trails.**

---

## The Rule

For every defined scope you've started:

1. **Finish what you started.** Every item in the scope reaches a terminal state — done, explicitly deferred to a pending task, OR explicitly rejected by the user. No item ends in "we'll come back to this."
2. **New discoveries → pending task.** When you notice a problem outside the current scope (more interesting, more urgent, more architectural — doesn't matter), immediately capture it as a pending task per [pending-tasks](../pending-tasks/SKILL.md). Then return to the current scope.
3. **Never propose pivots that abandon scope.** Do NOT author option lists that include "stop the current work and do X instead." That decision is the user's, not the agent's. If the new discovery is genuinely emergency-blocking, surface it as information; let the user decide whether to pivot or defer it.

---

## The Problem This Skill Solves

There is a recurring failure mode: an agent commits to a multi-step plan, starts executing it, discovers another problem along the way, and **suggests punting the remaining scope to a pending task so it can chase the new problem**. The reasoning sounds principled ("the new problem is the actual blocker"), but the effect is:

- **Stale plans pile up** — the original scope ends in a half-done state nobody returns to
- **Audit trails fragment** — Plan A is 3 of 6 items done; the rest "got captured as pending tasks"; later nobody remembers what Plan A's "done" criterion was
- **Work gets diluted** — the new "more important" problem now competes with everything else in the pending queue, and the original problem is now lower priority because it was "almost done"
- **Trust erodes** — the user picks a path; the agent half-commits and then negotiates a different one

The fix: agents finish their scope. New scope is for the user to define.

---

## Decision Framework

When you're executing a defined scope and notice another problem, run this checklist:

### Step 1 — Identify the trigger pattern

Are you about to:
- Author an option list that includes "stop the current work and X"?
- Propose punting remaining todos to a pending task before they're done?
- Write "but the new problem is the real blocker, recommended pivot…"?
- Suggest "fork now: A first, B as follow-up"?
- Use language like "honest read: the new thing matters more than the rest of the plan"?

If yes → STOP. You're about to violate this skill.

### Step 2 — Classify the new discovery

| New discovery type | Action |
|---|---|
| In-scope (the current plan explicitly covers it) | Continue the current scope; the discovery is part of the work |
| Adjacent but out-of-scope (related but separate concern) | Capture as a pending task IMMEDIATELY (don't lose it); return to current scope |
| Blocking the current scope (current work cannot complete without resolving the discovery first) | Address the discovery as the minimum needed to unblock; capture the deeper work as a pending task; return to scope |
| Emergency-blocking everything (data loss in progress, irreversible damage) | Surface to user with information; let user decide pivot. **Do NOT pivot autonomously.** |

### Step 3 — Continue the current scope

If the discovery is anything other than emergency-blocking, return to the next item in the current scope. The discovery is captured and won't be lost. The current scope reaches its terminal state.

### Step 4 — Surface completion + the captured discoveries

Once the scope is fully done (every item in terminal state), report:
- What was completed
- What pending tasks were captured along the way
- Any genuine open questions for the user

This is the right place for "problem X surfaced and is captured as a pending task — want to drill it now?" — not in the middle of execution as a reason to pivot.

---

## Worked Examples

### Example 1 — Refactor surfaces a deeper architectural issue

**Setup:** Plan: migrate 8 modules from pattern-A to pattern-B. Module 3 of 8 reveals a deeper issue — the shared core itself has a bug.

**Wrong:** Pause the migration after module 3, file a pending task for modules 4-8, drill the core bug now.

**Right:** If the bug doesn't BLOCK modules 4-8 → capture the core bug as a pending task, finish migrating 4-8, then surface for user direction. If the bug DOES block 4-8 → fix the minimum needed in the core to unblock, capture the deeper fix as a pending task, continue migration.

### Example 2 — Bug-fix branches into infrastructure work

**Setup:** Plan: fix bug X in module Y. Mid-fix, agent discovers module Y's whole logging pattern is non-uniform across the codebase.

**Wrong:** "The bug fix is small but the logging non-uniformity is the real story. Recommend we pivot to a full logging audit instead."

**Right:** Fix bug X. Capture "logging non-uniformity audit" as a pending task. Report bug X fixed + pending task captured.

---

## Anti-Patterns

| Wrong | Why it fails | Right |
|---|---|---|
| "The new problem is the actual blocker — recommend we drill that instead of finishing the current work." | Re-scopes the user's chosen plan mid-execution. | Finish the current work; capture the discovery as a pending task; surface at end. |
| Option list with "skip remaining items in current scope" as a choice. | Treats the user's commitment as renegotiable. | Option list should be variants of HOW to finish the current scope, not whether to. |
| "Honest read: the new thing matters more than the rest of the plan." | Substitutes agent's priority judgment for the user's. | Surface the new thing as a pending-task candidate; let user re-prioritize next turn. |
| Half-finishing 4 of 6 todos and saying "remaining items captured as follow-up." | Stale plan with no clear terminal state. The "follow-up" rarely happens. | Drive every todo to a terminal state — done, explicitly deferred by user, OR explicitly rejected by user. |
| "We discovered X mid-execution; here's a 3-option list including 'punt remaining work'." | The 3-option list itself violates the rule — only the user introduces re-scoping options. | Capture X as a pending task; ask the user only if there's a genuine emergency. |
| Mid-scope pending-task capture WITHOUT returning to scope after. | Loses the original scope under the new task. | After capturing, ALWAYS return to the current scope's next item. |

---

## Override Condition (the only valid pivot)

The user **explicitly** says one of:
- "Stop the current plan, drill X instead"
- "Punt the rest to a pending task and do Y"
- "Forget the plan, do Z"
- "Pivot to {new thing}"

When this happens:
1. Confirm: "Stopping the plan mid-execution. Items remaining: …. Capturing as pending task '…'. Moving to {new thing}. Confirm?"
2. Get explicit yes.
3. Execute the pivot.

Without that explicit instruction, **the agent finishes what was started**.

---

## Verification

When mid-scope (3 of 6 todos done; plan in progress):

1. **Check: did you propose a pivot in your last response?** If yes, retract it. Continue the scope.
2. **Check: did you author an option list with "skip remaining scope" as a choice?** If yes, that option list violated the rule. Re-author without that choice.
3. **Check: did you capture a discovery and then KEEP working on the discovery?** That's a hidden pivot. Stop the discovery work; return to the next scope item.
4. **Check: does every item in the original todo list / scope reach a terminal state?** If you can answer "yes" with confidence at the end of the scope, you complied with this skill.

---

## See Also

- [`prefer-uniformity`](../prefer-uniformity/SKILL.md) — horizontal application discipline. This skill is temporal completion discipline. Both protect against half-done work, on different axes.
- [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md) — vertical depth discipline (fix root cause). Three orthogonal axes of work quality.
- [`pending-tasks`](../pending-tasks/SKILL.md) — the canonical capture path for discoveries that must be deferred. Mid-scope discoveries go HERE, not into pivots.
