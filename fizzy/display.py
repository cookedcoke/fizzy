"""The customer display: a second screen facing the queue. The staff pages tell it who's being
served (instantly, through the Mac app's message bridge or a BroadcastChannel in the same browser);
it shows their first name, points, rewards they can have now, their tab and what they owe (the
owner chose to show that; Settings, Customer display turns it off). First name only, never the score breakdown."""
from functools import lru_cache

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import badges, config, db, events, logic, portal, rewards, stats
from .web import back, get_conn, local, money, staff, templates

router = APIRouter()


@router.get("/display", response_class=HTMLResponse)
def display(request: Request, who=Depends(staff)):
    return templates.TemplateResponse(request, "display.html", {"student_site": config.PUBLIC_HOST, "qr": _qr()})


@lru_cache(maxsize=1)
def _qr():
    """The student site as a QR code (inline SVG), so a phone in the queue can open it."""
    if not config.PUBLIC_HOST:
        return None
    import segno
    return segno.make(f"https://{config.PUBLIC_HOST}/", error="m").svg_inline(scale=1, border=2, dark="#1B1A1F", light="#FFFFFF", omitsize=True)


def menu_items(conn):
    """Every Square item with a price: (item_id, name, price_cents). One line per item."""
    return conn.execute("SELECT item_id, item_name, MIN(price_cents) AS price FROM catalog_items WHERE deleted = 0 AND price_cents > 0 "
                        "GROUP BY item_id ORDER BY item_name").fetchall()


def hidden_items(conn):
    """Items left off the display's menu. Stored as the ones hidden, so new items show by default."""
    import json
    try:
        return set(json.loads(db.get_setting(conn, "display_hidden_items", "[]") or "[]"))
    except ValueError:
        return set()


@router.post("/settings/display")
async def save_message(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    import json
    form = await request.form()
    message, owing = form.get("message", ""), form.get("owing", "")
    if form.get("menu"):  # the item list was on the page, so unticked ones are hidden
        shown = set(form.getlist("show"))
        db.set_setting(conn, "display_hidden_items", json.dumps(sorted(r["item_id"] for r in menu_items(conn) if r["item_id"] not in shown)))
    text = " ".join(message.split())[:120]
    db.set_setting(conn, "display_message", text)
    db.set_setting(conn, "display_owing", "1" if owing else "0")
    db.audit(conn, who, "settings", None, f"display message: {text}")
    conn.commit()
    return back(request, "/settings#display", "Saved.")


@router.get("/api/display/idle")
def idle(who=Depends(staff), conn=Depends(get_conn)):
    """What's on: shown between customers."""
    start, end = stats.day_bounds(stats.today())
    board, _ = portal.leaderboard(conn, since=portal.month_start(), limit=3)
    hide = hidden_items(conn)
    menu = [m for m in menu_items(conn) if m["item_id"] not in hide][:12]  # the item, not "Coke (Mini)"
    return JSONResponse({
        "events": [{"name": e["name"], "multiplier": e["multiplier"]} for e in events.on(conn)],
        "soon": _soon(conn),
        "rewards": [{"name": r["name"], "points": r["points_cost"]} for r in rewards.active(conn)][:6],
        "leaders": [{"rank": b["rank"], "name": b["name"], "points": b["points"]} for b in board],
        "popular": [i["name"] for i in stats.top_items(stats.checkouts(conn, start, end), 3)],
        "menu": [{"name": m["item_name"], "price": money(m["price"])} for m in menu],
        "message": db.get_setting(conn, "display_message", "") or "",
    }, headers={"Cache-Control": "no-store"})


def _soon(conn):
    """The next couple of double points days, each only once (a weekly one is booked two weeks ahead)."""
    out, seen = [], set()
    for e in events.upcoming(conn):
        key = (e["rule_id"] or e["name"], e["multiplier"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": e["name"], "multiplier": e["multiplier"], "start": e["start_date"], "weekly": bool(e["rule_id"])})
    return out[:3]


def _tab_line(st):
    if st["status"] == "approved" and st["can_charge"]:
        return f"You can put up to {money(st['available_cents'])} on your tab."
    if st["status"] == "blocked_overdue":
        return "Nothing more on your tab until that's paid."
    if st["status"] in ("approved", "paused"):
        return "Your tab's paused. Ask at the counter."
    if st["status"] == "eligible":
        return "You can ask for a tab."
    return None


def payload(conn, c, summary=None, offers=None):
    """Everything the display shows for one customer. The customer page puts this in the message
    it sends, so the display can show it straight away without asking the server."""
    s = summary or logic.customer_summary(conn, c)
    offers = rewards.offers_for(conn, c["id"], s["points"]) if offers is None else offers
    ready = [o["reward"]["name"] for o in offers if o["can_redeem"]]
    nxt = min((o for o in offers if not o["can_redeem"] and not o["limit_hit"]), key=lambda o: o["short"], default=None)
    me = badges.for_customer(conn, c["id"])
    led = s["ledger"]
    owing = None
    if led["owing_cents"] > 0 and db.get_setting(conn, "display_owing", "1") == "1":
        unpaid = [ch["due_at"] for ch in led["charges"] if ch["unpaid_cents"] > 0]
        owing = {"owing": money(led["owing_cents"]), "overdue": money(led["overdue_cents"]) if led["overdue_cents"] else None,
                 "due": local(min(unpaid), "%A") if unpaid and not led["overdue_cents"] else None}
    _, rank = portal.leaderboard(conn, since=portal.month_start(), limit=0, me=c["id"]) if c["passcode_enc"] else (None, None)
    on = events.on(conn)
    return {
        "id": c["id"], "first": (c["name"] or "").split(" ")[0], "points": s["points"],
        "ready": ready[:3],
        "next": {"name": nxt["reward"]["name"], "short": nxt["short"], "cost": nxt["reward"]["points_cost"]} if nxt else None,
        "tab": _tab_line(s["status"]),
        "streak": me["streak"], "today": me["today"],
        "rank": rank["rank"] if rank and rank["rank"] <= 10 else None,     # only bragging rights, top 10 this month
        "boost": max((e["multiplier"] for e in on), default=None),
        "new": me["visits"] == 0,
        "owing": owing,
    }


@router.get("/api/display/customer/{cid:int}")
def customer(cid: int, who=Depends(staff), conn=Depends(get_conn)):
    """Polled while someone's on screen, so a Square sale landing shows as "+300 points"."""
    c = conn.execute("SELECT * FROM customers WHERE id = ? AND merged_into IS NULL", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    return JSONResponse(payload(conn, c), headers={"Cache-Control": "no-store"})
