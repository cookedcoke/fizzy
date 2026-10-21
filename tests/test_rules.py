from datetime import datetime, timedelta, timezone

from fizzy import logic, square
from fizzy.customers import parse_directory_csv, search, import_rows


def iso(days_ago=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)).isoformat(timespec="seconds")


def pay(pid, amount, cust="SQ1", source="CASH", ext=None, ts=None, order_id=None):
    p = {"id": pid, "status": "COMPLETED", "amount_money": {"amount": amount}, "source_type": source,
         "customer_id": cust, "created_at": ts or iso(), "order_id": order_id}
    if ext:
        p["external_details"] = ext
    return p


def order(*items, oid=None):
    """Order with line items given as names or (name, cents)."""
    lines = []
    for it in items:
        name, cents = it if isinstance(it, tuple) else (it, 0)
        lines.append({"name": name, "quantity": "1", "total_money": {"amount": cents}})
    return {"id": oid, "line_items": lines}


def tab_order(oid, discount, *items, cust="SQ1", ts=None, returns=None):
    """A completed Square order with the "Fizzy Tab" discount applied (the tab sale)."""
    o = order(*(items or ("Coke",)), oid=oid)
    o.update(state="COMPLETED", customer_id=cust, closed_at=ts or iso(), created_at=ts or iso(),
             discounts=[{"name": "Fizzy Tab", "percentage": "100", "applied_money": {"amount": discount}}])
    if returns:
        o["returns"] = returns
    return o


def reload(conn, c):
    return conn.execute("SELECT * FROM customers WHERE id = ?", (c["id"],)).fetchone()


def test_csv_parsing_keeps_only_name_and_email():
    text = "Original Name,Cleaned Name,Email\nADELINE YATES,Adeline Yates,rns00000001@reddamnorthshore.nsw.edu.au\nX,Bad,someone@gmail.com\n"
    rows, errors = parse_directory_csv(text)
    assert rows == [{"name": "Adeline Yates", "email": "rns00000001@reddamnorthshore.nsw.edu.au"}]
    assert len(errors) == 1


def test_import_dry_run_and_search(conn):
    rows, _ = parse_directory_csv("Cleaned Name,Email\nNora Le Fevre,rns1@reddamnorthshore.nsw.edu.au\nNora Zhang,rns2@reddamnorthshore.nsw.edu.au\n")
    assert import_rows(conn, rows, "Bru", apply=False)["added"] == 2
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0
    import_rows(conn, rows, "Bru")
    assert {c["name"] for c in search(conn, "chloe")} == {"Nora Le Fevre", "Nora Zhang"}
    assert [c["name"] for c in search(conn, "zhnag")] == ["Nora Zhang"]  # typo tolerant
    assert search(conn, "c le")[0]["name"] == "Nora Le Fevre"
    assert [c["name"] for c in search(conn, "rns2")] == ["Nora Zhang"]


def test_sale_awards_points_and_unlock_at_30(conn, cust):
    c = cust()
    assert square.process_payment(conn, pay("p1", 2500), order("Coke")) == "sale"
    assert logic.points_balance(conn, c["id"]) == 2500
    assert logic.tab_status(conn, c)["status"] == "locked"
    square.process_payment(conn, pay("p2", 500), order("Chips"))
    assert logic.tab_status(conn, c)["status"] == "eligible"
    assert not logic.tab_status(conn, c)["can_charge"]  # still needs staff approval
    assert square.process_payment(conn, pay("p2", 500), order("Chips")) is None  # idempotent
    assert logic.points_balance(conn, c["id"]) == 3000


