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
    css = client.get("/static/me.css", headers={"host": "link.cookedcoke.co"}, follow_redirects=False)  # the page's styles load
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]


# ---- review fixes: guests, claims, merges, edits and the link host -----------------------------------------

def _db():
    from fizzy import db
    return db.connect()


def _prizes(cid):
    conn = _db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM prizes WHERE customer_id = ?", (cid,))]
    finally:
        conn.close()


def _rule_points(cid):
    conn = _db()
    try:
        return conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ? AND reason = 'rule'", (cid,)).fetchone()[0]
    finally:
        conn.close()


def test_one_guest_phone_wins_the_prize_once(client):
    a = setup_student(client, "Alice A", "rns101", points=0)
    b = setup_student(client, "Bob B", "rns102", points=0)
    c = setup_student(client, "Cat C", "rns103", points=0)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink", "prize_points": "500"})
    client.post("/logout")
    client.get("/me/c/XP6RCECMRQ")
    client.get("/me/c/PPUCH7QEGB")                       # finished once, as a guest
    for sid in ("rns101", "rns102", "rns103"):           # then three people sign in on that phone
        client.post("/me/login", data={"who": sid, "passcode": PC}, headers=J)
        client.post("/me/logout", headers=J)
    assert len(_prizes(a)) == 1 and _rule_points(a) == 500
    assert _prizes(b) == [] and _prizes(c) == [] and _rule_points(b) == _rule_points(c) == 0
    # signing out leaves the phone as a fresh guest, with nothing found yet
    assert "1 of 2 found" in client.get("/me/c/XP6RCECMRQ").text


def test_claimed_guest_progress_never_moves_on_sign_in(client):
    a = setup_student(client, "Alice A", "rns101", points=0)
    b = setup_student(client, "Bob B", "rns102", points=0)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    client.post("/logout")
    client.get("/me/c/XP6RCECMRQ")
    claim = re.search(r"claim code ([A-Z0-9]{5})", client.get("/me/c/PPUCH7QEGB").text).group(1)
    conn = _db()
    try:
        ok, _ = challenges.claim(conn, claim, b, "Mike")    # staff on another device claim it for Bob
        conn.commit()
    finally:
        conn.close()
    assert ok
    client.post("/me/login", data={"who": "rns101", "passcode": PC}, headers=J)
    assert _prizes(a) == [] and len(_prizes(b)) == 1


def test_guest_scans_move_once(conn, cust):
    a, b = cust(), cust(name="Other", email="b@x.edu", sq="SQ2")
    rules.create(conn, challenges.new_hunt("Market Day", "", "", "", POSTERS, "A free drink", ""), "Will")
    challenges.guest_scan(conn, "phone-1", "XP6RCECMRQ")
    challenges.merge_guest(conn, "phone-1", a["id"])
    challenges.merge_guest(conn, "phone-1", b["id"])
    assert challenges.cards(conn, a["id"])[0]["progress"]["done"] == 1
    assert challenges.cards(conn, b["id"])[0]["progress"]["done"] == 0


def test_claim_never_gives_a_prize_twice(conn, cust):
    c = cust()
    rules.create(conn, challenges.new_hunt("Market Day", "", "", "", POSTERS, "A free drink", ""), "Will")
    challenges.scan(conn, c["id"], "XP6RCECMRQ")
    challenges.scan(conn, c["id"], "PPUCH7QEGB")           # finished signed in
    pid = conn.execute("SELECT id FROM prizes").fetchone()[0]
    conn.execute("UPDATE prizes SET given_at = ?, given_by = 'Mike' WHERE id = ?", ("2026-11-02T01:00:00+00:00", pid))
    for guest in ("phone-1", "phone-2"):                    # fresh sessions replay the public codes
        challenges.guest_scan(conn, guest, "XP6RCECMRQ")
        ok, msg, _ = challenges.guest_scan(conn, guest, "PPUCH7QEGB")
        code = re.search(r"claim code ([A-Z0-9]{5})", msg).group(1)
        ok, msg = challenges.claim(conn, code, c["id"], "Mike")
        assert not ok and "already given on" in msg.lower() and "Give them" not in msg
        assert conn.execute("SELECT claimed_at FROM guest_finishes WHERE claim = ?", (code,)).fetchone()[0] is None


