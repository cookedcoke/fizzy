"""Two optional locks on top of the staff sign-in.

- The PIN (set on the Settings page) for anything done by hand: giving points, rewards, tabs,
  money records, tools, settings. Square-linked and automatic things (claiming a sale, syncing)
  never ask.
- The owner password (FIZZY_ADMIN_PASSWORD in .env, so it can't be changed or removed from
  inside Fizzy) for the dangerous stuff: big points or money changes, the whole-school tools,
  deleting records, data downloads, imports and Settings, including the PIN itself.

Set it on the Settings page. Entering it unlocks this browser for a few minutes. Forgot it?
On the home PC: docker compose exec fizzy python -m fizzy clear-pin
"""
import hashlib
import hmac
import re
import secrets
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from . import config, db
from .web import back, get_conn, staff

router = APIRouter()

UNLOCK_SECONDS = 600
ADMIN_UNLOCK_SECONDS = 300
FAIL_LIMIT, FAIL_WINDOW = 5, 600
_fails = []

# Every staff POST needs the PIN except these: Square-linked and automatic things, signing in and
# out, and the student site. New pages are covered automatically. (The browser gets both lists.)
PROTECTED = [
    ("POST", r"^/"),
    ("GET", r"^/tools/(export/|backup\.db)"),
]
EXEMPT = [
    r"^/(login|logout|switch)$",
    r"^/me(/|$)",
    r"^/enrol/",
    r"^/customers/\d+/claim$",            # claiming a Square sale
    r"^/square/(sync|webhook)$",
    r"^/square/payments/[^/]+/assign$",   # saying whose Square sale it was
    r"^/tools/sync_now$",
    r"^/profit/refresh$",                 # re-read the catalogue from Square
    r"^/breach/\d+/ack$",                 # just dismisses a warning
    r"^/settings/backup$",
    r"^/settings/pin/unlock$",
]


# Owner password territory. Paths that need it outright:
ADMIN = [
    ("POST", r"^/tools/(reset_points|revoke_all|approve_eligible|pause_tabs|bonus_points|bulk_archive|restore|merge"
             r"|resync|sign_out_all|download_backup)$"),
    ("GET", r"^/tools/(export/|backup\.db)"),
    ("POST", r"^/settings"),
    ("POST", r"^/import$"),
    ("POST", r"^/money/(restock|payout)/\d+/delete$"),
]
# ...and changes by hand that are bigger than these (points either way, dollars).
ADMIN_LIMITED = r"^/(customers/\d+/(points|repay)|tools/)"
ADMIN_LIMITS = {"delta": 500, "points": 500, "amount": 50}


class PinRequired(Exception):
    def __init__(self, tier="pin"):
        self.tier = tier


def _hash(pin, salt):
    return hashlib.scrypt(pin.encode(), salt=salt.encode(), n=2 ** 14, r=8, p=1).hex()


def stored(conn):
    return db.get_setting(conn, "admin_pin", "") or ""


def check(conn, pin):
    s = stored(conn)
    if not s:
        return True
    salt, h = s.split("$", 1)
    return hmac.compare_digest(_hash(pin or "", salt), h)


def admin_on():
    return bool(config.ADMIN_PASSWORD)


def protected(method, path):
    return (any(m == method and re.search(p, path) for m, p in PROTECTED)
            and not any(re.search(p, path) for p in EXEMPT))


def _number(v):
    try:
        return abs(float(str(v).replace(",", "").replace("$", "").strip()))
    except ValueError:
        return 0


def admin_needed(method, path, form=None):
    if not admin_on() or not protected(method, path):
        return False
    if any(m == method and re.search(p, path) for m, p in ADMIN):
        return True
    if form is not None and re.search(ADMIN_LIMITED, path):
        if form.get("everyone"):
            return True
        return any(_number(form.get(k, "")) > limit for k, limit in ADMIN_LIMITS.items())
    return False


