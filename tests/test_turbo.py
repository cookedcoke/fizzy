"""Turbo Drive on the staff site: what the server has to get right for pages swapped in place.

The browser side (app.js init()/teardown, view transitions, the morph refresh) is checked by hand in
a browser; these are the parts that live in the templates and the Python."""
import inspect
import re
from pathlib import Path

from fizzy import tools
from tests.test_app import client, login  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent / "fizzy"
CSV = b"Cleaned Name,Email\nAdeline Yates,rns00000001@reddamnorthshore.nsw.edu.au\n"


def _head(page):
    return page.split("</head>", 1)[0]


def _body(page):
    return page.split("<body", 1)[1]


def _customer(client):
    client.post("/import", files={"file": ("d.csv", CSV, "text/csv")}, data={"confirm": "1"})
    return client.get("/api/search?q=adel").json()[0]["id"]


def _tag(page, pattern):
    """The opening tag matching pattern (a regex for part of it)."""
    m = re.search((pattern if pattern.startswith("<") else r"<[^<>]*" + pattern) + r"[^<>]*>", page)
    assert m, pattern
    return m.group(0)


def test_turbo_and_the_page_scripts_load_once_in_the_head(client):
    login(client)
    cid = _customer(client)
    for path in ("/", "/customers", f"/customers/{cid}", "/tools", "/settings", "/rewards", "/money", "/audit", "/import"):
        page = client.get(path).text
        head, body = _head(page), _body(page)
        for js in ("turbo.js", "fuzzy.js", "app.js", "passkey.js"):
            tag = _tag(head, rf'src="/static/{js}\?v=')
            assert "defer" in tag and 'data-turbo-track="reload"' in tag and 'data-turbo-eval="false"' in tag, (path, tag)
            assert f"/static/{js}" not in body, (path, js)   # not again in the body, where Turbo would run it on every page
        assert 'data-turbo-track="reload"' in _tag(head, r'href="/static/style\.css'), path
        assert '<meta name="turbo-prefetch" content="false">' in head, path
        assert '<meta name="view-transition" content="same-origin">' in head, path
    # The sign-in page is a staff page too (signed out)
    client.post("/logout")
    assert "/static/turbo.js" in _head(client.get("/login").text)


def test_no_turbo_on_the_student_site_or_the_customer_display(client):
    for page in (client.get("/me").text,):
        assert "turbo" not in page.lower()
    login(client)
    assert "turbo" not in client.get("/display").text.lower()
    for f in ("me_base.html", "display.html"):
        assert "turbo" not in (ROOT / "templates" / f).read_text().lower()


def test_pages_say_they_are_from_fizzy(client):
    """The page script loads a 5xx for real (so the service worker shows "Fizzy is down") unless
    Fizzy itself sent it, which this header says."""
    assert client.get("/login").headers["x-fizzy"] == "page"
    login(client)
    assert client.get("/").headers["x-fizzy"] == "page"
    assert client.get("/customers/999999").headers["x-fizzy"] == "page"   # Fizzy's own error page too


def test_forms_and_links_that_need_a_full_page_load(client):
    login(client)
    dash = client.get("/").text
    assert 'data-turbo="false"' in _tag(dash, r'action="/logout"')
    assert 'data-turbo="false"' in _tag(dash, r'<a href="/ask"')
    login_page = client.get("/login").text
    assert 'data-turbo="false"' in _tag(login_page, r'action="/login"')
    assert 'data-turbo="false"' in _tag(login_page, r'action="/login/passkey"')
    imp = client.get("/import").text
    assert 'data-turbo="false"' in _tag(imp, r'action="/import"')
    preview = client.post("/import", files={"file": ("d.csv", CSV, "text/csv")}).text   # the preview, with the confirm form
    assert preview.count('action="/import"') == 2
    assert all('data-turbo="false"' in t for t in re.findall(r'<form[^>]*action="/import"[^>]*>', preview))
    page = client.get("/tools").text
    for tool in tools.HANDLERS:
        tag = _tag(page, rf'action="/tools/{tool}"')
        assert ('data-turbo="false"' in tag) == (tool in tools.PAGE_TOOLS), tag
    assert '&quot;' not in "".join(re.findall(r"<form[^>]*>", page))
    # Downloads: the backup (not a .csv) and every export link
    for path in ("/tools", "/settings"):
        assert 'data-turbo="false"' in _tag(client.get(path).text, r'href="/tools/backup\.db"'), path
    for t in re.findall(r'<a[^>]*href="/tools/export/[^"]+"[^>]*>', page):
        assert 'data-turbo="false"' in t
    for t in re.findall(r'<a[^>]*href="/export/[^"]+"[^>]*>', client.get("/customers").text):
        assert 'data-turbo="false"' in t
    # Ask: a full page load in and out
    ask = client.get("/ask").text
    assert '<meta name="turbo-visit-control" content="reload">' in _head(ask)
    assert 'data-turbo="false"' in _tag(ask, r"<body")
    # The update progress page has its own timer: never drawn by Turbo
    assert '<meta name="turbo-visit-control" content="reload">' in _head(client.get("/settings/update/progress?since=1").text)