def test_tab_charge_repay_points_and_overdue(conn, cust):
    c = cust()
    logic.set_tab_decision(conn, c["id"], "approved", "good kid", "Bru")
    c = reload(conn, c)
    assert square.process_order(conn, tab_order("o1", 400, ts=iso(days_ago=8)))
    assert not square.process_order(conn, tab_order("o1", 400, ts=iso(days_ago=8)))  # idempotent
    assert logic.points_balance(conn, c["id"]) == 0  # no points until repaid
    st = logic.tab_status(conn, c)
    assert st["status"] == "blocked_overdue" and not st["can_charge"]
    led = logic.tab_ledger(conn, c["id"])
    assert led["owing_cents"] == 400 and led["overdue_cents"] == 400

    logic.add_repayment(conn, c["id"], 500, "cash", "", "Mike")  # overpays by $1
    assert logic.points_balance(conn, c["id"]) == 400  # only the owed part earns points
    led = logic.tab_ledger(conn, c["id"])
    assert led["balance_cents"] == -100 and not led["has_overdue"]
    assert led["late_count"] == 1  # permanent record of paying late
    st = logic.tab_status(conn, c)
    assert st["status"] == "approved" and st["available_cents"] == 1000


def test_limit_breach_is_flagged_but_still_owed(conn, cust):
    c = cust()
    logic.set_tab_decision(conn, c["id"], "approved", "", "Bru")
    c = reload(conn, c)
    logic.add_tab_charge(conn, c, 800, "x", iso())
    logic.add_tab_charge(conn, c, 400, "y", iso())
    charges = conn.execute("SELECT breach FROM tab_charges ORDER BY id").fetchall()
    assert charges[0]["breach"] is None and "Over limit" in charges[1]["breach"]
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 1200


def test_fifo_partial_repayment(conn, cust):
    c = cust()
    logic.add_tab_charge(conn, c, 300, "a", iso(days_ago=3))
    logic.add_tab_charge(conn, c, 300, "b", iso(days_ago=1))
    logic.add_repayment(conn, c["id"], 400, "card", "", "Frankie")
    ch = logic.tab_ledger(conn, c["id"])["charges"]
    assert (ch[0]["paid_cents"], ch[1]["paid_cents"]) == (300, 100)
    assert ch[0]["settled_at"] is not None and ch[1]["settled_at"] is None


