"""Admin tools: one-off and occasional jobs (events, end of term, fixing mistakes, reports).

Every tool is listed in TOOLS (label + one-line tip shown on hover) and handled by a
POST /tools/<id> route. Anything that changes data is written to the audit log.
"""
import csv
import io
import os
import re
import secrets
import sqlite3
import tempfile
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import config, customers, db, logic, square
from .web import back, cents, get_conn, local, money, render, safe_back, staff

router = APIRouter(prefix="/tools")

# (section, [(id, label, tip)]) - the tip is what staff see on hover, and at the top of the opened form.
TOOLS = [
    ("Points", [
        ("bulk_points", "Give points to many", "Add or take points from a bunch of people at once, like prizes."),
        ("bonus_points", "Bonus points for a period", "Give extra points for what people bought between two dates. For a day that's already happened - plan future ones on Rewards."),
        ("transfer_points", "Move points", "Move points from one person to another, if they went to the wrong one."),
        ("reset_points", "Reset all points", "Set everyone's points to 0. Spending and tabs aren't touched."),
    ]),
    ("Tabs", [
        ("pause_tabs", "Pause all tabs", "Stop anyone using a tab for now. Turn it back on and nothing else has changed."),
        ("revoke_all", "Turn off all tabs", "Turn everyone's tab off, like at the end of the year. Turn them back on one at a time."),
        ("manual_charge", "Add tab charge by hand", "Put something on someone's tab that didn't go through Square."),
        ("write_off", "Write off a debt", "Wipe some or all of what someone owes. No points for it."),
        ("extend_due", "Give more time to pay", "Give someone more days to pay what they owe."),
    ]),
    ("Customers", [
        ("add_customer", "Add a customer", "Add someone who isn't in the school list, like a teacher."),
        ("merge", "Merge duplicates", "Same person twice? Join them into one."),
        ("bulk_archive", "Archive many", "Hide people who've left school. Nothing is deleted."),
        ("restore", "Restore archived", "Bring hidden people back."),
        ("test_accounts", "Make test accounts", "Fake customers to try things on. They stay out of leaderboards, reports and exports."),
        ("delete_test_accounts", "Delete test accounts", "Remove every test account and everything done to them. Real customers aren't touched."),
    ]),
    ("Reports", [
        ("takings", "Takings summary", "What came in over some dates, by cash, card and tab. Handy for counting the till."),
        ("chase", "Who to chase", "Everyone who owes money, most overdue first, with their emails."),
        ("balances", "All balances", "Everyone who owes money or has credit."),
        ("leaderboard", "Points leaderboard", "Top customers by points, ready to print."),
    ]),
    ("Fun", [
        ("raffle", "Random winner", "Pick random winners for a giveaway. Can give more chances to people with more points."),
    ]),
    ("Square", [
        ("sync_now", "Sync now", "Get the latest from Square now instead of waiting."),
        ("resync", "Re-sync from a date", "Go back over Square from a date, e.g. after Fizzy was down. Nothing gets counted twice."),
        ("history", "Import sales history", "Bring in Square sales from before Fizzy started, for reports and Fizz only. Nobody gets points, tabs or badges for them."),
        ("find_payment", "Find a Square payment", "Paste a Square payment ID to see what Fizzy did with it."),
    ]),
    ("Data", [
        ("export", "Export CSV", "Download any table as a spreadsheet."),
        ("download_backup", "Download backup", "Download a copy of everything."),
        ("integrity", "Health check", "Look for anything that needs fixing up."),
    ]),
    ("Staff", [
        ("notice", "Noticeboard", "Leave a note at the top of the dashboard for the others."),
        ("sign_out_all", "Sign everyone out", "Sign out every device, like if a phone got lost. Change the password too if someone else knows it."),
    ]),
]

# Tools that answer with a page (a report, the raffle draw, the raw Square payment) rather than a redirect
# back to /tools. Their forms load the classic way (data-turbo="false"): Turbo only draws a form's answer
# when it's a redirect, so these would look like nothing happened (and a second click draws the raffle again).
PAGE_TOOLS = {"takings", "chase", "balances", "leaderboard", "raffle", "integrity", "find_payment"}

EXPORTS = {
    "customers": "SELECT id, name, email, square_customer_id, tab_decision, tab_decision_note, created_at, archived_at FROM customers",
    "points": "SELECT p.*, c.name FROM points_ledger p JOIN customers c ON c.id = p.customer_id",
    "tab_charges": "SELECT t.*, c.name FROM tab_charges t JOIN customers c ON c.id = t.customer_id",
    "repayments": "SELECT r.*, c.name FROM tab_repayments r JOIN customers c ON c.id = r.customer_id",
    "square_payments": "SELECT square_payment_id, square_order_id, square_customer_id, customer_id, kind, amount_cents, tender, items, created_at, no_account, no_account_by FROM square_payments",
    "tab_decisions": "SELECT d.*, c.name FROM tab_decisions d JOIN customers c ON c.id = d.customer_id",
    "audit_log": "SELECT * FROM audit_log",
}


class ToolError(Exception):
    pass


# ---- helpers ----------------------------------------------------------------------------------

def resolve(conn, text, archived_ok=False):
    """Find exactly one customer. Only an exact match is acted on: 'Name <email>' (what the
    suggestion list fills in), an email, a full student number, or an exact unique name.
    Anything fuzzier stops with suggestions, so a typo can't hit the wrong student."""
    text = " ".join((text or "").split())
    if not text:
        raise ToolError("Pick a customer.")
    arch = "" if archived_ok else " AND archived_at IS NULL"
    m = re.search(r"<([^>]+)>", text)
    email = (m.group(1) if m else text if "@" in text else "").strip().lower()
    if email:
        row = conn.execute(f"SELECT * FROM customers WHERE lower(email) = ?{arch}", (email,)).fetchone()
        if row:
            return row
        raise ToolError(f"No customer with email {email}.")
    sid = text.lower()
    if re.fullmatch(r"(rns)?\d+", sid):
        sid = sid if sid.startswith("rns") else f"rns{sid}"
        rows = conn.execute(f"SELECT * FROM customers WHERE email LIKE ? || '@%'{arch}", (sid,)).fetchall()
        if len(rows) == 1:
            return rows[0]
        raise ToolError(f"No customer with student number {sid}.")
    # Compare names with spacing tidied on both sides (Square-made names can have double spaces).
    rows = [r for r in conn.execute(f"SELECT * FROM customers WHERE 1 = 1{arch}")
            if " ".join(r["name"].split()).lower() == text.lower()]
    if len(rows) == 1:
        return rows[0]
    hits = rows or customers.search(conn, text, 5)
    if not hits:
        raise ToolError(f"No customer matches '{text}'.")
    who = "; ".join(f"{h['name']} <{h['email']}>" for h in hits[:5])
    if rows:
        raise ToolError(f"More than one '{text}': {who}. Pick one from the list.")
    raise ToolError(f"No exact match for '{text}'. Did you mean: {who}? Pick one from the list.")


