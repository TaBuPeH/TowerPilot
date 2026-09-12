---
name: simplest-solution-first
description: "Always design the minimum architecture that satisfies the request. Fewer components, fewer hops, fewer custom mechanisms. Triggers when the first sketch of a solution involves a NEW service / NEW background job / NEW side-channel / NEW gap-detector / NEW in-memory cache, OR when the design adds a component to handle a corner case that the existing surface could absorb."
---

# Simplest Solution First — Minimum Architecture That Satisfies the Request

**MANDATORY: Design the minimum architecture that satisfies the request. Add components only when removing one breaks an explicit requirement or leaves the root cause unresolved. Minimum is NOT the easy fix — if the smaller design patches a symptom, load [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md). Pair with [`prefer-uniformity`](../prefer-uniformity/SKILL.md) for uniform application; that skill owns the uniform-rollout doctrine.**

---

## Rules

1. **Fewest moving parts.** For every new component in a sketch (module, background job, cron, side-channel, env var, config flag), ask "what if I remove this — does the existing surface absorb it?" If yes, remove it.
2. **No hypothetical components.** Don't build for unbuilt future requirements. "We might need X later" is not an override.
3. **No single-target exceptions.** Recast every special-cased flow as a uniform contract (real impl on one target, no-op default on others). Hand rollout to [`prefer-uniformity`](../prefer-uniformity/SKILL.md).
4. **Minimum must still fix the root cause.** If the smaller design only routes around the problem, load [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md) and re-design from the root.

---

## Procedure

**Write → Remove → Document.**

1. **Write** the architecture as one sentence with no proper nouns for new components. If you can't, you don't understand the requirement yet.
2. **Remove** each component in your sketch by asking the Rule 1 question. Loop until nothing more removes.
3. **Document** every removed component as an accepted trade-off in the plan's Risks section. Conscious choice + visible trace > forgotten omission.

---

## Override Conditions (the only valid exceptions)

A component the rule would remove gets to stay ONLY when ONE of these holds AND is cited in the design doc:

1. **Explicit functional requirement** that no existing surface can absorb. Cite the requirement.
2. **Data-loss / irreversibility / safety risk** would result from removal — NOT performance, NOT latency, NOT observability alone (those are addressed via metrics/logging on the existing surface).
3. **User explicitly asks** for the redundancy. Case-specific; future similar work defaults back to minimum unless re-affirmed.

---

## Anti-Patterns

| Wrong | Right |
|---|---|
| "We need a safety-net job in case the main path silently breaks." | Drop the job; accept "no event = no recovery"; mitigate via observability on the existing surface. |
| "We need a new module/service to own this responsibility." | Put it in the existing component that already sees the data. |
| "We need a new channel/queue to signal this." | Call directly or piggyback the existing event stream. |
| "Target X needs a special path because it's different." | Uniform contract — X gets the real impl, others no-op. See [`prefer-uniformity`](../prefer-uniformity/SKILL.md). |
| "Let's keep the old path as a fallback in case the new one fails." | Single path; failure logs a warning and degrades. Dual paths defeat the architectural goal. |
| "We should plan for {hypothetical future requirement}." | Build for the actual requirement; refactor when the real one shows up. |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Design keeps growing new components after the simplification pass | The requirement is framed as "detect + orchestrate" instead of using an existing trigger surface as the orchestrator. Re-write the one-sentence description and rerun the removal pass. |
| "Simplest" solution feels like a patch / workaround | Rule 4 violation — load [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md) and re-design from root. |
| A single target appears to need a special path | Rule 3 violation — recast as uniform contract; hand to [`prefer-uniformity`](../prefer-uniformity/SKILL.md). |
| User pushes back with "but we need X" after you removed it | Ask which Override Condition applies and request the citation. Document the resolution in the plan. |

---

## Verification

1. Can you write the architecture as one sentence with no new component names? If yes, you're at minimum.
2. For each NEW module / job / channel / map / env var, write the "what if removed?" answer + the Override Condition citation (or remove it).
3. Every removed component's trade-off is documented in the plan's Risks.

---

## See Also

- [`prefer-uniformity`](../prefer-uniformity/SKILL.md) — owns uniform-rollout doctrine (Rule 3).
- [`correct-fix-over-easy-fix`](../correct-fix-over-easy-fix/SKILL.md) — owns root-cause discipline (Rule 4).
- [`single-source-of-truth`](../single-source-of-truth/SKILL.md) — fewer SSOTs = fewer components.
- [`finish-the-scope`](../finish-the-scope/SKILL.md) — don't re-grow what you just shrank.
