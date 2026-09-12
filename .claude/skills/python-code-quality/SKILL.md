---
name: python-code-quality
description: Python code-quality core — fail-loud error handling, truthiness-guard and mutable-default anti-patterns, determinism rules, lint/type-check basics. Use when reviewing, refactoring, or hardening Python code.
---

<!-- imported 2026-08-18 from https://github.com/wdm0006/python-skills, scrubbed and distilled for this project -->

# Python Code Quality

Tools: `ruff check src && ruff format src` (lint+format), `mypy src` (types). Minimal config in `pyproject.toml`: ruff `select = ["E","W","F","I","B","C4","UP"]`; mypy `disallow_untyped_defs = true`, `warn_return_any = true`.

## Anti-patterns

```python
def f(items: list = []):            # BUG: mutable default shared across calls
def f(items: list | None = None):   # fix: None default, `items = items or []` inside

except: pass                        # BUG: bare except swallows everything
except ValueError as e: log(e)      # fix: specific exception, visible handling

hour = cfg.get("start_hour") or 9   # BUG: a valid 0 silently becomes 9
hour = h if (h := cfg.get("start_hour")) is not None else 9   # guard on None, not truthiness

if name is not "":                  # BUG: identity vs literal (works only by interning)
if name != "":                      # fix: value comparison
```

`x = x or default` is fine **only** when the single falsy value you mean to replace is an empty container. For numbers/bools/strings where `0`/`False`/`""` are meaningful, it's a bug — use `is None`.

## Fail loud — don't degrade silently

The costliest bugs are failures that look like success. When you catch an error: recover meaningfully, or make it **visible** (raise, error-log, or return a distinguishable sentinel). A `return`/`continue`/fallback inside `except` that produces normal-looking output is where silent corruption lives.

- Don't collapse exceptions into a generic string (`return {"error": str(e)}`) — traceback and context are lost; every failure looks the same. Let it raise.
- Give success and failure **distinct** sentinels — never set the same status value on both paths.
- Never substitute fabricated/sample data on failure — plausible invented numbers are worse than an error.
- Partial results must say so: return `(items, complete)` or raise — a truncated fetch must not look like a full one.
- Batch loops: collect per-item errors and return them; a bare `continue` makes skipped items vanish with no trace of coverage lost.
- Subprocess-style calls: a nonzero exit shouldn't discard stdout — many tools write their real summary there and exit nonzero *by design*. Return stdout+stderr+code and let the caller decide.

## Determinism & reproducibility

- **Sorting before serializing:** iterating a `set` (or unsorted dict merge) into a file/JSON reshuffles output every run — churny diffs, drowned real changes. `sorted(...)` + `json.dump(..., sort_keys=True)`.
- **RNGs:** seed *all* of them and make them injectable — `rng = random.Random(seed)` and `nprng = np.random.default_rng(seed)` threaded through, never bare globals (`random.seed`/`np.random.seed` clobber caller state, and seeding one stream leaves the other free-running).
- **Locale-dependent parsing** (`strptime` with `%b`/`%a`) changes behavior across machines — pin formats.
- Non-determinism forces loose assertions ("contains any of…") that catch nothing. Inject the seam; assert exact output.

## A rule you enabled isn't a rule that fires

Linters ship invisible exemptions. Before relying on a check, **write the violation on purpose once and confirm it's reported** — same discipline as watching a regression test go red. The worst Ruff default: `dummy-variable-rgx` matches *any* leading-underscore name, so `F811` (redefinition of unused name) ignores every `_private` helper — a duplicated `def _helper` after a merge passes clean, and someone patches the dead copy. Fixes:

```toml
[tool.ruff.lint]
dummy-variable-rgx = "^_$"    # only bare `_` is a throwaway (tradeoff: `_unused = f()` now trips F841)
```

```bash
# after resolving a conflict, hunt duplicated top-level definitions directly:
grep -oE '^(def|class) [A-Za-z_][A-Za-z0-9_]*' module.py | sort | uniq -d
```

## Pythonic idioms

```python
for item in items:                      # not range(len(items)); enumerate() when index needed
value = d.get(key, default)             # not if key in d: ...
with open(path) as f:                   # not manual try/finally close
squares = [x**2 for x in numbers]       # comprehensions (simple ones only)
```

## Review checklist

- [ ] ruff + mypy pass; public functions typed
- [ ] No mutable defaults; no bare except; no `is`/`is not` vs literals
- [ ] Truthiness guards don't swallow valid `0`/`False`/`""` (guard on `is None`)
- [ ] Failures fail loud — no fabricated fallbacks, colliding sentinels, or silent truncation
- [ ] Batch loops collect per-item errors instead of bare `continue`
- [ ] Deterministic output: sort before serializing; seed/inject all RNGs (stdlib *and* numpy)
- [ ] Load-bearing lint rules verified to actually fire; no duplicate `def`/`class` after merges
