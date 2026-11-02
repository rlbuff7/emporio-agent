import json

import pytest

from emporio.etl import CSV_PREFIX, TABLES, build_database


def test_row_counts_match_csvs(tmp_path):
    counts = build_database(db_path=tmp_path / "emporio.db")
    assert counts == {
        "categories": 9,
        "customers": 50,
        "products": 65,
        "orders": 20,
        "order_items": 22,
        "promotions": 25,
    }


def test_no_orphan_foreign_keys(conn):
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_empty_strings_become_null(conn):
    tracking, eta, notes = conn.execute(
        "SELECT tracking_code, estimated_delivery, notes FROM orders WHERE order_id = 9"
    ).fetchone()
    assert (tracking, eta, notes) == (None, None, None)


def test_specs_are_queryable_json(conn):
    for (specs,) in conn.execute("SELECT specs FROM products"):
        assert isinstance(json.loads(specs), dict)
    strings = conn.execute(
        "SELECT json_extract(specs, '$.strings') FROM products WHERE product_id = 81"
    ).fetchone()[0]
    assert strings == "nylon"


def test_prices_are_numeric(conn):
    price = conn.execute(
        "SELECT price_brl FROM products WHERE name LIKE 'Takamine GD20%'"
    ).fetchone()[0]
    assert price == pytest.approx(2199.0)


def test_all_promotions_loaded_but_only_active_ones_in_view(conn):
    assert conn.execute("SELECT COUNT(*) FROM promotions WHERE is_active = 0").fetchone()[0] == 21
    active = conn.execute(
        "SELECT product_id, discount_percent FROM active_promotions ORDER BY product_id"
    ).fetchall()
    assert active == [(90, 18), (94, 8), (121, 20), (127, 10)]


def test_view_keeps_one_promotion_per_product(conn):
    rows = conn.execute(
        "SELECT product_id, COUNT(*) FROM active_promotions GROUP BY product_id HAVING COUNT(*) > 1"
    ).fetchall()
    assert rows == []


def test_view_picks_highest_discount_when_two_are_active(conn):
    conn.execute(
        "INSERT INTO promotions VALUES (900, 90, 5, 'Test', 1), (901, 90, 25, 'Test', 1)"
    )
    assert conn.execute(
        "SELECT promotion_id, discount_percent FROM active_promotions WHERE product_id = 90"
    ).fetchone() == (901, 25)
    conn.rollback()


def test_invalid_csv_leaves_no_partial_database(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for table, columns in TABLES.items():
        header = ",".join(source for _, source, _ in columns)
        (raw / f"{CSV_PREFIX}{table}.csv").write_text(header + "\n", encoding="utf-8")
    (raw / f"{CSV_PREFIX}categories.csv").write_text(
        "category_id,name,description\nnot-a-number,X,Y\n", encoding="utf-8"
    )
    db = tmp_path / "emporio.db"
    with pytest.raises(ValueError):
        build_database(raw_dir=raw, db_path=db)
    assert not db.exists()
