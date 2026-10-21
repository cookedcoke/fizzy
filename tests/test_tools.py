from fizzy import db, logic, tools
from tests.test_app import client, login  # noqa: F401  (fixture)


def setup_people(client):
    login(client)
    csv = (b"Cleaned Name,Email\nAdeline Yates,rns1@reddamnorthshore.nsw.edu.au\n"
           b"Adam Fenn,rns2@reddamnorthshore.nsw.edu.au\nNora Zhang,rns3@reddamnorthshore.nsw.edu.au\n")
    client.post("/import", files={"file": ("d.csv", csv, "text/csv")}, data={"confirm": "1"})


def conn_():
    from fizzy import config
    return db.connect(config.DB_PATH)


def flash(client):
    r = client.get("/tools")
    return r.text.split('<div class="flash">')[1].split("</div>")[0] if 'class="flash"' in r.text else ""


def post(client, tool, **data):
    return client.post(f"/tools/{tool}", data=data, follow_redirects=False)


def test_every_tool_listed_and_page_renders(client):
    setup_people(client)
    page = client.get("/tools").text
    for _, items in tools.TOOLS:
        for tid, label, tip in items:
            assert f'id="{tid}"' in page and label in page


def test_points_tools(client):
    setup_people(client)
    post(client, "bulk_points", delta="50", note="Halloween", people="Adeline Yates\nrns2\nNobody Here")
    msg = flash(client)
    assert "2 customer(s)" in msg and "Nobody Here" in msg
    post(client, "transfer_points", **{"from": "Adeline Yates <rns1@reddamnorthshore.nsw.edu.au>", "to": "Chloe", "points": "20"})
    c = conn_()
    assert [logic.points_balance(c, i) for i in (1, 2, 3)] == [30, 50, 20]
    post(client, "reset_points", confirm="nope")
    assert "Type RESET" in flash(client)
    post(client, "reset_points", confirm="reset")
    assert [logic.points_balance(c, i) for i in (1, 2, 3)] == [0, 0, 0]


def test_tab_tools(client):
    setup_people(client)
    c = conn_()
    post(client, "manual_charge", customer="Adeline Yates", amount="4.50", items="Coke")
    assert logic.tab_ledger(c, 1)["owing_cents"] == 450
    post(client, "write_off", customer="Adeline Yates", amount="1.50", note="spilt drink")
    assert logic.tab_ledger(c, 1)["owing_cents"] == 300
    assert logic.points_balance(c, 1) == 0  # write-offs earn nothing
    before = c.execute("SELECT due_at FROM tab_charges").fetchone()[0]
    post(client, "extend_due", customer="Adeline Yates", days="3")
    assert c.execute("SELECT due_at FROM tab_charges").fetchone()[0] > before
    post(client, "pause_tabs", paused="1")
    logic.set_tab_decision(c, 2, "approved", "", "t"); c.commit()
    row = c.execute("SELECT * FROM customers WHERE id = 2").fetchone()
    assert logic.tab_status(c, row)["status"] == "paused"
    assert "All tabs are paused" in client.get("/").text
    post(client, "revoke_all", confirm="REVOKE")
    assert c.execute("SELECT tab_decision FROM customers WHERE id = 2").fetchone()[0] == "revoked"


def test_ambiguous_name_is_rejected(client):
    setup_people(client)
    post(client, "manual_charge", customer="Ad", amount="1")
    assert "matches several people" in flash(client)


def test_customer_tools(client):
    setup_people(client)
    r = post(client, "add_customer", name="Ms Teacher", email="teacher@example.com")
    assert r.headers["location"] == "/customers/4"
    post(client, "bulk_points", delta="10", note="x", people="teacher@example.com")
    post(client, "merge", keep="Nora Zhang", dup="teacher@example.com")
    c = conn_()
    assert logic.points_balance(c, 3) == 10
    assert c.execute("SELECT archived_at FROM customers WHERE id = 4").fetchone()[0]
    post(client, "bulk_archive", people="rns1")
    assert client.get("/api/search?q=adeline").json() == []
    post(client, "restore", all="1")
    assert client.get("/api/search?q=adeline").json()[0]["name"] == "Adeline Yates"


def test_reports_and_misc(client):
    setup_people(client)
    post(client, "manual_charge", customer="Nora Zhang", amount="2")
    post(client, "bulk_points", delta="5", note="x", everyone="1")
    for tool, data in [("takings", {"start": "2020-01-01", "end": "2030-01-01"}), ("chase", {}), ("balances", {}),
                       ("leaderboard", {"n": "5"}), ("integrity", {}),
                       ("raffle", {"pool": "points", "n": "2", "start": "2020-01-01", "end": "2030-01-01", "weighted": "1"})]:
        r = client.post(f"/tools/{tool}", data=data)
        assert r.status_code == 200 and "<table>" in r.text, tool
    chase = client.post("/tools/chase").text
    assert "Nora Zhang" in chase and "rns3@" in chase
    assert client.get("/tools/export/points.csv").text.startswith("id,customer_id")
    assert client.get("/tools/backup.db").content[:15] == b"SQLite format 3"
    post(client, "notice", notice="Restock Coke Zero")
    assert "Restock Coke Zero" in client.get("/").text
    post(client, "sync_now")
    assert "configured (SQUARE_ACCESS_TOKEN)" in flash(client)


def test_sign_out_everyone(client):
    setup_people(client)
    post(client, "sign_out_all", confirm="SIGN OUT")
    assert client.get("/", follow_redirects=False).status_code == 303
    login(client)
    assert client.get("/").status_code == 200
