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
                 "VALUES ('P2', NULL, 'sale', 300, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    assert r("/square/payments/P1/assign", {"customer": "Test Kid"}) == pin.OWNER   # someone's tab money
    assert r("/square/payments/P1/kind", {"kind": "sale"}) == pin.OWNER
    assert r("/square/payments/P2/assign", {"customer": "Test Kid"}) == pin.OPEN     # an unclaimed sale: like claiming


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
