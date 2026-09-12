---
name: skill-compliance
description: Evaluate modified implementation files against applicable skill conventions and produce compliance reports after implementation tasks.
---

# Skill Compliance Evaluation

**After a significant implementation task, spawn an independent agent to evaluate all modified source files against the applicable `.claude/skills/` conventions. Never self-evaluate — the compliance agent must have zero implementation context.**

## Rules

1. **Independent agent only** — spawn a general-purpose agent with a fresh context. Never evaluate code you just wrote.
2. **Dynamic category discovery** — read the skills trigger map (or the skills directory) to determine which skills apply to the modified files. New skills must be included.
3. **Read before rating** — the agent must open and read every modified source file. Never assign PASS without inspecting the actual code.
4. **Scope to modified files** — pre-existing issues are noted as "pre-existing" but excluded from the fix-priority counts.
5. **Every violation needs a file path** — WARN/FAIL ratings must cite `file:line` with a concrete fix snippet. Abstract descriptions are rejected.
6. **Run AFTER implementation verification** — compliance evaluates the final code state after build/lint/test pass and any audit deviations are resolved.
7. **Finalize only when resolved** — every HIGH-priority item must be fixed or explicitly accepted before the report is considered done.
8. **Violation checkboxes** — every violation is a `- [ ]` checkbox in the report. Mark `- [x]` when fixed. Never delete items.

## When This Skill Applies

**Trigger after any substantial implementation task** touching source files. Do NOT trigger for docs-only, config-only, or skill/plan edits.

## How to Execute

1. Collect the list of modified files (e.g. `git diff --name-only <base>..HEAD`, or the session's edit log).
2. Determine the applicable skills: match each modified file's domain against the skills directory / trigger map.
3. Spawn ONE independent agent with:
   - The modified file list
   - The list of applicable skills to load and evaluate against
   - The instruction to read every file, rate each applicable skill category PASS / WARN / FAIL, and cite `file:line` + a fix snippet for every WARN/FAIL
4. Write the findings as a checklist report (one `- [ ]` per violation, grouped by severity HIGH / MEDIUM / LOW).
5. Fix or explicitly accept every HIGH and MEDIUM item; tick the boxes as they resolve.

## Orchestration: Execution Order

```
1. IMPLEMENT → 2. BUILD+LINT+TEST → 3. VERIFY BEHAVIOR → 4. SKILL-COMPLIANCE → 5. PROCESS VIOLATIONS
```

Compliance evaluates the final state after verification fixes land.

## Anti-Patterns

| Wrong | Right | Why |
|---|---|---|
| Self-evaluating code you just wrote | Independent agent with zero implementation context | Self-grading bias rates own work PASS |
| PASS without reading the file | Read every modified file before rating | A rating without inspection is inference |
| "Violates naming conventions" with no location | `file:line` + concrete fix snippet | Abstract findings can't be actioned or verified |
| Counting pre-existing issues as new violations | Mark them "pre-existing", exclude from priority counts | Conflating them inflates the report and misassigns blame |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Compliance agent reports zero findings on a large diff | Agent didn't load the applicable skills or didn't read the files | Re-spawn with explicit skill list and read-before-rating instruction |
| Findings cite stale line numbers | Report written before latest edits | Re-run the scan on the final state |
| Report never reaches done | HIGH items neither fixed nor explicitly accepted | Walk every HIGH item to a terminal state (fixed / accepted with reason) |

## Verification

1. Every modified source file appears in the report with a rating.
2. Every WARN/FAIL carries `file:line` + fix snippet.
3. Every HIGH/MEDIUM item is `[x]` fixed or explicitly accepted.

## See Also

- [skill-writing-guide](../skill-writing-guide/SKILL.md) — the rubric for the skills themselves
- [verify-never-infer](../verify-never-infer/SKILL.md) — ratings without inspection are inference
- [prefer-uniformity](../prefer-uniformity/SKILL.md) — uniform pattern application is what makes compliance checks feasible
