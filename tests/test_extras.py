"""Profit, double points days, streaks/badges and backup health."""
from datetime import date, datetime, timedelta, timezone

import pytest

from fizzy import app as appmod, badges, config, db, events, logic, square, stats
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import iso, order, pay, tab_order


# ---- profit -------------------------------------------------------------------------------------

def test_profit_uses_costs_and_ignores_the_tab_discount(conn, cust):
    cust()
    square.process_payment(conn, pay("p1", 650, order_id="o1"), order(("Coke", 350), ("Chips", 300), oid="o1"))
    tab = tab_order("o2", 350, ("Coke", 350))
    tab["line_items"][0].update(gross_sales_money={"amount": 350}, total_money={"amount": 0},
                                applied_discounts=[{"discount_uid": "d1", "applied_money": {"amount": 350}}])
    tab["discounts"][0]["uid"] = "d1"
    square.process_order(conn, tab)
    stats.set_cost(conn, "coke", 120, "Will")                              # names match case-insensitively
    p = stats.profit(stats.summary(conn, stats.today())["checkouts"], stats.costs(conn))
    coke = next(r for r in p["items"] if r["name"] == "Coke")
    assert (coke["qty"], coke["revenue"], coke["cost"], coke["profit"]) == (2, 700, 240, 460)  # tab sale at full price
    assert p["uncosted"] == 1 and p["uncosted_revenue"] == 300 and p["margin"] == round(100 * 460 / 700)


def test_reward_discount_lowers_revenue(conn, cust):
    cust()
    o = order(("Coke", 0), oid="o1")
    o["discounts"] = [{"uid": "r", "name": "Points: Free Coke", "applied_money": {"amount": 350}}]
    o["line_items"][0].update(gross_sales_money={"amount": 350},
                              applied_discounts=[{"discount_uid": "r", "applied_money": {"amount": 350}}])
    square.process_payment(conn, pay("p1", 0, order_id="o1"), o)
    stats.set_cost(conn, "Coke", 120, "Will")
    p = stats.profit(stats.summary(conn, stats.today())["checkouts"], stats.costs(conn))
    assert p["profit"] == -120                                             # a free Coke costs us the Coke


def test_profit_page_saves_costs(client):
    login(client)
    conn = db.connect()
    try:
        square.process_payment(conn, pay("p1", 350, cust=None, order_id="o1"), order(("Coke", 350), oid="o1"))
        conn.commit()
    finally:
        conn.close()
    assert "No cost set" in client.get("/profit").text
    r = client.post("/profit", data={"name": ["Coke"], "cost": ["$1.20"]})
    assert "Saved 1 cost" in r.text and "1.20" in r.text and "66%" in r.text
    assert "isn&#39;t a dollar amount" in client.post("/profit", data={"name": ["Coke"], "cost": ["abc"]}).text
    client.post("/profit", data={"name": ["Coke"], "cost": [""]})            # blank removes it
    conn = db.connect()
    try:
        assert stats.costs(conn) == {}
    finally:
        conn.close()


# ---- double points days -------------------------------------------------------------------------

def test_event_bonus_tops_up_follows_refunds_and_late_tab_repayments(conn, cust):
    c = cust()
    t = stats.today().isoformat()
    events.create(conn, "Double Friday", "2", t, t, "Will")
    square.process_payment(conn, pay("p1", 500, order_id="o1"), order(("Coke", 500), oid="o1"))
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 1000
    events.reconcile(conn)                                                  # idempotent
    assert logic.points_balance(conn, c["id"]) == 1000
    square.process_refund(conn, {"id": "r1", "payment_id": "p1", "status": "COMPLETED",
                                 "amount_money": {"amount": 200}, "created_at": iso()})
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 600                       # 300 left, doubled
    logic.set_tab_decision(conn, c["id"], "approved", "", "Will")
    square.process_order(conn, tab_order("o2", 400))                        # bought on the tab during the event
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 600                       # nothing until it's paid
    logic.add_repayment(conn, c["id"], 400, "cash", "", "Will")
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 600 + 800


