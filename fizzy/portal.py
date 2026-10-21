"""Customer portal: students look up their own points, tab and rewards.

Staff set a passcode for a student at the stall (staff type it in, once), which is how we know
the passcode belongs to the right person. The student then signs in on the
public host (FIZZY_PUBLIC_HOST, e.g. my.cookedcoke.co) with their name or student number and
that passcode. The public host only serves these pages; everything staff-side is 404 there.

Read-only on purpose: nothing a student does here changes their account.
"""
import asyncio
import base64
import hashlib
import hmac
import re
import time
import unicodedata

from fastapi import APIRouter, Depends, Form, HTTPException, Request
import anyio
from cryptography.fernet import Fernet, InvalidToken
from fastapi.responses import RedirectResponse

from . import config, logic, rewards
from .db import audit, now_iso
from .web import get_conn, staff, templates

router = APIRouter()

PASSCODE_MIN = 4
PASSCODE_MAX = 64
SESSION_HOURS = 12                 # signed in this long at most, then sign in again
FAIL_WINDOW = 900
LIMIT_PER_NAME = 5                 # wrong passcodes per name per 15 minutes
LIMIT_PER_IP = 20                  # per network per 15 minutes
MAX_IN_FLIGHT = 8                  # sign-ins being checked at once; more get "busy"
FAIL_DELAY = 1
_fails = {}
_in_flight = 0
_check_slots = None                # its own small thread limiter, so a flood can't starve the staff pages

# ---- passcodes --------------------------------------------------------------------------------

# Encrypted, not hashed: staff can look a passcode up for a student who forgot it. Someone with
# only the database (or a backup) can't read them; someone with the database and .env can.

def _fernet():
    key = hashlib.sha256(b"fizzy-passcode:" + config.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_passcode(passcode):
    return _fernet().encrypt(passcode.encode()).decode()


def reveal_passcode(stored):
    """The passcode, or None (nothing set, or the secret key has changed since)."""
    if not stored:
        return None
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def check_passcode(passcode, stored):
    actual = reveal_passcode(stored)
    return actual is not None and hmac.compare_digest(passcode.encode(), actual.encode())


def passcode_problem(passcode):
    """Why a passcode isn't allowed, or None. Deliberately relaxed: staff type it in for the student."""
    if len(passcode) < PASSCODE_MIN:
        return f"Passcode must be at least {PASSCODE_MIN} characters."
    if len(passcode) > PASSCODE_MAX:
        return f"Passcode can be at most {PASSCODE_MAX} characters."
    return None


def set_passcode(conn, customer, passcode, staff_name):
    passcode = passcode.strip()
    problem = passcode_problem(passcode)
    if problem:
        raise ValueError(problem)
    conn.execute("UPDATE customers SET passcode_enc = ?, passcode_set_at = ? WHERE id = ?",
                 (encrypt_passcode(passcode), now_iso(), customer["id"]))
    audit(conn, staff_name, "passcode_set", customer["id"], "")


def remove_passcode(conn, customer, staff_name):
    conn.execute("UPDATE customers SET passcode_enc = NULL, passcode_set_at = NULL WHERE id = ?", (customer["id"],))
    audit(conn, staff_name, "passcode_removed", customer["id"], "")


# ---- sign-in ----------------------------------------------------------------------------------

def _norm(s):
    return " ".join((s or "").lower().replace("’", "'").split())


MAX_CANDIDATES = 3                 # one sign-in checks at most this many accounts


def _letters(s):
    """Just the letters, lower case, accents dropped: O'Donnell = odonnell = O Donnell."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _name_keys(name):
    """Ways a student might type their own name: in order, surname first, or without middle names."""
    parts = [p for p in re.split(r"[\s\-]+", unicodedata.normalize("NFKD", name or "")) if _letters(p)]
    keys = {_letters(name), _letters("".join(reversed(parts)))}
    if len(parts) > 2:
        keys |= {_letters(parts[0] + parts[-1]), _letters(parts[-1] + parts[0])}
    return keys


def _distance(a, b, cap):
    """Edit distance (with swapped letters counting as one), giving up past cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def _allowed_typos(typed):
    return 0 if len(typed) < 6 else 1 if len(typed) < 11 else 2


def candidates(conn, who):
    """Accounts this sign-in could be for. Forgiving about how a name is typed (case, spaces,
    apostrophes, hyphens, accents, surname first, middle names left out, a typo or two), but
    it never lists or suggests names: the passcode still has to be right, and the reply is the
    same either way. A student number must be exact."""
    typed = _letters(who)
    if len(typed) < 3:
        return []
    rows = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL AND merged_into IS NULL "
                        "AND passcode_enc IS NOT NULL").fetchall()
    exact = [r for r in rows if typed == _letters(r["email"].split("@")[0])]
    if exact:
        return exact[:MAX_CANDIDATES]
    cap, scored = _allowed_typos(typed), []
    for r in rows:
        d = min(_distance(typed, k, cap) for k in _name_keys(r["name"]))
        if d <= cap:
            scored.append((d, r["id"], r))
    scored.sort(key=lambda t: t[:2])
    return [r for _, _, r in scored[:MAX_CANDIDATES]]


