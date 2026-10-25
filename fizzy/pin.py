"""Three levels of who-can-do-what: anyone signed in, the PIN, and the owner password.

- The PIN is set on the Settings page.
- The owner password is FIZZY_ADMIN_PASSWORD in .env, so it can't be changed or removed from
  inside Fizzy. It works anywhere the PIN does.
- The owner picks the level for each kind of action on the Settings page ("Who can do what"),
  plus how big a points or money change has to be before it needs a higher level.

Pop-ups only ever say "Enter passcode", and a passcode that isn't high enough is just wrong.
Forgot the PIN? On the home PC: docker compose exec fizzy python -m fizzy clear-pin
"""
import hashlib
import hmac
import json
import re
import secrets
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from . import config, db
from .web import back, get_conn, staff

router = APIRouter()

OPEN, PIN, OWNER = 0, 1, 2
LEVELS = {"open": OPEN, "pin": PIN, "owner": OWNER}
LEVEL_LABELS = {"open": "Anyone signed in", "pin": "PIN", "owner": "Owner only"}
UNLOCK_SECONDS = {PIN: 600, OWNER: 300}
FAIL_LIMIT, FAIL_WINDOW = 5, 600
_fails = []

# Never asked, whatever the settings say: signing in and out, the student site, devices joining.
FREE = [r"^/(login|logout|switch)$", r"^/me(/|$)", r"^/enrol/", r"^/square/webhook$", r"^/settings/pin/unlock$"]
# Always the owner: the locks themselves.
LOCKED = [r"^/settings/(pin|permissions)$"]

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
               r"|resync|sign_out_all)$")], "owner"),
    ("downloads", "Download exports and backups",
     [("GET", r"^/tools/(export/|backup\.db)"), ("POST", r"^/tools/download_backup$")], "owner"),
    ("other_tools", "Other tools: reports, raffle, noticeboard, find a payment", [("POST", r"^/tools/")], "pin"),
    ("import", "Import the school list", [("POST", r"^/import$")], "owner"),
    ("settings", "Settings: tab rules and devices", [("POST", r"^/settings")], "owner"),
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
    try:
        return abs(float(str(v).replace(",", "").replace("$", "").strip()))
    except ValueError:
        return 0


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
    if form is not None and re.search(BIG_PATHS, path):
        big = (form.get("everyone") or _number(form.get("delta")) > perms["big_points"]
               or _number(form.get("points")) > perms["big_points"] or _number(form.get("amount")) > perms["big_dollars"])
        if big:
            level = max(level, LEVELS[perms["big"]])
    return _eff(conn, level)


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
    conn = db.connect()
    try:
        if required(conn, request.method, path, form) > unlocked(request, conn):
            raise PinRequired()
    finally:
        conn.close()


# ---- pages -----------------------------------------------------------------------------------

def _locked_out(now):
    _fails[:] = [t for t in _fails if t > now - FAIL_WINDOW]
    return len(_fails) >= FAIL_LIMIT


@router.post("/settings/pin/unlock")
def unlock(request: Request, pin: str = Form(""), need: int = Form(PIN), who=Depends(staff), conn=Depends(get_conn)):
    now = time.time()
    if _locked_out(now):
        return JSONResponse({"ok": False, "error": "Too many wrong tries. Wait 10 minutes."})
    rank = OWNER if is_owner(pin) else PIN if check(conn, pin) else OPEN
    if rank < max(need, PIN):  # a PIN where the owner's is needed is just a wrong passcode
        _fails.append(now)
        db.audit(conn, who, "passcode_wrong", None, "")
        conn.commit()
        left = FAIL_LIMIT - len(_fails)
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
