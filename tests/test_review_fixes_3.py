"""Fixes from the iOS app review that live on the server."""
import pytest
from fastapi import HTTPException

from fizzy import db, web
from tests.test_app import client, login  # noqa: F401  (fixture)


def test_cents_reads_comma_decimals():
    assert web.cents("12,50") == 1250          # a phone's decimal pad in some regions
    assert web.cents("1,200") == 120000
    assert web.cents("$3.5") == 350
    with pytest.raises(HTTPException):
        web.cents("abc")


def test_profit_refusal_is_a_bad_flash(client):
    login(client)
    r = client.post("/profit", data={"name": "Coke", "key": "coke", "base": "coke", "cost": "abc"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/profit", headers={"X-Fizzy-Format": "json"}).json()
    assert page["flash_kind"] == "bad" and "isn't a dollar amount" in page["flash"]
    client.post("/profit", data={"name": "Coke", "key": "coke", "base": "coke", "cost": "1,20"}, follow_redirects=False)
    page = client.get("/profit", headers={"X-Fizzy-Format": "json"}).json()
    assert page["flash_kind"] == "ok" and "Saved 1 cost" in page["flash"]


def test_edit_with_someone_elses_square_id_is_a_bad_flash(client):
    login(client)
    conn = db.connect()
    conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES ('A One', 'a@x', 'SQA', ?)", (db.now_iso(),))
    conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES ('B Two', 'b@x', 'SQB', ?)", (db.now_iso(),))
    conn.commit()
    bid = conn.execute("SELECT id FROM customers WHERE email = 'b@x'").fetchone()[0]
    conn.close()
    client.post(f"/customers/{bid}/edit", data={"name": "B Two", "square_customer_id": "SQA"}, follow_redirects=False)
    page = client.get(f"/customers/{bid}", headers={"X-Fizzy-Format": "json"}).json()
    assert page["flash_kind"] == "bad" and "already linked" in page["flash"]


def test_deleted_restock_is_not_brought_back_by_a_resend(client):
    login(client)
    data = {"day": "2026-03-02", "shop": "Costco", "item": "Coke", "qty": "24", "cost": "30", "client_id": "phone-abc"}
    assert client.post("/money/restock", data=data, follow_redirects=False).status_code == 303
    conn = db.connect()
    rid = conn.execute("SELECT id FROM restocks").fetchone()[0]
    conn.close()
    assert client.post(f"/money/restock/{rid}/delete", follow_redirects=False).status_code == 303
    client.post("/money/restock", data=data, follow_redirects=False)   # the phone never heard back, so it sends it again
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM restocks").fetchone()[0] == 0
    conn.close()
    page = client.get("/money", headers={"X-Fizzy-Format": "json"}).json()
    assert page["flash_kind"] == "ok" and "deleted" in page["flash"]
