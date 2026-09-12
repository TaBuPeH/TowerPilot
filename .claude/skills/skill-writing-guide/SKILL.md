---
name: skill-writing-guide
description: >-
  DO NOT auto-load. Opinionated rubric for writing and evaluating Claude
  skills — structure, frontmatter, scoring, failure modes. Use ONLY when the
  user explicitly asks to create, audit, or refactor a skill — never trigger
  from filename, code pattern, or task type, even when editing other SKILL.md
  files.
---

# Skill Writing Guide — Best Practices

> [!warning]
> **DO NOT auto-load this skill.** It is NOT part of any trigger map. Only load it when the user explicitly asks to create, evaluate, or improve a skill using best practices. Never apply it automatically when generating skills as part of other tasks.

**When this skill is explicitly loaded, use it to write new skills that maximize Claude's compliance, or to audit existing skills against the quality criteria below.**

---

## Skill Quality Criteria (scoring)

Every skill is evaluated on these criteria, scored 0-2 each:

| # | Criterion           | 0 = Missing                        | 1 = Partial                              | 2 = Good                                              |
| - | ------------------- | ---------------------------------- | ---------------------------------------- | ------------------------------------------------------ |
| 1 | **Frontmatter**     | No YAML block                      | Has `name` but vague `description`       | `name` + trigger-specific `description`                |
| 2 | **Description**     | Vague ("helps with backend")       | Names the domain but not triggers        | Names exact triggers ("when creating an entity...")     |
| 3 | **Prime Directive** | Descriptive opening                | Imperative but buried or weak            | Bold imperative as first content after title            |
| 4 | **Rules Placement** | Critical rules buried past line 100 | Some rules at top, some buried          | All critical rules in first 30 lines                   |
| 5 | **Code Examples**   | None or fragments only             | Has examples but missing imports/context | Complete, runnable, language-tagged, project-specific |
| 6 | **Anti-Patterns**   | None                               | Mentions what not to do inline           | Dedicated section with wrong/right/why format          |
| 7 | **Troubleshooting** | None                               | Inline error mentions                    | Symptom/cause/fix table                                |
| 8 | **Verification**    | None                               | Mentions "run lint" in passing           | Explicit checklist or commands with expected output     |
| 9 | **Cross-Links**     | None                               | 1-2 links, not comprehensive             | "See also" with all related skills listed              |
| 10 | **Token Efficiency** | Repeats framework docs / other skills | Some bloat but mostly useful          | Every line carries project-specific information         |
| 11 | **Instruction-vs-Example Separation** | Examples / templates / reference tables sit inline in SKILL.md and outweigh the imperative rules | Some examples extracted to sidecar, others still inline | Every illustrative example, full template, agent prompt, ASCII diagram, or non-decision reference table lives in a sidecar file (`reference.md`, `examples.md`, etc.); SKILL.md links out with precise wording and keeps only directly-actionable instructions (May/Must/Always/Never/Should); amendments/decision logs live in `history.md` with rule text merged in place (Rule 10) — no inline `Decision`/`Amendment`/`Change History` sections |

**Grade scale:** A (20-22) | B (18-19) | C+ (16-17) | C (14-15) | D (12-13) | F (<12)

**Minimum score floors (mandatory for grade A):**
- **Anti-Patterns** must score **2** (dedicated section with wrong/right/why)
- **Troubleshooting** must score **2** (symptom/cause/fix table)
- **Verification** must score **2** (explicit checklist or commands)
- **Instruction-vs-Example Separation** must score **2** (no inline examples, templates, agent prompts, ASCII diagrams, pure-reference tables, or `Decision`/`Amendment`/`Change History` sections in SKILL.md — they belong in sidecar files; Rule 10)
- No criterion may score **0**

A skill scoring 20+ total but failing any floor is capped at **B** until the floor is met.

---

## The 10 Rules of Effective Skills

### 1. Instruction first, explanation after

Put the actionable rule at the top. Follow with reasoning. Never bury the instruction after paragraphs of context.

### 2. One complete example beats ten paragraphs

Include at least one complete, runnable code example per major pattern — with imports and registration/wiring. Fragments cause me to guess from training data, which won't match your project conventions.

