"""Challenges (fizzy/challenges.py): steps to finish for a prize, e.g. a scavenger hunt."""
import json
import re

import pytest

from fizzy import challenges, config, rules
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_custom_rules import make, sale
from tests.test_portal import PC, setup_student

J = {"X-Fizzy-Format": "json"}


def hunt(conn, **extra):
    rule = {"kind": "challenge", "name": "Find the Fizz", "summary": "Find all 3 codes",
            "steps": [{"label": "Library", "scan": True, "hint": "By the returns desk"}, {"label": "Gym", "scan": True},
                      {"label": "Level 7", "scan": True}],
            "prize": {"text": "A free drink", "points": 100}}
    rule.update(extra)
    rid = make(conn, rule)
    spec = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
    return rid, [s["code"] for s in spec["steps"]]


def test_scanning_every_code_wins_the_prize_once(conn, cust):
    c = cust()
    rid, codes = hunt(conn)
    assert len(set(codes)) == 3 and all(len(x) >= 12 for x in codes)
    ok, msg = challenges.scan(conn, c["id"], challenges.code_url(codes[0]))      # the QR is a link
    assert ok and "Library" in msg and "1 of 3" in msg
    ok, msg = challenges.scan(conn, c["id"], codes[0])                            # or just the code
    assert ok and "already" in msg
    challenges.scan(conn, c["id"], codes[1])
    ok, msg = challenges.scan(conn, c["id"], codes[2])
    assert ok and "finished" in msg and "free drink" in msg
    prize = conn.execute("SELECT * FROM prizes WHERE customer_id = ?", (c["id"],)).fetchall()
    assert len(prize) == 1 and prize[0]["text"] == "A free drink" and prize[0]["given_at"] is None
    pts = conn.execute("SELECT SUM(delta) FROM points_ledger WHERE customer_id = ? AND rule_key = ?", (c["id"], f"rule:{rid}")).fetchone()[0]
    assert pts == 100
    rules.award(conn, c["id"])                                                    # never paid twice
    assert conn.execute("SELECT COUNT(*) FROM prizes").fetchone()[0] == 1
    assert conn.execute("SELECT SUM(delta) FROM points_ledger WHERE rule_key IS NOT NULL").fetchone()[0] == 100


def test_unknown_paused_and_sign_in_codes_are_refused(conn, cust):
    c = cust()
    rid, codes = hunt(conn)
    assert challenges.scan(conn, c["id"], "not-a-real-code-at-all")[0] is False
    assert "sign-in" in challenges.scan(conn, c["id"], "https://my.example.com/q/abcdefgh123")[1]
    rules.set_state(conn, rid, "pause", "Will")
    assert challenges.scan(conn, c["id"], codes[0])[0] is False


def test_purchase_steps_and_the_card(conn, cust):
    c = cust()
    rid = make(conn, {"kind": "challenge", "name": "Taste test", "summary": "Try two things",
                      "steps": [{"label": "A Chi Forest", "need": {"metric": "units", "op": ">=", "value": 1, "where": {"items": ["chi forest"]}}},
                                {"label": "Find the code", "scan": True}],
                      "prize": {"points": 50}})
    card = challenges.cards(conn, c["id"])[0]
    assert card["progress"] == {"done": 0, "total": 2} and card["actions"][0]["kind"] == "scan"
    assert [s["kind"] for s in card["steps"]] == ["buy", "scan"] and card["prize"] == "50 points"
    sale(conn, "p1", ("Chi Forest Peach", 350))
    card = challenges.cards(conn, c["id"])[0]
    assert card["steps"][0]["done"] and not card["done"]
    code = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])["steps"][1]["code"]
    challenges.scan(conn, c["id"], code)
    card = challenges.cards(conn, c["id"])[0]
    assert card["done"] and card["actions"] == [] and code not in json.dumps(card)          # the secret never goes out


def test_editing_keeps_printed_codes(conn, cust):
    rid, codes = hunt(conn)
    spec = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
    spec["name"] = "Find the Fizz 2"
    spec["steps"].append({"label": "Canteen", "scan": True})
    for s in spec["steps"]:
        s.pop("code", None)                                          # Fizz sends it back without codes
    rules.update(conn, rid, spec, "Will")
    after = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
    assert [s["code"] for s in after["steps"]][:3] == codes and after["steps"][3]["code"] not in codes


def test_bad_challenges_are_explained(conn):
    for bad, words in [({"steps": []}, "list of steps"), ({"steps": [{"label": "x"}]}, "scan"),
                       ({"prize": {}}, "prize"), ({"steps": [{"scan": True}]}, "label")]:
        rule = {"kind": "challenge", "name": "X", "summary": "Y", "steps": [{"label": "A", "scan": True}],
                "prize": {"text": "A drink"}}
        rule.update(bad)
        with pytest.raises(rules.RuleError, match=words):
            rules.validate(conn, rule)


