from fizzy import db, pin
from fizzy.__main__ import main
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_errors import _customer


def _owing(cid):
    conn = db.connect()
    try:
        return conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ?", (cid,)).fetchone()[0]
    finally:
        conn.close()


def test_no_pin_means_no_asking(client):
    login(client)
    cid = _customer()
    client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"})
    assert _owing(cid) == 5


def test_pin_guards_manual_changes(client):
    login(client)
    cid = _customer()
    assert "PIN set" in client.post("/settings/pin", data={"new": "4821"}).text
    client.post("/switch", data={"who": "Mike"})  # someone new on the counter: locked again
    ref = {"referer": f"http://testserver/customers/{cid}"}
    r = client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"}, headers=ref)
    assert "Enter the PIN first" in r.text and _owing(cid) == 0
    r = client.post("/tools/bulk_points", data={"delta": "5", "note": "x", "everyone": "1"}, headers=ref)
    assert "Enter the PIN first" in r.text and _owing(cid) == 0
    assert "Enter the PIN first" in client.get("/tools/backup.db").text
    assert client.post("/settings/pin/unlock", data={"pin": "1111"}).json()["ok"] is False
    assert client.post("/settings/pin/unlock", data={"pin": "4821"}).json()["ok"] is True
    client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"})
    assert _owing(cid) == 5
    # Day-to-day stuff is never asked for.
    assert client.get("/").status_code == 200


def test_bad_pins_and_lockout(client, monkeypatch):
    login(client)
    assert "4 to 8 digits" in client.post("/settings/pin", data={"new": "12"}).text
    client.post("/settings/pin", data={"new": "123456"})
    monkeypatch.setattr(pin, "_fails", [])
    for _ in range(pin.FAIL_LIMIT):
        client.post("/settings/pin/unlock", data={"pin": "000000"})
    assert "Too many" in client.post("/settings/pin/unlock", data={"pin": "123456"}).json()["error"]


def test_clear_pin_from_the_command_line(client):
    login(client)
    client.post("/settings/pin", data={"new": "4821"})
    assert main(["clear-pin"]) == 0
    conn = db.connect()
    try:
        assert pin.stored(conn) == ""
    finally:
        conn.close()


def test_everything_by_hand_is_covered_but_square_stuff_is_not():
    for path in ("/customers/1/points", "/customers/1/redeem/2", "/rewards", "/events", "/money/restock",
                 "/money/payout", "/customers/1/passcode/reveal", "/profit", "/tools/bulk_points", "/settings/pin",
                 "/redemptions/3/undo", "/square/payments/abc/kind", "/some/new/page"):
        assert pin.protected("POST", path), path
    for path in ("/login", "/logout", "/switch", "/me/login", "/customers/1/claim", "/square/sync", "/square/webhook",
                 "/square/payments/abc/assign", "/tools/sync_now", "/profit/refresh", "/settings/pin/unlock"):
        assert not pin.protected("POST", path), path
    assert not pin.protected("GET", "/customers/1")


def test_owner_password_guards_the_dangerous_stuff(client, monkeypatch):
    from fizzy import config
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-pw")
    monkeypatch.setattr(pin, "_fails", [])
    login(client)
    cid = _customer()
    ref = {"referer": f"http://testserver/customers/{cid}"}
    client.post(f"/customers/{cid}/points", data={"delta": "50", "note": "x"})          # small: fine
    assert _owing(cid) == 50
    r = client.post(f"/customers/{cid}/points", data={"delta": "-9000000", "note": "lol"}, headers=ref)
    assert "owner password" in r.text and _owing(cid) == 50
    r = client.post("/tools/reset_points", data={"confirm": "RESET", "note": "x"}, headers=ref)
    assert "owner password" in r.text and _owing(cid) == 50
    assert "owner password" in client.post("/settings/pin", data={"new": "1234"}, headers=ref).text
    assert client.post("/settings/pin/unlock", data={"pin": "nope", "tier": "admin"}).json()["ok"] is False
    assert client.post("/settings/pin/unlock", data={"pin": "owner-pw", "tier": "admin"}).json()["ok"] is True
    client.post(f"/customers/{cid}/points", data={"delta": "-50", "note": "fix"})
    assert _owing(cid) == 0
    client.post("/switch", data={"who": "Mike"})
    r = client.post(f"/customers/{cid}/points", data={"delta": "600", "note": "x"}, headers=ref)
    assert "owner password" in r.text


def test_rules_for_admin():
    from fizzy import config
    old, config.ADMIN_PASSWORD = config.ADMIN_PASSWORD, "x"
    try:
        assert pin.admin_needed("POST", "/customers/1/points", {"delta": "501"})
        assert not pin.admin_needed("POST", "/customers/1/points", {"delta": "-500"})
        assert pin.admin_needed("POST", "/customers/1/repay", {"amount": "$60"})
        assert pin.admin_needed("POST", "/tools/bulk_points", {"delta": "5", "everyone": "1"})
        assert pin.admin_needed("POST", "/tools/revoke_all", {})
        assert pin.admin_needed("GET", "/tools/backup.db")
        assert not pin.admin_needed("POST", "/customers/1/claim", {})
        assert not pin.admin_needed("POST", "/rewards", {"points": "1000"})   # a reward's price isn't a points change
    finally:
        config.ADMIN_PASSWORD = old


def test_owner_password_works_wherever_the_pin_does(client, monkeypatch):
    from fizzy import config
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-pw")
    monkeypatch.setattr(pin, "_fails", [])
    login(client)
    cid = _customer()
    client.post("/settings/pin/unlock", data={"pin": "owner-pw", "tier": "admin"})
    client.post("/settings/pin", data={"new": "4821"})
    client.post("/switch", data={"who": "Mike"})
    r = client.post("/settings/pin/unlock", data={"pin": "owner-pw", "tier": "pin"}).json()
    assert r["ok"] and r["tier"] == "admin"
    client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"})
    client.post(f"/customers/{cid}/points", data={"delta": "900", "note": "x"})
    assert _owing(cid) == 905
