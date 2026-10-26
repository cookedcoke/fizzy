"""Three levels of who-can-do-what: anyone signed in, the PIN, and the owner password.

- The PIN is set on the Settings page.
- The owner password is FIZZY_ADMIN_PASSWORD in .env, so it can't be changed or removed from
  inside Fizzy. It works anywhere the PIN does.
- The owner picks the level for each kind of action on the Settings page ("Who can do what"),
  plus how big a points or money change has to be before it needs a higher level.

Pop-ups only ever say "Enter passcode", and a passcode that isn't high enough is just wrong.
Forgot the PIN? On the home PC: docker compose exec -u fizzy fizzy python -m fizzy clear-pin
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import config, db
from .web import back, get_conn, staff

router = APIRouter()

OPEN, PIN, OWNER = 0, 1, 2
LEVELS = {"open": OPEN, "pin": PIN, "owner": OWNER}
LEVEL_LABELS = {"open": "Anyone signed in", "pin": "PIN", "owner": "Owner only"}
UNLOCK_SECONDS = {PIN: 600, OWNER: 300}
FAIL_LIMIT, FAIL_LIMIT_ALL, FAIL_WINDOW = 5, 30, 600  # per browser, and across everyone
_fails = {}

# Never asked, whatever the settings say: signing in and out, the student site, devices joining.
FREE = [r"^/(login|logout|switch)$", r"^/me(/|$)", r"^/enrol/", r"^/square/webhook$", r"^/settings/pin/unlock$"]
# Always the owner: the locks themselves.
LOCKED = [r"^/settings/(pin|permissions)$", r"^/customers/\d+/kind$"]

# (id, label, [(method, path)], default level). First match wins, so specific ones go first.
ACTIONS = [
    ("claim", "Claim Square sales, and say whose sale it was",
     [("POST", r"^/customers/\d+/claim$"), ("POST", r"^/square/payments/[^/]+/assign$")], "open"),
    ("sync", "Sync with Square, refresh the catalogue",
     [("POST", r"^/square/sync$"), ("POST", r"^/tools/sync_now$"), ("POST", r"^/profit/refresh$")], "open"),
    ("warnings", "Dismiss warnings, back up now", [("POST", r"^/breach/"), ("POST", r"^/settings/backup$")], "open"),
    ("points", "Give or take points", [("POST", r"^/customers/\d+/points$"), ("POST", r"^/tools/(bulk_points|transfer_points)$")], "pin"),
    ("redeem", "Redeem rewards, and undo them", [("POST", r"^/customers/\d+/redeem/"), ("POST", r"^/redemptions/")], "pin"),
    ("rewards", "Set up and end rewards", [("POST", r"^/rewards")], "pin"),
    ("events", "Double points days", [("POST", r"^/events")], "pin"),
    ("tab_decisions", "Approve, reject or revoke someone's tab", [("POST", r"^/customers/\d+/tab$")], "pin"),
    ("tab_money", "Tab money by hand: repayments, charges, write-offs, more time",
     [("POST", r"^/customers/\d+/repay$"), ("POST", r"^/tools/(manual_charge|write_off|extend_due)$")], "pin"),
    ("customers", "Add and edit customers", [("POST", r"^/customers/\d+/edit$"), ("POST", r"^/tools/add_customer$")], "pin"),
    ("passcodes", "Set or read back student passcodes", [("POST", r"^/customers/\d+/passcode")], "pin"),
    ("money", "Add restocks and payouts", [("POST", r"^/money/(restock|payout)$")], "pin"),
    ("money_delete", "Delete restocks and payouts", [("POST", r"^/money/(restock|payout)/\d+/delete$")], "owner"),
    ("costs", "Item costs", [("POST", r"^/profit$")], "pin"),
    ("square_kind", "Change how a Square payment is counted", [("POST", r"^/square/payments/[^/]+/kind$")], "pin"),
    ("school_tools", "Whole-school tools: reset points, revoke all, approve all, pause tabs, bonus for a period, "
                     "archive/restore many, merge, re-sync, sign everyone out",
     [("POST", r"^/tools/(reset_points|revoke_all|approve_eligible|pause_tabs|bonus_points|bulk_archive|restore|merge"
               r"|resync|sign_out_all|delete_test_accounts)$")], "owner"),
    ("downloads", "Download exports and backups",
     [("GET", r"^/tools/(export/|backup\.db)"), ("POST", r"^/tools/download_backup$")], "owner"),
    ("other_tools", "Other tools: reports, raffle, noticeboard, find a payment", [("POST", r"^/tools/")], "pin"),
    ("import", "Import the school list", [("POST", r"^/import$")], "owner"),
    ("settings", "Settings: tab rules and devices", [("POST", r"^/settings")], "owner"),
    # Not paths: these apply to whoever an action is aimed at (see _targets).
    ("admin_accounts", "Changes to admin accounts (claiming their sales is fine)", [], "owner"),
    ("test_accounts", "Changes to test accounts (redeeming rewards still needs its usual level)", [], "open"),
]
OTHER = "pin"  # anything not listed (new pages) until it's given its own row
# A points or money change by hand bigger than these needs the "big" level, whatever its row says.
BIG_PATHS = r"^/(customers/\d+/(points|repay)|tools/)"
DEFAULTS = {"big_points": 500, "big_dollars": 50, "big": "owner"}


class PinRequired(Exception):
    pass


# ---- the passcodes ---------------------------------------------------------------------------

def _hash(pin, salt):
    return hashlib.scrypt(pin.encode(), salt=salt.encode(), n=2 ** 14, r=8, p=1).hex()


def stored(conn):
    return db.get_setting(conn, "admin_pin", "") or ""


def check(conn, pin):
    s = stored(conn)
    if not s:
        return False
    salt, h = s.split("$", 1)
    return hmac.compare_digest(_hash(pin or "", salt), h)


def admin_on():
    return bool(config.ADMIN_PASSWORD)


def is_owner(code):
    return admin_on() and hmac.compare_digest((code or "").encode(), config.ADMIN_PASSWORD.encode())


def validate_new(pin):
    pin = (pin or "").strip()
    if not re.fullmatch(r"\d{4,8}", pin):
        raise ValueError("The PIN must be 4 to 8 digits.")
    if is_owner(pin):
        raise ValueError("Pick a PIN that isn't the owner password.")
    return pin


def set_pin(conn, pin, who):
    salt = secrets.token_hex(8)
    db.set_setting(conn, "admin_pin", f"{salt}${_hash(pin, salt)}" if pin else "")
    db.audit(conn, who, "admin_pin", None, "set" if pin else "removed")


# ---- who can do what -------------------------------------------------------------------------

def permissions(conn):
    """{action id: level name, big_points, big_dollars, big}, with the owner's changes applied."""
    out = {a[0]: a[3] for a in ACTIONS} | DEFAULTS
    try:
        saved = json.loads(db.get_setting(conn, "permissions", "") or "{}")
    except ValueError:
        saved = {}
    for k, v in saved.items():
        if k in out and (v in LEVELS if k not in ("big_points", "big_dollars") else isinstance(v, int)):
            out[k] = v
    return out