def test_apps_scan_and_staff_give_the_prize(client):
    cid = setup_student(client)
    client.post("/challenges", data={"name": "Find the Fizz", "stops": "Library | By the desk\nGym", "prize_text": "A free drink"})
    from fizzy import db
    conn = db.connect()
    try:
        spec = json.loads(conn.execute("SELECT spec FROM rules WHERE kind = 'challenge'").fetchone()[0])
    finally:
        conn.close()
    codes = [s["code"] for s in spec["steps"]]
    assert spec["steps"][0]["hint"] == "By the desk"
    page = client.get("/challenges/1/codes").text
    assert "<svg" in page and "Library" in page
    client.post("/logout")
    client.post("/me/login", data={"who": "rns5", "passcode": PC}, headers=J)
    me = client.get("/me", headers=J).json()
    card = me["cards"][0]
    assert card["title"] == "Find the Fizz" and card["actions"][0]["kind"] == "scan" and not any(x in json.dumps(me) for x in codes)
    r = client.post("/me/scan", data={"code": "https://example.com/me/c/" + codes[0]}, headers=J)
    assert r.status_code == 200 and "1 of 2" in r.json()["message"]
    bad = client.post("/me/scan", data={"code": "nonsense"}, headers=J)
    assert bad.status_code == 400 and "error" in bad.json()
    r = client.get(f"/me/c/{codes[1]}", follow_redirects=True)             # the phone camera's way in
    assert "finished" in r.text
    assert client.get("/me", headers=J).json()["cards"][0]["done"]
    client.post("/me/logout")
    login(client)
    page = client.get(f"/customers/{cid}").text
    assert "Challenge prizes" in page and "A free drink" in page
    client.post(f"/customers/{cid}/prizes/1/given")   # no PIN set in tests, so nothing asks for one
    page = client.get(f"/customers/{cid}", headers=J).json()
    assert page["prizes"][0]["given_at"] and page["prizes"][0]["given_by"] == "Mike"


# ---- codes already printed on posters, and playing without signing in -----------------------------

POSTERS = "Start | the master poster | HTTPS://LINK.COOKEDCOKE.CO/XP6RCECMRQ\nPoster 1 | bakery stall | PPUCH7QEGB"


def test_printed_codes_from_any_link(conn, cust):
    c = cust()
    rules.create(conn, challenges.new_hunt("Market Day", "", "", "", POSTERS, "A free drink", ""), "Will")
    spec = json.loads(conn.execute("SELECT spec FROM rules").fetchone()[0])
    assert [s["code"] for s in spec["steps"]] == ["XP6RCECMRQ", "PPUCH7QEGB"]
    assert challenges.scan(conn, c["id"], "HTTPS://LINK.COOKEDCOKE.CO/PPUCH7QEGB")[0]       # the poster's own link
    assert challenges.scan(conn, c["id"], "https://link.cookedcoke.co/xp6rcecmrq/")[0]      # any case, a trailing slash
    assert conn.execute("SELECT COUNT(*) FROM prizes").fetchone()[0] == 1
    with pytest.raises(rules.RuleError, match="same code"):
        rules.create(conn, challenges.new_hunt("Twice", "", "", "", "A | | PPUCH7QEGB\nB | | ppuch7qegb", "A drink", ""), "Will")


def test_guest_plays_then_staff_claim_it_onto_a_new_student(client):
    login(client)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink", "prize_points": "100"})
    client.post("/logout")
    phone = client
    r = phone.get("/me/c/XP6RCECMRQ")
    assert "1 of 2 found" in r.text and "Sign in" in r.text
    r = phone.get("/me/c/PPUCH7QEGB")
    claim = re.search(r"claim code ([A-Z0-9]{5})", r.text).group(1)
    assert claim in r.text and "Show this at the stall" in r.text
    assert claim in phone.get("/me/hunt").text                                   # coming back to it
    # at the stall: a new student, added there and then
    login(client)
    client.post("/tools/add_customer", data={"name": "New Kid", "email": "rns20260001@reddamnorthshore.nsw.edu.au"})
    cid = client.get("/api/index").json()[-1]["id"]
    assert "No hunt finished" in client.post(f"/customers/{cid}/claim-prize", data={"code": "NOPE1"}).text
    r = client.post(f"/customers/{cid}/claim-prize", data={"code": claim.lower()})
    assert "claimed" in r.text and "free drink" in r.text
    page = client.get(f"/customers/{cid}", headers=J).json()
    assert page["prizes"][0]["given_at"] and page["s"]["points"] == 100
    assert "already claimed" in client.post(f"/customers/{cid}/claim-prize", data={"code": claim}).text


def test_signing_in_moves_the_phones_progress(client):
    setup_student(client)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    client.post("/logout")
    client.get("/me/c/XP6RCECMRQ")                                               # one found before signing in
    r = client.post("/me/login", data={"who": "rns5", "passcode": PC}, follow_redirects=True)
    assert r.status_code == 200
    me = client.get("/me", headers=J).json()
    assert me["cards"][0]["progress"] == {"done": 1, "total": 2}
    client.post("/me/scan", data={"code": "PPUCH7QEGB"}, headers=J)
    assert client.get("/me", headers=J).json()["cards"][0]["done"]


def test_link_host_serves_apples_file_and_poster_codes(client, monkeypatch):
    login(client)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    client.post("/logout")
    monkeypatch.setattr(config, "LINK_HOST", "link.cookedcoke.co")
    aasa = client.get("/.well-known/apple-app-site-association", headers={"host": "link.cookedcoke.co"}).json()
    assert aasa["appclips"]["apps"] == ["9MQ6DTJYXF.co.cookedcoke.student.Clip"]
    assert aasa["applinks"]["details"][0]["appIDs"] == ["9MQ6DTJYXF.co.cookedcoke.student"]
    r = client.get("/XP6RCECMRQ", headers={"host": "link.cookedcoke.co"})                     # a poster, opened in Safari
    assert "1 of 2 found" in r.text
    r = client.get("/PPUCH7QEGB", headers={"host": "link.cookedcoke.co", **J}).json()        # the App Clip
    assert r["ok"] and r["cards"][0]["claim"] and "claim code" in r["message"]
    page = client.get("/tools", headers={"host": "link.cookedcoke.co"}, follow_redirects=False)   # never a staff page
    assert "Tools" not in page.text