def test_event_rules(conn):
    t = stats.today()
    with pytest.raises(events.EventError, match="more than 1"):
        events.create(conn, "x", "1", t.isoformat(), "", "Will")
    with pytest.raises(events.EventError, match="today or a day in the future"):
        events.create(conn, "x", "2", (t - timedelta(days=1)).isoformat(), "", "Will")
    with pytest.raises(events.EventError, match="before the first"):
        events.create(conn, "x", "2", (t + timedelta(days=3)).isoformat(), (t + timedelta(days=2)).isoformat(), "Will")
    soon = events.create(conn, "Soon", "2", (t + timedelta(days=2)).isoformat(), "", "Will")
    now = events.create(conn, "Now", "3", t.isoformat(), (t + timedelta(days=4)).isoformat(), "Will")
    assert [e["name"] for e in events.upcoming(conn)] == ["Soon"] and [e["name"] for e in events.on(conn)] == ["Now"]
    assert "Deleted" in events.cancel(conn, soon, "Will")
    assert "has ended" in events.cancel(conn, now, "Will")
    assert conn.execute("SELECT end_date FROM points_events WHERE id = ?", (now,)).fetchone()[0] == t.isoformat()
    assert events.on(conn) == []


def test_events_show_on_rewards_dashboard_and_student_site(client):
    login(client)
    t = stats.today().isoformat()
    r = client.post("/events", data={"name": "Double points", "multiplier": "2", "start": t, "end": ""})
    assert "Extra points get added" in r.text and "On now" in r.text
    assert "is on today" in client.get("/").text
    client.post("/logout")
    assert "is on today" in client.get("/me").text


# ---- streaks and badges -------------------------------------------------------------------------

def test_streaks_count_trading_days_and_today_is_not_over_yet():
    d = [date(2026, 10, n) for n in (12, 13, 14, 15, 16, 19, 20)]          # Mon-Fri, Mon-Tue (weekend skipped)
    trading = set(d)
    assert badges.streaks(set(d), trading, d[-1]) == (7, 7, True)
    assert badges.streaks(set(d[:-1]), trading, d[-1]) == (6, 6, False)      # hasn't bought today yet: still alive
    assert badges.streaks(set(d[:-2]), trading, d[-1]) == (0, 5, False)      # missed Monday: broken
    assert badges.streaks(set(), trading, d[-1]) == (0, 0, False)


def test_badges_on_the_student_site(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    client.post("/customers/1/passcode", data={"passcode": "fizz-4417"})
    conn = db.connect()
    try:
        conn.execute("UPDATE customers SET square_customer_id = 'SQ1' WHERE id = 1")
        square.process_payment(conn, pay("p1", 650, order_id="o1"), order(("Coke", 350), ("Chips", 300), oid="o1"))
        conn.commit()
        b = badges.for_customer(conn, 1)
    finally:
        conn.close()
    got = {x["name"] for x in b["badges"] if x["earned"]}
    assert got == {"First sip"} and b["streak"] == 1 and b["today"]
    explorer = next(x for x in b["badges"] if x["name"] == "Explorer")
    assert explorer["count"] == "2 / 5" and explorer["progress"] == 40
    client.post("/logout")
    page = client.post("/me/login", data={"who": "Nora Le Fevre", "passcode": "fizz-4417"}).text
    assert "1-day streak" in page and "First sip" in page and "Badges" in page


# ---- backup health ------------------------------------------------------------------------------

def test_backup_health(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", "")
    assert appmod.backup_health(conn) == (False, "Backups aren't set up. (Owner: see the README.)")
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path))
    assert "No backup yet" in appmod.backup_health(conn)[1]
    path = db.backup(conn, str(tmp_path))
    db.set_setting(conn, "last_backup", db.now_iso())
    db.set_setting(conn, "last_backup_path", path)
    assert appmod.backup_health(conn)[0] is True
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    db.set_setting(conn, "last_backup", old)
    assert appmod.backup_health(conn) == (False, "No backup for 3 day(s).")
    db.set_setting(conn, "last_backup", db.now_iso())
    db.set_setting(conn, "last_backup_path", str(tmp_path / "gone.db"))
    assert "missing" in appmod.backup_health(conn)[1]
    db.set_setting(conn, "backup_error", "2026-10-22T00:00:00+00:00: [Errno 28] No space left on device")
    assert appmod.backup_health(conn) == (False, "Last backup failed: [Errno 28] No space left on device. Check the backup drive.")


