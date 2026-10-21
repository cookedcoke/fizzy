"""Customer portal: students look up their own points, tab and rewards.

Staff set a passcode for a student at the stall (the student types it on the counter device),
which is how we know the passcode belongs to the right person. The student then signs in on the
public host (FIZZY_PUBLIC_HOST, e.g. my.cookedcoke.co) with their name or student number and
that passcode. The public host only serves these pages; everything staff-side is 404 there.

Read-only on purpose: nothing a student does here changes their account.
"""
import asyncio
import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from . import config, logic, rewards
from .db import audit, now_iso
from .web import get_conn, staff, templates

router = APIRouter()

PASSCODE_MIN = 6
PASSCODE_MAX = 64
SESSION_HOURS = 12                 # signed in this long at most, then sign in again
FAIL_WINDOW = 900
LIMIT_PER_NAME = 5                 # wrong passcodes per name per 15 minutes
LIMIT_PER_IP = 20                  # per network per 15 minutes
LIMIT_ALL = 200                    # everyone together: stops one passcode being tried against every name
FAIL_DELAY = 1
_fails = {}

# ---- passcodes --------------------------------------------------------------------------------

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


def hash_passcode(passcode):
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${hashlib.scrypt(passcode.encode(), salt=salt, **_SCRYPT).hex()}"


def check_passcode(passcode, stored):
    """Constant-ish time: always does one scrypt, even with nothing stored."""
    try:
        _, salt, digest = (stored or "scrypt$00$00").split("$")
        got = hashlib.scrypt(passcode.encode(), salt=bytes.fromhex(salt), **_SCRYPT).hex()
    except ValueError:
        return False
    return bool(stored) and hmac.compare_digest(got, digest)


def passcode_problem(passcode, customer):
    """Why a passcode isn't allowed, or None."""
    if len(passcode) < PASSCODE_MIN:
        return f"Passcode must be at least {PASSCODE_MIN} characters."
    if len(passcode) > PASSCODE_MAX:
        return f"Passcode can be at most {PASSCODE_MAX} characters."
    low = passcode.lower()
    if len(set(low)) == 1 or low in "0123456789012345678909876543210" or low in "abcdefghijklmnopqrstuvwxyz":
        return "That passcode is too easy to guess - pick something less obvious."
    if low in (customer["email"].split("@")[0].lower(), _norm(customer["name"]).replace(" ", "")):
        return "Don't use the student number or name as the passcode."
    return None


def set_passcode(conn, customer, passcode, confirm, staff_name):
    if passcode != confirm:
        raise ValueError("The two passcodes don't match.")
    problem = passcode_problem(passcode, customer)
    if problem:
        raise ValueError(problem)
    conn.execute("UPDATE customers SET passcode_hash = ?, passcode_set_at = ? WHERE id = ?",
                 (hash_passcode(passcode), now_iso(), customer["id"]))
    audit(conn, staff_name, "passcode_set", customer["id"], "")


def remove_passcode(conn, customer, staff_name):
    conn.execute("UPDATE customers SET passcode_hash = NULL, passcode_set_at = NULL WHERE id = ?", (customer["id"],))
    audit(conn, staff_name, "passcode_removed", customer["id"], "")


# ---- sign-in ----------------------------------------------------------------------------------

def _norm(s):
    return " ".join((s or "").lower().replace("’", "'").split())


def candidates(conn, who):
    """Active customers matching a full name or student number exactly (no fuzzy matching here:
    the public site must not help anyone browse the directory)."""
    who = _norm(who)
    if not who:
        return []
    rows = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL AND merged_into IS NULL "
                        "AND passcode_hash IS NOT NULL").fetchall()
    return [r for r in rows if _norm(r["name"]) == who or r["email"].split("@")[0].lower() == who]


def authenticate(conn, who, passcode):
    found = None
    for c in candidates(conn, who) or [None]:
        if check_passcode(passcode, c["passcode_hash"] if c else None) and found is None:
            found = c
    return found


def _recent(key, now):
    return [t for t in _fails.get(key, []) if t > now - FAIL_WINDOW]


def _buckets(request, who):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if ":" in ip:
        ip = ":".join(ip.split(":")[:4]) + "::/64"
    return [(("name", _norm(who)), LIMIT_PER_NAME), (("ip", ip), LIMIT_PER_IP), (("all", "*"), LIMIT_ALL)]


def _session_marker(customer):
    """Changes when the passcode changes or is removed, which signs out existing sessions."""
    return hashlib.sha256((customer["passcode_hash"] or "").encode()).hexdigest()[:16]


def current(request, conn):
    me = request.session.get("me")
    if not me or time.time() - me.get("at", 0) > SESSION_HOURS * 3600:
        return None
    c = conn.execute("SELECT * FROM customers WHERE id = ? AND archived_at IS NULL AND merged_into IS NULL",
                     (me.get("id"),)).fetchone()
    if c is None or not c["passcode_hash"] or me.get("v") != _session_marker(c):
        return None
    return c


def _page(request, name, **ctx):
    response = templates.TemplateResponse(request, name, ctx)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _url(path):
    """Links on the public host drop the /me prefix (the middleware adds it back)."""
    return path if config.PUBLIC_HOST else "/me" + path


templates.env.globals["me_url"] = _url


@router.get("/me")
@router.get("/me/")
def home(request: Request, conn=Depends(get_conn)):
    c = current(request, conn)
    if c is None:
        return _page(request, "me_login.html", error=None, who="", live=rewards.active(conn))
    s = logic.customer_summary(conn, c)
    month, month_me = leaderboard(conn, since=month_start(), me=c["id"])
    alltime, alltime_me = leaderboard(conn, me=c["id"])
    return _page(request, "me.html", c=c, s=s, offers=rewards.offers_for(conn, c["id"], s["points"]),
                 activity=activity(conn, c["id"]), boards=[("This month", month, month_me), ("All time", alltime, alltime_me)])


