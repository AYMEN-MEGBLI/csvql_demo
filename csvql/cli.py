"""
csvql/cli.py — Command-line interface
Usage:
  csvql "SELECT * FROM 'data.csv' WHERE amount > 100"
  csvql --interactive
  csvql -i
    > SELECT * FROM 'data.csv' LIMIT 3
    > SELECT category, SUM(amount) FROM 'sales.csv' GROUP BY category
"""

import argparse
import sys
import json


def run_one(sql, threads=0, no_stats=False, json_out=False, csv_out=None, delimiter=","):
    from csvql import query
    result = query(sql, threads=threads, delimiter=delimiter)

    if json_out:
        print(result.to_json())
    elif csv_out:
        result.to_csv(csv_out)
        if not no_stats:
            print(f"Exported {result.row_count} rows -> {csv_out} "
                  f"({result.exec_time_ms:.1f} ms)")
    else:
        result.print_table()
        if not no_stats:
            print()


def interactive(threads=0, no_stats=False, delimiter=","):
    try:
        import readline
        _has_readline = True
    except ImportError:
        _has_readline = False
    import os
    histfile = os.path.expanduser("~/.csvql_history")
    if _has_readline:
        try:
            readline.read_history_file(histfile)
        except FileNotFoundError:
            pass
        readline.set_history_length(500)

    print("csvql interactive — Ctrl+D ou 'exit' pour quitter")
    print(f"delimiter: '{delimiter}'")
    while True:
        try:
            sql = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not sql or sql.lower() in ("exit", "quit"):
            break
        if sql.startswith(".delimiter"):
            _, _, val = sql.partition(" ")
            delimiter = val.strip() or ","
            print(f"delimiter -> '{delimiter}'")
            continue
        try:
            run_one(sql, threads=threads, no_stats=no_stats, delimiter=delimiter)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)

    if _has_readline:
        readline.write_history_file(histfile)


def main():
    parser = argparse.ArgumentParser(
        prog="csvql",
        description="Run SQL queries on CSV files",
    )
    parser.add_argument("sql", nargs="?", help="SQL query string")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Interactive REPL mode")
    parser.add_argument("--threads", type=int, default=0,
                        help="Number of threads (0 = auto)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--csv", metavar="FILE",
                        help="Export result to CSV file")
    parser.add_argument("--delimiter", default=",",
                        help="CSV field delimiter (default: ',')")
    parser.add_argument("--no-stats", action="store_true",
                        help="Don't print row count / exec time")
    args = parser.parse_args()

    if args.interactive or args.sql is None:
        interactive(threads=args.threads, no_stats=args.no_stats, delimiter=args.delimiter)
        return

    try:
        run_one(args.sql, threads=args.threads, no_stats=args.no_stats,
                json_out=args.json, csv_out=args.csv, delimiter=args.delimiter)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()