def test_a_failed_claim_moves_nothing_and_archived_accounts_are_refused(conn, cust):
    c, old = cust(), cust(name="Old", email="old@x.edu", sq="SQ2")
    rid, codes = hunt(conn)
    for code in codes:
        challenges.guest_scan(conn, "phone-1", code)
    claim = conn.execute("SELECT claim FROM guest_finishes").fetchone()[0]
    spec = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
    spec["steps"].append({"label": "Canteen", "scan": True})          # a stop added after the phone finished
    rules.update(conn, rid, spec, "Will")
    ok, msg = challenges.claim(conn, claim, c["id"], "Mike")
    assert not ok and "doesn't finish" in msg
    assert conn.execute("SELECT COUNT(*) FROM challenge_scans").fetchone()[0] == 0   # nothing moved over
    conn.execute("UPDATE customers SET archived_at = ? WHERE id = ?", ("2026-11-01T00:00:00+00:00", old["id"]))
    ok, msg = challenges.claim(conn, claim, old["id"], "Mike")
    assert not ok and "archived" in msg


def test_delete_test_accounts_after_a_hunt(client):
    login(client)
    client.post("/tools/test_accounts", data={"n": "1"})
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    conn = _db()
    try:
        tid = conn.execute("SELECT id FROM customers WHERE kind = 'test'").fetchone()[0]
        challenges.scan(conn, tid, "XP6RCECMRQ")
        challenges.guest_scan(conn, "phone-1", "XP6RCECMRQ")
        challenges.guest_scan(conn, "phone-1", "PPUCH7QEGB")
        challenges.merge_guest(conn, "phone-1", tid)          # finished and won on the test account
        conn.commit()
    finally:
        conn.close()
    r = client.post("/tools/delete_test_accounts", data={"confirm": "DELETE"})
    assert "Deleted 1 test account" in r.text
    conn = _db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM customers WHERE id = ?", (tid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM prizes").fetchone()[0] == 0
        assert conn.execute("SELECT customer_id FROM guest_finishes").fetchone()[0] is None
    finally:
        conn.close()


def test_merge_moves_hunt_progress_and_prizes(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Kim Keep", "email": "rns201@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/add_customer", data={"name": "Kim Dup", "email": "rns202@reddamnorthshore.nsw.edu.au"})
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink", "prize_points": "100"})
    client.post("/challenges", data={"name": "Second", "stops": "A | | AAAAAAAAAA1\nB | | BBBBBBBBBB2", "prize_text": "A cookie"})
    conn = _db()
    try:
        keep, dup = [r[0] for r in conn.execute("SELECT id FROM customers WHERE name LIKE 'Kim %' ORDER BY id")]
        for cid in (keep, dup):                               # both won Market Day (and its points): one of each stays
            challenges.scan(conn, cid, "XP6RCECMRQ")
            challenges.scan(conn, cid, "PPUCH7QEGB")
        conn.execute("UPDATE prizes SET given_at = ?, given_by = 'Mike' WHERE customer_id = ?", ("2026-11-02T01:00:00+00:00", dup))
        challenges.scan(conn, dup, "AAAAAAAAAA1")             # only the duplicate started the second
        conn.commit()
    finally:
        conn.close()
    r = client.post("/tools/merge", data={"keep": "Kim Keep", "dup": "Kim Dup"})
    assert r.status_code == 200 and "Merged" in r.text
    conn = _db()
    try:
        prizes = challenges.prizes_for(conn, keep)
        assert len(prizes) == 1 and prizes[0]["given_at"]           # the drink was handed over already: not again
        assert challenges.prizes_for(conn, dup) == []
        assert conn.execute("SELECT COUNT(*) FROM points_ledger WHERE rule_key LIKE 'rule:%'").fetchone()[0] == 1
        second = [c for c in challenges.cards(conn, keep) if c["title"] == "Second"][0]
        assert second["progress"]["done"] == 1
    finally:
        conn.close()


def test_editing_a_hunt_keeps_codes_and_ticks_with_their_stops(conn, cust):
    c = cust()
    rid = make(conn, {"kind": "challenge", "name": "Hunt", "summary": "Find 3",
                      "steps": [{"label": "Library", "scan": True}, {"label": "Gym", "scan": True}, {"label": "Lifts", "scan": True}],
                      "prize": {"text": "A drink"}})
    spec = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
    old = {s["label"]: s["code"] for s in spec["steps"]}
    challenges.scan(conn, c["id"], old["Library"])                    # ticked Library
    spec["steps"].insert(0, {"label": "Canteen", "scan": True})       # a stop added at the front
    for s in spec["steps"]:
        s.pop("code", None)                                           # Fizz sends it back without codes
    rules.update(conn, rid, spec, "Will")
    new = {s["label"]: s["code"] for s in json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])["steps"]}
    assert new["Library"] == old["Library"] and new["Gym"] == old["Gym"] and new["Lifts"] == old["Lifts"]
    assert new["Canteen"] not in old.values()
    steps = challenges.cards(conn, c["id"])[0]["steps"]
    assert [(s["label"], s["done"]) for s in steps] == [("Canteen", False), ("Library", True), ("Gym", False), ("Lifts", False)]
    spec["steps"][3]["label"] = "The lifts"                           # a typo fixed where it stands keeps its code
    rules.update(conn, rid, spec, "Will")
    assert json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])["steps"][3]["code"] == old["Lifts"]
    del spec["steps"][1]                                              # Library taken out: its find goes with it
    rules.update(conn, rid, spec, "Will")
    assert challenges.cards(conn, c["id"])[0]["progress"]["done"] == 0


