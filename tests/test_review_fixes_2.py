from datetime import datetime, timedelta

import pytest

from fizzy import config, db, logic, pin
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_errors import _customer
from tests.test_pin import _unlock, owner  # noqa: F401  (fixture)


@pytest.fixture
def rules(client, owner):
    conn = db.connect()
    pin.set_pin(conn, "4821", "t")
    conn.commit()
    yield conn, (lambda path, form: pin.required(conn, "POST", path, form))
    conn.close()


def test_odd_number_formats_count_as_big(rules):
    conn, r = rules
    _customer()
    assert r("/customers/1/points", {"delta": "99 999"}) == pin.OWNER
    assert r("/customers/1/points", {"delta": "lots"}) == pin.OWNER
    assert r("/customers/1/points", {"delta": "1e9"}) == pin.OWNER
    assert r("/customers/1/points", {"delta": " 40 "}) == pin.PIN


def test_blank_write_off_and_square_payment_moves_are_big(rules):
    conn, r = rules
    _customer()
    assert r("/tools/write_off", {"customer": "Test Kid", "amount": "", "note": "x"}) == pin.OWNER
    conn.execute("INSERT INTO square_payments(square_payment_id, customer_id, kind, amount_cents, created_at, processed_at) "
                 "VALUES ('P1', 1, 'tab_charge', 500, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO square_payments(square_payment_id, customer_id, kind, amount_cents, created_at, processed_at) "
                 "VALUES ('P2', NULL, 'sale', 300, ?, ?)", (db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO square_payments(square_payment_id, customer_id, kind, amount_cents, created_at, processed_at) "
                 "VALUES ('P3', NULL, 'sale', 300, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    assert r("/square/payments/P1/assign", {"customer": "Test Kid"}) == pin.OWNER   # someone's tab money
    assert r("/square/payments/P1/kind", {"kind": "sale"}) == pin.OWNER
    assert r("/square/payments/P2/assign", {"customer": "Test Kid"}) == pin.OPEN     # a fresh unclaimed sale: like claiming
    assert r("/square/payments/P3/assign", {"customer": "Test Kid"}) == pin.PIN      # an old one would pad a tab score


def test_double_points_rules(rules):
    conn, r = rules
    assert r("/events", {"multiplier": "2", "start": "2030-01-01", "end": "2030-01-01"}) == pin.PIN
    assert r("/events", {"multiplier": "2", "start": "2030-01-01", "weekly": "1"}) == pin.OWNER
    assert r("/events", {"multiplier": "5", "start": "2030-01-01"}) == pin.OWNER
    assert r("/events", {"multiplier": "2", "start": "2030-01-01", "end": "2030-02-01"}) == pin.OWNER


def test_test_accounts_cant_redeem_freely(rules, client):
    conn, r = rules
    login(client)
    _unlock(client, "owner-pw", 2)
    client.post("/tools/test_accounts", data={"n": "1"})
    assert r("/customers/1/points", {"delta": "50000", "note": "x"}) == pin.OPEN
    assert r("/customers/1/redeem/1", {}) == pin.PIN
    assert r("/customers/1/edit", {"name": "x", "square_customer_id": "SQREAL"}) == pin.PIN


def test_owner_gets_through_the_overall_cap(client, owner, monkeypatch):
    login(client)
    monkeypatch.setattr(pin, "FAIL_LIMIT_ALL", 0)
    assert "Too many" in _unlock(client, "1111")["error"]
    assert _unlock(client, "owner-pw", 2)["ok"] is True


def test_write_off_cant_go_past_owing(client):
    conn = db.connect()
    try:
        cid = _customer()
        with pytest.raises(ValueError):
            logic.add_repayment(conn, cid, 500, "writeoff", "x", "t")
    finally:
        conn.close()


def test_everyone_skips_test_and_admin(client):
    login(client)
    real = _customer()
    client.post("/tools/test_accounts", data={"n": "1"})
    client.post("/tools/bulk_points", data={"delta": "5", "note": "x", "everyone": "1"})
    conn = db.connect()
    try:
        got = dict(conn.execute("SELECT customer_id, SUM(delta) FROM points_ledger GROUP BY customer_id").fetchall())
    finally:
        conn.close()
    assert got == {real: 5}


def test_transactions_survives_silly_dates(client):
    for q in ("start=0001-01-01&end=9999-12-31", "start=9999-12-31", "start=nope&end=2026-13-40"):
        assert client.get(f"/transactions?{q}").status_code == 200


def test_assign_moves_the_whole_checkout(client):
    conn = db.connect()
    _customer()
    for pid, kind in (("order:O1", "tab_charge"), ("PAY1", "sale")):
        conn.execute("INSERT INTO square_payments(square_payment_id, square_order_id, customer_id, kind, amount_cents, created_at, processed_at) "
                     "VALUES (?, 'O1', NULL, ?, 300, ?, ?)", (pid, kind, db.now_iso(), db.now_iso()))
    conn.commit()
    from fizzy import square
    square.assign_customer(conn, "PAY1", conn.execute("SELECT * FROM customers WHERE id = 1").fetchone(), "t")
    assert {r[0] for r in conn.execute("SELECT customer_id FROM square_payments")} == {1}
    conn.close()


def test_score_counts_write_offs_net_and_top_band_gets_the_full_limit(client):
    conn = db.connect()
    _customer()
    for amt in (500, -500):
        conn.execute("INSERT INTO tab_repayments(customer_id, amount_cents, method, note, staff, created_at) "
                     "VALUES (1, ?, 'writeoff', '', 't', ?)", (amt, db.now_iso()))
    sc = logic.tab_score(conn, 1)
    assert not any("written off" in label for label, _ in sc["factors"])
    conn.close()
    assert logic.band_limit({"tab_limit_cents": 2525}, 1.0) == 2525
    assert logic.band_limit({"tab_limit_cents": 2525}, 0.5) == 1250


def test_odd_ids_dont_dodge_the_rules(client, owner):
    login(client)
    _customer()
    for bad in ("+1", "%201", "1.0", "1_0"):
        r = client.post(f"/customers/{bad}/kind", data={"kind": "admin"}, follow_redirects=False)
        assert r.status_code == 404, bad
    conn = db.connect()
    assert conn.execute("SELECT kind FROM customers WHERE id = 1").fetchone()[0] != "admin"
    conn.close()


def test_archived_namesake_doesnt_hide_an_admin(rules):
    conn, r = rules
    _customer()
    conn.execute("UPDATE customers SET kind = 'admin' WHERE id = 1")
    name = conn.execute("SELECT name FROM customers WHERE id = 1").fetchone()[0]
    conn.execute("INSERT INTO customers(name, email, created_at, archived_at) VALUES (?, 'x@x', ?, ?)", (name, db.now_iso(), db.now_iso()))
    conn.commit()
    assert r("/tools/transfer_points", {"from": name, "to": "someone", "points": "5"}) == pin.OWNER


def test_pin_stops_working_after_a_day_of_guessing(client, owner, monkeypatch):
    conn = db.connect()
    pin.set_pin(conn, "4821", "t")
    import time as _t
    db.set_setting(conn, "pin_wrong_day", f"{_t.strftime('%Y-%m-%d')}:{pin.DAY_LIMIT}")
    conn.commit()
    conn.close()
    login(client)
    assert client.post("/settings/pin/unlock", data={"pin": "4821", "need": 1}).json()["ok"] is False
    assert client.post("/settings/pin/unlock", data={"pin": "owner-pw", "need": 1}).json()["ok"] is True


def test_small_changes_add_up(rules):
    conn, r = rules
    _customer()
    for _ in range(4):
        conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, staff, created_at) VALUES (1, 400, 'manual', 'Will', ?)", (db.now_iso(),))
    conn.commit()
    assert pin.required(conn, "POST", "/customers/1/points", {"delta": "400"}, "Mike") == pin.PIN   # someone else: fine
    assert pin.required(conn, "POST", "/customers/1/points", {"delta": "400"}, "Will") == pin.OWNER  # 2000 in an hour


def test_closed_days_reach_the_website(client, owner):
    login(client)
    _unlock(client, "owner-pw", 2)
    assert "Couldn" in client.post("/settings/closed", data={"days": "next tuesday"}).text
    client.post("/settings/closed", data={"days": "2099-12-12 to 2100-01-31 Summer holidays\n2099-01-26 Australia Day\n\n"})
    assert "2099-12-12 to 2100-01-31 Summer holidays" in client.get("/settings").text
    p = client.get("/me/promos.json").json()
    assert p["closed"][0] == {"start": "2099-01-26", "end": "2099-01-26", "label": "Australia Day"}
    assert p["rules"]["score_needed"] == 60 and p["rules"]["due_days"] == 7


def test_test_accounts_hand_repayments_left_out_of_transactions_repaid(client):
    from fizzy import stats
    login(client)
    cid = _customer()
    conn = db.connect()
    tid = conn.execute("INSERT INTO customers(name, email, kind, created_at) VALUES ('Fake', 'f@x', 'test', ?)", (db.now_iso(),)).lastrowid
    for c in (cid, tid):
        conn.execute("INSERT INTO tab_charges(customer_id, amount_cents, items, charged_at, due_at) VALUES (?, 500, 'x', ?, ?)",
                     (c, db.now_iso(), db.now_iso()))
    logic.add_repayment(conn, cid, 200, "cash", "", "Mike")
    logic.add_repayment(conn, tid, 300, "cash", "", "Mike")  # a test account: not real money
    logic.add_repayment(conn, cid, 100, "writeoff", "", "Mike")
    conn.commit()
    s = stats.summary(conn, stats.today())
    conn.close()
    assert s["taken_cents"] == 200   # the dashboard leaves the test account's cash out
    from fizzy import transactions
    seen = {}
    real = transactions.render
    transactions.render = lambda request, tpl, **kw: seen.update(kw) or real(request, tpl, **kw)
    try:
        client.get("/transactions")
    finally:
        transactions.render = real
    assert seen["totals"]["repaid"] == 200


def test_customer_display(client):
    assert client.get("/display", follow_redirects=False).status_code == 303          # staff only
    login(client)
    cid = _customer()
    assert "Say your name when you pay" in client.get("/display").text
    page = client.get(f"/customers/{cid}").text
    assert 'id="display-msg"' in page and '"type": "show"' in page
    d = client.get(f"/api/display/customer/{cid}").json()
    assert set(d) == {"id", "first", "points", "ready", "next", "tab", "streak", "today", "rank", "boost", "new", "owing"}
    assert " " not in d["first"]
    assert client.get("/api/display/customer/99999").status_code == 404
    assert "events" in client.get("/api/display/idle").json()


def test_qr_sign_in(client, monkeypatch):
    from fastapi.testclient import TestClient
    from fizzy import portal
    monkeypatch.setattr(portal.secrets, "token_urlsafe", lambda n: "tok-123")
    login(client)
    cid = _customer()
    d = client.post(f"/customers/{cid}/qr-login").json()
    assert d["svg"].startswith("<svg") and d["seconds"] == 60
    assert client.get(f"/api/display/qr/{d['id']}").json()["state"] == "waiting"
    phone = TestClient(client.app)
    r = phone.get("/me/q/tok-123", follow_redirects=False)
    assert r.status_code == 303
    assert "points" in phone.get("/me").text.lower() and "Let me in" not in phone.get("/me").text   # signed in, no passcode needed
    assert client.get(f"/api/display/qr/{d['id']}").json()["state"] == "used"
    other = TestClient(client.app)
    assert "no good" in other.get("/me/q/tok-123").text                                           # works once
    client.post(f"/customers/{cid}/passcode/signout")
    assert "Let me in" in phone.get("/me").text                                                   # signed out remotely
    monkeypatch.setattr(portal.secrets, "token_urlsafe", lambda n: "tok-456")
    d2 = client.post(f"/customers/{cid}/qr-login").json()
    conn = db.connect(); conn.execute("UPDATE qr_logins SET expires_at = 1 WHERE id = ?", (d2["id"],)); conn.commit(); conn.close()
    assert "no good" in TestClient(client.app).get("/me/q/tok-456").text                          # expired
    assert "no good" in TestClient(client.app).get("/me/q/nonsense").text


def test_pick_display_menu_items(client, owner):
    conn = db.connect()
    for i, (n, var, p) in enumerate([("Coke", "Can", 300), ("Coke", "Mini", 200), ("Fanta", None, 300), ("Tab repayment", None, None)]):
        conn.execute("INSERT INTO catalog_items VALUES (?,?,?,?,?,?,0,?)", (f"v{i}", f"i-{n}", n, var, f"{n} ({var})" if var else n, p, db.now_iso()))
    conn.commit(); conn.close()
    login(client)
    _unlock(client, "owner-pw", 2)
    menu = client.get("/api/display/idle").json()["menu"]
    assert menu == [{"name": "Coke", "price": "$2.00"}, {"name": "Fanta", "price": "$3.00"}]   # no variations, nothing unpriced
    assert 'value="i-Fanta" checked' in client.get("/settings").text
    client.post("/settings/display", data={"message": "", "owing": "1", "menu": "1", "show": ["i-Coke"]})
    assert [m["name"] for m in client.get("/api/display/idle").json()["menu"]] == ["Coke"]
    assert 'value="i-Fanta" >' in client.get("/settings").text                                 # unticked now


@pytest.mark.badge_points
def test_badges_pay_points_once(client):
    from fizzy import badges
    conn = db.connect()
    cid = _customer()
    conn.execute("INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, created_at) VALUES (?, 300, 300, 'purchase', ?)", (cid, db.now_iso()))
    conn.commit()
    assert badges.award(conn, cid) == ["First sip"]
    assert badges.award(conn, cid) == []                                       # only once
    conn.commit()
    assert logic.points_balance(conn, cid) == 300 + 50
    login(client)
    client.post("/logout")
    conn.close()


@pytest.mark.badge_points
def test_badges_already_had_dont_pay_when_switched_on(conn, cust):
    from fizzy import badges
    c = cust()
    conn.execute("INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, created_at) VALUES (?, 300, 300, 'purchase', ?)", (c["id"], db.now_iso()))
    conn.execute("DELETE FROM settings WHERE key = 'badge_points_baseline'")
    badges.baseline(conn)
    assert badges.award(conn, c["id"]) == [] and logic.points_balance(conn, c["id"]) == 300


def test_test_sales_and_closed_days_dont_break_streaks(conn, cust):
    import json as _json
    from fizzy import badges, square
    from tests.test_custom_rules import at
    from tests.test_rules import order, pay
    c = cust()
    t = cust("Test Acct", "rns00000009@reddamnorthshore.nsw.edu.au", "SQT")
    conn.execute("UPDATE customers SET kind = 'test' WHERE id = ?", (t["id"],))

    def buy(pid, days, who="SQ1"):
        square.process_payment(conn, pay(pid, 300, cust=who, ts=at(days), order_id=f"o{pid}"), order(("Coke", 300), oid=f"o{pid}"))
    buy("a", 6); buy("b", 4)                       # Mia buys 6 and 4 days ago
    buy("t", 5, "SQT")                             # a staff test sale on the day between
    assert badges.for_customer(conn, c["id"])["best"] == 2
    buy("c", 2); buy("x", 3, "SQ2")                # someone else (not linked) buys on the day between
    assert badges.for_customer(conn, c["id"])["best"] == 2   # a real trading day she missed does break it
    day = (datetime.now(config.TZ).date() - timedelta(days=3)).isoformat()
    db.set_setting(conn, "closed_dates", _json.dumps([[day, day, ""]]))
    assert badges.for_customer(conn, c["id"])["best"] == 3   # marked closed: doesn't count


def test_long_jobs_run_in_the_background(client):
    import time as _t
    from fizzy import jobs
    login(client)
    assert client.get("/api/job").json()["name"] is None
    assert jobs.start("Testing a slow job", lambda c: (_t.sleep(0.3), "All done, 3 payments.")[1], "Will")
    assert not jobs.start("Another", lambda c: "x", "Will")                    # one at a time
    j = client.get("/api/job").json()
    assert j["running"] and j["name"] == "Testing a slow job"
    assert 'id="jobbar"' in client.get("/").text
    for _ in range(50):
        if not client.get("/api/job").json()["running"]:
            break
        _t.sleep(0.05)
    j = client.get("/api/job").json()
    assert j["ok"] and j["message"] == "All done, 3 payments."
    assert jobs.start("Broken", lambda c: 1 / 0, "Will")
    _t.sleep(0.2)
    assert client.get("/api/job").json()["ok"] is False                        # a crash is reported, not lost


def test_display_coming_up_shows_a_weekly_event_once(client):
    from datetime import date as _d
    from fizzy import events, stats
    conn = db.connect()
    t = stats.today() + timedelta(days=1)
    events.create_rule(conn, "Double points Friday", "2", t.isoformat(), "", "Will")  # books two weeks ahead
    events.create(conn, "Exam week", "3", (t + timedelta(days=3)).isoformat(), "", "Will")
    conn.commit(); conn.close()
    login(client)
    soon = client.get("/api/display/idle").json()["soon"]
    assert [s["name"] for s in soon] == ["Double points Friday", "Exam week"] and soon[0]["weekly"]


def test_history_sales_dont_count_as_nobody_on_them(client):
    from fizzy import square
    from tests.test_custom_rules import at
    from tests.test_rules import order, pay
    conn = db.connect()
    db.set_setting(conn, "history_before", at(2, hour=0))
    for i in range(3):  # imported history: no customers, as expected
        square.process_payment(conn, pay(f"h{i}", 300, cust=None, ts=at(4 + i), order_id=f"oh{i}"), order(("Coke", 300), oid=f"oh{i}"))
    square.process_payment(conn, pay("live", 300, cust=None, ts=at(1), order_id="olive"), order(("Coke", 300), oid="olive"))
    conn.commit(); conn.close()
    login(client)
    assert "1 sale this week with nobody on it" in client.get("/square").text
    page = client.get("/transactions?start=2020-01-01&show=nobody").text
    assert page.count('class="assign"') == 1


def test_not_a_real_sale_drops_out_of_everything(client, owner):
    from fizzy import square, stats
    from tests.test_custom_rules import at
    from tests.test_rules import order, pay
    conn = db.connect()
    cid = _customer()
    conn.execute("UPDATE customers SET square_customer_id = 'SQ1' WHERE id = ?", (cid,))
    square.process_payment(conn, pay("big", 6000, ts=at(1), order_id="ob"), order(("Custom amount", 6000), oid="ob"))
    square.process_payment(conn, pay("ok", 300, ts=at(1), order_id="oo"), order(("Coke", 300), oid="oo"))
    conn.commit()
    lo, hi = stats.day_bounds(stats.today() - timedelta(days=1))
    assert sum(c["sold_cents"] for c in stats.checkouts(conn, lo, hi)) == 6300
    login(client)
    assert "Enter the passcode" in client.post("/square/payments/big/kind", data={"kind": "void"}).text   # big money: owner
    _unlock(client, "owner-pw", 2)
    client.post("/square/payments/big/kind", data={"kind": "void"})
    assert sum(c["sold_cents"] for c in stats.checkouts(conn, lo, hi)) == 300                          # gone from the numbers
    assert logic.points_balance(conn, cid) == 300                                                       # and its points with it
    conn.close()


def test_payments_over_100_dollars_are_not_real_sales(conn, cust):
    from fizzy import square, stats
    from tests.test_custom_rules import at
    from tests.test_rules import order, pay
    c = cust()
    square.process_payment(conn, pay("huge", 100000000, ts=at(1), order_id="oh"), order(("Custom amount", 100000000), oid="oh"))
    square.process_payment(conn, pay("fine", 9900, ts=at(1), order_id="of"), order(("Lots of Coke", 9900), oid="of"))
    lo, hi = stats.day_bounds(stats.today() - timedelta(days=1))
    assert sum(x["sold_cents"] for x in stats.checkouts(conn, lo, hi)) == 9900
    assert logic.points_balance(conn, c["id"]) == 9900
    assert conn.execute("SELECT kind FROM square_payments WHERE square_payment_id = 'huge'").fetchone()[0] == "void"
    # one that came in before the limit existed gets fixed at start-up
    conn.execute("UPDATE square_payments SET kind = 'sale' WHERE square_payment_id = 'huge'")
    assert square.void_huge(conn) == 1 and sum(x["sold_cents"] for x in stats.checkouts(conn, lo, hi)) == 9900