### 3. Show wrong alongside right

Anti-patterns with specific failure messages create strong negative anchors. I actively avoid patterns I've seen labeled as wrong.

### 4. Be absolute, not hedging

"Always" and "never" produce consistent behavior. "Consider" and "generally" produce inconsistent behavior. If a rule is conditional, state the exact condition — not "when appropriate."

### 5. Respect the context window

Every token in a skill competes with the actual task for attention. Omit standard framework documentation (I know it from training). Include only the **delta** — what's different about your project.

### 6. Cover the edge cases

The happy path is where I need skills least. The 20% of scenarios where things don't fit neatly are where I produce the most wrong output. Decision tables handle this efficiently.

### 7. Include verification criteria

Without explicit verification (build commands, lint checks, expected output), I report tasks as done based on my own judgment, which may miss project-specific requirements.

### 8. Cross-link, don't duplicate

If another skill covers a topic, reference it with a link instead of repeating the content. Duplication wastes tokens and creates maintenance burden when one copy gets updated but the other doesn't.

### 9. Instruction-vs-Example separation (the rule of thumb)

For every block of content you are about to add to a SKILL.md, ask: **"Is this a direct instruction on what to do, or is it an example / reference?"**

- **Direct instruction** carries an imperative verb with prioritization (`Must`, `Always`, `Never`, `Should`, `May`, `Required`, `Forbidden`). It tells the agent what to do or refuse to do. → **STAYS IN SKILL.md.**
- **Example, reference table, agent prompt, ASCII diagram, full template, illustrative scenario, "real-world incident" anecdote, sample report body, decision-tree drawing** → **MOVES to a sidecar file** (`reference.md`, `examples.md`, `history.md`, …) and is referenced from SKILL.md by a one-line pointer with precise wording (e.g., `Full template at [reference.md → Template](reference.md#template)`).

The rule cuts SKILL.md weight without losing information — illustrations are loaded on demand, not on every session start. Mandatory sections that are themselves directives (Rules, Anti-Patterns table, Troubleshooting table, Verification checklist, Frontmatter, Prime Directive) STAY regardless — they ARE the instructions.

**Borderline cases.** A reference table is instructional when it encodes a decision rule ("if tier=X, do Y") and reference-only when it merely catalogues facts. When in doubt, keep decision tables in SKILL.md and move pure catalogues to sidecar.

**Hot-path skills** (loaded almost every session) get the most savings from this rule because the SKILL.md is read repeatedly. Apply it most aggressively to those.

### 10. Amendments merge in place — SKILL.md is current-state-only

When a new decision changes a rule, edit the affected rule text **in place** so SKILL.md always states the current contract, and append the decision's story (date, what changed, why) as a row in the skill's `history.md` sidecar. SKILL.md must NEVER accrete `Decision NN` / `Amendment AN` / `Change History`-titled sections — a reader loading the skill needs the current rules, not the archaeology of how they got there.

**Size ceilings:** keep hot-path SKILL.md files under ~14KB; all others under ~22KB. Content that would exceed the ceiling moves to a sidecar per Rule 9.

**Sidecar pointers are imperative triggers, never passive see-alsos.** Write `BEFORE spawning the audit sub-agent, Read [reference.md → Prompt Template](reference.md#prompt-template)` — the pointer states *when* to load the sidecar and *what* is there, so the reader loads it exactly when the section is worked on.

---

## Canonical SKILL.md Structure (mandatory)

**Every skill MUST contain these sections in this exact order.** This is not advisory — agents creating or editing skills must produce this structure. Omitting a section requires explicit justification.

```text
1. YAML frontmatter (name + trigger-specific description)
2. H1 title
3. Prime directive (bold, imperative, 1-3 sentences)
4. Rules (numbered, bold lead-ins, most critical first, within first 30 lines)
5. Core content (procedures, code examples, decision tables)
6. Anti-patterns (dedicated section: wrong approach / why it fails / correct approach)
7. Troubleshooting (symptom/cause/fix table, minimum 3 rows)
8. Verification (explicit commands with expected output)
9. See also (cross-links to ALL related skills)
```

This ordering maps to how I process content — top items get the strongest attention weight.