def authenticate(conn, who, passcode, found_candidates=None):
    found = None
    for c in (candidates(conn, who) if found_candidates is None else found_candidates) or [None]:
        if check_passcode(passcode, c["passcode_enc"] if c else None) and found is None:
            found = c
    return found


def _recent(key, now):
    return [t for t in _fails.get(key, []) if t > now - FAIL_WINDOW]


def _buckets(request, who):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if ":" in ip:
        ip = ":".join(ip.split(":")[:4]) + "::/64"
    return [(("name", _letters(who)), LIMIT_PER_NAME), (("ip", ip), LIMIT_PER_IP)]


def _session_marker(customer):
    """Changes when the passcode changes or is removed, which signs out existing sessions."""
    return hashlib.sha256((customer["passcode_enc"] or "").encode()).hexdigest()[:16]


def current(request, conn):
    me = request.session.get("me")
    if not me or time.time() - me.get("at", 0) > SESSION_HOURS * 3600:
        return None
    c = conn.execute("SELECT * FROM customers WHERE id = ? AND archived_at IS NULL AND merged_into IS NULL",
                     (me.get("id"),)).fetchone()
    if c is None or not c["passcode_enc"] or me.get("v") != _session_marker(c):
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
    global _in_flight, _check_slots
    now = time.time()
    found = candidates(conn, who)
    # Limited per typed name, per network, and per account (a name and a student number are the
    # same account, so they share one allowance).
    buckets = _buckets(request, who) + [(("acct", c["id"]), LIMIT_PER_NAME) for c in found]
    if any(len(_recent(k, now)) >= limit for k, limit in buckets if k[0] != "acct"):
        return _page(request, "me_login.html", who=who, live=rewards.active(conn),
                     error="Too many tries. Wait 15 minutes and try again.")
    # A locked *account* gets the ordinary "don't match" answer (even for the right passcode).
    # Saying "too many tries" would tell anyone which name goes with which student number.
    acct_locked = any(len(_recent(k, now)) >= limit for k, limit in buckets if k[0] == "acct")
    if _in_flight >= MAX_IN_FLIGHT:
        return _page(request, "me_login.html", who=who, live=rewards.active(conn),
                     error="Lots of people are signing in right now. Try again in a moment.")
    # Count the attempt before checking it, so a burst of parallel guesses can't all get past
    # the limits before the first one fails. Taken back off if it turns out to be right.
    for k, _ in buckets:
        _fails[k] = _recent(k, now) + [now]
    if len(_fails) > 5000:
        for k in [k for k, v in _fails.items() if not v or v[-1] < now - FAIL_WINDOW]:
            del _fails[k]
    _check_slots = _check_slots or anyio.CapacityLimiter(2)
    _in_flight += 1
    try:
        c = await anyio.to_thread.run_sync(authenticate, conn, who, passcode.strip()[:PASSCODE_MAX],
                                           [] if acct_locked else found, limiter=_check_slots)
    finally:
        _in_flight -= 1
    if c is None:
        await asyncio.sleep(FAIL_DELAY)
        return _page(request, "me_login.html", who=who, live=rewards.active(conn),
                     error="That name and passcode don't match. No passcode yet? Ask at the Cooked Coke stall.")
    for k, _ in buckets:
        if k[0] != "ip" and now in _fails.get(k, []):
            _fails[k].remove(now)
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
# down the board. Only students with a passcode (who signed up at the stall) are on it, names
# are shortened to first name + last initial, and it's only shown to signed-in students.

def short_name(name):
    parts = name.split()
    return parts[0] if len(parts) < 2 else f"{parts[0]} {parts[-1][0].upper()}."


def leaderboard(conn, since=None, limit=10, me=None):
    rows = conn.execute(
        "SELECT c.id, c.name, SUM(p.delta) AS pts FROM points_ledger p JOIN customers c ON c.id = p.customer_id "
        "WHERE p.reason NOT IN ('redeem', 'redeem_undo') AND c.archived_at IS NULL AND c.merged_into IS NULL "
        "AND c.passcode_enc IS NOT NULL "
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
def staff_set_passcode(request: Request, cid: int, passcode: str = Form(""), remove: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    if remove:
        remove_passcode(conn, c, who)
        msg = "Passcode removed - they can't sign in to the student site until a new one is set."
    else:
        try:
            set_passcode(conn, c, passcode, who)
        except ValueError as e:
            request.session["flash"] = str(e)
            return RedirectResponse(f"/customers/{cid}", status_code=303)
        site = config.PUBLIC_HOST or "the student site"
        msg = f"Passcode set. {c['name'].split(' ')[0]} can now sign in at {site}."
    conn.commit()
    request.session["flash"] = msg
    return RedirectResponse(f"/customers/{cid}", status_code=303)