def resolve_many(conn, text, archived_ok=False):
    """One person per line (or separated by ';'). Commas are kept, so 'Surname, Given' works."""
    found, missing = [], []
    for line in re.split(r"[\n;]+", text or ""):
        if line.strip():
            parts = [p.strip() for p in line.split(",")]
            candidates = [line] + ([f"{parts[1]} {parts[0]}"] if len(parts) == 2 and all(parts) else [])
            for i, cand in enumerate(candidates):
                try:
                    c = resolve(conn, cand, archived_ok)
                    if c["id"] not in {f["id"] for f in found}:
                        found.append(c)
                    break
                except ToolError as e:
                    if i == len(candidates) - 1:
                        missing.append(str(e))
    return found, missing


def day_start(d, end=False):
    """Local date (YYYY-MM-DD) -> UTC ISO at the start (or end) of that day."""
    try:
        day = datetime.strptime(d, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ToolError("Pick a date.")
    if not 2000 <= day.year <= 2999:
        raise ToolError("Pick a date between 2000 and 2999.")
    dt = datetime.combine(day + timedelta(days=1) if end else day, time(), tzinfo=config.TZ)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def link(text, href):
    """A report cell that links somewhere. Plain strings are escaped as usual."""
    return {"text": text, "href": href}


def cust_link(c):
    return link(c["name"], f"/customers/{c['id']}")


NUMBER = re.compile(r"^[-+]?\$?[\d,]+(\.\d+)?%?$")


def report(request, title, headers, rows, note=None, copy=None):
    # Columns that are all numbers or money line up on the right, like every other table.
    numeric = [bool(rows) and all(isinstance(r[i], (int, float)) or (isinstance(r[i], str) and (NUMBER.match(r[i].strip()) or not r[i].strip()))
                                  for r in rows) for i in range(len(headers))]
    home = _back(request) or "/tools"
    crumb = (home, PAGE_NAMES.get(home.split("?")[0]) or ("Customer" if home.startswith("/customers/") else "Back"))
    return render(request, "tool_result.html", title=title, headers=headers, rows=rows, note=note, copy=copy, numeric=numeric, crumb=crumb)


def _nice_day(iso):
    try:
        return date.fromisoformat(iso).strftime("%a %-d %b %Y")
    except (TypeError, ValueError):
        return iso


def _back(request):
    """The page a tool was run from, when it wasn't Tools (the form's hidden "back")."""
    return getattr(getattr(request, "state", None), "tool_back", None)


def done(request, tool, msg, kind="ok"):
    """Back to where the tool was run (its own page, or Tools), opened at the tool with the message."""
    return back(request, f"{_back(request) or '/tools'}#{tool}", msg, kind)


def tool_names():
    return {t[0]: t[1] for _, items in TOOLS for t in items}


# ---- where everything lives ---------------------------------------------------------------------
# Every action lives on the page of the thing it changes (README, "Where things live"). Tools keeps a
# copy of every tool form too, and an index of every action, each linking to its home.

PAGE_NAMES = {"/": "Dashboard", "/customers": "Customers", "/transactions": "Sales", "/square": "Square", "/tabs": "Tabs",
              "/rewards": "Rewards", "/money": "Money", "/profit": "Profit", "/settings": "Settings", "/audit": "Log",
              "/tools": "Tools", "/display/settings": "Customer display"}
CUSTOMER_PAGE = "A customer's page"

# Tool id -> (link to its home, where that is).
HOMES = {
    "bulk_points": ("/customers#bulk_points", "Customers"),
    "bonus_points": ("/rewards#bonus_points", "Rewards"),
    "transfer_points": ("/customers", CUSTOMER_PAGE),
    "reset_points": ("/rewards#reset_points", "Rewards"),
    "pause_tabs": ("/tabs#pause_tabs", "Tabs"),
    "revoke_all": ("/tabs#revoke_all", "Tabs"),
    "manual_charge": ("/customers", CUSTOMER_PAGE),
    "write_off": ("/customers", CUSTOMER_PAGE),
    "extend_due": ("/customers", CUSTOMER_PAGE),
    "add_customer": ("/customers#add_customer", "Customers"),
    "merge": ("/customers#merge", "Customers"),
    "bulk_archive": ("/customers#bulk_archive", "Customers"),
    "restore": ("/customers#restore", "Customers"),
    "test_accounts": ("/customers#test_accounts", "Customers"),
    "delete_test_accounts": ("/customers#delete_test_accounts", "Customers"),
    "takings": ("/transactions#takings", "Sales"),
    "chase": ("/tabs#chase", "Tabs"),
    "balances": ("/tabs#balances", "Tabs"),
    "leaderboard": ("/rewards#leaderboard", "Rewards"),
    "raffle": ("/rewards#raffle", "Rewards"),
    "sync_now": ("/square", "Sales, Square"),
    "resync": ("/square#resync", "Sales, Square"),
    "history": ("/square#history", "Sales, Square"),
    "find_payment": ("/square#find_payment", "Sales, Square"),
    "export": ("/tools#exports", "Tools, and each page's own"),
    "download_backup": ("/settings#backups", "Settings"),
    "integrity": ("/settings#integrity", "Settings"),
    "notice": ("/#notice", "Dashboard"),
    "sign_out_all": ("/settings#sign_out_all", "Settings"),
}

# Each export, on the page its table belongs to.
EXPORT_HOMES = {"customers": ("/customers#exports", "Customers"), "points": ("/rewards#points-export", "Rewards"),
                "tab_charges": ("/tabs#tab-exports", "Tabs"), "repayments": ("/tabs#tab-exports", "Tabs"),
                "tab_decisions": ("/tabs#tab-exports", "Tabs"), "square_payments": ("/square#square-export", "Sales, Square"),
                "audit_log": ("/audit", "Log")}
EXPORT_LABELS = {"customers": "customers", "points": "points history", "tab_charges": "tab charges", "repayments": "tab repayments",
                 "tab_decisions": "tab decisions", "square_payments": "Square payments", "audit_log": "activity log"}

# Actions that were never tools: (label, what it's for, link, where).
PAGE_ACTIONS = [
    ("Find a customer", "Search by name or student number. A card scan works on any page.", "/customers", "Customers"),
    ("Claim sale", "Ring the sale up in Square first, then press Claim sale: it links the sale from the 30 seconds before.", "/customers", CUSTOMER_PAGE),
    ("Pick a sale", "Choose from the latest Square sales with nobody on them.", "/customers", CUSTOMER_PAGE),
    ("Record a repayment", "Money for a tab that didn't go through Square.", "/customers", CUSTOMER_PAGE),
    ("Tab limit, or turn a tab off", "Pick a limit, adjust their tab score, or turn their tab off or back on.", "/customers", CUSTOMER_PAGE),
    ("Adjust points", "Give or take points from one person.", "/customers", CUSTOMER_PAGE),
    ("Redeem a reward", "Shows on their page once they have the points.", "/customers", CUSTOMER_PAGE),
    ("Sign-in code", "A one-time QR code on the customer display for the student site.", "/customers", CUSTOMER_PAGE),
    ("Student site passcode", "Set, show or remove someone's passcode.", "/customers", CUSTOMER_PAGE),
    ("Edit a customer", "Name, Square ID, archive, account type, sign out their phones.", "/customers", CUSTOMER_PAGE),
    ("Import the school list", "Upload the school CSV: preview, then confirm.", "/customers#import", "Customers"),
    ("Export for Square", "A file to add people to Square (Square Dashboard, Customers, Import).", "/customers#exports", "Customers"),
    ("Every sale", "Every Square checkout with who it was, by date.", "/transactions", "Sales"),
    ("Assign a sale", "Say whose sale it was, for a sale with nobody on it.", "/square?show=unlinked", "Sales, Square"),
    ("No account", "A teacher's sale, or anyone's without an account: it counts, nobody gets it.", "/square", "Sales, Square"),
    ("Change how a payment counts", "Sale, tab charge, tab repayment, or not a real sale.", "/square", "Sales, Square"),
    ("Who owes", "Everyone owing, overdue and due soon.", "/tabs", "Tabs"),
    ("Tab rules", "Highest tab limit, days to pay, minimum tab score.", "/tabs#rules", "Tabs"),
    ("Start a reward", "Something to spend points on, like a free Coke.", "/rewards#new-reward", "Rewards"),
    ("Double points day", "Extra points on one day, a few days, or every week.", "/rewards#new-event", "Rewards"),
    ("Custom badges and events", "Pause, restart or delete them. Make new ones with Fizz.", "/rewards#rules", "Rewards"),
    ("Fizz (Ask)", "Questions about the stall, and suggestions you can do with one press.", "/#ask", "Dashboard"),
    ("Restocks", "What we spent on stock, with the receipt.", "/money#restock", "Money"),
    ("Pay the partners", "Record a payout.", "/money#payout", "Money"),
    ("Item costs", "What one of each item costs us, for profit.", "/profit", "Money, Profit"),
    ("Customer display message", "The message between customers, what's on the shelf, and showing what they owe.", "/display/settings", "Display menu"),
    ("Who can do what", "Which actions need the PIN or the owner password.", "/settings#permissions", "Settings"),
    ("Updates", "Update Fizzy.", "/settings#updates", "Settings"),
    ("Gemini key for Fizz", "Turn Fizz on or off.", "/settings#ask", "Settings"),
    ("Term dates", "For badges and events that work by term.", "/settings#terms", "Settings"),
    ("Closed days", "cookedcoke.co shows Closed, and the dashboard a recap.", "/settings#closed", "Settings"),
    ("PIN", "Set, change or remove the staff PIN.", "/settings#pin", "Settings"),
    ("Maintenance mode", "A Back soon page for everyone.", "/settings#maintenance", "Settings"),
    ("Back up now", "A copy in the backup folder.", "/settings#backups", "Settings"),
    ("Security keys", "Sign in with a key or passkey.", "/settings#passkeys", "Settings"),
    ("Devices", "Register a counter device.", "/settings#devices", "Settings"),
    ("Activity log", "Everything everyone did, newest first.", "/audit", "Log"),
]


def index():
    """Every action, for the search on Tools: {label, tip, href, where, section}."""
    out = []
    for section, items in TOOLS:
        for tid, label, tip in items:
            if tid == "export":
                continue
            href, where = HOMES[tid]
            out.append({"id": tid, "label": label, "tip": tip, "href": href, "where": where, "section": section})
    for name in EXPORTS:
        href, where = EXPORT_HOMES[name]
        out.append({"id": f"export_{name}", "label": f"Export {EXPORT_LABELS[name]}", "tip": "Download it as a spreadsheet (CSV).",
                    "href": href, "where": where, "section": "Data"})
    for label, tip, href, where in PAGE_ACTIONS:
        out.append({"id": "", "label": label, "tip": tip, "href": href, "where": where, "section": "Pages"})
    return out


DANGER = ["reset_points", "revoke_all", "write_off", "merge", "sign_out_all", "bulk_archive", "delete_test_accounts"]


def form_ctx(conn):
    """What a page needs to draw tool forms (_tools.html). Every page with tools on it gets this as tool_ctx."""
    people = conn.execute("SELECT name, email, archived_at FROM customers ORDER BY name").fetchall()
    today = datetime.now(config.TZ).date()
    hb = db.get_setting(conn, "history_before", "")
    return {"people": [p for p in people if not p["archived_at"]], "archived": [p for p in people if p["archived_at"]],
            "cfg": logic.settings(conn), "notice": db.get_setting(conn, "notice", ""), "exports": list(EXPORTS),
            "export_labels": EXPORT_LABELS, "page_tools": PAGE_TOOLS, "today": today.isoformat(),
            "week_ago": (today - timedelta(days=7)).isoformat(), "square_on": bool(config.SQUARE_TOKEN),
            "history_before": local(hb, "%Y-%m-%d") if hb else "", "danger": DANGER,
            "info": {t[0]: {"label": t[1], "tip": t[2], "section": s} for s, items in TOOLS for t in items}}


# ---- page -------------------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def tools_page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    t = form_ctx(conn)
    return render(request, "tools.html", tools=TOOLS, people=t["people"], archived=t["archived"], cfg=t["cfg"], notice=t["notice"],
                  exports=t["exports"], page_tools=PAGE_TOOLS, today=t["today"], week_ago=t["week_ago"], square_on=t["square_on"],
                  history_before=t["history_before"], index=index(), tool_ctx=t)


@router.post("/{tool}")
async def run_tool(tool: str, request: Request, who=Depends(staff), conn=Depends(get_conn)):
    handler = HANDLERS.get(tool)
    if handler is None:
        raise HTTPException(404, "Unknown tool")
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    # Run from its own page (a hidden "back"): answer there, not on Tools. Only ever a path on this site.
    request.state.tool_back = safe_back(form.get("back"))
    if request.state.tool_back and request.state.tool_back.split("?")[0] == "/tools":
        request.state.tool_back = None
    try:
        # In a worker thread: some tools wait on Square (Sync now waits for a running sync), and
        # that mustn't freeze the whole app for everyone else.
        def go():
            if tool in LOCKED_TOOLS:  # read balances and write under the write lock, so a double-click can't do it twice
                conn.execute("BEGIN IMMEDIATE")
            return handler(request, conn, who, form)
        result = await run_in_threadpool(go)
        conn.commit()
        return result
    except (ToolError, ValueError) as e:  # ValueError: rule checks in logic.py (e.g. a 0-point change)
        conn.rollback()
        return done(request, tool, f"{tool_names()[tool]}: {e}", "bad")


@router.get("/export/{name}.csv")
def export(name: str, who=Depends(staff), conn=Depends(get_conn)):
    if name not in EXPORTS:
        raise HTTPException(404)
    cur = conn.execute(EXPORTS[name])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([d[0] for d in cur.description])
    w.writerows(cur.fetchall())
    db.audit(conn, who, "export", None, name)
    conn.commit()
    stamp = datetime.now(config.TZ).strftime("%Y%m%d")
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=fizzy-{name}-{stamp}.csv"})


@router.get("/backup.db")
def download_backup(who=Depends(staff), conn=Depends(get_conn)):
    with tempfile.TemporaryDirectory() as tmp:
        path = db.backup(conn, tmp, 1)
        with open(path, "rb") as f:
            data = f.read()
    db.audit(conn, who, "download_backup")
    conn.commit()
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={os.path.basename(path)}"})


# ---- handlers -------------------------------------------------------------------------------
# Each takes (request, conn, staff, form) and returns a response. Raise ToolError for user mistakes.

def _int(form, key, minimum=None):
    try:
        v = int(form.get(key, "").strip())
    except ValueError:
        raise ToolError(f"'{form.get(key, '')}' isn't a whole number.")
    if minimum is not None and v < minimum:
        raise ToolError(f"Must be at least {minimum}.")
    if abs(v) > 1_000_000:
        raise ToolError(f"{v:,} is way too big.")
    return v


def _confirm(form, word):
    if form.get("confirm", "").strip().upper() != word:
        raise ToolError(f"Type {word} to confirm.")


def h_bulk_points(request, conn, who, form):
    delta = _int(form, "delta")
    if abs(delta) > logic.MAX_POINTS_CHANGE:
        raise ToolError(f"The most in one go is {logic.MAX_POINTS_CHANGE:,} points.")
    if delta == 0:
        raise ToolError("Points can't be 0.")
    note = form.get("note", "").strip()
    if not note:
        raise ToolError("A reason is required.")
    if form.get("everyone"):
        targets, missing = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL AND kind IS NULL").fetchall(), []
    else:
        targets, missing = resolve_many(conn, form.get("people", ""))
    if not targets:
        raise ToolError("Nobody selected. " + " ".join(missing))
    for c in targets:
        logic.adjust_points(conn, c["id"], delta, note, who)
    msg = f"{delta:+d} points to {len(targets)} customer(s)."
    if missing:
        msg += " Not found: " + " | ".join(missing)
    return done(request, "bulk_points", msg)


def h_bonus_points(request, conn, who, form):
    try:
        mult = float(form.get("multiplier") or 0)
    except ValueError:
        raise ToolError("Multiplier must be a number, e.g. 2.")
    if mult <= 1:
        raise ToolError("Multiplier must be more than 1 (e.g. 2 for double points).")
    start, end = day_start(form.get("start")), day_start(form.get("end"), end=True)
    note = f"x{mult:g} bonus {form['start']} to {form['end']}" + (f" - {form['note'].strip()}" if form.get("note", "").strip() else "")
    total = given = 0
    for cid, pts in logic.purchases_in(conn, start, end).items():
        extra = round(pts * (mult - 1))
        if extra <= 0:
            continue
        logic.adjust_points(conn, cid, extra, note, who)
        total += extra
        given += 1
    return done(request, "bonus_points", f"Gave {total} bonus points to {given} customer(s). (Don't run the same period twice.)")


def h_transfer_points(request, conn, who, form):
    src, dst = resolve(conn, form.get("from")), resolve(conn, form.get("to"))
    if src["id"] == dst["id"]:
        raise ToolError("Pick two different customers.")
    pts = _int(form, "points", 1)
    have = logic.points_balance(conn, src["id"])
    if pts > have:
        raise ToolError(f"{src['name']} only has {have} points.")
    note = form.get("note", "").strip() or "Points moved"
    logic.adjust_points(conn, src["id"], -pts, f"{note} (to {dst['name']})", who)
    logic.adjust_points(conn, dst["id"], pts, f"{note} (from {src['name']})", who)
    return done(request, "transfer_points", f"Moved {pts} points from {src['name']} to {dst['name']}.")


def h_reset_points(request, conn, who, form):
    _confirm(form, "RESET")
    note = form.get("note", "").strip() or "Points reset"
    rows = conn.execute(
        "SELECT customer_id, SUM(delta) AS bal FROM points_ledger GROUP BY customer_id HAVING bal != 0"
    ).fetchall()
    for r in rows:
        logic.adjust_points(conn, r["customer_id"], -r["bal"], note, who)
    db.audit(conn, who, "points_reset_all", None, f"{len(rows)} customers: {note}")
    return done(request, "reset_points", f"Reset points for {len(rows)} customer(s).")


def h_pause_tabs(request, conn, who, form):
    paused = form.get("paused") == "1"
    db.set_setting(conn, "tabs_paused", "1" if paused else "0")
    db.audit(conn, who, "tabs_paused" if paused else "tabs_unpaused", None, form.get("note", ""))
    return done(request, "pause_tabs", "All tabs paused." if paused else "Tabs back on.")


def h_revoke_all(request, conn, who, form):
    _confirm(form, "REVOKE")
    note = form.get("note", "").strip() or "Turned off all tabs"
    rows = conn.execute("SELECT id FROM customers WHERE tab_decision IS NULL AND archived_at IS NULL").fetchall()
    for r in rows:
        logic.set_tab_decision(conn, r["id"], "revoked", note, who)
    return done(request, "revoke_all", f"Turned off {len(rows)} tab(s).")


def h_manual_charge(request, conn, who, form):
    c = resolve(conn, form.get("customer"))
    amt = cents(form.get("amount"))
    if amt <= 0:
        raise ToolError("Amount must be more than $0.")
    items = form.get("items", "").strip() or "Manual charge"
    logic.add_tab_charge(conn, c, amt, f"{items} (entered by hand)", db.now_iso(), who)
    return done(request, "manual_charge", f"Charged {money(amt)} to {c['name']}'s tab. Only use this if the sale isn't in Square with the Fizzy Tab discount - if it is, Fizzy already has it.")


def h_write_off(request, conn, who, form):
    c = resolve(conn, form.get("customer"))
    owing = logic.tab_ledger(conn, c["id"])["owing_cents"]
    amt = cents(form["amount"]) if form.get("amount", "").strip() else owing
    note = form.get("note", "").strip()
    if not note:
        raise ToolError("A reason is required.")
    if amt <= 0 or amt > owing:
        raise ToolError(f"{c['name']} owes {money(owing)}; write off between $0.01 and that.")
    logic.add_repayment(conn, c["id"], amt, "writeoff", note, who)
    return done(request, "write_off", f"Wrote off {money(amt)} for {c['name']}.")


def h_extend_due(request, conn, who, form):
    c = resolve(conn, form.get("customer"))
    days = _int(form, "days", 1)
    if days > 120:
        raise ToolError("That's too long. Give at most 120 days.")
    led = logic.tab_ledger(conn, c["id"])
    open_ids = [ch["id"] for ch in led["charges"] if ch["unpaid_cents"] > 0]
    if not open_ids:
        raise ToolError(f"{c['name']} has no unpaid charges.")
    base = datetime.now(timezone.utc)
    for ch in led["charges"]:
        if ch["id"] in open_ids:
            # Count from now for overdue charges, so the extension is real time to pay.
            start = max(logic.parse_ts(ch["due_at"]), base)
            conn.execute("UPDATE tab_charges SET due_at = ? WHERE id = ?",
                         ((start + timedelta(days=days)).isoformat(timespec="seconds"), ch["id"]))
    db.audit(conn, who, "extend_due", c["id"], f"+{days} days on {len(open_ids)} charge(s) {form.get('note', '')}".strip())
    return done(request, "extend_due", f"Gave {c['name']} {days} more day(s) on {len(open_ids)} charge(s).")


def h_test_accounts(request, conn, who, form):
    n = _int(form, "n", 1)
    if n > 20:
        raise ToolError("20 at a time is plenty.")
    made, i = [], 0
    while len(made) < n:
        i += 1
        if conn.execute("SELECT 1 FROM customers WHERE email = ?", (f"test{i}@test.invalid",)).fetchone():
            continue
        cur = conn.execute("INSERT INTO customers(name, email, created_at, kind) VALUES (?, ?, ?, 'test')",
                           (f"Test Account {i}", f"test{i}@test.invalid", db.now_iso()))
        made.append(cur.lastrowid)
    db.audit(conn, who, "test_accounts", None, f"made {n}")
    if n == 1:
        return back(request, f"/customers/{made[0]}", "Made a test account. Do whatever you like with it.")
    return done(request, "test_accounts", f"Made {n} test accounts. Search \"Test Account\" to find them.")


def h_delete_test_accounts(request, conn, who, form):
    _confirm(form, "DELETE")
    ids = [r[0] for r in conn.execute("SELECT id FROM customers WHERE kind = 'test'")]
    if not ids:
        raise ToolError("There aren't any test accounts.")
    marks = ",".join("?" * len(ids))
    real = conn.execute(f"SELECT name FROM customers WHERE merged_into IN ({marks}) AND kind IS NOT 'test'", ids).fetchall()
    if real:  # their history lives on the test account now; deleting it would lose it
        raise ToolError("Real customers were merged into a test account (" + ", ".join(r[0] for r in real[:5]) +
                        "), so nothing was deleted. Turn that account into a normal one instead.")
    # Square sales put on a test account go back to "no customer" rather than vanishing.
    conn.execute(f"UPDATE square_payments SET customer_id = NULL WHERE customer_id IN ({marks})", ids)
    for table in ("redemptions", "points_ledger", "tab_repayments", "tab_charges", "tab_decisions", "square_aliases",
                  "challenge_scans", "prizes", "qr_logins", "claim_waits"):
        conn.execute(f"DELETE FROM {table} WHERE customer_id IN ({marks})", ids)
    # A hunt played as a guest and moved onto a test account: back to just that phone's.
    conn.execute(f"UPDATE guest_finishes SET customer_id = NULL WHERE customer_id IN ({marks})", ids)
    conn.execute(f"UPDATE guest_scans SET customer_id = NULL WHERE customer_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM customers WHERE id IN ({marks})", ids)
    db.audit(conn, who, "test_accounts_deleted", None, f"{len(ids)} deleted")
    return done(request, "delete_test_accounts", f"Deleted {len(ids)} test account(s).")


def h_add_customer(request, conn, who, form):
    name = " ".join(form.get("name", "").split())
    email = form.get("email", "").strip().lower()
    if not name or "@" not in email:
        raise ToolError("Name and email are required.")
    try:
        cur = conn.execute("INSERT INTO customers(name, email, created_at) VALUES (?, ?, ?)", (name, email, db.now_iso()))
    except sqlite3.IntegrityError:
        raise ToolError(f"{email} already exists.")
    db.audit(conn, who, "customer_add", cur.lastrowid, f"{name} <{email}>")
    return back(request, f"/customers/{cur.lastrowid}", f"Added {name}.")


def h_merge(request, conn, who, form):
    keep, dup = resolve(conn, form.get("keep")), resolve(conn, form.get("dup"))
    if keep["id"] == dup["id"]:
        raise ToolError("Pick two different customers.")
    if (keep["kind"] == "test") != (dup["kind"] == "test"):
        raise ToolError("Can't merge a test account with a real customer.")
    # A rule paid to both (the same badge, or the same hunt prize): keep's stays, the duplicate's goes.
    conn.execute("DELETE FROM points_ledger WHERE customer_id = ? AND rule_key IS NOT NULL AND rule_key IN "
                 "(SELECT rule_key FROM points_ledger WHERE customer_id = ? AND rule_key IS NOT NULL)", (dup["id"], keep["id"]))
    for table in ("points_ledger", "tab_charges", "tab_repayments", "square_payments", "tab_decisions", "audit_log",
                  "square_aliases", "redemptions"):
        conn.execute(f"UPDATE {table} SET customer_id = ? WHERE customer_id = ?", (keep["id"], dup["id"]))
    _merge_challenges(conn, keep["id"], dup["id"])
    conn.execute("UPDATE customers SET merged_into = ? WHERE merged_into = ?", (keep["id"], dup["id"]))  # earlier merges
    # Square profiles: keep's stays primary; the duplicate's becomes an alias, so its future sales
    # (and the next customer sync) still land on the kept customer.
    if keep["square_customer_id"] is None:
        conn.execute("UPDATE customers SET square_customer_id = NULL WHERE id = ?", (dup["id"],))
        conn.execute("UPDATE customers SET square_customer_id = ? WHERE id = ?", (dup["square_customer_id"], keep["id"]))
    elif dup["square_customer_id"]:
        conn.execute("INSERT OR REPLACE INTO square_aliases(square_customer_id, customer_id) VALUES (?, ?)",
                     (dup["square_customer_id"], keep["id"]))
    # Tab approval: if only the duplicate had a decision, the kept customer takes it over.
    if keep["tab_decision"] is None and dup["tab_decision"] is not None:
        conn.execute("UPDATE customers SET tab_decision = ?, tab_decision_note = ?, tab_decision_by = ?, tab_decision_at = ? "
                     "WHERE id = ?", (dup["tab_decision"], dup["tab_decision_note"], dup["tab_decision_by"],
                                      dup["tab_decision_at"], keep["id"]))
    # Student site passcode: likewise, if only the duplicate had one.
    if keep["passcode_enc"] is None and dup["passcode_enc"] is not None:
        conn.execute("UPDATE customers SET passcode_enc = ?, passcode_set_at = ? WHERE id = ?",
                     (dup["passcode_enc"], dup["passcode_set_at"], keep["id"]))
    if not keep["score_adjust"] and dup["score_adjust"]:
        conn.execute("UPDATE customers SET score_adjust = ? WHERE id = ?", (dup["score_adjust"], keep["id"]))
    conn.execute("UPDATE customers SET square_customer_id = NULL, archived_at = ?, merged_into = ?, tab_decision = NULL, "
                 "passcode_enc = NULL, passcode_set_at = NULL WHERE id = ?", (db.now_iso(), keep["id"], dup["id"]))
    # Their tab money and charges now sit together: the points have to match the combined tab.
    logic.reconcile_tab_points(conn, keep["id"], staff=who)
    logic.reconcile_tab_points(conn, dup["id"], staff=who)
    db.audit(conn, who, "merge", keep["id"], f"merged #{dup['id']} {dup['name']} <{dup['email']}> into this customer")
    return back(request, f"/customers/{keep['id']}", f"Merged {dup['name']} into {keep['name']}.")


def _merge_challenges(conn, keep, dup):
    """Hunt finds and prizes move to the kept customer. One prize per challenge: if both won it, keep the one
    already handed over (else the earlier), so a drink isn't given twice or lost."""
    conn.execute("INSERT OR IGNORE INTO challenge_scans(customer_id, rule_id, step_code, scanned_at) "
                 "SELECT ?, rule_id, step_code, scanned_at FROM challenge_scans WHERE customer_id = ?", (keep, dup))
    conn.execute("DELETE FROM challenge_scans WHERE customer_id = ?", (dup,))
    for p in conn.execute("SELECT * FROM prizes WHERE customer_id = ?", (dup,)).fetchall():
        k = conn.execute("SELECT * FROM prizes WHERE customer_id = ? AND rule_id = ?", (keep, p["rule_id"])).fetchone()
        if k is None:
            conn.execute("UPDATE prizes SET customer_id = ? WHERE id = ?", (keep, p["id"]))
            continue
        better = p if (p["given_at"] and not k["given_at"]) or (bool(p["given_at"]) == bool(k["given_at"]) and p["won_at"] < k["won_at"]) else k
        if better is p:
            conn.execute("UPDATE prizes SET won_at = ?, given_at = ?, given_by = ? WHERE id = ?",
                         (p["won_at"], p["given_at"], p["given_by"], k["id"]))
        conn.execute("DELETE FROM prizes WHERE id = ?", (p["id"],))
    conn.execute("UPDATE guest_finishes SET customer_id = ? WHERE customer_id = ?", (keep, dup))
    conn.execute("UPDATE guest_scans SET customer_id = ? WHERE customer_id = ?", (keep, dup))


def h_bulk_archive(request, conn, who, form):
    found, missing = resolve_many(conn, form.get("people", ""))
    for c in found:
        conn.execute("UPDATE customers SET archived_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
        db.audit(conn, who, "archive", c["id"], form.get("note", ""))
    msg = f"Archived {len(found)} customer(s)."
    if missing:
        msg += " Not found: " + " | ".join(missing)
    return done(request, "bulk_archive", msg)


def h_restore(request, conn, who, form):
    if form.get("all"):
        found = conn.execute("SELECT * FROM customers WHERE archived_at IS NOT NULL").fetchall()
        missing = []
    else:
        found, missing = resolve_many(conn, form.get("people", ""), archived_ok=True)
    for c in found:
        conn.execute("UPDATE customers SET archived_at = NULL WHERE id = ?", (c["id"],))
        db.audit(conn, who, "restore", c["id"])
    msg = f"Restored {len(found)} customer(s)."
    if missing:
        msg += " Not found: " + " | ".join(missing)
    return done(request, "restore", msg)


def h_takings(request, conn, who, form):
    start, end = day_start(form.get("start")), day_start(form.get("end"), end=True)
    rows = []
    for r in conn.execute(
        "SELECT kind, tender, COUNT(*) AS n, SUM(amount_cents) AS total FROM square_payments "
        "WHERE created_at >= ? AND created_at < ? AND kind != 'void' GROUP BY kind, tender ORDER BY kind, total DESC", (start, end)
    ):
        label = {"sale": "Square: sales", "tab_charge": "Put on tabs (not money)", "tab_repayment": "Square: tab repayments"}[r["kind"]]
        rows.append([label, r["tender"], r["n"], money(r["total"])])
    for r in conn.execute(
        "SELECT SUM(r.amount_cents) AS total, COUNT(*) AS n FROM square_refunds r "
        "WHERE r.created_at >= ? AND r.created_at < ? AND r.square_refund_id NOT LIKE 'return:%'",
        (start, end),
    ):
        if r["n"]:
            rows.append(["Square: refunds", "", r["n"], money(-r["total"])])
    for r in conn.execute(
        "SELECT SUM(amount_cents) AS total, COUNT(*) AS n FROM square_refunds WHERE created_at >= ? AND created_at < ? "
        "AND square_refund_id LIKE 'return:%'", (start, end),
    ):
        if r["n"]:
            rows.append(["Returned from tabs (not money)", "", r["n"], money(-r["total"])])
    for r in conn.execute(
        "SELECT method, COUNT(*) AS n, SUM(amount_cents) AS total FROM tab_repayments WHERE created_at >= ? AND created_at < ? "
        "AND square_payment_id IS NULL AND customer_id NOT IN (SELECT id FROM customers WHERE kind = 'test') GROUP BY method", (start, end)
    ):
        label = "Fizzy: debts written off" if r["method"] == "writeoff" else "Fizzy: repayments recorded without Square"
        rows.append([label, r["method"], r["n"], money(r["total"])])
    return report(request, f"Takings {_nice_day(form['start'])}" + ("" if form['start'] == form['end'] else f" to {_nice_day(form['end'])}"), ["What", "Tender / method", "Count", "Total"], rows,
                  note="Square sales are what was paid at the time (tab sales were discounted, so they add $0). "
                       "Square tab repayments are tabs paid off through Square. Fizzy repayments were recorded by hand "
                       "without Square - add those to the till count. Write-offs aren't money.")


def _balances(conn):
    out = []
    for c in conn.execute("SELECT DISTINCT c.* FROM customers c JOIN tab_charges t ON t.customer_id = c.id "
                          "UNION SELECT DISTINCT c.* FROM customers c JOIN tab_repayments r ON r.customer_id = c.id"):
        led = logic.tab_ledger(conn, c["id"])
        if led["balance_cents"] != 0:
            open_due = [ch["due_at"] for ch in led["charges"] if ch["unpaid_cents"] > 0]
            out.append((c, led, min(open_due) if open_due else None))
    return out


def h_chase(request, conn, who, form):
    rows = [b for b in _balances(conn) if b[1]["owing_cents"] > 0 and b[0]["kind"] != "test"]
    rows.sort(key=lambda b: b[2] or "")
    from .web import local
    table = [[cust_link(c), c["email"], money(led["owing_cents"]),
              money(led["overdue_cents"]) if led["overdue_cents"] else "", local(due, "%a %-d %b")]
             for c, led, due in rows]
    overdue_emails = ", ".join(c["email"] for c, led, _ in rows if led["has_overdue"])
    all_emails = ", ".join(c["email"] for c, _, _ in rows)
    return report(request, "Who to chase", ["Customer", "Email", "Owing", "Overdue", "Oldest due"], table,
                  copy=[("Overdue emails", overdue_emails), ("Everyone owing", all_emails)])


def h_balances(request, conn, who, form):
    rows = sorted(_balances(conn), key=lambda b: -b[1]["balance_cents"])
    table = [[cust_link(c),
              money(led["balance_cents"]) if led["balance_cents"] > 0 else "",
              money(-led["balance_cents"]) if led["balance_cents"] < 0 else "",
              money(led["overdue_cents"]) if led["overdue_cents"] else ""] for c, led, _ in rows]
    return report(request, "All balances", ["Customer", "Owes", "In credit", "Overdue"], table)


def h_leaderboard(request, conn, who, form):
    n = _int(form, "n", 1)
    rows = conn.execute(
        "SELECT c.id, c.name, SUM(p.delta) AS pts FROM customers c JOIN points_ledger p ON p.customer_id = c.id "
        "WHERE c.archived_at IS NULL AND c.kind IS NOT 'test' GROUP BY c.id HAVING pts > 0 ORDER BY pts DESC LIMIT ?", (n,)
    ).fetchall()
    return report(request, f"Top {n} by points", ["#", "Customer", "Points"],
                  [[i + 1, cust_link(r), r["pts"]] for i, r in enumerate(rows)])


def h_raffle(request, conn, who, form):
    n = _int(form, "n", 1)
    pool_kind = form.get("pool", "bought")
    if pool_kind == "bought":
        start, end = day_start(form.get("start")), day_start(form.get("end"), end=True)
        pool = conn.execute(
            "SELECT c.id, c.name, (SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = c.id) AS pts "
            "FROM customers c WHERE c.archived_at IS NULL AND c.kind IS NOT 'test' AND (EXISTS (SELECT 1 FROM points_ledger p WHERE p.customer_id = c.id "
            "AND p.reason = 'purchase' AND p.created_at >= ? AND p.created_at < ? AND (SELECT SUM(q.delta) FROM points_ledger q "
            "WHERE q.square_payment_id = p.square_payment_id AND q.reason IN ('purchase', 'refund')) > 0) "  # not refunded
            "OR EXISTS (SELECT 1 FROM tab_charges t WHERE t.customer_id = c.id AND t.charged_at >= ? AND t.charged_at < ? "
            "AND t.amount_cents > t.refunded_cents))", (start, end, start, end)
        ).fetchall()
    else:
        pool = conn.execute(
            "SELECT c.id, c.name, COALESCE(SUM(p.delta), 0) AS pts FROM customers c LEFT JOIN points_ledger p "
            "ON p.customer_id = c.id WHERE c.archived_at IS NULL AND c.kind IS NOT 'test' GROUP BY c.id"
        ).fetchall()
        if pool_kind == "points":
            pool = [p for p in pool if p["pts"] > 0]
    weighted = bool(form.get("weighted"))
    if weighted:
        pool = [p for p in pool if p["pts"] > 0]
    if not pool:
        raise ToolError("Nobody in the draw.")
    rng, remaining, winners = secrets.SystemRandom(), list(pool), []
    for _ in range(min(n, len(remaining))):
        if weighted:
            pick = rng.choices(remaining, weights=[p["pts"] for p in remaining])[0]
        else:
            pick = rng.choice(remaining)
        winners.append(pick)
        remaining.remove(pick)
    db.audit(conn, who, "raffle", None, f"pool={pool_kind} weighted={weighted} entrants={len(pool)} winners="
             + ", ".join(w["name"] for w in winners))
    return report(request, f"Winners ({len(pool)} in the draw{', weighted by points' if weighted else ''})",
                  ["#", "Winner", "Points"],
                  [[i + 1, cust_link(w), w["pts"]] for i, w in enumerate(winners)],
                  note="Recorded in the activity log.")


def h_sync_now(request, conn, who, form):
    if not config.SQUARE_TOKEN:
        raise ToolError("Square isn't configured (SQUARE_ACCESS_TOKEN).")
    return _background(request, "sync_now", "Syncing with Square", lambda c: square.sync_job(square.sync(c, wait=True, catalog=True)), who)


def h_resync(request, conn, who, form):
    if not config.SQUARE_TOKEN:
        raise ToolError("Square isn't configured (SQUARE_ACCESS_TOKEN).")
    square.request_resync(conn, day_start(form.get("start")))
    db.audit(conn, who, "square_resync", None, f"from {form.get('start')}")
    conn.commit()
    return _background(request, "resync", f"Re-syncing from {form['start']}",
                       lambda c: square.sync_job(square.sync(c, wait=True), f"Re-sync from {form['start']}: "), who)


def _background(request, tool, name, fn, who):
    from . import jobs
    if not jobs.start(name, fn, who):
        raise ToolError("Something's already running (see the bar at the top). Wait for it to finish.")
    return done(request, tool, f"{name}. You can keep using Fizzy; the bar at the top says when it's done.")


def h_history(request, conn, who, form):
    if not config.SQUARE_TOKEN:
        raise ToolError("Square isn't configured (SQUARE_ACCESS_TOKEN).")
    launch = day_start(form.get("launch"))
    start = day_start(form.get("start"))
    if start >= launch:
        raise ToolError("Import from a date before the launch date.")
    db.set_setting(conn, "history_before", launch)
    square.request_resync(conn, start)
    db.audit(conn, who, "square_history", None, f"from {form.get('start')}, launch {form.get('launch')}")
    conn.commit()
    return _background(request, "history", f"Importing sales history from {form['start']}",
                       lambda c: square.sync_job(square.sync(c, wait=600), f"History imported (sales before {form['launch']} earn nothing): "), who)


def h_find_payment(request, conn, who, form):
    pid = form.get("payment_id", "").strip()
    row = conn.execute("SELECT 1 FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone()
    if not row:
        raise ToolError(f"Fizzy hasn't seen payment '{pid}'. Try Sync now, or Re-sync from its date.")
    return RedirectResponse(f"/square/payments/{pid}", status_code=303)


def h_integrity(request, conn, who, form):
    checks = []
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    checks.append(["Database file", ic, "ok" if ic == "ok" else "PROBLEM - restore from a backup"])

    def count(label, sql, fix):
        n = conn.execute(sql).fetchone()[0]
        checks.append([label, n, "ok" if n == 0 else fix])

    count("Square payments with no customer", "SELECT COUNT(*) FROM square_payments WHERE customer_id IS NULL AND kind != 'void' "
          "AND no_account IS NULL",
          link("Attach customers in Square, or link their Square IDs", "/square?show=unlinked"))
    count("Customers with negative points",
          "SELECT COUNT(*) FROM (SELECT customer_id FROM points_ledger GROUP BY customer_id HAVING SUM(delta) < 0)",
          "Usually refunds after points were spent - adjust if needed")
    count("Unreviewed rule-breaking tab charges", "SELECT COUNT(*) FROM tab_charges WHERE breach IS NOT NULL AND breach_ack_by IS NULL",
          link("Review on the dashboard", "/"))
    count("Active customers not linked to Square",
          "SELECT COUNT(*) FROM customers WHERE square_customer_id IS NULL AND archived_at IS NULL AND kind IS NOT 'test'",
          link("Import this CSV into Square, then sync", "/export/square-customers.csv"))
    err = db.get_setting(conn, "square_last_error")
    checks.append(["Last Square sync error", err or "none", "ok" if not err else "Check the Square page"])
    return report(request, "Health check", ["Check", "Result", "Status"], [[a, b, c] for a, b, c in checks])


def h_notice(request, conn, who, form):
    text = form.get("notice", "").strip()
    db.set_setting(conn, "notice", f"{text}\n— {who}, {datetime.now(config.TZ):%a %-d %b %H:%M}" if text else "")
    db.audit(conn, who, "notice", None, text or "(cleared)")
    msg = "Noticeboard updated." if text else "Noticeboard cleared."
    if _back(request) == "/":  # from the dashboard: the note itself is at the top, no need to open the form again
        return back(request, "/", msg)
    return done(request, "notice", msg)


def h_sign_out_all(request, conn, who, form):
    _confirm(form, "SIGN OUT")
    db.set_setting(conn, "session_gen", secrets.token_hex(4))
    db.audit(conn, who, "sign_out_all")
    request.session.clear()  # this device too, now: the sign-in page shouldn't still show who was on
    return RedirectResponse("/login", status_code=303)


LOCKED_TOOLS = {"bulk_points", "transfer_points", "manual_charge", "write_off", "extend_due", "merge", "reset_points",
                "test_accounts", "delete_test_accounts"}
HANDLERS = {name[2:]: fn for name, fn in globals().items() if name.startswith("h_")}
assert set(HANDLERS) == {t for t in tool_names() if t not in ("export", "download_backup")}, "TOOLS and handlers out of sync"
