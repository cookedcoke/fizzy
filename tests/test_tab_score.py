from datetime import datetime, timedelta, timezone

from fizzy import db, logic


def iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def buy(conn, cid, cents, days_ago):
    conn.execute("INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, created_at) VALUES (?, ?, ?, 'purchase', ?)",
                 (cid, cents, cents, iso(days_ago)))


def test_new_customer_starts_low(conn, cust):
    c = cust()
    s = logic.tab_score(conn, c["id"])
    assert s["score"] == 30 and s["limit_cents"] == 250 and not s["ready"]
    assert ("New here (under 2 weeks)", -10) in s["factors"]


def test_history_builds_the_score_and_the_limit(conn, cust):
    c = cust()
    for d in range(30, 10, -2):        # 10 different days, $4 each = $40, first one a month ago
        buy(conn, c["id"], 400, d)
    s = logic.tab_score(conn, c["id"])
    assert s["score"] == 40 + 16 + 10 and s["ready"]                       # 66
    assert s["limit_cents"] == 750 and s["next"] == {"score": 80, "limit_cents": 1000}
    st = logic.tab_status(conn, c)
    assert st["status"] == "eligible"                                       # spent $30+ and score 60+


def test_on_time_raises_it_late_and_overdue_lower_it(conn, cust):
    c = cust()
    for d in range(30, 10, -2):
        buy(conn, c["id"], 400, d)
    logic.set_tab_decision(conn, c["id"], "approved", "", "Will")
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (c["id"],)).fetchone()
    base = logic.tab_score(conn, c["id"])["score"]
    logic.add_tab_charge(conn, c, 300, "x", iso(5))                      # due in 7 days: paid on day 3, on time
    logic.add_repayment(conn, c["id"], 300, "cash", "", "Will", created_at=iso(3))
    assert logic.tab_score(conn, c["id"])["score"] == base + 5 + 1 + 1   # on time, another day, $3 more spent
    logic.add_tab_charge(conn, c, 500, "y", iso(20))  # older, so repayments go here first: $2 of it is still overdue
    s = logic.tab_score(conn, c["id"])
    assert ("Money overdue right now", -25) in s["factors"] and s["score"] < base
    assert logic.tab_status(conn, c)["status"] == "blocked_overdue"


def test_score_needed_setting(conn, cust):
    c = cust()
    for d in range(30, 20, -1):
        buy(conn, c["id"], 300, d)                                        # $30 over 10 days: 40 + 12 + 10 = 62
    assert logic.tab_status(conn, c)["status"] == "eligible"
    db.set_setting(conn, "tab_score_needed", 70)
    st = logic.tab_status(conn, c)
    assert st["status"] == "locked" and st["spent_enough"]


def test_score_cant_be_farmed(conn, cust):
    c = cust()
    for d in range(30, 10, -2):
        buy(conn, c["id"], 400, d)
    logic.set_tab_decision(conn, c["id"], "approved", "", "Will")
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (c["id"],)).fetchone()
    base = logic.tab_score(conn, c["id"])["score"]
    for i in range(5):  # five 10c tab charges the same day, each repaid straight after
        logic.add_tab_charge(conn, c, 10, "x", iso(5))
        logic.add_repayment(conn, c["id"], 10, "cash", "", "Will", created_at=iso(4))
    names = [n for n, _ in logic.tab_score(conn, c["id"])["factors"]]
    assert not any("on time" in n for n in names)          # under $1: doesn't count
    logic.add_tab_charge(conn, c, 300, "y", iso(3))
    logic.add_tab_charge(conn, c, 300, "z", iso(3))
    logic.add_repayment(conn, c["id"], 600, "cash", "", "Will", created_at=iso(2))
    assert ("Paid off 1 tab on time", 5) in logic.tab_score(conn, c["id"])["factors"]   # same week: one
