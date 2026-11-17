"""Red Coke and Black Coke memberships (members.py)."""
import json
from datetime import date, timedelta

from fizzy import db, logic, members, square
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import iso, order, pay


def set_term(conn, days_left=30):
    today = date.today()
    db.set_setting(conn, "terms", json.dumps([[(today - timedelta(days=20)).isoformat(),
                                               (today + timedelta(days=days_left)).isoformat(), "Term 4"]]))
    return (today + timedelta(days=days_left)).isoformat()


def with_discount(o, name, cents):
    o["discounts"] = [{"uid": "d1", "name": name, "applied_money": {"amount": cents}}]
    return o


def test_bought_in_square_lasts_the_term_and_earns_no_points(conn, cust):
    c = cust()
    end = set_term(conn)
    square.process_payment(conn, pay("p1", 1500, order_id="o1"), order(("Black Coke membership", 1200), ("Coke", 300), oid="o1"))
    m = members.active(conn, c["id"])
    assert m["tier"] == "black" and m["pct"] == 20 and m["ends_on"] == end
    assert logic.points_balance(conn, c["id"]) == 300       # the drink earns points, the membership doesn't
    assert logic.customer_summary(conn, c)["member"]["name"] == "Black Coke"


def test_a_drink_called_black_coke_is_not_a_membership(conn, cust):
    c = cust()
    square.process_payment(conn, pay("p1", 300, order_id="o1"), order(("Black Coke Zero", 300), oid="o1"))
    assert members.active(conn, c["id"]) is None and logic.points_balance(conn, c["id"]) == 300


def test_refund_takes_the_membership_away_not_points(conn, cust):
    c = cust()
    set_term(conn)
    square.process_payment(conn, pay("p1", 800, order_id="o1"), order(("Red Coke membership", 500), ("Coke", 300), oid="o1"))
    assert logic.points_balance(conn, c["id"]) == 300
    square.process_refund(conn, {"id": "r1", "status": "COMPLETED", "payment_id": "p1", "amount_money": {"amount": 500},
                                 "created_at": iso()})
    assert members.active(conn, c["id"]) is None
    assert logic.points_balance(conn, c["id"]) == 300       # the fee never earned any
    square.process_refund(conn, {"id": "r2", "status": "COMPLETED", "payment_id": "p1", "amount_money": {"amount": 300},
                                 "created_at": iso()})
    assert logic.points_balance(conn, c["id"]) == 0


def test_claimed_later_grants_it_and_unclaiming_takes_it_back(conn, cust):
    c = cust()
    set_term(conn)
    square.process_payment(conn, pay("p1", 500, cust=None, order_id="o1"), order(("Red Coke", 500), oid="o1"))
    assert members.active(conn, c["id"]) is None
    key = square.unclaimed(conn)[0]["key"]
    square.claim(conn, key, c, "Mike")
    assert members.active(conn, c["id"])["tier"] == "red"
    sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = 'p1'").fetchone()
    square.undo_payment_effects(conn, sp)
    assert members.active(conn, c["id"]) is None


def test_one_membership_per_order_with_split_tenders(conn, cust):
    c = cust()
    set_term(conn)
    o = order(("Black Coke membership", 1200), oid="o1")
    square.process_payment(conn, pay("p1", 600, order_id="o1"), o)
    square.process_payment(conn, pay("p2", 600, order_id="o1", source="CARD"), o)
    assert conn.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 1


def test_holidays_buy_the_next_term(conn, cust):
    today = date.today()
    nxt = (today + timedelta(days=60)).isoformat()
    db.set_setting(conn, "terms", json.dumps([[(today - timedelta(days=90)).isoformat(), (today - timedelta(days=10)).isoformat(), "T3"],
                                              [(today + timedelta(days=5)).isoformat(), nxt, "T4"]]))
    assert members.term_end(conn, today) == nxt


