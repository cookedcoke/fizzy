"""Paying off a tab in My CCC with Apple Pay: Fizzy charges the Square nonce as a "Tab repayment" order."""
import json

import httpx
import pytest

from fizzy import config, logic, portal, square
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_portal import J, PC, me_login, setup_student


REAL_CLIENT = square.SquareClient


class FakeSquare:
    """Square's orders and payments APIs, as far as pay_tab needs them. Remembers what it was sent, and
    answers a repeated idempotency key with the same result (as Square does)."""

    def __init__(self, decline=None):
        self.decline = decline
        self.orders, self.payments, self.calls = {}, {}, []

    def handle(self, request):
        path, body = request.url.path, json.loads(request.content or b"{}")
        self.calls.append((request.method, path, body))
        if request.method == "POST" and path == "/v2/orders":
            key = body["idempotency_key"]
            if key not in self.orders:
                o = dict(body["order"], id=f"O{len(self.orders) + 1}", state="OPEN")
                for li in o["line_items"]:
                    li["total_money"] = li["base_price_money"]
                self.orders[key] = o
            return httpx.Response(200, json={"order": self.orders[key]})
        if request.method == "POST" and path == "/v2/payments":
            if self.decline:
                return httpx.Response(400, json={"errors": [{"code": self.decline, "category": "PAYMENT_METHOD_ERROR"}]})
            key = body["idempotency_key"]
            if key not in self.payments:
                self.payments[key] = {
                    "id": f"P{len(self.payments) + 1}", "status": "COMPLETED", "order_id": body["order_id"],
                    "amount_money": body["amount_money"], "source_type": "WALLET",
                    "customer_id": body.get("customer_id"), "created_at": "2026-11-13T01:00:00.000Z",
                    "wallet_details": {"brand": "APPLE_PAY"}}
                for o in self.orders.values():
                    if o["id"] == body["order_id"]:
                        o["state"] = "COMPLETED"
            return httpx.Response(200, json={"payment": self.payments[key]})
        if request.method == "GET" and path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"order": next(o for o in self.orders.values() if o["id"] == oid)})
        return httpx.Response(404, json={"errors": []})

    def client(self):
        return REAL_CLIENT(token="t", base="https://sq.test", transport=httpx.MockTransport(self.handle))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(config, "APPLE_PAY", True)
    monkeypatch.setattr(config, "SQUARE_TOKEN", "t")
    monkeypatch.setattr(config, "SQUARE_LOCATION_ID", "L1")
    monkeypatch.setattr(config, "SQUARE_APP_ID", "sandbox-sq0idb-app")


def owes(conn, c, cents):
    logic.add_tab_charge(conn, c, cents, "1x Toastie", "2026-11-10T01:00:00+00:00")
    conn.commit()


def test_paying_off_a_tab(conn, cust, on):
    c = cust()
    owes(conn, c, 800)
    sq = FakeSquare()
    assert square.pay_tab(conn, c, "cnon:apple", 800, "attempt-123", sq.client()) == 800
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0
    order = sq.calls[0][2]["order"]
    assert order["line_items"][0]["name"].lower() == config.SQUARE_REPAYMENT_ITEM and order["customer_id"] == "SQ1"
    row = conn.execute("SELECT * FROM square_payments").fetchone()
    assert row["kind"] == "tab_repayment" and row["customer_id"] == c["id"] and row["amount_cents"] == 800
    rep = conn.execute("SELECT * FROM tab_repayments").fetchone()
    assert rep["square_payment_id"] == "P1" and rep["method"] == "card"
    # a repayment earns its points, as at the counter
    assert conn.execute("SELECT SUM(delta) FROM points_ledger WHERE reason = 'tab_repaid'").fetchone()[0] == 800


def test_the_next_sync_doesnt_count_it_twice(conn, cust, on):
    c = cust()
    owes(conn, c, 800)
    sq = FakeSquare()
    square.pay_tab(conn, c, "cnon:apple", 500, "attempt-123", sq.client())
    payment = next(iter(sq.payments.values()))
    order = next(iter(sq.orders.values()))
    assert square.process_payment(conn, payment, order) is None      # what the poller would do next
    assert conn.execute("SELECT COUNT(*) FROM tab_repayments").fetchone()[0] == 1
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 300


