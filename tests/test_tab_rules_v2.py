"""Tabs come from the tab score: everyone at or over the minimum has one, staff pick a limit band
(a staff adjustment line in the score) or turn a tab off. And phones get pointed at the iOS app."""
import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fizzy import db, logic


def iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def reload(conn, c):
    return conn.execute("SELECT * FROM customers WHERE id = ?", (c["id"],)).fetchone()


# ---- rules -----------------------------------------------------------------------------------------

def test_a_band_sets_the_adjustment_and_the_limit_still_follows_the_score(conn, cust):
    c = cust()                                                         # score 30: $2.50
    cfg = logic.settings(conn)
    assert logic.bands(cfg) == [(20, 250), (50, 500), (65, 750), (80, 1000)]
    adj = logic.adjust_for_band(conn, c, 65, cfg)
    logic.set_score_adjust(conn, c["id"], adj, "known regular", "Will")
    c = reload(conn, c)
    st = logic.tab_status(conn, c)
    assert adj == 35 and st["score"]["score"] == 65 and st["limit_cents"] == 750 and st["available_cents"] == 750
    assert ("Staff adjustment", 35) in st["score"]["factors"]
    hist = conn.execute("SELECT decision, note, staff FROM tab_decisions WHERE customer_id = ?", (c["id"],)).fetchone()
    assert tuple(hist) == ("score +35", "known regular", "Will")
    logic.add_tab_charge(conn, c, 300, "x", iso(20))                   # overdue: the adjustment doesn't shield them
    st = logic.tab_status(conn, reload(conn, c))
    assert st["status"] == "blocked_overdue" and not st["can_charge"]
    assert ("Money overdue right now", -25) in st["score"]["factors"] and st["score"]["adjust"] == 35


def test_the_score_is_still_capped_at_100(conn, cust):
    c = cust()
    logic.set_score_adjust(conn, c["id"], 100, "", "Will")
    s = logic.tab_score(conn, c["id"])
    assert s["score"] == 100 and s["limit_cents"] == 1000 and s["base"] == 30 and s["adjust"] == 100
    with pytest.raises(ValueError):
        logic.set_score_adjust(conn, c["id"], 101, "", "Will")


def test_a_low_score_means_no_tab_and_the_lowest_band_starts_at_the_minimum(conn, cust):
    c = cust()
    logic.set_score_adjust(conn, c["id"], -15, "", "Will")            # 30 - 15 = 15, under the minimum of 20
    st = logic.tab_status(conn, reload(conn, c))
    assert st["status"] == "low_score" and not st["can_charge"]
    logic.add_tab_charge(conn, reload(conn, c), 100, "x", iso())
    assert conn.execute("SELECT breach FROM tab_charges").fetchone()[0] == "Tab not allowed (status: Score too low)"
    db.set_setting(conn, "tab_min_score", 55)
    assert logic.bands(logic.settings(conn)) == [(55, 500), (65, 750), (80, 1000)]  # $2.50 would be under it


def test_turning_a_tab_off_beats_the_score_and_old_approve_means_on(conn, cust):
    c = cust()
    logic.set_score_adjust(conn, c["id"], 60, "", "Will")
    logic.set_tab_decision(conn, c["id"], "revoked", "ask a parent", "Mike")
    st = logic.tab_status(conn, reload(conn, c))
    assert st["status"] == "revoked" and st["label"] == "Tab off" and not st["can_charge"]
    logic.set_tab_decision(conn, c["id"], "approved", "", "Mike")     # what older iOS apps send
    c = reload(conn, c)
    assert c["tab_decision"] is None and logic.tab_status(conn, c)["status"] == "approved"
    assert [r[0] for r in conn.execute("SELECT decision FROM tab_decisions ORDER BY id")] == ["score +60", "revoked", "on"]


def test_paused_tabs_still_pause_everyone(conn, cust):
    c = cust()
    db.set_setting(conn, "tabs_paused", "1")
    st = logic.tab_status(conn, c)
    assert st["status"] == "paused" and not st["can_charge"]


