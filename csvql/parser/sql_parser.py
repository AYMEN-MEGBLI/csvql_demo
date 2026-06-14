"""
csvql/parser/sql_parser.py
──────────────────────────
Converts a SQL string into an AST dict consumed by the execution layer.

Supported syntax (MVP):
    SELECT col1, AGG(col2) [AS alias], ...
    FROM   'filepath.csv'
    [WHERE  col op value]
    [JOIN   'other.csv' ON left_col = right_col]
    [GROUP  BY col1, col2]
    [ORDER  BY col [ASC|DESC]]
    [LIMIT  n [OFFSET m]]

Returns a plain dict — no external dependencies.
"""

from __future__ import annotations

import re
from typing import Any

from ..exceptions import CsvqlSyntaxError

# ── Regex helpers ──────────────────────────────────────────────────────

_AGG_RE    = re.compile(r"^(SUM|COUNT|AVG|MIN|MAX)\((.+?)\)(?:\s+AS\s+(\w+))?$",
                        re.IGNORECASE)
_ALIAS_RE  = re.compile(r"^(.+?)\s+AS\s+(\w+)$", re.IGNORECASE)
_WHERE_RE  = re.compile(
    r"^(.+?)\s*(>=|<=|!=|<>|>|<|=|LIKE)\s*(.+)$", re.IGNORECASE
)
_JOIN_RE   = re.compile(
    r"JOIN\s+'([^']+)'\s+ON\s+(\w+)\s*=\s*(\w+)", re.IGNORECASE
)
_QUOTED_RE = re.compile(r"^['\"](.+)['\"]$")


def _strip_quotes(s: str) -> str:
    m = _QUOTED_RE.match(s.strip())
    return m.group(1) if m else s.strip()


def _parse_column(raw: str) -> dict[str, Any]:
    """Parse a single SELECT column expression (may include AGG / AS)."""
    raw = raw.strip()

    # AGG(col) [AS alias]
    m = _AGG_RE.match(raw)
    if m:
        return {
            "type":   "agg",
            "func":   m.group(1).upper(),
            "column": m.group(2).strip(),
            "alias":  m.group(3) or f"{m.group(1).upper()}({m.group(2).strip()})",
        }

    # col AS alias
    m = _ALIAS_RE.match(raw)
    if m:
        return {"type": "column", "name": m.group(1).strip(), "alias": m.group(2)}

    # plain column or *
    return {"type": "column", "name": raw, "alias": None}


def _parse_where(clause: str) -> dict[str, Any]:
    """Parse a WHERE clause string into {column, op, value}."""
    clause = clause.strip()
    m = _WHERE_RE.match(clause)
    if not m:
        raise CsvqlSyntaxError(f"Cannot parse WHERE clause: '{clause}'")

    raw_val = m.group(3).strip()
    value   = _strip_quotes(raw_val)

    op_map = {"<>": "!="}
    op = op_map.get(m.group(2), m.group(2))

    return {
        "column": m.group(1).strip(),
        "op":     op,
        "value":  value,
    }


def _tokenize(sql: str) -> dict[str, str]:
    """
    Split SQL into keyword sections.
    Returns a dict: {"SELECT": "...", "FROM": "...", "WHERE": "...", ...}
    """
    keywords = ["SELECT", "FROM", "WHERE", "JOIN",
                 "GROUP BY", "GROUPBY",
                 "ORDER BY", "ORDERBY",
                 "LIMIT", "OFFSET"]

    # Build a pattern that splits on any keyword (case-insensitive)
    pattern = "|".join(
        r"\b" + re.escape(kw) + r"\b" for kw in keywords
    )
    tokens: dict[str, str] = {}
    parts = re.split(f"({pattern})", sql, flags=re.IGNORECASE)

    current_key: str | None = None
    for part in parts:
        upper = part.strip().upper()
        if upper in [kw.upper() for kw in keywords]:
            # Normalise GROUP BY / ORDER BY
            current_key = upper.replace(" ", "_")
        elif current_key:
            tokens[current_key] = tokens.get(current_key, "") + part.strip()

    # Normalise alternative spellings (e.g. GROUPBY → GROUP_BY)
    for alt, canonical in {"GROUPBY": "GROUP_BY", "ORDERBY": "ORDER_BY"}.items():
        if alt in tokens:
            tokens[canonical] = tokens.pop(alt)

    return tokens


# ── Public API ─────────────────────────────────────────────────────────

def parse(sql: str) -> dict[str, Any]:
    """
    Parse a SQL string and return an AST dict.

    Example output:
    {
        "type":      "SELECT",
        "columns":   [{"type": "column", "name": "id", "alias": None},
                      {"type": "agg", "func": "SUM", "column": "amount",
                       "alias": "total"}],
        "from":      "sales.csv",
        "where":     {"column": "amount", "op": ">", "value": "100"},
        "join":      {"file": "customers.csv", "left": "id", "right": "id"},
        "group_by":  ["category"],
        "order_by":  {"column": "total", "desc": True},
        "limit":     10,
        "offset":    0,
    }
    """
    sql = sql.strip().rstrip(";")
    tokens = _tokenize(sql)

    ast: dict[str, Any] = {"type": "SELECT"}

    # ── SELECT columns ─────────────────────────────────────────────────
    select_raw = tokens.get("SELECT", "").strip()
    if not select_raw:
        raise CsvqlSyntaxError("Missing SELECT clause")

    if select_raw == "*":
        ast["columns"] = [{"type": "column", "name": "*", "alias": None}]
    else:
        ast["columns"] = [_parse_column(c) for c in select_raw.split(",")]

    # ── FROM ───────────────────────────────────────────────────────────
    from_raw = tokens.get("FROM", "").strip()
    if not from_raw:
        raise CsvqlSyntaxError("Missing FROM clause")
    ast["from"] = _strip_quotes(from_raw.split()[0])

    # ── WHERE ──────────────────────────────────────────────────────────
    if "WHERE" in tokens:
        ast["where"] = _parse_where(tokens["WHERE"])
    else:
        ast["where"] = None

    # ── JOIN ───────────────────────────────────────────────────────────
    if "JOIN" in tokens:
        m = _JOIN_RE.search("JOIN " + tokens["JOIN"])
        if m:
            ast["join"] = {
                "file":  m.group(1),
                "left":  m.group(2),
                "right": m.group(3),
            }
        else:
            raise CsvqlSyntaxError(f"Invalid JOIN syntax: '{tokens['JOIN']}'")
    else:
        ast["join"] = None

    # ── GROUP BY ───────────────────────────────────────────────────────
    if "GROUP_BY" in tokens:
        ast["group_by"] = [c.strip() for c in tokens["GROUP_BY"].split(",")]
    else:
        ast["group_by"] = []

    # ── ORDER BY ───────────────────────────────────────────────────────
    if "ORDER_BY" in tokens:
        parts = tokens["ORDER_BY"].strip().split()
        col   = parts[0]
        desc  = len(parts) > 1 and parts[1].upper() == "DESC"
        ast["order_by"] = {"column": col, "desc": desc}
    else:
        ast["order_by"] = None

    # ── LIMIT / OFFSET ─────────────────────────────────────────────────
    ast["limit"]  = int(tokens["LIMIT"].strip())  if "LIMIT"  in tokens else None
    ast["offset"] = int(tokens["OFFSET"].strip()) if "OFFSET" in tokens else 0

    return ast