"""Custom badges and events (fizzy/rules.py) and the Ask page (fizzy/ask.py) with a fake Gemini."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from fizzy import ask, config, db, logic, rules, square
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_pin import _unlock, owner  # noqa: F401  (fixture)
from tests.test_rules import order, pay


def at(days_ago=0, hour=12, minute=0):
    """A Sydney time on a day before today, as Square's UTC timestamp."""
    d = datetime.now(config.TZ).replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def sale(conn, pid, *items, ts=None, cust="SQ1"):
    square.process_payment(conn, pay(pid, sum(c for _, c in items), cust=cust, ts=ts or at(), order_id=f"o{pid}"),
                           order(*items, oid=f"o{pid}"))


def make(conn, rule, days_ago=30):
    rid = rules.create(conn, rule, "Will")
    old = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    conn.execute("UPDATE rules SET created_at = ? WHERE id = ?", (old, rid))  # made a while back, so events count these sales
    return rid


def bonus(conn, cid):
    return sum(r[0] for r in conn.execute("SELECT delta FROM points_ledger WHERE customer_id = ? AND reason = 'rule'", (cid,)))


def test_badge_with_two_item_counts(conn, cust):
    c = cust()
    make(conn, {"kind": "badge", "name": "Bubble Boss", "summary": "2 Chi Forest and 1 milk tea", "points": 500,
                "need": {"all": [{"metric": "units", "op": ">=", "value": 2, "where": {"items": ["chi forest"]}},
                                 {"metric": "units", "op": ">=", "value": 1, "where": {"items": ["milk tea"]}}]}})
    sale(conn, "p1", ("Chi Forest Peach", 400), ts=at(3))
    sale(conn, "p2", ("Chi Forest Lychee", 400), ts=at(2))
    assert rules.award(conn, c["id"]) == []                              # no milk tea yet
    sale(conn, "p3", ("Milk Tea", 450), ts=at(1))
    assert rules.award(conn, c["id"]) == ["Bubble Boss"]
    assert rules.award(conn, c["id"]) == []                              # once
    assert bonus(conn, c["id"]) == 500


def test_weekly_badge_repeats(conn, cust):
    c = cust()
    make(conn, {"kind": "badge", "name": "Three a week", "summary": "3 purchases in a week", "points": 50, "repeat": "week",
                "need": {"metric": "purchases", "op": ">=", "value": 3}})
    monday = datetime.now(config.TZ).date() - timedelta(days=datetime.now(config.TZ).weekday() + 14)  # two weeks back
    for i in range(3):
        d = (monday - datetime.now(config.TZ).date()).days
        sale(conn, f"a{i}", ("Coke", 300), ts=at(-d - i))       # Mon-Wed two weeks ago
        sale(conn, f"b{i}", ("Coke", 300), ts=at(-d + 7 - i))   # Mon-Wed the week before
    rules.award(conn, c["id"])
    assert bonus(conn, c["id"]) == 100                                   # two different weeks


def test_event_multiplier_on_matching_items_and_never_before_it_existed(conn, cust):
    c = cust()
    sale(conn, "old", ("Noodles", 350), ts=at(10))
    make(conn, {"kind": "event", "name": "Noodle double", "summary": "Double points on noodles",
                "where": {"items": ["noodles"]}, "bonus": {"type": "multiplier", "value": 2, "on": "matching_items"}}, days_ago=5)
    sale(conn, "new", ("Noodles", 350), ("Coke", 300), ts=at(1))
    rules.award(conn, c["id"])
    assert bonus(conn, c["id"]) == 350                                   # only the noodles, only the new sale


def test_every_nth_and_limit_and_time_of_day(conn, cust):
    c = cust()
    make(conn, {"kind": "event", "name": "Every 3rd Coke", "summary": "Every 3rd Coke at lunch gets 100 points",
                "where": {"items": ["coke"], "time_from": "13:00", "time_to": "14:00"}, "bonus": {"type": "points", "points": 100},
                "every_nth": 3, "limit": {"per": "ever", "times": 1}})
    for i in range(7):
        sale(conn, f"l{i}", ("Coke", 300), ts=at(7 - i, hour=13, minute=20))
    sale(conn, "recess", ("Coke", 300), ts=at(1, hour=11, minute=5))   # not lunch: doesn't count
    rules.award(conn, c["id"])
    assert bonus(conn, c["id"]) == 100                                   # 3rd and 6th qualify, limit 1


