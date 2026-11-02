"""Each test pins a finding from docs/data_quality.md, so the report cannot drift from the data."""


def test_finding_1_description_contradicts_specs(conn):
    description, strings = conn.execute(
        "SELECT description, json_extract(specs, '$.strings') FROM products WHERE product_id = 137"
    ).fetchone()
    assert "5 cordas" in description and strings == "4"


def test_finding_2_unsellable_products(conn):
    rows = conn.execute(
        "SELECT product_id FROM products WHERE status != 'active' OR stock_quantity = 0 ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == [96, 113, 130, 136]


def test_finding_3_empty_categories(conn):
    rows = conn.execute(
        "SELECT category_id FROM categories "
        "WHERE category_id NOT IN (SELECT category_id FROM products) ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == [6, 7, 8]


def test_finding_4_catalog_size(conn):
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 65


def test_finding_5_black_friday_rows_are_inactive(conn):
    flags = conn.execute(
        "SELECT is_active FROM promotions WHERE campaign = 'Black Friday'"
    ).fetchall()
    assert flags == [(0,), (0,), (0,)]


def test_finding_6_products_with_several_promotions(conn):
    rows = conn.execute(
        "SELECT product_id FROM promotions GROUP BY product_id HAVING COUNT(*) > 1 ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == [93, 96, 99]


def test_finding_7_discounts_outside_manual_ranges(conn):
    assert conn.execute(
        "SELECT campaign, discount_percent FROM promotions WHERE promotion_id IN (1, 17) ORDER BY 1"
    ).fetchall() == [("Aniversário da Loja", 30), ("Black Friday", 10)]


def test_finding_8_coming_soon_product_was_ordered(conn):
    rows = conn.execute("SELECT order_id FROM order_items WHERE product_id = 130 ORDER BY 1").fetchall()
    assert [r[0] for r in rows] == [3, 14]


def test_finding_9_totals_differ_from_current_prices(conn):
    rows = conn.execute(
        """
        SELECT o.order_id, o.total_brl, SUM(p.price_brl * i.quantity)
        FROM orders o
        JOIN order_items i ON i.order_id = o.order_id
        JOIN products p ON p.product_id = i.product_id
        GROUP BY o.order_id
        HAVING ABS(o.total_brl - SUM(p.price_brl * i.quantity)) > 0.01
        ORDER BY o.order_id
        """
    ).fetchall()
    assert rows == [(3, 3450.0, 3498.0), (20, 1400.0, 1488.0)]


def test_finding_10_order_8_shipped_with_past_estimate(conn):
    assert conn.execute(
        "SELECT status, estimated_delivery FROM orders WHERE order_id = 8"
    ).fetchone() == ("shipped", "2026-02-28")


def test_finding_12_shared_email(conn):
    rows = conn.execute(
        "SELECT customer_id FROM customers WHERE email = 'bruno.martins@jmail.com' ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == [11, 43]


def test_finding_15_three_installments_always_above_50(conn):
    cheapest = conn.execute("SELECT MIN(price_brl) FROM products").fetchone()[0]
    assert cheapest / 3 >= 50
