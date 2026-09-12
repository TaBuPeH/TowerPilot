---
name: source-code-first
description: Always search project source code before compiled output, build artifacts, or installed third-party packages (site-packages, node_modules, dist, vendored copies). Use when investigating logic, tracing execution, or exploring the codebase.
---

# Source Code First — Search Priority for Agents & Workers

**Always search the project's own source files first. Never default to build output, caches, or installed third-party packages when raw source is available.**

## Rules

1. **Source first, always** — start every code investigation from the project's source directories. This is the default for all Grep, Glob, and Read operations.
2. **Exclude build output and package directories by default** — `dist/`, `build/`, `__pycache__/`, `.venv/`, `site-packages/`, `node_modules/`, vendored copies. Add exclusion globs or scope searches to source explicitly.
3. **Never edit build output** — all edits target source files. Build/output artifacts are overwritten on every rebuild.
4. **Delegate with this rule** — when spawning sub-agents for code investigation, include the source-code-first instruction in the prompt.
5. **Installed packages only for third-party signatures** — acceptable only when you need function signatures, type stubs, or internal behavior of a third-party dependency that has no local source checkout.
6. **Build output only for build verification** — acceptable only to confirm compiled/generated output exists or check timestamps before restarting a process.
7. **If a dependency has a local source checkout, read that** — an installed copy may be stale relative to the current dev branch.

---

## Search Priority

| Priority | Location | When to use |
|----------|----------|-------------|
| 1 (default) | Project source files | **Always** — for understanding logic, execution flow, implementation details, debugging |
| 2 (supplementary) | Installed third-party packages (`site-packages/`, `node_modules/`) | Only for third-party package signatures, type definitions, or undocumented behavior |
| 3 (verification only) | Build/generated output | Only for build verification — confirming generated output matches expectations |

---

## Anti-Patterns

| Temptation | Why it fails | Do this instead |
|---|---|---|
| Grepping build output to understand logic | Generated code is stripped of comments, types, and structure | Scope the search to source directories |
| Reading an installed copy of a package that has a local source repo | The installed version may be stale or stripped | Read the source checkout |
| Editing a generated file to "quick-fix" a runtime issue | Overwritten on the next build/generation — the edit is lost | Edit the source, then regenerate |
| Broad Grep with no path scoping | Hundreds of irrelevant matches from caches and package dirs drown the signal | Scope to source, or exclude `__pycache__/`, `.venv/`, `node_modules/`, `dist/` |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Grep returns hundreds of irrelevant matches | Default search path includes build output / installed packages | Scope the search to the source directory or add exclusion globs |
| Agent concludes a function doesn't exist | Searched generated output where the name was mangled or inlined | Re-search in source — original names are preserved there |
| Agent reads the wrong version of a shared library | Read the installed copy instead of the current source | Read the source checkout for anything you also develop locally |
| Sub-agent wastes tokens on irrelevant matches | Delegated search without the source-code-first instruction in the prompt | Include the Agent Prompt Template below in all sub-agent prompts |

---

## Agent Prompt Template

When spawning sub-agents for code investigation, include this in the prompt:

```
IMPORTANT: Always search project source code first. Do NOT search build output,
__pycache__, .venv/site-packages, node_modules, or vendored copies to understand
project logic. Only use installed packages for third-party signatures/internals,
and build output for build verification. Exclude both from default searches.
```

---

## Verification

After any code investigation task, self-check:

1. Were all Grep/Glob calls scoped to source (not build output, not installed packages)?
2. Were shared-library lookups directed to source checkouts when one exists?
3. If build output or an installed package was accessed, was it for a valid reason (build verification, third-party signature)?
4. Did sub-agent prompts include the source-code-first instruction?
5. Were conclusions based on source code, not generated output?

---

## See Also

- [verify-against-ground-truth](../verify-against-ground-truth/SKILL.md) — WHETHER to trust a doc claim; this skill governs WHERE to read the truth
- [verify-never-infer](../verify-never-infer/SKILL.md) — cite facts from source read in this session
