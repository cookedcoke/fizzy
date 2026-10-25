import json

import pytest

from fizzy import config, db, pin
from fizzy.__main__ import main
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_errors import _customer

NEED = "Enter the passcode first"


def _points(cid):
    conn = db.connect()
    try:
        return conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ?", (cid,)).fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-pw")
    monkeypatch.setattr(pin, "_fails", [])


def _unlock(client, code, need=1):
    return client.post("/settings/pin/unlock", data={"pin": code, "need": need}).json()


def test_nothing_set_means_no_asking(client):
    login(client)
    cid = _customer()
    client.post(f"/customers/{cid}/points", data={"delta": "5000", "note": "x"})
    assert _points(cid) == 5000


def test_pin_guards_manual_changes(client):
    login(client)
    cid = _customer()
    client.post("/settings/pin", data={"new": "4821"})
    client.post("/switch", data={"who": "Mike"})  # someone new on the counter: locked again
    ref = {"referer": f"http://testserver/customers/{cid}"}
    r = client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"}, headers=ref)
    assert NEED in r.text and _points(cid) == 0
    assert NEED in client.get("/tools/backup.db").text
    assert _unlock(client, "1111")["ok"] is False
    assert _unlock(client, "4821")["ok"] is True
    client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"})
    assert _points(cid) == 5
    assert client.get("/").status_code == 200


def test_owner_levels(client, owner):
    login(client)
    cid = _customer()
    _unlock(client, "owner-pw", 2)
    client.post("/settings/pin", data={"new": "4821"})
    client.post("/switch", data={"who": "Mike"})
    ref = {"referer": f"http://testserver/customers/{cid}"}
    _unlock(client, "4821")
    client.post(f"/customers/{cid}/points", data={"delta": "50", "note": "x"})                  # PIN is enough
    assert _points(cid) == 50
    r = client.post(f"/customers/{cid}/points", data={"delta": "-9000000", "note": "lol"}, headers=ref)
    assert NEED in r.text and _points(cid) == 50
    assert NEED in client.post("/tools/reset_points", data={"confirm": "RESET", "note": "x"}, headers=ref).text
    # The PIN where the owner's is needed is just a wrong passcode.
    r = _unlock(client, "4821", 2)
    assert r == {"ok": False, "error": "Wrong passcode."}
    assert _unlock(client, "owner-pw", 2)["ok"] is True
    client.post(f"/customers/{cid}/points", data={"delta": "-50", "note": "fix"})
    assert _points(cid) == 0


def test_owner_password_works_wherever_the_pin_does(client, owner):
    login(client)
    cid = _customer()
    _unlock(client, "owner-pw", 2)
    client.post("/settings/pin", data={"new": "4821"})
    client.post("/switch", data={"who": "Mike"})
    r = _unlock(client, "owner-pw", 1)
    assert r["ok"] and r["rank"] == 2
    client.post(f"/customers/{cid}/points", data={"delta": "900", "note": "x"})
    assert _points(cid) == 900


def test_owner_tunes_who_can_do_what(client, owner):
    login(client)
    cid = _customer()
    ref = {"referer": f"http://testserver/customers/{cid}"}
    _unlock(client, "owner-pw", 2)
    client.post("/settings/pin", data={"new": "4821"})
    form = {a[0]: a[3] for a in pin.ACTIONS} | {"points": "open", "big_points": "100", "big_dollars": "20", "big": "pin"}
    assert "Saved" in client.post("/settings/permissions", data=form).text
    client.post("/switch", data={"who": "Mike"})
    client.post(f"/customers/{cid}/points", data={"delta": "40", "note": "x"})       # now open to anyone
    assert _points(cid) == 40
    assert NEED in client.post(f"/customers/{cid}/points", data={"delta": "150", "note": "x"}, headers=ref).text
    _unlock(client, "4821")
    client.post(f"/customers/{cid}/points", data={"delta": "150", "note": "x"})       # big now only needs the PIN
    assert _points(cid) == 190
    # The table itself stays owner only.
    assert NEED in client.post("/settings/permissions", data=form, headers=ref).text
    conn = db.connect()
    try:
        assert json.loads(db.get_setting(conn, "permissions"))["points"] == "open"
    finally:
        conn.close()


def test_rules():
    conn = db.connect()
    old = config.ADMIN_PASSWORD
    try:
        db.init(conn)
        pin.set_pin(conn, "4821", "t")
        config.ADMIN_PASSWORD = "x"
        r = lambda m, p, f=None: pin.required(conn, m, p, f)
        assert r("POST", "/customers/1/points", {"delta": "501"}) == pin.OWNER
        assert r("POST", "/customers/1/points", {"delta": "-500"}) == pin.PIN
        assert r("POST", "/customers/1/repay", {"amount": "$60"}) == pin.OWNER
        assert r("POST", "/tools/bulk_points", {"delta": "5", "everyone": "1"}) == pin.OWNER
        assert r("POST", "/tools/revoke_all", {}) == pin.OWNER
        assert r("GET", "/tools/backup.db") == pin.OWNER
        assert r("POST", "/customers/1/claim", {}) == pin.OPEN
        assert r("POST", "/login", {}) == pin.OPEN
        assert r("POST", "/rewards", {"points": "1000"}) == pin.PIN   # a reward's price isn't a points change
        assert r("POST", "/some/new/page", {}) == pin.PIN
        assert r("GET", "/customers/1") == pin.OPEN
        config.ADMIN_PASSWORD = ""
        assert r("POST", "/tools/revoke_all", {}) == pin.PIN          # no owner password: the PIN tops out
    finally:
        config.ADMIN_PASSWORD = old
        conn.close()


def test_bad_pins_and_lockout(client, monkeypatch):
    login(client)
    assert "4 to 8 digits" in client.post("/settings/pin", data={"new": "12"}).text
    client.post("/settings/pin", data={"new": "123456"})
    monkeypatch.setattr(pin, "_fails", [])
    for _ in range(pin.FAIL_LIMIT):
        _unlock(client, "000000")
    assert "Too many" in _unlock(client, "123456")["error"]


def test_clear_pin_from_the_command_line(client):
    login(client)
    client.post("/settings/pin", data={"new": "4821"})
    assert main(["clear-pin"]) == 0
    conn = db.connect()
    try:
        assert pin.stored(conn) == ""
    finally:
        conn.close()


def test_owner_but_no_pin_leaves_pin_things_open(client, owner):
    login(client)
    cid = _customer()
    client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"})
    assert _points(cid) == 5
    ref = {"referer": f"http://testserver/customers/{cid}"}
    assert NEED in client.post(f"/customers/{cid}/points", data={"delta": "5000", "note": "x"}, headers=ref).text
