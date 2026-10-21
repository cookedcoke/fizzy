import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fizzy import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    from fizzy import app as appmod
    importlib.reload(appmod)
    with TestClient(appmod.create_app(background=False)) as c:
        yield c


def login(client):
    return client.post("/login", data={"password": "test-pw", "who": "Mike"}, follow_redirects=False)


def test_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.post("/login", data={"password": "nope", "who": "Mike"}).text.count("Wrong password") == 1


def test_full_flow(client):
    assert login(client).status_code == 303
    csv = b"Original Name,Cleaned Name,Email\nADELINE YATES,Adeline Yates,rns00000001@reddamnorthshore.nsw.edu.au\n"
    r = client.post("/import", files={"file": ("d.csv", csv, "text/csv")})
    assert "Import 1 row<" in r.text
    client.post("/import", files={"file": ("d.csv", csv, "text/csv")}, data={"confirm": "1"})
    hits = client.get("/api/search?q=adel").json()
    assert hits[0]["name"] == "Adeline Yates"
    cid = hits[0]["id"]
    client.post(f"/customers/{cid}/tab", data={"decision": "approve", "note": "ok"})
    client.post(f"/customers/{cid}/points", data={"delta": "100", "note": "event"})
    page = client.get(f"/customers/{cid}").text
    assert "Can use tab" in page and ">100<" in page
    for path in ["/", "/square", "/settings", "/audit", "/import", "/customers?q=zzz", "/export/square-customers.csv"]:
        assert client.get(path).status_code == 200, path
    assert "fizzy-1" in client.get("/export/square-customers.csv").text
    assert client.post("/square/webhook", content=b"{}").status_code == 403
