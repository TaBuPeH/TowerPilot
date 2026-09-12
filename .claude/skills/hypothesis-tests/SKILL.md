---
name: hypothesis-tests
description: Generate property-based tests using Hypothesis. Builds input strategies in tests/strategies.py that model the valid search-space for each function, then writes minimal, behaviour-focused tests.
argument-hint: "[file, directory, or description of what to test]"
disable-model-invocation: true
---

<!-- imported 2026-08-18 from https://github.com/honnibal/claude-skills, scrubbed and distilled -->

# Property-Based Tests with Hypothesis

You are in property-based test authoring mode: read production code, design Hypothesis strategies modelling each function's valid input space, and test core behavioural contracts.

## Scope

$ARGUMENTS

If the user names files/directories, work only those; otherwise prioritise modules with complex logic and no tests. For large codebases, ask (`AskUserQuestion`) where to start.

**Don't rely on memory for Hypothesis APIs** — for anything you're not fully confident about (`@st.composite` draw interface, `st.from_type`/`register_type_strategy` resolution, `settings` profiles, `RuleBasedStateMachine`), check the installed package source under `site-packages/hypothesis/` or the official docs at `hypothesis.readthedocs.io`. A strategy that silently generates invalid data is worse than a minute spent checking.

## Workflow

1. **Survey** — read files in scope; note what each function accepts, returns, and maintains as invariants; note existing tests.
2. **Design strategies** — write them all to `tests/strategies.py` (see below).
3. **Write tests** — `tests/test_<module>.py`, importing strategies, using `@given`.
4. **Verify** — `pytest --co -q` to check collection, then run. Fix bugs in your strategies/tests; **report** bugs found in production code, don't silently fix.

## Strategy design (`tests/strategies.py`)

A strategy is a **model of the function's valid input space**, not "data of the right type".

- **Start from the function, not the type.** Guards, assertions, early returns reveal constraints the signature doesn't (a `str` that must be non-empty, a path with a specific extension).
- **Mentally sample each strategy** and trace values through the function — would any hit an unguarded raise or a meaningless result? Tighten.
- **Encode constraints, don't filter:** `st.integers(min_value=1)` over `.filter(lambda x: x > 0)`; `st.from_regex(r"[a-z_]\w*", fullmatch=True)` over `.filter(str.isidentifier)`.
- **Mirror the production domain** — records that look like real records; `st.builds()` when each argument is independent, `@st.composite` as soon as fields depend on each other:

```python
@st.composite
def valid_date_range(draw):
    start = draw(st.dates(min_value=date(2020, 1, 1)))
    end = draw(st.dates(min_value=start))          # dependent field
    return DateRange(start=start, end=end)
```

- **Compose from small named pieces** (`valid_name`, `positive_int`) so strategies read as documentation of valid input.
- **Register core domain types** (`st.register_type_strategy(T, ...)`) so `st.from_type(T)` resolves automatically.
- **Don't over-constrain** — a strategy generating a handful of values is a parameterised test in disguise. Tight enough to be valid, loose enough to surprise you.
- Structure the file: atomic strategies, then composed/domain strategies, then type registrations; brief comment per group explaining what it models and why the constraints exist.

## Test design

Good properties (things true for ALL valid inputs):

- Roundtrip/inverse: `decode(encode(x)) == x`
- Idempotence: `f(f(x)) == f(x)`
- Invariant preservation: `len(merge(a, b)) <= len(a) + len(b)`
- Monotonicity: `x <= y` implies `f(x) <= f(y)`
- Equivalence to a reference: `fast_path(x) == naive_impl(x)`
- No-crash smoke — only as a last resort; if no checkable contract exists, leave a `# TODO` and move on.

Do NOT test: structural trivia (`assert "name" in result` tests schema, not behaviour); expected outputs computed by re-running the production logic; accidents of the current implementation the contract doesn't promise; pure glue functions (test the leaves instead).

**Mocks:** minimise. Never mock the function under test; if the test is mostly mocks, leave `# TODO: needs refactoring for testability`. Acceptable: external I/O (network, filesystem, device) that would be slow or non-deterministic.

**Conventions:** one test class per function, one method per property, named for the property (`test_roundtrip`, `test_idempotent` — never `test_works`). `assume()` sparingly — every `assume` is a missed strategy improvement. Don't lower `max_examples` just to pass faster.

## Coverage discipline

A weak test that merely calls the function creates the *illusion* of coverage. If you can't state the property in one sentence, don't write the test — leave `# TODO: no obvious property` instead. Skip trivial one-liners. Prefer one genuine roundtrip property over five field-checks.

## Critical rules

- **Read before testing** — understand actual constraints, not just signatures.
- **Valid inputs only** — error-path testing is a separate, explicit activity, not `@given` noise.
- **Strategies live in `tests/strategies.py`**, reusable and separate from assertions.
- **Keep tests minimal** — one property per test; bundled checks obscure which property broke.
- **Ask when uncertain** (`AskUserQuestion`).
