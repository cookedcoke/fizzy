"""The React student site (student-web/): served for the student pages when built, otherwise the classic pages."""
from fizzy import config, db, portal, rewards
from tests.test_app import client  # noqa: F401  (fixture)

INDEX = '<!doctype html><html><head><link rel="manifest" href="/manifest.webmanifest"><!--fizzy-config--></head><body><div id="root"></div></body></html>'


def _react(monkeypatch, tmp_path):
    f = tmp_path / "index.html"
    f.write_text(INDEX)
    monkeypatch.setattr(portal, "SPA_INDEX", str(f))
    monkeypatch.setattr(config, "STUDENT_UI", "react")


def _student(conn, name="Augie Lim", code="fizz1234"):
    cid = conn.execute("INSERT INTO customers(name, email, created_at) VALUES (?, ?, ?)",
                       (name, "rns12345678@reddamnorthshore.nsw.edu.au", db.now_iso())).lastrowid
    portal.set_passcode(conn, conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone(), code, "Will")
    conn.commit()
    return cid


def test_react_page_is_served_with_its_settings(client, monkeypatch, tmp_path):
    _react(monkeypatch, tmp_path)
    for path in ["/me", "/me/hunt", "/me/c/SOMECODE"]:
        r = client.get(path)
        assert r.status_code == 200 and '<div id="root">' in r.text, path
        assert 'window.__FIZZY__={"base": "/me"' in r.text and 'href="/me/manifest.webmanifest"' in r.text
        assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
    assert "challenge_scans" not in client.get("/me/c/SOMECODE").text   # the page scans it itself, through JSON


def test_classic_pages_when_switched_off_or_not_built(client, monkeypatch, tmp_path):
    _react(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "STUDENT_UI", "classic")
    assert '<div id="root">' not in client.get("/me").text
    monkeypatch.setattr(config, "STUDENT_UI", "react")
    monkeypatch.setattr(portal, "SPA_INDEX", str(tmp_path / "missing.html"))
    assert '<div id="root">' not in client.get("/me").text and "Let me in" in client.get("/me").text


def test_the_json_the_react_page_reads(client, monkeypatch, tmp_path):
    _react(monkeypatch, tmp_path)
    J = {"X-Fizzy-Format": "json"}
    conn = db.connect(config.DB_PATH)
    _student(conn)
    rewards.create(conn, "Free Coke", 1000, "", None, "Will")
    conn.commit()
    out = client.get("/me", headers=J).json()
    assert out["signed_in"] is False and out["rewards"][0]["name"] == "Free Coke"
    assert client.post("/me/login", data={"who": "Augie Lim", "passcode": "nope"}, headers=J).status_code == 401
    assert client.post("/me/login", data={"who": "Augie Lim", "passcode": "fizz1234"}, headers=J).json() == {"ok": True}
    me = client.get("/me", headers=J).json()
    assert me["signed_in"] and me["first"] == "Augie" and me["sid"] == "rns12345678" and "note" not in me
    scan = client.get("/me/c/NOTACODE", headers=J).json()
    assert scan["signed_in"] is True and scan["ok"] is False and scan["result"]["kind"] == "bad"
    client.post("/me/logout", headers=J)
    guest = client.get("/me/c/NOTACODE", headers=J).json()
    assert guest["signed_in"] is False and guest["cards"] == [] and guest["result"]["title"] == "Not a hunt code"
