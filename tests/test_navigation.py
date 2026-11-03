"""Where things live: every action on the page of the thing it changes (README, "Where things live"), the
header's six tabs and its More menu, and Tools as an index of everything."""
import re

from fizzy import stats, tools
from tests.test_app import client, login  # noqa: F401  (fixture)

CSV = b"Cleaned Name,Email\nAdeline Yates,rns00000001@reddamnorthshore.nsw.edu.au\nAdam Fenn,rns00000002@reddamnorthshore.nsw.edu.au\n"
JSON = {"X-Fizzy-Format": "json"}


def _people(client):
    login(client)
    client.post("/import", files={"file": ("d.csv", CSV, "text/csv")}, data={"confirm": "1"})
    return client.get("/api/search?q=adeline").json()[0]["id"]


def _header(page):
    return page.split("<header>", 1)[1].split("</header>", 1)[0]


def _flash(page):
    m = re.search(r'<div class="flash[^"]*" role="status">(.*?)</div>', page, re.S)
    return m.group(1) if m else ""


def test_header_has_the_six_tabs_and_the_more_menu(client):
    login(client)
    h = _header(client.get("/").text)
    nav = h.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', nav) == [
        ("/", "Dashboard"), ("/customers", "Customers"), ("/transactions", "Sales"), ("/tabs", "Tabs"), ("/rewards", "Rewards"), ("/money", "Money")]
    menu = h.split('<div class="nav-menu">', 1)[1].split("</div>", 1)[0]
    assert re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', menu) == [("/settings", "Settings"), ("/audit", "Log"), ("/tools", "Tools")]
    assert "<summary>More</summary>" in h
    for gone in ("/ask", "/import", "/profit", "/square"):
        assert f'href="{gone}"' not in h.split('<div class="who">')[0], gone
    # Search, the Display menu, On counter, Lock and the clock are all still there
    assert "data-search" in client.get("/tabs").text.split("</header>")[0]
    for bit in ("data-display-mode", 'action="/switch"', 'action="/logout"', "data-clock"):
        assert bit in h, bit


def test_the_right_tab_is_on(client):
    login(client)
    def on(path):
        h = _header(client.get(path).text)
        return re.findall(r'<a href="[^"]+"[^>]*class="on"[^>]*>([^<]+)</a>', h)
    assert on("/") == ["Dashboard"] and on("/ask") == ["Dashboard"]
    assert on("/transactions") == ["Sales"] and on("/square") == ["Sales"]
    assert on("/money") == ["Money"] and on("/profit") == ["Money"]
    assert on("/tabs") == ["Tabs"] and on("/customers") == ["Customers"]
    assert on("/settings") == ["Settings"] and on("/tools") == ["Tools"] and on("/audit") == ["Log"]
    assert '<details class="nav-more on">' in client.get("/settings").text
    assert '<details class="nav-more">' in client.get("/").text


