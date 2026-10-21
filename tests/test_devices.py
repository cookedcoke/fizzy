import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from fizzy import config, db, devices
from tests.test_app import client, login  # noqa: F401  (fixture)


@pytest.fixture
def locked(client, monkeypatch):
    monkeypatch.setattr(config, "HOST", "testserver")
    monkeypatch.setattr(config, "ACCESS_HOST", "access.test")
    return client


def test_unregistered_device_is_sent_to_access_host(locked):
    r = locked.get("/customers?q=a", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://access.test/customers?q=a"
    assert locked.post("/login", data={"password": "test-pw", "who": "Mike"}).status_code == 403
    assert locked.get("/healthz").status_code == 200
    assert locked.post("/square/webhook", content=b"{}").status_code == 403  # exempt: its own signature check


def test_access_host_needs_a_valid_access_token(locked, monkeypatch):
    assert locked.get("/login", headers={"host": "access.test"}).status_code == 403
    monkeypatch.setattr(devices, "verify_access", lambda t: {"email": "bru@example.com"} if t == "ok" else None)
    h = {"host": "access.test", "cf-access-jwt-assertion": "ok"}
    assert locked.get("/login", headers=h).status_code == 200
    locked.post("/login", data={"password": "test-pw", "who": "Bru"}, headers=h)
    assert locked.get("/", headers=h).status_code == 200
    conn = db.connect(config.DB_PATH)
    assert conn.execute("SELECT details FROM audit_log WHERE action = 'login'").fetchone()[0] == "via bru@example.com"


def test_register_then_revoke_a_device(locked):
    conn = db.connect(config.DB_PATH)
    link = devices.create_link(conn, "Counter iPad", "Bru")
    conn.commit()
    path = link.split("testserver")[1]
    assert "Counter iPad" in locked.get(path).text               # confirm page; GET doesn't use the link up
    r = locked.post(path, follow_redirects=False)
    assert r.status_code == 303 and devices.DEVICE_COOKIE in r.cookies
    assert locked.post(path).status_code == 410                   # one use only
    r = locked.get("/", follow_redirects=False)
    assert r.headers["location"].startswith("/login")             # straight to the password screen
    login(locked)
    assert "This device: <b>Counter iPad</b>" in locked.get("/settings").text
    device_id = conn.execute("SELECT id FROM devices").fetchone()[0]
    locked.post(f"/settings/devices/{device_id}/revoke")
    r = locked.get("/", follow_redirects=False)
    assert r.headers["location"].startswith("https://access.test/")


def test_expired_link(locked):
    conn = db.connect(config.DB_PATH)
    link = devices.create_link(conn, "Old", "Bru")
    conn.execute("UPDATE device_links SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    assert locked.get(link.split("testserver")[1]).status_code == 410


def test_off_by_default(client):
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")


def test_verify_access_checks_signature_audience_and_issuer(monkeypatch):
    monkeypatch.setattr(config, "CF_ACCESS_TEAM", "team.cloudflareaccess.com")
    monkeypatch.setattr(config, "CF_ACCESS_AUD", "aud123")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(devices, "_jwks", SimpleNamespace(get_signing_key_from_jwt=lambda t: SimpleNamespace(key=key.public_key())))
    claims = {"email": "mike@example.com", "aud": "aud123", "iss": "https://team.cloudflareaccess.com", "exp": time.time() + 60}
    assert devices.verify_access(jwt.encode(claims, key, algorithm="RS256"))["email"] == "mike@example.com"
    assert devices.verify_access(jwt.encode({**claims, "aud": "other"}, key, algorithm="RS256")) is None
    assert devices.verify_access(jwt.encode({**claims, "exp": time.time() - 60}, key, algorithm="RS256")) is None
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert devices.verify_access(jwt.encode(claims, other, algorithm="RS256")) is None   # forged
