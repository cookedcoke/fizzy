"""Dashboard sales numbers (fizzy/stats.py)."""
from datetime import date

from fizzy import logic, square, stats
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import iso, order, pay, tab_order


def test_sold_taken_orders_and_items(conn, cust):
    c = cust()
    # a card sale, a tab sale ($0 cash), a repayment + drink, and a walk-up
    square.process_payment(conn, pay("p1", 650, source="CARD", order_id="o1"), order(("Coke", 350), ("Chips", 300), oid="o1"))
    square.process_order(conn, tab_order("o2", 400, ("Noodles", 400)))
    square.process_payment(conn, pay("p2", 0, order_id="o2"), tab_order("o2", 400, ("Noodles", 400)))
    square.process_payment(conn, pay("p3", 750, source="CARD", order_id="o3"), order(("Tab repayment", 400), ("Coke", 350), oid="o3"))
    square.process_payment(conn, pay("p4", 350, cust=None, order_id="o4"), order(("Coke", 350), oid="o4"))
    logic.add_repayment(conn, c["id"], 100, "cash", "", "Will")             # by hand, not through Square
    t = stats.summary(conn, stats.today())
    assert t["orders"] == 4
    assert t["sold_cents"] == 650 + 400 + 350 + 350                        # goods, including what went on the tab
    assert t["taken_cents"] == 650 + 750 + 350 + 100                       # money in: tab sale adds nothing
    assert (t["tab_cents"], t["repaid_cents"]) == (400, 400 + 100)             # repaid includes cash taken by hand
    assert (t["customers"], t["walkups"]) == (1, 1)
    top = {i["name"]: i["qty"] for i in stats.top_items(t["checkouts"])}
    assert top == {"Coke": 3, "Chips": 1, "Noodles": 1}                    # the repayment item isn't an item


def test_refunds_come_off_taken(conn, cust):
    cust()
    square.process_payment(conn, pay("p1", 500, order_id="o1"), order(("Coke", 500), oid="o1"))
    square.process_refund(conn, {"id": "r1", "payment_id": "p1", "status": "COMPLETED",
                                 "amount_money": {"amount": 200}, "created_at": iso()})
    assert stats.summary(conn, stats.today())["taken_cents"] == 300


def test_by_hour_widens_to_fit_and_weekends_are_skipped(conn, cust, monkeypatch):
    cust()
    square.process_payment(conn, pay("p1", 500), None)
    hours = stats.by_hour(stats.summary(conn, stats.today())["checkouts"], first=7, last=16)
    assert sum(h["cents"] for h in hours) == 500 and hours[0]["hour"] <= 7 and hours[-1]["hour"] >= 16
    monkeypatch.setattr(stats, "today", lambda: date(2026, 10, 19))       # a Monday
    week = stats.dashboard(conn)["week"]
    assert len(week) == 7 and all(w["day"].weekday() < 5 for w in week) and week[-1]["day"] == date(2026, 10, 19)
    assert week[0]["day"] == date(2026, 10, 9)                             # back past two weekends


def test_dashboard_shows_sales(client):
    login(client)
    from fizzy import db
    conn = db.connect()
    try:
        square.process_payment(conn, pay("p1", 650, cust=None, order_id="o1"), order(("Coke", 350), ("Chips", 300), oid="o1"))
        conn.commit()
    finally:
        conn.close()
    page = client.get("/").text
    assert "$6.50" in page and "Sold by hour" in page and "Chips" in page and "data-autorefresh" in page
    assert "data-clock" in page
