import json
from datetime import datetime
from decimal import Decimal

import pytest

from emporio.tools import STORE_TZ, StoreTools, max_installments, price_info

MONDAY_AFTERNOON = datetime(2026, 11, 9, 14, 30, tzinfo=STORE_TZ)


@pytest.fixture
def tools(db_path, tmp_path):
    return StoreTools(db_path=db_path, handoffs_path=tmp_path / "handoffs.jsonl",
                      clock=lambda: MONDAY_AFTERNOON)


def at(tools, *args):
    """Same tools, clock frozen at datetime(*args) in store time."""
    return StoreTools(tools.db_path, tools.handoffs_path,
                      clock=lambda: datetime(*args, tzinfo=STORE_TZ))


# Pricing (manual 3, 3.1, 6.2) ------------------------------------------------

def test_pix_discount_only_without_promotion():
    assert price_info(2199.0, None, None)["pix_price_brl"] == 2089.05
    promo = price_info(2399.0, 18, "Desconto Exclusivo")
    assert promo["final_price_brl"] == 1967.18
    assert promo["pix_price_brl"] is None


@pytest.mark.parametrize("price, expected", [
    ("159.90", 3),   # 3x of 53.30 >= 50; 4x of 39.98 < 80
    ("480.00", 6),   # 6x of 80 >= 80; 7x of 68.57 < 100
    ("749.00", 7),   # 7x of 107 >= 100; 8x of 93.63 < 100
    ("2199.00", 12),
    ("90.00", 1),    # 2x of 45 < 50
])
def test_max_installments_follows_section_3_1(price, expected):
    assert max_installments(Decimal(price)) == expected


# Products ---------------------------------------------------------------------

def test_guitars_up_to_1000_are_active_in_stock_and_cheap_enough(tools):
    result = tools.search_products(category="violoes", max_price=1000)
    assert result["total_found"] > 0
    for product in result["products"]:
        assert product["category"] == "Violões"
        assert product["available"] is True
        assert product["final_price_brl"] <= 1000
    prices = [p["final_price_brl"] for p in result["products"]]
    assert prices == sorted(prices)


def test_unavailable_products_are_hidden_by_default(tools):
    available = tools.search_products(category="Violões", max_price=800)
    everything = tools.search_products(category="Violões", max_price=800, only_available=False)
    ids = {p["product_id"] for p in available["products"]}
    assert 113 in {p["product_id"] for p in everything["products"]}  # discontinued, R$ 749
    assert 113 not in ids
    assert available["total_found"] < everything["total_found"]


def test_spec_filters(tools):
    result = tools.search_products(category="Violões", strings="nylon", electronics=True, only_available=False)
    assert result["total_found"] > 0
    for product in result["products"]:
        specs = tools.get_product(str(product["product_id"]))["specs"]
        assert specs["strings"] == "nylon" and specs["electronics"] == "yes"


def test_takamine_gd20_price(tools):
    product = tools.get_product("Takamine GD20")
    assert product["product_id"] == 95
    assert product["final_price_brl"] == 2199.0


def test_name_lookup_ignores_case_accents_and_hyphens(tools):
    assert tools.get_product("crafter ht100")["product_id"] == 90


def test_ambiguous_name_returns_candidates(tools):
    result = tools.get_product("Crafter")
    assert result["ambiguous"] is True
    assert {c["product_id"] for c in result["candidates"]} == {90, 97, 102}


def test_unknown_product(tools):
    assert tools.get_product("Stradivarius")["error"] == "not_found"


def test_discontinued_product_is_not_available_and_gets_alternatives(tools):
    product = tools.get_product("113")
    assert product["status"] == "discontinued" and product["available"] is False
    assert product["alternatives"]
    assert all(alt["available"] and alt["category"] == "Violões" for alt in product["alternatives"])


def test_ended_promotions_are_reported(tools):
    assert "Black Friday" in tools.get_product("102")["ended_promotions"]


def test_unreliable_descriptions_are_hidden(tools):
    assert tools.get_product("137")["description"] is None   # bass, finding 1
    assert tools.get_product("95")["description"]            # guitar, fine


