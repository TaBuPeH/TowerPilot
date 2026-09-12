---
name: tighten-types
description: Analyze Python code and tighten type annotations. Finds missing attribute types, replaces loose dicts with Pydantic models or TypedDicts, adds overloads, and removes redundant in-body annotations.
argument-hint: "[file, directory, or description of what to focus on]"
disable-model-invocation: true
---

<!-- imported 2026-08-18 from https://github.com/honnibal/claude-skills, scrubbed and distilled -->

# Tighten Python Type Annotations

You are in type-tightening mode: systematically review Python files, identify weak or missing annotations, and fix them.

## Scope

$ARGUMENTS

If the user names files/directories, work only those; otherwise work through the project's Python files. For large codebases, ask (`AskUserQuestion`) which modules to start with.

## Workflow

1. **Survey** — read the files in scope; build a mental model of the module's types before proposing changes.
2. **Analyse** — apply the checklist; collect findings first so cross-cutting patterns surface (the same dict shape in several places = one shared model).
3. **Edit** — one grouped pass per file; summarize what changed and why.
4. **Verify** — run the configured type checker (`pyproject.toml [tool.mypy]`, `pyrightconfig.json`, …); fix any errors you introduced.

## Checklist

**1. Missing class attribute annotations.** Attributes assigned in `__init__` with no annotation → annotate on the class body:

```python
class Pipeline:
    name: str
    _cache: dict[str, Any]
    def __init__(self, name: str) -> None: ...
```

Prefer concrete types from the defining library over generic stand-ins; `__slots__` hints at which attributes exist.

**2. Import types from third-party libraries** instead of `Any` or hand-rolled aliases (numpy: `np.ndarray`/`npt.NDArray`, Pydantic: `BaseModel`, etc.). Use `TYPE_CHECKING` imports to avoid runtime cycles:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from heavy.module import Thing
```

**3. Structured dicts → Pydantic model or TypedDict.** Signals: literal string keys used consistently across construction and access; several functions passing the same shape; a dict built incrementally then returned; docstrings describing expected keys. Choose **BaseModel** when the value crosses a system boundary (config files, serialisation) or needs validation; **TypedDict** for internal structures or where callers expect a plain dict. Place the model near its users, not in one giant types file. Ask when unsure.

**4. `@overload` for narrowable unions** — when an argument determines the return type:

```python
@overload
def load(path: str, as_bytes: Literal[False] = ...) -> str: ...
@overload
def load(path: str, as_bytes: Literal[True]) -> bytes: ...
def load(path: str, as_bytes: bool = False) -> str | bytes: ...
```

Same technique for input-type-determines-output (`str -> str`, `Doc -> Doc`) and string-flag selectors. Don't add overloads speculatively — only when the narrowing is clear from the implementation.

**5. Redundant in-body annotations** signal a type that's too loose upstream — fix the root cause:

- `x: T = f(...)` where `f` already returns `T` → remove; if `f` is unannotated, annotate `f` instead.
- Annotation narrowing a union / `assert isinstance` narrowing → tighten the source type.
- Keep annotations on initial declarations (`items: list[str] = []` is fine); the concern is re-annotations and casts compensating for loose types elsewhere.

**6. Lower priority (only while already touching the file):** `Optional[X]` → `X | None`; `typing.List` → `list`; `-> None` on `__init__`; `Self` for methods returning self; `collections.abc` ABCs in parameters; `Final` for constants.

## Critical rules

- **Read before editing** — understand data flow before tightening.
- **Don't break runtime behaviour** — annotations must be invisible at runtime; Pydantic introductions are NOT (validation, attribute access) — update call sites.
- **Preserve public API compatibility** — `dict` → `SomeModel` in a signature is breaking; flag it and ask. Overloads add precision without breaking.
- **Run the type checker** after changes; introduce no new errors.
- **Ask when uncertain** (`AskUserQuestion`).
