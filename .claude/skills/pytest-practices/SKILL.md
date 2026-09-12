---
name: pytest-practices
description: pytest essentials — fixtures, parametrize, tmp_path/monkeypatch, marks, run flags, and anti-patterns. Use when writing or debugging pytest tests, fixtures, conftest.py, or pytest config.
---

<!-- imported 2026-08-18 from https://github.com/aks-builds/quality-skills, scrubbed and distilled for this project -->

# pytest Practices

Plain `assert` works — pytest rewrites it for rich diffs. No `unittest.TestCase`, no matcher API. Don't fabricate pytest APIs or fixture names; when uncertain, check `docs.pytest.org`.

## Layout

```
tests/
├── conftest.py        # shared fixtures — auto-discovered, available to everything below it
├── unit/test_*.py
└── fixtures/          # static test data (sample screenshots, templates)
```

A `conftest.py` in a subdirectory scopes its fixtures to that subtree.

## Fixtures

```python
@pytest.fixture
def fake_screen():
    img = np.zeros((720, 1280, 3), np.uint8)
    yield img                      # yield style = setup before, teardown after

def test_detector(fake_screen):    # injected by name
    assert not detect(fake_screen)
```

- Scopes: `function` (default) / `class` / `module` / `session`. Pick the narrowest scope that's still cheap — a session-scoped fixture that **mutates** is a flake factory.
- `@pytest.fixture(autouse=True)` runs for every test; use sparingly (surprises readers).
- `@pytest.fixture(params=[...])` runs every dependent test once per param.

## Parametrize

```python
@pytest.mark.parametrize('name,expected', [
    ('gem_claim', True),
    ('nothing', False),
    pytest.param('edge_case', True, id='readable-name'),
])
def test_template(name, expected):
    assert match(name) == expected
```

## Built-in fixtures (the daily set)

| Fixture | Use |
|---|---|
| `tmp_path` | Per-test temp dir (`pathlib.Path`) — never write into the repo |
| `monkeypatch` | `setattr`/`setenv`/dict item swaps; auto-reverts after the test |
| `capsys` / `caplog` | Capture stdout/stderr / logging records |

```python
def test_reads_config(monkeypatch, tmp_path):
    cfg = tmp_path / 'app.toml'; cfg.write_text('debug = true')
    monkeypatch.setenv('APP_CONFIG_PATH', str(cfg))
    assert load_config().debug is True
```

Mocking: `monkeypatch.setattr('mod.clock.now', lambda: fixed)` for simple swaps; `unittest.mock.patch` for call assertions. Mock at the boundary (adb socket, filesystem) — never your own logic under test.

## Marks & running

```python
@pytest.mark.slow                      # register custom marks in pyproject.toml to avoid warnings
@pytest.mark.skipif(cond, reason=...)  # conditional skip
@pytest.mark.xfail(reason=...)         # known-broken
```

| Command | Purpose |
|---|---|
| `pytest tests/unit` / `pytest file.py::test_x` | Subset / single test |
| `pytest -k "expr"` / `-m "not slow"` | Filter by name / mark |
| `pytest -x` / `--lf` | Stop at first failure / rerun last-failed |
| `pytest --pdb` / `-v` / `--tb=short` | Debugger on failure / verbose / short tracebacks |

## Anti-patterns

- `assert x or "message"` — always true. Correct form: `assert x, "message"`.
- Expensive work at `conftest.py` module level — runs at collection; use a session fixture.
- Session-scoped fixtures that mutate → order-dependent tests.
- One mega-fixture doing five things — split; fixtures compose.
- Module-level imports that hit the network/device — collection alone then needs hardware.
- Mutable default shared across `parametrize` cases — use a factory.
- Writing files anywhere but `tmp_path` — leaks junk into the project.
- Snapshot-asserting huge blobs — fragile; assert the fields that matter.
- Order-dependent failures (revealed by random ordering) — fix the shared state, not the order.
