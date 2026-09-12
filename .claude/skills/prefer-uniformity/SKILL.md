---
name: prefer-uniformity
description: "Apply patterns uniformly across modules, entities, and specs — never selectively skip a pattern as an optimization unless explicitly instructed. Triggers when rolling out a shared pattern (migration, schema field, logging convention, lint rule, naming convention) across multiple targets and the question 'should we skip this where it's not strictly needed?' comes up."
---

# Prefer Uniformity Over Optimization

**MANDATORY: When applying a shared pattern across a fleet of modules or entities, apply it uniformly. Do NOT selectively skip the pattern on a subset "because that target doesn't strictly need it." Selective optimization is only valid when the user explicitly instructs it.**

---

## The Rule

For every cross-cutting pattern — migration, schema field, logging convention, lint rule, audit hook, naming convention, config shape — choose the **uniform** application:

- Apply to every module / entity / spec in the affected scope
- Use the same template, same migration, same handler shape, same emit point
- Even when a subset of the targets would "work fine without it"

**Override condition:** only when the user explicitly says "optimize this — apply only where strictly necessary" (or equivalent — see § Override Condition). Default is always uniform.

---

## Why Uniformity Wins by Default

| Dimension | Uniform | Selective (optimized) |
|---|---|---|
| Audit / verification | One static check answers "is the pattern applied?" across the whole fleet — binary yes/no per target. | Audit must encode "this target needs it, this one doesn't" — a moving target. |
| Cognitive surface | One template to learn + maintain. | N decisions to remember + re-explain to every new contributor. |
| Migration / rollout | One pass over the fleet; success criterion is binary completion. | Per-target judgment call, harder to script, harder to verify. |
| Onboarding | New module inherits the pattern by template. | New module requires "does this one need X?" judgment from the author. |
| Drift detection | Lint / audit can ban deviations. | "Selective" deviations are by design — drift detection becomes ambiguous. |
| Pattern evolution | Single template can be evolved fleet-wide. | Per-target customizations resist global evolution. |
| Refactor cost when criterion changes | Zero — pattern is already everywhere. | Retrofit pass across previously-skipped targets. |

The faster solution (skip the pattern where it's "not strictly needed") is **locally optimal but globally expensive** — every saved hour at install time costs hours per year in audit, drift, onboarding, and retrofit.

---

## Decision Framework

When you encounter a choice between "apply pattern X uniformly" vs "apply pattern X only where strictly necessary":

### Step 1 — Confirm the pattern is CROSS-CUTTING

This skill applies to patterns that span multiple modules / entities / specs. It does NOT apply to:
- Single-file refactors
- One-off bug fixes
- Per-feature implementation decisions where the pattern is the feature

### Step 2 — Default to uniform

Write down the scope explicitly: "applying X to every target in {scope}."

### Step 3 — Check for explicit override

Did the user explicitly say one of: "optimize", "apply only where strictly necessary", "skip the ones that don't need it", "selective rollout", "only apply to {explicit subset}"? If **yes** → proceed selectively, document the optimization criterion in the plan / migration script. If **no** → STOP and apply uniformly.

### Step 4 — Document the uniform decision

In the plan / migration script: state the rule as "this pattern applies to every {module|entity|spec} in {scope}, no exceptions." This becomes the audit criterion.

---

## Anti-Patterns

| Wrong | Why it fails | Right |
|---|---|---|
| "This target doesn't need the extra field, so skip the migration there." | Future-self forgets the criterion when the target's needs change; the audit script grows conditionals. | Apply the template uniformly. |
| "This module is internal, no external-facing ID needed." | The first time it crosses a boundary, retrofit is painful and the leakage is hard to grep. | Uniform convention everywhere. |
| "This component is small — skip the standard checks." | Small components grow; one missed guard corrupts state quietly. | Every component follows the pattern. |
| "This README is for a sub-folder — skip the standard metadata block." | Formatting fragments; future maintainers can't grep for a uniform shape. | Same metadata block convention everywhere. |
| Refactoring a fleet by "starting with the targets that need it most" without committing to uniform completion. | Partial rollout creates a tri-state (rolled-out / pending / not-needed) that's audit-hostile. | Plan the uniform rollout up-front; track binary "target X done yes/no". |
| "Most call sites use the simple primitive; we'll selectively apply the robust one only where it matters." | The audit rule has to grow conditions for which sites require which primitive. | Either apply the robust primitive uniformly OR drop it entirely — an explicit all-or-nothing choice. |

---

## Override Condition (the only valid exception)

The user **explicitly** says one of:
- "Optimize this — apply only where strictly necessary"
- "Skip the ones that don't need it"
- "Selective rollout"
- "Only apply to {explicit subset}"
- "Don't apply this pattern uniformly — {reason}"

When this happens:

1. **Confirm the optimization criterion in writing** — in the plan / migration script, state which targets get the pattern and which don't, and the rule that decides.
2. **Document the rule as auditable** — a script or grep can determine "should target X have the pattern?" without human judgment.
3. **Note the decision in the plan's Risks** — selective application is a deviation from default and the risk surface (retrofit when criterion changes) should be acknowledged.

A user pivot to optimization is **case-specific**. Future similar work defaults back to uniform unless the user re-affirms.

---

## Verification

When proposing or reviewing a cross-cutting pattern rollout:

1. **State the scope explicitly** — "this applies to every X in {scope: Y}".
2. **Confirm no targets are skipped** unless the override condition was met.
3. **Encode the audit criterion** as a static check where feasible (single script per pattern).
4. **Track binary completion** per target, not partial / "needed vs not".

---

## See Also

- [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md) — vertical depth (fix root cause, not symptom). This skill is horizontal breadth (apply uniformly, not selectively). Both are mandatory; they cover different axes.
- [`single-source-of-truth`](../single-source-of-truth/SKILL.md) — uniform pattern → one canonical template.
- [`simplest-solution-first`](../simplest-solution-first/SKILL.md) — minimum SCOPE + uniform APPLICATION.
- [`finish-the-scope`](../finish-the-scope/SKILL.md) — a uniform rollout is a scope; finish it.
