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
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import config, customers, db, logic, square
from .web import back, cents, get_conn, money, render, staff

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
        ("pause_tabs", "Pause all tabs", "Stop anyone using a tab for now. Turn it back on and everyone's approval is still there."),
        ("approve_eligible", "Approve all eligible", "Approve everyone who's spent enough and hasn't been approved or rejected yet."),
        ("revoke_all", "Revoke all tabs", "Take tabs off everyone, like at the end of the year."),
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

EXPORTS = {
    "customers": "SELECT id, name, email, square_customer_id, tab_decision, tab_decision_note, created_at, archived_at FROM customers",
    "points": "SELECT p.*, c.name FROM points_ledger p JOIN customers c ON c.id = p.customer_id",
    "tab_charges": "SELECT t.*, c.name FROM tab_charges t JOIN customers c ON c.id = t.customer_id",
    "repayments": "SELECT r.*, c.name FROM tab_repayments r JOIN customers c ON c.id = r.customer_id",
    "square_payments": "SELECT square_payment_id, square_order_id, square_customer_id, customer_id, kind, amount_cents, tender, items, created_at FROM square_payments",
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
    dt = datetime.combine(day + timedelta(days=1) if end else day, time(), tzinfo=config.TZ)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def link(text, href):
    """A report cell that links somewhere. Plain strings are escaped as usual."""
    return {"text": text, "href": href}


def cust_link(c):
    return link(c["name"], f"/customers/{c['id']}")


def report(request, title, headers, rows, note=None, copy=None):
    return render(request, "tool_result.html", title=title, headers=headers, rows=rows, note=note, copy=copy)


def done(request, tool, msg):
    return back(request, f"/tools#{tool}", msg)


def tool_names():
    return {t[0]: t[1] for _, items in TOOLS for t in items}


# ---- page -------------------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def tools_page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    people = conn.execute("SELECT name, email, archived_at FROM customers ORDER BY name").fetchall()
    archived = [p for p in people if p["archived_at"]]
    today = datetime.now(config.TZ).date().isoformat()
    week_ago = (datetime.now(config.TZ).date() - timedelta(days=7)).isoformat()
    return render(request, "tools.html", tools=TOOLS, people=[p for p in people if not p["archived_at"]],
                  archived=archived, cfg=logic.settings(conn), notice=db.get_setting(conn, "notice", ""),
                  exports=list(EXPORTS), today=today, week_ago=week_ago, square_on=bool(config.SQUARE_TOKEN))


@router.post("/{tool}")
async def run_tool(tool: str, request: Request, who=Depends(staff), conn=Depends(get_conn)):
    handler = HANDLERS.get(tool)
    if handler is None:
        raise HTTPException(404, "Unknown tool")
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        # In a worker thread: some tools wait on Square (Sync now waits for a running sync), and
        # that mustn't freeze the whole app for everyone else.
        result = await run_in_threadpool(handler, request, conn, who, form)
        conn.commit()
        return result
    except (ToolError, ValueError) as e:  # ValueError: rule checks in logic.py (e.g. a 0-point change)
        conn.rollback()
        return done(request, tool, f"{tool_names()[tool]}: {e}")


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
        targets, missing = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL").fetchall(), []
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


def h_approve_eligible(request, conn, who, form):
    cfg = logic.settings(conn)
    rows = conn.execute(
        "SELECT c.id FROM customers c JOIN points_ledger p ON p.customer_id = c.id WHERE c.tab_decision IS NULL "
        "AND c.archived_at IS NULL GROUP BY c.id HAVING SUM(p.spend_cents) >= ?", (cfg["unlock_spend_cents"],)
    ).fetchall()
    note = form.get("note", "").strip() or "Bulk approved"
    for r in rows:
        logic.set_tab_decision(conn, r["id"], "approved", note, who)
    return done(request, "approve_eligible", f"Approved {len(rows)} customer(s).")


def h_revoke_all(request, conn, who, form):
    _confirm(form, "REVOKE")
    note = form.get("note", "").strip() or "Bulk revoked"
    rows = conn.execute("SELECT id FROM customers WHERE tab_decision = 'approved'").fetchall()
    for r in rows:
        logic.set_tab_decision(conn, r["id"], "revoked", note, who)
    return done(request, "revoke_all", f"Revoked {len(rows)} tab(s).")


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
    start = conn.execute("SELECT COUNT(*) FROM customers WHERE kind = 'test'").fetchone()[0]
    made = []
    for i in range(start + 1, start + n + 1):
        while conn.execute("SELECT 1 FROM customers WHERE email = ?", (f"test{i}@test.invalid",)).fetchone():
            i += 100
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
    # Square sales put on a test account go back to "no customer" rather than vanishing.
    conn.execute(f"UPDATE square_payments SET customer_id = NULL WHERE customer_id IN ({marks})", ids)
    for table in ("redemptions", "points_ledger", "tab_repayments", "tab_charges", "tab_decisions", "square_aliases"):
        conn.execute(f"DELETE FROM {table} WHERE customer_id IN ({marks})", ids)
    conn.execute(f"UPDATE customers SET merged_into = NULL WHERE merged_into IN ({marks})", ids)
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
    for table in ("points_ledger", "tab_charges", "tab_repayments", "square_payments", "tab_decisions", "audit_log",
                  "square_aliases", "redemptions"):
        conn.execute(f"UPDATE {table} SET customer_id = ? WHERE customer_id = ?", (keep["id"], dup["id"]))
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
    conn.execute("UPDATE customers SET square_customer_id = NULL, archived_at = ?, merged_into = ?, tab_decision = NULL, "
                 "passcode_enc = NULL, passcode_set_at = NULL WHERE id = ?", (db.now_iso(), keep["id"], dup["id"]))
    # Their tab money and charges now sit together: the points have to match the combined tab.
    logic.reconcile_tab_points(conn, keep["id"], staff=who)
    logic.reconcile_tab_points(conn, dup["id"], staff=who)
    db.audit(conn, who, "merge", keep["id"], f"merged #{dup['id']} {dup['name']} <{dup['email']}> into this customer")
    return back(request, f"/customers/{keep['id']}", f"Merged {dup['name']} into {keep['name']}.")


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
        "WHERE created_at >= ? AND created_at < ? GROUP BY kind, tender ORDER BY kind, total DESC", (start, end)
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
        "AND square_payment_id IS NULL GROUP BY method", (start, end)
    ):
        label = "Fizzy: debts written off" if r["method"] == "writeoff" else "Fizzy: repayments recorded without Square"
        rows.append([label, r["method"], r["n"], money(r["total"])])
    return report(request, f"Takings {form['start']} to {form['end']}", ["What", "Tender / method", "Count", "Total"], rows,
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
              money(led["overdue_cents"]) if led["overdue_cents"] else "", local(due, "%a %d %b")]
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
            "AND p.reason = 'purchase' AND p.created_at >= ? AND p.created_at < ?) OR EXISTS (SELECT 1 FROM tab_charges t "
            "WHERE t.customer_id = c.id AND t.charged_at >= ? AND t.charged_at < ?))", (start, end, start, end)
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
    return done(request, "sync_now", square.describe_sync(square.sync(conn, wait=True)))


