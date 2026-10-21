"""Registered devices and Cloudflare Access.

MAC addresses never leave the local network, so a web app can't see them. Instead:

- The counter device is *registered*: it opens a one-time link and gets a long-lived,
  random device cookie. On the main host (FIZZY_HOST) only registered devices get in.
- Any other device is sent to FIZZY_ACCESS_HOST, which sits behind Cloudflare Access
  (email one-time code). Fizzy also checks Cloudflare's signed token itself, so that host
  can't be reached around Access.

Both then still need the staff password. With FIZZY_ACCESS_HOST unset, none of this applies.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from . import config
from .db import audit, now_iso

DEVICE_COOKIE = "fizzy_device"
DEVICE_COOKIE_DAYS = 400       # browsers cap cookie lifetime at about 400 days; refreshed while in use
LINK_MINUTES = 15
_jwks = None


def enabled():
    return bool(config.ACCESS_HOST)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_link(conn, name, staff):
    """One-time registration link for a device. Valid for LINK_MINUTES, usable once."""
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Give the device a name, e.g. 'Counter iPad'.")
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=LINK_MINUTES)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO device_links(token_hash, name, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                 (_hash(token), name, staff, now_iso(), expires))
    audit(conn, staff, "device_link", None, name)
    return f"https://{config.HOST}/enrol/{token}"


def pending_link(conn, token):
    row = conn.execute("SELECT * FROM device_links WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or row["used_at"] or row["expires_at"] < now_iso():
        return None
    return row


def redeem(conn, token):
    """Use a registration link: returns the new device's secret cookie value, or None."""
    link = pending_link(conn, token)
    if link is None:
        return None
    device_token = secrets.token_urlsafe(32)
    conn.execute("UPDATE device_links SET used_at = ? WHERE token_hash = ?", (now_iso(), link["token_hash"]))
    cur = conn.execute("INSERT INTO devices(name, token_hash, created_by, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                       (link["name"], _hash(device_token), link["created_by"], now_iso(), now_iso()))
    audit(conn, link["created_by"], "device_registered", None, f"#{cur.lastrowid} {link['name']}")
    return device_token


def check(conn, device_token):
    """The device row for a cookie value, or None. Returns (row, refreshed) where refreshed
    means last_seen was updated and the cookie should be re-sent to extend it."""
    if not device_token:
        return None, False
    row = conn.execute("SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL", (_hash(device_token),)).fetchone()
    if row is None:
        return None, False
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    if (row["last_seen_at"] or "") < stale:
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (now_iso(), row["id"]))
        conn.commit()
        return row, True
    return row, False


def revoke(conn, device_id, staff):
    row = conn.execute("SELECT name FROM devices WHERE id = ?", (device_id,)).fetchone()
    conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now_iso(), device_id))
    audit(conn, staff, "device_revoked", None, f"#{device_id} {row['name'] if row else ''}")


def verify_access(token):
    """Validate Cloudflare Access's signed token (Cf-Access-Jwt-Assertion). Returns its claims
    (including the signed-in email) or None."""
    global _jwks
    if not token or not config.CF_ACCESS_TEAM or not config.CF_ACCESS_AUD:
        return None
    import jwt  # only needed when Access is in use
    try:
        if _jwks is None:
            _jwks = jwt.PyJWKClient(f"https://{config.CF_ACCESS_TEAM}/cdn-cgi/access/certs", cache_keys=True)
        key = _jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=config.CF_ACCESS_AUD,
                          issuer=f"https://{config.CF_ACCESS_TEAM}")
    except Exception:
        return None
