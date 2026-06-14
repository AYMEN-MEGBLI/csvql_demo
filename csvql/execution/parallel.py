"""
csvql/execution/parallel.py
───────────────────────────
Splits a CSV file into N chunks and processes each chunk in a
separate thread via ThreadPoolExecutor.

The C extension (_core) releases the GIL during csv_parse_chunk,
enabling true parallelism despite Python's GIL.
Internal data format: list[list] instead of list[dict] for ~1.6×
speedup and lower memory. Converted to list[dict] at the API boundary.
"""

from __future__ import annotations

import fnmatch
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

try:
    from .. import _core  # C extension
    _C_AVAILABLE = True
except ImportError:
    _C_AVAILABLE = False

try:
    import numpy as _np
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False


# ── Chunk splitting ────────────────────────────────────────────────────

def _get_chunks(filepath: str, num_threads: int) -> list[tuple[int, int]]:
    file_size  = os.path.getsize(filepath)
    header_end = _core.header_end(filepath) if _C_AVAILABLE else _naive_header_end(filepath)

    data_size  = file_size - header_end
    if data_size <= 0:
        return [(0, file_size)]

    num_chunks = min(num_threads, data_size)
    chunk_size = data_size // num_chunks
    chunks: list[tuple[int, int]] = []
    start = header_end

    for i in range(num_chunks):
        if i == num_chunks - 1:
            end = file_size
        else:
            approx = start + chunk_size
            if approx >= file_size:
                end = file_size
            else:
                end = _core.find_newline(filepath, approx) if _C_AVAILABLE \
                      else _naive_find_newline(filepath, approx)
        chunks.append((start, end))
        if end >= file_size:
            break
        start = end

    return chunks


def _naive_header_end(filepath: str) -> int:
    with open(filepath, "rb") as f:
        line = f.readline()
        return len(line)


def _naive_find_newline(filepath: str, approx: int) -> int:
    with open(filepath, "rb") as f:
        f.seek(approx)
        f.readline()
        return f.tell()


# ── Type inference ─────────────────────────────────────────────────────

def _infer_types(
    filepath:  str,
    delimiter: str = ",",
    sample:    int = 100,
) -> list[type]:
    """Sample the first *sample* rows and infer per-column types.

    Returns a list of ``(str | float)`` — one per column. A column is
    considered numeric (float) when at least 90 % of its non‑empty
    values parse as ``float()``.
    """
    import csv
    ncols = 0
    counts: list[int] = []
    rows_seen = 0
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=delimiter)
        ncols = len(next(reader))  # header
        counts = [0] * ncols
        for i, fields in enumerate(reader):
            if i >= sample:
                break
            rows_seen = i + 1
            for j in range(min(len(fields), ncols)):
                v = fields[j].strip()
                if v:
                    try:
                        float(v)
                        counts[j] += 1
                    except ValueError:
                        pass

    threshold = max(1, int(rows_seen * 0.9))
    return [float if c >= threshold else str for c in counts]


# ── Chunk processor ────────────────────────────────────────────────────

def _parse_csv_line(line: str, delimiter: str) -> list[str]:
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            if in_quotes and i + 1 < len(line) and line[i + 1] == '"':
                current.append('"')
                i += 1
            else:
                in_quotes = not in_quotes
        elif c == delimiter and not in_quotes:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(c)
        i += 1
    fields.append("".join(current).strip())
    return fields


def _process_chunk(
    idx:       int,
    filepath:  str,
    start:     int,
    end:       int,
    headers:   list[str],
    where:     tuple[int, str, str] | None,
    delimiter: str = ",",
    col_types: list[type] | None = None,
) -> tuple[int, list[list]]:
    """
    Worker function executed in each thread.

    Returns (idx, rows) where *rows* is a list[list] (no dict overhead).
    *where* is (col_index, op, value) when the query has a WHERE clause.
    """
    if _C_AVAILABLE:
        rows: list[list] = _core.parse_chunk(filepath, start, end, headers)
        if where:
            col_idx, op, ref = where
            rows = _core.filter_rows(rows, col_idx, op, ref)
    else:
        rows = _fallback_parse(filepath, start, end, headers, where, delimiter, col_types)

    return idx, rows


def _build_row(fields: list[str], ncols: int,
               col_types: list[type] | None) -> list:
    row: list = [""] * ncols
    for i in range(min(len(fields), ncols)):
        raw = fields[i]
        if col_types and i < len(col_types) and col_types[i] is float:
            try:
                row[i] = float(raw)
            except (ValueError, TypeError):
                row[i] = raw
        else:
            row[i] = raw
    return row


