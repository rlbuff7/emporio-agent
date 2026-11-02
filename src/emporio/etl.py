"""Load the store's CSV exports into a SQLite database.

Run with ``python -m emporio.etl``. The database is rebuilt from scratch on every
run, so the CSVs in ``data/raw`` stay the single source of truth.

Cleaning applied (see docs/data_quality.md for the reasoning):
- empty strings become NULL;
- ids, quantities, stock and flags become integers; prices become REAL;
- dates are validated as ISO ``YYYY-MM-DD`` (stored as TEXT, SQLite has no date type);
- ``products.specs`` is validated as JSON and stored as TEXT, queryable with ``json_extract``;
- every promotion row is loaded (inactive ones are needed to tell a customer a
  promotion has ended); the ``active_promotions`` view keeps one active
  promotion per product, the one with the highest discount.
"""

import csv
import json
import sqlite3
from datetime import date
from pathlib import Path

from emporio.paths import DB_PATH, RAW_DIR

CSV_PREFIX = "desafio_tecnico_ai_eng - "

SCHEMA = """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT
);

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    email       TEXT NOT NULL,  -- not UNIQUE: customers 11 and 43 share an e-mail
    city        TEXT NOT NULL
);

CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    category_id    INTEGER NOT NULL REFERENCES categories(category_id),
    description    TEXT,
    price_brl      REAL NOT NULL CHECK (price_brl > 0),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status         TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    specs          TEXT CHECK (specs IS NULL OR json_valid(specs)),
    created_at     TEXT
);

CREATE TABLE orders (
    order_id           INTEGER PRIMARY KEY,
    customer_id        INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date         TEXT NOT NULL,
    status             TEXT NOT NULL
                       CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    total_brl          REAL NOT NULL CHECK (total_brl >= 0),
    payment_method     TEXT NOT NULL,
    tracking_code      TEXT,
    estimated_delivery TEXT,
    notes              TEXT
);

CREATE TABLE order_items (
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE promotions (
    promotion_id     INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES products(product_id),
    discount_percent INTEGER NOT NULL CHECK (discount_percent BETWEEN 1 AND 100),
    campaign         TEXT NOT NULL,  -- CSV column "description": a campaign label, not a validity period
    is_active        INTEGER NOT NULL CHECK (is_active IN (0, 1))
);

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_promotions_product ON promotions(product_id);

-- One active promotion per product. Ties on discount fall back to the lowest promotion_id.
CREATE VIEW active_promotions AS
SELECT promotion_id, product_id, discount_percent, campaign
FROM (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY product_id
               ORDER BY discount_percent DESC, promotion_id
           ) AS rank
    FROM promotions p
    WHERE is_active = 1
)
WHERE rank = 1;
"""


def _text(value: str) -> str | None:
    value = value.strip()
    return value or None


def _int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def _float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def _date(value: str) -> str | None:
    value = value.strip()
    return date.fromisoformat(value).isoformat() if value else None


def _json(value: str) -> str | None:
    value = value.strip()
    return json.dumps(json.loads(value), ensure_ascii=False) if value else None


# table -> list of (target column, source CSV column, converter), in insert order.
# Parents come before children so foreign keys can be enforced during the load.
TABLES = {
    "categories": [
        ("category_id", "category_id", _int),
        ("name", "name", _text),
        ("description", "description", _text),
    ],
    "customers": [
        ("customer_id", "customer_id", _int),
        ("name", "name", _text),
        ("phone", "phone", _text),
        ("email", "email", _text),
        ("city", "city", _text),
    ],
    "products": [
        ("product_id", "product_id", _int),
        ("name", "name", _text),
        ("category_id", "category_id", _int),
        ("description", "description", _text),
        ("price_brl", "price_brl", _float),
        ("stock_quantity", "stock_quantity", _int),
        ("status", "status", _text),
        ("specs", "specs", _json),
        ("created_at", "created_at", _date),
    ],
    "orders": [
        ("order_id", "order_id", _int),
        ("customer_id", "customer_id", _int),
        ("order_date", "order_date", _date),
        ("status", "status", _text),
        ("total_brl", "total_brl", _float),
        ("payment_method", "payment_method", _text),
        ("tracking_code", "tracking_code", _text),
        ("estimated_delivery", "estimated_delivery", _date),
        ("notes", "notes", _text),
    ],
    "order_items": [
        ("order_id", "order_id", _int),
        ("product_id", "product_id", _int),
        ("quantity", "quantity", _int),
    ],
    "promotions": [
        ("promotion_id", "promotion_id", _int),
        ("product_id", "product_id", _int),
        ("discount_percent", "discount_percent", _int),
        ("campaign", "description", _text),
        ("is_active", "is_active", _int),
    ],
}


def read_csv(raw_dir: Path, table: str) -> list[dict[str, str]]:
    path = raw_dir / f"{CSV_PREFIX}{table}.csv"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def build_database(raw_dir: Path = RAW_DIR, db_path: Path = DB_PATH) -> dict[str, int]:
    """Rebuild ``db_path`` from the CSVs in ``raw_dir``. Returns row counts per table."""
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        counts = {}
        for table, columns in TABLES.items():
            rows = [
                tuple(convert(raw[source]) for _, source, convert in columns)
                for raw in read_csv(raw_dir, table)
            ]
            names = ", ".join(target for target, _, _ in columns)
            marks = ", ".join("?" for _ in columns)
            conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({marks})", rows)
            counts[table] = len(rows)
        conn.commit()
    except Exception:
        conn.close()
        db_path.unlink(missing_ok=True)
        raise
    conn.close()
    return counts


def main() -> None:
    counts = build_database()
    print(f"Built {DB_PATH}")
    for table, n in counts.items():
        print(f"  {table:<12} {n:>4} rows")


if __name__ == "__main__":
    main()
