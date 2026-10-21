import pytest

from fizzy import config, db, portal
from tests.test_app import client, login  # noqa: F401  (fixture)

PC = "fizz-4417"


@pytest.fixture(autouse=True)
def _reset_limits():
    portal._fails.clear()
    yield
    portal._fails.clear()


def setup_student(client, name="Nora Le Fevre", sid="rns5", points=1500):
    login(client)
    client.post("/tools/add_customer", data={"name": name, "email": f"{sid}@reddamnorthshore.nsw.edu.au"})
    cid = client.get("/api/index").json()[-1]["id"]
    if points:
        client.post(f"/customers/{cid}/points", data={"delta": str(points), "note": "staff-only note xyz"})
    r = client.post(f"/customers/{cid}/passcode", data={"passcode": PC})
    assert "Passcode set" in r.text
    return cid


def me_login(client, who, pc=PC):
    return client.post("/me/login", data={"who": who, "passcode": pc})


def test_passcode_rules(conn, cust):
    c = cust(name="Nora Le Fevre", email="rns5@reddamnorthshore.nsw.edu.au")
    assert "at least 4" in portal.passcode_problem("123")
    assert "at most" in portal.passcode_problem("x" * 65)
    assert portal.passcode_problem("1234") is None                    # relaxed on purpose: staff type it in
    portal.set_passcode(conn, c, "  1234 ", "Will")                    # stray spaces ignored
    stored = conn.execute("SELECT passcode_enc FROM customers").fetchone()[0]
    assert "1234" not in stored and portal.reveal_passcode(stored) == "1234"   # encrypted, but staff can read it
    assert portal.check_passcode("1234", stored) and not portal.check_passcode("1235", stored)
    assert "1234" not in str([tuple(r) for r in conn.execute("SELECT * FROM audit_log")])


def test_student_signs_in_and_sees_only_their_own_stats(client):
    setup_student(client)
    client.post("/rewards", data={"name": "Free Coke", "points": "1000"})
    client.post("/logout")
    page = client.get("/me").text
    assert "See my stats" in page and "Free Coke" in page            # live rewards are public
    for who in ("nora le fevre", "  Nora  Le Fevre ", "RNS5"):
        r = me_login(client, who)
        assert "Hi Nora" in r.text and "1,500" in r.text and "You can have this now" in r.text, who
        assert "staff-only note xyz" not in r.text                     # staff notes stay private
        client.post("/me/logout")
    assert "See my stats" in client.get("/me").text
    # staff pages still need the staff password
    assert client.get("/", follow_redirects=False).status_code == 303


def test_wrong_or_fuzzy_names_get_the_same_answer(client):
    setup_student(client)
    client.post("/logout")
    for who, pc in [("Nora Le Fevre", "wrong-one"), ("Nora Le Feve", PC), ("Nobody Here", PC)]:
        r = me_login(client, who, pc)
        assert "name and passcode don" in r.text and "Hi Nora" not in r.text


def test_too_many_wrong_tries_locks_that_name(client):
    setup_student(client)
    client.post("/logout")
    portal.FAIL_DELAY, delay = 0, portal.FAIL_DELAY
    try:
        for _ in range(portal.LIMIT_PER_NAME):
            me_login(client, "Nora Le Fevre", "wrong-one")
        assert "Too many tries" in me_login(client, "Nora Le Fevre").text
    finally:
        portal.FAIL_DELAY = delay


def test_new_passcode_signs_out_old_sessions_and_remove_blocks(client):
    cid = setup_student(client)
    assert "Hi Nora" in me_login(client, "Nora Le Fevre").text
    client.post(f"/customers/{cid}/passcode", data={"passcode": "new-code-99"})
    assert "See my stats" in client.get("/me").text                  # old session gone
    assert "Hi Nora" in me_login(client, "Nora Le Fevre", "new-code-99").text
    client.post(f"/customers/{cid}/passcode", data={"remove": "1"})
    assert "See my stats" in client.get("/me").text
    assert "name and passcode don" in me_login(client, "Nora Le Fevre", "new-code-99").text


def test_archived_customer_cant_sign_in(client):
    cid = setup_student(client)
    client.post(f"/customers/{cid}/edit", data={"name": "Nora Le Fevre", "square_customer_id": "", "archived": "1"})
    assert "name and passcode don" in me_login(client, "Nora Le Fevre").text


