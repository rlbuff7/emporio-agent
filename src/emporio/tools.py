"""Functions the LLM can call. Every number, price, date and status comes from here.

The model only decides *which* tool to call and how to phrase the result; it never
computes a price, a discount, an installment or a deadline. Each tool returns a
JSON-serialisable dict.
"""

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from emporio.db import connect
from emporio.paths import DB_PATH, HANDOFFS_PATH

# Campo Grande/MS is UTC-4 all year (Brazil dropped daylight saving time in 2019).
# A fixed offset avoids depending on the OS time-zone database, which Windows lacks.
STORE_TZ = timezone(timedelta(hours=-4), "America/Campo_Grande")

CENT = Decimal("0.01")
PIX_DISCOUNT = Decimal("0.05")  # manual 3: 5% off the list price, never on top of a promotion (6.2)
MAX_RESULTS = 10

# Manual 1.2 / data_quality finding 1: descriptions in these categories name other
# brands and contradict the specs, so they are not shown to the customer.
UNRELIABLE_DESCRIPTION_CATEGORIES = {2, 3, 4}  # basses, drums, keyboards

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]

# Manual 2: Mon-Fri 09:00-18:00, Sat 09:00-13:00, Sun and holidays closed.
OPENING_HOURS = {wd: (time(9), time(18)) for wd in range(5)} | {5: (time(9), time(13))}

# Fixed-date national holidays. Movable ones (Carnaval, Good Friday, Corpus Christi)
# and municipal holidays are not covered; see README limitations.
NATIONAL_HOLIDAYS = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independência do Brasil",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Dia da Consciência Negra",
    (12, 25): "Natal",
}

# Manual 4.1, 4.2 and 8.1, in calendar days.
REGRET_DAYS = 7
DEFECT_EXCHANGE_DAYS = 30
LEGAL_WARRANTY_DAYS = 90

VERIFICATION_FAILED = {
    "error": "verification_failed",
    "message": "Não encontrei um pedido com esse número e esse e-mail/telefone.",
}


# --- helpers -----------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation: 'Violão HT-100' -> 'violao ht100'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]", "", word) for word in text.split())


def _money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def max_installments(price: Decimal) -> int:
    """Largest number of interest-free installments allowed by manual 3.1.

    Minimum installment: R$ 50 up to 3x (our reading of "exceto abaixo de R$ 50"),
    R$ 80 from 4x to 6x, R$ 100 from 7x to 12x.
    """
    for n in range(12, 1, -1):
        minimum = 100 if n >= 7 else 80 if n >= 4 else 50
        if price / n >= minimum:
            return n
    return 1


def price_info(list_price: float, discount_percent: int | None, campaign: str | None) -> dict:
    """Original price, promotion, final price, PIX price and installments, all in code."""
    original = Decimal(str(list_price))
    if discount_percent:
        final = original * (100 - discount_percent) / 100
        promotion = {"campaign": campaign, "discount_percent": discount_percent}
        pix_price = None  # not cumulative with a promotion (manual 6.2)
    else:
        final = original
        promotion = None
        pix_price = _money(original * (1 - PIX_DISCOUNT))
    final = final.quantize(CENT, rounding=ROUND_HALF_UP)
    n = max_installments(final)
    return {
        "original_price_brl": _money(original),
        "promotion": promotion,
        "final_price_brl": _money(final),
        "pix_price_brl": pix_price,
        "max_installments": n,
        "installment_value_brl": _money(final / n),
    }


def _is_available(row: sqlite3.Row) -> bool:
    return row["status"] == "active" and row["stock_quantity"] > 0


PRODUCT_QUERY = """
SELECT p.product_id, p.name, p.category_id, c.name AS category, p.description,
       p.price_brl, p.stock_quantity, p.status, p.specs,
       ap.discount_percent, ap.campaign
FROM products p
JOIN categories c ON c.category_id = p.category_id
LEFT JOIN active_promotions ap ON ap.product_id = p.product_id
"""


def _summary(row: sqlite3.Row) -> dict:
    return {
        "product_id": row["product_id"],
        "name": row["name"],
        "category": row["category"],
        "status": row["status"],
        "stock_quantity": row["stock_quantity"],
        "available": _is_available(row),
        **price_info(row["price_brl"], row["discount_percent"], row["campaign"]),
    }


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _identity_matches(email_or_phone: str, email: str, phone: str) -> bool:
    given = email_or_phone.strip().lower()
    if "@" in given:
        return given == email.strip().lower()
    given_digits, phone_digits = _digits(given), _digits(phone)
    # Accept the number with or without area code / country code, but never a short fragment.
    return len(given_digits) >= 8 and (
        phone_digits.endswith(given_digits) or given_digits.endswith(phone_digits)
    )