def test_tried_all_and_first_of_day(conn, cust):
    c = cust()
    d = cust("Other Kid", "rns00000002@reddamnorthshore.nsw.edu.au", "SQ2")
    make(conn, {"kind": "badge", "name": "Explorer+", "summary": "Try Coke, Fanta and Sprite", "points": 200,
                "need": {"metric": "tried_all", "items": ["coke", "fanta", "sprite"]}})
    make(conn, {"kind": "event", "name": "Early bird", "summary": "First buyer of the day gets 50", "where": {"first_of_day": 1},
                "bonus": {"type": "points", "points": 50}})
    sale(conn, "x0", ("Water", 200), ts=at(2, hour=11, minute=1), cust="SQ2")
    for i, item in enumerate(("Coke", "Fanta", "Sprite")):
        sale(conn, f"x{i + 1}", (item, 300), ts=at(2, hour=11, minute=5 + i))
    rules.award(conn, c["id"]); rules.award(conn, d["id"])
    assert bonus(conn, c["id"]) == 200 and bonus(conn, d["id"]) == 50


def test_validation_says_whats_wrong(conn):
    bad = [({"kind": "badge", "name": "x", "summary": "y", "points": 10, "need": {"metric": "vibes", "value": 1}}, "Unknown metric"),
           ({"kind": "badge", "name": "x", "summary": "y", "points": 99999, "need": {"metric": "units", "value": 1}}, "points"),
           ({"kind": "event", "name": "x", "summary": "y", "bonus": {"type": "multiplier", "value": 50}}, "bonus.value"),
           ({"kind": "badge", "name": "x", "summary": "y", "points": 5, "window": {"term": "current"}, "need": {"metric": "units", "value": 1}}, "term dates"),
           ({"kind": "event", "name": "x", "summary": "y", "where": {"weather": "rain"}, "bonus": {"type": "points", "points": 5}}, "doesn't know"),
           ("import os", "object")]
    for rule, why in bad:
        with pytest.raises(rules.RuleError, match=why):
            rules.validate(conn, rule)


def test_term_window(conn, cust):
    c = cust()
    today = datetime.now(config.TZ).date()
    db.set_setting(conn, "terms", json.dumps([[(today - timedelta(days=20)).isoformat(), (today + timedelta(days=20)).isoformat(), "Term 4"]]))
    make(conn, {"kind": "badge", "name": "Term regular", "summary": "5 days this term", "points": 300, "window": {"term": "current"},
                "need": {"metric": "days", "op": ">=", "value": 5}})
    for i in range(6):
        sale(conn, f"t{i}", ("Coke", 300), ts=at(25 - i if i < 2 else 10 - i))  # 2 before the term, 4 in it
    assert rules.award(conn, c["id"]) == []
    sale(conn, "t9", ("Coke", 300), ts=at(1))
    assert rules.award(conn, c["id"]) == ["Term regular"]


def test_sql_tool_is_read_only(client):
    assert "error" in ask._sql("DELETE FROM customers")
    assert "error" in ask._sql("SELECT 1; DROP TABLE customers")
    assert ask._sql("SELECT COUNT(*) AS n FROM customers")["rows"][0]["n"] == 0


class FakeGemini:
    """Plays back a script of Gemini replies and records what was sent."""
    def __init__(self, script):
        self.script, self.sent = list(script), []

    def __call__(self, url, params=None, json=None, timeout=None, headers=None):
        self.sent.append(copy.deepcopy(json))
        parts = self.script.pop(0)

        class R:
            status_code = 200
            text = ""

            def json(self_inner):
                return {"candidates": [{"content": {"role": "model", "parts": parts}}]}
        return R()