def test_page_tools_are_exactly_the_tools_that_answer_with_a_page():
    """Turbo draws nothing for a form answered with a 200 page: such tools load the classic way."""
    for name, fn in tools.HANDLERS.items():
        src = inspect.getsource(fn)
        page = "report(" in src or "render(" in src or "/square/payments/" in src   # the raw payment is plain text
        assert page == (name in tools.PAGE_TOOLS), name


def test_post_routes_that_draw_a_page_are_known():
    """A POST that answers with a page (not a redirect) needs data-turbo="false" on its form.
    These are the ones there are; a new one has to be added here, and its form marked."""
    from fizzy.app import create_app
    known = {"/login", "/login/passkey", "/import", "/tools/{tool}",
             "/square/webhook", "/enrol/{token}"}   # not forms on a staff page
    found = set()
    for r in create_app(background=False).routes:
        if "POST" in (getattr(r, "methods", None) or ()):
            src = inspect.getsource(r.endpoint)
            if re.search(r"\b(render|TemplateResponse|HTMLResponse|PlainTextResponse|error_page)\(", src) or "handler(" in src:
                found.add(r.path)
    assert found <= known, found - known


def test_back_keeps_the_section_as_data_jump(client):
    login(client)
    r = client.post("/settings/terms", data={"terms": "nonsense"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#terms")
    assert 'data-jump="terms"' in _tag(client.get("/settings").text, r"<body")
    assert "data-jump" not in client.get("/settings").text          # once
    # Only on the page it was meant for
    client.post("/tools/extend_due", data={"customer": "Nobody", "days": "3"})
    assert "data-jump" not in client.get("/settings").text
    client.post("/tools/extend_due", data={"customer": "Nobody", "days": "3"}, follow_redirects=False)
    assert 'data-jump="extend_due"' in client.get("/tools").text
    # A redirect without an anchor drops one left over from before
    client.post("/settings/terms", data={"terms": "nonsense"}, follow_redirects=False)
    client.post("/square/sync", follow_redirects=False)   # back(request, "/square", ...)
    assert "data-jump" not in client.get("/settings").text


def test_ask_redirects_keep_their_anchor(client):
    """Ask always loads in full, so the browser keeps #end from the redirect itself (and ask.html scrolls
    to the end): no data-jump, which would move the message into the empty #end."""
    login(client)
    r = client.post("/ask", data={"text": ""}, follow_redirects=False)
    assert r.headers["location"] == "/ask#end"
    assert "data-jump" not in client.get("/ask").text


def test_no_page_comes_from_the_page_cache(client):
    """Back and Forward ask Fizzy again, as without Turbo: every page shows points, tabs or who's on the
    counter (the header), and any of them can have changed on another screen since."""
    login(client)
    cid = _customer(client)
    no_cache = '<meta name="turbo-cache-control" content="no-cache">'
    for path in ("/", "/customers", f"/customers/{cid}", f"/customers/{cid}/claim", "/transactions", "/square", "/rewards",
                 "/profit", "/money", "/tools", "/import", "/settings", "/audit", "/customers/999999"):
        assert _head(client.get(path).text).count(no_cache) == 1, path
    # The dashboard morphs (keeping the scroll) only for its own refresh, which adds these just before it:
    # in the page they'd also apply to a form there that redirects back to /, leaving its message off screen.
    dash = _head(client.get("/").text)
    assert "turbo-refresh-method" not in dash and "turbo-refresh-scroll" not in dash
    app = (ROOT / "static" / "app.js").read_text()
    assert '"turbo-refresh-method:morph", "turbo-refresh-scroll:preserve"' in app


def test_page_scripts_clean_up_after_themselves():
    """app.js: per-page code runs in init() and is undone on turbo:before-render; no reloads for the
    dashboard's refresh, and none of the old "quiet" workaround left behind."""
    app = (ROOT / "static" / "app.js").read_text()
    assert 'addEventListener("turbo:before-render"' in app and 'addEventListener("turbo:render"' in app
    assert 'addEventListener("turbo:before-fetch-response"' in app
    assert "turbo:before-cache" not in app and "data-turbo-preview" not in app   # no page cache, so no previews to tidy
    assert "location.reload()" not in app.replace("if (!window.Turbo) { location.reload(); return; }", "")
    for f in [ROOT / "static" / "app.js", ROOT / "static" / "style.css", ROOT / "templates" / "base.html"]:
        assert "quiet" not in f.read_text().replace("quick and quiet", ""), f.name
    init = app.split("function init() {", 1)[1].split("\n  }\n", 1)[0]
    assert "addEventListener(" not in init and "setInterval(" not in init
    base = (ROOT / "templates" / "base.html").read_text()
    assert "fizzyNavOff" in base and "fizzyEdgesOff" in base   # the header scripts drop the last page's listeners


def test_a_form_turbo_sent_without_a_high_enough_passcode_gets_423(client):
    """app.js asks for the level the server wants and sends the form again (the server knows things the
    page doesn't, like a Square payment being tab money). A classic form still gets the message."""
    from fizzy import pin
    from tests.test_errors import _customer
    login(client)
    cid = _customer()
    client.post("/settings/pin", data={"new": "4821"})
    client.post("/switch", data={"who": "Will"})  # someone new on the counter: locked again
    turbo = {"X-Turbo-Request-Id": "abc", "Accept": "text/vnd.turbo-stream.html, text/html, application/xhtml+xml"}
    r = client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"}, headers=turbo, follow_redirects=False)
    assert r.status_code == 423 and r.headers["x-fizzy"] == "pin" and r.json()["need"] == pin.PIN
    r = client.post(f"/customers/{cid}/points", data={"delta": "5", "note": "x"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Enter the passcode first" in client.get(r.headers["location"]).text
    app = (ROOT / "static" / "app.js").read_text()
    assert "r.statusCode !== 423" in app and 'r.header("X-Fizzy") !== "pin"' in app


def test_a_tab_on_old_scripts_keeps_the_message_for_its_reload(client):
    """After an update, Turbo throws away the page it fetched (its tracked scripts changed) and loads it
    for real: the message and the section to land on must still be there for that load."""
    from fizzy.web import templates
    login(client)
    client.post("/tools/extend_due", data={"customer": "Nobody", "days": "3"}, follow_redirects=False)
    stale = client.get("/tools", headers={"X-Turbo-Request-Id": "a", "X-Fizzy-V": "old"}).text
    assert "No customer matches" in stale and 'data-jump="extend_due"' in stale
    page = client.get("/tools").text   # the reload
    assert "No customer matches" in page and 'data-jump="extend_due"' in page
    assert "No customer matches" not in client.get("/tools").text   # once
    # A tab on the current scripts uses it up as before
    client.post("/tools/extend_due", data={"customer": "Nobody", "days": "3"}, follow_redirects=False)
    assert "No customer matches" in client.get("/tools", headers={"X-Fizzy-V": templates.env.globals["V"]}).text
    assert "No customer matches" not in client.get("/tools").text


def test_error_pages_are_not_kept_for_back(client):
    login(client)
    page = client.get("/customers/999999").text
    assert '<meta name="turbo-cache-control" content="no-cache">' in _head(page)


def test_nav_links_keep_their_order_for_the_more_menu(client):
    """Turbo's copy of a page has the overflow tabs inside the More menu already: the header script
    gathers them from both places, in their first order (data-i)."""
    login(client)
    nav = client.get("/rewards").text.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'data-i="(\d+)"', nav) == [str(i) for i in range(12)]
    assert 'querySelectorAll("nav a, .nav-menu a")' in (ROOT / "templates" / "base.html").read_text()


def test_a_failed_sync_is_a_failed_job():
    import time
    from fizzy import jobs, square
    assert square.sync_job({"error": "boom"}) == ("Sync failed: boom", False)
    assert square.sync_job({"sale": 1}, "Re-sync: ")[1] is True
    assert jobs.start("Syncing with Square", lambda c: square.sync_job({"error": "boom"}), "Will")
    for _ in range(50):
        if not jobs.current()["running"]:
            break
        time.sleep(0.02)
    assert jobs.current()["ok"] is False and jobs.current()["message"] == "Sync failed: boom"
    src = inspect.getsource(tools.h_sync_now) + inspect.getsource(tools.h_resync)
    assert "describe_sync" not in src and "sync_job" in src


def test_sign_everyone_out_leaves_a_bare_sign_in_page(client):
    """The sign-in page after "Sign everyone out" has no staff header (it used to show the last person on
    the counter until some other request noticed the session was gone). The same for any stale session."""
    login(client)
    r = client.post("/tools/sign_out_all", data={"confirm": "SIGN OUT"}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert "<header>" not in client.get("/login").text
    login(client)
    from fizzy import db
    conn = db.connect()
    db.set_setting(conn, "session_gen", "elsewhere")   # signed out from another device
    conn.commit(); conn.close()
    page = client.get("/login").text
    assert "<header>" not in page and 'id="lock-rules"' not in page


def test_settings_problems_show_as_problems(client):
    login(client)
    client.post("/settings", data={"tab_limit": "10", "due_days": "999", "min_score": "50"}, follow_redirects=False)
    assert '<div class="flash bad" role="status">Days to pay must be between 1 and 365.' in client.get("/settings").text
    client.post("/settings", data={"tab_limit": "10", "due_days": "14", "min_score": "500"}, follow_redirects=False)
    assert '<div class="flash bad" role="status">The minimum tab score' in client.get("/settings").text


def test_after_an_outage_back_and_forward_load_the_page_for_real():
    """Turbo's pages are history entries of one document. When the offline (or "back soon") page takes over
    that document's place, Back/Forward to those entries stays inside it unless it reloads itself."""
    assert 'addEventListener("popstate", function () { location.reload(); })' in (ROOT / "static" / "offline.html").read_text()
    assert 'addEventListener("popstate", again)' in (ROOT / "static" / "down-staff.html").read_text()


def test_fixed_things_in_main_stay_on_the_screen():
    """The page-in animation must not leave a transform on <main>: that pins the New sale pop-up and
    Ask's typing box to the bottom of <main> instead of the screen after a full page load."""
    css = (ROOT / "static" / "style.css").read_text()
    rule = re.search(r"html:not\(\.still\) main \{ animation: ([^}]*)\}", css).group(1)
    assert "backwards" in rule and "both" not in rule and "forwards" not in rule


def test_no_shortcut_hints_left():
    for f in (ROOT / "templates").glob("*.html"):
        assert not re.search(r"\bG then [A-Z]\b", f.read_text()), f.name


def test_tool_errors_show_as_problems(client):
    """A tool that fails says so in red (flash bad), not in the green of a success."""
    login(client)
    r = client.post("/tools/reset_points", data={"confirm": "nope"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tools#reset_points"
    page = client.get("/tools").text
    assert 'class="flash bad"' in page and "Type RESET to confirm" in page


def test_error_page_go_back_after_a_turbo_form(client):
    """A form Turbo sent draws its error page at the form's own address, so "Go back" loads that page again."""
    from fizzy.app import error_page
    from starlette.requests import Request

    def req(method, turbo):
        headers = [(b"x-turbo-request-id", b"1")] if turbo else []
        return Request({"type": "http", "method": method, "path": "/tools/x", "headers": headers, "query_string": b"",
                        "session": {}, "app": client.app, "server": ("t", 80), "scheme": "http", "root_path": ""})
    assert "location.reload()" in error_page(req("POST", True), 500).body.decode()
    assert "history.back()" in error_page(req("POST", False), 500).body.decode()
    assert "history.back()" in error_page(req("GET", True), 404).body.decode()


def test_ask_redirects_keep_no_jump(client):
    """Ask loads in full and the browser keeps its #end itself: no data-jump that would move the message."""
    from fizzy.web import back
    from starlette.requests import Request
    req = Request({"type": "http", "method": "POST", "path": "/ask", "headers": [], "query_string": b"",
                   "session": {"jump": ["/x", "y"]}, "app": client.app, "server": ("t", 80), "scheme": "http", "root_path": ""})
    r = back(req, "/ask#end", "Done", jump=False)
    assert r.headers["location"] == "/ask#end" and "jump" not in req.session
    back(req, "/settings#backups", "x")
    assert req.session["jump"] == ["/settings", "backups"]
    ask = (ROOT / "ask.py").read_text()
    assert ask.count('back(request, "/ask#end"') == ask.count("jump=False")


def test_one_time_values_survive_a_stale_tab(client):
    """A Turbo fetch from a tab on the old scripts is thrown away, so one-time values wait for the real load."""
    from fizzy.web import take, templates
    from starlette.requests import Request

    def req(v):
        return Request({"type": "http", "method": "GET", "path": "/settings", "query_string": b"", "session": {"new_device_link": "L"},
                        "headers": [(b"x-fizzy-v", v.encode())] if v else [], "app": client.app, "server": ("t", 80), "scheme": "http", "root_path": ""})
    stale = req("old")
    assert take(stale, "new_device_link") == "L" and stale.session["new_device_link"] == "L"
    fresh = req(templates.env.globals["V"])
    assert take(fresh, "new_device_link") == "L" and "new_device_link" not in fresh.session
    app = (ROOT / "app.py").read_text()
    assert 'take(request, "pk_add_options")' in app and 'take(request, "new_device_link")' in app
