"""Points shop: staff-run reward events (e.g. "Free Coke" for 1000 points, "$2 off" for 500).

A reward is live from when it's created until staff end it. While it's live, every customer
page shows it: a Redeem button if they can afford it, otherwise how many points they need.
Redeeming takes the points off and logs it; staff apply the matching discount in Square.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config, logic
from .db import audit, now_iso
from .web import back, get_conn, render, staff

router = APIRouter()


class RewardError(ValueError):
    pass


MAX_POINTS = 1_000_000
MAX_LIMIT = 1_000


def active(conn):
    return conn.execute("SELECT * FROM rewards WHERE ended_at IS NULL ORDER BY points_cost, id").fetchall()


def times_redeemed(conn, reward_id, customer_id):
    return conn.execute("SELECT COUNT(*) FROM redemptions WHERE reward_id = ? AND customer_id = ? AND undone_at IS NULL",
                        (reward_id, customer_id)).fetchone()[0]


def offers_for(conn, customer_id, points=None, rewards=None):
    """Every live reward as seen for this customer: can they redeem it now, and if not, why."""
    points = logic.points_balance(conn, customer_id) if points is None else points
    out = []
    for r in active(conn) if rewards is None else rewards:
        used = times_redeemed(conn, r["id"], customer_id)
        limit_hit = r["per_customer_limit"] is not None and used >= r["per_customer_limit"]
        short = max(0, r["points_cost"] - points)
        out.append({"reward": r, "used": used, "limit_hit": limit_hit, "short": short,
                    "can_redeem": not limit_hit and short == 0})
    return out


def can_redeem_any(conn):
    """Customer ids who can redeem at least one live reward right now (for the search tag)."""
    rewards = active(conn)
    if not rewards:
        return set()
    cheapest = min(r["points_cost"] for r in rewards)
    ids = set()
    for row in conn.execute("SELECT customer_id, SUM(delta) AS pts FROM points_ledger GROUP BY customer_id HAVING pts >= ?",
                            (cheapest,)):
        if any(o["can_redeem"] for o in offers_for(conn, row["customer_id"], row["pts"], rewards)):
            ids.add(row["customer_id"])
    return ids


def create(conn, name, points_cost, square_note, limit, staff_name):
    name = " ".join((name or "").split())
    if not name:
        raise RewardError("Give the reward a name, e.g. 'Free Coke'.")
    if not 0 < points_cost <= MAX_POINTS:
        raise RewardError(f"Points must be between 1 and {MAX_POINTS:,}.")
    if limit is not None and not 0 < limit <= MAX_LIMIT:
        raise RewardError(f"The limit per customer must be between 1 and {MAX_LIMIT:,} (or leave it blank).")
    if config.SQUARE_TAB_DISCOUNT and config.SQUARE_TAB_DISCOUNT.lower() in (square_note or "").lower():
        raise RewardError("Don't use the Fizzy Tab discount for rewards - it would put the sale on a tab.")
    cur = conn.execute(
        "INSERT INTO rewards(name, points_cost, square_note, per_customer_limit, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, points_cost, (square_note or "").strip() or None, limit, staff_name, now_iso()))
    audit(conn, staff_name, "reward_start", None, f"{name} for {points_cost} points")
    return cur.lastrowid


def end(conn, reward_id, staff_name):
    r = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    if r is None or r["ended_at"]:
        return
    conn.execute("UPDATE rewards SET ended_at = ?, ended_by = ? WHERE id = ?", (now_iso(), staff_name, reward_id))
    audit(conn, staff_name, "reward_end", None, r["name"])


def _write_lock(conn):
    """Take SQLite's write lock before checking, so two tabs can't both pass the checks and
    both write (double redeem past the limit, or below zero)."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def redeem(conn, reward_id, customer, staff_name):
    _write_lock(conn)
    r = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    if r is None or r["ended_at"]:
        raise RewardError("That reward has ended.")
    offer = offers_for(conn, customer["id"], rewards=[r])[0]
    if offer["limit_hit"]:
        raise RewardError(f"{customer['name']} has already used {r['name']} {offer['used']} time(s) - the limit.")
    if offer["short"]:
        raise RewardError(f"{customer['name']} needs {offer['short']} more points for {r['name']}.")
    ts = now_iso()
    cur = conn.execute("INSERT INTO redemptions(reward_id, customer_id, points, staff, created_at) VALUES (?, ?, ?, ?, ?)",
                       (r["id"], customer["id"], r["points_cost"], staff_name, ts))
    conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, note, staff, created_at) VALUES (?, ?, 'redeem', ?, ?, ?)",
                 (customer["id"], -r["points_cost"], f"Redeemed: {r['name']}", staff_name, ts))
    audit(conn, staff_name, "redeem", customer["id"], f"{r['name']} (-{r['points_cost']} points)")
    return cur.lastrowid


