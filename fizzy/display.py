"""The customer display: a second screen facing the queue. The staff pages tell it who's being
served (instantly, through the Mac app's message bridge or a BroadcastChannel in the same browser);
it shows their first name, points, rewards they can have now and whether they can use their tab.
Privacy: first name only, never amounts owed, overdue charges or the score breakdown."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, events, logic, rewards
from .web import get_conn, money, staff, templates

router = APIRouter()


@router.get("/display", response_class=HTMLResponse)
def display(request: Request, who=Depends(staff)):
    return templates.TemplateResponse(request, "display.html", {"student_site": config.PUBLIC_HOST})


@router.get("/api/display/idle")
def idle(who=Depends(staff), conn=Depends(get_conn)):
    """What's on: shown between customers."""
    return JSONResponse({
        "events": [{"name": e["name"], "multiplier": e["multiplier"]} for e in events.on(conn)],
        "rewards": [{"name": r["name"], "points": r["points_cost"]} for r in rewards.active(conn)][:6],
    }, headers={"Cache-Control": "no-store"})


def _tab_line(st):
    if st["status"] == "approved" and st["can_charge"]:
        return f"You can put up to {money(st['available_cents'])} on your tab."
    if st["status"] in ("approved", "blocked_overdue", "paused"):
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
    return {
        "id": c["id"], "first": (c["name"] or "").split(" ")[0], "points": s["points"],
        "ready": ready[:3],
        "next": {"name": nxt["reward"]["name"], "short": nxt["short"]} if nxt else None,
        "tab": _tab_line(s["status"]),
    }


@router.get("/api/display/customer/{cid:int}")
def customer(cid: int, who=Depends(staff), conn=Depends(get_conn)):
    """Polled while someone's on screen, so a Square sale landing shows as "+300 points"."""
    c = conn.execute("SELECT * FROM customers WHERE id = ? AND merged_into IS NULL", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    return JSONResponse(payload(conn, c), headers={"Cache-Control": "no-store"})