def _fallback_parse(
    filepath: str, start: int, end: int,
    headers: list[str], where: tuple[int, str, str] | None,
    delimiter: str = ",",
    col_types: list[type] | None = None,
) -> list[list]:
    """Pure-Python fallback when C extension is not compiled.

    Returns list[list] — dicts are built only once at the API boundary.
    When *col_types* is provided, numeric columns are stored as ``float``
    instead of ``str``, saving conversions later.
    """
    import csv
    import io

    with open(filepath, "rb") as f:
        f.seek(start)
        data = f.read(end - start).decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(data), delimiter=delimiter)
    ncols = len(headers)
    rows: list[list] = []

    if where:
        col_idx, op, ref = where
        for fields in reader:
            if not fields or not any(fields):
                continue
            row = _build_row(fields, ncols, col_types)
            if _eval_where_list(row, col_idx, op, ref):
                rows.append(row)
    else:
        for fields in reader:
            if not fields or not any(fields):
                continue
            rows.append(_build_row(fields, ncols, col_types))

    return rows


def _eval_where_list(
    row: list, col_idx: int, op: str, ref: str,
    ref_f: float | None = None, ref_is_num: bool = False,
) -> bool:
    val = row[col_idx] if col_idx < len(row) else ""

    # Numeric fast path — val is pre-typed and ref is numeric
    if isinstance(val, (int, float)) and ref_is_num:
        lhs, rhs = float(val), ref_f
        if op == "=":   return lhs == rhs
        if op == "!=":  return lhs != rhs
        if op == ">":   return lhs >  rhs
        if op == ">=":  return lhs >= rhs
        if op == "<":   return lhs <  rhs
        if op == "<=":  return lhs <= rhs
        return False

    # LIKE — always string comparison
    if op == "LIKE":
        return fnmatch.fnmatch(str(val), str(ref).replace("%", "*"))

    # String or mixed-type comparison
    try:
        lhs, rhs = float(val), ref_f if ref_is_num else float(ref)
        if op == "=":   return lhs == rhs
        if op == "!=":  return lhs != rhs
        if op == ">":   return lhs >  rhs
        if op == ">=":  return lhs >= rhs
        if op == "<":   return lhs <  rhs
        if op == "<=":  return lhs <= rhs
    except (ValueError, TypeError):
        lhs_str, rhs_str = str(val), str(ref)
        if op == "=":   return lhs_str == rhs_str
        if op == "!=":  return lhs_str != rhs_str
        if op == ">":   return lhs_str >  rhs_str
        if op == ">=":  return lhs_str >= rhs_str
        if op == "<":   return lhs_str <  rhs_str
        if op == "<=":  return lhs_str <= rhs_str
    return False


# ── LRU cache for parsed files ─────────────────────────────────────────
# Cache stores (headers, rows_as_lists).

_PARSE_CACHE: OrderedDict[
    tuple[str, str], tuple[list[str], list[list], list[type] | None]
] = OrderedDict()
_PARSE_CACHE_MAXSIZE = 8


def _load_file(
    filepath:  str,
    delimiter: str = ",",
    threads:   int = 0,
) -> tuple[list[str], list[list]]:
    key = (filepath, delimiter)
    if key in _PARSE_CACHE:
        _PARSE_CACHE.move_to_end(key)
        return _PARSE_CACHE[key][:2]  # (headers, rows)

    headers, rows, col_types = _parse_file(filepath, delimiter, threads)

    _PARSE_CACHE[key] = (headers, rows, col_types)
    _PARSE_CACHE.move_to_end(key)
    if len(_PARSE_CACHE) > _PARSE_CACHE_MAXSIZE:
        _PARSE_CACHE.popitem(last=False)

    return headers, rows


def _parse_file(
    filepath:  str,
    delimiter: str,
    threads:   int,
) -> tuple[list[str], list[list], list[type] | None]:
    if threads <= 0:
        threads = os.cpu_count() or 4
    if _C_AVAILABLE:
        headers, _ = _core.read_headers(filepath)
        col_types = None
    else:
        with open(filepath, encoding="utf-8-sig") as f:
            headers = [h.strip().strip('"') for h in _parse_csv_line(f.readline(), delimiter)]
        col_types = _infer_types(filepath, delimiter)

    chunks = _get_chunks(filepath, threads)
    partial: list[list[list]] = [[] for _ in range(threads)]

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {
            pool.submit(_process_chunk, i, filepath, s, e, headers, None, delimiter, col_types): i
            for i, (s, e) in enumerate(chunks)
        }
        for future in as_completed(futures):
            idx, rows = future.result()
            partial[idx] = rows

    all_rows: list[list] = []
    for chunk_rows in partial:
        all_rows.extend(chunk_rows)
    return headers, all_rows, col_types


