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
from fastapi.responses import JSONResponse, RedirectResponse

from . import badges, config, events, logic, rewards, stats
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


MAX_CANDIDATES = 3                 # exact matches checked at most (two students with the same full name)
MAX_WHO = 100                      # longest name/number we'll look at


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
    typed = _letters(who.split("@")[0] if "@" in who else who)  # a whole school email works too
    if len(typed) < 3:
        return []
    rows = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL AND merged_into IS NULL "
                        "AND passcode_enc IS NOT NULL").fetchall()
    # Always scan everyone (same work whether or not the name exists, so timing doesn't tell).
    full, partial, cap, best, at_best = [], [], _allowed_typos(typed), None, []
    for r in rows:
        keys = _name_keys(r["name"])
        number = _letters(r["email"].split("@")[0])
        if typed in (number, number.removeprefix("rns")) or typed == _letters(r["name"]) or typed == _letters("".join(reversed(r["name"].split()))):
            full.append(r)
        elif typed in keys:
            partial.append(r)  # matched without middle names
        # A misspelling only ever counts for one account, and only if it's clearly the closest.
        d = min(_distance(typed, k, cap) for k in keys)
        if d > cap or (best is not None and d > best):
            continue
        if best is None or d < best:
            best, at_best = d, [r]
        else:
            at_best.append(r)
    if full:
        return full[:MAX_CANDIDATES]
    if partial:
        return partial if len(partial) == 1 else []
    return at_best if len(at_best) == 1 else []


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
        return _page(request, "me_login.html", error=None, who="", live=rewards.active(conn),
                     events_on=events.on(conn), events_soon=events.upcoming(conn))
    s = logic.customer_summary(conn, c)
    month, month_me = leaderboard(conn, since=month_start(), me=c["id"])
    alltime, alltime_me = leaderboard(conn, me=c["id"])
    return _page(request, "me.html", c=c, s=s, offers=rewards.offers_for(conn, c["id"], s["points"]),
                 activity=activity(conn, c["id"]), boards=[("This month", month, month_me), ("All time", alltime, alltime_me)],
                 me=badges.for_customer(conn, c["id"]), events_on=events.on(conn), events_soon=events.upcoming(conn))


@router.post("/me/login")
async def login(request: Request, who: str = Form(""), passcode: str = Form(""), conn=Depends(get_conn)):
    global _in_flight, _check_slots
    now = time.time()
    who = who[:MAX_WHO]
    # Per typed name and per network first: cheap, and checked before any searching.
    buckets = _buckets(request, who)
    if any(len(_recent(k, now)) >= limit for k, limit in buckets):
        return _page(request, "me_login.html", who=who, live=rewards.active(conn), events_on=events.on(conn), events_soon=events.upcoming(conn),
                     error="Too many tries. Wait 15 minutes and try again.")
    if _in_flight >= MAX_IN_FLIGHT:
        return _page(request, "me_login.html", who=who, live=rewards.active(conn), events_on=events.on(conn), events_soon=events.upcoming(conn),
                     error="Lots of people are signing in right now. Try again in a moment.")
    # Count the attempt before checking anything (no awaits in between), so a burst of parallel
    # guesses can't all get past the limits before the first one fails. Taken back off if right.
    for k, _ in buckets:
        _fails[k] = _recent(k, now) + [now]
    _check_slots = _check_slots or anyio.CapacityLimiter(2)
    _in_flight += 1
    try:
        # Name matching and passcode checks run off the event loop, a couple at a time, so a flood
        # of sign-ins can't slow the staff pages.
        found = await anyio.to_thread.run_sync(candidates, conn, who, limiter=_check_slots)
        # Per account too (a name and a student number are the same account, so they share one
        # allowance). A locked account gets the ordinary "don't match" answer, even for the right
        # passcode: saying "too many tries" would tell anyone which name goes with which number.
        acct = [(("acct", c["id"]), LIMIT_PER_NAME) for c in found]
        acct_locked = any(len(_recent(k, now)) >= limit for k, limit in acct)
        for k, _ in acct:
            _fails[k] = _recent(k, now) + [now]
        buckets += acct
        if len(_fails) > 5000:
            for k in [k for k, v in _fails.items() if not v or v[-1] < now - FAIL_WINDOW]:
                del _fails[k]
        c = await anyio.to_thread.run_sync(authenticate, conn, who, passcode.strip()[:PASSCODE_MAX],
                                           [] if acct_locked else found, limiter=_check_slots)
    finally:
        _in_flight -= 1
    if c is None:
        await asyncio.sleep(FAIL_DELAY)
        return _page(request, "me_login.html", who=who, live=rewards.active(conn), events_on=events.on(conn), events_soon=events.upcoming(conn),
                     error="That name and passcode don't match. No passcode yet? Ask at the Cooked Coke stall.")
    for k, _ in buckets:  # right in the end, so it doesn't count (the school shares one network)
        if now in _fails.get(k, []):
            _fails[k].remove(now)
    request.session["me"] = {"id": c["id"], "v": _session_marker(c), "at": int(now)}
    return RedirectResponse(_url("/"), status_code=303)


