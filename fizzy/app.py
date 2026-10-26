"""Fizzy web app (staff only, shared password)."""
import asyncio
import hmac
import secrets
from html import escape
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config, customers, db, devices, events, logic, pin, portal, rewards, square, stats, tools
from . import money as money_pages
from . import pwa
from .web import HERE, LoginRequired, back, cents, get_conn, local, money, render, session_gen, staff, templates

log = logging.getLogger("fizzy")


GATE_EXEMPT = ("/static/", "/healthz", "/square/webhook", "/enrol/", "/sw.js", "/manifest.webmanifest")
GATE_PAGE = ("<!doctype html><meta name=viewport content='width=device-width'><title>{title}</title>"
             "<body style='font:15px system-ui;max-width:520px;margin:15vh auto;padding:0 16px'>"
             "<h1 style='font-size:20px'>{title}</h1><p>{body}</p>")


def set_device_cookie(response, token):
    response.set_cookie(devices.DEVICE_COOKIE, token, max_age=devices.DEVICE_COOKIE_DAYS * 86400,
                        httponly=True, secure=config.SECURE_COOKIES, samesite="lax")


# ---- background jobs --------------------------------------------------------------------------

_stop = threading.Event()


def _poller():
    while not _stop.wait(5):
        conn = db.connect()
        try:
            # The last sync *or failed attempt*: a failing sync waits a full poll interval before
            # retrying instead of re-reading Square every minute.
            last = max(db.get_setting(conn, "square_last_sync") or "", db.get_setting(conn, "square_last_attempt") or "")
            due = not last or datetime.now(timezone.utc) - logic.parse_ts(last) >= timedelta(seconds=config.SQUARE_POLL_SECONDS)
            if config.SQUARE_TOKEN and due:
                square.sync(conn)
            events.reconcile(conn)
            conn.commit()
            if config.BACKUP_DIR:
                last_b = db.get_setting(conn, "last_backup")
                tried = db.get_setting(conn, "backup_last_attempt")
                due = not last_b or datetime.now(timezone.utc) - logic.parse_ts(last_b) >= timedelta(hours=24)
                retry_ok = not tried or datetime.now(timezone.utc) - logic.parse_ts(tried) >= timedelta(hours=1)
                if due and retry_ok:
                    try:
                        path = db.backup(conn, config.BACKUP_DIR, config.BACKUP_KEEP)
                    except Exception as e:  # e.g. the backup drive is unplugged or full
                        log.exception("backup failed")
                        db.set_setting(conn, "backup_error", f"{db.now_iso()}: {e}")
                        db.set_setting(conn, "backup_last_attempt", db.now_iso())
                        conn.commit()
                    else:
                        db.set_setting(conn, "last_backup", db.now_iso())
                        db.set_setting(conn, "last_backup_path", path)
                        db.set_setting(conn, "backup_error", "")
                        conn.commit()
        except Exception:
            log.exception("background job failed")
        finally:
            conn.close()
        _stop.wait(55)


_sync_thread = None
_sync_again = threading.Event()


def trigger_sync():
    """A webhook arrived: sync now. One thread at a time; a webhook during a sync means one more
    pass straight after it, so nothing waits for the next poll."""
    global _sync_thread
    _sync_again.set()
    if _sync_thread is not None and _sync_thread.is_alive():
        return
    def run():
        conn = db.connect()
        try:
            while _sync_again.is_set():
                _sync_again.clear()
                square.sync(conn)
        finally:
            conn.close()
    _sync_thread = threading.Thread(target=run, daemon=True)
    _sync_thread.start()


@asynccontextmanager
async def lifespan(app):
    conn = db.connect()
    db.init(conn)
    conn.close()
    if not config.PASSWORD or not config.SECRET_KEY:
        raise RuntimeError("Set FIZZY_PASSWORD and FIZZY_SECRET_KEY (see .env.example).")
    t = None
    if app.state.background:
        t = threading.Thread(target=_poller, daemon=True)
        t.start()
    yield
    _stop.set()