# ── Public parallel query ──────────────────────────────────────────────

def parallel_query(
    filepath:   str,
    ast:        dict[str, Any],
    threads:    int = 0,
    delimiter:  str = ",",
) -> list[dict]:
    """
    Execute a parsed SQL AST against *filepath*.

    Internally operates on list[list] for speed and memory efficiency;
    converts to list[dict] only once, after all processing is done.
    """
    if threads <= 0:
        threads = os.cpu_count() or 4

    headers, all_rows = _load_file(filepath, delimiter, threads)
    col_map: dict[str, int] = {h: i for i, h in enumerate(headers)}

    # ── WHERE filtering (list[list] — fast, no dict overhead) ──────────
    where = ast.get("where")
    if where:
        col_idx = col_map.get(where["column"])
        if col_idx is not None:
            op, ref = where["op"], where["value"]

            # Pre-convert ref to float when possible (avoids try/except per row)
            try:
                ref_f = float(ref)
                ref_is_num = True
            except ValueError:
                ref_f = None
                ref_is_num = False

            filtered: list[list] = []
            for row in all_rows:
                if _eval_where_list(row, col_idx, op, ref, ref_f, ref_is_num):
                    filtered.append(row)
            all_rows = filtered

    # ── GROUP BY (before projection — agg needs raw cols) ──────────────
    cols     = ast.get("columns", [])
    agg_cols = [c for c in cols if c.get("type") == "agg"]
    group_by = ast.get("group_by", [])

    is_groupby = bool(group_by or agg_cols)
    if is_groupby:
        all_rows = _apply_groupby(all_rows, headers, col_map, group_by, agg_cols)

    # ── ORDER BY (before projection — col names still exist) ───────────
    order = ast.get("order_by")
    if order:
        col = order["column"]
        desc = order.get("desc", False)

        if all_rows and isinstance(all_rows[0], list):
            col_idx = col_map.get(col)
            if col_idx is not None:
                def _sort_key_list(r):
                    v = r[col_idx] if col_idx < len(r) else ""
                    try:
                        return (0, float(v))
                    except (ValueError, TypeError):
                        return (0, v)
                all_rows.sort(key=_sort_key_list, reverse=desc)
        else:
            def _sort_key_dict(r):
                v = r.get(col)
                if v is None:
                    return (1, "")
                try:
                    return (0, float(v))
                except (ValueError, TypeError):
                    return (0, v)
            all_rows.sort(key=_sort_key_dict, reverse=desc)

    # ── LIMIT / OFFSET ─────────────────────────────────────────────────
    offset = ast.get("offset") or 0
    limit  = ast.get("limit")
    if offset:
        all_rows = all_rows[offset:]
    if limit is not None:
        all_rows = all_rows[:limit]

    # ── Final conversion: list[list] → list[dict] (once) ───────────────
    is_star    = any(c.get("name") == "*" for c in cols)
    plain_cols = [c for c in cols if c.get("type") == "column" and c["name"] != "*"]

    if all_rows and isinstance(all_rows[0], list):
        if is_star or not (plain_cols or agg_cols):
            all_rows = [dict(zip(headers, row)) for row in all_rows]
        else:
            all_rows = [
                {c["alias"] or c["name"]: row[col_map[c["name"]]]
                 for c in plain_cols}
                for row in all_rows
            ]
    else:
        # Already list[dict] (e.g. from GROUP BY) — just project
        if not is_star and (plain_cols or agg_cols):
            all_rows = [
                {c["alias"] or c["name"]: row.get(c["alias"] or c["name"])
                 for c in cols if c.get("type") in ("column", "agg")}
                for row in all_rows
            ]

    return all_rows


# ── GROUP BY (operates on list[list]) ──────────────────────────────────
_MIN_NUMPY_ROWS = 100_000


def _apply_groupby(
    rows:      list[list],
    headers:   list[str],
    col_map:   dict[str, int],
    group_by:  list[str],
    agg_cols:  list[dict],
) -> list[list | dict]:
    """
    Returns list[list] when group keys + aggregates fit the list format,
    or list[dict] when the output is naturally dict-shaped (numpy path).
    """
    if _NP_AVAILABLE and len(rows) >= _MIN_NUMPY_ROWS:
        return _apply_groupby_numpy(rows, headers, col_map, group_by, agg_cols)
    return _apply_groupby_py(rows, headers, col_map, group_by, agg_cols)