def unlocked_until(request, conn):
    """0 if there's no PIN at all (so nothing is asked), else when this browser's unlock runs out."""
    s = stored(conn)
    if not s:
        return 0
    if request.session.get("pin_for") != s[:12]:
        return -1
    return request.session.get("pin_until", -1)


def admin_until(request):
    return request.session.get("admin_until", -1) if admin_on() else 0


def rules():
    """What the browser needs to ask before sending, rather than after."""
    return {"protect": PROTECTED, "exempt": EXEMPT, "admin": ADMIN if admin_on() else [],
            "limited": ADMIN_LIMITED if admin_on() else "^$", "limits": ADMIN_LIMITS}


async def guard(request: Request):
    """App-wide dependency: refuse protected actions until the PIN (or owner password) is entered."""
    if not request.session.get("auth") or not protected(request.method, request.url.path):
        return
    form = await request.form() if request.method == "POST" else None
    now = time.time()
    if admin_needed(request.method, request.url.path, form):
        if admin_until(request) < now:
            raise PinRequired("admin")
        return
    conn = db.connect()
    try:
        until = unlocked_until(request, conn)
    finally:
        conn.close()
    if until and until < now and not admin_until(request) > now:
        raise PinRequired("pin")


def validate_new(pin):
    pin = (pin or "").strip()
    if not re.fullmatch(r"\d{4,8}", pin):
        raise ValueError("The PIN must be 4 to 8 digits.")
    return pin


def set_pin(conn, pin, who):
    salt = secrets.token_hex(8)
    db.set_setting(conn, "admin_pin", f"{salt}${_hash(pin, salt)}" if pin else "")
    db.audit(conn, who, "admin_pin", None, "set" if pin else "removed")


def _locked_out(now):
    _fails[:] = [t for t in _fails if t > now - FAIL_WINDOW]
    return len(_fails) >= FAIL_LIMIT


@router.post("/settings/pin/unlock")
def unlock(request: Request, pin: str = Form(""), tier: str = Form("pin"), who=Depends(staff), conn=Depends(get_conn)):
    now = time.time()
    if _locked_out(now):
        return JSONResponse({"ok": False, "error": "Too many wrong tries. Wait 10 minutes."})
    # The owner password works anywhere the PIN does (and then unlocks everything).
    owner = admin_on() and hmac.compare_digest(pin.encode(), config.ADMIN_PASSWORD.encode())
    ok = owner or (tier != "admin" and check(conn, pin))
    if owner:
        tier = "admin"
    what = "owner password" if tier == "admin" else "PIN or owner password" if admin_on() else "PIN"
    if not ok:
        _fails.append(now)
        db.audit(conn, who, f"{tier}_wrong", None, "")
        conn.commit()
        left = FAIL_LIMIT - len(_fails)
        return JSONResponse({"ok": False, "error": f"Wrong {what}." + (f" {left} more tries." if left <= 2 else "")})
    if tier == "admin":
        until = int(now + ADMIN_UNLOCK_SECONDS)
        request.session["admin_until"] = until
        db.audit(conn, who, "admin_unlock", None, "")
        conn.commit()
    else:
        until = int(now + UNLOCK_SECONDS)
        request.session.update(pin_until=until, pin_for=stored(conn)[:12])
    return JSONResponse({"ok": True, "until": until, "tier": tier})


@router.post("/settings/pin")
def change(request: Request, new: str = Form(""), remove: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    # Getting here at all needs the old PIN, or the owner password if there is one (it's under /settings).
    try:
        pin = "" if remove else validate_new(new)
    except ValueError as e:
        return back(request, "/settings#pin", str(e))
    set_pin(conn, pin, who)
    conn.commit()
    if pin:
        request.session.update(pin_until=int(time.time() + UNLOCK_SECONDS), pin_for=stored(conn)[:12])
    return back(request, "/settings#pin", "PIN set. Fizzy will ask for it before manual changes." if pin else "PIN removed.")