def test_refunds_reverse_points_and_tab(conn, cust):
    c = cust()
    square.process_payment(conn, pay("s1", 600), order("Coke"))
    square.process_refund(conn, {"id": "r1", "payment_id": "s1", "status": "COMPLETED", "amount_money": {"amount": 200}, "created_at": iso()})
    assert logic.points_balance(conn, c["id"]) == 400
    assert logic.spend_cents(conn, c["id"]) == 400

    square.process_order(conn, tab_order("t1", 500, ("Snack", 0), ("Coke", 0)))
    logic.add_repayment(conn, c["id"], 300, "cash", "", "Bru")
    assert logic.points_balance(conn, c["id"]) == 700
    # Return the snack ($3 of the tab) from the tab sale: owing drops, and since only $2 was
    # still owed, $1 of the earlier repayment is now credit - its points come back off.
    ret = {"source_order_id": "t1", "return_line_items": [{"name": "Snack", "total_discount_money": {"amount": 300}}]}
    square.process_order(conn, {"id": "t2", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    assert logic.tab_ledger(conn, c["id"])["balance_cents"] == -100
    assert logic.points_balance(conn, c["id"]) == 600
    square.process_order(conn, {"id": "t2", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    assert logic.tab_ledger(conn, c["id"])["balance_cents"] == -100  # returns processed once


def test_square_repayment_matches_manual_entry(conn, cust):
    c = cust()
    logic.add_tab_charge(conn, c, 300, "a", iso(days_ago=1))
    logic.add_repayment(conn, c["id"], 300, "cash", "", "Mike")
    assert square.process_payment(conn, pay("rp1", 300), order(("Tab repayment", 300))) == "tab_repayment"
    reps = conn.execute("SELECT * FROM tab_repayments").fetchall()
    assert len(reps) == 1 and reps[0]["square_payment_id"] == "rp1"
    assert logic.points_balance(conn, c["id"]) == 300


def test_unlinked_payment_applied_after_customer_linked(conn, cust):
    c = cust(sq=None)
    square.process_payment(conn, pay("p1", 250, cust="SQNEW"), order("Coke"))
    assert logic.points_balance(conn, c["id"]) == 0
    res = square.sync_customers(conn, [{"id": "SQNEW", "email_address": c["email"].upper()}])
    assert res["linked"] == 1
    assert square.link_pending(conn) == 1
    assert logic.points_balance(conn, c["id"]) == 250


def test_zero_dollar_cash_payment_on_a_tab_sale_records_nothing_extra(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 300))
    assert square.process_payment(conn, pay("p0", 0, order_id="o1"), order(("Coke", 0), oid="o1")) == "sale"
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 300
    assert conn.execute("SELECT COUNT(*) FROM points_ledger").fetchone()[0] == 0


def test_card_repayment_through_square(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 450))
    assert square.process_payment(conn, pay("rp1", 450, source="CARD"), order(("Tab repayment", 450))) == "tab_repayment"
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0
    assert logic.points_balance(conn, c["id"]) == 450
    assert conn.execute("SELECT method FROM tab_repayments").fetchone()[0] == "card"


def test_repayment_and_purchase_in_one_sale_are_split(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 400))
    # Pays off $4 and buys a $3 drink in the same sale: $7 on one card payment.
    square.process_payment(conn, pay("p1", 700, source="CARD", order_id="o2"), order(("Tab repayment", 400), ("Coke", 300), oid="o2"))
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0
    kinds = dict(conn.execute("SELECT kind, amount_cents FROM square_payments WHERE square_payment_id LIKE 'p1%'").fetchall())
    assert kinds == {"tab_repayment": 400, "sale": 300}
    assert logic.points_balance(conn, c["id"]) == 700  # 400 for the repayment + 300 for the drink


def test_part_tab_part_paid(conn, cust):
    c = cust()
    # $6 sale: $4 put on the tab via the discount, $2 paid by card.
    square.process_order(conn, tab_order("o1", 400, ("Coke", 300), ("Chips", 300)))
    square.process_payment(conn, pay("p1", 200, source="CARD", order_id="o1"), order(("Coke", 300), ("Chips", 300), oid="o1"))
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 400
    assert logic.points_balance(conn, c["id"]) == 200


def test_orders_without_tab_discount_or_customer(conn, cust):
    c = cust()
    plain = tab_order("o1", 0)
    plain["discounts"] = [{"name": "Staff 10%", "applied_money": {"amount": 30}}]
    assert not square.process_order(conn, plain)
    square.process_order(conn, tab_order("o2", 300, cust=None))
    row = conn.execute("SELECT customer_id, kind FROM square_payments").fetchone()
    assert row["customer_id"] is None and row["kind"] == "tab_charge"  # shows under Needs attention
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0


def test_other_tender_is_a_normal_sale_by_default(conn, cust):
    cust()
    assert square.process_payment(conn, pay("o1", 100, source="EXTERNAL", ext={"type": "OTHER"}), order("Coke")) == "sale"


def test_reclassify(conn, cust):
    c = cust()
    square.process_payment(conn, pay("x1", 300, source="CARD"), order("Coke"))
    assert logic.points_balance(conn, c["id"]) == 300
    square.reclassify(conn, "x1", "tab_charge", "Bru")
    assert logic.points_balance(conn, c["id"]) == 0
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 300


def test_manual_points_need_reason(conn, cust):
    c = cust()
    import pytest
    with pytest.raises(ValueError):
        logic.adjust_points(conn, c["id"], 100, " ", "Bru")
    logic.adjust_points(conn, c["id"], -50, "event", "Bru")
    assert logic.points_balance(conn, c["id"]) == -50
    assert logic.spend_cents(conn, c["id"]) == 0  # manual points don't count towards unlock


def test_webhook_signature():
    import base64, hashlib, hmac
    body = b'{"type":"payment.updated"}'
    sig = base64.b64encode(hmac.new(b"k", b"https://x/square/webhook" + body, hashlib.sha256).digest()).decode()
    assert square.verify_webhook(body, sig, "k", "https://x/square/webhook")
    assert not square.verify_webhook(body + b" ", sig, "k", "https://x/square/webhook")