UNDO_HOURS = 12


def undo(conn, redemption_id, staff_name):
    """Give the points back for a redemption made by mistake (same day only)."""
    _write_lock(conn)
    red = conn.execute("SELECT d.*, r.name FROM redemptions d JOIN rewards r ON r.id = d.reward_id WHERE d.id = ?",
                       (redemption_id,)).fetchone()
    if red is None or red["undone_at"]:
        raise RewardError("Already undone.")
    if logic.parse_ts(red["created_at"]) < logic.parse_ts(now_iso()) - timedelta(hours=UNDO_HOURS):
        raise RewardError("Too old to undo here - use Adjust points instead.")
    ts = now_iso()
    if conn.execute("UPDATE redemptions SET undone_at = ?, undone_by = ? WHERE id = ? AND undone_at IS NULL",
                    (ts, staff_name, redemption_id)).rowcount == 0:
        raise RewardError("Already undone.")
    conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, note, staff, created_at) VALUES (?, ?, 'redeem_undo', ?, ?, ?)",
                 (red["customer_id"], red["points"], f"Undone: {red['name']}", staff_name, ts))
    audit(conn, staff_name, "redeem_undo", red["customer_id"], red["name"])
    return red["customer_id"]


def recent_redemptions(conn, customer_id):
    since = (logic.parse_ts(now_iso()) - timedelta(hours=UNDO_HOURS)).isoformat(timespec="seconds")
    return conn.execute("SELECT d.*, r.name, r.square_note FROM redemptions d JOIN rewards r ON r.id = d.reward_id "
                        "WHERE d.customer_id = ? AND d.undone_at IS NULL AND d.created_at >= ? ORDER BY d.created_at DESC",
                        (customer_id, since)).fetchall()


# ---- pages ------------------------------------------------------------------------------------

def _int_or_none(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        raise RewardError(f"'{v}' isn't a whole number.")


@router.get("/rewards", response_class=HTMLResponse)
def rewards_page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    rows = conn.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM redemptions d WHERE d.reward_id = r.id AND d.undone_at IS NULL) AS used, "
        "(SELECT COUNT(DISTINCT customer_id) FROM redemptions d WHERE d.reward_id = r.id AND d.undone_at IS NULL) AS people "
        "FROM rewards r ORDER BY r.ended_at IS NOT NULL, r.created_at DESC").fetchall()
    return render(request, "rewards.html", live=[r for r in rows if not r["ended_at"]],
                  ended=[r for r in rows if r["ended_at"]][:30])


@router.post("/rewards")
def rewards_create(request: Request, name: str = Form(""), points: str = Form(""), square_note: str = Form(""),
                   limit: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    try:
        cost = _int_or_none(points)
        create(conn, name, cost or 0, square_note, _int_or_none(limit), who)
    except RewardError as e:
        return back(request, "/rewards", str(e))
    conn.commit()
    return back(request, "/rewards", f"“{' '.join(name.split())}” is live - it now shows on every customer page.")


@router.post("/rewards/{reward_id}/end")
def rewards_end(request: Request, reward_id: int, who=Depends(staff), conn=Depends(get_conn)):
    end(conn, reward_id, who)
    conn.commit()
    return back(request, "/rewards", "Reward ended. Its history is kept below.")


@router.post("/customers/{cid}/redeem/{reward_id}")
def customer_redeem(request: Request, cid: int, reward_id: int, who=Depends(staff), conn=Depends(get_conn)):
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    try:
        redeem(conn, reward_id, c, who)
    except RewardError as e:
        conn.rollback()
        return back(request, f"/customers/{cid}", str(e))
    conn.commit()
    r = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    todo = f" In Square: {r['square_note']}." if r["square_note"] else ""
    return back(request, f"/customers/{cid}", f"Redeemed {r['name']} (-{r['points_cost']} points).{todo}")


@router.post("/redemptions/{rid}/undo")
def redemption_undo(request: Request, rid: int, who=Depends(staff), conn=Depends(get_conn)):
    try:
        cid = undo(conn, rid, who)
    except RewardError as e:
        conn.rollback()
        return back(request, request.headers.get("referer") or "/rewards", str(e))
    conn.commit()
    return back(request, f"/customers/{cid}", "Redemption undone - points given back.")