def _eff(conn, level):
    """A level that can actually be asked for: with no owner password the PIN tops out, and with
    no PIN, things set to "PIN" are open."""
    if level == OWNER and not admin_on():
        level = PIN
    if level == PIN and not stored(conn):
        level = OPEN
    return level


def _number(v):
    """How big a typed amount is, read the way the handlers read it. Anything that isn't plainly a
    number counts as huge, so an odd format can't sneak past the big-change rule."""
    v = re.sub(r"[\s,$]", "", str(v or ""))
    if not v:
        return 0
    try:
        n = abs(float(v))
    except ValueError:
        return float("inf")
    return n if n == n else float("inf")


def required(conn, method, path, form=None):
    """The level this request needs, as a number (0 = just being signed in)."""
    if any(re.search(p, path) for p in FREE):
        return OPEN
    if any(re.search(p, path) for p in LOCKED):
        return _eff(conn, OWNER)
    perms = permissions(conn)
    level = None
    for aid, _, pats, _ in ACTIONS:
        if any(m == method and re.search(p, path) for m, p in pats):
            level = LEVELS[perms[aid]]
            break
    if level is None:
        if method != "POST":
            return OPEN
        level = LEVELS[OTHER]
    payment = None
    m = re.match(r"^/square/payments/([^/]+)/(assign|kind)$", path)
    if m:
        base = m.group(1).removesuffix(":sale")
        payment = conn.execute("SELECT COALESCE(MAX(customer_id), 0) AS owner, COALESCE(SUM(amount_cents), 0) AS cents, "
                               "MAX(kind != 'sale') AS tab FROM square_payments WHERE square_payment_id IN (?, ?)",
                               (base, base + ":sale")).fetchone()
        if m.group(2) == "assign" and payment["owner"]:
            # Moving a sale that already belongs to someone isn't claiming: it takes from them.
            level = max(level, LEVELS[perms["square_kind"]])
    if form is not None and not re.search(r"^/customers/\d+/claim$", path):
        kinds, complete = _targets(conn, path, form)
        if "admin" in kinds:
            level = max(level, LEVELS[perms["admin_accounts"]])
        elif complete and kinds and all(k == "test" for k in kinds) and not NOT_PLAY.search(path) \
                and not (path.endswith("/edit") and (form.get("square_customer_id") or "").strip()):
            return _eff(conn, LEVELS[perms["test_accounts"]])
    if form is not None and (re.search(BIG_PATHS, path) or payment or path == "/events"):
        people = max(1, len([x for x in re.split(r"[\n;]+", form.get("people") or "") if x.strip()]))
        big = (form.get("everyone") or _number(form.get("delta")) * people > perms["big_points"]
               or _number(form.get("points")) > perms["big_points"] or _number(form.get("amount")) > perms["big_dollars"])
        if path == "/tools/write_off" and not (form.get("amount") or "").strip():
            big = True  # blank means "everything they owe", whatever that is
        if payment and (payment["tab"] or payment["cents"] > perms["big_dollars"] * 100):
            big = True  # re-labelling or moving tab money changes what someone owes
        if path == "/events":  # bonus points for everyone: big unless it's a plain one-off double points day
            try:
                days = (date.fromisoformat(form.get("end") or form.get("start")) - date.fromisoformat(form.get("start"))).days
            except (TypeError, ValueError):
                days = 99
            big = bool(form.get("weekly")) or _number(form.get("multiplier") or 2) > 2 or days > 6
        if big:
            level = max(level, LEVELS[perms["big"]])
    return _eff(conn, level)


