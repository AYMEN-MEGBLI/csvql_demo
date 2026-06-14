"""
benchmarks/bench_vs_duckdb_polars.py
────────────────────────────────────
Compare csvql vs DuckDB vs Polars on identical queries.

Usage:
  python benchmarks/bench_vs_duckdb_polars.py          # full suite
  python benchmarks/bench_vs_duckdb_polars.py --quick  # 10K only
"""

import time
import os
import sys
import csv
import random
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from csvql.execution.parallel import _PARSE_CACHE

GREEN  = "\033[92m" if sys.stdout.isatty() else ""
YELLOW = "\033[93m" if sys.stdout.isatty() else ""
CYAN   = "\033[96m" if sys.stdout.isatty() else ""
BOLD   = "\033[1m"  if sys.stdout.isatty() else ""
RESET  = "\033[0m"  if sys.stdout.isatty() else ""
SEP    = "═" * 80


def generate_csv(path: str, n: int):
    CATEGORIES = ["Food", "Tech", "Clothing", "Books", "Sports"]
    REGIONS    = ["North", "South", "East", "West"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "region", "amount", "date"])
        for i in range(n):
            w.writerow([
                i + 1,
                random.choice(CATEGORIES),
                random.choice(REGIONS),
                round(random.uniform(10, 5000), 2),
                f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            ])
    print(f"  {YELLOW}✓{RESET} {path} ({n:,} rows)")


def ms(fn, label=""):
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def clear_cache():
    _PARSE_CACHE.clear()


QUERIES = [
    ("Q1  SELECT *",          "SELECT * FROM '{path}'"),
    ("Q2  SELECT * WHERE",    "SELECT * FROM '{path}' WHERE amount > 1000"),
    ("Q3  GROUP BY SUM",      "SELECT category, SUM(amount) AS total FROM '{path}' GROUP BY category"),
    ("Q4  GROUP BY COUNT",    "SELECT region, COUNT(*) AS cnt FROM '{path}' GROUP BY region"),
    ("Q5  ORDER BY",          "SELECT * FROM '{path}' ORDER BY amount DESC"),
    ("Q6  WHERE+GROUP+ORDER",
     "SELECT category, SUM(amount) AS total FROM '{path}' WHERE amount > 100 GROUP BY category ORDER BY total DESC"),
    ("Q7  GROUP BY 2 cols",   "SELECT region, category, SUM(amount) AS t FROM '{path}' GROUP BY region, category"),
]


def run_csvql(sql, path):
    from csvql import query
    clear_cache()
    return ms(lambda: query(sql.format(path=path)))


def run_duckdb(sql, path):
    import duckdb
    sql_f = sql.format(path=path)
    return ms(lambda: duckdb.sql(sql_f).fetchall())


def run_polars(sql, path):
    import polars as pl
    path_f = path
    return ms(lambda: pl.read_csv(path_f).pipe(
        lambda df: _polars_query(df, sql)
    ).collect())


def _polars_query(df, sql):
    """Translate SQL string to Polars lazy ops."""
    import polars as pl

    has_where = "WHERE" in sql
    has_group = "GROUP BY" in sql
    has_order = "ORDER BY" in sql

    lazy = df.lazy()

    if has_where:
        if "amount > 1000" in sql:
            lazy = lazy.filter(pl.col("amount") > 1000)
        elif "amount > 100" in sql:
            lazy = lazy.filter(pl.col("amount") > 100)

    if has_group:
        if "GROUP BY category" in sql:
            if "SUM(amount)" in sql:
                lazy = lazy.group_by("category").agg(pl.sum("amount").alias("total"))
            elif "SUM(amount) AS t" in sql:
                lazy = lazy.group_by("category").agg(pl.sum("amount").alias("t"))
        elif "GROUP BY region" in sql:
            if "COUNT(*)" in sql:
                lazy = lazy.group_by("region").agg(pl.count().alias("cnt"))
        elif "GROUP BY region, category" in sql:
            lazy = lazy.group_by("region", "category").agg(pl.sum("amount").alias("t"))

    if has_order:
        if "ORDER BY amount DESC" in sql:
            lazy = lazy.sort("amount", descending=True)
        elif "ORDER BY total DESC" in sql:
            lazy = lazy.sort("total", descending=True)

    return lazy


def bench(path: str, label: str):
    print(f"\n  {BOLD}{label}{RESET}")
    print(f"  {'─' * 60}")

    hdr = f"  {'Query':<28} {'csvql':>10} {'DuckDB':>10} {'Polars':>10}"
    print(f"\n  {hdr}")
    print(f"  {'─' * len(hdr)}")

    rows = []
    for qname, qsql in QUERIES:
        t_csvql = run_csvql(qsql, path)
        t_duck  = run_duckdb(qsql, path)
        t_pol   = run_polars(qsql, path)
        rows.append((qname, t_csvql, t_duck, t_pol))

        # Highlight fastest
        times = [t_csvql, t_duck, t_pol]
        best  = min(times)
        def fmt(t):
            s = f"{t:>8.1f}"
            return f"{GREEN}{s}{RESET}" if t == best else s

        print(f"  {qname:<28} {fmt(t_csvql):>10} {fmt(t_duck):>10} {fmt(t_pol):>10}")

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    print(f"\n  {BOLD}csvql vs DuckDB vs Polars{RESET}")
    print(f"  {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"  C ext: {__import__('csvql.execution.parallel', fromlist=['_C_AVAILABLE'])._C_AVAILABLE}")

    sizes = [10_000] if args.quick else [10_000, 100_000]
    tmpdir = "/tmp/csvql_bench"
    paths = []
    for n in sizes:
        fp = f"{tmpdir}/sales_{n}.csv"
        if not os.path.exists(fp):
            generate_csv(fp, n)
        paths.append(fp)

    all_data = {}
    for fp in paths:
        all_data[os.path.basename(fp)] = bench(fp, os.path.basename(fp))

    # Summary
    print(f"\n  {'─' * 60}")
    print(f"  {BOLD}Summary — cold query times (ms){RESET}")
    print(f"  {'─' * 60}")
    cols = [os.path.basename(p) for p in paths]
    for engine in ["csvql", "DuckDB", "Polars"]:
        print(f"\n  {BOLD}{engine}:{RESET}")
        hdr = f"    {'Query':<26}" + "".join(f" {c:>12}" for c in cols)
        print(f"    {hdr}")
        print(f"    {'─' * len(hdr)}")
        for qname, _, _, _ in zip(QUERIES, *[[]]):
            pass
        for qi, (qname, _, _, _) in enumerate(QUERIES):
            cells = ""
            for label, results in all_data.items():
                r = results[qi]
                idx = ["csvql", "DuckDB", "Polars"].index(engine) + 1
                cells += f" {r[idx]:>12.1f}"
            print(f"    {qname:<26}{cells}")

    print(f"\n  {GREEN}Done.{RESET}\n")


if __name__ == "__main__":
    main()