def test_old_scans_by_position_move_to_codes(tmp_path):
    from fizzy import db
    conn = db.connect(str(tmp_path / "old.db"))
    db.init(conn)
    conn.execute("INSERT INTO customers(name, email, created_at) VALUES ('Kid', 'k@x.edu', 'x')")
    spec = {"kind": "challenge", "name": "H", "summary": "s", "prize": {"text": "A drink"},
            "steps": [{"label": "A", "scan": True, "code": "AAAAAAAAAA"}, {"label": "B", "scan": True, "code": "BBBBBBBBBB"}]}
    conn.execute("INSERT INTO rules(id, kind, name, spec, created_by, created_at) VALUES (1, 'challenge', 'H', ?, 'W', 'x')", (json.dumps(spec),))
    conn.executescript("""
        DROP TABLE challenge_scans; DROP TABLE guest_scans;
        CREATE TABLE challenge_scans (customer_id INTEGER NOT NULL REFERENCES customers(id), rule_id INTEGER NOT NULL,
            step INTEGER NOT NULL, scanned_at TEXT NOT NULL, PRIMARY KEY (customer_id, rule_id, step));
        CREATE TABLE guest_scans (guest TEXT NOT NULL, rule_id INTEGER NOT NULL, step INTEGER NOT NULL,
            scanned_at TEXT NOT NULL, PRIMARY KEY (guest, rule_id, step));
        INSERT INTO challenge_scans VALUES (1, 1, 1, 'x');
        INSERT INTO challenge_scans VALUES (1, 1, 7, 'x');
        INSERT INTO guest_scans VALUES ('phone', 1, 0, 'x');
    """)
    db.init(conn)
    assert [tuple(r) for r in conn.execute("SELECT customer_id, rule_id, step_code FROM challenge_scans")] == [(1, 1, "BBBBBBBBBB")]
    assert [tuple(r) for r in conn.execute("SELECT guest, step_code, customer_id FROM guest_scans")] == [("phone", "AAAAAAAAAA", None)]
    db.init(conn)                                                      # again: nothing changes
    assert conn.execute("SELECT COUNT(*) FROM challenge_scans").fetchone()[0] == 1
    conn.close()


def test_link_host_sends_browsers_to_the_student_site(client, monkeypatch):
    setup_student(client, points=0)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    client.post("/logout")
    monkeypatch.setattr(config, "LINK_HOST", "link.cookedcoke.co")
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.cookedcoke.co")
    r = client.get("http://link.cookedcoke.co/XP6RCECMRQ", follow_redirects=False)        # Safari
    assert r.status_code == 303 and r.headers["location"] == "https://my.cookedcoke.co/c/XP6RCECMRQ"
    r = client.get("http://link.cookedcoke.co/hunt", follow_redirects=False)
    assert r.headers["location"] == "https://my.cookedcoke.co/hunt"
    clip = client.get("http://link.cookedcoke.co/PPUCH7QEGB", headers=J)                    # the App Clip still gets data
    assert clip.status_code == 200 and clip.json()["ok"]
    assert client.get("http://link.cookedcoke.co/hunt", headers=J).json()["ok"]
    # On the student site: a guest find there moves over when they sign in there.
    page = client.get("http://my.cookedcoke.co/c/XP6RCECMRQ")
    assert "1 of 2 found" in page.text and 'href="https://my.cookedcoke.co/"' in page.text
    client.post("http://my.cookedcoke.co/login", data={"who": "rns5", "passcode": PC}, headers=J)
    me = client.get("http://my.cookedcoke.co/", headers=J).json()
    assert me["cards"][0]["progress"]["done"] == 1
    # Signed in, the app opening a poster's link gets an answer, not the home page.
    r = client.get("http://my.cookedcoke.co/c/PPUCH7QEGB", headers=J).json()
    assert r["ok"] and "finished" in r["message"]


def test_the_clip_can_save_a_nickname_on_the_link_host(client, monkeypatch, clock):
    from fizzy import db
    setup_student(client, points=0)
    conn = db.connect()
    rid, codes = timed(conn)
    conn.commit()
    client.post("/logout")
    monkeypatch.setattr(config, "LINK_HOST", "link.cookedcoke.co")
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.cookedcoke.co")
    card = client.get(f"http://link.cookedcoke.co/{codes['Foundry']}", headers=J).json()["cards"][0]
    assert card["run"]["needs_name"] is True
    r = client.post("http://link.cookedcoke.co/hunt/name", data={"nickname": "Zed", "klass": "9C"}, headers=J)
    assert r.status_code == 200 and r.json()["ok"]
    card = client.get("http://link.cookedcoke.co/hunt", headers=J).json()["cards"][0]
    assert card["run"]["nickname"] == "Zed" and card["run"]["needs_name"] is False
    assert client.post("http://link.cookedcoke.co/hunt/name", data={"nickname": "Z", "klass": ""}, headers=J).json()["ok"] is False