@router.get("/me/login")
def login_page_again():
    """Reloading after a sign-in (or the down page coming back) lands here: back to the start."""
    return RedirectResponse(_url("/"), status_code=303)


@router.get("/me/promos.json")
def promos(conn=Depends(get_conn)):
    """What's on, for the main website (cookedcoke.co) to show: live rewards and double points
    days now or in the next two weeks. Public on purpose - nothing about any student."""
    t = stats.today()
    cfg = logic.settings(conn)
    body = {
        "rewards": [{"name": r["name"], "points": r["points_cost"]} for r in rewards.active(conn)],
        "double_points": [{"name": e["name"], "multiplier": e["multiplier"], "start": e["start_date"], "end": e["end_date"],
                           "on_now": e["start_date"] <= t.isoformat() <= e["end_date"]}
                          for e in list(events.on(conn)) + list(events.upcoming(conn))],
        "student_site": f"https://{config.PUBLIC_HOST}/" if config.PUBLIC_HOST else None,
        "closed": [{"start": a, "end": b, "label": label} for a, b, label in logic.closed_days(conn) if b >= t.isoformat()],
        "rules": {"unlock_spend_cents": cfg["unlock_spend_cents"], "score_needed": cfg["tab_score_needed"], "due_days": cfg["tab_due_days"]},
    }
    return JSONResponse(body, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=60"})


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
        elif p["reason"] == "bonus" and p["event_id"] and p["note"]:
            what = f"Bonus: {p['note']}"           # the double points day's public name
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
        "AND c.passcode_enc IS NOT NULL AND c.kind IS NOT 'test' "
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

@router.post("/customers/{cid:int}/passcode/reveal")
def staff_reveal_passcode(cid: int, who=Depends(staff), conn=Depends(get_conn)):
    """A student's passcode, for staff to read out when they've forgotten it. Only fetched when
    staff press Show (never sitting in the page), and every look is logged."""
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    code = reveal_passcode(c["passcode_enc"])
    audit(conn, who, "passcode_viewed", cid, "")
    conn.commit()
    return JSONResponse({"passcode": code}, headers={"Cache-Control": "no-store"})


@router.post("/customers/{cid:int}/passcode")
def staff_set_passcode(request: Request, cid: int, passcode: str = Form(""), remove: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    if remove:
        remove_passcode(conn, c, who)
        msg = "Passcode removed."
    else:
        try:
            set_passcode(conn, c, passcode, who)
        except ValueError as e:
            request.session["flash"], request.session["flash_kind"] = str(e), "bad"
            return RedirectResponse(f"/customers/{cid}", status_code=303)
        site = config.PUBLIC_HOST or "the student site"
        msg = f"Passcode set. {c['name'].split(' ')[0]} can log in at {site}."
    conn.commit()
    request.session["flash"] = msg
    return RedirectResponse(f"/customers/{cid}", status_code=303)
