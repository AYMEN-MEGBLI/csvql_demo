"""tests/integration/test_queries.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from csvql import query, QueryBuilder, CsvqlFileError

SALES = "tests/data/sales.csv"
CUST  = "tests/data/customers.csv"


# ── Basic SELECT ───────────────────────────────────────────────────────

def test_select_star():
    r = query(f"SELECT * FROM '{SALES}'")
    assert len(r) == 100
    assert "category" in r.columns


def test_select_columns():
    r = query(f"SELECT category, amount FROM '{SALES}'")
    assert r.columns == ["category", "amount"]


def test_select_limit():
    r = query(f"SELECT * FROM '{SALES}' LIMIT 5")
    assert len(r) == 5


def test_select_limit_offset():
    r_all   = query(f"SELECT * FROM '{SALES}' LIMIT 10")
    r_off   = query(f"SELECT * FROM '{SALES}' LIMIT 5 OFFSET 5")
    assert r_off[0] == r_all[5]


# ── WHERE ──────────────────────────────────────────────────────────────

def test_where_gt():
    r = query(f"SELECT * FROM '{SALES}' WHERE amount > 1000")
    for row in r:
        assert float(row["amount"]) > 1000


def test_where_eq():
    r = query(f"SELECT * FROM '{SALES}' WHERE category = Food")
    for row in r:
        assert row["category"] == "Food"


def test_where_like():
    r = query(f"SELECT * FROM '{SALES}' WHERE category LIKE F%")
    for row in r:
        assert row["category"].startswith("F")


def test_where_no_results():
    r = query(f"SELECT * FROM '{SALES}' WHERE amount > 999999")
    assert len(r) == 0
    assert not r


# ── GROUP BY ──────────────────────────────────────────────────────────

def test_groupby_count():
    r = query(
        f"SELECT category, COUNT(id) AS cnt FROM '{SALES}' GROUP BY category"
    )
    totals = sum(row["cnt"] for row in r)
    assert totals == 100   # all rows accounted for


def test_groupby_count_no_space():
    r = query(
        "SELECT category, COUNT(id) AS cnt FROM '" + SALES + "' GROUPBY category"
    )
    totals = sum(row["cnt"] for row in r)
    assert totals == 100


def test_groupby_count_string_col():
    r = query(
        "SELECT category, COUNT(category) AS cnt FROM '" + SALES + "' GROUP BY category"
    )
    totals = sum(row["cnt"] for row in r)
    assert totals == 100


def test_groupby_sum():
    r_all  = query(f"SELECT * FROM '{SALES}'")
    r_sum  = query(f"SELECT category, SUM(amount) AS total FROM '{SALES}' GROUP BY category")
    total  = sum(row["total"] for row in r_sum)
    direct = sum(float(row["amount"]) for row in r_all)
    assert abs(total - direct) < 0.01


# ── ORDER BY ──────────────────────────────────────────────────────────

def test_order_by_asc():
    r = query(f"SELECT * FROM '{SALES}' ORDER BY amount ASC LIMIT 5")
    amounts = [float(row["amount"]) for row in r]
    assert amounts == sorted(amounts)


def test_order_by_desc():
    r = query(f"SELECT * FROM '{SALES}' ORDER BY amount DESC LIMIT 5")
    amounts = [float(row["amount"]) for row in r]
    assert amounts == sorted(amounts, reverse=True)


# ── QueryBuilder ──────────────────────────────────────────────────────

def test_builder_basic():
    r = (QueryBuilder(SALES)
         .select("category", "amount")
         .where("amount > 500")
         .limit(10)
         .execute())
    assert len(r) <= 10
    for row in r:
        assert float(row["amount"]) > 500


def test_builder_call_override():
    b = QueryBuilder(SALES).select("*")
    r1 = b()
    r2 = b(_limit_val=5)
    assert len(r2) == 5


def test_builder_sql_method():
    sql = QueryBuilder(SALES).select("id", "amount").where("amount > 100").sql()
    assert "SELECT" in sql
    assert "WHERE" in sql


# ── Errors ────────────────────────────────────────────────────────────

def test_file_not_found():
    with pytest.raises(CsvqlFileError):
        query("SELECT * FROM 'nonexistent.csv'")


# ── Multi-thread consistency ───────────────────────────────────────────

def test_threads_same_result():
    sql = f"SELECT * FROM '{SALES}' WHERE amount > 100"
    r1 = query(sql, threads=1)
    r4 = query(sql, threads=4)
    assert len(r1) == len(r4)