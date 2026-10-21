"""Fizzy web app (staff only, shared password)."""
import hmac
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config, customers, db, logic, square, tools
from .web import HERE, LoginRequired, back, cents, get_conn, money, render, session_gen, staff

log = logging.getLogger("fizzy")


# ---- background jobs --------------------------------------------------------------------------

_stop = threading.Event()


def _poller():
    while not _stop.wait(5):
        conn = db.connect()
        try:
            last = db.get_setting(conn, "square_last_sync")
            due = not last or datetime.now(timezone.utc) - logic.parse_ts(last) >= timedelta(seconds=config.SQUARE_POLL_SECONDS)
            if config.SQUARE_TOKEN and due:
                square.sync(conn)
            if config.BACKUP_DIR:
                last_b = db.get_setting(conn, "last_backup")
                if not last_b or datetime.now(timezone.utc) - logic.parse_ts(last_b) >= timedelta(hours=24):
                    path = db.backup(conn, config.BACKUP_DIR, config.BACKUP_KEEP)
                    db.set_setting(conn, "last_backup", db.now_iso())
                    db.set_setting(conn, "last_backup_path", path)
                    conn.commit()
        except Exception:
            log.exception("background job failed")
        finally:
            conn.close()
        _stop.wait(55)


def trigger_sync():
    def run():
        conn = db.connect()
        try:
            square.sync(conn)
        finally:
            conn.close()
    threading.Thread(target=run, daemon=True).start()


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
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.background = background
    app.add_middleware(
        SessionMiddleware, secret_key=config.SECRET_KEY or "unset", session_cookie="fizzy",
        max_age=60 * 60 * 24 * 30, same_site="lax", https_only=config.SECURE_COOKIES,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    register(app)
    app.include_router(tools.router)
    return app


# ---- auth -------------------------------------------------------------------------------------

_fails = {}


def register(app):
    @app.exception_handler(LoginRequired)
    async def _login_redirect(request, exc):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return render(request, "login.html", next=next, error=None)

    @app.post("/login")
    def login(request: Request, password: str = Form(...), who: str = Form(...), next: str = Form("/")):
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        fails = [t for t in _fails.get(ip, []) if t > time.time() - 900]
        if len(fails) >= 10:
            return render(request, "login.html", next=next, error="Too many attempts. Wait 15 minutes.")
        if not hmac.compare_digest(password.encode(), config.PASSWORD.encode()) or who not in config.STAFF_NAMES:
            _fails[ip] = fails + [time.time()]
            time.sleep(1)
            return render(request, "login.html", next=next, error="Wrong password.")
        _fails.pop(ip, None)
        conn = db.connect()
        try:
            request.session.update(auth=True, staff=who, gen=session_gen(conn))
        finally:
            conn.close()
        return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)

    @app.post("/switch")
    def switch(request: Request, who: str = Form(...), _=Depends(staff)):
        if who in config.STAFF_NAMES:
            request.session["staff"] = who
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
            "SELECT DISTINCT c.* FROM customers c JOIN tab_charges t ON t.customer_id = c.id"
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
            "WHERE c.tab_decision IS NULL GROUP BY c.id HAVING spent >= ? ORDER BY spent DESC",
            (cfg["unlock_spend_cents"],),
        ).fetchall()
        top = conn.execute(
            "SELECT c.id, c.name, SUM(p.delta) AS points, SUM(p.spend_cents) AS spent FROM customers c "
            "JOIN points_ledger p ON p.customer_id = c.id GROUP BY c.id ORDER BY spent DESC LIMIT 10"
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
        )

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
        else:
            rows = []
        results = [logic.customer_summary(conn, c, cfg) for c in rows]
        count = conn.execute("SELECT COUNT(*) FROM customers WHERE archived_at IS NULL").fetchone()[0]
        return render(request, "customers.html", q=q, results=results, count=count)

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
        return render(request, "customer.html", s=s, c=c, points=points, payments=payments,
                      decisions=decisions, audit_rows=audit_rows)

    @app.post("/customers/{cid}/repay")
    def repay(request: Request, cid: int, amount: str = Form(...), method: str = Form(...), note: str = Form(""),
              who=Depends(staff), conn=Depends(get_conn)):
        load_customer(conn, cid)
        if method not in ("cash", "payid", "card", "other"):
            raise HTTPException(400, "bad method")
        amt = cents(amount)
        try:
            logic.add_repayment(conn, cid, amt, method, note, who)
        except ValueError as e:
            return back(request, f"/customers/{cid}", str(e))
        conn.commit()
        return back(request, f"/customers/{cid}", f"Recorded {money(amt)} repayment ({method}). "
                    "Remember to ring up the 'Tab repayment' item in Square too.")

    @app.post("/customers/{cid}/points")
    def points(request: Request, cid: int, delta: int = Form(...), note: str = Form(""), who=Depends(staff),
               conn=Depends(get_conn)):
        load_customer(conn, cid)
        try:
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
            raise HTTPException(400, "bad decision")
        logic.set_tab_decision(conn, cid, mapping[decision], note, who)
        conn.commit()
        return back(request, f"/customers/{cid}", f"Tab {decision} saved.")

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
    def export_square(who=Depends(staff), conn=Depends(get_conn)):
        db.audit(conn, who, "export_square_csv")
        conn.commit()
        return Response(customers.square_import_csv(conn), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=fizzy-square-customers.csv"})

    # ---- square -------------------------------------------------------------------------

    @app.get("/square", response_class=HTMLResponse)
    def square_page(request: Request, show: str = "", who=Depends(staff), conn=Depends(get_conn)):
        where = "WHERE customer_id IS NULL" if show == "unlinked" else ""
        rows = conn.execute(
            f"SELECT sp.*, c.name FROM square_payments sp LEFT JOIN customers c ON c.id = sp.customer_id {where} "
            "ORDER BY sp.created_at DESC LIMIT 200"
        ).fetchall()
        unlinked_customers = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE square_customer_id IS NULL AND archived_at IS NULL"
        ).fetchone()[0]
        return render(
            request, "square.html", rows=rows, show=show, square_on=bool(config.SQUARE_TOKEN),
            last_sync=db.get_setting(conn, "square_last_sync"), sync_error=db.get_setting(conn, "square_last_error"),
            cursor=db.get_setting(conn, "square_cursor"), unlinked_customers=unlinked_customers,
            tab_match=config.SQUARE_TAB_MATCH, repay_item=config.SQUARE_REPAYMENT_ITEM,
            webhook_on=bool(config.SQUARE_WEBHOOK_KEY),
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

    @app.post("/square/sync")
    def square_sync_now(request: Request, who=Depends(staff), conn=Depends(get_conn)):
        if not config.SQUARE_TOKEN:
            return back(request, "/square", "Square isn't configured (SQUARE_ACCESS_TOKEN).")
        result = square.sync(conn)
        return back(request, "/square", f"Sync: {result}")

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
        return render(request, "settings.html", cfg=logic.settings(conn),
                      last_backup=db.get_setting(conn, "last_backup"),
                      last_backup_path=db.get_setting(conn, "last_backup_path"), backup_dir=config.BACKUP_DIR)

    @app.post("/settings")
    def save_settings(request: Request, tab_limit: str = Form(...), due_days: int = Form(...),
                      unlock_spend: str = Form(...), who=Depends(staff), conn=Depends(get_conn)):
        if due_days < 1:
            return back(request, "/settings", "Due days must be at least 1.")
        new = {"tab_limit_cents": cents(tab_limit), "tab_due_days": due_days, "unlock_spend_cents": cents(unlock_spend)}
        for k, v in new.items():
            db.set_setting(conn, k, v)
        db.audit(conn, who, "settings", None, json.dumps(new))
        conn.commit()
        return back(request, "/settings", "Settings saved. New due-day settings apply to new charges only.")

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

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"


app = create_app()
