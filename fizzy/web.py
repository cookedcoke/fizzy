"""Shared web helpers: templates, auth dependency, form parsing."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
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


def local(ts, fmt="%a %-d %b %H:%M"):
    if not ts:
        return ""
    return logic.parse_ts(ts).astimezone(config.TZ).strftime(fmt)


ACTION_LABELS = {
    "update_requested": "Asked for an update", "maintenance": "Maintenance mode",
    "admin_pin": "Changed the PIN", "archive": "Archived", "assign_customer": "Said whose sale it was",
    "breach_ack": "Dismissed a warning", "cameras_armed": "Armed the cameras", "cameras_disarmed": "Disarmed the cameras", "claim_wait": "Claim sale button", "customer_add": "Added a customer", "customer_edit": "Edited a customer",
    "customer_kind": "Changed account type", "device_link": "Made a device link", "device_revoked": "Removed a device",
    "download_backup": "Downloaded a backup", "export": "Exported", "export_square_csv": "Exported for Square",
    "extend_due": "Gave more time to pay", "import": "Imported the school list", "incident_verdict": "Marked a security incident", "item_costs": "Changed item costs",
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
    "tab_on": "Turned a tab back on", "tab_revoked": "Turned a tab off", "tab_rejected": "Turned a tab off",
    "score_adjust": "Changed a tab score",
    "no_account": "Marked a sale No account", "no_account_undo": "Undid No account on a sale",
    "tabs_paused": "Paused or unpaused tabs", "test_accounts": "Made test accounts", "test_accounts_deleted": "Deleted test accounts",
}
templates.env.filters["action"] = lambda a: ACTION_LABELS.get(a) or (a or "").replace("_", " ").capitalize()
templates.env.filters["money"] = money
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else {}
templates.env.filters["noicon"] = lambda d: {k: v for k, v in (d or {}).items() if k != "icon"}
templates.env.filters["local"] = local
templates.env.filters["ymd"] = lambda d, fmt="%a %-d %b": datetime.strptime(d, "%Y-%m-%d").strftime(fmt) if d else ""
templates.env.globals["STATUS_LABELS"] = logic.STATUS_LABELS
templates.env.globals["TZ"] = config.TZ.key


def sale_amounts(k):
    """A checkout's money in words: "$4.50 paid, $3.00 on tab"."""
    parts = [(k["sale_cents"], "paid"), (k["tab_cents"], "on tab"), (k["repay_cents"], "repaid")]
    return ", ".join(f"{money(c)} {what}" for c, what in parts if c)


def sale_time(ts):
    return local(ts, "%-I:%M %p").lower()


def claim_bar(conn, now=None):
    """The Claim sale presses for the bar at the top of every staff page (see square.recent_waits)."""
    from . import square
    out = []
    for w in square.recent_waits(conn, now):
        sale = w.pop("sale")
        out.append({**w, "first": w["name"].split(" ")[0], "amounts": sale_amounts(sale) if sale else "",
                    "sale_at": sale_time(sale["at"]) if sale else ""})
    return out


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



def _signed_in(request):
    """A session that still counts: not from before "sign everyone out" or a password change, and for
    someone still on FIZZY_STAFF."""
    if not request.session.get("auth") or request.session.get("staff") not in config.STAFF_NAMES:
        return False
    conn = db.connect()
    try:
        return request.session.get("gen") == session_gen(conn)
    finally:
        conn.close()


def staff(request: Request):
    name = request.session.get("staff")
    if not request.session.get("auth") or name not in config.STAFF_NAMES:  # also anyone taken off FIZZY_STAFF
        raise LoginRequired()
    if not _signed_in(request):  # signed out by "sign everyone out", or a password change
        request.session.clear()
        raise LoginRequired()
    return name


# Phone browsers (not iPads or Android tablets, whose browsers don't say "Mobile"). The staff site
# turns them away: on a phone the iOS app is the way in. The app sends its own User-Agent.
_PHONE_UA = re.compile(r"iPhone|iPod|Android.+Mobile|Windows Phone|BlackBerry|Opera Mini|IEMobile|Mobile.+Firefox", re.I)


def is_phone(request):
    ua = request.headers.get("user-agent") or ""
    if "ipad" in ua.lower() or wants_json(request):
        return False
    return request.headers.get("sec-ch-ua-mobile") == "?1" or bool(_PHONE_UA.search(ua))


def wants_json(request):
    """The iOS app asks for a page's data instead of its HTML (see render)."""
    return request.headers.get("x-fizzy-format") == "json"


# Never sent to the app, even though the templates get them: secrets, and big blobs no page shows.
HIDDEN_KEYS = {"passcode_enc", "token_hash", "public_key", "credential_id", "raw_json", "lock_rules",
               "tool_ctx"}   # what a page needs to draw tool forms (tools.form_ctx): the whole customer list