def test_printed_codes_use_the_link_host(client, monkeypatch):
    login(client)
    client.post("/challenges", data={"name": "Market Day", "stops": "Library\nGym", "prize_text": "A free drink"})
    monkeypatch.setattr(config, "LINK_HOST", "link.cookedcoke.co")
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.cookedcoke.co")
    conn = _db()
    try:
        code = json.loads(conn.execute("SELECT spec FROM rules").fetchone()[0])["steps"][0]["code"]
    finally:
        conn.close()
    assert challenges.code_url(code) == f"https://link.cookedcoke.co/{code}"
    client.post("/logout")
    clip = client.get(f"http://link.cookedcoke.co/{code}", headers=J).json()     # both hosts know the same codes
    site = client.get(f"http://my.cookedcoke.co/c/{code}", headers=J).json()
    assert clip["ok"] and site["ok"] and "Library" in clip["message"] and "Library" in site["message"]
    monkeypatch.setattr(config, "LINK_HOST", "")
    assert challenges.code_url(code) == f"https://my.cookedcoke.co/c/{code}"


def test_apples_file_opens_the_app_only_for_codes(client, monkeypatch):
    monkeypatch.setattr(config, "LINK_HOST", "link.cookedcoke.co")
    aasa = client.get("http://link.cookedcoke.co/.well-known/apple-app-site-association").json()
    comps = aasa["applinks"]["details"][0]["components"]
    excluded = [c["/"] for c in comps if c.get("exclude")]
    assert {"/", "/hunt", "/static/*"} <= set(excluded) and comps[-1]["/"] == "/*" and not comps[-1].get("exclude")


def test_fizz_never_sees_a_hunts_codes(client):
    from fizzy import ask
    login(client)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    conn = _db()
    try:
        out = json.dumps(ask.run_tool(conn, "Mike", "list_rules", {}))
    finally:
        conn.close()
    assert "Market Day" in out and "XP6RCECMRQ" not in out and "PPUCH7QEGB" not in out
    rows = ask._sql("SELECT name, spec FROM rules")["rows"]
    assert rows[0]["name"] == "Market Day" and rows[0]["spec"] is None


def test_challenge_levels(client):
    from fizzy import db, pin
    conn = db.connect()
    old = config.ADMIN_PASSWORD
    try:
        db.init(conn)
        pin.set_pin(conn, "4821", "t")
        config.ADMIN_PASSWORD = "x"
        rid = rules.create(conn, challenges.new_hunt("Market Day", "", "", "", POSTERS, "A free drink", ""), "Will")
        bid = make(conn, {"kind": "badge", "name": "B", "summary": "s", "need": {"metric": "days", "op": ">=", "value": 3}, "points": 10})
        conn.commit()
        assert pin.required(conn, "POST", f"/rules/{rid}/pause", {}) == pin.PIN          # like starting it
        assert pin.required(conn, "POST", f"/rules/{rid}/delete", {}) == pin.PIN
        assert pin.required(conn, "POST", f"/rules/{bid}/pause", {}) == pin.OWNER        # badges as before
        assert pin.required(conn, "POST", "/challenges", {"prize_points": "100"}) == pin.PIN
        assert pin.required(conn, "POST", "/challenges", {"prize_points": "600"}) == pin.OWNER   # big, like points by hand
        db.set_setting(conn, "permissions", json.dumps({"points": "owner"}))
        assert pin.required(conn, "POST", "/challenges", {"prize_points": "100"}) == pin.OWNER
        assert pin.required(conn, "POST", "/challenges", {"prize_points": ""}) == pin.PIN
        browser = pin.rules(conn)["actions"]
        assert ["POST", rf"^/rules/{rid}/(pause|resume|delete)$", pin.PIN] in browser
    finally:
        config.ADMIN_PASSWORD = old
        conn.close()


def test_a_won_prize_stays_on_the_page_until_given(conn, cust):
    from datetime import timedelta
    from fizzy import stats
    c = cust()
    today = stats.today()
    rid, codes = hunt(conn, window={"from": (today - timedelta(days=3)).isoformat(), "to": (today - timedelta(days=1)).isoformat()})
    for code in codes:
        conn.execute("INSERT INTO challenge_scans(customer_id, rule_id, step_code, scanned_at) VALUES (?, ?, ?, 'x')", (c["id"], rid, code))
    challenges.award(conn, c["id"])
    card = challenges.cards(conn, c["id"])
    assert len(card) == 1 and card[0]["done"] and "free drink" in card[0]["status"]
    conn.execute("UPDATE prizes SET given_at = 'x'")
    assert challenges.cards(conn, c["id"]) == []


