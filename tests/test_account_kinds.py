from fizzy import db, pin
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_errors import _customer
from tests.test_pin import NEED, _points, _unlock, owner  # noqa: F401  (fixture)


def _kind(cid, kind):
    conn = db.connect()
    try:
        conn.execute("UPDATE customers SET kind = ? WHERE id = ?", (kind, cid))
        conn.commit()
    finally:
        conn.close()


def test_admin_accounts_need_the_owner(client, owner):
    login(client)
    me = _customer()
    _unlock(client, "owner-pw", 2)
    client.post("/settings/pin", data={"new": "4821"})
    assert "now an admin account" in client.post(f"/customers/{me}/kind", data={"kind": "admin"}).text
    client.post("/switch", data={"who": "Mike"})
    _unlock(client, "4821")
    ref = {"referer": f"http://testserver/customers/{me}"}
    r = client.post(f"/customers/{me}/points", data={"delta": "5", "note": "for me"}, headers=ref)
    assert NEED in r.text and _points(me) == 0
    r = client.post("/tools/bulk_points", data={"delta": "5", "note": "x", "people": "Test Kid"}, headers=ref)
    assert NEED in r.text and _points(me) == 0
    # Un-marking yourself needs the owner too.
    assert NEED in client.post(f"/customers/{me}/kind", data={"kind": ""}, headers=ref).text
    _unlock(client, "owner-pw", 2)
    client.post(f"/customers/{me}/points", data={"delta": "5", "note": "ok"})
    assert _points(me) == 5


def test_test_accounts_are_a_playground(client, owner):
    login(client)
    _unlock(client, "owner-pw", 2)
    client.post("/settings/pin", data={"new": "4821"})
    assert "Made 3 test accounts" in client.post("/tools/test_accounts", data={"n": "3"}).text
    conn = db.connect()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM customers WHERE kind = 'test' ORDER BY id")]
    finally:
        conn.close()
    assert len(ids) == 3
    client.post("/switch", data={"who": "Mike"})        # locked again: nothing unlocked
    client.post(f"/customers/{ids[0]}/points", data={"delta": "-90000", "note": "lol"})
    assert _points(ids[0]) == -90000                 # no passcode, no big-change limit on test accounts
    client.post("/tools/bulk_points", data={"delta": "777", "note": "x", "people": "Test Account 2\nTest Account 3"})
    assert _points(ids[1]) == 777
    # ...but anything with a real customer in it still asks.
    real = _customer()
    ref = {"referer": "http://testserver/tools"}
    r = client.post("/tools/bulk_points", data={"delta": "5", "note": "x", "people": "Test Account 2\nTest Kid"}, headers=ref)
    assert NEED in r.text and _points(real) == 0
    # Left out of the leaderboard, and deleted in one go.
    assert "Test Account" not in client.post("/tools/leaderboard", data={"n": "10"}).text
    _unlock(client, "owner-pw", 2)
    assert "Deleted 3 test account(s)" in client.post("/tools/delete_test_accounts", data={"confirm": "DELETE"}).text
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM customers WHERE kind = 'test'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    finally:
        conn.close()




def test_test_accounts_dont_count_as_missing_from_square(client):
    login(client)
    client.post("/tools/test_accounts", data={"n": "2"})
    assert "new only (0)" in client.get("/customers").text
    page = client.get("/customers/1").text
    assert "Not in Square yet" not in page and "Test account" in page
    sq = client.get("/square").text
    assert "Not in Square" not in sq or ">0<" in sq


def test_test_level_fails_closed(client):
    from fizzy import config
    login(client)
    client.post("/tools/test_accounts", data={"n": "1"})
    client.post("/settings/pin", data={"new": "4821"})
    conn = db.connect()
    try:
        r = lambda path, form: pin.required(conn, "POST", path, form)
        assert r("/tools/bulk_points", {"delta": "5", "people": "Test Account 1"}) == pin.OPEN
        # A name that can't be found, or a tool that picks people another way: normal level.
        assert r("/tools/bulk_points", {"delta": "5", "people": "Test Account 1\nNobody Here"}) == pin.PIN
        assert r("/tools/manual_charge", {"customer": "Test Account 1", "people": "garbage;;"}) >= pin.OPEN
        assert r("/tools/raffle", {"customer": "Test Account 1"}) == pin.PIN
        assert r("/tools/restore", {"people": "Test Account 1", "all": "1"}) == pin.PIN
    finally:
        conn.close()


def test_no_merging_real_into_test_and_numbering(client):
    login(client)
    real = _customer()
    client.post("/tools/test_accounts", data={"n": "2"})
    r = client.post("/tools/merge", data={"keep": "Test Account 1", "dup": "Test Kid"})
    assert "Can&#39;t merge a test account with a real customer" in r.text
    client.post("/tools/delete_test_accounts", data={"confirm": "DELETE"})
    client.post("/tools/test_accounts", data={"n": "3"})
    conn = db.connect()
    try:
        names = [x[0] for x in conn.execute("SELECT name FROM customers WHERE kind = 'test' ORDER BY id")]
        assert len(set(names)) == 3
        assert conn.execute("SELECT merged_into FROM customers WHERE id = ?", (real,)).fetchone()[0] is None
    finally:
        conn.close()