def test_active_promotions_show_original_discount_and_final(tools):
    promotions = tools.get_active_promotions()["promotions"]
    assert {p["product_id"] for p in promotions} == {90, 94, 121, 127}
    crafter = next(p for p in promotions if p["product_id"] == 90)
    assert (crafter["original_price_brl"], crafter["promotion"]["discount_percent"],
            crafter["final_price_brl"]) == (2399.0, 18, 1967.18)


# Orders (LGPD) -------------------------------------------------------------------

def test_order_requires_matching_email(tools, conn):
    email = conn.execute(
        "SELECT c.email FROM orders o JOIN customers c USING (customer_id) WHERE order_id = 1"
    ).fetchone()[0]
    assert tools.get_order_status(1, email.upper())["status"] == "delivered"
    assert tools.get_order_status(1, "someone.else@jmail.com")["error"] == "verification_failed"


def test_order_accepts_phone_in_any_format(tools, conn):
    phone = conn.execute(
        "SELECT c.phone FROM orders o JOIN customers c USING (customer_id) WHERE order_id = 1"
    ).fetchone()[0]
    digits = "".join(ch for ch in phone if ch.isdigit())
    for variant in (phone, digits, "+55 " + phone, digits[2:]):
        assert tools.get_order_status(1, variant)["order_id"] == 1
    assert tools.get_order_status(1, digits[-4:])["error"] == "verification_failed"


def test_missing_order_and_wrong_contact_look_the_same(tools):
    assert tools.get_order_status(999, "x@y.com") == tools.get_order_status(1, "x@y.com")


def test_shared_email_customer_phones_stay_separate(tools):
    # Customers 11 and 43 share an e-mail (finding 12). Order 10 belongs to customer 11:
    # customer 11's phone opens it, customer 43's phone does not.
    assert tools.get_order_status(10, "(67) 98543-2109")["order_id"] == 10
    assert tools.get_order_status(10, "(67) 99456-3456")["error"] == "verification_failed"


def _contact(conn, order_id):
    return conn.execute(
        "SELECT c.email FROM orders o JOIN customers c USING (customer_id) WHERE order_id = ?",
        (order_id,),
    ).fetchone()[0]


def test_late_shipment_is_flagged(tools, conn):
    order = tools.get_order_status(8, _contact(conn, 8))
    assert order["status"] == "shipped"
    assert order["delayed"]["days_past_estimate"] == (MONDAY_AFTERNOON.date() - datetime(2026, 2, 28).date()).days


def test_return_windows_are_computed_in_code(tools, conn):
    email = _contact(conn, 7)  # delivered, estimated delivery 2026-02-17, ordered 2026-02-03
    inside = at(tools, 2026, 2, 20, 10, 0).get_order_status(7, email)["policy_windows"]
    assert inside["regret_return_7_days"]["within_window"] is True
    assert inside["regret_return_7_days"]["deadline"] == "2026-02-24"
    assert inside["defect_exchange_30_days"]["within_window"] is True
    later = tools.get_order_status(7, email)["policy_windows"]
    assert later["regret_return_7_days"]["within_window"] is False
    assert later["legal_warranty_90_days"]["within_window"] is False


def test_policy_windows_only_for_delivered_orders(tools, conn):
    assert "policy_windows" not in tools.get_order_status(9, _contact(conn, 9))


# Store hours (manual 2) ----------------------------------------------------------

def test_open_on_weekday_afternoon(tools):
    now = tools.get_current_datetime()
    assert now["is_open_now"] is True and now["today_hours"] == "09:00–18:00"


def test_closed_on_saturday_afternoon_reopens_monday(tools):
    now = at(tools, 2026, 11, 7, 14, 0).get_current_datetime()
    assert now["weekday"] == "sábado" and now["is_open_now"] is False
    assert now["next_opening"] == {"date": "2026-11-09", "weekday": "segunda-feira", "opens_at": "09:00"}


def test_closed_on_national_holiday(tools):
    now = at(tools, 2026, 11, 2, 10, 0).get_current_datetime()
    assert now["holiday"] == "Finados" and now["is_open_now"] is False
    assert now["next_opening"]["date"] == "2026-11-03"


def test_before_opening_reopens_same_day(tools):
    assert at(tools, 2026, 11, 9, 7, 0).get_current_datetime()["next_opening"]["date"] == "2026-11-09"


# Handoff and dispatch --------------------------------------------------------------

