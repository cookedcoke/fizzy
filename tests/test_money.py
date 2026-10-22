"""Restocks, receipts, payouts and the money picture (fizzy/money.py)."""
import os

import pytest

from fizzy import config, db, money, stats
from tests.test_app import client, login  # noqa: F401  (fixture)


def test_restock_sets_item_costs_and_totals(conn):
    rid = money.add_restock(conn, stats.today(), " Costco ", "", [("Coke", 24, 1800), ("Chips", 10, 1200), ("", 1, 0)], "Will")
    r = conn.execute("SELECT * FROM restocks WHERE id = ?", (rid,)).fetchone()
    assert (r["shop"], r["total_cents"]) == ("Costco", 3000)
    assert stats.costs(conn) == {"coke": 75, "chips": 120}
    with pytest.raises(money.MoneyError, match="at least one item"):
        money.add_restock(conn, stats.today(), "x", "", [], "Will")
    with pytest.raises(money.MoneyError, match="at least 1"):
        money.add_restock(conn, stats.today(), "x", "", [("Coke", 0, 100)], "Will")


def test_payout_rules(conn):
    with pytest.raises(money.MoneyError, match="who was paid"):
        money.add_payout(conn, stats.today(), "Nobody", 100, "", "Will")
    with pytest.raises(money.MoneyError, match="more than"):
        money.add_payout(conn, stats.today(), "Will", 0, "", "Will")


def test_money_pages(client, tmp_path, monkeypatch):
    login(client)
    r = client.post("/money/restock", data={"day": stats.today().isoformat(), "shop": "Costco",
                                             "item": ["Coke", "Chips"], "qty": ["24", "10"], "cost": ["$18", "12.00"]},
                    files={"receipt": ("r.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")})
    assert "Restock saved" in r.text and "24× Coke" in r.text and "Receipt" in r.text
    name = os.listdir(money.receipts_dir())[0]
    assert client.get(f"/money/receipts/{name}").content.startswith(b"\xff\xd8")
    assert client.get("/money/receipts/..%2Fapp.db").status_code == 404
    bad = client.post("/money/restock", data={"item": ["Coke"], "qty": ["1"], "cost": ["1"]},
                      files={"receipt": ("x.exe", b"MZ", "application/octet-stream")})
    assert "must be a photo" in bad.text
    r = client.post("/money/payout", data={"partner": ["Will", "Mike", "Frankie"], "amount": ["10", "", "9.50"],
                                           "day": stats.today().isoformat()})
    assert "Recorded 2 payouts" in r.text and "$19.50" in r.text
    client.post("/money/restock/1/delete")
    assert os.listdir(money.receipts_dir()) == []                           # receipt goes with it
    assert "Money" in client.get("/").text


def test_backup_copies_receipts(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "fizzy.db"))
    name = money.save_receipt(b"\xff\xd8 photo", "image/jpeg")
    db.backup(conn, str(tmp_path / "backups"))
    assert os.listdir(tmp_path / "backups" / "receipts") == [name]
