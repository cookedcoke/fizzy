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


def order(*names):
    return {"line_items": [{"name": n, "quantity": "1"} for n in names]}


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
    assert [c["name"] for c in search(conn, "chloe")] == ["Nora Le Fevre", "Nora Zhang"]
    assert [c["name"] for c in search(conn, "zhnag")] == ["Nora Zhang"]  # typo tolerant
    assert [c["name"] for c in search(conn, "c le")] == ["Nora Le Fevre"]
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
    tab = {"source": "Fizzy Tab", "type": "OTHER"}
    assert square.process_payment(conn, pay("t1", 400, source="EXTERNAL", ext=tab, ts=iso(days_ago=8)), order("Coke")) == "tab_charge"
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
    logic.add_repayment(conn, c["id"], 400, "payid", "", "Frankie")
    ch = logic.tab_ledger(conn, c["id"])["charges"]
    assert (ch[0]["paid_cents"], ch[1]["paid_cents"]) == (300, 100)
    assert ch[0]["settled_at"] is not None and ch[1]["settled_at"] is None


def test_refunds_reverse_points_and_tab(conn, cust):
    c = cust()
    square.process_payment(conn, pay("s1", 600), order("Coke"))
    square.process_refund(conn, {"id": "r1", "payment_id": "s1", "status": "COMPLETED", "amount_money": {"amount": 200}, "created_at": iso()})
    assert logic.points_balance(conn, c["id"]) == 400
    assert logic.spend_cents(conn, c["id"]) == 400

    tab = {"source": "Fizzy Tab", "type": "OTHER"}
    square.process_payment(conn, pay("t1", 500, source="EXTERNAL", ext=tab), order("Snack"))
    logic.add_repayment(conn, c["id"], 500, "cash", "", "Bru")
    assert logic.points_balance(conn, c["id"]) == 900
    # Refund the already-repaid tab sale: customer is now in credit and loses those points.
    square.process_refund(conn, {"id": "r2", "payment_id": "t1", "status": "COMPLETED", "amount_money": {"amount": 500}, "created_at": iso()})
    assert logic.tab_ledger(conn, c["id"])["balance_cents"] == -500
    assert logic.points_balance(conn, c["id"]) == 400


def test_square_repayment_matches_manual_entry(conn, cust):
    c = cust()
    logic.add_tab_charge(conn, c, 300, "a", iso(days_ago=1))
    logic.add_repayment(conn, c["id"], 300, "cash", "", "Mike")
    assert square.process_payment(conn, pay("rp1", 300), order("Tab repayment")) == "tab_repayment"
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


def test_reclassify(conn, cust):
    c = cust()
    square.process_payment(conn, pay("x1", 300, source="EXTERNAL", ext={"source": "Something"}), order("Coke"))
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