def test_event_bonus_follows_refunds_by_purchase_date(conn, cust):
    c = cust()
    t = stats.today()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO points_events(name, multiplier, start_date, end_date, created_at) VALUES (?, 2, ?, ?, ?)",
                 ("Double", (t - timedelta(days=1)).isoformat(), (t - timedelta(days=1)).isoformat(), db.now_iso()))
    square.process_payment(conn, pay("p1", 500, ts=yesterday), None)                  # bought on the double day
    square.process_payment(conn, pay("p0", 400, ts=iso(days_ago=8)), None)            # last week
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 500 * 2 + 400
    refund = {"status": "COMPLETED", "created_at": iso()}
    square.process_refund(conn, {**refund, "id": "r0", "payment_id": "p0", "amount_money": {"amount": 400}})
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 1000          # refunding last week's sale doesn't touch the bonus
    square.process_refund(conn, {**refund, "id": "r1", "payment_id": "p1", "amount_money": {"amount": 500}})
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 0             # refunding the double-day sale takes its bonus back


def test_ending_todays_event_keeps_what_was_earned(conn, cust):
    c = cust()
    t = stats.today().isoformat()
    eid = events.create(conn, "Double", "2", t, t, "Will")
    square.process_payment(conn, pay("p1", 500, ts=iso(hours=0.05)), None)   # 3 minutes ago, during the event
    events.reconcile(conn)
    assert "has ended" in events.cancel(conn, eid, "Will")                  # a one-day event, running now
    conn.execute("UPDATE points_events SET ended_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(timespec="seconds"), eid))
    square.process_payment(conn, pay("p2", 300, ts=iso()), None)            # bought after it ended
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 1000 + 300
    b = badges.for_customer(conn, c["id"])
    assert next(x for x in b["badges"] if x["name"] == "Double trouble")["earned"]
    square.process_refund(conn, {"id": "r1", "payment_id": "p1", "status": "COMPLETED", "amount_money": {"amount": 500}, "created_at": iso()})
    events.reconcile(conn)
    b = badges.for_customer(conn, c["id"])
    assert not next(x for x in b["badges"] if x["name"] == "Double trouble")["earned"]   # bonus refunded: badge goes


def test_weekly_double_points(conn, cust):
    c = cust()
    t = stats.today()
    rid = events.create_rule(conn, "Double points Friday", "2", t.isoformat(), "", "Will")
    days = [r[0] for r in conn.execute("SELECT start_date FROM points_events WHERE rule_id = ? ORDER BY start_date", (rid,))]
    assert days == [(t + timedelta(days=7 * i)).isoformat() for i in range(3)]          # today + the next two weeks
    events.schedule(conn)
    assert conn.execute("SELECT COUNT(*) FROM points_events WHERE rule_id = ?", (rid,)).fetchone()[0] == 3  # no dupes
    square.process_payment(conn, pay("p1", 500), None)
    events.reconcile(conn)
    assert logic.points_balance(conn, c["id"]) == 1000                                   # today's one works
    assert "won't repeat" in events.stop_rule(conn, rid, "Will")
    left = [r[0] for r in conn.execute("SELECT start_date FROM points_events WHERE rule_id = ?", (rid,))]
    assert left == [t.isoformat()]                                                       # today's carries on, future ones go
    events.schedule(conn)
    assert conn.execute("SELECT COUNT(*) FROM points_events WHERE rule_id = ?", (rid,)).fetchone()[0] == 1


def test_weekly_from_the_page(client):
    login(client)
    t = stats.today().isoformat()
    r = client.post("/events", data={"name": "Double points Friday", "multiplier": "2", "start": t, "weekly": "1"})
    assert "Every week" in r.text and "Every " in r.text and "Stop" in r.text
