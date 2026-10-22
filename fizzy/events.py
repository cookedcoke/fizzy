"""Scheduled double-points days (or any multiplier), set up ahead of time on the Rewards page.

Bonus points are kept in step automatically rather than handed out once: every minute Fizzy
works out what each customer's bonus for an event should be and adds or takes back the
difference. So purchases made during the event get their bonus within a minute, a tab bought
during the event gets its bonus when it's paid off (even weeks later), and a refund takes it
back. Uses the same rule as the "Bonus points for a period" tool (logic.purchases_in).
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request

from . import logic
from .db import audit, now_iso
from .stats import day_bounds, today
from .web import back, get_conn, staff

router = APIRouter()

KEEP_UP_DAYS = 60       # keep an event's bonus in step this long after it ends (late tab repayments)
MAX_MULTIPLIER = 5


class EventError(ValueError):
    pass


def _date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise EventError("Pick the dates.")


def all_events(conn):
    return conn.execute("SELECT * FROM points_events ORDER BY start_date DESC, id DESC").fetchall()


def on(conn, day=None):
    day = (day or today()).isoformat()
    return conn.execute("SELECT * FROM points_events WHERE start_date <= ? AND end_date >= ? AND ended_at IS NULL "
                        "ORDER BY multiplier DESC", (day, day)).fetchall()


def upcoming(conn, days=14):
    t = today()
    return conn.execute("SELECT * FROM points_events WHERE start_date > ? AND start_date <= ? ORDER BY start_date",
                        (t.isoformat(), (t + timedelta(days=days)).isoformat())).fetchall()


def create(conn, name, multiplier, start, end, staff_name):
    name = " ".join((name or "").split()) or "Double points"
    try:
        mult = float(multiplier)
    except (TypeError, ValueError):
        raise EventError("The multiplier must be a number, e.g. 2 for double points.")
    if not 1 < mult <= MAX_MULTIPLIER:
        raise EventError(f"The multiplier must be more than 1 and at most {MAX_MULTIPLIER}.")
    s, e = _date(start), _date(end or start)
    if e < s:
        raise EventError("The last day is before the first day.")
    if s < today():
        raise EventError("Pick today or a day in the future. (For past days, use Tools - Bonus points for a period.)")
    if (e - s).days > 60:
        raise EventError("That's more than 60 days - split it up.")
    cur = conn.execute("INSERT INTO points_events(name, multiplier, start_date, end_date, created_by, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (name, mult, s.isoformat(), e.isoformat(), staff_name, now_iso()))
    audit(conn, staff_name, "points_event", None, f"{name} x{mult:g}, {s} to {e}")
    return cur.lastrowid


# ---- weekly ones ---------------------------------------------------------------------------------
# A rule ("double points every Friday") turns into ordinary events, two weeks ahead, so everything
# else (bonus top-ups, the student site, the website) treats each Friday like any other event.

AHEAD_DAYS = 14
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def create_rule(conn, name, multiplier, start, end, staff_name):
    first = create(conn, name, multiplier, start, end, staff_name)  # validates, and makes the first one
    ev = conn.execute("SELECT * FROM points_events WHERE id = ?", (first,)).fetchone()
    s, e = _date(ev["start_date"]), _date(ev["end_date"])
    if (e - s).days > 6:
        raise EventError("A weekly one can run at most 7 days.")
    cur = conn.execute("INSERT INTO points_rules(name, multiplier, weekday, days, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (ev["name"], ev["multiplier"], s.weekday(), (e - s).days + 1, staff_name, now_iso()))
    conn.execute("UPDATE points_events SET rule_id = ? WHERE id = ?", (cur.lastrowid, first))
    audit(conn, staff_name, "points_rule", None, f"{ev['name']} every {DAY_NAMES[s.weekday()]}")
    schedule(conn)
    return cur.lastrowid


def schedule(conn):
    """Make the events for every running weekly rule, up to two weeks ahead."""
    t = today()
    for r in conn.execute("SELECT * FROM points_rules WHERE stopped_at IS NULL").fetchall():
        made = {x[0] for x in conn.execute("SELECT start_date FROM points_events WHERE rule_id = ?", (r["id"],))}
        last = max(made) if made else None
        for i in range(AHEAD_DAYS + 1):
            d = t + timedelta(days=i)
            if d.weekday() != r["weekday"] or d.isoformat() in made or (last and d.isoformat() < last):
                continue
            conn.execute("INSERT INTO points_events(name, multiplier, start_date, end_date, created_by, created_at, rule_id) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (r["name"], r["multiplier"], d.isoformat(),
                                                          (d + timedelta(days=r["days"] - 1)).isoformat(), r["created_by"], now_iso(), r["id"]))


def stop_rule(conn, rule_id, staff_name):
    """Stop repeating. Ones that haven't started yet go; one running now carries on; bonus stays."""
    r = conn.execute("SELECT * FROM points_rules WHERE id = ?", (rule_id,)).fetchone()
    if r is None or r["stopped_at"]:
        return "Already stopped."
    conn.execute("UPDATE points_rules SET stopped_at = ? WHERE id = ?", (now_iso(), rule_id))
    conn.execute("DELETE FROM points_events WHERE rule_id = ? AND start_date > ?", (rule_id, today().isoformat()))
    audit(conn, staff_name, "points_rule_stop", None, r["name"])
    return f"{r['name']} won't repeat any more."