def test_scan_results_for_the_page():
    from fizzy.portal import scan_result
    r = scan_result(True, "Found it: Poster 3. 4 of 13 done.")
    assert r["kind"] == "found" and r["title"] == "Found it: Poster 3" and r["line"] == "4 of 13 done." and r["label"] == "Poster 3"
    assert scan_result(True, "You've already found Poster 1. 5 of 13 found.")["kind"] == "already"
    assert scan_result(True, "You've already finished The Hunt.")["kind"] == "already"
    r = scan_result(True, "Found it: Poster 12. That's everything - you've finished The Hunt! Show this at the counter for a free drink.")
    assert r["kind"] == "done" and r["line"].startswith("You've finished The Hunt!")
    r = scan_result(True, "Found it: Poster 12. That's everything! Show your claim code AC3DE at the stall.")
    assert r["kind"] == "done" and r["line"] == "Show your claim code AC3DE at the stall."
    assert scan_result(False, "That code isn't part of a challenge that's on.")["kind"] == "bad"
    assert scan_result(False, "Spring starts on Sunday 8 Nov. Come back then.")["kind"] == "soon"
    assert scan_result(False, "Winter has finished.")["kind"] == "over"
    assert scan_result(True, "Your hunt progress is on your account now.")["kind"] == "info"
    assert scan_result(True, None) is None


def test_hunt_pages_show_the_scan_progress_and_prize(client):
    setup_student(client)
    client.post("/challenges", data={"name": "Market Day", "stops": POSTERS, "prize_text": "A free drink"})
    client.post("/logout")
    page = client.get("/me/c/XP6RCECMRQ").text                       # a guest
    assert "is-found" in page and "Found it: Start" in page and "bakery stall" in page and "PPUCH7QEGB" not in page
    assert "Open your camera" in page
    assert "is-bad" in client.get("/me/c/NOTACODE1234").text
    page = client.get("/me/c/PPUCH7QEGB").text
    assert "is-done" in page and 'class="t-code"' in page and "Open your camera" not in page
    client.post("/me/login", data={"who": "rns5", "passcode": PC})   # moves the finished hunt onto the account
    page = client.get("/me").text
    assert "Nora Le Fevre" in page and ">rns5<" in page and "Show this at the counter for a free drink" in page
    page = client.get("/me/c/NOTACODE1234", follow_redirects=True).text
    assert "is-bad" in page and "Not a hunt code" in page


# ---- timed clue-chain hunts (fizzy/runs.py) ----------------------------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

from fizzy import runs  # noqa: E402


class Clock:
    def __init__(self, h=12, m=40):
        self.t = datetime(2026, 11, 5, h, m, tzinfo=config.TZ)

    def __call__(self):
        return self.t

    def tick(self, s):
        self.t += timedelta(seconds=s)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(runs, "_now", c)
    return c


def timed(conn, **extra):
    rule = {"kind": "challenge", "name": "Market Day", "summary": "Find every stall", "order": "shuffled",
            "steps": [{"label": "Foundry", "scan": True, "start": True, "hint": "Start here"},
                      {"label": "Pottery", "scan": True, "hint": "Where clay gets fired"},
                      {"label": "Print shop", "scan": True, "hint": "Where your name turns into a keyring"},
                      {"label": "Cafe", "scan": True, "hint": "Smells of toast"}],
            "prize": {"text": "A free drink", "points": 100}}
    rule.update(extra)
    rid = make(conn, rule)
    spec = json.loads(conn.execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
    return rid, {s["label"]: s["code"] for s in spec["steps"]}


def run_in_order(conn, clock, guest, codes, secs=25, name=None):
    """Play a whole run for a phone, a stop every `secs` seconds. Returns the last message."""
    ok, msg, _ = challenges.guest_scan(conn, guest, codes["Foundry"])
    assert ok, msg
    order = json.loads(conn.execute("SELECT order_json FROM hunt_runs WHERE player = ?", (f"g:{guest}",)).fetchone()[0])
    for c in order:
        clock.tick(secs)
        ok, msg, _ = challenges.guest_scan(conn, guest, c)
        assert ok, msg
    return msg


def test_clue_chain_only_counts_the_next_stop(conn, clock):
    rid, codes = timed(conn)
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes["Pottery"])                 # before the start code
    assert not ok and "start code first" in msg and "Foundry" in msg
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes["Foundry"])
    assert ok and "clock is running" in msg and "Your next clue:" in msg
    order = [k for c in json.loads(conn.execute("SELECT order_json FROM hunt_runs").fetchone()[0])
             for k, v in codes.items() if v == c]
    assert sorted(order) == ["Cafe", "Pottery", "Print shop"] and "Foundry" not in order
    wrong = next(k for k in codes if k not in ("Foundry", order[0]))
    clock.tick(30)
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes[wrong])
    assert not ok and msg.startswith("Not this one - your clue is:")                  # no penalty, doesn't count
    assert challenges.guest_cards(conn, "p1")[0]["progress"]["done"] == 1
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes[order[0]])
    assert ok and "2 of 4 found" in msg
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes[order[0]])                  # scanning it again is harmless
    assert ok and "already found" in msg


