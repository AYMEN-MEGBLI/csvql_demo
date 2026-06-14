"""
benchmarks/bench_optimisations.py
────────────────────────────────
Compare csvql performance across query types, dataset sizes, and
optimisation features (first-query vs cached, type inference, etc.).

Usage:
  python benchmarks/bench_optimisations.py             # full run
  python benchmarks/bench_optimisations.py --quick     # smaller dataset

Output: a comparison table printed to stdout.
"""

import time
import os
import sys
import csv
import random
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from csvql import query
from csvql.execution.parallel import _PARSE_CACHE, _infer_types

# ── Colours (optional, for nicer terminal output) ─────────────────────
GREEN  = "\033[92m" if sys.stdout.isatty() else ""
YELLOW = "\033[93m" if sys.stdout.isatty() else ""
CYAN   = "\033[96m" if sys.stdout.isatty() else ""
BOLD   = "\033[1m"  if sys.stdout.isatty() else ""
RESET  = "\033[0m"  if sys.stdout.isatty() else ""

SEP = "─" * 90


def generate_csv(path: str, n: int):
    """Generate a sales CSV with *n* rows."""
    CATEGORIES = ["Food", "Tech", "Clothing", "Books", "Sports"]
    REGIONS    = ["North", "South", "East", "West"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "category", "region", "amount", "date"])
        for i in range(n):
            writer.writerow([
                i + 1,
                random.choice(CATEGORIES),
                random.choice(REGIONS),
                round(random.uniform(10, 5000), 2),
                f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            ])
    print(f"  {YELLOW}✓{RESET} Generated {path} ({n:,} rows)")


def clear_cache():
    _PARSE_CACHE.clear()


# ── Queries to benchmark ──────────────────────────────────────────────
QUERIES = [
    ("Q1: SELECT *",            "SELECT * FROM '{path}'"),
    ("Q2: SELECT * WHERE",      "SELECT * FROM '{path}' WHERE amount > 1000"),
    ("Q3: GROUP BY + SUM",      "SELECT category, SUM(amount) AS total FROM '{path}' GROUP BY category"),
    ("Q4: GROUP BY + COUNT",    "SELECT region, COUNT(*) AS cnt FROM '{path}' GROUP BY region"),
    ("Q5: ORDER BY",            "SELECT * FROM '{path}' ORDER BY amount DESC"),
    ("Q6: WHERE + GROUP + ORDER",
     "SELECT category, SUM(amount) AS total FROM '{path}' WHERE amount > 100 GROUP BY category ORDER BY total DESC"),
    ("Q7: LIMIT + OFFSET",      "SELECT * FROM '{path}' LIMIT 100 OFFSET 50"),
    ("Q8: GROUP BY 2 cols",     "SELECT region, category, SUM(amount) AS total FROM '{path}' GROUP BY region, category"),
]


def ms(fn):
    """Time a function and return milliseconds."""
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def run_bench(path: str, label: str):
    print(f"\n{SEP}")
    print(f"  {BOLD}{label}{RESET}")
    print(SEP)

    rows = []
    for qname, qsql in QUERIES:
        sql = qsql.format(path=path)

        # ── Cold (no cache) ─────────────────────────────────────────
        clear_cache()
        t_cold = ms(lambda: query(sql))

        # ── Hot (cached) — run twice, take second ──────────────────
        query(sql)  # warm up
        t_hot = ms(lambda: query(sql))

        rows.append((qname, t_cold, t_hot))

    # ── Print table ────────────────────────────────────────────────
    hdr = f"  {'Query':<32} {'Cold (ms)':>10} {'Hot (ms)':>10} {'Speedup':>8}"
    print(f"\n  {hdr}")
    print(f"  {'─' * len(hdr)}")
    for qname, cold, hot in rows:
        sp = cold / hot if hot > 0 else 0
        cold_s = f"{CYAN}{cold:>8.1f}{RESET}" if cold > 0 else f"{cold:>8.1f}"
        hot_s  = f"{GREEN}{hot:>8.1f}{RESET}"
        sp_s   = f"{sp:>5.1f}x" if sp > 1 else f"{YELLOW}{sp:>5.1f}x{RESET}"
        print(f"  {qname:<32} {cold_s:>10} {hot_s:>10} {sp_s:>8}")
    print()

    return rows


