"""
csvql/api/query.py
──────────────────
Public entry points:
  query(sql, threads=0)  → CsvqlResult
  QueryBuilder(filepath) → fluent builder → CsvqlResult
"""

from __future__ import annotations

import os
import time
from typing import Any

from ..parser.sql_parser import parse
from ..execution.parallel import parallel_query
from ..exceptions import CsvqlFileError
from .result import CsvqlResult


# ── query() ───────────────────────────────────────────────────────────

def query(sql: str, threads: int = 0, delimiter: str = ",") -> CsvqlResult:
    """
    Execute a SQL query on a CSV file.

    Parameters
    ----------
    sql       : SQL string — see README for supported syntax
    threads   : number of threads (0 = auto, uses all CPU cores)
    delimiter : CSV field delimiter (default: ',')

    Returns
    -------
    CsvqlResult with magic methods: len, iter, getitem, getattr, |, &, +
    """
    t0  = time.perf_counter()
    ast = parse(sql)

    filepath = ast["from"]
    if not os.path.isfile(filepath):
        raise CsvqlFileError(f"File not found: '{filepath}'")

    rows = parallel_query(filepath, ast, threads=threads, delimiter=delimiter)

    elapsed = (time.perf_counter() - t0) * 1000
    return CsvqlResult(rows, exec_time_ms=elapsed, sql=sql)


# ── QueryBuilder ──────────────────────────────────────────────────────

class QueryBuilder:
    """
    Fluent builder for constructing SQL queries programmatically.

    Example
    -------
    result = (
        QueryBuilder("sales.csv")
        .select("category", "SUM(amount) AS total")
        .where("amount > 100")
        .group_by("category")
        .order_by("total", desc=True)
        .limit(10)
        .execute()
    )
    """

    def __init__(self, filepath: str, delimiter: str = ",") -> None:
        self._filepath    = filepath
        self._delimiter   = delimiter
        self._select_cols : list[str] = ["*"]
        self._where       : str | None = None
        self._join        : str | None = None
        self._group_cols  : list[str] = []
        self._order_col   : str | None = None
        self._order_desc  : bool = False
        self._limit_val   : int | None = None
        self._offset_val  : int = 0
        self._threads     : int = 0

    def select(self, *cols: str) -> "QueryBuilder":
        self._select_cols = list(cols)
        return self

    def where(self, condition: str) -> "QueryBuilder":
        self._where = condition
        return self

    def join(self, other_file: str, on: str) -> "QueryBuilder":
        self._join = f"'{other_file}' ON {on}"
        return self

    def group_by(self, *cols: str) -> "QueryBuilder":
        self._group_cols = list(cols)
        return self

    def order_by(self, col: str, desc: bool = False) -> "QueryBuilder":
        self._order_col  = col
        self._order_desc = desc
        return self

    def limit(self, n: int, offset: int = 0) -> "QueryBuilder":
        self._limit_val  = n
        self._offset_val = offset
        return self

    def threads(self, n: int) -> "QueryBuilder":
        self._threads = n
        return self

    def _build_sql(self) -> str:
        parts: list[str] = []

        sel = ", ".join(self._select_cols) if self._select_cols else "*"
        parts.append(f"SELECT {sel}")
        parts.append(f"FROM '{self._filepath}'")

        if self._where:
            parts.append(f"WHERE {self._where}")
        if self._join:
            parts.append(f"JOIN {self._join}")
        if self._group_cols:
            parts.append(f"GROUP BY {', '.join(self._group_cols)}")
        if self._order_col:
            direction = "DESC" if self._order_desc else "ASC"
            parts.append(f"ORDER BY {self._order_col} {direction}")
        if self._limit_val is not None:
            parts.append(f"LIMIT {self._limit_val}")
        if self._offset_val:
            parts.append(f"OFFSET {self._offset_val}")

        return " ".join(parts)

    def execute(self) -> CsvqlResult:
        return query(self._build_sql(), threads=self._threads, delimiter=self._delimiter)

    def __call__(self, **overrides: Any) -> CsvqlResult:
        """
        Execute with temporary overrides.
        e.g. builder(where="amount > 500", limit=5)
        """
        tmp = QueryBuilder(self._filepath)
        tmp.__dict__.update(self.__dict__)
        for k, v in overrides.items():
            setattr(tmp, f"_{k}" if not k.startswith("_") else k, v)
        return tmp.execute()

    def sql(self) -> str:
        """Return the generated SQL string without executing."""
        return self._build_sql()

    def __repr__(self) -> str:
        return f"QueryBuilder(sql={self._build_sql()!r})"