"""Security keys, end to end, with a software key standing in for a real one."""
import hashlib
import json
import os
import re

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from fizzy import db
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_pin import _unlock, owner  # noqa: F401  (fixture)

RP, ORIGIN = "testserver", "http://testserver"


class SoftKey:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cid = os.urandom(16)
        self.count = 0

    def _client_data(self, kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()

    def create(self, options, origin=ORIGIN):
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        auth = (hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([0x41]) + (0).to_bytes(4, "big")
                + bytes(16) + len(self.cid).to_bytes(2, "big") + self.cid + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return json.dumps({"id": bytes_to_base64url(self.cid), "rawId": bytes_to_base64url(self.cid), "type": "public-key",
                           "response": {"clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options["challenge"], origin)),
                                        "attestationObject": bytes_to_base64url(att)}})

    def get(self, options, origin=ORIGIN):
        self.count += 1
        auth = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x01]) + self.count.to_bytes(4, "big")
        cd = self._client_data("webauthn.get", options["challenge"], origin)
        sig = self.key.sign(auth + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return json.dumps({"id": bytes_to_base64url(self.cid), "rawId": bytes_to_base64url(self.cid), "type": "public-key",
                           "response": {"clientDataJSON": bytes_to_base64url(cd), "authenticatorData": bytes_to_base64url(auth),
                                        "signature": bytes_to_base64url(sig), "userHandle": None}})


def _add_key(client, key, name="Stall key"):
    _unlock(client, "owner-pw", 2)
    page = client.post("/settings/passkeys", data={"name": name}).text  # lands back on Settings, ready to add
    opts = json.loads(re.search(r'<div class="notice"[^>]*data-passkey-add>\s*<script type="application/json">(.*?)</script>', page, re.S).group(1))
    return client.post("/settings/passkeys/finish", data={"credential": key.create(opts)})


def _sign_in(client, key, who="Will", origin=ORIGIN):
    opts = client.post("/login/passkey/options").json()
    return client.post("/login/passkey", data={"who": who, "credential": key.get(opts, origin), "next": "/"}, follow_redirects=False)


def test_add_a_key_and_sign_in_with_it(client, owner):
    login(client)
    key = SoftKey()
    assert "Added Stall key" in _add_key(client, key).text
    client.post("/logout")
    r = _sign_in(client, key)
    assert r.status_code == 303 and client.get("/", follow_redirects=False).status_code == 200
    conn = db.connect()
    row = conn.execute("SELECT * FROM passkeys").fetchone()
    assert row["sign_count"] == 1 and row["last_used_at"]
    assert "security key Stall key" in conn.execute("SELECT details FROM audit_log WHERE action = 'login' ORDER BY id DESC").fetchone()[0]
    conn.close()


def test_adding_a_key_needs_the_owner(client, owner):
    login(client)
    conn = db.connect()
    from fizzy import pin
    pin.set_pin(conn, "4821", "t")
    conn.commit()
    conn.close()
    assert "data-passkey-add" not in client.post("/settings/passkeys", data={"name": "Sneaky"}).text
    _unlock(client, "4821", 1)
    assert "data-passkey-add" not in client.post("/settings/passkeys", data={"name": "Sneaky"}).text
    assert "Added" in _add_key(client, SoftKey()).text


def test_bad_answers_dont_get_in(client, owner):
    login(client)
    key = SoftKey()
    _add_key(client, key)
    client.post("/logout")
    assert "added to Fizzy" in _sign_in(client, SoftKey()).text                  # a key nobody added
    t = _sign_in(client, key, origin="https://evil.example").text
    assert "check out" in t, re.search(r"class=\"err\">(.*?)<", t).group(1)
    opts = client.post("/login/passkey/options").json()
    answer = key.get(opts)
    client.post("/login/passkey", data={"who": "Will", "credential": answer})
    client.post("/logout")
    r = client.post("/login/passkey", data={"who": "Will", "credential": answer}, follow_redirects=False)  # replayed
    assert r.status_code == 200 and client.get("/", follow_redirects=False).status_code == 303
    assert "Pick who" in client.post("/login/passkey", data={"who": "Nobody", "credential": answer}).text
    assert client.post("/login/passkey", data={"who": "Will", "credential": "garbage"}).status_code == 200


def test_no_keys_yet_and_removing(client, owner):
    assert "No security keys" in client.post("/login/passkey/options").json()["error"]
    login(client)
    key = SoftKey()
    _add_key(client, key)
    conn = db.connect()
    kid = conn.execute("SELECT id FROM passkeys").fetchone()[0]
    conn.close()
    client.post(f"/settings/passkeys/{kid}/remove")
    client.post("/logout")
    assert "No security keys" in client.post("/login/passkey/options").json()["error"]
