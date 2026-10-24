from fastapi.testclient import TestClient

from fizzy import db
from tests.test_app import client, login  # noqa: F401  (fixture)


def _customer():
    conn = db.connect()
    try:
        cid = conn.execute("INSERT INTO customers(name, email, created_at) VALUES ('Test Kid', 'k@x', ?)", (db.now_iso(),)).lastrowid
        conn.commit()
        return cid
    finally:
        conn.close()


def test_blank_password_is_a_message(client):
    r = client.post("/login", data={"password": "", "who": "Mike"})
    assert r.status_code == 200 and "Type the password first" in r.text
    r = client.post("/login", data={"who": "Mike"})
    assert r.status_code == 200 and "Type the password first" in r.text


def test_silly_points_are_a_message(client):
    login(client)
    cid = _customer()
    for delta, msg in (("1000000000000000000000000", "too many points"), ("lots", "isn&#39;t a whole number"), ("", "Type how many")):
        r = client.post(f"/customers/{cid}/points", data={"delta": delta, "note": "x"})
        assert r.status_code == 200 and msg in r.text, delta
    r = client.post(f"/customers/{cid}/points", data={"delta": "1,000", "note": "x"})
    assert "Points +1000" in r.text
    r = client.post("/tools/bulk_points", data={"delta": "99999999999999999999", "note": "x", "everyone": "1"})
    assert "way too big" in r.text
    assert r.status_code == 200 and "Internal" not in r.text


def test_missing_form_field_goes_back_with_a_note(client):
    login(client)
    r = client.post("/settings", data={"tab_limit": "10"}, headers={"referer": "http://testserver/settings"})
    assert r.status_code == 200 and "missing or not a proper number" in r.text


def test_not_found_and_crash_pages(client, monkeypatch):
    login(client)
    r = client.get("/customers/999999")
    assert r.status_code == 404 and "Page not found" in r.text and "<html" in r.text
    from fizzy import stats
    monkeypatch.setattr(stats, "today", lambda: 1 / 0)
    r = TestClient(client.app, raise_server_exceptions=False, cookies=client.cookies).get("/")
    assert r.status_code == 500 and "Something broke" in r.text and "<html" in r.text


def test_silly_dollar_amounts_go_back_with_a_note(client):
    login(client)
    cid = _customer()
    ref = {"referer": f"http://testserver/customers/{cid}"}
    for amount, msg in (("1e309", "isn&#39;t a dollar amount"), ("99999999999999999999", "way too much"),
                        ("-5", "can&#39;t be negative"), ("nan", "isn&#39;t a dollar amount"), ("", "missing or not a proper number")):
        r = client.post(f"/customers/{cid}/repay", data={"amount": amount, "method": "cash"}, headers=ref)
        assert r.status_code == 200 and msg in r.text, amount
    r = client.post("/settings", data={"tab_limit": "1e309", "due_days": "7", "unlock_spend": "30"},
                    headers={"referer": "http://testserver/settings"})
    assert r.status_code == 200 and "dollar amount" in r.text
    r = client.post("/settings", data={"tab_limit": "10", "due_days": "99999999", "unlock_spend": "30"})
    assert "between 1 and 365" in r.text


def test_card_scan_opens_the_student(client):
    login(client)
    conn = db.connect()
    try:
        cid = conn.execute("INSERT INTO customers(name, email, created_at) VALUES ('Scan Kid', "
                           "'rns01234567@reddamnorthshore.nsw.edu.au', ?)", (db.now_iso(),)).lastrowid
        conn.commit()
    finally:
        conn.close()
    r = client.get("/scan?code=RNS01234567", follow_redirects=False)
    assert r.headers["location"] == f"/customers/{cid}"
    assert "No one with student number RNS07654321" in client.get("/scan?code=rns07654321").text
    assert "No one with" in client.get("/scan?code=rns%25").text