def test_add_customer_from_customers(client):
    _people(client)
    page = client.get("/customers").text
    assert "+ Add customer" in page and 'action="/tools/add_customer"' in page
    r = client.post("/tools/add_customer", data={"name": "Ms Teacher", "email": "teach@x.au", "back": "/customers"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/customers/")
    assert "Added Ms Teacher" in client.get(r.headers["location"]).text
    # A mistake comes back to Customers, opened at the form, not to Tools
    r = client.post("/tools/add_customer", data={"name": "Again", "email": "teach@x.au", "back": "/customers"}, follow_redirects=False)
    assert r.headers["location"] == "/customers#add_customer"
    page = client.get("/customers").text
    assert "already exists" in _flash(page) and 'data-jump="add_customer"' in page


def test_no_results_offers_to_add_them(client):
    _people(client)
    page = client.get("/customers?q=Zebedee Quux").text
    assert "No one matches “Zebedee Quux”" in page and "Add “Zebedee Quux”" in page
    form = page.split('id="add_customer"', 1)[1].split("</form>", 1)[0]
    assert 'value="Zebedee Quux"' in form and '<details class="tool" id="add_customer" open>' in page
    # A student number fills in their school email; the live search's "no one" line links here too
    page = client.get("/customers?add=rns12345678").text
    assert 'value="rns12345678@reddamnorthshore.nsw.edu.au"' in page
    assert "/customers?add=" in open("fizzy/static/app.js").read()


def test_a_card_nobody_has_points_to_add_customer(client):
    _people(client)
    r = client.get("/scan?code=rns07654321", follow_redirects=False)
    assert r.headers["location"] == "/customers?add=rns07654321#add_customer"
    page = client.get(r.headers["location"]).text
    assert "No one with student number RNS07654321" in page and "Add them here" in page
    assert 'value="rns07654321@reddamnorthshore.nsw.edu.au"' in page


def test_customer_page_has_their_own_tab_and_points_tools(client):
    cid = _people(client)
    page = client.get(f"/customers/{cid}").text
    who = "Adeline Yates &lt;rns00000001@reddamnorthshore.nsw.edu.au&gt;"
    for tool in ("manual_charge", "write_off", "extend_due"):
        form = page.split(f'action="/tools/{tool}"', 1)[1].split("</form>", 1)[0]
        assert f'<input type="hidden" name="customer" value="{who}">' in form, tool
        assert f'name="back" value="/customers/{cid}"' in form, tool
    move = page.split('action="/tools/transfer_points"', 1)[1].split("</form>", 1)[0]
    assert f'name="from" list="people" placeholder="Type a name…" autocomplete="off" required value="{who}"' in move
    # Run from here, it comes back here
    r = client.post("/tools/manual_charge", data={"customer": "Adeline Yates <rns00000001@reddamnorthshore.nsw.edu.au>", "amount": "3",
                                                 "back": f"/customers/{cid}"}, follow_redirects=False)
    assert r.headers["location"] == f"/customers/{cid}#manual_charge"
    assert "Charged $3.00" in _flash(client.get(f"/customers/{cid}").text)
    r = client.post("/tools/extend_due", data={"customer": "Adeline Yates <rns00000001@reddamnorthshore.nsw.edu.au>", "days": "3",
                                              "back": f"/customers/{cid}"}, follow_redirects=False)
    assert r.headers["location"] == f"/customers/{cid}#extend_due"


def test_back_never_leaves_the_site(client):
    _people(client)
    for bad in ("https://evil.example/", "//evil.example/x", "/\\evil.example", "javascript:alert(1)", "/a b", "evil"):
        r = client.post("/tools/extend_due", data={"customer": "Nobody", "days": "3", "back": bad}, follow_redirects=False)
        assert r.headers["location"] == "/tools#extend_due", bad


def test_tabs_and_sales_pages(client):
    _people(client)
    tabs = client.get("/tabs").text
    for bit in ('id="rules"', 'action="/settings"', "Who owes", 'action="/tools/pause_tabs"', 'action="/tools/revoke_all"',
                'action="/tools/chase"', 'action="/tools/balances"', "/tools/export/tab_charges.csv",
                "/tools/export/repayments.csv", "/tools/export/tab_decisions.csv"):
        assert bit in tabs, bit
    j = client.get("/tabs", headers=JSON).json()
    assert j["page"] == "tabs" and {"total_owed", "overdue", "due_soon", "owing", "cfg"} <= set(j) and "tool_ctx" not in j
    r = client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/transactions"
    for path in ("/transactions", "/square"):
        page = client.get(path).text
        bar = page.split('<nav class="subtabs"', 1)[1].split("</nav>", 1)[0]
        assert 'href="/transactions"' in bar and 'href="/square"' in bar and bar.count('class="on"') == 1
    assert client.get("/sales", headers=JSON).json()["page"] == "transactions"
    assert 'action="/tools/takings"' in client.get("/transactions").text
    sq = client.get("/square").text
    for tool in ("resync", "history", "find_payment"):
        assert f'action="/tools/{tool}"' in sq, tool
    for path in ("/money", "/profit"):
        bar = client.get(path).text.split('<nav class="subtabs"', 1)[1].split("</nav>", 1)[0]
        assert 'href="/money"' in bar and 'href="/profit"' in bar


def test_tab_rules_moved_from_settings_to_tabs(client):
    login(client)
    settings = client.get("/settings").text
    assert "<h2>Tab rules</h2>" not in settings and 'href="/tabs#rules"' in settings
    assert 'href="/display/settings"' in settings and 'name="message" maxlength="120"' not in settings
    r = client.post("/settings", data={"tab_limit": "10", "due_days": "9", "min_score": "40"}, follow_redirects=False)
    assert r.headers["location"] == "/tabs#rules"
    assert 'name="due_days" min="1" value="9"' in client.get("/tabs").text
    # Settings' JSON keeps what it had
    j = client.get("/settings", headers=JSON).json()
    assert {"cfg", "display_message", "display_owing", "display_menu"} <= set(j)


def test_customer_display_settings_from_the_display_menu(client):
    login(client)
    assert '<option value="edit">Edit message…</option>' in client.get("/").text
    page = client.get("/display/settings").text
    assert 'action="/settings/display"' in page
    r = client.post("/settings/display", data={"message": "Cold drinks!", "owing": "1"}, follow_redirects=False)
    assert r.headers["location"] == "/display/settings"
    assert 'value="Cold drinks!"' in client.get("/display/settings").text


def test_rewards_has_its_create_forms_and_they_work(client):
    login(client)
    page = client.get("/rewards").text
    assert 'action="/rewards"' in page and 'action="/events"' in page
    assert "Start one with the form." not in page and "Start a reward" in page
    for tool in ("bonus_points", "leaderboard", "raffle", "reset_points"):
        assert f'action="/tools/{tool}"' in page, tool
    assert "/tools/export/points.csv" in page
    client.post("/rewards", data={"name": "Free Coke", "points": "1000"})
    client.post("/events", data={"name": "Double points", "multiplier": "2", "start": stats.today().isoformat()})
    page = client.get("/rewards").text
    assert "Free Coke" in page and "Double points" in page.split('id="events"', 1)[1]
    r = client.post("/tools/leaderboard", data={"n": "5", "back": "/rewards"})
    assert 'href="/rewards">Rewards</a> /' in r.text   # the report's way back is to Rewards


def test_tools_index_lists_every_tool_with_a_home(client):
    login(client)
    page = client.get("/tools").text
    index = page.split('id="actions"', 1)[1].split("</ul>", 1)[0]
    links = dict((label, href) for href, label in re.findall(r'<a href="([^"]+)"><b>([^<]+)</b>', index))
    for _, items in tools.TOOLS:
        for tid, label, _ in items:
            if tid == "export":
                continue
            assert label in links, label
            assert not links[label].startswith("/tools"), (label, links[label])
    for name in tools.EXPORTS:
        assert any(h.startswith(tools.EXPORT_HOMES[name][0]) for h in links.values()), name
    for label in ("Add a customer", "Claim sale", "Tab rules", "Import the school list", "Fizz (Ask)", "Start a reward"):
        assert label in links, label
    assert "data-filter" in page and "data-filter-item" in index
    # Every tool form is still on Tools too
    for tid in tools.HANDLERS:
        assert f'action="/tools/{tid}"' in page, tid


def test_every_tool_is_on_its_home_page(client):
    cid = _people(client)
    for tid, (href, _) in tools.HOMES.items():
        if tid in ("export", "download_backup", "sync_now"):
            continue
        path = f"/customers/{cid}" if href == "/customers" and tid not in ("add_customer",) else href.split("#")[0]
        page = client.get(path).text
        assert f'action="/tools/{tid}"' in page, (tid, path)
    assert 'href="/tools/backup.db"' in client.get("/settings").text
    assert 'action="/square/sync"' in client.get("/square").text


def test_a_tool_run_from_its_home_comes_back_there(client):
    _people(client)
    r = client.post("/tools/pause_tabs", data={"paused": "1", "back": "/tabs"}, follow_redirects=False)
    assert r.headers["location"] == "/tabs#pause_tabs"
    page = client.get("/tabs").text
    assert "All tabs paused." in _flash(page) and 'data-jump="pause_tabs"' in page
    r = client.post("/tools/bulk_points", data={"delta": "5", "note": "prize", "people": "Adam Fenn", "back": "/customers"}, follow_redirects=False)
    assert r.headers["location"] == "/customers#bulk_points"
    r = client.post("/tools/resync", data={"start": "2026-01-01", "back": "/square"}, follow_redirects=False)
    assert r.headers["location"] == "/square#resync"   # Square isn't set up here: the problem shows on Square
    # And from Tools (no back), Tools as before
    r = client.post("/tools/pause_tabs", data={"paused": "0"}, follow_redirects=False)
    assert r.headers["location"] == "/tools#pause_tabs"


def test_ask_is_fizz_on_the_dashboard(client):
    login(client)
    page = client.get("/").text
    box = page.split('id="ask"', 1)[1].split("</section>", 1)[0]
    assert "<h2>Fizz" in box and "data-ask" in box and 'placeholder="Ask Fizz anything about the stall…"' in box
    assert "No Gemini key yet" in box and 'href="/settings#ask"' in box
    assert 'name="back" value="/"' in box
    # /ask still works, full screen, and the plain form from the box comes back to the box
    assert client.get("/ask").status_code == 200 and "<h1>Fizz</h1>" in client.get("/ask").text
    r = client.post("/ask", data={"text": "", "back": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/#ask"
    r = client.post("/ask", data={"text": ""}, follow_redirects=False)
    assert r.headers["location"] == "/ask#end"
    r = client.post("/ask/clear", data={"back": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/#ask"
    assert set(client.get("/ask", headers=JSON).json()) >= {"timeline", "enabled", "pending", "examples"}
    assert "chat" in client.get("/", headers=JSON).json()


def test_dashboard_add_a_note(client):
    login(client)
    page = client.get("/").text
    assert 'data-open="notice"' in page and "Add a note" in page and 'action="/tools/notice"' in page
    r = client.post("/tools/notice", data={"notice": "Fridge is broken", "back": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    page = client.get("/").text
    assert "Fridge is broken" in page and "Edit note" in page


def test_old_addresses_still_work(client):
    cid = _people(client)
    for path in ("/", "/customers", f"/customers/{cid}", "/transactions", "/square", "/rewards", "/money", "/profit", "/tools",
                 "/ask", "/import", "/settings", "/audit", "/tabs", "/sales", "/display/settings"):
        assert client.get(path).status_code == 200, path
    r = client.get("/import", follow_redirects=False)
    assert r.headers["location"] == "/customers#import"
    page = client.get("/customers").text
    assert 'action="/import"' in page and 'data-turbo="false" enctype="multipart/form-data"' in page
    assert client.get("/import", headers=JSON).json()["page"] == "import"


def test_chat_partial_closes_what_it_opens():
    """The chat box is included inside the dashboard's columns: a stray </div> splits them apart."""
    import re
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent / "fizzy" / "templates" / "_chat.html").read_text()
    for tag in ("div", "form", "section", "details"):
        assert len(re.findall(rf"<{tag}\b", s)) == len(re.findall(rf"</{tag}>", s)), tag