def _apply_groupby_py(
    rows:      list[list],
    headers:   list[str],
    col_map:   dict[str, int],
    group_by:  list[str],
    agg_cols:  list[dict],
) -> list[dict]:
    """Pure-Python GROUP BY — returns list[dict] (cardinality is small)."""
    gb_idx = [col_map[g] for g in group_by]

    agg_info: list[tuple[str, str, int | None]] = []
    for a in agg_cols:
        if a["func"] == "COUNT" or a["column"] == "*":
            agg_info.append((a["func"], a["alias"], None))
        else:
            agg_info.append((a["func"], a["alias"], col_map.get(a["column"])))

    groups: dict[tuple, dict] = {}

    for row in rows:
        key = tuple(row[i] for i in gb_idx)
        if key not in groups:
            acc: dict = {g: row[gb_idx[j]] for j, g in enumerate(group_by)}
            acc["__row_count"] = 0
            for _, alias, _ in agg_info:
                acc[f"__vals_{alias}"] = []
            groups[key] = acc
        acc = groups[key]
        acc["__row_count"] += 1
        for func, alias, col_idx in agg_info:
            if func == "COUNT" or col_idx is None:
                continue
            v = row[col_idx] if col_idx < len(row) else ""
            try:
                acc[f"__vals_{alias}"].append(float(v))
            except (TypeError, ValueError):
                pass

    result: list[dict] = []
    for acc in groups.values():
        out = {g: acc[g] for g in group_by}
        for func, alias, _ in agg_info:
            vals = acc[f"__vals_{alias}"]
            if func == "COUNT":
                out[alias] = acc["__row_count"]
            elif func == "SUM":
                out[alias] = sum(vals)
            elif func == "AVG":
                out[alias] = sum(vals) / len(vals) if vals else 0
            elif func == "MIN":
                out[alias] = min(vals) if vals else None
            elif func == "MAX":
                out[alias] = max(vals) if vals else None
        result.append(out)

    return result


def _apply_groupby_numpy(
    rows:      list[list],
    headers:   list[str],
    col_map:   dict[str, int],
    group_by:  list[str],
    agg_cols:  list[dict],
) -> list[dict]:
    import numpy as np

    gb_idx = [col_map[g] for g in group_by]

    keys = np.empty(len(rows), dtype=object)
    for i, row in enumerate(rows):
        keys[i] = tuple(row[j] for j in gb_idx)

    unique_keys, inverse = np.unique(keys, return_inverse=True)
    n_groups = len(unique_keys)

    result: list[dict] = []
    for uk in unique_keys:
        row: dict = {}
        for j, g in enumerate(group_by):
            row[g] = uk[j]
        result.append(row)

    for a in agg_cols:
        alias = a["alias"]
        func  = a["func"]

        if func == "COUNT":
            counts = np.bincount(inverse, minlength=n_groups)
            for i, row in enumerate(result):
                row[alias] = int(counts[i])
            continue

        col_name = a["column"]
        if col_name == "*":
            continue

        col_idx = col_map.get(col_name)
        if col_idx is None:
            continue

        vals = np.zeros(len(rows), dtype=float)
        for i, r in enumerate(rows):
            v = r[col_idx] if col_idx < len(r) else ""
            try:
                vals[i] = float(v) if v not in (None, "") else np.nan
            except (TypeError, ValueError):
                vals[i] = np.nan

        valid = ~np.isnan(vals)

        if func == "SUM":
            sums = np.zeros(n_groups, dtype=float)
            np.add.at(sums, inverse, np.where(valid, vals, 0.0))
            for i, row in enumerate(result):
                row[alias] = sums[i]
        elif func == "AVG":
            sums = np.zeros(n_groups, dtype=float)
            cnts = np.zeros(n_groups, dtype=float)
            np.add.at(sums, inverse, np.where(valid, vals, 0.0))
            np.add.at(cnts, inverse, valid.astype(float))
            for i, row in enumerate(result):
                row[alias] = sums[i] / cnts[i] if cnts[i] > 0 else 0.0
        elif func == "MIN":
            mins = np.full(n_groups, np.inf)
            np.minimum.at(mins, inverse, np.where(valid, vals, np.inf))
            for i, row in enumerate(result):
                row[alias] = None if np.isinf(mins[i]) else mins[i]
        elif func == "MAX":
            maxs = np.full(n_groups, -np.inf)
            np.maximum.at(maxs, inverse, np.where(valid, vals, -np.inf))
            for i, row in enumerate(result):
                row[alias] = None if np.isneginf(maxs[i]) else maxs[i]

    return result