def test_escalation_writes_numbered_tickets(tools):
    first = tools.escalate_to_human(reason="complaint", summary="Pedido 8 atrasado", order_id=8)
    second = tools.escalate_to_human(reason="warranty", summary="Defeito no captador")
    assert (first["ticket"], second["ticket"]) == ("EMP-0001", "EMP-0002")
    records = [json.loads(line) for line in tools.handoffs_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["order_id"] == 8 and records[1]["reason"] == "warranty"


def test_null_arguments_fall_back_to_safe_defaults(tools):
    result = tools.call("search_products", {"category": "Violões", "only_available": None, "query": None})
    assert result["total_found"] > 0
    assert all(p["available"] for p in result["products"])  # filter stayed on
    ticket = tools.call("escalate_to_human", {"reason": "other", "summary": "x", "order_id": None})
    assert "ticket" in ticket


def test_dispatch_rejects_unknown_tools_and_bad_arguments(tools):
    assert tools.call("drop_tables", {})["error"] == "unknown_tool"
    assert tools.call("_rows", {})["error"] == "unknown_tool"
    assert tools.call("get_product", {"wrong": 1})["error"] == "invalid_arguments"
    assert tools.call("get_product", {"name_or_id": "95"})["product_id"] == 95


# Opening hours on any date (manual 2) ----------------------------------------------

@pytest.mark.parametrize("day, open_, hours, holiday", [
    ("2026-12-31", True, "09:00–18:00", None),                  # Thursday, not a holiday
    ("2026-12-25", False, "fechado", "Natal"),
    ("2027-03-26", False, "fechado", "Paixão de Cristo (Sexta-feira Santa)"),  # Easter 2027 is March 28
    ("2026-04-03", False, "fechado", "Paixão de Cristo (Sexta-feira Santa)"),  # matches Decreto 16.469
    ("2026-08-26", False, "fechado", "Aniversário de Campo Grande"),          # Wednesday
    ("2027-06-14", True, "09:00–18:00", None),                  # day after Santo Antônio
    ("2026-06-04", True, "09:00–18:00", None),                  # Corpus Christi: ponto facultativo
    ("2026-02-16", True, "09:00–18:00", None),                  # Carnival Monday: ponto facultativo
    ("2027-10-11", False, "fechado", "Criação do Estado de Mato Grosso do Sul"),  # Monday
    ("2026-12-26", True, "09:00–13:00", None),                  # Saturday
    ("2026-12-27", False, "fechado", None),                     # Sunday
])
def test_opening_hours_on_any_date(tools, day, open_, hours, holiday):
    result = tools.get_opening_hours(day)
    assert (result["open"], result["hours"], result["holiday"]) == (open_, hours, holiday)


def test_easter_dates():
    from emporio.tools import easter
    assert [easter(y).isoformat() for y in (2025, 2026, 2027)] == ["2025-04-20", "2026-04-05", "2027-03-28"]


def test_santo_antonio_closes_the_store():
    from emporio.tools import holiday_name
    from datetime import date
    assert holiday_name(date(2027, 6, 13)) == "Santo Antônio, padroeiro de Campo Grande"


def test_opening_hours_rejects_bad_dates(tools):
    assert tools.get_opening_hours("31/12")["error"] == "invalid_date"


def test_good_friday_closes_the_store_today(tools):
    now = at(tools, 2026, 4, 3, 10, 0).get_current_datetime()  # Good Friday 2026
    assert now["holiday"] == "Paixão de Cristo (Sexta-feira Santa)" and now["is_open_now"] is False
    assert now["next_opening"]["date"] == "2026-04-04"


def test_every_2026_closure_in_campo_grande_calendar():
    """The full 2026 list (Decreto 16.469 + the two municipal holidays): the store is closed on each."""
    from datetime import date
    from emporio.tools import holiday_name
    closures = ["01-01", "04-03", "04-21", "05-01", "06-13", "08-26", "09-07",
                "10-11", "10-12", "11-02", "11-15", "11-20", "12-25"]
    assert [d for d in closures if holiday_name(date.fromisoformat(f"2026-{d}")) is None] == []
    pontos_facultativos = ["01-02", "02-16", "02-17", "02-18", "04-02", "04-20",
                           "06-04", "06-05", "10-28", "12-24", "12-31"]
    assert [d for d in pontos_facultativos if holiday_name(date.fromisoformat(f"2026-{d}"))] == []
