import json
from pathlib import Path

from fizzy import db
from fizzy.__main__ import main
from tests.test_app import client, login  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def _fresh():
    from fizzy import app
    app._maint["at"] = 0


def test_maintenance_mode(client):
    login(client)
    assert main(["maintenance", "on", "Upgrading the server, back by lunch."]) == 0
    _fresh()
    r = client.get("/")
    assert r.status_code == 503 and "Back soon" in r.text and "Upgrading the server, back by lunch." in r.text
    assert r.headers["x-fizzy"] == "maintenance"
    assert client.get("/me").status_code == 503
    assert client.get("/static/down.html").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.post("/square/webhook", content=b"{}").status_code != 503   # sales still come in
    assert main(["maintenance", "off"]) == 0
    _fresh()
    assert client.get("/").status_code == 200


def test_default_message_and_escaping(client):
    main(["maintenance", "on"])
    _fresh()
    assert "doing some maintenance" in client.get("/").text
    main(["maintenance", "on", "<script>x</script>"])
    _fresh()
    assert "<script>x</script>" not in client.get("/").text
    main(["maintenance", "off"])
    _fresh()


def test_worker_has_the_current_page():
    page = (ROOT / "fizzy" / "static" / "down.html").read_text()
    worker = (ROOT / "cloudflare" / "worker.js").read_text()
    assert json.dumps(page) in worker, "run: python cloudflare/make_worker.py"
