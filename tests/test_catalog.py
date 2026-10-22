"""Square's item list (square.sync_catalog) and matching items by Square's ids."""
import httpx

from fizzy import config, db, money, square, stats
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_rules import order, pay


def item(iid, name, *variations):
    """A catalogue ITEM with (variation_id, variation_name, price) variations."""
    return {"type": "ITEM", "id": iid, "item_data": {"name": name, "variations": [
        {"type": "ITEM_VARIATION", "id": vid, "item_variation_data": {"item_id": iid, "name": vn, "price_money": {"amount": p}}}
        for vid, vn, p in variations]}}


class Cat:
    def __init__(self, items=(), fail=None):
        self.items, self.fail = list(items), fail

    def catalog_items(self):
        if self.fail:
            raise httpx.HTTPStatusError("no", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.fail))
        return iter(self.items)


COKE = item("I1", "Coca-Cola", ("V1", "Can", 350), ("V2", "Mini", 250))
SPRITE = item("I2", "Sprite", ("V3", "Regular", 300))


def line(name, vid, cents, variation=""):
    li = {"name": name, "quantity": "1", "total_money": {"amount": cents}, "catalog_object_id": vid}
    if variation:
        li["variation_name"] = variation
    return li


def test_catalog_sync_names_and_prices(conn):
    assert square.sync_catalog(conn, Cat([COKE, SPRITE])) == 3
    rows = {r["variation_id"]: (r["display"], r["price_cents"]) for r in conn.execute("SELECT * FROM catalog_items")}
    assert rows == {"V1": ("Coca-Cola (Can)", 350), "V2": ("Coca-Cola (Mini)", 250), "V3": ("Sprite", 300)}
    assert square.sync_catalog(conn, Cat([SPRITE])) is None                 # not due again for 6 hours
    square.sync_catalog(conn, Cat([SPRITE]), force=True)                    # Coca-Cola deleted in Square
    assert conn.execute("SELECT COUNT(*) FROM catalog_items WHERE deleted = 0").fetchone()[0] == 1


def test_variations_cost_separately_and_renames_keep_costs(conn, cust):
    cust()
    square.sync_catalog(conn, Cat([COKE, SPRITE]))
    o = order(oid="o1")
    o["line_items"] = [line("Coca-Cola", "V1", 350, "Can"), line("Coca-Cola", "V2", 250, "Mini")]
    square.process_payment(conn, pay("p1", 600, order_id="o1"), o)
    stats.set_cost(conn, "Coca-Cola (Can)", 75, "Will", key="v:V1")
    stats.set_cost(conn, "Coca-Cola (Mini)", 55, "Will", key="v:V2")
    p = stats.profit(stats.summary(conn, stats.today())["checkouts"], stats.costs(conn))
    assert {r["name"]: r["profit"] for r in p["items"]} == {"Coca-Cola (Can)": 275, "Coca-Cola (Mini)": 195}
    renamed = item("I1", "Coke", ("V1", "Can", 350), ("V2", "Mini", 250))  # renamed in Square
    square.sync_catalog(conn, Cat([renamed, SPRITE]), force=True)
    p = stats.profit(stats.summary(conn, stats.today())["checkouts"], stats.costs(conn))
    assert {r["name"]: r["profit"] for r in p["items"]} == {"Coke (Can)": 275, "Coke (Mini)": 195}


def test_name_costs_apply_to_variations_and_restocks_update_them(conn):
    stats.set_cost(conn, "Coca-Cola", 70, "Will")
    square.sync_catalog(conn, Cat([COKE]))
    costs = stats.costs(conn)
    assert stats.cost_of(costs, "v:V1", "coca-cola") == 70 and "v:V1" not in costs     # falls back, no copies
    money.add_restock(conn, stats.today(), "", "", [("Coca-Cola", 10, 1000)], "Will")  # by the plain name
    assert stats.cost_of(stats.costs(conn), "v:V2", "coca-cola") == 100               # profit follows the restock


def test_blanking_an_inherited_cost_removes_it(client):
    login(client)
    conn = db.connect()
    try:
        stats.set_cost(conn, "Coca-Cola", 70, "Will")
        square.sync_catalog(conn, Cat([COKE]))
        conn.commit()
    finally:
        conn.close()
    r = client.post("/profit", data={"name": ["Coca-Cola (Can)", "Coca-Cola (Mini)"], "key": ["v:V1", "v:V2"],
                                     "base": ["coca-cola", "coca-cola"], "cost": ["", ""]})
    assert "Saved 1 cost" in r.text
    conn = db.connect()
    try:
        assert stats.costs(conn) == {}
    finally:
        conn.close()


def test_lines_without_a_catalogue_id_still_match_by_name(conn, cust):
    cust()
    square.process_payment(conn, pay("p1", 400, order_id="o1"), order(("Custom amount", 400), oid="o1"))
    stats.set_cost(conn, "custom amount", 100, "Will")
    p = stats.profit(stats.summary(conn, stats.today())["checkouts"], stats.costs(conn))
    assert p["profit"] == 300


def test_restocks_pick_square_items(conn):
    square.sync_catalog(conn, Cat([COKE, SPRITE]))
    money.add_restock(conn, stats.today(), "Costco", "", [("coca-cola (mini)", 30, 1650), ("SPRITE", 24, 1800), ("Cookies", 10, 500)], "Will")
    keys = {r["name"]: r["item_key"] for r in conn.execute("SELECT * FROM restock_items")}
    assert keys == {"Coca-Cola (Mini)": "v:V2", "Sprite": "v:V3", "Cookies": "cookies"}
    assert stats.costs(conn)["v:V2"] == 55 and stats.costs(conn)["v:V3"] == 75


def test_no_permission_is_reported_not_fatal(conn):
    assert square.sync_catalog(conn, Cat(fail=403)) == 0
    assert "ITEMS_READ" in db.get_setting(conn, "catalog_error")


def test_profit_page_lists_unsold_catalogue_items(client, monkeypatch):
    login(client)
    conn = db.connect()
    try:
        square.sync_catalog(conn, Cat([COKE, SPRITE]))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(config, "SQUARE_TOKEN", "x")
    page = client.get("/profit").text
    assert "Coca-Cola (Mini)" in page and "sells for $2.50" in page and "3 items from Square" in page
    r = client.post("/profit", data={"name": ["Coca-Cola (Mini)"], "key": ["v:V2"], "base": ["coca-cola"], "cost": ["0.55"]})
    assert "Saved 1 cost" in r.text and "78%" in r.text                    # (2.50 - 0.55) / 2.50
