"""
Use Pythonic Design  au lieu dict simple
CsvqlResult — the object returned by every query() call.

"""

from __future__ import annotations

import csv
import io
import time
from typing import Any, Iterator


class CsvqlResult:
    """Holds query results with a rich Pythonic interface."""

    def __init__(
        self,
        rows:         list[dict],
        exec_time_ms: float = 0.0,
        sql:          str   = "",
    ) -> None:
        self._rows         = rows
        self.exec_time_ms  = round(exec_time_ms, 2)
        self.sql           = sql
        self._columns: list[str] = list(rows[0].keys()) if rows else []

   

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def col_count(self) -> int:
        return len(self._columns)

    @property
    def columns(self) -> list[str]:
        return list(self._columns)

    

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[dict]:
        return iter(self._rows)

    def __bool__(self) -> bool:
        return len(self._rows) > 0

    def __contains__(self, item: Any) -> bool:
        if isinstance(item, dict):
            return any(
                all(row.get(k) == v for k, v in item.items())
                for row in self._rows
            )
        return item in self._rows


    def __getitem__(self, key: int | slice) -> dict | "CsvqlResult":
        if isinstance(key, slice):
            return CsvqlResult(self._rows[key], self.exec_time_ms, self.sql)
        return self._rows[key]

    

    def __getattr__(self, name: str) -> list:
        if name.startswith("_"):
            raise AttributeError(name)
        cols = object.__getattribute__(self, "_columns")
        if name in cols:
            rows = object.__getattribute__(self, "_rows")
            return [row.get(name) for row in rows]
        raise AttributeError(
            f"CsvqlResult has no column '{name}'. "
            f"Available: {cols}"
        )

  

    def __add__(self, other: Any) -> "CsvqlResult":
        """result + row_dict  OR  result + other_result"""
        if isinstance(other, dict):
            return CsvqlResult(self._rows + [other], self.exec_time_ms, self.sql)
        if isinstance(other, CsvqlResult):
            return CsvqlResult(self._rows + other._rows, self.exec_time_ms, self.sql)
        return NotImplemented

    def __or__(self, other: "CsvqlResult") -> "CsvqlResult":
        """UNION — combines rows, removes exact duplicates."""
        if not isinstance(other, CsvqlResult):
            return NotImplemented
        seen:  list[dict] = []
        added: set[int]   = set()
        for row in self._rows + other._rows:
            h = hash(frozenset(row.items()))
            if h not in added:
                added.add(h)
                seen.append(row)
        return CsvqlResult(seen, self.exec_time_ms)

    def __and__(self, other: "CsvqlResult") -> "CsvqlResult":
        """INTERSECT — rows present in both results."""
        if not isinstance(other, CsvqlResult):
            return NotImplemented
        other_hashes = {hash(frozenset(r.items())) for r in other._rows}
        shared = [r for r in self._rows
                  if hash(frozenset(r.items())) in other_hashes]
        return CsvqlResult(shared, self.exec_time_ms)



    def __eq__(self, other: Any) -> bool:
        if isinstance(other, CsvqlResult):
            return self._rows == other._rows
        return NotImplemented

    def __lt__(self, other: "CsvqlResult") -> bool:
        return len(self) < len(other)

    def __gt__(self, other: "CsvqlResult") -> bool:
        return len(self) > len(other)

  

    def __enter__(self) -> "CsvqlResult":
        return self

    def __exit__(self, *_: Any) -> None:
        self._rows = []   # free memory



    def __str__(self) -> str:
        return self._render_table()

    def __repr__(self) -> str:
        return (
            f"CsvqlResult("
            f"rows={self.row_count}, "
            f"cols={self.col_count}, "
            f"exec_time={self.exec_time_ms}ms)"
        )


    def _render_table(self, max_rows: int = 50) -> str:   
        """ Render a pretty table  with a limit of 50 """
        if not self._rows:
            return "(empty result)"

        cols = self._columns

        widths = {c: len(c) for c in cols}
        for row in self._rows[:max_rows]:
            for c in cols:
                widths[c] = max(widths[c], len(str(row.get(c, ""))))

        sep = "┼".join("─" * (widths[c] + 2) for c in cols)
        top = "┌" + "┬".join("─" * (widths[c] + 2) for c in cols) + "┐"
        mid = "├" + sep + "┤"
        bot = "└" + "┴".join("─" * (widths[c] + 2) for c in cols) + "┘"

        def fmt_row(row: dict, fill: str = " ") -> str:
            cells = []
            for c in cols:
                v = str(row.get(c, ""))
                cells.append(f" {v:{fill}<{widths[c]}} ")
            return "│" + "│".join(cells) + "│"

        header = fmt_row({c: c for c in cols})
        lines  = [top, header, mid]
        for r in self._rows[:max_rows]:
            lines.append(fmt_row(r))
        if self.row_count > max_rows:
            lines.append(f"│ ... {self.row_count - max_rows} more rows ...")
        lines.append(bot)
        lines.append(
            f"{self.row_count} row(s) · "
            f"{self.col_count} col(s) · "
            f"{self.exec_time_ms} ms"
        )
        return "\n".join(lines)

    

    def print_table(self, max_rows: int = 50) -> None:
        "Print pretty table "
        print(self._render_table(max_rows))

    def to_list(self) -> list[dict]:
        """Return raw list of dicts."""
        return list(self._rows)

    def to_csv(self, filepath: str, encoding: str = "utf-8") -> None:
        """Export result to a CSV file."""
        if not self._rows:
            return
        with open(filepath, "w", newline="", encoding=encoding) as f:
            writer = csv.DictWriter(f, fieldnames=self._columns)
            writer.writeheader()
            writer.writerows(self._rows)

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        import json
        return json.dumps(self._rows, ensure_ascii=False, default=str)

    def filter(self, func: Any) -> "CsvqlResult":
        """result.filter(lambda r: r['amount'] > 100)"""
        return CsvqlResult(
            [r for r in self._rows if func(r)],
            self.exec_time_ms
        )

    def head(self, n: int = 5) -> "CsvqlResult":
        return self[:n]

    def tail(self, n: int = 5) -> "CsvqlResult":
        return CsvqlResult(self._rows[-n:], self.exec_time_ms)