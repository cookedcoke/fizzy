"""Security page: incidents from the clip analyser, their videos, verdicts, and the cameras' switch (fizzy/security.py)."""
import os

import httpx
import pytest

from fizzy import config, security
from tests.test_app import client, login  # noqa: F401  (fixture)

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
CLIP = "20261110_121301_Security02.mp4"
ROOM = "20261110_121246_Security01.mp4"
RATING = {"clip": CLIP, "camera": "Security 02", "time": "2026-11-10 12:13:01", "score": 8, "people": 2,
          "items_taken": True, "summary": "One person takes a drink from the fridge and leaves.", "context": [ROOM]}


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(config, "SECURITY_TOKEN", "s3cret")
    return {"Authorization": "Bearer s3cret"}


def test_ingest_needs_the_key(client, token):
    assert client.post("/security/ingest", json=RATING).status_code == 403
    assert client.post("/security/ingest", json=RATING, headers={"Authorization": "Bearer nope"}).status_code == 403


def test_ingest_off_without_a_key(client, monkeypatch):
    monkeypatch.setattr(config, "SECURITY_TOKEN", "")
    assert client.post("/security/ingest", json=RATING, headers={"Authorization": "Bearer "}).status_code == 404


def test_incident_with_videos(client, token):
    iid = client.post("/security/ingest", json=RATING, headers=token).json()["id"]
    assert client.post("/security/ingest", json=RATING | {"score": 9}, headers=token).json()["id"] == iid   # same clip: updated
    assert client.put(f"/security/ingest/{iid}/{CLIP}", content=MP4, headers=token).json()["ok"]
    assert client.put(f"/security/ingest/{iid}/{ROOM}", content=MP4, headers=token).status_code == 200
    # only this incident's own clips, and only real MP4s
    assert client.put(f"/security/ingest/{iid}/20261110_000000_Security01.mp4", content=MP4, headers=token).status_code == 404
    assert client.put(f"/security/ingest/{iid}/{ROOM}", content=b"<html>", headers=token).status_code == 400
    assert client.put(f"/security/ingest/{iid}/..%2Fapp.db", content=MP4, headers=token).status_code == 404

    assert client.get("/security", follow_redirects=False).status_code == 303          # staff only
    login(client)
    page = client.get("/security").text
    assert "9/10" in page and "takes a drink" in page and "Tue 10 Nov, 12:13 PM" in page
    detail = client.get(f"/security/incidents/{iid}").text
    assert f"/security/clips/{iid}/{CLIP}" in detail and f"/security/clips/{iid}/{ROOM}" in detail
    video = client.get(f"/security/clips/{iid}/{CLIP}")
    assert video.content == MP4 and video.headers["content-type"] == "video/mp4"

    client.post(f"/security/incidents/{iid}/verdict", data={"verdict": "theft"})
    assert "Nothing to watch" in client.get("/security").text
    assert "Theft" in client.get("/security?show=all").text
    assert "Marked a security incident" in client.get("/audit").text
    client.post(f"/security/incidents/{iid}/verdict", data={"verdict": ""})
    assert "takes a drink" in client.get("/security").text                              # reopened


def test_prune_deletes_oldest_videos(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "fizzy.db"))
    ids = []
    for n, t in enumerate(["09", "10", "11"]):
        ids.append(security.save_incident(conn, RATING | {"clip": f"20261110_{t}0000_Security02.mp4",
                                                          "time": f"2026-11-10 {t}:00:00", "context": []}))
        path = security.clip_path(ids[-1], f"20261110_{t}0000_Security02.mp4")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(b"x" * 1000)
    assert security.prune_clips(conn, limit_bytes=1500) == 2
    gone = [r["clips_gone"] for r in conn.execute("SELECT clips_gone FROM incidents ORDER BY id")]
    assert gone == [1, 1, 0]                                                           # oldest first; ratings kept


def test_camera_switch(client, monkeypatch):
    login(client)
    assert "Unknown" in client.get("/security").text                                   # Home Assistant not set up
    monkeypatch.setattr(config, "HA_TOKEN", "t")
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"state": "armed_away"}
        return []
    monkeypatch.setattr(security, "_ha", fake)
    assert ">Armed<" in client.get("/security").text
    r = client.post("/security/disarm")
    assert "Cameras disarmed" in r.text
    assert [c for c in calls if c[0] == "POST"] == [
        ("POST", "/api/services/alarm_control_panel/alarm_disarm", {"entity_id": config.HA_ALARM_ENTITY})]
    assert "Disarmed the cameras" in client.get("/audit").text

    def down(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(security, "_ha", down)
    assert "reach Home Assistant. Nothing changed" in client.post("/security/arm").text
    assert "Home Assistant didn&#39;t answer" in client.get("/security").text