def test_approved_customers_keep_their_tab_once(conn, cust):
    low = cust("Low Kid", "rns1@reddamnorthshore.nsw.edu.au", "SQ1")
    fine = cust("Fine Kid", "rns2@reddamnorthshore.nsw.edu.au", "SQ2")
    off = cust("Off Kid", "rns3@reddamnorthshore.nsw.edu.au", "SQ3")
    conn.execute("UPDATE customers SET tab_decision = 'approved', tab_decision_note = 'old' WHERE id IN (?, ?)", (low["id"], fine["id"]))
    conn.execute("UPDATE customers SET tab_decision = 'rejected' WHERE id = ?", (off["id"],))
    db.set_setting(conn, "tab_min_score", 45)                           # new customers (30) are under it
    conn.execute("UPDATE customers SET score_adjust = 20 WHERE id = ?", (fine["id"],))  # already 50
    assert logic.retire_approvals(conn) == 1
    low, fine, off = reload(conn, low), reload(conn, fine), reload(conn, off)
    assert low["tab_decision"] is None and low["tab_decision_note"] is None and low["score_adjust"] == 15
    assert logic.tab_status(conn, low)["status"] == "approved"           # 30 + 15 = 45: still has a tab
    assert fine["tab_decision"] is None and fine["score_adjust"] == 20   # untouched
    assert off["tab_decision"] == "rejected"                             # still off
    conn.execute("UPDATE customers SET tab_decision = 'approved' WHERE id = ?", (off["id"],))
    assert logic.retire_approvals(conn) == 0                             # only ever once
    assert reload(conn, off)["tab_decision"] == "approved"


def test_an_overdue_approved_customer_gets_only_what_the_score_needs(conn, cust):
    c = cust()
    conn.execute("UPDATE customers SET tab_decision = 'approved' WHERE id = ?", (c["id"],))
    logic.add_tab_charge(conn, reload(conn, c), 300, "x", iso(8))       # overdue (7 days to pay): -25
    db.set_setting(conn, "tab_min_score", 40)
    assert logic.tab_score(conn, c["id"])["score"] == 6                  # 40 - 10 new + 1 day - 25
    logic.retire_approvals(conn)
    assert reload(conn, c)["score_adjust"] == 9                          # 40 - 31: the overdue -25 isn't made up for


# ---- pages -----------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fizzy import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    from fizzy import app as appmod
    importlib.reload(appmod)
    with TestClient(appmod.create_app(background=False)) as c:
        c.post("/login", data={"password": "test-pw", "who": "Mike"})
        c.post("/tools/add_customer", data={"name": "New Kid", "email": "rns1@reddamnorthshore.nsw.edu.au"})
        yield c


def post(client, path, data):
    return client.post(path, data=data, follow_redirects=False)


def flash(client):
    return client.get("/customers/1", headers={"X-Fizzy-Format": "json"}).json()["flash"]


def test_customer_page_limit_buttons(client):
    page = client.get("/customers/1").text
    assert "Tab limit</h2>" in page and "Can use tab – up to $2.50" in page
    assert 'name="band" value="20" class="on" disabled' in page          # their limit now
    assert client.post("/customers/1/tab", data={"band": "50", "note": "regular"}).status_code == 200
    page = client.get("/customers/1").text
    assert "Can use tab – up to $5.00" in page and "Staff adjustment" in page and "Includes a staff adjustment of +20" in page
    post(client, "/customers/1/tab", {"decision": "adjust", "adjust": "+5"})
    assert flash(client) == "Tab score 35, limit $2.50."
    d = client.get("/customers/1", headers={"X-Fizzy-Format": "json"}).json()
    assert d["s"]["status"]["score"]["score"] == 35 and d["s"]["status"]["limit_cents"] == 250


