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