@router.post("/me/login")
async def login(request: Request, who: str = Form(""), passcode: str = Form(""), conn=Depends(get_conn)):
    now = time.time()
    buckets = _buckets(request, who)
    if any(len(_recent(k, now)) >= limit for k, limit in buckets):
        return _page(request, "me_login.html", who=who, live=rewards.active(conn),
                     error="Too many tries. Wait 15 minutes and try again.")
    c = await run_in_threadpool(authenticate, conn, who, passcode[:PASSCODE_MAX])   # scrypt is slow on purpose
    if c is None:
        for k, _ in buckets:
            _fails[k] = _recent(k, now) + [now]
        if len(_fails) > 5000:
            for k in [k for k, v in _fails.items() if not v or v[-1] < now - FAIL_WINDOW]:
                del _fails[k]
        await asyncio.sleep(FAIL_DELAY)
        return _page(request, "me_login.html", who=who, live=rewards.active(conn),
                     error="That name and passcode don't match. No passcode yet? Ask at the Cooked Coke stall.")
    _fails.pop(buckets[0][0], None)
    request.session["me"] = {"id": c["id"], "v": _session_marker(c), "at": int(now)}
    return RedirectResponse(_url("/"), status_code=303)


@router.post("/me/logout")
def logout(request: Request):
    request.session.pop("me", None)
    return RedirectResponse(_url("/"), status_code=303)


REASONS = {
    "purchase": "Purchase", "refund": "Refund", "tab_repaid": "Tab paid off", "tab_refund": "Tab refund",
    "credit_used": "Tab paid from credit", "tab_adjust": "Tab correction", "redeem": "Reward",
    "redeem_undo": "Reward undone", "manual": "Adjustment", "bonus": "Bonus",
}


def activity(conn, customer_id, limit=15):
    """Recent points and tab events in plain words. Staff notes and names are left out."""
    rows = []
    for p in conn.execute("SELECT * FROM points_ledger WHERE customer_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                          (customer_id, limit)):
        what = REASONS.get(p["reason"], "Adjustment")
        if p["reason"] in ("redeem", "redeem_undo") and p["note"]:
            what = p["note"]                       # "Redeemed: Free Coke" - written by Fizzy, not staff
        rows.append({"at": p["created_at"], "what": what, "points": p["delta"], "money": None})
    for t in conn.execute("SELECT charged_at, amount_cents FROM tab_charges WHERE customer_id = ? "
                          "ORDER BY charged_at DESC LIMIT ?", (customer_id, limit)):
        rows.append({"at": t["charged_at"], "what": "Put on tab", "points": None, "money": t["amount_cents"]})
    for r in conn.execute("SELECT created_at, amount_cents, method FROM tab_repayments WHERE customer_id = ? "
                          "ORDER BY created_at DESC LIMIT ?", (customer_id, limit)):
        rows.append({"at": r["created_at"], "what": "Tab written off" if r["method"] == "writeoff" else "Tab payment",
                     "points": None, "money": -r["amount_cents"]})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:limit]


# ---- leaderboard ------------------------------------------------------------------------------
# Ranked by points *earned* (not the balance), so spending points on a reward doesn't drop anyone
# down the board. Names are shortened to first name + last initial, and it's only shown to
# signed-in students - never to the open internet.

def short_name(name):
    parts = name.split()
    return parts[0] if len(parts) < 2 else f"{parts[0]} {parts[-1][0].upper()}."


def leaderboard(conn, since=None, limit=10, me=None):
    rows = conn.execute(
        "SELECT c.id, c.name, SUM(p.delta) AS pts FROM points_ledger p JOIN customers c ON c.id = p.customer_id "
        "WHERE p.reason NOT IN ('redeem', 'redeem_undo') AND c.archived_at IS NULL AND c.merged_into IS NULL "
        "AND p.created_at >= ? GROUP BY c.id HAVING pts > 0 ORDER BY pts DESC, MIN(p.created_at)",
        (since or "",)).fetchall()
    board, my_rank, rank, prev = [], None, 0, None
    for i, r in enumerate(rows, 1):
        if r["pts"] != prev:
            rank, prev = i, r["pts"]
        if r["id"] == me:
            my_rank = {"rank": rank, "points": r["pts"], "of": len(rows)}
        if i <= limit:
            board.append({"rank": rank, "name": short_name(r["name"]), "points": r["pts"], "me": r["id"] == me})
    return board, my_rank


def month_start():
    from datetime import datetime, timezone
    now = datetime.now(config.TZ)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")


# ---- staff side -------------------------------------------------------------------------------

@router.post("/customers/{cid}/passcode")
def staff_set_passcode(request: Request, cid: int, passcode: str = Form(""), confirm: str = Form(""),
                       remove: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    if remove:
        remove_passcode(conn, c, who)
        msg = "Passcode removed - they can't sign in to the student site until a new one is set."
    else:
        try:
            set_passcode(conn, c, passcode, confirm, who)
        except ValueError as e:
            request.session["flash"] = str(e)
            return RedirectResponse(f"/customers/{cid}", status_code=303)
        site = config.PUBLIC_HOST or "the student site"
        msg = f"Passcode set. {c['name'].split(' ')[0]} can now sign in at {site}."
    conn.commit()
    request.session["flash"] = msg
    return RedirectResponse(f"/customers/{cid}", status_code=303)