def test_customer_page_rejects_bad_input(client):
    post(client, "/customers/1/tab", {"band": "51"})
    assert flash(client) == "Pick one of the limits."
    post(client, "/customers/1/tab", {"decision": "adjust", "adjust": "lots"})
    assert flash(client).startswith("The adjustment must be a whole number")
    post(client, "/customers/1/tab", {"decision": "adjust", "adjust": "-500"})
    assert flash(client) == "An adjustment goes from -100 to +100."
    assert client.post("/customers/1/tab", data={"decision": "nonsense"}).status_code == 400


def test_turn_off_and_on_from_the_page_and_the_dashboard_count(client):
    client.post("/customers/1/tab", data={"decision": "off", "note": "ask a parent"})
    page = client.get("/customers/1").text
    assert "<b>No tab</b>" in page and "Turn tab back on" in page and "ask a parent" in page
    assert "Tabs turned off</div><div class=\"v\">1<" in client.get("/").text
    client.post("/customers/1/tab", data={"decision": "on"})
    assert "Can use tab" in client.get("/customers/1").text
    assert "Eligible for a tab" not in client.get("/").text


def test_settings_minimum_score(client):
    client.post("/settings", data={"tab_limit": "10", "due_days": "7", "min_score": "35"})
    assert logic_status(client) == "low_score"
    post(client, "/settings", {"tab_limit": "10", "due_days": "7", "min_score": "101"})
    assert flash(client) == "The minimum tab score must be a whole number from 0 to 100."


def logic_status(client):
    return client.get("/customers/1", headers={"X-Fizzy-Format": "json"}).json()["s"]["status"]["status"]


# ---- phones ----------------------------------------------------------------------------------------

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
ANDROID_PHONE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
IPAD = "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130 Mobile/15E148 Safari/604.1"
ANDROID_TABLET = "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"


@pytest.mark.parametrize("ua", [IPHONE, ANDROID_PHONE])
def test_phones_are_sent_to_the_app(client, ua):
    for path in ("/", "/login", "/customers/1"):
        r = client.get(path, headers={"User-Agent": ua}, follow_redirects=False)
        assert r.status_code == 403 and "Use the Fizzy app" in r.text, path
    assert client.post("/customers/1/tab", data={"decision": "off"}, headers={"User-Agent": ua}).status_code == 403
    assert client.get("/static/style.css", headers={"User-Agent": ua}).status_code == 200
    assert client.get("/healthz", headers={"User-Agent": ua}).status_code == 200
    assert client.get("/me/login", headers={"User-Agent": ua}).status_code == 200      # the student site is for phones


@pytest.mark.parametrize("ua", [IPAD, ANDROID_TABLET, MAC])
def test_tablets_and_computers_are_fine(client, ua):
    assert client.get("/customers/1", headers={"User-Agent": ua}).status_code == 200


def test_the_ios_app_is_fine(client):
    r = client.get("/customers/1", headers={"User-Agent": "FizzyiOS/1.0 CFNetwork/1.0 Darwin/24.0", "X-Fizzy-Format": "json"})
    assert r.status_code == 200 and r.json()["c"]["name"] == "New Kid"


def test_chrome_client_hint_counts_as_a_phone(client):
    r = client.get("/", headers={"User-Agent": "Mozilla/5.0 (Linux; Android 10; K)", "Sec-CH-UA-Mobile": "?1"})
    assert r.status_code == 403


# ---- no keyboard shortcuts ---------------------------------------------------------------------

def test_no_keyboard_shortcuts_left(client):
    """Shortcuts were removed (nobody used them): no key chips, no data-key, no ? panel."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "fizzy"
    for f in list((root / "templates").glob("*.html")) + [root / "static" / "app.js", root / "static" / "style.css"]:
        if f.name.startswith(("me", "_me")):
            continue
        text = f.read_text(encoding="utf-8")
        assert "data-key" not in text and "<kbd" not in text and "keys-btn" not in text, f.name
    for path in ("/", "/customers/1", "/customers/1/claim", "/tools", "/square", "/profit"):
        page = client.get(path).text
        assert "<kbd" not in page and "Keyboard shortcuts" not in page, path
