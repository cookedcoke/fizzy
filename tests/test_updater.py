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


def test_failed_update_shows_the_log(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FIZZY_UPDATES_DIR", str(tmp_path))
    login(client)
    (tmp_path / "update-status.json").write_text(json.dumps(
        {"state": "failed", "step": "done", "at": "Mon", "message": "went back", "behind": 1, "seen": time.time(), "started": 1}))
    (tmp_path / "last-failure.log").write_text("fizzy-1  | RuntimeError: boom")
    assert "RuntimeError: boom" in client.get("/settings").text
    assert "RuntimeError: boom" in client.get("/settings/update/status").json()["log"]


def test_unreadable_status_isnt_reported_as_not_running(client, tmp_path, monkeypatch):
    from fizzy import updater
    monkeypatch.setenv("FIZZY_UPDATES_DIR", str(tmp_path))
    monkeypatch.setenv("FIZZY_UPDATE_STATUS_DIR", str(tmp_path))
    real = type(tmp_path / "x").read_text

    def deny(self, *a, **k):
        if self.name == "update-status.json":
            raise PermissionError("denied")
        return real(self, *a, **k)
    monkeypatch.setattr(type(tmp_path / "x"), "read_text", deny)
    assert updater.status()["unreadable"] is True
    login(client)
    page = client.get("/settings").text
    assert "isn't allowed to read it" in page and "The updater isn't running" not in page


def test_interrupted_update_isnt_left_running(tmp_path):
    # The PC restarted (or the updater was stopped) part way through: the next start says so.
    import shutil
    import subprocess
    from pathlib import Path
    (tmp_path / "deploy").mkdir()
    shutil.copy(Path(__file__).resolve().parent.parent / "deploy" / "update.sh", tmp_path / "deploy" / "update.sh")
    (tmp_path / "update-status").mkdir()
    status = tmp_path / "update-status" / "update-status.json"
    status.write_text(json.dumps({"state": "running", "step": "verify", "at": "Mon", "from": "a", "to": "b",
                                  "message": "Updating from a", "behind": 1, "started": 5, "seen": 5}))
    subprocess.run(["bash", str(tmp_path / "deploy" / "update.sh"), "--check"], cwd=tmp_path, capture_output=True, timeout=30)
    s = json.loads(status.read_text())
    assert s["state"] == "failed" and s["step"] == "done" and "interrupted" in s["message"]
