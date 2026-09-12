---
name: markdown-format
description: Universal markdown formatting for `.md` files in this repo — blockquote metadata header (NEVER YAML frontmatter), standard markdown links, callouts, tables. Use when creating or significantly editing any `.md` file (plans, knowledge docs, audits, READMEs, scratch notes — anything not auto-generated).
---

# Markdown Format

**One markdown format for everything in this repo.** Local files, notes, docs, READMEs, knowledge bases, scratch notes — all of it.

YAML frontmatter at the top of content files fails outside specialized tools: generic LLMs treat the frontmatter as opaque code and skip it, some platforms strip it on import, and on GitHub it renders as a raw `---` table separator. The metadata becomes invisible to most readers — which defeats the point. The only YAML frontmatter that may appear in this repo is on files where a tool requires it (e.g. `.claude/skills/*/SKILL.md` — Claude Code's skill loader reads `name:` / `description:` from YAML). Content files never use YAML.

## Rules

1. **Every content `.md` file starts with an `# H1` heading followed by a blockquote metadata header** — `> **Key:** value`, one field per line. NEVER `---` YAML frontmatter for content. The `# H1` is the title; do not duplicate it as a `> **Title:**` field.
2. **Bump `> **Updated:** YYYY-MM-DD` on every meaningful edit.** Stale dates hide review freshness.
3. **All cross-references use standard markdown links** `[text](path)` — relative paths for repo files, full `https://` URLs for external. NEVER `[[wiki-links]]` — they break in most renderers and IDEs.
4. **Use callout syntax `> [!type] Title`** for notices. Renderers that support it show styled boxes; everything else degrades gracefully to a plain blockquote.
5. **No inline `#hashtags` on headers.** Put taxonomy in the metadata blockquote (`> **Tags:** one, two, three`) instead.
6. **Separate `##` sections with `---` horizontal rules** — never between `###` or `####`.
7. **Use tables for 3+ items with shared columns.** Bullet lists for short or sequential lists.
8. **Keep the metadata block tight** — five to ten fields tops. If you need more structure, put it in a `## Metadata` section, not in the header.

---

## Scope

Apply to **every `.md` file** in this repo, including plans, pending tasks, knowledge docs, personal notes, scratch files, and READMEs.

### The YAML exception — system-required frontmatter only

A small set of files MUST start with `---` YAML frontmatter because a tool reads it:

- `.claude/skills/*/SKILL.md` — Claude Code's skill loader requires `name:` / `description:`
- `.claude/commands/*.md` — slash command headers
- `.claude/agents/*.md` — agent definitions

For these files: keep ONLY the system-required fields in the YAML block. Everything else (longer description, body content) follows this skill's rules. Do not duplicate `name`/`description` into a body blockquote — one source of truth.

### Out of scope

- Auto-generated markdown (changelogs, package docs, lock files) — leave alone
- `.md` files inside package/vendor/build directories — never edit

---

## Blockquote Metadata Header

```markdown
# Plan: Refactor the Detection Pipeline

> **Status:** Draft
> **Type:** Plan
> **Created:** 2026-04-27
> **Updated:** 2026-04-27
> **Owner:** Georgi
> **Tags:** refactor, detection-pipeline

## Summary

...
```

**Rules for the header:**

- One field per line, each line starts with `> **Key:** value`
- Bold the key, plain value, single space after the colon
- Place immediately after the `# H1` — no blank line between, no prose interleaved
- A single blank line follows the header, then the first `##` section starts

**Common fields** (use what applies; document type-specific conventions add their own):

| Field | Use for | Example |
|---|---|---|
| `Status` | Lifecycle | Draft, Approved, In Progress, Executed, Done, Archived |
| `Type` | Document kind | Plan, Task, Audit, Knowledge, Report, Note |
| `Created` | First-write date (ISO) | `2026-04-27` |
| `Updated` | Last-edit date (ISO) | `2026-04-27` |
| `Owner` | Driver / author | `Georgi` |
| `Tags` | Comma-separated taxonomy | `refactor, detection-pipeline` |

**Why blockquote works for every reader:** LLMs ingest it as content; GitHub renders a clean blockquote; `grep`/`rg`/embedding indexes find values directly. See [reference.md](reference.md) for the forbidden YAML pattern.

---

## Markdown Links — Standard, Not Wiki-Links

- **Internal repo refs:** path relative to the file's directory
- **External:** full `https://` URLs
- **Anchors:** GitHub-style `#section-name` — lowercase, hyphenated, punctuation stripped

**Never use `[[wiki-links]]`** — they render as broken `[[text]]` literal in most readers.

---

## Callouts

```markdown
> [!info] Optional Title
> [!warning] Watch Out
> [!danger] Critical
> [!important]
> [!note]
> [!tip]
```

| Type | When to use |
|---|---|
| `[!info]` | Context, scope declarations, background |
| `[!warning]` | Gotchas, deprecations, surprises |
| `[!danger]` | Security risks, data loss, never-do-this |
| `[!important]` | Hard requirements, must-follow constraints |
| `[!note]` | Supplementary detail |
| `[!tip]` | Shortcuts, recommendations |

---

## Tables for Structured Data

| When | Use | Why |
|---|---|---|
| 3+ items, shared columns | Table | Comparable at a glance |
| Sequential steps | Numbered list | Order matters |
| Short list (≤2 items) | Bullet list | Table is overkill |
| Single fact per line | Inline text | No structure needed |

---

## Sections and Horizontal Rules

- Use `---` horizontal rules ONLY between `##` (h2) sections — never between `###`/`####`.
- Avoid `---` immediately after the metadata header — let the first `##` start cleanly with a blank line above.
- One `# H1` per file. Multiple H1s break tooling that uses the heading tree (TOCs, anchors, IDE outlines).

---

## Anti-Patterns

| Wrong | Right | Why |
|---|---|---|
| `---` YAML frontmatter at the top of a content file | `# H1` followed by `> **Status:** ...` blockquote header | Generic readers strip or misrender YAML; metadata becomes invisible. Blockquote stays first-class content. |
| `[[wiki-link]]` for cross-references | `[text](relative/path.md)` standard markdown links | Wiki-links render as broken `[[...]]` literal in most readers. |
| Inline `#hashtag` on `## Header #tag` | `> **Tags:** one, two` in metadata | Inline hashtags are tool-specific clutter. |
| `> **Warning:**` plain blockquote | `> [!warning] Title` callout | Callouts upgrade to styled boxes where supported and stay readable elsewhere. |
| Editing body but leaving `> **Updated:**` stale | Bumping `> **Updated:** YYYY-MM-DD` on every edit | Stale dates hide content freshness. |
| `---` between `### Subsection` headings | `---` only between `## Section` headings | Horizontal rules between subsections break visual hierarchy. |
| YAML frontmatter AND blockquote header in same file | Blockquote only (system-required SKILL.md frontmatter excepted) | Two metadata sources of truth diverge on edit. |
| Multiple `# H1` headings in one file | One `# H1` at the top, rest as `##`/`###` | Multiple H1s break TOCs and anchors. |
| Skipping the metadata header on a "small note" | Always include at least `Status` + `Created` + `Updated` | A note without metadata is unfindable in 3 months — the smallest header still beats nothing. |

---

## Verification

Before considering any `.md` file complete:

1. **No `---` YAML frontmatter** at the top (unless it's a `SKILL.md`, slash command, agent file, or other tool-required exception)
2. **Blockquote header** is present immediately after `# H1`, no blank line between them
3. **`Updated:` reflects the current edit date**
4. **All internal links use `[text](path)` standard markdown** — no `[[wiki]]`
5. **Callouts use `> [!type]` syntax** — not `> **Type:**`
6. **Horizontal rules only between `##` sections**
7. **Single `# H1`** at the top, no duplicates lower in the file
8. **No inline `#hashtag` on headers**

See [reference.md](reference.md) for quick verification bash scripts.

---

## See Also

- [reference.md](reference.md) — complete file example, forbidden YAML pattern, verification scripts
- [pending-tasks](../pending-tasks/SKILL.md) — task-specific blockquote header fields
- [single-source-of-truth](../single-source-of-truth/SKILL.md) — one canonical home per fact
- [skill-writing-guide](../skill-writing-guide/SKILL.md) — `SKILL.md` files use the system-required YAML exception (do not apply this skill's "no YAML" rule to skill files themselves)
