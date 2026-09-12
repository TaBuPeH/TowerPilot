---
name: codebase-memory
description: >-
  Query the local codebase-memory (cbm) MCP graph for structural navigation
  across indexed repos — "where is X defined", "who calls X", callers/callees,
  impact analysis, complexity/hot-path detection, dead code. Use INSTEAD of
  blind grep for where/who/what-connects questions; still read source for
  how/why. Trigger when locating a symbol, tracing a call graph, assessing
  blast radius of a change, or hunting nested-loop/recursion hot paths.
---

# Codebase Memory (cbm) — Structural Navigation Over Blind Grep

**For "where / who / what-connects" questions, query the `mcp__codebase-memory__*` graph FIRST — it is a pre-built structural index of the indexed repos, answering in one call what would take many greps. Then read the actual source for the "how / why". The graph LOCATES; source is the TRUTH.**

> [!note]
> cbm is a machine-local MCP server. If the `mcp__codebase-memory__*` tools are not available in this project's session, or this repo is not in `list_projects`, index it first (or fall back to Grep) — do not assume the graph covers this project.

## Rules

1. **Use it for structure, not comprehension.** It knows *that* A calls B, where X is defined, and the shape of the call graph — NOT *what the code does*, the business rules, or the conditionals. Behavior questions still require reading source (pair with [source-code-first](../source-code-first/SKILL.md)).
2. **It is a STATIC SNAPSHOT — never cite it as a verified fact.** A graph node is not proof. Per [verify-never-infer](../verify-never-infer/SKILL.md), before asserting a line number, signature, or "X returns Y", `Read` the current source. The graph is only as fresh as the last index — stale after edits until refreshed.
3. **Refresh after code changes.** cbm does NOT auto-update. After editing a repo, run the refresh script (`~/.local/bin/cbm-refresh.sh <abs-repo-path>`, or `--all`) — it re-indexes that repo AND re-runs the cross-repo pass.
4. **Static vs runtime.** cbm shows what code CAN call, not what actually ran. For runtime behavior, read logs / run output.
5. **Vendored and generated code is NOT indexed.** Third-party packages and build output are invisible to the graph. Files it could only partially parse live in the separate `missed` graph (`query_graph(graph="missed")`) — absence from the code graph is not a completeness guarantee.
6. **Project name = repo path, mechanically.** Drive letter upper-cased, `:` dropped, every `/` → `-`. `d:/projects/tower` → `D-projects-tower`. Run `list_projects` if unsure.

---

## Which tool for which question

| Question | Tool / call |
|---|---|
| Where is `X` defined? What are its properties? | `search_graph` (name/label filter) |
| Who calls `X`? What does `X` call? Blast radius of changing it? | `trace_path function_name:X direction:inbound\|outbound\|both mode:calls` |
| How does a value flow through `X`? | `trace_path mode:data_flow parameter_name:...` |
| Hot paths — nested loops, recursion, O(n²) scans? | `query_graph` on complexity props (`transitive_loop_depth`, `linear_scan_in_loop`, `recursive`) |
| Which files parsed incompletely? | `query_graph(graph="missed")` |
| What projects are indexed? | `list_projects` |

BEFORE writing a complexity Cypher query, Read [reference.md → Query Recipes](reference.md#query-recipes) for the exact query bodies.

---

## What it does NOT replace

- **Behavior / logic / "why"** → read source.
- **Strings, config, literals** — env var names, log/error text, magic constants → `Grep` wins outright (they aren't graph structure).
- **Current-state facts you will assert** → `Read` the live file (Rule 2).
- **Runtime reality** — actual behavior, timing, failures → logs and live runs.
- **Anything vendored / generated / in the `missed` graph** (Rule 5).

---

## Anti-Patterns

| Temptation | Why it fails | Do this instead |
|---|---|---|
| Quote a line number / signature straight from a graph node in a report | The graph is a snapshot; the file may have changed since indexing | `Read` the source to confirm, then cite the file (Rule 2) |
| Grep-sweep everything to find who calls a function | Slow, misses indirection | `trace_path mode:calls` — the graph already resolved it |
| Ask cbm "what does this function do" | The graph has structure, not semantics — you get callees, not logic | `Read` the source file for behavior |
| Trust cbm right after editing a repo | It does not auto-index; results are stale | Refresh first (Rule 3) |
| Look for a third-party symbol in the graph | Vendored/generated code is excluded | `Read` the package source directly |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `trace_path`/`query_graph` returns nothing for a real symbol | Wrong `project` name, or repo not (re)indexed since the symbol was added | Verify via `list_projects`; derive name per Rule 6; refresh the repo |
| A project is missing from `list_projects` | Repo never indexed, or its db failed validation | Index/refresh the repo |
| Results look one version behind reality | Static snapshot, not refreshed since last edit | Refresh; for asserted facts always `Read` source (Rule 2) |
| Graph has no node for a file you know exists | File vendored/generated, or only partially parsed | Check `query_graph(graph="missed")`; read excluded code directly |

---

## Verification

Before relying on a cbm answer:

1. Did you pass the correct path-derived `project` name (Rule 6), confirmed via `list_projects` if unsure?
2. If the repo was edited this session, did you refresh before querying?
3. For any fact you will state to the user or write into a doc, did you `Read` the live source to confirm it (Rule 2)?
4. For a "what does it do" question, did you read source rather than infer from the call graph (Rule 1)?

---

## See Also

- [source-code-first](../source-code-first/SKILL.md) — WHERE to read the truth once cbm points you (cbm locates, source is canonical)
- [verify-against-ground-truth](../verify-against-ground-truth/SKILL.md) — WHETHER to trust a located claim
- [verify-never-infer](../verify-never-infer/SKILL.md) — a graph node is never a citable fact
- [reference.md](reference.md) — Cypher query recipes (complexity, missed graph)