def test_discount_check(conn, cust):
    member, other = cust(), cust("Other Kid", "rns00000002@x", "SQ2")
    set_term(conn)
    members.grant(conn, member["id"], "red", iso(1), "Mike")
    # a member who got no discount is owed it back
    square.process_payment(conn, pay("p1", 400, order_id="o1"), order(("Coke", 400), oid="o1"))
    got = members.issues(conn, member["id"])
    assert [i["kind"] for i in got] == ["missed"] and got[0]["owed_cents"] == 40 and "$0.40" in got[0]["text"]
    square.process_payment(conn, pay("p0", 300, order_id="o0", ts=iso(hours=1)), order(("Coke", 300), oid="o0"))
    got = members.issues(conn, member["id"])
    assert len(got) == 1 and got[0]["owed_cents"] == 70 and "on 2 sales" in got[0]["text"]
    # once refunded in Square, it's settled
    for pid, amt in (("p1", 40), ("p0", 30)):
        square.process_refund(conn, {"id": "r" + pid, "status": "COMPLETED", "payment_id": pid, "amount_money": {"amount": amt},
                                     "created_at": iso()})
    assert members.issues(conn, member["id"]) == []
    # a Black discount for a Red member, and any discount for someone who isn't one
    square.process_payment(conn, pay("p2", 320, order_id="o2"), with_discount(order(("Coke", 320), oid="o2"), "Black Coke 20%", 80))
    assert [i["kind"] for i in members.issues(conn, member["id"])] == ["wrong_tier"]
    square.process_payment(conn, pay("p3", 360, cust="SQ2", order_id="o3"), with_discount(order(("Coke", 360), oid="o3"), "Red Coke 10%", 40))
    assert [i["kind"] for i in members.issues(conn, other["id"])] == ["not_member"]
    # the right discount: nothing to say
    square.process_payment(conn, pay("p4", 360, order_id="o4"), with_discount(order(("Coke", 360), oid="o4"), "Red Coke 10%", 40))
    assert [i["kind"] for i in members.issues(conn, member["id"])] == ["wrong_tier"]


def test_given_by_hand_on_the_customer_page(client):  # noqa: F811
    login(client)
    client.post("/import", files={"file": ("d.csv", b"Cleaned Name,Email\nAda Yates,rns1@reddamnorthshore.nsw.edu.au\n", "text/csv")},
                data={"confirm": "1"})
    r = client.post("/customers/1/membership", data={"tier": "black", "note": "paid cash"})
    assert "Black Coke until" in r.text and 'tag member-black' in r.text and "20% off everything" in r.text
    assert "already has Black Coke" in client.post("/customers/1/membership", data={"tier": "red"}).text
    assert client.get("/api/search?q=ada").json()[0]["member"]["tier"] == "black"
    data = client.get("/customers/1", headers={"X-Fizzy-Format": "json"}).json()
    mid = data["memberships"][0]["id"]
    assert data["s"]["member"]["pct"] == 20
    assert "Cancelled Black Coke" in client.post(f"/customers/1/membership/{mid}/cancel").text
    assert client.get("/api/search?q=ada").json()[0]["member"] is None


def test_a_missed_discount_shows_on_the_customer_page(client):  # noqa: F811
    from fizzy import config
    login(client)
    client.post("/import", files={"file": ("d.csv", b"Cleaned Name,Email\nAda Yates,rns1@reddamnorthshore.nsw.edu.au\n", "text/csv")},
                data={"confirm": "1"})
    client.post("/customers/1/membership", data={"tier": "red"})
    conn = db.connect(config.DB_PATH)
    conn.execute("UPDATE customers SET square_customer_id = 'SQ1' WHERE id = 1")
    square.process_payment(conn, pay("p1", 400, order_id="o1"), order(("Coke", 400), oid="o1"))
    conn.commit()
    page = client.get("/customers/1").text
    assert "No Red Coke discount: owed $0.40 back" in page and "1× Coke" in page and "built-in" not in page
