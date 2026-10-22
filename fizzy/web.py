"""Shared web helpers: templates, auth dependency, form parsing."""
import hashlib
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


templates.env.filters["money"] = money
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
    """Bumped by the "sign everyone out" tool; sessions from an older generation are rejected."""
    return db.get_setting(conn, "session_gen", "0")


def staff(request: Request):
    name = request.session.get("staff")
    if not request.session.get("auth") or not name:
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
    return templates.TemplateResponse(request, name, ctx)


def back(request, url, msg=None):
    if msg:
        request.session["flash"] = msg
    return RedirectResponse(url, status_code=303)


def cents(value: str) -> int:
    v = (value or "").replace("$", "").replace(",", "").strip()
    try:
        return round(float(v) * 100)
    except ValueError:
        raise HTTPException(400, f"'{value}' isn't a dollar amount")