def test_scans_from_before_the_run_dont_count(conn, clock):
    rid, codes = timed(conn)
    for label in ("Pottery", "Print shop", "Cafe"):                                    # an earlier test, before it was timed
        conn.execute("INSERT INTO guest_scans(guest, rule_id, step_code, scanned_at) VALUES (?, ?, ?, ?)",
                     ("p1", rid, codes[label], "2026-11-01T10:00:00+00:00"))
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes["Foundry"])
    assert ok and "1 of 4 found" in msg
    card = challenges.guest_cards(conn, "p1")[0]
    assert card["progress"]["done"] == 1 and not card["run"]["finished"]
    msg = run_in_order(conn, clock, "p2", codes)
    assert "That's everything" in msg
    order = json.loads(conn.execute("SELECT order_json FROM hunt_runs WHERE player = 'g:p1'").fetchone()[0])
    for c in order:
        clock.tick(25)
        ok, msg, _ = challenges.guest_scan(conn, "p1", c)
        assert ok, msg
    assert "That's everything" in msg


def test_a_clue_chain_never_gives_away_unfound_stops(conn, cust, clock):
    rid, codes = timed(conn)
    c = cust()
    challenges.guest_scan(conn, "p1", codes["Foundry"])
    card = challenges.guest_cards(conn, "p1")[0]
    shown = json.dumps(card)
    assert len(card["steps"]) == 2 and card["steps"][0]["done"] and not card["steps"][1]["done"]
    assert card["steps"][1]["detail"] == "Your next clue"
    for label in ("Pottery", "Print shop", "Cafe"):
        assert label not in shown                                                       # only clues are shown, never the names
    assert card["progress"] == {"done": 1, "total": 4}
    ok, _ = challenges.scan(conn, c["id"], codes["Foundry"])
    mine = challenges.cards(conn, c["id"])[0]
    assert ok and all(l not in json.dumps(mine) for l in ("Pottery", "Print shop", "Cafe"))
    assert mine["run"]["clue"] and mine["progress"]["done"] == 1


def test_scans_that_count_need_a_gap(conn, clock):
    rid, codes = timed(conn)
    challenges.guest_scan(conn, "p1", codes["Foundry"])
    order = json.loads(conn.execute("SELECT order_json FROM hunt_runs").fetchone()[0])
    clock.tick(8)
    ok, msg, _ = challenges.guest_scan(conn, "p1", order[0])
    assert not ok and "Hold on" in msg and "13 more seconds" in msg
    assert challenges.guest_cards(conn, "p1")[0]["progress"]["done"] == 1
    clock.tick(13)
    assert challenges.guest_scan(conn, "p1", order[0])[0]
    ok, msg, _ = challenges.guest_scan(conn, "p1", order[1])                           # the next one is held up too
    assert not ok and "Hold on" in msg


def test_the_time_window(conn, clock):
    rid, codes = timed(conn, hours={"open": "12:45", "last_start": "13:10", "close": "13:25"})
    ok, msg, _ = challenges.guest_scan(conn, "early", codes["Foundry"])
    assert not ok and "opens at 12:45pm" in msg
    clock.tick(6 * 60)                                                                  # 12:46
    assert challenges.guest_scan(conn, "a", codes["Foundry"])[0]
    clock.t = clock.t.replace(hour=13, minute=11)                                       # past the last start
    ok, msg, _ = challenges.guest_scan(conn, "late", codes["Foundry"])
    assert not ok and "Too late to start" in msg and "1:10pm" in msg
    order = json.loads(conn.execute("SELECT order_json FROM hunt_runs WHERE player = 'g:a'").fetchone()[0])
    assert challenges.guest_scan(conn, "a", order[0])[0]                                # a run already going carries on
    clock.t = clock.t.replace(hour=13, minute=26)
    ok, msg, _ = challenges.guest_scan(conn, "a", order[1])
    assert not ok and "closed for today" in msg
    kind = lambda m: __import__("fizzy.portal", fromlist=["x"]).scan_result(False, m)["kind"]
    assert kind("Market Day opens at 12:45pm today.") == "soon" and kind(msg) == "over"
    # an ordinary hunt with hours uses open and close too
    rid2, codes2 = hunt(conn, hours={"open": "09:00", "close": "10:00"})
    assert not challenges.guest_scan(conn, "z", codes2[0])[0]