def _window(start: date, days: int, today: date) -> dict:
    deadline = start + timedelta(days=days)
    return {
        "counted_from": start.isoformat(),
        "deadline": deadline.isoformat(),
        "days_elapsed": (today - start).days,
        "within_window": today <= deadline,
    }


# --- tools -------------------------------------------------------------------

class StoreTools:
    """The agent's tools. Paths and clock are injectable so tests are deterministic."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        handoffs_path: Path = HANDOFFS_PATH,
        clock: Callable[[], datetime] = lambda: datetime.now(STORE_TZ),
    ):
        self.db_path = db_path
        self.handoffs_path = handoffs_path
        self.clock = clock

    def call(self, name: str, arguments: dict) -> dict:
        """Dispatch a tool call from the model. Unknown tools and bad arguments become errors, not crashes."""
        method = getattr(self, name, None)
        if name.startswith("_") or name == "call" or not callable(method):
            return {"error": "unknown_tool", "message": f"Tool '{name}' does not exist."}
        try:
            return method(**arguments)
        except (TypeError, ValueError) as exc:
            return {"error": "invalid_arguments", "message": str(exc)}

    def _rows(self, where: str = "", params: tuple = ()) -> list[sqlite3.Row]:
        with closing(connect(self.db_path)) as conn:
            return conn.execute(f"{PRODUCT_QUERY} {where}", params).fetchall()

    # Products ----------------------------------------------------------------

    def search_products(
        self,
        category: str | None = None,
        query: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        strings: str | None = None,
        electronics: bool | None = None,
        only_available: bool = True,
    ) -> dict:
        """Filter the catalog. Price filters apply to the final price (after promotion), sorted cheapest first."""
        terms = _normalize(query).split() if query else []
        results = []
        for row in self._rows():
            specs = json.loads(row["specs"] or "{}")
            if category and _normalize(category) != _normalize(row["category"]):
                continue
            if only_available and not _is_available(row):
                continue
            if strings and specs.get("strings") != strings:
                continue
            if electronics is not None and specs.get("electronics") != ("yes" if electronics else "no"):
                continue
            haystack = _normalize(f"{row['name']} {row['category']} {' '.join(map(str, specs.values()))}")
            if any(term not in haystack.replace(" ", "") for term in terms):
                continue
            item = _summary(row)
            if min_price is not None and item["final_price_brl"] < min_price:
                continue
            if max_price is not None and item["final_price_brl"] > max_price:
                continue
            results.append(item)
        results.sort(key=lambda item: item["final_price_brl"])
        return {
            "total_found": len(results),
            "products": results[:MAX_RESULTS],
            "truncated": len(results) > MAX_RESULTS,
        }

    def get_product(self, name_or_id: str) -> dict:
        """One product with specs, pricing and ended promotions; candidates if the name is ambiguous."""
        key = str(name_or_id).strip()
        if key.isdigit():
            rows = self._rows("WHERE p.product_id = ?", (int(key),))
        else:
            terms = _normalize(key).split()
            rows = [
                row for row in self._rows()
                if all(term in _normalize(row["name"]).replace(" ", "") for term in terms)
            ]
        if not rows:
            return {"error": "not_found", "message": f"Nenhum produto encontrado para '{key}'."}
        if len(rows) > 1:
            return {
                "ambiguous": True,
                "candidates": [{"product_id": r["product_id"], "name": r["name"]} for r in rows[:MAX_RESULTS]],
            }
        row = rows[0]
        product = _summary(row)
        product["specs"] = json.loads(row["specs"] or "{}")
        product["description"] = (
            None if row["category_id"] in UNRELIABLE_DESCRIPTION_CATEGORIES else row["description"]
        )
        with closing(connect(self.db_path)) as conn:
            product["ended_promotions"] = [
                r["campaign"] for r in conn.execute(
                    "SELECT DISTINCT campaign FROM promotions WHERE product_id = ? AND is_active = 0",
                    (row["product_id"],),
                )
            ]
        if not product["available"]:
            product["alternatives"] = self._alternatives(row)
        return product

    def _alternatives(self, row: sqlite3.Row, limit: int = 3) -> list[dict]:
        """Available products in the same category, closest in price (manual 7.3)."""
        same_category = [
            _summary(r) for r in self._rows("WHERE p.category_id = ?", (row["category_id"],))
            if _is_available(r) and r["product_id"] != row["product_id"]
        ]
        same_category.sort(key=lambda item: abs(item["original_price_brl"] - row["price_brl"]))
        return same_category[:limit]

    def get_active_promotions(self, category: str | None = None) -> dict:
        """Promotions active right now, with original price, discount and final price (manual 6.2)."""
        promotions = [
            _summary(row) for row in self._rows("WHERE ap.product_id IS NOT NULL")
            if not category or _normalize(category) == _normalize(row["category"])
        ]
        promotions.sort(key=lambda item: item["final_price_brl"])
        return {"promotions": promotions}

    # Orders ------------------------------------------------------------------

    def get_order_status(self, order_id: int | str, email_or_phone: str) -> dict:
        """Order details, only if the e-mail or phone belongs to the order's customer (LGPD)."""
        if not str(order_id).strip().isdigit():
            return VERIFICATION_FAILED
        with closing(connect(self.db_path)) as conn:
            order = conn.execute(
                """
                SELECT o.*, c.name AS customer_name, c.email, c.phone, c.city
                FROM orders o JOIN customers c ON c.customer_id = o.customer_id
                WHERE o.order_id = ?
                """,
                (int(order_id),),
            ).fetchone()
            # Same answer whether the order is missing or the contact is wrong:
            # never reveal which part failed.
            if order is None or not _identity_matches(email_or_phone, order["email"], order["phone"]):
                return VERIFICATION_FAILED
            items = conn.execute(
                """
                SELECT p.name, i.quantity FROM order_items i
                JOIN products p ON p.product_id = i.product_id
                WHERE i.order_id = ? ORDER BY p.name
                """,
                (order["order_id"],),
            ).fetchall()

        today = self.clock().date()
        order_date = date.fromisoformat(order["order_date"])
        eta = date.fromisoformat(order["estimated_delivery"]) if order["estimated_delivery"] else None
        result = {
            "order_id": order["order_id"],
            "customer_first_name": order["customer_name"].split()[0],
            "status": order["status"],
            "order_date": order["order_date"],
            "items": [{"name": i["name"], "quantity": i["quantity"]} for i in items],
            "total_brl": order["total_brl"],  # amount charged; may differ from current prices
            "payment_method": order["payment_method"],
            "tracking_code": order["tracking_code"],
            "estimated_delivery": order["estimated_delivery"],
            "notes": order["notes"],
            "today": today.isoformat(),
            "days_since_order": (today - order_date).days,
        }
        if order["status"] == "shipped" and eta and eta < today:
            result["delayed"] = {"days_past_estimate": (today - eta).days}
        if order["status"] == "delivered":
            # No actual receipt date exists; the estimate stands in for it (data_quality finding 11).
            received = eta or order_date
            result["policy_windows"] = {
                "receipt_date_is_estimate": True,
                "regret_return_7_days": _window(received, REGRET_DAYS, today),
                "preference_exchange_7_days": _window(received, REGRET_DAYS, today),
                "defect_exchange_30_days": _window(order_date, DEFECT_EXCHANGE_DAYS, today),
                "legal_warranty_90_days": _window(received, LEGAL_WARRANTY_DAYS, today),
            }
        return result

    # Store info --------------------------------------------------------------

    def get_current_datetime(self) -> dict:
        """Current date/time in Campo Grande and whether the store is open (manual 2)."""
        now = self.clock().astimezone(STORE_TZ)
        today = now.date()
        holiday = NATIONAL_HOLIDAYS.get((today.month, today.day))
        hours = None if holiday else OPENING_HOURS.get(today.weekday())
        is_open = bool(hours) and hours[0] <= now.time() < hours[1]
        return {
            "date": today.isoformat(),
            "time": now.strftime("%H:%M"),
            "weekday": WEEKDAYS_PT[today.weekday()],
            "holiday": holiday,
            "today_hours": f"{hours[0]:%H:%M}–{hours[1]:%H:%M}" if hours else "fechado",
            "is_open_now": is_open,
            "next_opening": None if is_open else self._next_opening(now),
        }

    def _next_opening(self, now: datetime) -> dict:
        for offset in range(0, 8):
            day = now.date() + timedelta(days=offset)
            hours = OPENING_HOURS.get(day.weekday())
            if not hours or (day.month, day.day) in NATIONAL_HOLIDAYS:
                continue
            opening = datetime.combine(day, hours[0], STORE_TZ)
            if opening > now:
                return {"date": day.isoformat(), "weekday": WEEKDAYS_PT[day.weekday()],
                        "opens_at": f"{hours[0]:%H:%M}"}
        raise RuntimeError("no opening found within a week")

    # Handoff -----------------------------------------------------------------

    def escalate_to_human(
        self,
        reason: str,
        summary: str,
        order_id: int | None = None,
        customer_contact: str | None = None,
    ) -> dict:
        """Open a ticket for the human team (complaints, exchanges, warranty, anything the bot must not decide)."""
        self.handoffs_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.handoffs_path.read_text(encoding="utf-8").splitlines() if self.handoffs_path.exists() else []
        ticket = f"EMP-{len(existing) + 1:04d}"
        record = {
            "ticket": ticket,
            "created_at": self.clock().isoformat(timespec="seconds"),
            "reason": reason,
            "summary": summary,
            "order_id": order_id,
            "customer_contact": customer_contact,
        }
        with open(self.handoffs_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ticket": ticket, "response_time": "até 24 horas úteis"}
