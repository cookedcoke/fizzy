"""Optional admin PIN for the manual, can't-easily-undo stuff: everything on the Tools page, hand
points and repayments, tab decisions, editing customers, deleting restocks and payouts, Settings.

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

from . import db
from .web import back, get_conn, staff

router = APIRouter()

UNLOCK_SECONDS = 600
FAIL_LIMIT, FAIL_WINDOW = 5, 600
_fails = []

# (method, path) that need the PIN. The same list goes to the browser so it can ask before sending.
PROTECTED = [
    ("POST", r"^/tools/"),
    ("GET", r"^/tools/(export/|backup\.db)"),
    ("POST", r"^/customers/\d+/(points|repay|tab|edit)$"),
    ("POST", r"^/money/(restock|payout)/\d+/delete$"),
    ("POST", r"^/square/payments/[^/]+/kind$"),
    ("POST", r"^/import$"),
    ("POST", r"^/settings"),
]


class PinRequired(Exception):
    pass


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


def protected(method, path):
    return any(m == method and re.search(p, path) for m, p in PROTECTED)


def unlocked_until(request, conn):
    """0 if there's no PIN at all (so nothing is asked), else when this browser's unlock runs out."""
    s = stored(conn)
    if not s:
        return 0
    if request.session.get("pin_for") != s[:12]:
        return -1
    return request.session.get("pin_until", -1)


def guard(request: Request):
    """App-wide dependency: refuse protected actions until the PIN has been entered."""
    if not request.session.get("auth") or not protected(request.method, request.url.path) or request.url.path in ("/settings/pin/unlock",):
        return
    conn = db.connect()
    try:
        until = unlocked_until(request, conn)
    finally:
        conn.close()
    if until and until < time.time():
        raise PinRequired()


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
def unlock(request: Request, pin: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    now = time.time()
    if _locked_out(now):
        return JSONResponse({"ok": False, "error": "Too many wrong PINs. Wait 10 minutes."})
    if not check(conn, pin):
        _fails.append(now)
        db.audit(conn, who, "admin_pin_wrong", None, "")
        conn.commit()
        left = FAIL_LIMIT - len(_fails)
        return JSONResponse({"ok": False, "error": "Wrong PIN." + (f" {left} more tries." if left <= 2 else "")})
    until = int(now + UNLOCK_SECONDS)
    request.session.update(pin_until=until, pin_for=stored(conn)[:12])
    return JSONResponse({"ok": True, "until": until})


@router.post("/settings/pin")
def change(request: Request, new: str = Form(""), remove: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    # Getting here at all needs the old PIN (it's under /settings), so no need to ask again.
    try:
        pin = "" if remove else validate_new(new)
    except ValueError as e:
        return back(request, "/settings#pin", str(e))
    set_pin(conn, pin, who)
    conn.commit()
    if pin:
        request.session.update(pin_until=int(time.time() + UNLOCK_SECONDS), pin_for=stored(conn)[:12])
    return back(request, "/settings#pin", "PIN set. Fizzy will ask for it before manual changes." if pin else "PIN removed.")
