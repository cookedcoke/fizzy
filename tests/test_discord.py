"""Discord announcements (fizzy/discord.py)."""
from fizzy import config, discord, events, stats
from tests.test_app import client, login  # noqa: F401  (fixture)


def test_payload_only_pings_what_was_asked(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_MENTION", "")
    p = discord.payload("New reward: @everyone free", "x")
    assert p["allowed_mentions"] == {"parse": []}                       # a name can't ping anyone
    monkeypatch.setattr(config, "DISCORD_MENTION", "<@&123>")
    p = discord.payload("t", "b")
    assert p["content"].startswith("<@&123> **t**") and p["allowed_mentions"] == {"parse": [], "roles": ["123"]}


def test_announcement_wording():
    t = stats.today().isoformat()
    assert events.announcement("Double points Friday", "2", t, "", "")[1].startswith("Double points on everything, today.")
    assert "every " in events.announcement("Double points Friday", "2", t, "", "1")[1]


def test_launching_announces(client, monkeypatch):
    sent = []
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.example/hook")
    monkeypatch.setattr(discord, "announce", lambda title, body: sent.append((title, body)) or True)
    login(client)
    assert "Announce on Discord" in client.get("/rewards").text
    client.post("/rewards", data={"name": "Free Coke", "points": "1000", "announce": "1"})
    client.post("/events", data={"name": "Double points", "multiplier": "2", "start": stats.today().isoformat(), "announce": "1"})
    client.post("/rewards", data={"name": "Quiet one", "points": "50"})          # box unticked: no post
    assert [s[0] for s in sent] == ["New reward: Free Coke", "Double points"]
    assert "1,000 points" in sent[0][1]