def test_ranking_and_ties(conn, clock):
    rid, codes = timed(conn)
    # a: 3 stops at 25 s = 75 s. b: the same, but starts later and finishes later. c: slower. d: quickest.
    for g, secs in (("a", 25), ("b", 25), ("c", 40), ("d", 12)):
        clock.t = clock.t.replace(hour=12, minute=41)       # all begin together, so a finishes before b: the tie goes to a
        challenges.guest_scan(conn, g, codes["Foundry"])
        order = json.loads(conn.execute("SELECT order_json FROM hunt_runs WHERE player = ?", (f"g:{g}",)).fetchone()[0])
        conn.execute("UPDATE rules SET spec = json_set(spec, '$.scan_gap', 0) WHERE id = ?", (rid,))
        for c in order:
            clock.tick(secs)
            assert challenges.guest_scan(conn, g, c)[0]
    rows = runs.board(conn, rid, 10)
    names = [r["player"] for r in rows]
    assert names == ["g:d", "g:a", "g:b", "g:c"]                                        # a and b tie on 75 s: a finished first
    assert [r["elapsed_ms"] for r in rows] == [36000, 75000, 75000, 120000]
    assert [runs.rank_of(conn, r) for r in rows] == [1, 2, 3, 4]
    assert runs.fmt_ms(75000) == "1:15" and runs.fmt_ms(3725000) == "1:02:05"
    assert challenges.guest_cards(conn, "b")[0]["run"]["rank"] == 3
    assert "You're #3 so far" in challenges.guest_cards(conn, "b")[0]["text"]


def test_one_run_per_person(conn, cust, clock):
    rid, codes = timed(conn, scan_gap=0)
    challenges.guest_scan(conn, "p1", codes["Foundry"])
    started = conn.execute("SELECT started_at FROM hunt_runs").fetchone()[0]
    clock.tick(300)
    ok, msg, _ = challenges.guest_scan(conn, "p1", codes["Foundry"])                    # scanning the start again doesn't restart it
    assert ok and "already started" in msg
    assert conn.execute("SELECT started_at FROM hunt_runs").fetchone()[0] == started
    assert conn.execute("SELECT COUNT(*) FROM hunt_runs").fetchone()[0] == 1
    run_in_order(conn, clock, "p2", codes)
    clock.tick(60)
    ok, msg, _ = challenges.guest_scan(conn, "p2", codes["Foundry"])                    # finished: no replay for a better time
    assert ok and "already finished" in msg
    assert conn.execute("SELECT COUNT(*) FROM hunt_runs WHERE player = 'g:p2'").fetchone()[0] == 1
    before = conn.execute("SELECT elapsed_ms FROM hunt_runs WHERE player = 'g:p2'").fetchone()[0]
    clock.tick(60)
    challenges.guest_scan(conn, "p2", codes["Foundry"])
    assert conn.execute("SELECT elapsed_ms FROM hunt_runs WHERE player = 'g:p2'").fetchone()[0] == before
    # signing in moves the phone's run onto the account, once
    c = cust()
    challenges.merge_guest(conn, "p1", c["id"])
    assert conn.execute("SELECT player FROM hunt_runs WHERE rule_id = ? AND customer_id = ?", (rid, c["id"])).fetchone()[0] == f"c:{c['id']}"


def test_finishing_pays_the_prize_and_gives_a_claim_code(conn, cust, clock):
    rid, codes = timed(conn)
    msg = run_in_order(conn, clock, "p1", codes)
    assert "claim code" in msg and "Your time: 1:15" in msg
    fin = conn.execute("SELECT * FROM guest_finishes WHERE guest = 'p1'").fetchone()
    assert fin and fin["claim"] in msg
    c = cust()
    ok, note = challenges.claim(conn, fin["claim"], c["id"], "Will")                    # staff claim: the drink and the points
    assert ok and "free drink" in note.lower()
    assert conn.execute("SELECT SUM(delta) FROM points_ledger WHERE customer_id = ? AND rule_key = ?", (c["id"], f"rule:{rid}")).fetchone()[0] == 100
    # a signed-in player's own run pays at the finish
    c2 = cust(name="Other", email="b@x.edu", sq="SQ2")
    clock.tick(500)
    assert challenges.scan(conn, c2["id"], codes["Foundry"])[0]
    order = json.loads(conn.execute("SELECT order_json FROM hunt_runs WHERE player = ?", (f"c:{c2['id']}",)).fetchone()[0])
    for code in order:
        clock.tick(25)
        ok, msg = challenges.scan(conn, c2["id"], code)
        assert ok, msg
    assert "free drink" in msg and "Your time: 1:15" in msg
    assert conn.execute("SELECT COUNT(*) FROM prizes WHERE customer_id = ?", (c2["id"],)).fetchone()[0] == 1