def h_resync(request, conn, who, form):
    if not config.SQUARE_TOKEN:
        raise ToolError("Square isn't configured (SQUARE_ACCESS_TOKEN).")
    square.request_resync(conn, day_start(form.get("start")))
    db.audit(conn, who, "square_resync", None, f"from {form.get('start')}")
    conn.commit()
    return done(request, "resync", f"Re-sync from {form['start']}: " + square.describe_sync(square.sync(conn, wait=True)))


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

    count("Square payments with no customer", "SELECT COUNT(*) FROM square_payments WHERE customer_id IS NULL",
          link("Attach customers in Square, or link their Square IDs", "/square?show=unlinked"))
    count("Customers with negative points",
          "SELECT COUNT(*) FROM (SELECT customer_id FROM points_ledger GROUP BY customer_id HAVING SUM(delta) < 0)",
          "Usually refunds after points were spent - adjust if needed")
    count("Unreviewed rule-breaking tab charges", "SELECT COUNT(*) FROM tab_charges WHERE breach IS NOT NULL AND breach_ack_by IS NULL",
          link("Review on the dashboard", "/"))
    count("Active customers not linked to Square",
          "SELECT COUNT(*) FROM customers WHERE square_customer_id IS NULL AND archived_at IS NULL AND kind IS NOT 'test'",
          link("Import this CSV into Square, then sync", "/export/square-customers.csv"))
    count("Approved tabs on archived customers",
          "SELECT COUNT(*) FROM customers WHERE archived_at IS NOT NULL AND tab_decision = 'approved'",
          "Revoke them from the customer page")
    err = db.get_setting(conn, "square_last_error")
    checks.append(["Last Square sync error", err or "none", "ok" if not err else "Check the Square page"])
    return report(request, "Health check", ["Check", "Result", "Status"], [[a, b, c] for a, b, c in checks])


def h_notice(request, conn, who, form):
    text = form.get("notice", "").strip()
    db.set_setting(conn, "notice", f"{text}\n— {who}, {datetime.now(config.TZ):%a %d %b %H:%M}" if text else "")
    db.audit(conn, who, "notice", None, text or "(cleared)")
    return done(request, "notice", "Noticeboard updated." if text else "Noticeboard cleared.")


def h_sign_out_all(request, conn, who, form):
    _confirm(form, "SIGN OUT")
    db.set_setting(conn, "session_gen", secrets.token_hex(4))
    db.audit(conn, who, "sign_out_all")
    return RedirectResponse("/login", status_code=303)


HANDLERS = {name[2:]: fn for name, fn in globals().items() if name.startswith("h_")}
assert set(HANDLERS) == {t for t in tool_names() if t not in ("export", "download_backup")}, "TOOLS and handlers out of sync"