# Tools whose customers all come from the name fields below, so "all test accounts" can be trusted.
# Real-world value: never "anything goes", even on test accounts (free Cokes are real Cokes).
NOT_PLAY = re.compile(r"^/(customers/\d+/redeem/|redemptions/)")
PRECISE_TOOLS = r"^/tools/(bulk_points|transfer_points|manual_charge|write_off|extend_due|merge|bulk_archive|restore)$"


def _targets(conn, path, form):
    """(kinds of the customers an action is aimed at, whether that list is surely complete).
    From the page address, or the names typed into a tool. Any name that can't be looked up makes
    it incomplete, so the test-account level is never used on a guess."""
    ids, complete = set(), True
    m = re.match(r"^/customers/(\d+)/", path)
    if m:
        ids.add(int(m.group(1)))
    m = re.match(r"^/square/payments/([^/]+)/(assign|kind)$", path)
    if m:
        base = m.group(1).removesuffix(":sale")
        for r in conn.execute("SELECT customer_id FROM square_payments WHERE square_payment_id IN (?, ?) "
                              "AND customer_id IS NOT NULL", (base, base + ":sale")):
            ids.add(r[0])
        if m.group(2) == "assign" and form is not None and form.get("customer"):
            from . import tools
            try:
                ids.add(tools.resolve(conn, form["customer"], True)["id"])
            except Exception:
                complete = False
    m = re.match(r"^/redemptions/(\d+)/", path)
    if m:
        r = conn.execute("SELECT customer_id FROM redemptions WHERE id = ?", (int(m.group(1)),)).fetchone()
        if r:
            ids.add(r[0])
        else:
            complete = False
    if form is not None and path.startswith("/tools/"):
        from . import tools
        complete = complete and bool(re.search(PRECISE_TOOLS, path)) and not form.get("everyone") and not form.get("all")
        for k in ("customer", "from", "to", "keep", "dup"):
            if form.get(k):
                try:
                    ids.add(tools.resolve(conn, form[k], True)["id"])
                except Exception:  # not found / ambiguous: the tool itself says so
                    complete = False
        if form.get("people"):
            try:
                found, missing = tools.resolve_many(conn, form["people"], True)
                ids |= {c["id"] for c in found}
                complete = complete and not missing
            except Exception:
                complete = False
    if not ids:
        return [], False
    marks = ",".join("?" * len(ids))
    kinds = [r[0] for r in conn.execute(f"SELECT kind FROM customers WHERE id IN ({marks})", tuple(ids))]
    return kinds, complete and len(kinds) == len(ids)  # an id that doesn't exist isn't "all test accounts"


def customer_level(conn, kind, base):
    """The level for an action at `base` aimed at a customer of this kind (for the customer page)."""
    perms = permissions(conn)
    if kind == "admin":
        return _eff(conn, max(base, LEVELS[perms["admin_accounts"]]))
    if kind == "test":
        return _eff(conn, LEVELS[perms["test_accounts"]])
    return _eff(conn, base)


def unlocked(request, conn):
    """The level this browser has unlocked right now."""
    u = request.session.get("unlock") or {}
    if u.get("until", 0) < time.time():
        return OPEN
    if u.get("rank") == PIN and u.get("for") != stored(conn)[:12]:
        return OPEN  # the PIN was changed since
    return u.get("rank", OPEN)