# Anything named like a secret goes too, so a secret added to a page later isn't sent by accident.
# ("key" on its own is an item or sale key, not a secret.)
SECRET_NAME = re.compile(r"(^|_)(secret|token|password|passwd|hash|api_?key|private)($|_)|_enc$", re.I)


def _hidden(k):
    return k in HIDDEN_KEYS or bool(SECRET_NAME.search(str(k)))


def _plain(v):
    """Template context -> JSON-ready values. Rows become dicts; dates and Markup become strings."""
    if isinstance(v, dict) or hasattr(v, "keys"):
        return {str(k): _plain(v[k]) for k in v.keys() if not _hidden(k)}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_plain(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)):
        return str(v) if type(v) is not str and isinstance(v, str) else v  # Markup -> plain str
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, bytes):
        return None
    return str(v)


def take(request, key, default=None):
    """A one-time value from the session (a message, a new device link): read and forgotten.

    Except for a Turbo fetch from a tab still running the scripts from before an update (app.js sends
    its ?v=): Turbo throws that page away and loads it again for real (data-turbo-track), so the value
    is left for that load."""
    v = request.headers.get("x-fizzy-v")
    return (request.session.get if v and v != templates.env.globals["V"] else request.session.pop)(key, default)


def render(request, name, **ctx):
    signed_in = _signed_in(request)
    ctx.setdefault("staff", request.session.get("staff") if signed_in else None)
    ctx["staff_names"] = config.STAFF_NAMES
    ctx["flash"] = take(request, "flash")
    ctx["flash_kind"] = take(request, "flash_kind", "ok")
    jump = take(request, "jump")  # set by back(): the section to land on (see there)
    if jump and jump[0] == request.url.path:
        ctx["jump"] = jump[1]
    if signed_in:
        from . import pin
        conn = db.connect()
        try:
            ctx["lock_rules"] = pin.rules(conn, request.session.get("staff"))
            ctx["unlocked"] = pin.unlocked(request, conn)
            ctx["unlocked_until"] = (request.session.get("unlock") or {}).get("until", 0)
            if not wants_json(request):  # the app asks /api/claim-waits itself
                ctx.setdefault("claim_waits", claim_bar(conn))
        finally:
            conn.close()
    if wants_json(request):
        # The same data the page is drawn from, so the app shows exactly what the page would.
        ctx = {k: v for k, v in ctx.items() if k != "request"}
        ctx.update(page=name.removesuffix(".html"), tz=config.TZ.key)
        return JSONResponse(_plain(ctx), headers={"Cache-Control": "no-store", "Vary": "X-Fizzy-Format"})
    response = templates.TemplateResponse(request, name, ctx)
    response.headers["Vary"] = "X-Fizzy-Format"  # same address, HTML or JSON: never cache one for the other
    # Says "this page is from Fizzy itself". An error page without it came from Cloudflare (the home PC
    # is down), and the page script then loads it for real so the service worker can show its own page.
    response.headers.setdefault("X-Fizzy", "page")
    return response


def back(request, url, msg=None, kind="ok", jump=True):
    """Redirect with a message for the next page. kind "bad" shows it as a problem.

    A "#section" on the URL is also kept in the session and drawn as <body data-jump>: pages change
    without a full load (Turbo), and a redirect followed that way loses the # part, so the page
    script scrolls to (and opens) that section itself, with the message next to it. jump=False for a
    page that always loads in full (Ask), where the browser keeps the # itself."""
    if msg:
        request.session["flash"], request.session["flash_kind"] = msg, kind
    path, _, section = url.partition("#")
    if section and jump:
        request.session["jump"] = [urlsplit(path).path or "/", section]
    else:
        request.session.pop("jump", None)
    return RedirectResponse(url, status_code=303)


def safe_back(value):
    """Where a form asks to be sent back to (its hidden "back" field): a path on this site, with its query,
    or None. Never another site ("//evil", "https://...", "/\\evil"), and never a # part (the caller adds that)."""
    v = (value or "").strip()
    if not v or len(v) > 500 or not v.startswith("/") or v.startswith("//") or "\\" in v or re.search(r"[\x00-\x20]", v):
        return None
    parts = urlsplit(v)
    if parts.scheme or parts.netloc:
        return None
    return parts.path + (f"?{parts.query}" if parts.query else "")


MAX_DOLLARS = 10_000


def cents(value: str) -> int:
    v = (value or "").replace("$", "").strip()
    v = re.sub(r",(\d{1,2})$", r".\1", v).replace(",", "")  # "12,50" is $12.50 (a phone's decimal pad in some regions), "1,200" is $1,200
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
