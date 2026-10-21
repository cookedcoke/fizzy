import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fizzy import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    from fizzy import app as appmod
    importlib.reload(appmod)
    with TestClient(appmod.create_app(background=False)) as c:
        yield c


def login(client):
    return client.post("/login", data={"password": "test-pw", "who": "Mike"}, follow_redirects=False)


def test_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.post("/login", data={"password": "nope", "who": "Mike"}).text.count("Wrong password") == 1


def test_full_flow(client):
    assert login(client).status_code == 303
    csv = b"Original Name,Cleaned Name,Email\nADELINE YATES,Adeline Yates,rns00000001@reddamnorthshore.nsw.edu.au\n"
    r = client.post("/import", files={"file": ("d.csv", csv, "text/csv")})
    assert "Import 1 row<" in r.text
    client.post("/import", files={"file": ("d.csv", csv, "text/csv")}, data={"confirm": "1"})
    hits = client.get("/api/search?q=adel").json()
    assert hits[0]["name"] == "Adeline Yates"
    cid = hits[0]["id"]
    client.post(f"/customers/{cid}/tab", data={"decision": "approve", "note": "ok"})
    client.post(f"/customers/{cid}/points", data={"delta": "100", "note": "event"})
    page = client.get(f"/customers/{cid}").text
    assert "Can use tab" in page and ">100<" in page
    for path in ["/", "/square", "/settings", "/audit", "/import", "/customers?q=zzz", "/export/square-customers.csv"]:
        assert client.get(path).status_code == 200, path
    assert "fizzy-1" in client.get("/export/square-customers.csv").text
    assert client.post("/square/webhook", content=b"{}").status_code == 403


def test_search_index_matches_customer_page(client):
    login(client)
    csv = b"Cleaned Name,Email\nAdeline Yates,rns1@reddamnorthshore.nsw.edu.au\nAdam Fenn,rns2@reddamnorthshore.nsw.edu.au\n"
    client.post("/import", files={"file": ("d.csv", csv, "text/csv")}, data={"confirm": "1"})
    client.post("/customers/1/tab", data={"decision": "approve"})
    client.post("/tools/manual_charge", data={"customer": "Adeline Yates", "amount": "3"})
    rows = {r["name"]: r for r in client.get("/api/index").json()}
    assert rows["Adeline Yates"]["status"] == "approved" and rows["Adeline Yates"]["owing"] == 300
    assert rows["Adam Fenn"]["status"] == "locked" and rows["Adam Fenn"]["sid"] == "rns2"
    assert "app.js?v=" in client.get("/").text


def test_repayment_already_rung_up_in_square_is_not_entered_twice(client):
    from fizzy import config, db, square
    login(client)
    client.post("/tools/add_customer", data={"name": "Test Kid", "email": "rns9@reddamnorthshore.nsw.edu.au"})
    conn = db.connect(config.DB_PATH)
    conn.execute("UPDATE customers SET square_customer_id = 'SQ9' WHERE id = 1")
    now = db.now_iso()
    square.process_order(conn, {"id": "o1", "state": "COMPLETED", "customer_id": "SQ9", "closed_at": now, "created_at": now,
                                "line_items": [], "discounts": [{"name": "Fizzy Tab", "applied_money": {"amount": 300}}]})
    square.process_payment(conn, {"id": "p1", "status": "COMPLETED", "amount_money": {"amount": 300}, "source_type": "CASH",
                                  "customer_id": "SQ9", "created_at": now, "order_id": "o2"},
                           {"line_items": [{"name": "Tab repayment", "total_money": {"amount": 300}}]})
    conn.commit()
    r = client.post("/customers/1/repay", data={"amount": "3.00", "method": "cash"})
    assert "already recorded from Square" in r.text
    assert conn.execute("SELECT COUNT(*) FROM tab_repayments").fetchone()[0] == 1
    client.post("/customers/1/repay", data={"amount": "3.00", "method": "cash", "confirm_dup": "1"})
    assert conn.execute("SELECT COUNT(*) FROM tab_repayments").fetchone()[0] == 2
    page = client.post("/tools/takings", data={"start": "2020-01-01", "end": "2030-01-01"}).text
    assert "Put on tabs" in page and "recorded without Square" in page


def test_assign_from_dashboard_clears_needs_attention(client):
    from fizzy import config, db, square
    login(client)
    client.post("/tools/add_customer", data={"name": "Test Kid", "email": "rns9@reddamnorthshore.nsw.edu.au"})
    conn = db.connect(config.DB_PATH)
    now = db.now_iso()
    square.process_order(conn, {"id": "o1", "state": "COMPLETED", "closed_at": now, "created_at": now,
                                "line_items": [{"name": "Coke", "quantity": "1"}],
                                "discounts": [{"name": "Fizzy Tab", "applied_money": {"amount": 300}}]})
    conn.commit()
    assert "with no customer attached" in client.get("/").text
    r = client.post("/square/payments/order:o1/assign", data={"customer": "Test Kid"}, headers={"referer": "/"})
    assert "Assigned to Test Kid" in r.text and "with no customer attached" not in r.text
    r = client.post("/square/payments/order:o1/assign", data={"customer": "Nobody Here"}, headers={"referer": "/"})
    assert "No customer matches" in r.text


def test_dashboard_eligible_and_top_lists_skip_archived(client):
    from fizzy import config, db, logic
    login(client)
    client.post("/tools/add_customer", data={"name": "Gone Kid", "email": "rns7@reddamnorthshore.nsw.edu.au"})
    conn = db.connect(config.DB_PATH)
    logic.adjust_points(conn, 1, 1, "seed", "t")
    conn.execute("UPDATE points_ledger SET spend_cents = 5000")
    conn.commit()
    assert "Gone Kid" in client.get("/").text
    conn.execute("UPDATE customers SET archived_at = '2026-01-01T00:00:00+00:00'")
    conn.commit()
    assert "Gone Kid" not in client.get("/").text


def test_login_lockout_spares_known_browsers(client, monkeypatch):
    from fizzy import app as appmod
    monkeypatch.setattr(appmod, "FAIL_DELAY", 0)
    appmod._fails.clear()
    assert login(client).status_code == 303                 # counter browser signs in: gets the known-browser cookie
    known = client.cookies.get(appmod.KNOWN_COOKIE)
    client.post("/logout")
    from fastapi.testclient import TestClient
    stranger = TestClient(client.app)
    for _ in range(10):
        stranger.post("/login", data={"password": "nope", "who": "Mike"})
    assert "Too many attempts" in stranger.post("/login", data={"password": "test-pw", "who": "Mike"}).text
    client.cookies.set(appmod.KNOWN_COOKIE, known)
    assert login(client).status_code == 303                 # same network, but the counter still gets in
    appmod._fails.clear()


def test_tab_note_enter_cannot_submit_a_decision(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Note Kid", "email": "rns8@reddamnorthshore.nsw.edu.au"})
    page = client.get("/customers/1").text
    form = page[page.index('action="/customers/1/tab"'):]
    first_button = form[form.index("<button"):form.index(">", form.index("<button")) + 1]
    assert "disabled" in first_button                        # Enter in the note box does nothing
    assert "onclick=\"return confirm" not in client.get("/settings").text
