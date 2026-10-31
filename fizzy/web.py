"""Shared web helpers: templates, auth dependency, form parsing."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import config, db, logic

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def _asset_version():
    """Changes whenever a static file changes, so browsers can cache /static forever."""
    h = hashlib.sha1()
    for f in sorted((HERE / "static").iterdir()):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["V"] = _asset_version()


def money(cents):
    cents = cents or 0
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def local(ts, fmt="%a %d %b %H:%M"):
    if not ts:
        return ""
    return logic.parse_ts(ts).astimezone(config.TZ).strftime(fmt)


ACTION_LABELS = {
    "update_requested": "Asked for an update", "maintenance": "Maintenance mode",
    "admin_pin": "Changed the PIN", "archive": "Archived", "assign_customer": "Said whose sale it was",
    "breach_ack": "Dismissed a warning", "customer_add": "Added a customer", "customer_edit": "Edited a customer",
    "customer_kind": "Changed account type", "device_link": "Made a device link", "device_revoked": "Removed a device",
    "download_backup": "Downloaded a backup", "export": "Exported", "export_square_csv": "Exported for Square",
    "extend_due": "Gave more time to pay", "import": "Imported the school list", "item_costs": "Changed item costs",
    "login": "Signed in", "merge": "Merged customers", "notice": "Changed the noticeboard", "owner_unlock": "Owner unlocked",
    "square_history": "Imported sales history", "rule_add": "Added a custom badge or event", "rule_change": "Changed a custom badge or event",
    "rule_pause": "Paused a custom badge or event", "rule_resume": "Restarted a custom badge or event",
    "rule_delete": "Deleted a custom badge or event", "qr_login": "Showed a sign-in code", "qr_login_used": "Signed in with a code", "me_signout": "Signed out their phones",
    "passkey_add": "Added a security key", "passkey_remove": "Removed a security key",
    "passcode_removed": "Removed a passcode", "passcode_set": "Set a passcode", "passcode_viewed": "Read a passcode",
    "passcode_wrong": "Wrong passcode", "pin_wrong": "Wrong passcode", "admin_wrong": "Wrong passcode",
    "payout": "Recorded a payout", "payout_delete": "Deleted a payout",
    "permissions": "Changed who can do what", "points_adjust": "Changed points", "points_event": "Scheduled double points",
    "points_event_delete": "Deleted double points", "points_event_end": "Ended double points", "points_reset_all": "Reset all points",
    "points_rule": "Set up weekly double points", "points_rule_stop": "Stopped weekly double points", "raffle": "Ran a raffle",
    "reclassify": "Changed how a payment counts", "redeem": "Redeemed a reward", "redeem_undo": "Undid a redemption",
    "restock": "Added a restock", "restock_delete": "Deleted a restock", "restore": "Restored customers",
    "reward_end": "Ended a reward", "reward_start": "Started a reward", "settings": "Changed settings",
    "sign_out_all": "Signed everyone out", "square_resync": "Re-synced Square", "tab_charge_refund": "Refunded a tab charge",
    "tabs_paused": "Paused or unpaused tabs", "test_accounts": "Made test accounts", "test_accounts_deleted": "Deleted test accounts",
}
templates.env.filters["action"] = lambda a: ACTION_LABELS.get(a) or (a or "").replace("_", " ").capitalize()
templates.env.filters["money"] = money
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else {}
templates.env.filters["noicon"] = lambda d: {k: v for k, v in (d or {}).items() if k != "icon"}
templates.env.filters["local"] = local
templates.env.filters["ymd"] = lambda d, fmt="%a %d %b": datetime.strptime(d, "%Y-%m-%d").strftime(fmt) if d else ""
templates.env.globals["STATUS_LABELS"] = logic.STATUS_LABELS
templates.env.globals["TZ"] = config.TZ.key


class LoginRequired(Exception):
    pass


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def session_gen(conn):
    """Bumped by the "sign everyone out" tool; sessions from an older generation are rejected.
    Changing FIZZY_PASSWORD signs everyone out too."""
    pw = hashlib.sha256(f"{config.SECRET_KEY}:{config.PASSWORD}".encode()).hexdigest()[:12]
    return f'{db.get_setting(conn, "session_gen", "0")}:{pw}'



def staff(request: Request):
    name = request.session.get("staff")
    if not request.session.get("auth") or name not in config.STAFF_NAMES:  # also anyone taken off FIZZY_STAFF
        raise LoginRequired()
    conn = db.connect()
    try:
        if request.session.get("gen") != session_gen(conn):
            request.session.clear()
            raise LoginRequired()
    finally:
        conn.close()
    return name


def render(request, name, **ctx):
    ctx.setdefault("staff", request.session.get("staff"))
    ctx["staff_names"] = config.STAFF_NAMES
    ctx["flash"] = request.session.pop("flash", None)
    ctx["flash_kind"] = request.session.pop("flash_kind", "ok")
    if request.session.get("auth"):
        from . import pin
        conn = db.connect()
        try:
            ctx["lock_rules"] = pin.rules(conn, request.session.get("staff"))
            ctx["unlocked"] = pin.unlocked(request, conn)
            ctx["unlocked_until"] = (request.session.get("unlock") or {}).get("until", 0)
        finally:
            conn.close()
    return templates.TemplateResponse(request, name, ctx)


def back(request, url, msg=None, kind="ok"):
    """Redirect with a message for the next page. kind "bad" shows it as a problem."""
    if msg:
        request.session["flash"], request.session["flash_kind"] = msg, kind
    return RedirectResponse(url, status_code=303)


MAX_DOLLARS = 10_000


def cents(value: str) -> int:
    v = (value or "").replace("$", "").replace(",", "").strip()
    try:
        n = float(v)
    except ValueError:
        n = None
    if n is None or n != n or n in (float("inf"), float("-inf")):
        raise HTTPException(400, f"'{value}' isn't a dollar amount." if v else "Type an amount.")
    if n < 0:
        raise HTTPException(400, "Amounts can't be negative.")
    if n > MAX_DOLLARS:
        raise HTTPException(400, f"${n:,.0f} is way too much. The most is ${MAX_DOLLARS:,}.")
    return round(n * 100)