def test_a_retry_is_never_charged_twice(conn, cust, on):
    c = cust()
    owes(conn, c, 800)
    sq = FakeSquare()
    square.pay_tab(conn, c, "cnon:apple", 400, "attempt-123", sq.client())
    with pytest.raises(square.PayError):                             # already paid 400, so only 400 is owed...
        square.pay_tab(conn, c, "cnon:apple", 800, "attempt-123", sq.client())
    square.pay_tab(conn, c, "cnon:apple", 400, "attempt-123", sq.client())   # ...and the same attempt again
    assert len(sq.payments) == 1
    assert conn.execute("SELECT COUNT(*) FROM tab_repayments").fetchone()[0] == 1


def test_no_square_profile_still_lands_on_their_tab(conn, cust, on):
    c = cust(sq=None)
    owes(conn, c, 600)
    sq = FakeSquare()
    square.pay_tab(conn, c, "cnon:apple", 600, "attempt-456", sq.client())
    assert "customer_id" not in sq.calls[0][2]["order"]
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0


@pytest.mark.parametrize("amount,why", [(0, "between"), (49, "between"), (801, "between"), ("8", "between")])
def test_amounts_are_checked(conn, cust, on, amount, why):
    c = cust()
    owes(conn, c, 800)
    sq = FakeSquare()
    with pytest.raises(square.PayError, match=why):
        square.pay_tab(conn, c, "cnon:apple", amount, "attempt-123", sq.client())
    assert sq.calls == []                                            # nothing was sent to Square


def test_nothing_owed(conn, cust, on):
    c = cust()
    with pytest.raises(square.PayError, match="don't owe"):
        square.pay_tab(conn, c, "cnon:apple", 500, "attempt-123", FakeSquare().client())


def test_a_decline_says_so_and_records_nothing(conn, cust, on):
    c = cust()
    owes(conn, c, 800)
    with pytest.raises(square.PayError, match="declined"):
        square.pay_tab(conn, c, "cnon:apple", 800, "attempt-123", FakeSquare(decline="CARD_DECLINED").client())
    assert conn.execute("SELECT COUNT(*) FROM tab_repayments").fetchone()[0] == 0
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 800


def test_off_by_default(conn, cust, monkeypatch):
    monkeypatch.setattr(config, "APPLE_PAY", False)
    c = cust()
    owes(conn, c, 800)
    with pytest.raises(square.PayError, match="isn't switched on"):
        square.pay_tab(conn, c, "cnon:apple", 800, "attempt-123", FakeSquare().client())


def test_the_app_endpoint(client, on, monkeypatch):
    from fizzy import db
    sq = FakeSquare()
    monkeypatch.setattr(square, "SquareClient", lambda *a, **k: sq.client())
    cid = setup_student(client, points=0)
    conn = db.connect()
    owes(conn, conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone(), 750)
    client.post("/logout")
    assert client.post("/me/tab/pay", data={"nonce": "n", "amount_cents": "750", "key": "k-12345678"}, headers=J).status_code == 401
    me_login(client, "rns5")
    tab = client.get("/me", headers=J).json()["tab"]
    assert tab["apple_pay"]["app_id"] == "sandbox-sq0idb-app" and tab["owing_cents"] == 750
    r = client.post("/me/tab/pay", data={"nonce": "n", "amount_cents": "750", "key": "k-12345678"}, headers=J)
    assert r.status_code == 200 and r.json()["owing_cents"] == 0 and "all paid off" in r.json()["message"]
    assert client.get("/me", headers=J).json()["tab"]["apple_pay"] is None        # nothing left to pay
    bad = client.post("/me/tab/pay", data={"nonce": "n", "amount_cents": "100", "key": "k-87654321"}, headers=J)
    assert bad.status_code == 400 and "don't owe" in bad.json()["error"]


def test_the_website_points_at_the_app_to_pay(client, on, monkeypatch):
    from fizzy import db
    cid = setup_student(client, points=0)
    conn = db.connect()
    owes(conn, conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone(), 750)
    client.post("/logout")
    me_login(client, "rns5")
    assert "Apple Pay in the" not in client.get("/me").text                  # off until the app version is out
    monkeypatch.setattr(config, "APP_PAY_PROMO", True)
    page = client.get("/me").text
    assert "Pay it off with Apple Pay in the" in page and config.STUDENT_APP_STORE_URL in page
