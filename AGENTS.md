# csvql optimisation notes

## Benchmark methodology
- Use `hyperfine --warmup 3 'query ...'` for real wall-clock numbers.
- For macro-benchmarks, query 2 columns + WHERE on a generated 32 000-row CSV.
- For micro-benchmarks, use the `timeit` module with `gc.disable()` / `gc.enable()`.

## Summary table

| Feature | Status | Cold gain (10K) | Hot gain (10K) | Effort |
|---|---|---|---|---|
| Extension C (mmap + GIL release) | ✅ Done | ×1.8–2.5 | — | Medium |
| LRU parse cache | ✅ Done | — | ×1.5–42 | Low |
| list[list] refactor | ✅ Done | ×1.2, −56% memory | — | Medium |
| Type inference (pre-typed float) | ✅ Done | ×1.1 | — | Low |
| Numba JIT | ❌ Not beneficial | 4× slower | — | Medium |

## Key findings

### 1. numpy GROUP BY acceleration — NOT worth it for typical datasets
- Implemented `_apply_groupby_numpy` with a 100k-row threshold (`_MIN_NUMPY_ROWS`).
- Benchmark (32k rows, 3 cols, GROUP BY city SUM salary): numpy is **slower** (42.5 ms) than pure Python (25.3 ms).
- Root cause: csvql stores rows as `list[dict]`; converting to numpy arrays is O(n) overhead that outweighs numpy's speed for < 100k rows.
- Conclusion: keep the numpy path for datasets >= 100k rows, but the default path is pure Python.

### 2. LRU cache for parsed files — big win
- Implemented `_PARSE_CACHE` (`OrderedDict`, `maxsize=8`).
- First query: ~104 ms. Subsequent queries: 0–22 ms.
- Key design choice: store `(headers, rows)` as `list[list]` (post-refactor).

### 3. Numba JIT evaluation — NOT helpful
- Tried JIT-compiling `_eval_where_list` with numba.
- Benchmark (10M calls): numba 30 ms vs pure Python 7 ms.
- Numba is 4× slower because the function is dominated by string→float conversion, which numba cannot accelerate.
- Even after type inference (values already float): converting `list[list]` → numpy array costs ~10ms, destroying any numba gain.
- GROUP BY is dominated by Python dict/tuple operations that numba can't accelerate.

### 4. Internal representation: list[dict] → list[list] — big win
- Replaced internal `list[dict]` with `list[list]` + column-index lookups.
- Memory: 4089 KB vs 8690 KB (−56 %).
- First-query speed: 86 ms (vs 104 ms) — 17 % improvement.
- Both `WHERE` filtering and `GROUP BY` operate on `list[list]`; conversion to `list[dict]` happens once at the API boundary.
- Results (`Result` class) are only populated on explicit user iteration; internal parallelism uses raw `list[list]`.

### 5. Type inference — done, all 53 tests pass
- Added `_infer_types()` that samples the first 100 rows and flags columns where >90 % of values parse as `float`.
- `_fallback_parse` stores pre-typed columns as native `float` during CSV reading, avoiding `float()` conversion cost in WHERE / GROUP BY / ORDER BY.
- Col types are stored in the parse cache alongside headers and rows.
- **Not yet applied in the C extension path** (C parser does its own type inference internally).

### 6. C extension (mmap + GIL release) — done, all 53 tests pass
- Fixed `setup.py` to compile `csvql._core` from C sources.
- Fixed `csv_parser.c`: duplicate `size` variable, `owns_headers` cleanup, `parse_buffer` header-skip logic for chunk mode.
- Fixed `query_executor.c`: `fnmatch.h` portability guard.
- Updated `python_bindings.c` to return `list[list]` (matching the refactored Python side), updated `py_filter_rows` to accept column index.
- Thread scaling: 1→4 threads gives ×1.3 speedup on 10K rows.
- Cold query ×1.8–2.5 faster than pure Python on 10K rows.
- All 53 tests pass.
