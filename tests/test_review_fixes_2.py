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