def run_parse_bench(paths: list[str]):
    """Compare parse time without vs with type inference
    (indirect: type inference is baked into _parse_file)."""

    print(f"\n{SEP}")
    print(f"  {BOLD}Parse performance (first query on each file){RESET}")
    print(SEP)

    hdr = f"  {'File':<30} {'Rows':>10} {'Parse (ms)':>10} {'Type inference (ms)':>20}"
    print(f"\n  {hdr}")
    print(f"  {'─' * len(hdr)}")

    for fp in paths:
        nrows = sum(1 for _ in open(fp, encoding="utf-8")) - 1
        # Time type inference alone
        clear_cache()
        t_infer = ms(lambda: _infer_types(fp, ","))

        # Time full parse
        clear_cache()
        t_parse = ms(lambda: query(f"SELECT * FROM '{fp}'"))
        # Subtract the overhead of the query layer (rough)
        t_parse -= 0.5

        print(f"  {os.path.basename(fp):<30} {nrows:>10,} {t_parse:>10.1f} {t_infer:>20.3f}")

    print()


def run_thread_bench(path: str):
    """Compare single-thread vs multi-thread performance."""
    print(f"\n{SEP}")
    print(f"  {BOLD}Thread scaling — {os.path.basename(path)}{RESET}")
    print(SEP)

    import os as _os
    ncpu = _os.cpu_count() or 4
    sql = f"SELECT region, COUNT(*) AS cnt FROM '{path}' GROUP BY region"

    hdr = f"  {'Threads':<10} {'Time (ms)':>10} {'Speedup':>8}"
    print(f"\n  {hdr}")
    print(f"  {'─' * len(hdr)}")

    base_t = None
    for t in [1, 2, 4, ncpu]:
        clear_cache()
        t_ms = ms(lambda: query(sql, threads=t))
        if base_t is None:
            base_t = t_ms
        sp = base_t / t_ms
        sp_s = f"{sp:>5.1f}x" if sp >= 1 else f"{YELLOW}{sp:>5.1f}x{RESET}"
        print(f"  {t:<10} {t_ms:>10.1f}  {sp_s:>8}")

    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="Use 1K and 10K datasets instead of 10K+100K")
    args = parser.parse_args()

    print(f"\n  {BOLD}{'═' * 54}{RESET}")
    print(f"  {BOLD}  csvql optimisation benchmarks{RESET}")
    print(f"  {BOLD}{'═' * 54}{RESET}")
    print(f"  Date: {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"  C ext: {__import__('csvql.execution.parallel', fromlist=['_C_AVAILABLE'])._C_AVAILABLE}")
    print(f"  numpy: {__import__('csvql.execution.parallel', fromlist=['_NP_AVAILABLE'])._NP_AVAILABLE}")

    if args.quick:
        sizes = [1_000, 10_000]
    else:
        sizes = [10_000, 100_000]

    tmpdir = "/tmp/csvql_bench"
    paths = []
    for n in sizes:
        fp = f"{tmpdir}/sales_{n}.csv"
        if not os.path.exists(fp):
            generate_csv(fp, n)
        paths.append(fp)

    # ── Per-size benchmarks ─────────────────────────────────────────
    all_results = {}
    for fp in paths:
        label = os.path.basename(fp)
        all_results[label] = run_bench(fp, f"Benchmark — {label}")

    # ── Parse bench ─────────────────────────────────────────────────
    run_parse_bench(paths)

    # ── Thread scaling ─────────────────────────────────────────────
    run_thread_bench(paths[-1])  # largest file

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  {BOLD}Summary — Cold query times per dataset{RESET}")
    print(f"{SEP}")
    cols = [os.path.basename(p) for p in paths]
    hdr = f"  {'Query':<32}" + "".join(f" {c:>14}" for c in cols)
    print(f"\n  {hdr}")
    print(f"  {'─' * len(hdr)}")

    from collections import defaultdict
    by_q: dict[str, list] = defaultdict(list)
    for label, results in all_results.items():
        for qname, cold, _ in results:
            by_q[qname].append((label, cold))

    for qname, vals in by_q.items():
        cells = "".join(f" {t:>14.1f}" for _, t in vals)
        print(f"  {qname:<32}{cells}")

    print(f"\n  {GREEN}Done.{RESET}\n")


if __name__ == "__main__":
    main()
