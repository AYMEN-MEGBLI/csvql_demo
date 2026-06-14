"""
benchmarks/bench_vs_pandas.py
─────────────────────────────
Compares csvql vs pandas on 3 queries of increasing complexity.
Run: python benchmarks/bench_vs_pandas.py
"""

import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from csvql import query

MEDIUM = "tests/data/sales_medium.csv"

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    print("pandas not installed — showing csvql times only\n")


def timeit(fn, label, runs=3):
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t) * 1000)
    avg = sum(times) / len(times)
    print(f"  {label:<30} {avg:>8.1f} ms")
    return avg


print("=" * 55)
print(f"  csvql benchmark — {MEDIUM}")
print("=" * 55)

# ── Query 1: SELECT + WHERE ────────────────────────────────────────────
print("\n[1] SELECT * WHERE amount > 1000")

t_csvql = timeit(
    lambda: query(f"SELECT * FROM '{MEDIUM}' WHERE amount > 1000"),
    "csvql (auto threads)"
)
t_csvql1 = timeit(
    lambda: query(f"SELECT * FROM '{MEDIUM}' WHERE amount > 1000", threads=1),
    "csvql (1 thread)"
)
if HAS_PANDAS:
    t_pd = timeit(
        lambda: pd.read_csv(MEDIUM).query("amount > 1000"),
        "pandas"
    )
    print(f"  Speedup vs pandas: {t_pd/t_csvql:.1f}x")

# ── Query 2: GROUP BY + SUM ────────────────────────────────────────────
print("\n[2] GROUP BY category, SUM(amount)")

t_csvql = timeit(
    lambda: query(
        f"SELECT category, SUM(amount) AS total FROM '{MEDIUM}' GROUP BY category"
    ),
    "csvql (auto threads)"
)
if HAS_PANDAS:
    t_pd = timeit(
        lambda: pd.read_csv(MEDIUM).groupby("category")["amount"].sum(),
        "pandas"
    )
    print(f"  Speedup vs pandas: {t_pd/t_csvql:.1f}x")

# ── Query 3: WHERE + GROUP BY + ORDER BY ──────────────────────────────
print("\n[3] WHERE + GROUP BY + ORDER BY DESC")

t_csvql = timeit(
    lambda: query(
        f"SELECT category, SUM(amount) AS total "
        f"FROM '{MEDIUM}' "
        f"WHERE amount > 100 "
        f"GROUP BY category "
        f"ORDER BY total DESC"
    ),
    "csvql (auto threads)"
)
if HAS_PANDAS:
    t_pd = timeit(
        lambda: (
            pd.read_csv(MEDIUM)
            .query("amount > 100")
            .groupby("category")["amount"]
            .sum()
            .sort_values(ascending=False)
        ),
        "pandas"
    )
    print(f"  Speedup vs pandas: {t_pd/t_csvql:.1f}x")

print("\n" + "=" * 55)
print("  Done.")