def cancel(conn, event_id, staff_name):
    """Delete an event that hasn't started, or end a running one now. Bonus already earned stays."""
    ev = conn.execute("SELECT * FROM points_events WHERE id = ?", (event_id,)).fetchone()
    if ev is None:
        return "Already gone."
    t = today()
    if _date(ev["start_date"]) > t:
        conn.execute("DELETE FROM points_events WHERE id = ?", (event_id,))
        audit(conn, staff_name, "points_event_delete", None, ev["name"])
        return f"Deleted {ev['name']}."
    if _date(ev["end_date"]) >= t and not ev["ended_at"]:
        conn.execute("UPDATE points_events SET end_date = ?, ended_at = ? WHERE id = ?", (t.isoformat(), now_iso(), event_id))
        audit(conn, staff_name, "points_event_end", None, ev["name"])
        return f"{ev['name']} has ended. Bonus points already earned stay."
    return f"{ev['name']} has already finished."


def reconcile(conn):
    """Bring every recent event's bonus points to what they should be. Returns points changed."""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")  # read and top up under the write lock, so two runs can't both add
    try:
        return _reconcile(conn)
    finally:
        if own:  # we opened it, so we close it: never leave the lock held
            conn.commit()


def _reconcile(conn):
    schedule(conn)
    t = today()
    changed = 0
    for ev in conn.execute("SELECT * FROM points_events WHERE start_date <= ? AND end_date >= ?",
                           (t.isoformat(), (t - timedelta(days=KEEP_UP_DAYS)).isoformat())).fetchall():
        start, _ = day_bounds(date.fromisoformat(ev["start_date"]))
        _, end = day_bounds(date.fromisoformat(ev["end_date"]))
        if ev["ended_at"]:
            end = min(end, ev["ended_at"])
        target = {cid: max(0, round(pts * (ev["multiplier"] - 1))) for cid, pts in logic.purchases_in(conn, start, end).items()}
        have = {r[0]: r[1] for r in conn.execute(
            "SELECT customer_id, SUM(delta) FROM points_ledger WHERE event_id = ? GROUP BY customer_id", (ev["id"],))}
        for cid in set(target) | set(have):
            diff = target.get(cid, 0) - (have.get(cid) or 0)
            if diff:
                conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, note, staff, created_at, event_id) "
                             "VALUES (?, ?, 'bonus', ?, 'fizzy', ?, ?)", (cid, diff, ev["name"], now_iso(), ev["id"]))
                changed += abs(diff)
    return changed


# ---- pages (the events section lives on the Rewards page) --------------------------------------

@router.post("/events")
def events_create(request: Request, name: str = Form(""), multiplier: str = Form("2"), start: str = Form(""),
                  end: str = Form(""), weekly: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    try:
        if weekly:
            create_rule(conn, name, multiplier, start, end, who)
        else:
            create(conn, name, multiplier, start, end, who)
        reconcile(conn)
    except EventError as e:
        return back(request, "/rewards#events", str(e))
    conn.commit()
    return back(request, "/rewards#events", "Done. Extra points get added by themselves.")


@router.post("/events/{event_id}/cancel")
def events_cancel(request: Request, event_id: int, who=Depends(staff), conn=Depends(get_conn)):
    msg = cancel(conn, event_id, who)
    conn.commit()
    return back(request, "/rewards#events", msg)


@router.post("/events/rules/{rule_id}/stop")
def rules_stop(request: Request, rule_id: int, who=Depends(staff), conn=Depends(get_conn)):
    msg = stop_rule(conn, rule_id, who)
    conn.commit()
    return back(request, "/rewards#events", msg)
