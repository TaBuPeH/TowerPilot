---
name: single-source-of-truth
description: >-
  Canonical-location discipline for every fact, rule, and decision in this
  repo. Use when writing or editing CLAUDE.md / memory files, when authoring or
  reviewing any skill SKILL.md, when creating a new knowledge or architecture
  doc, when answering "where does X live?" questions, and any time you suspect
  a rule is duplicated across two surfaces.
---

# Single Source of Truth

**Every fact, rule, or decision lives in exactly one canonical location. Every other surface that mentions it is a pointer or a cache, never a copy.**

## Rules

1. **Match every artefact to one canonical home before writing.**

   | Truth domain | SSOT | Cache / mirror |
   |---|---|---|
   | Code + code-adjacent docs | The repo (source files, per-module docs) | Build output, installed copies |
   | Workflow + convention rules | `.claude/skills/<name>/SKILL.md` (one skill per domain) + CLAUDE.md (pointer index only) | Hooks **implement**; never redefine |
   | Session/personal memory | Per-user memory files | Recall index only — never citable as team truth |

2. **Pointer pattern, not copy-paste.** When a fact needs to surface in N places, ONE is canonical and the rest are `[See: <name>](path)` pointers. Never duplicate the rule body.
3. **Code is canonical for code facts.** Docs, plans, and memory describing code are caches; when they disagree with source, source wins (see [verify-against-ground-truth](../verify-against-ground-truth/SKILL.md)).
4. **Personal memory is recall, never citation.** A remembered decision is a *promotion candidate*: when it proves durable, promote it to a skill (workflow / convention rule) or a knowledge doc. Plans and reports cite the promoted artefact — never the memory itself.

---

## Decision Procedure

Before writing any rule, fact, or decision:

1. **Search the canonical SSOT first.** Match the artefact to its domain, then search there: `Grep` the skills directory for convention rules, the repo for code-adjacent docs.
2. **If it exists, point.** Insert a `[See: <name>](path)` link to the canonical location. Stop.
3. **If it does not exist, write it in the SSOT.** Pick the canonical home; write the canonical version there.
4. **Never write the same content in two places.** A second mention is a pointer, never a copy. If you find yourself pasting, the second site is wrong by construction.

---

## Anti-Patterns

| Wrong | Right | Why |
|---|---|---|
| Copying a rule body into CLAUDE.md and a skill | Rule body in the skill; CLAUDE.md holds a one-line pointer | Two copies diverge on the first edit |
| Citing a memory note as proof of a code fact | Verify in source; cite `file:line` | Memory is a recall index, not evidence |
| Documenting the same procedure in two knowledge docs | One canonical doc; the other links to it | Duplication rots asymmetrically |
| A hook or script that re-states a skill's rule text | The hook enforces; the skill defines | The definition must have exactly one home |

## Verification

1. For every rule you wrote this session: does it exist in exactly one place, with pointers elsewhere?
2. Grep for a distinctive phrase of the rule — more than one hit outside pointers means duplication.
3. For every fact you cited: is the citation to the canonical source, not a cache?

## See Also

- [verify-against-ground-truth](../verify-against-ground-truth/SKILL.md) — code wins disagreements with docs
- [verify-never-infer](../verify-never-infer/SKILL.md) — memory is recall, never citation
- [markdown-format](../markdown-format/SKILL.md) — markdown conventions for every `.md` file
- [skill-writing-guide](../skill-writing-guide/SKILL.md) — the rubric skills are graded against