def create_app(background=True):
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(pin.guard)])
    app.state.background = background
    app.add_middleware(
        SessionMiddleware, secret_key=config.SECRET_KEY or "unset", session_cookie="fizzy",
        max_age=60 * 60 * 24 * 30, same_site="lax", https_only=config.SECURE_COOKIES,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def device_gate(request, call_next):
        """With FIZZY_ACCESS_HOST set: the main host only serves registered devices, and the
        Access host only serves requests carrying a valid Cloudflare Access token."""
        if not devices.enabled() or request.url.path.startswith(GATE_EXEMPT) or getattr(request.state, "public", False):
            return await call_next(request)
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host == config.ACCESS_HOST.lower():
            claims = await run_in_threadpool(devices.verify_access, request.headers.get("cf-access-jwt-assertion"))
            if not claims:
                return HTMLResponse(GATE_PAGE.format(
                    title="Cloudflare Access required",
                    body="This address only works through Cloudflare Access, and Fizzy couldn't verify the "
                         "Access sign-in. If you just set it up, check <code>CF_ACCESS_TEAM_DOMAIN</code> and "
                         "<code>CF_ACCESS_AUD</code>."), status_code=403)
            request.state.access_email = claims.get("email")
            return await call_next(request)
        def check():
            conn = db.connect()
            try:
                return devices.check(conn, request.cookies.get(devices.DEVICE_COOKIE))
            finally:
                conn.close()
        device, refreshed = await run_in_threadpool(check)
        if device is None:
            if request.method == "GET":
                target = f"https://{config.ACCESS_HOST}{request.url.path}"
                if request.url.query:
                    target += "?" + request.url.query
                return RedirectResponse(target, status_code=303)
            return HTMLResponse(GATE_PAGE.format(title="Device not registered",
                                                 body=f'Use <a href="https://{config.ACCESS_HOST}/">{config.ACCESS_HOST}</a>.'),
                                status_code=403)
        request.state.device = device["name"]
        response = await call_next(request)
        if refreshed:
            set_device_cookie(response, request.cookies.get(devices.DEVICE_COOKIE))
        return response

    @app.middleware("http")
    async def cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") and request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.middleware("http")
    async def maintenance(request, call_next):
        """Maintenance mode (python -m fizzy maintenance on): everyone gets the "back soon" page.
        Square's webhook still gets through, so no sales are missed. Runs before the device gate."""
        msg = await run_in_threadpool(maintenance_message)
        path = request.url.path
        if msg is None or path.startswith("/static/") or path in ("/healthz", "/square/webhook"):
            return await call_next(request)
        error = None
        if request.method == "POST" and path.endswith("/maintenance/off"):  # the owner box on the page itself
            password = (await request.form()).get("password", "")
            error = await run_in_threadpool(turn_off_maintenance, request, password)
            if error is None:
                return RedirectResponse("/", status_code=303)
        return HTMLResponse(down_page(msg, owner_box=True, error=error), status_code=503,
                            headers={"Retry-After": "60", "Cache-Control": "no-store", "X-Fizzy": "maintenance"})

    @app.middleware("http")
    async def public_host(request, call_next):
        """The student site's host serves only the student pages (and static files): its paths
        map onto /me, so no staff page is reachable there. With a student host set, /me isn't
        served on the staff hosts. Added last, so it runs before the device gate."""
        host = (request.headers.get("host") or "").split(":")[0].strip().rstrip(".").lower()
        path = request.url.path
        if config.PUBLIC_HOST and host == config.PUBLIC_HOST:
            if path == "/robots.txt":
                return PlainTextResponse("User-agent: *\nDisallow: /\n")
            if not path.startswith("/static/"):
                request.scope["path"] = "/me" + (path if path != "/" else "")
            request.state.public = True
            return await call_next(request)
        if config.PUBLIC_HOST and (path == "/me" or path.startswith("/me/")):
            return error_page(request, 404)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    register(app)
    app.include_router(tools.router)
    app.include_router(rewards.router)
    app.include_router(portal.router)
    app.include_router(events.router)
    app.include_router(pin.router)
    app.include_router(pwa.router)
    app.include_router(money_pages.router)
    return app


_maint = {"at": 0.0, "msg": None}


def maintenance_message():
    """None when off, else the message to show (blank for the default). Checked every few seconds."""
    if time.time() - _maint["at"] > 3:
        conn = db.connect()
        try:
            v = db.get_setting(conn, "maintenance")
        finally:
            conn.close()
        _maint.update(at=time.time(), msg=v)
    return _maint["msg"]


def down_page(msg="", owner_box=False, error=None):
    page = (HERE / "static" / "down.html").read_text()
    if msg:
        page = re.sub(r"<!--MSG-->.*?<!--/MSG-->", lambda _: escape(msg), page, flags=re.S)
    if owner_box and pin.admin_on():
        box = ('<details class="owner"' + (" open" if error else "") + '><summary>Owner? Turn maintenance off</summary>'
               '<form method="post" action="/maintenance/off"><input type="password" name="password" placeholder="Owner password" '
               'aria-label="Owner password" autocomplete="current-password" required><button>Turn off</button></form>'
               + (f'<p class="err" role="alert">{escape(error)}</p>' if error else "") + "</details>")
        page = page.replace("<!--OWNER--><!--/OWNER-->", box)
    return page


def set_maintenance(msg, who):
    """msg None turns it off; "" is on with the default message."""
    conn = db.connect()
    try:
        if msg is None:
            conn.execute("DELETE FROM settings WHERE key = 'maintenance'")
        else:
            db.set_setting(conn, "maintenance", msg)
        db.audit(conn, who, "maintenance", None, "off" if msg is None else f"on {msg}".strip())
        conn.commit()
    finally:
        conn.close()
    _maint["at"] = 0  # take effect straight away


def turn_off_maintenance(request, password):
    """The owner box on the maintenance page. Returns an error, or None once it's off."""
    now, me = time.time(), pin._browser(request)
    if pin._recent(me, now) >= pin.FAIL_LIMIT:
        return "Too many wrong tries. Wait 10 minutes."
    if not pin.is_owner(password):
        pin._fails[me].append(now)
        return "Wrong password."
    set_maintenance(None, "owner")  # runs before sign-in cookies are read; the password is what counts
    return None


def backup_health(conn):
    """For the dashboard: is the daily backup actually happening? Returns (ok, text)."""
    if not config.BACKUP_DIR:
        return False, "Backups aren't set up. (Owner: see the README.)"
    last, path, err = (db.get_setting(conn, k) for k in ("last_backup", "last_backup_path", "backup_error"))
    if err:
        return False, f"Last backup failed: {err.split(': ', 1)[-1]}. Check the backup drive."
    if not last:
        return False, "No backup yet - the first one runs within a minute of starting."
    age = datetime.now(timezone.utc) - logic.parse_ts(last)
    if path and not os.path.exists(path):
        return False, "The latest backup file is missing. Check the backup drive."
    if age > timedelta(hours=26):
        return False, f"No backup for {age.days} day(s)." if age.days else f"No backup for {int(age.total_seconds() // 3600)} hours."
    return True, local(last)


# ---- auth -------------------------------------------------------------------------------------

# ---- login rate limiting --------------------------------------------------------------------
# Browsers that have signed in before carry a signed "known browser" cookie and are only limited
# by their own failures, so someone guessing from the same school network can't lock the counter
# out. Unknown browsers are limited per network (IPv6 per /64) and all together.
KNOWN_COOKIE = "fizzy_known"
FAIL_WINDOW = 900
LIMIT_PER_BUCKET = 10
LIMIT_ALL_UNKNOWN = 60
FAIL_DELAY = 1  # seconds after a wrong password
_fails = {}


def _known_serializer():
    return URLSafeSerializer(config.SECRET_KEY, salt="fizzy-known-browser")


def _fail_buckets(request):
    token = request.cookies.get(KNOWN_COOKIE)
    if token:
        try:
            return [("known", _known_serializer().loads(token))]
        except BadSignature:
            pass
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if ":" in ip:  # IPv6: one household/school gets a whole /64
        ip = ":".join(ip.split(":")[:4]) + "::/64"
    return [("ip", ip), ("unknown", "*")]


def _recent(key, now):
    return [t for t in _fails.get(key, []) if t > now - FAIL_WINDOW]


def _locked(buckets, now):
    for b in buckets:
        limit = LIMIT_ALL_UNKNOWN if b[0] == "unknown" else LIMIT_PER_BUCKET
        if len(_recent(b, now)) >= limit:
            return True
    return False


def _record_fail(buckets, now):
    for b in buckets:
        _fails[b] = _recent(b, now) + [now]
    if len(_fails) > 5000:  # forget old entries
        for k in [k for k, v in _fails.items() if not v or v[-1] < now - FAIL_WINDOW]:
            del _fails[k]


ERRORS = {
    400: ("That didn't work", "Something in that request wasn't right. Go back and try again."),
    403: ("Not allowed", "This device or account can't open that."),
    404: ("Page not found", "That page doesn't exist or has been deleted."),
    405: ("Page not found", "That page doesn't exist."),
    500: ("Something broke", "Fizzy hit an error. Nothing was saved from that last step. Try again, and if it keeps "
                             "happening, check the logs on the home PC."),
}


def error_page(request, status, message=None):
    heading, default = ERRORS.get(status, ERRORS[400] if status < 500 else ERRORS[500])
    if getattr(request.state, "public", False) or request.url.path == "/me" or request.url.path.startswith("/me/"):
        home = "/" if getattr(request.state, "public", False) else "/me"
        response = templates.TemplateResponse(request, "me_error.html", {"heading": heading, "message": message or default,
                                                                          "home": home}, status_code=status)
    else:
        response = render(request, "error.html", heading=heading, message=message or default, home="/")
        response.status_code = status
    return response


def _same_site_referer(request):
    ref = request.headers.get("referer") or ""
    host = request.headers.get("host") or ""
    for scheme in ("https://", "http://"):
        if host and ref.startswith(scheme + host + "/"):
            return ref[len(scheme + host):]
    return None


def register(app):
    @app.exception_handler(RequestValidationError)
    async def _bad_form(request, exc):
        """A missing or garbled form field: back to the form with a note, not a page of JSON."""
        ref = _same_site_referer(request)
        if request.method == "POST" and ref:
            return back(request, ref, "Something in that form was missing or not a proper number. Check it and try again.")
        return error_page(request, 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, exc):
        ref = _same_site_referer(request)
        if exc.status_code == 400 and request.method == "POST" and ref and isinstance(exc.detail, str):
            return back(request, ref, exc.detail)  # a typo in a form: back to it with what was wrong
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Method Not Allowed") else None
        return error_page(request, exc.status_code, detail if exc.status_code != 404 else None)

    @app.exception_handler(Exception)
    async def _crash(request, exc):
        log.exception("Error on %s %s", request.method, request.url.path)
        return error_page(request, 500)

    @app.exception_handler(pin.PinRequired)
    async def _need_pin(request, exc):
        msg = "Enter the passcode first. Nothing was changed."
        if request.method == "GET":
            return back(request, "/tools", msg)
        return back(request, _same_site_referer(request) or "/", msg)

    @app.exception_handler(LoginRequired)
    async def _login_redirect(request, exc):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return render(request, "login.html", next=next, error=None)

    @app.post("/login")
    async def login(request: Request, password: str = Form(""), who: str = Form(""), next: str = Form("/")):
        if not password:
            return render(request, "login.html", next=next, error="Type the password first.")
        now, buckets = time.time(), _fail_buckets(request)
        if _locked(buckets, now):
            return render(request, "login.html", next=next, error="Too many attempts. Wait 15 minutes.")
        if not hmac.compare_digest(password.encode(), config.PASSWORD.encode()) or who not in config.STAFF_NAMES:
            _record_fail(buckets, now)
            await asyncio.sleep(FAIL_DELAY)
            return render(request, "login.html", next=next, error="Wrong password.")
        for b in buckets:
            if b[0] != "unknown":
                _fails.pop(b, None)
        conn = db.connect()
        try:
            request.session.update(auth=True, staff=who, gen=session_gen(conn))
            via = getattr(request.state, "device", None) or getattr(request.state, "access_email", None)
            db.audit(conn, who, "login", None, f"via {via}" if via else "")
            conn.commit()
        finally:
            conn.close()
        safe_next = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
        response = RedirectResponse(safe_next, status_code=303)
        if not request.cookies.get(KNOWN_COOKIE):
            response.set_cookie(KNOWN_COOKIE, _known_serializer().dumps(secrets.token_urlsafe(16)),
                                max_age=400 * 86400, httponly=True, secure=config.SECURE_COOKIES, samesite="lax")
        return response

    @app.post("/switch")
    def switch(request: Request, who: str = Form(...), _=Depends(staff)):
        if who in config.STAFF_NAMES:
            request.session["staff"] = who
            request.session.pop("unlock", None)  # a new person on the counter enters it again
        return RedirectResponse(request.headers.get("referer") or "/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---- dashboard -----------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        cfg = logic.settings(conn)
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=2)
        with_tab = conn.execute(
            "SELECT DISTINCT c.* FROM customers c JOIN tab_charges t ON t.customer_id = c.id WHERE c.kind IS NOT 'test'"
        ).fetchall()
        total_owed = total_overdue = 0
        overdue, due_soon = [], []
        for c in with_tab:
            led = logic.tab_ledger(conn, c["id"], now)
            total_owed += led["owing_cents"]
            total_overdue += led["overdue_cents"]
            for ch in led["charges"]:
                if ch["unpaid_cents"] <= 0:
                    continue
                row = {"customer": c, "charge": ch}
                if ch["overdue"]:
                    overdue.append(row)
                elif logic.parse_ts(ch["due_at"]) <= soon:
                    due_soon.append(row)
        overdue.sort(key=lambda r: r["charge"]["due_at"])
        due_soon.sort(key=lambda r: r["charge"]["due_at"])

        eligible = conn.execute(
            "SELECT c.*, SUM(p.spend_cents) AS spent FROM customers c JOIN points_ledger p ON p.customer_id = c.id "
            "WHERE c.tab_decision IS NULL AND c.archived_at IS NULL AND c.kind IS NOT 'test' GROUP BY c.id HAVING spent >= ? ORDER BY spent DESC",
            (cfg["unlock_spend_cents"],),
        ).fetchall()
        top = conn.execute(
            "SELECT c.id, c.name, SUM(p.delta) AS points, SUM(p.spend_cents) AS spent FROM customers c "
            "JOIN points_ledger p ON p.customer_id = c.id WHERE c.archived_at IS NULL AND c.kind IS NOT 'test' "
            "GROUP BY c.id ORDER BY spent DESC LIMIT 10"
        ).fetchall()
        breaches = conn.execute(
            "SELECT t.*, c.name FROM tab_charges t JOIN customers c ON c.id = t.customer_id "
            "WHERE t.breach IS NOT NULL AND t.breach_ack_by IS NULL ORDER BY t.charged_at DESC"
        ).fetchall()
        unlinked_tabs = conn.execute(
            "SELECT * FROM square_payments WHERE customer_id IS NULL AND kind != 'sale' ORDER BY created_at DESC"
        ).fetchall()
        return render(
            request, "dashboard.html", total_owed=total_owed, total_overdue=total_overdue, overdue=overdue,
            due_soon=due_soon, eligible=eligible, top=top, breaches=breaches, unlinked_tabs=unlinked_tabs,
            last_sync=db.get_setting(conn, "square_last_sync"), sync_error=db.get_setting(conn, "square_last_error"),
            square_on=bool(config.SQUARE_TOKEN), notice=db.get_setting(conn, "notice", ""),
            tabs_paused=cfg["tabs_paused"],
            people=conn.execute("SELECT name, email FROM customers WHERE archived_at IS NULL ORDER BY name").fetchall()
            if unlinked_tabs else [],
            live_rewards=rewards.active(conn), events_on=events.on(conn), events_soon=events.upcoming(conn, 7),
            sales=stats.dashboard(conn), backup=backup_health(conn),
        )

    PROFIT_RANGES = {"today": ("Today", 0), "7": ("Last 7 days", 6), "30": ("Last 30 days", 29)}

    @app.get("/profit", response_class=HTMLResponse)
    def profit_page(request: Request, range: str = "7", who=Depends(staff), conn=Depends(get_conn)):
        label, back_days = PROFIT_RANGES.get(range, PROFIT_RANGES["7"])
        d = stats.today()
        start, _ = stats.day_bounds(d - timedelta(days=back_days))
        _, end = stats.day_bounds(d)
        cost_map = stats.costs(conn)
        p = stats.profit(stats.checkouts(conn, start, end), cost_map)
        # Everything in Square's item list (and anything else with a cost) shows too, even if it
        # hasn't sold in this range, so costs can be set up front. Price is Square's list price.
        sold = {r["key"] for r in p["items"]}
        idle = []
        for r in conn.execute("SELECT * FROM catalog_items WHERE deleted = 0 ORDER BY display"):
            key = f"v:{r['variation_id']}"
            if key not in sold:
                base = stats.item_key(r["item_name"])
                idle.append({"key": key, "base": base, "name": r["display"], "price": r["price_cents"],
                             "cost_each": stats.cost_of(cost_map, key, base)})
        listed = sold | {i["key"] for i in idle} | {i["base"] for i in idle} | {r["base"] for r in p["items"]}
        for r in conn.execute("SELECT * FROM item_costs ORDER BY name"):
            if r["name_key"] not in listed:
                idle.append({"key": r["name_key"], "base": r["name_key"], "name": r["name"], "price": None,
                             "cost_each": r["cost_cents"]})
        for i in idle:
            i["margin"] = (round(100 * (i["price"] - i["cost_each"]) / i["price"])
                           if i["price"] and i["cost_each"] is not None else None)
        return render(request, "profit.html", pr=p, idle=idle, range=range if range in PROFIT_RANGES else "7",
                      ranges=PROFIT_RANGES, label=label, square_on=bool(config.SQUARE_TOKEN),
                      catalog_sync=db.get_setting(conn, "catalog_last_ok"), catalog_error=db.get_setting(conn, "catalog_error"),
                      catalog_count=conn.execute("SELECT COUNT(*) FROM catalog_items WHERE deleted = 0").fetchone()[0])

    @app.post("/profit/refresh")
    def profit_refresh(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        if not config.SQUARE_TOKEN:
            return back(request, "/profit", "Square isn't connected (SQUARE_ACCESS_TOKEN).")
        try:
            n = square.sync_catalog(conn, square.SquareClient(), force=True)
        except Exception as e:  # network trouble: say so, don't crash
            return back(request, "/profit", f"Couldn't reach Square: {e}")
        if db.get_setting(conn, "catalog_error"):
            return back(request, "/profit")  # the page shows what went wrong
        return back(request, "/profit", f"Got {n} item{'' if n == 1 else 's'} from Square.")

    @app.post("/profit")
    async def profit_save(request: Request, who=Depends(staff)):
        form = await request.form()
        names, values = form.getlist("name"), form.getlist("cost")
        keys, bases = form.getlist("key"), form.getlist("base")
        conn = db.connect()
        try:
            changed = 0
            have = stats.costs(conn)
            for i, (name, value) in enumerate(zip(names, values)):
                key = keys[i] if i < len(keys) else stats.item_key(name)
                base = bases[i] if i < len(bases) else key
                value = value.strip().replace("$", "")
                cents_v = None
                if value:
                    try:
                        cents_v = round(float(value) * 100)
                        if cents_v > 10_000_000:
                            raise OverflowError
                    except (ValueError, OverflowError):
                        return back(request, request.headers.get("referer") or "/profit", f"'{value}' isn't a dollar amount ({name}).")
                    if cents_v < 0:
                        return back(request, request.headers.get("referer") or "/profit", f"A cost can't be negative ({name}).")
                if stats.cost_of(have, key, base) == cents_v:
                    continue
                if cents_v is None and have.get(key) is None:
                    # Blanking a cost it got from the plain item name: remove that (its other
                    # variations were showing the same inherited cost).
                    stats.set_cost(conn, name, None, who, key=base)
                    have.pop(base, None)
                else:
                    stats.set_cost(conn, name, cents_v, who, key=key)
                    have[key] = cents_v
                changed += 1
            if changed:
                db.audit(conn, who, "item_costs", None, f"{changed} item cost(s) changed")
            conn.commit()
        finally:
            conn.close()
        return back(request, request.headers.get("referer") or "/profit", f"Saved {changed} cost(s)." if changed else "No changes.")

    @app.post("/breach/{charge_id}/ack")
    def ack_breach(request: Request, charge_id: int, who=Depends(staff), conn=Depends(get_conn)):
        conn.execute("UPDATE tab_charges SET breach_ack_by = ? WHERE id = ?", (who, charge_id))
        db.audit(conn, who, "breach_ack", None, f"charge #{charge_id}")
        conn.commit()
        return back(request, "/", "Alert dismissed.")

    # ---- customers ----------------------------------------------------------------------

    @app.get("/customers", response_class=HTMLResponse)
    def customer_list(request: Request, q: str = "", who=Depends(staff), conn=Depends(get_conn)):
        cfg = logic.settings(conn)
        if q:
            rows = customers.search(conn, q, 50)
            if len(rows) == 1:
                return RedirectResponse(f"/customers/{rows[0]['id']}", status_code=303)
        else:  # recently active people, so the page isn't empty
            rows = conn.execute(
                "SELECT c.* FROM customers c JOIN (SELECT customer_id, MAX(created_at) AS last FROM points_ledger "
                "GROUP BY customer_id) p ON p.customer_id = c.id WHERE c.archived_at IS NULL ORDER BY p.last DESC LIMIT 25"
            ).fetchall()
        results = [logic.customer_summary(conn, c, cfg) for c in rows]
        count = conn.execute("SELECT COUNT(*) FROM customers WHERE archived_at IS NULL").fetchone()[0]
        not_in_square = conn.execute("SELECT COUNT(*) FROM customers WHERE archived_at IS NULL "
                                     "AND square_customer_id IS NULL AND kind IS NOT 'test'").fetchone()[0]
        return render(request, "customers.html", q=q, results=results, count=count, not_in_square=not_in_square,
                      can_reward=rewards.can_redeem_any(conn) if results else set())

    @app.get("/api/index")
    def api_index(who=Depends(staff), conn=Depends(get_conn)):
        rows = logic.search_index(conn)
        can = rewards.can_redeem_any(conn)
        for r in rows:
            r["reward"] = r["id"] in can
        return JSONResponse(rows, headers={"Cache-Control": "no-store"})

    @app.get("/scan")
    def scan(request: Request, code: str = "", who=Depends(staff), conn=Depends(get_conn)):
        """A student card's barcode (their RNS number) scanned at the counter."""
        sid = code.strip().lower()
        c = conn.execute("SELECT id FROM customers WHERE email LIKE ? ORDER BY archived_at IS NOT NULL LIMIT 1",
                         (sid.replace("%", "").replace("_", "") + "@%",)).fetchone() if re.fullmatch(r"rns\d{8}", sid) else None
        if c is None:
            return back(request, "/", f"No one with student number {sid.upper()} yet. Add them on Tools, Add a customer.")
        return RedirectResponse(f"/customers/{c['id']}", status_code=303)

    @app.get("/api/search")
    def api_search(q: str = "", who=Depends(staff), conn=Depends(get_conn)):
        out = []
        cfg = logic.settings(conn)
        for c in customers.search(conn, q, 10):
            s = logic.customer_summary(conn, c, cfg)
            out.append({
                "id": c["id"], "name": c["name"], "email": c["email"], "points": s["points"],
                "owing": money(s["ledger"]["owing_cents"]), "status": s["status"]["label"],
                "status_key": s["status"]["status"],
            })
        return JSONResponse(out)

    def load_customer(conn, cid):
        c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
        if c is None:
            raise HTTPException(404, "No such customer")
        return c

    @app.get("/customers/{cid}", response_class=HTMLResponse)
    def customer_page(request: Request, cid: int, who=Depends(staff), conn=Depends(get_conn)):
        c = load_customer(conn, cid)
        s = logic.customer_summary(conn, c)
        points = conn.execute(
            "SELECT * FROM points_ledger WHERE customer_id = ? ORDER BY created_at DESC, id DESC LIMIT 200", (cid,)
        ).fetchall()
        payments = conn.execute(
            "SELECT * FROM square_payments WHERE customer_id = ? ORDER BY created_at DESC LIMIT 100", (cid,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM tab_decisions WHERE customer_id = ? ORDER BY created_at DESC", (cid,)
        ).fetchall()
        audit_rows = conn.execute(
            "SELECT * FROM audit_log WHERE customer_id = ? ORDER BY created_at DESC LIMIT 100", (cid,)
        ).fetchall()
        opened = datetime.now(timezone.utc)
        watch_since = (opened - timedelta(seconds=90)).isoformat(timespec="seconds")
        opened = opened.isoformat(timespec="seconds")
        kind_levels = {lv: pin.customer_level(conn, c["kind"], lv) for lv in (pin.OPEN, pin.PIN, pin.OWNER)}
        return render(request, "customer.html", kind_levels=kind_levels, s=s, c=c, points=points, payments=payments, watch_since=watch_since, opened=opened,
                      decisions=decisions, audit_rows=audit_rows,
                      offers=rewards.offers_for(conn, cid, s["points"]),
                      recent_redemptions=rewards.recent_redemptions(conn, cid), public_host=config.PUBLIC_HOST,
                      passcode_readable=portal.reveal_passcode(c["passcode_enc"]) is not None)

    @app.get("/customers/{cid}/claim", response_class=HTMLResponse)
    def claim_page(request: Request, cid: int, who=Depends(staff), conn=Depends(get_conn)):
        """Pick the sale just rung up in Square (with no customer) and give it to this customer."""
        c = load_customer(conn, cid)
        note = None
        if config.SQUARE_TOKEN:
            result = square.sync(conn, wait=8)  # fetch the sale that was just taken, if the webhook hasn't
            if "error" in result:
                note = square.describe_sync(result)
        return render(request, "claim.html", c=c, checkouts=square.unclaimed(conn), note=note, st=logic.tab_status(conn, c),
                      hours=square.CLAIM_HOURS, square_on=bool(config.SQUARE_TOKEN))

    @app.get("/api/customers/{cid}/new-sales")
    def new_sales(cid: int, since: str = "", who=Depends(staff), conn=Depends(get_conn)):
        """For the "new sale - claim it?" prompt on a customer's page: unclaimed sales that came in
        after `since` (when the page was opened, less a little slack), newest first."""
        c = load_customer(conn, cid)
        st = logic.tab_status(conn, c)
        out = []
        for k in square.unclaimed(conn, limit=3):
            if since and k["at"] <= since:
                continue
            out.append({"key": k["key"], "at": local(k["at"], "%H:%M"), "at_iso": k["at"],
                        "items": k["items"] or ("Tab repayment" if k["repay_cents"] else "Sale"),
                        "paid": money(k["sale_cents"]) if k["sale_cents"] else None,
                        "tab": money(k["tab_cents"]) if k["tab_cents"] else None,
                        "repaid": money(k["repay_cents"]) if k["repay_cents"] else None,
                        "tab_warning": bool(k["tab_cents"]) and (not st["can_charge"] or k["tab_cents"] > st["available_cents"])})
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @app.post("/customers/{cid}/claim")
    def claim(request: Request, cid: int, key: str = Form(...), who=Depends(staff), conn=Depends(get_conn)):
        c = load_customer(conn, cid)
        try:
            square.claim(conn, key, c, who)
        except ValueError as e:
            return back(request, f"/customers/{cid}/claim", str(e))
        conn.commit()
        return back(request, f"/customers/{cid}", f"Sale claimed for {c['name']}.")

    @app.post("/customers/{cid}/repay")
    def repay(request: Request, cid: int, amount: str = Form(...), method: str = Form(...), note: str = Form(""),
              confirm_dup: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
        load_customer(conn, cid)
        if method not in ("cash", "card", "other"):
            raise HTTPException(400, "Pick cash, card or other.")
        amt = cents(amount)
        # Repayments are normally rung up in Square and recorded automatically; don't let the
        # same money be entered twice. (A hand entry made first is matched when Square's arrives.)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        dup = conn.execute(
            "SELECT created_at FROM tab_repayments WHERE customer_id = ? AND amount_cents = ? AND note = 'Recorded in Square' "
            "AND created_at >= ?", (cid, amt, since)).fetchone()
        if dup and not confirm_dup:
            return back(request, f"/customers/{cid}", f"A {money(amt)} repayment was already recorded from Square at "
                        f"{local(dup['created_at'])}. Nothing added. If this is a second payment, tick 'This is a separate payment'.")
        try:
            logic.add_repayment(conn, cid, amt, method, note, who)
        except ValueError as e:
            return back(request, f"/customers/{cid}", str(e))
        conn.commit()
        return back(request, f"/customers/{cid}", f"Recorded {money(amt)} repayment ({method}).")

    @app.post("/customers/{cid}/points")
    def points(request: Request, cid: int, delta: str = Form(""), note: str = Form(""), who=Depends(staff),
               conn=Depends(get_conn)):
        load_customer(conn, cid)
        try:
            delta = logic.whole_points(delta)
            logic.adjust_points(conn, cid, delta, note, who)
        except ValueError as e:
            return back(request, f"/customers/{cid}", str(e))
        conn.commit()
        return back(request, f"/customers/{cid}", f"Points {delta:+d}.")

    @app.post("/customers/{cid}/tab")
    def tab_decision(request: Request, cid: int, decision: str = Form(...), note: str = Form(""),
                     who=Depends(staff), conn=Depends(get_conn)):
        load_customer(conn, cid)
        mapping = {"approve": "approved", "reject": "rejected", "revoke": "revoked", "reset": None}
        if decision not in mapping:
            raise HTTPException(400, "Pick approve, reject, revoke or reset.")
        logic.set_tab_decision(conn, cid, mapping[decision], note, who)
        conn.commit()
        return back(request, f"/customers/{cid}", f"Tab {decision} saved.")

    @app.post("/customers/{cid}/kind")
    def set_kind(request: Request, cid: int, kind: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
        c = load_customer(conn, cid)
        kind = kind if kind in ("admin", "test") else None
        conn.execute("UPDATE customers SET kind = ? WHERE id = ?", (kind, cid))
        db.audit(conn, who, "customer_kind", cid, kind or "normal")
        conn.commit()
        label = {"admin": "an admin account", "test": "a test account", None: "a normal customer"}[kind]
        return back(request, f"/customers/{cid}", f"{c['name']} is now {label}.")

    @app.post("/customers/{cid}/edit")
    def edit_customer(request: Request, cid: int, name: str = Form(...), square_customer_id: str = Form(""),
                      archived: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
        c = load_customer(conn, cid)
        sid = square_customer_id.strip() or None
        try:
            conn.execute(
                "UPDATE customers SET name = ?, square_customer_id = ?, archived_at = ? WHERE id = ?",
                (" ".join(name.split()), sid, (c["archived_at"] or db.now_iso()) if archived else None, cid),
            )
        except Exception:
            return back(request, f"/customers/{cid}", "That Square customer ID is already linked to someone else.")
        db.audit(conn, who, "customer_edit", cid, f"name={name!r} square={sid} archived={bool(archived)}")
        square.link_pending(conn)
        conn.commit()
        return back(request, f"/customers/{cid}", "Saved.")

    # ---- import / export ---------------------------------------------------------------

    @app.get("/import", response_class=HTMLResponse)
    def import_form(request: Request, who=Depends(staff)):
        return render(request, "import.html", preview=None)

    @app.post("/import", response_class=HTMLResponse)
    async def import_csv(request: Request, file: UploadFile, confirm: str = Form(""), who=Depends(staff),
                         conn=Depends(get_conn)):
        text = (await file.read()).decode("utf-8-sig", errors="replace")
        rows, errors = customers.parse_directory_csv(text)
        counts = customers.import_rows(conn, rows, who, apply=bool(confirm))
        if confirm:
            conn.commit()
            return back(request, "/customers", f"Imported: {counts['added']} added, {counts['updated']} updated, "
                        f"{counts['unchanged']} unchanged.")
        return render(request, "import.html", preview={"rows": rows[:20], "total": len(rows), "errors": errors,
                                                       "counts": counts})

    @app.get("/export/square-customers.csv")
    def export_square(only: str = "", who=Depends(staff), conn=Depends(get_conn)):
        only_new = only == "new"
        db.audit(conn, who, "export_square_csv", None, "not in Square yet" if only_new else "everyone")
        conn.commit()
        name = "fizzy-square-new-customers.csv" if only_new else "fizzy-square-customers.csv"
        return Response(customers.square_import_csv(conn, only_new), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    # ---- square -------------------------------------------------------------------------

    @app.get("/square", response_class=HTMLResponse)
    def square_page(request: Request, show: str = "", who=Depends(staff), conn=Depends(get_conn)):
        where = "WHERE customer_id IS NULL" if show == "unlinked" else ""
        rows = conn.execute(
            f"SELECT sp.*, c.name FROM square_payments sp LEFT JOIN customers c ON c.id = sp.customer_id {where} "
            "ORDER BY sp.created_at DESC LIMIT 200"
        ).fetchall()
        people = conn.execute("SELECT name, email FROM customers WHERE archived_at IS NULL ORDER BY name").fetchall()
        unlinked_customers = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE square_customer_id IS NULL AND archived_at IS NULL AND kind IS NOT 'test'"
        ).fetchone()[0]
        return render(
            request, "square.html", rows=rows, show=show, square_on=bool(config.SQUARE_TOKEN),
            last_sync=db.get_setting(conn, "square_last_sync"), sync_error=db.get_setting(conn, "square_last_error"),
            cursor=db.get_setting(conn, "square_cursor"), unlinked_customers=unlinked_customers,
            tab_tenders=sorted(config.SQUARE_TAB_TENDERS), repay_item=config.SQUARE_REPAYMENT_ITEM,
            tab_discount=config.SQUARE_TAB_DISCOUNT,
            webhook_on=bool(config.SQUARE_WEBHOOK_KEY), people=people,
            item_errors=json.loads(db.get_setting(conn, "square_item_errors") or "[]"),
            conflicts=json.loads(db.get_setting(conn, "square_customer_conflicts") or "[]"),
            unclaimed=len(square.unclaimed(conn, hours=24 * 7, limit=1000)),
        )

    @app.get("/square/payments/{pid}", response_class=PlainTextResponse)
    def square_raw(pid: str, who=Depends(staff), conn=Depends(get_conn)):
        row = conn.execute("SELECT raw_json FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone()
        if row is None:
            raise HTTPException(404)
        return json.dumps(json.loads(row["raw_json"]), indent=2)

    @app.post("/square/payments/{pid}/kind")
    def square_reclassify(request: Request, pid: str, kind: str = Form(...), who=Depends(staff),
                          conn=Depends(get_conn)):
        square.reclassify(conn, pid, kind, who)
        conn.commit()
        return back(request, request.headers.get("referer") or "/square", f"Payment marked as {kind}.")

    @app.post("/square/payments/{pid}/assign")
    def square_assign(request: Request, pid: str, customer: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
        try:
            c = tools.resolve(conn, customer)
            square.assign_customer(conn, pid, c, who)
        except (tools.ToolError, ValueError) as e:
            return back(request, request.headers.get("referer") or "/square", str(e))
        conn.commit()
        return back(request, request.headers.get("referer") or "/square", f"Assigned to {c['name']}.")

    @app.post("/square/sync")
    def square_sync_now(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        if not config.SQUARE_TOKEN:
            return back(request, "/square", "Square isn't configured (SQUARE_ACCESS_TOKEN).")
        return back(request, "/square", square.describe_sync(square.sync(conn, wait=True)))

    @app.post("/square/webhook")
    async def square_webhook(request: Request):
        body = await request.body()
        if not square.verify_webhook(body, request.headers.get("x-square-hmacsha256-signature", "")):
            return PlainTextResponse("bad signature", status_code=403)
        trigger_sync()
        return PlainTextResponse("ok")

    # ---- settings / audit ---------------------------------------------------------------

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        return render(request, "settings.html", cfg=logic.settings(conn), pin_on=bool(pin.stored(conn)), admin_on=pin.admin_on(), perms=pin.permissions(conn),
                      actions=pin.ACTIONS, level_labels=pin.LEVEL_LABELS,
                      devices_on=devices.enabled(), access_host=config.ACCESS_HOST, main_host=config.HOST,
                      device_list=conn.execute("SELECT * FROM devices ORDER BY revoked_at IS NOT NULL, created_at DESC").fetchall(),
                      this_device=getattr(request.state, "device", None),
                      access_email=getattr(request.state, "access_email", None),
                      new_link=request.session.pop("new_device_link", None),
                      link_minutes=devices.LINK_MINUTES,
                      last_backup=db.get_setting(conn, "last_backup"),
                      last_backup_path=db.get_setting(conn, "last_backup_path"), backup_dir=config.BACKUP_DIR)

    @app.post("/settings")
    def save_settings(request: Request, tab_limit: str = Form(...), due_days: int = Form(...),
                      unlock_spend: str = Form(...), who=Depends(staff), conn=Depends(get_conn)):
        if not 1 <= due_days <= 365:
            return back(request, "/settings", "Days to pay must be between 1 and 365.")
        new = {"tab_limit_cents": cents(tab_limit), "tab_due_days": due_days, "unlock_spend_cents": cents(unlock_spend)}
        for k, v in new.items():
            db.set_setting(conn, k, v)
        db.audit(conn, who, "settings", None, json.dumps(new))
        conn.commit()
        return back(request, "/settings", "Saved.")

    @app.post("/settings/maintenance")
    def maintenance_on(request: Request, message: str = Form(""), who=Depends(staff)):
        if not pin.admin_on():  # only the owner can turn it off again, so there has to be one
            return back(request, "/settings#maintenance", "Set an owner password first (FIZZY_ADMIN_PASSWORD in .env).")
        set_maintenance(" ".join(message.split())[:200], who)
        return RedirectResponse("/", status_code=303)

    @app.post("/settings/backup")
    def backup_now(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        if not config.BACKUP_DIR:
            return back(request, "/settings", "Set FIZZY_BACKUP_DIR to enable backups.")
        path = db.backup(conn, config.BACKUP_DIR, config.BACKUP_KEEP)
        db.set_setting(conn, "last_backup", db.now_iso())
        db.set_setting(conn, "last_backup_path", path)
        conn.commit()
        return back(request, "/settings", f"Backed up to {path}")

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        rows = conn.execute(
            "SELECT a.*, c.name FROM audit_log a LEFT JOIN customers c ON c.id = a.customer_id "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT 500"
        ).fetchall()
        return render(request, "audit.html", rows=rows)

    # ---- registered devices ---------------------------------------------------------------

    @app.get("/enrol/{token}", response_class=HTMLResponse)
    def enrol_form(token: str, conn=Depends(get_conn)):
        link = devices.pending_link(conn, token)
        if link is None:
            return HTMLResponse(GATE_PAGE.format(title="Link expired or used",
                                                 body="Make a new one in Settings → Devices."), status_code=410)
        # A button rather than registering on GET, so link previews can't use the link up.
        return HTMLResponse(GATE_PAGE.format(
            title=f"Register this device as “{escape(link['name'])}”?",
            body=f"<form method=post><button style='font:inherit;padding:8px 16px'>Register this device</button></form>"
                 f"<p style='color:#6A717C'>It can then open {config.HOST} without Cloudflare Access (staff password still "
                 f"needed). Only do this on a device that stays at the counter.</p>"))

    @app.post("/enrol/{token}")
    def enrol(token: str, conn=Depends(get_conn)):
        device_token = devices.redeem(conn, token)
        if device_token is None:
            return HTMLResponse(GATE_PAGE.format(title="Link expired or used",
                                                 body="Make a new one in Settings → Devices."), status_code=410)
        conn.commit()
        response = RedirectResponse("/", status_code=303)
        set_device_cookie(response, device_token)
        return response

    @app.post("/settings/devices")
    def device_link(request: Request, name: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
        try:
            link = devices.create_link(conn, name, who)
        except ValueError as e:
            return back(request, "/settings#devices", str(e))
        conn.commit()
        request.session["new_device_link"] = link
        return back(request, "/settings#devices")

    @app.post("/settings/devices/{device_id}/revoke")
    def device_revoke(request: Request, device_id: int, who=Depends(staff), conn=Depends(get_conn)):
        devices.revoke(conn, device_id, who)
        conn.commit()
        return back(request, "/settings#devices", "Device removed. It now has to go through Cloudflare Access.")

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"


app = create_app()
