

"""tests/unit/test_result.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from csvql.api.result import CsvqlResult


ROWS = [
    {"category": "Food",  "amount": 100.0},
    {"category": "Tech",  "amount": 500.0},
    {"category": "Books", "amount": 50.0},
]


def make() -> CsvqlResult:
    return CsvqlResult(ROWS[:], exec_time_ms=12.5)


def test_len():
    assert len(make()) == 3


def test_iter():
    items = list(make())
    assert items[0]["category"] == "Food"


def test_bool_true():
    assert bool(make())


def test_bool_false():
    assert not bool(CsvqlResult([]))


def test_getitem_single():
    assert make()[0] == {"category": "Food", "amount": 100.0}


def test_getitem_slice():
    r = make()[0:2]
    assert isinstance(r, CsvqlResult)
    assert len(r) == 2


def test_contains_true():
    assert {"category": "Food"} in make()


def test_contains_false():
    assert {"category": "XYZ"} not in make()


def test_getattr_column():
    amounts = make().amount
    assert amounts == [100.0, 500.0, 50.0]


def test_getattr_missing():
    import pytest
    with pytest.raises(AttributeError):
        _ = make().nonexistent


def test_add_row():
    r = make() + {"category": "Sports", "amount": 200.0}
    assert len(r) == 4


def test_add_result():
    r = make() + make()
    assert len(r) == 6


def test_or_union():
    a = CsvqlResult([{"id": 1}, {"id": 2}])
    b = CsvqlResult([{"id": 2}, {"id": 3}])
    r = a | b
    assert len(r) == 3   # deduped


def test_and_intersect():
    a = CsvqlResult([{"id": 1}, {"id": 2}])
    b = CsvqlResult([{"id": 2}, {"id": 3}])
    r = a & b
    assert len(r) == 1
    assert r[0]["id"] == 2


def test_eq():
    assert make() == make()


def test_lt_gt():
    a = CsvqlResult(ROWS[:2])
    b = make()
    assert a < b
    assert b > a


def test_repr():
    r = repr(make())
    assert "CsvqlResult" in r
    assert "rows=3" in r


def test_to_list():
    assert make().to_list() == ROWS


def test_filter():
    r = make().filter(lambda row: row["amount"] > 99)
    assert len(r) == 2


def test_head():
    assert len(make().head(2)) == 2


def test_tail():
    assert make().tail(1)[0]["category"] == "Books"


def test_context_manager():
    with CsvqlResult(ROWS[:]) as r:
        assert len(r) == 3
    assert len(r) == 0   # freed


def test_print_table(capsys):
    make().print_table()
    out = capsys.readouterr().out
    assert "Food" in out
    assert "Tech" in out


def test_to_json():
    import json
    r = make()
    data = json.loads(r.to_json())
    assert len(data) == 3