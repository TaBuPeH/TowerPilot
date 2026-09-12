---
name: python-typing-ops
description: "Python type hints daily patterns — modern syntax, collection ABCs, Protocol, TypedDict, TypeGuard, Literal. Triggers on: type hints, typing, TypeVar, Protocol, mypy, pyright, annotation, TypedDict."
---

<!-- imported 2026-08-18 from https://github.com/0xDarkMatter/claude-mods, scrubbed and distilled for this project -->

# Python Typing Patterns

Modern type hints (3.10+ syntax; this project is 3.12 — use `X | Y`, builtin generics, no `typing.List`/`Optional`).

## The daily 80%

```python
def greet(name: str, times: int = 1) -> str: ...
def find(id: int) -> str | None: ...                  # optional = union with None
items: list[str]; mapping: dict[str, int]
coords: tuple[int, int]                               # fixed-shape tuple

from collections.abc import Sequence, Mapping, Iterable, Callable
def process(items: Sequence[str]) -> list[str]: ...   # accept ABCs, return concrete
Handler = Callable[[str, int], bool]                  # function type

from typing import TypeVar
T = TypeVar("T")
def first(items: Sequence[T]) -> T | None:
    return items[0] if items else None
```

numpy-friendly: annotate arrays as `np.ndarray` (or `npt.NDArray[np.uint8]` via `import numpy.typing as npt` when dtype matters — screenshots are `npt.NDArray[np.uint8]`, match scores `float`).

## Protocol (structural typing)

```python
from typing import Protocol

class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> bool: ...

def run(d: Detector) -> None: ...   # anything with a matching detect() qualifies — no inheritance
```

Use `Protocol` with `__call__` for callbacks that take keyword args (plain `Callable` can't express them).

## TypedDict (dicts with known keys)

```python
from typing import TypedDict, NotRequired

class MatchResult(TypedDict):
    name: str
    score: float
    region: NotRequired[tuple[int, int, int, int]]   # optional key

def best(r: MatchResult) -> str:
    return r["name"]                                  # type-safe key access
```

`total=False` makes all keys optional; `Required[...]` opts single keys back in.

## Type guards & narrowing

```python
from typing import TypeGuard

def is_str_list(val: list[object]) -> TypeGuard[list[str]]:
    return all(isinstance(x, str) for x in val)

if is_str_list(items):
    ", ".join(items)          # items is now list[str]
```

`isinstance(x, T)` and `x is None` checks narrow automatically inside the branch.

## Literal and Final

```python
from typing import Literal, Final

Mode = Literal["read", "write", "append"]   # only these values allowed
MAX_SIZE: Final = 1024                      # cannot be reassigned
```

## Quick reference

| Type | Use case |
|------|----------|
| `X \| None` | Optional value |
| `list[T]` / `dict[K, V]` | Builtin generics |
| `Sequence[T]` / `Mapping[K, V]` | Flexible parameters (accept list/tuple/dict-like) |
| `Callable[[Args], Ret]` | Function type |
| `TypeVar("T")` | Generic parameter |
| `Protocol` | Structural typing (duck-typed interfaces) |
| `TypedDict` | Dict with fixed keys |
| `Literal["a", "b"]` | Specific values only |
| `TypeGuard[T]` | Custom narrowing function |
| `Final` | Constant |

Check with `mypy src/ --strict` or `pyright src/`; config lives in `pyproject.toml` (`[tool.mypy] strict = true`).
