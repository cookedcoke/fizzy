"""Security keys (WebAuthn passkeys) as another way to sign in. One key can be shared by the whole
team: it stands in for the staff password, and whoever's on the counter is still picked from the
list. It never unlocks the PIN or owner passcode.

A key belongs to the host it was added on (fizzy.cookedcoke.co and the Cloudflare Access host each
need their own), because that's how browsers scope them."""
import json
import time

from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from .db import audit, now_iso

CHALLENGE_SECONDS = 300
MAX_KEYS = 20


class PasskeyError(Exception):
    pass


def where(request):
    """(rp_id, origin) for this request. Browsers only allow these on https or localhost."""
    host = request.headers.get("host") or request.url.netloc
    return host.split(":")[0].lower(), f"{request.url.scheme}://{host}"


def listed(conn):
    return conn.execute("SELECT * FROM passkeys ORDER BY created_at").fetchall()


def _take(session, key):
    """The challenge saved for this step, used once, if it hasn't expired."""
    saved = session.pop(key, None)
    if not saved or saved.get("t", 0) < time.time() - CHALLENGE_SECONDS:
        raise PasskeyError("That took too long. Try again.")
    return saved


def register_options(conn, request, name):
    name = " ".join((name or "").split())[:40]
    if not name:
        raise PasskeyError("Give the key a name first, e.g. Stall key.")
    if len(listed(conn)) >= MAX_KEYS:
        raise PasskeyError(f"There are already {MAX_KEYS} keys. Remove one first.")
    rp_id, _ = where(request)
    opts = generate_registration_options(
        rp_id=rp_id, rp_name="Fizzy", user_id=b"fizzy-staff", user_name="Fizzy staff", user_display_name="Fizzy staff",
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["credential_id"]))
                             for r in listed(conn) if r["rp_id"] == rp_id],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.DISCOURAGED,
                                                               user_verification=UserVerificationRequirement.PREFERRED))
    request.session["pk_add"] = {"c": bytes_to_base64url(opts.challenge), "name": name, "t": time.time()}
    return options_to_json(opts)


def register(conn, request, credential, staff):
    saved = _take(request.session, "pk_add")
    rp_id, origin = where(request)
    try:
        v = verify_registration_response(credential=credential, expected_challenge=base64url_to_bytes(saved["c"]),
                                         expected_rp_id=rp_id, expected_origin=origin)
    except Exception as e:  # the library raises several kinds; any of them means "not accepted"
        raise PasskeyError("The key's answer didn't check out. Try again.") from e
    cid = bytes_to_base64url(v.credential_id)
    if conn.execute("SELECT 1 FROM passkeys WHERE credential_id = ?", (cid,)).fetchone():
        raise PasskeyError("That key is already added.")
    conn.execute("INSERT INTO passkeys(name, credential_id, public_key, sign_count, rp_id, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (saved["name"], cid, v.credential_public_key, v.sign_count, rp_id, staff, now_iso()))
    audit(conn, staff, "passkey_add", None, saved["name"])
    return saved["name"]


def remove(conn, key_id, staff):
    row = conn.execute("SELECT name FROM passkeys WHERE id = ?", (key_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM passkeys WHERE id = ?", (key_id,))
        audit(conn, staff, "passkey_remove", None, row["name"])


def login_options(conn, request):
    rp_id, _ = where(request)
    keys = [r for r in listed(conn) if r["rp_id"] == rp_id]
    if not keys:
        raise PasskeyError("No security keys have been added for this address yet. Sign in with the password.")
    opts = generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["credential_id"])) for r in keys])
    request.session["pk_login"] = {"c": bytes_to_base64url(opts.challenge), "t": time.time()}
    return options_to_json(opts)


def check_login(conn, request, credential):
    """The key's name if `credential` is a good answer from a key added here, else PasskeyError."""
    saved = _take(request.session, "pk_login")
    rp_id, origin = where(request)
    cred = json.loads(credential) if isinstance(credential, str) else credential
    row = conn.execute("SELECT * FROM passkeys WHERE credential_id = ? AND rp_id = ?", (str(cred.get("id", "")), rp_id)).fetchone()
    if row is None:
        raise PasskeyError("That key hasn't been added to Fizzy.")
    try:
        v = verify_authentication_response(credential=cred, expected_challenge=base64url_to_bytes(saved["c"]),
                                           expected_rp_id=rp_id, expected_origin=origin,
                                           credential_public_key=row["public_key"],
                                           credential_current_sign_count=row["sign_count"])
    except Exception as e:
        raise PasskeyError("The key's answer didn't check out. Try again.") from e
    conn.execute("UPDATE passkeys SET sign_count = ?, last_used_at = ? WHERE id = ?", (v.new_sign_count, now_iso(), row["id"]))
    return row["name"]