**Hub skills** (skills that route to sub-files) still follow this structure but may keep Core Content short with links to sub-files. The hub itself must still have its own Anti-Patterns, Troubleshooting, and Verification sections — even if brief.

**Sidecar files (recommended for any skill with templates, diagrams, or long examples).** Apply Rule 9 (Instruction-vs-Example separation):

- `reference.md` — full templates, agent prompt blocks, ASCII diagrams, decision-tree drawings, expanded procedures, "real-world incident" anecdotes, edge-case catalogues. SKILL.md links into named anchors here.
- `history.md` — Change History table (append-only). SKILL.md ends with a one-line `See [history.md](history.md)` pointer instead of inlining the table.
- `examples.md` / `<topic>-spec.md` — domain-specific full samples. One file per artefact type.

Sidecar files load on demand when SKILL.md links into them — they cost zero tokens on sessions that don't need them.

---

## Frontmatter Template

```yaml
---
name: kebab-case-skill-name
description: >-
  One to three sentences. Must answer: (1) what domain does this cover,
  (2) what specific actions trigger this skill. Be precise about triggers —
  vague descriptions ("helps with backend") never match.
---
```

**Trigger-clause forms.** Both singular (`Trigger when` / `Trigger on` / `Use when` / `USER-INVOKED` / `DO NOT auto-load`) and plural (`Triggers when` / `Triggers on`) are accepted. Pick whichever reads more naturally — singular is fine for one condition; plural is natural English when the description lists multiple.

---

## Detailed Reference Topics

The following topics are covered in depth in [reference.md](reference.md) — read it when writing or auditing skills:

- **Content Type ROI** — token cost vs. quality impact for each content type (code examples, anti-patterns, decision tables, prose, etc.)
- **Skill Category Priorities** — what to emphasize for convention, infrastructure, workflow, decision framework, and reference skills
- **Splitting Guidelines** — when to keep a single file vs. split into sub-files (threshold: ~600-800 lines)
- **Common Failure Modes** — 7 root causes when skills don't produce intended behavior, with symptoms and fixes
- **Token Economics** — detailed cost-benefit analysis of every content type
- **Writing Style** — imperative voice, absolute vs. conditional rules, naming, quantification

---

## Example: Ideal SKILL.md

BEFORE writing a new skill from scratch, Read the complete model skill (a full example SKILL.md demonstrating every required section at the right depth) at [reference.md → Example: Ideal SKILL.md](reference.md#example-ideal-skillmd).

---

## Edit vs Judge Separation (mandatory for batch work)

**The agent that edits a skill must NOT be the agent that scores it.** When upgrading multiple skills:

1. **Implementation agent** reads the current SKILL.md and edits the skill
2. **Reviewer agent** (separate, fresh context) re-scores the edited skill against this rubric
3. If the reviewer scores below A or any floor criterion fails, the implementation agent revises

This prevents self-grading bias. A single developer editing one skill can self-review, but batch operations across 10+ skills require the split.

---

## The Quality Test (pre-commit checklist)

Before considering a skill complete, verify:

- [ ] **Frontmatter** has `name` (kebab-case) and trigger-specific `description`
- [ ] **Prime directive** is the first content — bold, imperative, unambiguous
- [ ] **Rules** are numbered, near the top, with bold lead-ins
- [ ] **Code examples** are complete (imports, wiring) and language-tagged
- [ ] **Anti-patterns** section exists with wrong/right/why format
- [ ] **Troubleshooting** table exists with symptom/cause/fix entries
- [ ] **Verification** section specifies how to confirm correct implementation
- [ ] **Cross-links** reference all related skills
- [ ] **No framework doc repetition** — only project-specific delta
- [ ] **Every sentence is an instruction** — if it doesn't tell me to do something, question whether it belongs
- [ ] **Instruction-vs-Example separation** — every full template, agent prompt, ASCII diagram, sample report body, "real-world incident" anecdote, or pure-reference catalogue is in a sidecar file and SKILL.md references it with precise wording. Inline directly-actionable instructions only.

---

## References

- [reference.md](reference.md) — Full best practices document from the LLM's perspective: how I process context, attention decay, pattern matching, token economics
