"""tests/unit/test_sql_parser.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from csvql.parser.sql_parser import parse
from csvql.exceptions import CsvqlSyntaxError


def test_select_star():
    ast = parse("SELECT * FROM 'data.csv'")
    assert ast["columns"][0]["name"] == "*"
    assert ast["from"] == "data.csv"


def test_select_columns():
    ast = parse("SELECT name, amount FROM 'sales.csv'")
    assert len(ast["columns"]) == 2
    assert ast["columns"][0]["name"] == "name"
    assert ast["columns"][1]["name"] == "amount"


def test_where_gt():
    ast = parse("SELECT * FROM 'x.csv' WHERE amount > 100")
    assert ast["where"]["column"] == "amount"
    assert ast["where"]["op"]     == ">"
    assert ast["where"]["value"]  == "100"


def test_where_like():
    ast = parse("SELECT * FROM 'x.csv' WHERE name LIKE 'Food%'")
    assert ast["where"]["op"] == "LIKE"


def test_group_by():
    ast = parse("SELECT category, SUM(amount) FROM 'x.csv' GROUP BY category")
    assert ast["group_by"] == ["category"]
    agg = ast["columns"][1]
    assert agg["type"] == "agg"
    assert agg["func"] == "SUM"


def test_order_by_desc():
    ast = parse("SELECT * FROM 'x.csv' ORDER BY amount DESC")
    assert ast["order_by"]["column"] == "amount"
    assert ast["order_by"]["desc"]   is True


def test_limit_offset():
    ast = parse("SELECT * FROM 'x.csv' LIMIT 10 OFFSET 5")
    assert ast["limit"]  == 10
    assert ast["offset"] == 5


def test_agg_alias():
    ast = parse("SELECT SUM(amount) AS total FROM 'x.csv'")
    agg = ast["columns"][0]
    assert agg["alias"] == "total"


def test_missing_select():
    with pytest.raises(CsvqlSyntaxError):
        parse("FROM 'x.csv'")


def test_missing_from():
    with pytest.raises(CsvqlSyntaxError):
        parse("SELECT *")