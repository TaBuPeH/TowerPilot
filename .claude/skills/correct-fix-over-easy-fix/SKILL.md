---
name: correct-fix-over-easy-fix
description: "MANDATORY: choose the architecturally correct fix over the easy fix. Prevents patching symptoms while leaving root causes. Triggers on every bug fix or refactor."
---

# Correct Fix Over Easy Fix

**MANDATORY: Before writing any fix, explicitly evaluate whether you are about to take the Easy path or the Correct path.** If you catch yourself gravitating toward the Easy fix, STOP and switch to the Correct fix. This is not optional.

---

## The Problem This Skill Solves

There is a recurring failure mode: when encountering a bug or architectural issue, the agent sees the root cause clearly but then implements a surface-level patch because it is smaller, faster, and "works." These patches:

- Compile and pass tests, creating a false sense of completion
- Leave the architectural flaw intact, causing the same class of bug to resurface elsewhere
- Accumulate as technical debt that compounds over time
- Erode trust when the "fixed" behavior glitches again days later

**The user's rule: ALWAYS go for the correct fix, even when it is bigger and architecturally challenging.**

---

## Decision Framework (apply to EVERY fix)

### Step 1: Identify the Root Cause Chain

Before writing a single line of code, trace the full causal chain:

```
Observable symptom → Intermediate cause → Root cause → Architectural gap
```

Write this chain down (in your reasoning or in the plan). If you cannot articulate the root cause, you are not ready to fix anything.

### Step 2: Generate Both Options

For every fix, explicitly name:

- **Easy fix**: What is the smallest change that makes the symptom go away?
- **Correct fix**: What change addresses the root cause or architectural gap?

### Step 3: Apply the Decision Rules

| Situation | Action |
|---|---|
| Easy fix and Correct fix are the same | Proceed — no conflict |
| Easy fix patches a symptom, Correct fix addresses root cause | **Always choose the Correct fix** |
| Correct fix is large but within the current task's scope | Implement it now, even if it takes longer |
| Correct fix is large AND outside the current task's scope | **Pause the current plan**, implement the Correct fix first (it is a prerequisite), then resume the original task |
| Correct fix requires cross-module changes or a plan | Create a plan, execute it immediately — do NOT defer it unless the user explicitly says to |
| Correct fix is genuinely out of scope (different feature area) | Create a full pending task document, but ONLY after confirming with the user that deferral is acceptable |

### Step 4: Announce Your Choice

When the fix is non-trivial, state explicitly in your response:

> **Fix assessment**: [1-2 sentences describing the easy fix vs correct fix and why you are choosing the correct one]

This makes the decision visible and auditable.

---

## Red Flags — Signs You Are Taking the Easy Path

Watch for these patterns in your own reasoning. If you catch any, STOP and re-evaluate:

1. **"Quick fix"** / **"Simple patch"** / **"Easy workaround"** — These phrases in your own thinking are a signal. Question whether you are papering over a symptom.

2. **Adding a fallback or default value to mask missing data** — If data should be present but isn't, the fix is ensuring the data flows correctly, not adding `or 0` / `?? defaultValue`.

3. **Patching a guard condition instead of fixing the data flow** — Example: widening `if user_id:` to `if user_id or public_id:` instead of fixing why `user_id` is missing in the first place.

4. **Adding a special case instead of fixing the general case** — Example: adding `if target == 'special': ...` instead of ensuring all targets flow through the same normalized pipeline.

5. **Bypassing an architectural layer** — Example: reading raw state directly in a consumer instead of going through the layer that already handles normalization.

6. **Hardcoding a retry/delay to work around a race condition** — The correct fix is eliminating the race condition, not hiding it with a `sleep(0.5)`.

7. **Fixing the consumer instead of the producer** — If a component emits bad data, fix the emission, not every consumer that reads it.

8. **Scope-reducing language** — "For now we can just...", "As a temporary measure...", "This is good enough for..." — these are almost always the Easy fix talking.

---

## Integration With Plans and Pending Tasks

### When the Correct fix is a prerequisite for the current task

1. **Pause the current plan** (mark the current step as blocked)
2. **Create a sub-plan** for the Correct fix
3. **Implement the Correct fix first**
4. **Verify it** (build / lint / test / live run)
5. **Resume the original plan** from where you left off

### When the Correct fix is genuinely out of scope