def test_ask_makes_a_badge_that_pays_when_done(client, owner, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    login(client)
    conn = db.connect()
    conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES ('Mia Patel', 'rns1@x', 'SQ1', ?)", (db.now_iso(),))
    sale(conn, "q1", ("Coke", 300), ts=at(1))
    conn.commit()
    rule = {"kind": "badge", "name": "Coke fan", "summary": "Buy a Coke", "points": 75, "need": {"metric": "units", "value": 1, "where": {"items": ["coke"]}}}
    fake = FakeGemini([
        [{"functionCall": {"name": "preview_rule", "args": {"rule_json": json.dumps(rule)}}}],
        [{"functionCall": {"name": "propose", "args": {"action": "create_rule", "payload_json": json.dumps({"rule": rule}), "summary": "Coke fan badge, 75 points"}}}],
        [{"text": "Here's a **Coke fan** badge. 1 student would get it now."}],
    ])
    monkeypatch.setattr(ask.httpx, "post", fake)
    page = client.post("/ask", data={"text": "make a badge for buying a coke worth 75"}).text
    assert "Coke fan badge, 75 points" in page and "<b>Coke fan</b>" in page and "Do it" in page
    preview = fake.sent[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert preview["students_who_qualify_now"] == 1                      # Gemini was told who'd qualify
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0  # nothing until Do it
    pid = conn.execute("SELECT id FROM ask_proposals").fetchone()[0]
    assert "Enter the passcode" in client.post(f"/ask/apply/{pid}").text  # owner level by default
    _unlock(client, "owner-pw", 2)
    client.post(f"/ask/apply/{pid}")
    assert conn.execute("SELECT name FROM rules").fetchone()[0] == "Coke fan"
    assert logic.points_balance(conn, 1) == 300 + 75                      # paid straight away
    assert "Coke fan" in client.get("/rewards").text
    conn.close()


def test_ask_without_a_key_says_how(client):
    login(client)
    assert "Settings, Ask" in client.get("/ask").text


def test_key_from_settings(client, owner, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    login(client)
    assert "Enter the passcode" in client.post("/settings/ask", data={"key": "AIza" + "x" * 35}).text   # owner only
    _unlock(client, "owner-pw", 2)
    assert "Ask is on" in client.post("/settings/ask", data={"key": "AIza" + "x" * 31 + "WXYZ"}).text
    assert ask.api_key().endswith("WXYZ") and ask.enabled()
    page = client.get("/settings").text
    assert "ends <code>WXYZ</code>" in page and "AIzaxxxx" not in page                                 # never shown back
    conn = db.connect()
    assert "AIza" not in conn.execute("SELECT value FROM settings WHERE key = 'gemini_key_enc'").fetchone()[0]  # encrypted
    conn.close()
    assert "doesn" in client.post("/settings/ask", data={"key": "hello"}).text
    assert "Ask is on" in client.post("/settings/ask", data={"key": "AQ.Ab8RN6" + "x" * 40}).text   # Google's newer style
    client.post("/settings/ask", data={"remove": "1"})
    assert not ask.enabled()


def test_history_import_earns_nothing(conn, cust):
    from fizzy import badges, stats
    c = cust()
    db.set_setting(conn, "history_before", at(5, hour=0))
    sale(conn, "old", ("Coke", 300), ts=at(10))                  # before launch, with the student attached
    square.process_order(conn, {"id": "tabold", "state": "COMPLETED", "customer_id": "SQ1", "closed_at": at(9), "created_at": at(9),
                                "line_items": [{"name": "Chips", "quantity": "1", "total_money": {"amount": 250}}],
                                "discounts": [{"name": "Fizzy Tab", "percentage": "100", "applied_money": {"amount": 250}}]})
    assert logic.points_balance(conn, c["id"]) == 0                # no points
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0      # no tab charge
    sale(conn, "new", ("Coke", 300), ts=at(1))
    assert logic.points_balance(conn, c["id"]) == 300              # after launch: as normal
    lo, hi = stats.day_bounds(stats.today() - timedelta(days=12))[0], "9999"
    assert len([x for x in stats.checkouts(conn, lo, hi) if x["items"]]) == 3   # but history is in the numbers
    make(conn, {"kind": "badge", "name": "Two Cokes", "summary": "2 Cokes", "points": 10,
                "need": {"metric": "units", "value": 2, "where": {"items": ["coke"]}}})
    assert rules.award(conn, c["id"]) == []                        # and custom badges don't count it


def test_sales_summary_and_answering_when_out_of_steps(client, monkeypatch):
    conn = db.connect()
    conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES ('Mia Patel', 'rns1@x', 'SQ1', ?)", (db.now_iso(),))
    sale(conn, "s1", ("Coke", 300), ("Chips", 250), ts=at(3)); sale(conn, "s2", ("Coke", 300), ts=at(2))
    conn.commit()
    today = datetime.now(config.TZ).date()
    r = ask._summary(conn, (today - timedelta(days=7)).isoformat(), today.isoformat(), "weekday,item")
    assert sum(x["items"] for x in r["rows"] if x["item"] == "Coke") == 2 and r["trading_days_per_weekday"]
    assert "error" in ask._summary(conn, "x", "y", "weekday") and "error" in ask._summary(conn, "2026-01-01", "2026-01-02", "vibes")
    # Out of steps: it's asked to answer from what it has instead of giving up.
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(ask, "MAX_STEPS", 2)
    fake = FakeGemini([[{"functionCall": {"name": "sales_summary", "args": {"start": "2026-01-01", "end": "2026-12-31", "group_by": "weekday"}}}]] * 2
                      + [[{"text": "Mondays are slowest."}]])
    monkeypatch.setattr(ask.httpx, "post", fake)
    assert ask.ask(conn, "Will", "slowest day?") == "Mondays are slowest."
    assert fake.sent[-1]["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"
    conn.close()