def test_public_host_serves_only_student_pages(client, monkeypatch):
    setup_student(client)
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.example")
    pub = {"host": "my.example"}
    assert "See my stats" in client.get("/", headers=pub).text
    for path in ("/customers", "/tools", "/audit", "/api/index", "/settings"):
        assert client.get(path, headers=pub, follow_redirects=False).status_code == 404, path
    assert client.post("/customers/1/passcode", headers=pub, data={"remove": "1"}).status_code == 404
    assert client.get("/robots.txt", headers=pub).text.startswith("User-agent: *")
    assert client.get("/me").status_code == 404                       # not on the staff host
    r = client.post("/login", headers=pub, data={"who": "Nora Le Fevre", "passcode": PC}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    page = client.get("/", headers=pub)
    assert "Hi Nora" in page.text and page.headers["cache-control"] == "no-store"
    assert 'action="/logout"' in page.text


def test_public_host_skips_the_device_lock(client, monkeypatch):
    monkeypatch.setattr(config, "ACCESS_HOST", "staff.example")
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.example")
    assert "See my stats" in client.get("/", headers={"host": "my.example"}).text
    assert client.get("/", follow_redirects=False).status_code == 303   # staff host still locked


def test_leaderboard(conn, cust):
    a = cust(name="Nora Le Fevre", email="rns1@x", sq="A")
    b = cust(name="Hugo Williams", email="rns2@x", sq="B")
    c = cust(name="Cher", email="rns3@x", sq="C")
    d = cust(name="Not Signedup", email="rns4@x", sq="D")
    conn.execute("UPDATE customers SET passcode_enc = 'x' WHERE id != ?", (d["id"],))
    ts = db.now_iso()
    for cid, pts, reason in [(a["id"], 500, "purchase"), (b["id"], 900, "purchase"), (b["id"], -800, "redeem"),
                             (c["id"], 500, "purchase"), (d["id"], 9999, "purchase")]:
        conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, created_at) VALUES (?, ?, ?, ?)",
                     (cid, pts, reason, ts))
    board, mine = portal.leaderboard(conn, me=a["id"], limit=2)
    assert [(r["rank"], r["name"], r["points"]) for r in board] == [(1, "Hugo W.", 900), (2, "Nora F.", 500)]
    assert mine == {"rank": 2, "points": 500, "of": 3}
    _, mine_c = portal.leaderboard(conn, me=c["id"], limit=2)
    assert mine_c["rank"] == 2                                          # tie shares the rank
    assert portal.leaderboard(conn, since="2999-01-01")[0] == []


def test_merge_carries_the_passcode(client):
    setup_student(client, name="Kim Dup", sid="rns8", points=0)
    client.post("/tools/add_customer", data={"name": "Kim Keep", "email": "rns7@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/merge", data={"keep": "Kim Keep", "dup": "Kim Dup"})
    assert "Hi Kim" in me_login(client, "Kim Keep").text
    client.post("/me/logout")
    assert "name and passcode don" in me_login(client, "Kim Dup").text


def test_parallel_guesses_cant_skip_the_limits(client, monkeypatch):
    import asyncio

    import httpx
    setup_student(client)
    monkeypatch.setattr(portal, "FAIL_DELAY", 0)

    async def burst():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            reqs = [ac.post("/me/login", data={"who": "Nora Le Fevre", "passcode": f"wrong-{i}"}) for i in range(30)]
            reqs.append(ac.post("/me/login", data={"who": "Nora Le Fevre", "passcode": PC}))
            return await asyncio.gather(*reqs)
    rs = asyncio.run(burst())
    tried = sum("name and passcode don" in r.text for r in rs)
    assert tried <= portal.LIMIT_PER_NAME                               # the rest were refused before checking
    assert rs[-1].status_code != 303                                    # the right one, late in the burst, didn't get in


def test_name_and_student_number_share_one_allowance(client, monkeypatch):
    setup_student(client)
    client.post("/logout")
    monkeypatch.setattr(portal, "FAIL_DELAY", 0)
    for i in range(portal.LIMIT_PER_NAME):
        me_login(client, "Nora Le Fevre" if i % 2 else "rns5", "wrong-one")
    assert "Too many tries" in me_login(client, "RNS5").text


def test_right_passcode_doesnt_use_up_the_allowance(client, monkeypatch):
    setup_student(client)
    monkeypatch.setattr(portal, "FAIL_DELAY", 0)
    for _ in range(portal.LIMIT_PER_NAME + 2):
        assert "Hi Nora" in me_login(client, "Nora Le Fevre").text
        client.post("/me/logout")


def test_trailing_dot_host_is_still_the_public_host(client, monkeypatch):
    login(client)
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.example")
    assert client.get("/api/index", headers={"host": "my.example."}).status_code == 404


def test_staff_can_look_up_a_forgotten_passcode(client):
    cid = setup_student(client)
    page = client.get(f"/customers/{cid}").text
    assert f'<span class="mono">{PC}</span>' in page and 'class="secret"' in page


def test_new_secret_key_makes_old_passcodes_unreadable_not_broken(client, monkeypatch):
    cid = setup_student(client)
    monkeypatch.setattr(config, "SECRET_KEY", "a-different-key")
    login(client)
    assert "set a new one" in client.get(f"/customers/{cid}").text
    assert "name and passcode don" in me_login(client, "Nora Le Fevre").text