1. **Ask the user** — "The correct fix for [X] requires [Y]. Should I implement it now or defer?"
2. If deferred: create a **full pending task document** per the [pending-tasks](../pending-tasks/SKILL.md) skill
3. If the Easy fix is needed as a **temporary bridge**: implement it, but add a `# TODO(pending-task): [link to pending task]` comment at the patch site so it is tracked and never forgotten
4. **Never defer silently** — the user must know that a correct fix exists and was consciously postponed

---

## Checklist (Mental — Run Before Every Fix)

- [ ] Have I traced the full root cause chain?
- [ ] Have I identified both the Easy fix and the Correct fix?
- [ ] Am I choosing the Correct fix?
- [ ] If the Correct fix is larger: have I planned it or paused to implement it?
- [ ] Am I adding a fallback/default to mask missing data? (Red flag)
- [ ] Am I patching a guard instead of fixing the data flow? (Red flag)
- [ ] Am I bypassing an architectural layer? (Red flag)
- [ ] Would two parallel consumers of the same data still return different results after my fix? (Red flag)

---

## Anti-Patterns

| Wrong | Right | Why |
|---|---|---|
| Add `or 0` / `?? defaultValue` to mask missing data | Trace WHY the data is missing and fix the data flow | The fallback hides a broken pipeline; the next consumer hits the same gap |
| Widen a guard condition to tolerate a missing value | Fix WHY the value is missing upstream | The guard tolerates broken resolution; every other consumer hits the same bug |
| Add a special-case branch for one target | Fix the general pipeline so all targets flow through identical normalization | Special cases multiply; the next target with the same quirk needs another branch |
| Read state directly, bypassing the layer that normalizes it | Fix the layer; consumers must not skip architectural boundaries | Bypassing the layer means every other caller bypasses it too eventually |
| `sleep(0.5)` to "wait out" a race condition | Eliminate the race by serializing or using a proper synchronization primitive | The delay is brittle to load and hides the real concurrency bug |
| Fix the consumer when the producer emits bad data | Fix the producer; every consumer is wrong otherwise | Consumer-side patches multiply and rot independently |
| "Quick fix for now / temporary measure / good enough for now" framing | State the correct fix and either implement it or file a pending task with a `# TODO(pending-task): <link>` bridge marker | "Temporary" patches survive forever; explicit deferral with a tracked task is the only honest way to defer |
| Self-justify the easy fix in chat without surfacing the trade-off to the user | Announce: "Easy fix vs Correct fix — choosing Correct because [reason]" before writing the fix | Hidden trade-offs put the cost of catching them on the reviewer instead of the implementer |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Cannot determine which fix is "correct" | Root cause chain incomplete or ambiguous | Go deeper: trace one more level. If still ambiguous after 3 levels, ask the user |
| Correct fix requires changes outside your control | Cross-boundary dependency | Create a pending task with full context; implement the easy fix as a temporary bridge with a `# TODO(pending-task): [link]` comment |
| Correct fix will miss the deadline | Scope larger than expected | Discuss with user — never silently choose the easy fix. Document the trade-off explicitly |
| Applied the correct fix but tests broke elsewhere | Fix exposed latent bugs in consumers that relied on broken behavior | Fix the consumers too — this validates the correct fix was needed. The old tests were testing broken behavior |
| Pushback that the fix is "too large" | Perception of over-engineering | Point to the Red Flags list and the specific root cause chain. Show that the easy fix leaves the root cause intact and will recur |
| Easy fix already shipped | Previous session took the easy path | Create a pending task for the correct fix; reference the shipped easy fix as the workaround to remove |

---

## Verification

After implementing any fix, verify the decision was correct:

1. **Root cause addressed** — can you articulate how the fix prevents the entire class of bug, not just this instance?
2. **No red flags remaining** — re-run the Red Flags checklist above. If any flag is still true, the fix is incomplete
3. **Build / lint / test** — run the project's verification commands on the affected code
4. **Parallel consumer check** — if multiple consumers read the same data, verify they return identical results after the fix

---

## See Also

- [pending-tasks](../pending-tasks/SKILL.md) — create deferred work docs when the correct fix is genuinely out of scope
- [simplest-solution-first](../simplest-solution-first/SKILL.md) — minimum architecture that still fixes the root cause
- [finish-the-scope](../finish-the-scope/SKILL.md) — the correct fix does not license abandoning the current scope
