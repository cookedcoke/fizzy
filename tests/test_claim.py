"""Claiming a sale rung up in Square with no customer (customer page, K)."""
import pytest

from fizzy import logic, square
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import iso, order, pay, tab_order


def test_plain_sale_is_claimed_with_its_points(conn, cust):
    c = cust()
    square.process_payment(conn, pay("p1", 450, cust=None, order_id="o1"), order(("Coke", 450), oid="o1"))
    [k] = square.unclaimed(conn)
    assert k["sale_cents"] == 450 and "Coke" in k["items"]
    square.claim(conn, k["key"], c, "Will")
    assert logic.points_balance(conn, c["id"]) == 450 and square.unclaimed(conn) == []


def test_tab_sale_and_its_zero_dollar_payment_are_one_checkout(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 300, ("Chips", 300), cust=None))
    square.process_payment(conn, pay("p1", 0, cust=None, order_id="o1"), tab_order("o1", 300, ("Chips", 300), cust=None))
    [k] = square.unclaimed(conn)
    assert k["tab_cents"] == 300
    square.claim(conn, k["key"], c, "Will")
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 300 and square.unclaimed(conn) == []


def test_mixed_repayment_and_purchase_claimed_together(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o0", 500))                    # an earlier tab sale, already theirs
    square.process_payment(conn, pay("p1", 800, cust=None, source="CARD", order_id="o2"),
                           order(("Tab repayment", 500), ("Coke", 300), oid="o2"))
    [k] = square.unclaimed(conn)
    assert (k["repay_cents"], k["sale_cents"]) == (500, 300)
    square.claim(conn, k["key"], c, "Will")
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0 and logic.points_balance(conn, c["id"]) == 800


def test_newest_first_old_ones_left_out_and_no_double_claims(conn, cust):
    a, b = cust(), cust(name="Other Kid", email="rns2@x", sq="SQ2")
    square.process_payment(conn, pay("old", 100, cust=None, ts=iso(hours=square.CLAIM_HOURS + 1)), None)
    square.process_payment(conn, pay("p1", 200, cust=None, ts=iso(hours=1)), None)
    square.process_payment(conn, pay("p2", 300, cust=None, ts=iso()), None)
    ks = square.unclaimed(conn)
    assert [k["sale_cents"] for k in ks] == [300, 200]
    square.claim(conn, ks[0]["key"], a, "Will")
    with pytest.raises(ValueError, match="already been claimed"):
        square.claim(conn, ks[0]["key"], b, "Will")                  # two tabs open: second one loses
    assert logic.points_balance(conn, a["id"]) == 300 and logic.points_balance(conn, b["id"]) == 0


def test_sales_already_linked_by_square_never_show(conn, cust):
    cust()
    square.process_payment(conn, pay("p1", 450), None)                   # SQ1 attached in Square
    assert square.unclaimed(conn) == []


def test_claim_pages(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    assert 'data-key="k"' in client.get("/customers/1").text
    assert "No unclaimed sales" in client.get("/customers/1/claim").text
    from fizzy import db
    conn = db.connect()
    try:
        square.process_payment(conn, pay("p1", 450, cust=None, order_id="o1"), order(("Coke", 450), oid="o1"))
        conn.commit()
    finally:
        conn.close()
    page = client.get("/customers/1/claim").text
    assert "Coke" in page and 'data-key="1"' in page and "autofocus" in page
    r = client.post("/customers/1/claim", data={"key": "o:o1"})
    assert "Sale claimed for Nora Le Fevre" in r.text and "450" in r.text
    r = client.post("/customers/1/claim", data={"key": "o:o1"})
    assert "already been claimed" in r.text


def test_claim_page_warns_when_the_tab_isnt_allowed(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    from fizzy import db
    conn = db.connect()
    try:
        square.process_order(conn, tab_order("o1", 300, ("Chips", 300), cust=None))
        conn.commit()
    finally:
        conn.close()
    assert "t put this on a tab" in client.get("/customers/1/claim").text


def test_new_sales_feed_for_the_prompt(client):
    from datetime import datetime, timedelta, timezone
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    page = client.get("/customers/1").text
    assert 'id="claim-prompt"' in page and "Claim for Nora" in page
    from fizzy import db
    conn = db.connect()
    try:
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        square.process_payment(conn, pay("old", 200, cust=None, ts=old), None)       # before the page opened
        square.process_payment(conn, pay("p1", 650, cust=None, order_id="o1"), order(("Coke", 350), ("Chips", 300), oid="o1"))
        square.process_order(conn, tab_order("o2", 300, ("Chips", 300), cust=None))
        conn.commit()
    finally:
        conn.close()
    since = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="seconds")
    sales = client.get(f"/api/customers/1/new-sales?since={since}").json()
    assert len(sales) == 2                                                   # the old one isn't offered
    tab = next(s for s in sales if s["tab"])
    assert tab["tab"] == "$3.00" and tab["tab_warning"] is True               # Nora isn't approved for a tab
    plain = next(s for s in sales if s["paid"])
    assert plain["paid"] == "$6.50" and "Coke" in plain["items"] and plain["tab_warning"] is False


def test_prompt_is_in_the_page_body_not_the_title(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    page = client.get("/customers/1").text
    assert page.index('id="claim-prompt"') > page.index("<body")
