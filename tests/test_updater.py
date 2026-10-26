import json
import time

from fizzy import config, pin
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_pin import _unlock


def test_update_button(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FIZZY_UPDATES_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-pw")
    monkeypatch.setattr(pin, "_fails", {})
    login(client)
    page = client.get("/settings").text
    assert "The updater isn't running on the home PC" in page
    _unlock(client, "owner-pw", 2)
    assert "isn&#39;t running" in client.post("/settings/update").text
    assert not (tmp_path / "update-requested").exists()
    # The script on the PC checks in and says there's something new.
    (tmp_path / "update-status.json").write_text(json.dumps(
        {"state": "ok", "at": "Mon 26 Oct 09:00", "message": "abc to def", "behind": 3, "seen": time.time()}))
    page = client.get("/settings").text
    assert "3 new changes" in page and "Update now" in page and "worked" in page
    r = client.post("/settings/update", follow_redirects=False)
    assert r.headers["location"].startswith("/settings/update/progress?since=")
    assert (tmp_path / "update-requested").exists()
    assert "Waiting for the home PC" in client.get(r.headers["location"]).text
    s = client.get("/settings/update/status").json()
    assert s["requested"] is True and s["watching"] is True


def test_update_is_owner_only(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-pw")
    login(client)
    r = client.post("/settings/update", headers={"referer": "http://testserver/settings"})
    assert "Enter the passcode first" in r.text
