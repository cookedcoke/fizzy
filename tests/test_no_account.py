"""No account: a real sale nobody gets (a teacher, a student who'd rather not record it, anyone without
an account). It still counts in takings and reports, but it's never claimed, linked or matched to a
Claim sale press (square.mark_no_account / undo_no_account)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from fizzy import db, pin, square, stats, web
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_claim_window import JSON, T, at, on, owner, put, sale, waits, when  # noqa: F401  (fixture)
from tests.test_review_fixes import FakeSquare
from tests.test_rules import order, pay

GRACE = square.CLAIM_GRACE


def marked(conn, pid):
    return conn.execute("SELECT no_account, no_account_by FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone()


def keys(conn, **kw):
    return [k["key"] for k in square.unclaimed(conn, **kw)]


def now_ts(sec=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat(timespec="seconds")


# ---- marking ----------------------------------------------------------------------------------------

def test_marking_takes_it_out_of_unclaimed_and_keeps_its_kind(conn):
    sale(conn, "p1", 450, -10, oid="o1")
    sale(conn, "p2", 300, -5)
    k = square.mark_no_account(conn, "o:o1", "Will")
    assert k["key"] == "o:o1" and k["sale_cents"] == 450
    assert keys(conn) == ["p:p2"]
    row = conn.execute("SELECT kind, customer_id, no_account, no_account_by FROM square_payments WHERE square_payment_id = 'p1'").fetchone()
    assert row["kind"] == "sale" and row["customer_id"] is None and row["no_account"] and row["no_account_by"] == "Will"
    assert square.mark_no_account(conn, "o:o1", "Will") is None          # again: nothing new
    [a] = conn.execute("SELECT * FROM audit_log WHERE action = 'no_account'").fetchall()
    assert a["staff"] == "Will" and "o:o1" in a["details"] and "$4.50" in a["details"]
    assert web.ACTION_LABELS["no_account"] and web.ACTION_LABELS["no_account_undo"]


def test_every_part_of_a_split_checkout_is_marked(conn):
    o = order(("Coke", 300), ("Chips", 200), oid="o1")
    o["tenders"] = [{"payment_id": "a"}, {"payment_id": "b"}]
    square.process_payment(conn, pay("a", 300, cust=None, ts=at(-10), order_id="o1"), o)
    square.process_payment(conn, pay("b", 200, cust=None, ts=at(-9), order_id="o1"), o)
    square.mark_no_account(conn, "o:o1", "Will")
    assert marked(conn, "a")["no_account"] and marked(conn, "b")["no_account"]


def test_a_late_part_of_a_marked_checkout_is_marked_too(conn, cust):
    cust(sq="SQ1")
    sale(conn, "a", 300, -10, oid="o1")
    square.mark_no_account(conn, "o:o1", "Will")
    square.process_payment(conn, pay("b", 200, cust="SQ1", ts=at(-9), order_id="o1"), order(("Coke", 500), oid="o1"))
    assert marked(conn, "b")["no_account_by"] == "Will" and owner(conn, "b") is None and owner(conn, "a") is None
    assert conn.execute("SELECT COUNT(*) FROM points_ledger").fetchone()[0] == 0


def test_a_claimed_sale_cant_be_marked(conn, cust):
    c = cust()
    sale(conn, "p1", 450, -10)
    square.claim(conn, "p:p1", c, "Will")
    with pytest.raises(ValueError, match="already someone's"):
        square.mark_no_account(conn, "p:p1", "Will")
    assert marked(conn, "p1")["no_account"] is None


def test_tab_money_cant_be_marked(conn):
    square.process_payment(conn, pay("p1", 300, cust=None, ts=at(-10), order_id="o1"), order(("Coke", 300), oid="o1"))
    conn.execute("UPDATE square_payments SET kind = 'tab_charge' WHERE square_payment_id = 'p1'")
    with pytest.raises(ValueError, match="needs a customer"):
        square.mark_no_account(conn, "o:o1", "Will")
    with pytest.raises(ValueError, match="hasn't seen"):
        square.mark_no_account(conn, "o:nope", "Will")


def test_a_marked_sale_cant_be_claimed_or_assigned(conn, cust):
    c = cust()
    sale(conn, "p1", 450, -10)
    square.mark_no_account(conn, "p:p1", "Will")
    with pytest.raises(ValueError, match="No account"):
        square.claim(conn, "p:p1", c, "Will")
    with pytest.raises(ValueError, match="No account"):
        square.assign_customer(conn, "p1", c, "Will")
    assert owner(conn, "p1") is None


# ---- the Claim sale button --------------------------------------------------------------------------

def test_a_press_with_only_a_no_account_sale_waits_then_expires(conn, cust):
    c = cust()
    sale(conn, "teacher", 450, -10)
    square.mark_no_account(conn, "p:teacher", "Will")
    assert square.claim_next(conn, c, "Will", now=when(0))[0] == "waiting"
    assert square.claim_waiting(conn, now=when(GRACE + 1)) == 0
    assert waits(conn)[0]["status"] == "expired" and owner(conn, "teacher") is None


def test_a_press_skips_a_stray_no_account_sale_for_the_real_one(conn, cust):
    c = cust()
    sale(conn, "real", 300, -20)
    sale(conn, "teacher", 450, -5)                                       # newer: without the mark, the press takes it
    square.mark_no_account(conn, "p:teacher", "Will")
    what, k = square.claim_next(conn, c, "Will", now=when(0))
    assert what == "claimed" and k["key"] == "p:real"
    assert owner(conn, "real") == c["id"] and owner(conn, "teacher") is None


def test_undo_lets_a_waiting_press_take_it(conn, cust):
    c = cust()
    sale(conn, "p1", 450, -10)
    square.mark_no_account(conn, "p:p1", "Will")
    assert square.claim_next(conn, c, "Will", now=when(0))[0] == "waiting"
    square.undo_no_account(conn, "p:p1", "Will")
    assert square.claim_waiting(conn, now=when(5)) == 1 and owner(conn, "p1") == c["id"]


# ---- the money still counts -------------------------------------------------------------------------

def test_takings_and_stats_are_the_same_marked_or_not(conn):
    square.process_payment(conn, pay("p1", 450, cust=None, ts=now_ts(-60), order_id="o1"), order(("Coke", 450), oid="o1"))
    square.process_payment(conn, pay("p2", 300, cust=None, ts=now_ts(-30)), None)
    day = stats.today()

    def numbers():
        s = stats.summary(conn, day)
        return ({k: s[k] for k in ("orders", "sold_cents", "taken_cents", "avg_cents", "walkups")},
                stats.top_items(s["checkouts"]), stats.profit(s["checkouts"], stats.costs(conn)))
    before = numbers()
    square.mark_no_account(conn, "o:o1", "Will")
    after = numbers()
    assert before == after and before[0]["sold_cents"] == 750 and before[1][0]["name"] == "Coke"
    assert [c["no_account"] for c in stats.summary(conn, day)["checkouts"]] == [True, False]


def test_takings_tool_counts_it(on):
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=now_ts(-60)), None))
    d = stats.today().isoformat()
    before = on.post("/tools/takings", data={"start": d, "end": d}).text
    assert put(lambda c: square.mark_no_account(c, "p:p1", "Mike"))
    after = on.post("/tools/takings", data={"start": d, "end": d}).text
    assert "$4.50" in before and before.count("$4.50") == after.count("$4.50")


# ---- syncs, Square customers, undo ------------------------------------------------------------------

def test_a_sync_doesnt_undo_it_and_link_pending_doesnt_link_it(conn, cust):
    p = pay("p1", 450, cust="SQNEW", ts=now_ts(-10))
    square.process_payment(conn, p, None)                                # a Square profile Fizzy doesn't know yet
    square.mark_no_account(conn, "p:p1", "Will")
    c = cust(sq="SQNEW")                                                 # now it does
    assert square.link_pending(conn) == 0
    square.sync(conn, client=FakeSquare(customers=[{"id": "SQNEW", "reference_id": f"fizzy-{c['id']}"}], payments=[p]))
    assert marked(conn, "p1")["no_account"] and owner(conn, "p1") is None
    assert conn.execute("SELECT COUNT(*) FROM points_ledger").fetchone()[0] == 0


def test_undo_makes_it_a_normal_unclaimed_sale(conn):
    sale(conn, "p1", 450, -10)
    square.mark_no_account(conn, "p:p1", "Will")
    k = square.undo_no_account(conn, "p:p1", "Amy")
    assert k["key"] == "p:p1" and marked(conn, "p1")["no_account"] is None and keys(conn) == ["p:p1"]
    assert conn.execute("SELECT staff FROM audit_log WHERE action = 'no_account_undo'").fetchone()[0] == "Amy"
    with pytest.raises(ValueError, match="isn't marked"):
        square.undo_no_account(conn, "p:p1", "Amy")


def test_undo_gives_it_to_the_square_customer_on_it(conn, cust):
    square.process_payment(conn, pay("p1", 450, cust="SQNEW", ts=now_ts(-10)), None)
    square.mark_no_account(conn, "p:p1", "Will")
    c = cust(sq="SQNEW")
    square.undo_no_account(conn, "p:p1", "Will")
    assert owner(conn, "p1") == c["id"] and conn.execute("SELECT SUM(delta) FROM points_ledger").fetchone()[0] == 450


# ---- the pages --------------------------------------------------------------------------------------

def test_the_pop_up_pick_a_sale_and_the_square_page(on):
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=now_ts(-5)), None))
    assert 'Square payments with no customer</td><td class="">1</td>' in on.post("/tools/integrity").text
    since = now_ts(-60)
    assert [s["key"] for s in on.get(f"/api/customers/1/new-sales?since={since}").json()] == ["p:p1"]
    page = on.get("/customers/1/claim").text
    assert 'form="no-account" name="key" value="p:p1"' in page and 'id="no-account"' in page
    assert 'class="cp-noacct" formaction="/square/no-account"' in on.get("/customers/1").text
    sq = on.get("/square").text
    assert "1 sale this week with nobody on it" in sq and 'action="/square/no-account"' in sq

    r = on.post("/square/no-account", data={"key": "p:p1"}, headers={"referer": "http://testserver/customers/1"})
    assert r.url.path == "/customers/1" and "Marked No account" in r.text
    assert on.get(f"/api/customers/1/new-sales?since={since}").json() == []
    assert 'value="p:p1"' not in on.get("/customers/1/claim").text
    sq = on.get("/square").text
    assert "this week with nobody on" not in sq and ">No account</span>" in sq and 'action="/square/no-account/undo"' in sq
    assert "p1" not in on.get("/square?show=unlinked").text
    assert ">No account</span>" in on.get("/transactions").text
    assert 'Square payments with no customer</td><td class="">0</td>' in on.post("/tools/integrity").text

    r = on.post("/square/no-account/undo", data={"key": "p:p1"}, headers={"referer": "http://testserver/square"})
    assert r.url.path == "/square" and "nobody on it again" in r.text and "1 sale this week with nobody on it" in r.text
    assert [s["key"] for s in on.get(f"/api/customers/1/new-sales?since={since}").json()] == ["p:p1"]


def test_json_answers(on):
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=now_ts(-5)), None))
    r = on.post("/square/no-account", data={"key": "p:p1"}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["no_account"] is True
    r = on.post("/square/no-account/undo", data={"key": "p:p1"}, headers=JSON)
    assert r.json()["no_account"] is False
    on.post("/customers/1/claim", data={"key": "p:p1"})
    r = on.post("/square/no-account", data={"key": "p:p1"}, headers=JSON)
    assert r.status_code == 400 and "already someone's" in r.json()["error"]
    r = on.post("/square/no-account", data={"key": "p:p1"}, headers={"referer": "http://testserver/square"})
    assert "already someone&#39;s" in r.text and put(lambda c: marked(c, "p1")["no_account"]) is None


def test_passcode_level_is_the_claim_level(client):
    conn = db.connect()
    try:
        db.init(conn)
        pin.set_pin(conn, "4821", "t")
        r = lambda p: pin.required(conn, "POST", p, {"key": "p:x"})  # noqa: E731
        assert r("/square/no-account") == pin.OPEN == r("/square/no-account/undo")
        db.set_setting(conn, "permissions", json.dumps({"claim": "pin"}))
        assert r("/square/no-account") == pin.PIN == r("/square/no-account/undo") == r("/customers/1/claim")
        acts = pin.rules(conn)["actions"]
        assert ["POST", r"^/square/no-account(/undo)?$", pin.PIN] in acts
    finally:
        conn.close()
