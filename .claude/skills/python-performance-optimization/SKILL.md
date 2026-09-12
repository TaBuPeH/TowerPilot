---
name: python-performance-optimization
description: Profile and optimize Python code — cProfile/py-spy/line_profiler workflow, numpy vectorization, caching, and benchmarking discipline. Use when debugging slow Python code or optimizing hot loops in the vision pipeline.
---

<!-- imported 2026-08-18 from https://github.com/jacexh/skills, scrubbed and distilled for this project -->

# Python Performance Optimization

Rule zero: **profile before optimizing**. Find the real bottleneck, fix the hot path, benchmark before/after.

## Picking a profiler

| Tool | Use when | Invocation |
|---|---|---|
| `cProfile` | First look — which functions eat time | `python -m cProfile -o out.prof script.py`, then `python -m pstats out.prof` (`sort cumtime` / `stats 10`) |
| `line_profiler` | One suspicious function, need per-line cost | `kernprof -l -v script.py` with `@profile` on the function |
| `py-spy` | A *running* process (e.g. the live autopilot) — no code changes | `py-spy top --pid N`; flamegraph: `py-spy record -o prof.svg --pid N` |
| `tracemalloc` | Memory growth / leaks | snapshot before/after, `snapshot2.compare_to(snapshot1, 'lineno')` |

Micro-benchmarks: use `timeit` (or `time.perf_counter()`), never wall-clock one-shot `time.time()`:

```python
import timeit
timeit.timeit(lambda: f(x), number=100)   # average over runs
```

## NumPy vectorization (the big lever here)

Per-element Python loops over image arrays are 10–100x slower than vectorized ops:

```python
# Slow: Python-level loop / listcomp over pixels
diffs = [abs(int(a) - int(b)) for a, b in zip(img1.flat, img2.flat)]

# Fast: whole-array ops (mind uint8 wraparound — promote first)
diffs = np.abs(img1.astype(np.int16) - img2.astype(np.int16))
mean_diff = diffs.mean()
```

- Replace loops with array expressions: `a * b`, `np.where(cond, x, y)`, boolean masks (`img[mask] = 0`), `np.count_nonzero`, `.sum()/.mean()` with `axis=`.
- Avoid needless copies: slicing is a view; `.copy()`, `astype`, and fancy indexing allocate. In hot loops, preallocate and use `out=` / in-place ops (`+=`).
- Crop the ROI *before* expensive ops (matchTemplate on a region, not the full frame).
- Keep dtypes small (`uint8`) until math demands promotion.

## Cheap wins in plain Python

```python
lookup = {k: v for ...}; x in lookup     # O(1) dict/set membership, not `x in list`  (O(n))
"".join(parts)                            # not repeated `s += part`
result = [f(i) for i in xs]               # comprehension over append-loop
data = (f(i) for i in xs)                 # generator when you only iterate once (constant memory)
```

- Hoist attribute/global lookups out of hot loops into locals; inline trivial helpers called millions of times.
- `@functools.lru_cache` for pure, repeated computations (check `f.cache_info()` to confirm hits).
- `__slots__` on classes instantiated by the thousands.
- CPU-bound and parallelizable? `multiprocessing.Pool` — threads won't help under the GIL.

## Benchmarking discipline

```python
def benchmark(func):                      # decorator for quick timing in dev
    @wraps(func)
    def wrapper(*a, **k):
        t = time.perf_counter(); r = func(*a, **k)
        print(f"{func.__name__}: {time.perf_counter()-t:.6f}s"); return r
    return wrapper
```

- Measure the same workload before and after; report the ratio, not vibes.
- Optimize hot paths only — clarity first everywhere else.

## Pitfalls

- Optimizing without profiling; over-optimizing rare paths.
- numpy `img1 + img2` on uint8 **wraps around** (200+100=44); `cv2.add` saturates. Promote dtype when doing arithmetic.
- Hidden O(n^2): list `in`/`remove`/`insert(0)` inside loops.
- Creating throwaway array copies every frame; reuse buffers.
- Ignoring algorithmic complexity — a better algorithm beats micro-tuning.