def rules(conn):
    """For the browser, so it can ask before sending rather than after (the server checks again)."""
    perms, eff = permissions(conn), lambda lv: _eff(conn, lv)
    acts = [[m, p, eff(LEVELS[perms[aid]])] for aid, _, pats, _ in ACTIONS for m, p in pats]
    return {"free": FREE, "locked": [[p, eff(OWNER)] for p in LOCKED], "actions": acts, "other": eff(LEVELS[OTHER]),
            "big": {"paths": BIG_PATHS, "points": perms["big_points"], "dollars": perms["big_dollars"],
                    "level": eff(LEVELS[perms["big"]])}, "numeric": not admin_on()}


async def guard(request: Request):
    """App-wide dependency: refuse an action until a high enough passcode has been entered."""
    if not request.session.get("auth"):
        return
    path = request.url.path
    if any(re.search(p, path) for p in FREE):
        return
    form = await request.form() if request.method == "POST" else None

    def check():  # in a worker thread: the database can wait on a write, and that mustn't stall every request
        conn = db.connect()
        try:
            return required(conn, request.method, path, form) > unlocked(request, conn)
        finally:
            conn.close()
    if await run_in_threadpool(check):
        raise PinRequired()


# ---- pages -----------------------------------------------------------------------------------

def _browser(request):
    # The signed "known browser" cookie from sign-in, so one person typing wrong codes on
    # purpose only locks their own browser out (the overall cap still stops guessing).
    return request.cookies.get("fizzy_known") or (request.client.host if request.client else "?")


def _recent(key, now):
    _fails[key] = [t for t in _fails.get(key, []) if t > now - FAIL_WINDOW]
    return len(_fails[key])


@router.post("/settings/pin/unlock")
def unlock(request: Request, pin: str = Form(""), need: int = Form(PIN), who=Depends(staff), conn=Depends(get_conn)):
    now, me = time.time(), _browser(request)
    # The overall cap stops PIN guessing; the owner password still works through it, so nobody
    # can keep the owner locked out by typing wrong codes from lots of browsers.
    if _recent(me, now) >= FAIL_LIMIT or (_recent("*", now) >= FAIL_LIMIT_ALL and not is_owner(pin)):
        return JSONResponse({"ok": False, "error": "Too many wrong tries. Wait 10 minutes."})
    rank = OWNER if is_owner(pin) else PIN if check(conn, pin) else OPEN
    if rank < max(need, PIN):  # a PIN where the owner's is needed is just a wrong passcode
        _fails[me].append(now)
        _fails["*"].append(now)
        db.audit(conn, who, "passcode_wrong", None, "")
        conn.commit()
        left = FAIL_LIMIT - len(_fails[me])
        return JSONResponse({"ok": False, "error": "Wrong passcode." + (f" {left} more tries." if left <= 2 else "")})
    until = int(now + UNLOCK_SECONDS[rank])
    request.session["unlock"] = {"rank": rank, "until": until, "for": stored(conn)[:12]}
    if rank == OWNER:
        db.audit(conn, who, "owner_unlock", None, "")
        conn.commit()
    return JSONResponse({"ok": True, "rank": rank, "until": until})


@router.post("/settings/pin")
def change(request: Request, new: str = Form(""), remove: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    try:
        pin = "" if remove else validate_new(new)
    except ValueError as e:
        return back(request, "/settings#pin", str(e))
    set_pin(conn, pin, who)
    conn.commit()
    if pin and unlocked(request, conn) < PIN:
        request.session["unlock"] = {"rank": PIN, "until": int(time.time() + UNLOCK_SECONDS[PIN]), "for": stored(conn)[:12]}
    return back(request, "/settings#pin", "PIN set." if pin else "PIN removed.")


@router.post("/settings/permissions")
async def save_permissions(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    form = await request.form()
    perms = permissions(conn)
    for aid, *_ in ACTIONS:
        if form.get(aid) in LEVELS:
            perms[aid] = form[aid]
    if form.get("big") in LEVELS:
        perms["big"] = form["big"]
    for k, top in (("big_points", 1_000_000), ("big_dollars", 10_000)):
        try:
            n = int(float(str(form.get(k, "")).replace(",", "").replace("$", "")))
        except ValueError:
            return back(request, "/settings#permissions", "The limits must be whole numbers.")
        if not 0 <= n <= top:
            return back(request, "/settings#permissions", f"Keep that limit between 0 and {top:,}.")
        perms[k] = n
    db.set_setting(conn, "permissions", json.dumps(perms))
    db.audit(conn, who, "permissions", None, json.dumps(perms))
    conn.commit()
    return back(request, "/settings#permissions", "Saved who can do what.")