def test_top_prize_page_nickname_and_removal(client, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(runs, "_now", clock)
    from fizzy import db
    setup_student(client)
    conn = db.connect()
    try:
        rid, codes = timed(conn, prize_top={"count": 2, "text": "Coupon from a friend"})
        conn.commit()
        client.post("/logout")
        for n, secs in ((1, 20), (2, 25), (3, 30)):
            client.cookies.clear()
            assert "Your clock is running" in client.get(f"/me/c/{codes['Foundry']}", follow_redirects=True).text
            assert "Add your nickname" in client.get("/me/hunt").text
            client.post("/me/hunt/name", data={"nickname": f"Runner{n}", "klass": f"8{n}"})
            order = [r for r in json.loads(db.connect().execute("SELECT order_json FROM hunt_runs ORDER BY id DESC LIMIT 1").fetchone()[0])]
            for c in order:
                clock.tick(secs)
                page = client.get(f"/me/c/{c}", follow_redirects=True).text
            assert "Your time" in client.get("/me/hunt").text or "You found them all" in page
        assert client.post("/me/hunt/name", data={"nickname": "x", "klass": ""}).status_code in (200, 303)
        client.post("/me/login", data={"who": "rns5", "passcode": PC})
        client.post("/logout")
        login(client)
        page = client.get(f"/challenges/{rid}/run").text
        assert "Runner1" in page and "Runner2" in page and "Runner3" not in page and "Coupon from a friend" in page
        board = client.get("/api/display/hunt").json()["hunts"][0]
        assert [r["name"] for r in board["rows"]] == ["Runner1", "Runner2"] and board["finished"] == 3       # the board is the prize's top 2
        first = db.connect().execute("SELECT id FROM hunt_runs WHERE nickname = 'Runner1'").fetchone()[0]
        client.post(f"/challenges/{rid}/run/{first}/given")
        assert "Given" in client.get(f"/challenges/{rid}/run").text
        client.post(f"/challenges/{rid}/run/{first}/remove")
        page = client.get(f"/challenges/{rid}/run").text
        assert "Runner1" not in page and "Runner3" in page                                  # next in line moves up
        # settings: times in order, and the hunt stays valid
        client.post(f"/challenges/{rid}/run", data={"order": "1", "start": codes["Foundry"], "open": "13:00", "close": "12:00"})
        assert "hours" not in json.loads(db.connect().execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])   # refused
        client.post(f"/challenges/{rid}/run", data={"order": "1", "start": codes["Foundry"], "open": "12:30", "last_start": "13:10",
                                                    "close": "13:25", "top_count": "10", "top_text": "Coupon v2"})
        spec = json.loads(db.connect().execute("SELECT spec FROM rules WHERE id = ?", (rid,)).fetchone()[0])
        assert spec["hours"] == {"open": "12:30", "last_start": "13:10", "close": "13:25"}
        assert spec["prize_top"] == {"count": 10, "text": "Coupon v2"} and spec["steps"][0]["start"] is True
    finally:
        conn.close()


def test_timed_hunt_settings_are_checked(conn):
    base = {"kind": "challenge", "name": "T", "summary": "s", "prize": {"text": "x"}}
    two = [{"label": "A", "scan": True, "start": True}, {"label": "B", "scan": True}]
    with pytest.raises(rules.RuleError):
        rules.validate(conn, dict(base, order="shuffled", steps=[{"label": "A", "scan": True}, {"label": "B", "scan": True}]))   # no start
    with pytest.raises(rules.RuleError):
        rules.validate(conn, dict(base, steps=two))                                      # a start step without the chain
    with pytest.raises(rules.RuleError):
        rules.validate(conn, dict(base, order="shuffled", steps=two, hours={"open": "13:00", "close": "12:00"}))
    with pytest.raises(rules.RuleError):
        rules.validate(conn, dict(base, order="shuffled", steps=two, hours={"open": "noon"}))
    ok = rules.validate(conn, dict(base, order="shuffled", steps=two, hours={"close": "13:25"}, prize_top={"count": 10}))
    assert ok["prize_top"] == {"count": 10, "text": ""}                                   # coupon text can come later


def test_timed_hunt_levels(client):
    from fizzy import db, pin
    conn = db.connect()
    try:
        db.init(conn)
        pin.set_pin(conn, "4821", "t")
        assert pin.required(conn, "POST", "/challenges/3/run", {}) == pin.PIN
        assert pin.required(conn, "POST", "/challenges/3/run/9/given", {}) == pin.PIN
        assert pin.required(conn, "POST", "/challenges/3/run/9/remove", {}) == pin.PIN
    finally:
        conn.close()
