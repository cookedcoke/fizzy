from fizzy import db, square
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import iso, order, pay


def test_transactions_show_customers_items_and_filters(client):
    login(client)
    conn = db.connect()
    try:
        cid = conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) "
                           "VALUES ('Mia Patel', 'm@x', 'SQ1', ?)", (db.now_iso(),)).lastrowid
        square.process_payment(conn, pay("p1", 300, cust="SQ1", ts=iso()), order(("Coke", 300), oid="o1"))
        square.process_payment(conn, pay("p2", 250, cust=None, ts=iso()), order(("Chips", 250), oid="o2"))
        conn.commit()
    finally:
        conn.close()
    page = client.get("/transactions").text
    assert "Mia Patel" in page and "1× Coke" in page and "1× Chips" in page and "+300" in page
    assert 'action="/square/payments/p2/assign"' in page                      # the one with nobody on it
    nobody = client.get("/transactions?show=nobody").text
    assert "Chips" in nobody and f'/customers/{cid}">Mia Patel' not in nobody
    mia = client.get("/transactions?q=mia").text
    assert f'/customers/{cid}">Mia Patel' in mia and "Chips" not in mia
    assert client.get("/transactions?start=junk&end=nope").status_code == 200
