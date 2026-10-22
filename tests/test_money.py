"""Restocks, receipts, payouts and the money picture (fizzy/money.py)."""
import os

import pytest

from fizzy import config, db, logic, money, square, stats
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import iso, order, pay


def test_restock_sets_item_costs_and_totals(conn):
    rid = money.add_restock(conn, stats.today(), " Costco ", "cash", "", [("Coke", 24, 1800), ("Chips", 10, 1200), ("", 1, 0)], "Will")
    r = conn.execute("SELECT * FROM restocks WHERE id = ?", (rid,)).fetchone()
    assert (r["shop"], r["paid_from"], r["total_cents"]) == ("Costco", "cash", 3000)
    assert stats.costs(conn) == {"coke": 75, "chips": 120}
    with pytest.raises(money.MoneyError, match="at least one item"):
        money.add_restock(conn, stats.today(), "x", "cash", "", [], "Will")
    with pytest.raises(money.MoneyError, match="at least 1"):
        money.add_restock(conn, stats.today(), "x", "cash", "", [("Coke", 0, 100)], "Will")


def test_money_picture_by_where_it_is(conn, cust):
    c = cust()
    square.process_payment(conn, pay("p1", 1000, source="CARD", order_id="o1"), order(("Coke", 1000), oid="o1"))
    square.process_payment(conn, pay("p2", 500, source="CASH", order_id="o2"), order(("Coke", 500), oid="o2"))
    square.process_refund(conn, {"id": "r1", "payment_id": "p1", "status": "COMPLETED", "amount_money": {"amount": 200}, "created_at": iso()})
    logic.set_tab_decision(conn, c["id"], "approved", "", "Will")
    logic.add_repayment(conn, c["id"], 300, "cash", "", "Will")             # by hand: counts as cash in
    money.add_restock(conn, stats.today(), "Costco", "bank", "", [("Coke", 10, 400)], "Will")
    money.add_payout(conn, stats.today(), "Mike", 100, "cash", "", "Will")
    p = money.picture(conn, stats.today(), stats.today())
    cash, bank = p["by"]["cash"], p["by"]["bank"]
    assert (cash["in"], cash["payouts"], cash["net"]) == (800, 100, 700)
    fee = round(800 * 1.6 / 100)
    assert (bank["in"], bank["refunds"], bank["fees"], bank["restocks"]) == (1000, 200, fee, 400)
    assert bank["net"] == 1000 - 200 - fee - 400
    assert p["paid"]["Mike"] == 100 and p["profit_before_payouts"] == p["total"]["net"] + 100
    assert "other" not in p["by"]                                          # hidden when unused


def test_payout_rules(conn):
    with pytest.raises(money.MoneyError, match="who was paid"):
        money.add_payout(conn, stats.today(), "Nobody", 100, "cash", "", "Will")
    with pytest.raises(money.MoneyError, match="more than"):
        money.add_payout(conn, stats.today(), "Will", 0, "cash", "", "Will")


def test_money_pages(client, tmp_path, monkeypatch):
    login(client)
    r = client.post("/money/restock", data={"day": stats.today().isoformat(), "shop": "Costco", "paid_from": "cash",
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
                                           "paid_from": "cash", "day": stats.today().isoformat()})
    assert "Recorded 2 payouts" in r.text and "Frankie</b> $9.50" in r.text
    assert "Card fee set to 1.75%" in client.post("/money/fee", data={"fee_pct": "1.75%"}).text
    assert "between 0 and 10" in client.post("/money/fee", data={"fee_pct": "50"}).text
    client.post("/money/restock/1/delete")
    assert os.listdir(money.receipts_dir()) == []                           # receipt goes with it
    assert "Money" in client.get("/").text


def test_backup_copies_receipts(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "fizzy.db"))
    name = money.save_receipt(b"\xff\xd8 photo", "image/jpeg")
    db.backup(conn, str(tmp_path / "backups"))
    assert os.listdir(tmp_path / "backups" / "receipts") == [name]
