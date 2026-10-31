"""Streaks and badges for the student site. Worked out on the fly from what Fizzy already
records; nothing is stored.

Badges pay points once each, when first earned (award); that part is recorded in points_ledger.

A streak counts the stall's trading days (days it sold anything) in a row on which the student
bought something, so weekends, holidays and closed lunches don't break it. Today doesn't break a
streak until it's over: no purchase yet today just means "keep it going".
"""
from datetime import timedelta

from . import config, logic, stats
from .db import get_setting, now_iso, set_setting
from .logic import parse_ts


def _local_day(ts):
    return parse_ts(ts).astimezone(config.TZ).date()


def visit_days(conn, cid):
    days = {_local_day(r[0]) for r in conn.execute(
        "SELECT created_at FROM points_ledger WHERE customer_id = ? AND reason = 'purchase' AND delta > 0", (cid,))}
    days |= {_local_day(r[0]) for r in conn.execute("SELECT charged_at FROM tab_charges WHERE customer_id = ?", (cid,))}
    return days


def trading_days(conn, since_days=400):
    """Days the stall really traded. A test account's sale (staff trying things on a Saturday) or a
    day marked closed in Settings doesn't count, or it would break everyone's streak."""
    since = (stats.today() - timedelta(days=since_days)).isoformat()
    days = {_local_day(r[0]) for r in conn.execute(
        "SELECT p.created_at FROM square_payments p LEFT JOIN customers c ON c.id = p.customer_id "
        "WHERE p.created_at >= ? AND (c.kind IS NULL OR c.kind != 'test')", (since,))}
    for a, b, _ in logic.closed_days(conn):
        days = {d for d in days if not a <= d.isoformat() <= b}
    return days


def streaks(visits, trading, today):
    """(current, best, bought_today)."""
    days = sorted((trading | visits) - {d for d in trading | visits if d > today})
    best = run = 0
    for d in days:
        run = run + 1 if d in visits else 0
        best = max(best, run)
    current = 0
    for d in reversed(days):
        if d == today and d not in visits:
            continue  # today isn't over yet
        if d not in visits:
            break
        current += 1
    return current, best, today in visits


def _items_tried(conn, cid):
    keys, cat = set(), stats.catalog(conn)
    for r in conn.execute("SELECT raw_json FROM square_payments WHERE customer_id = ? AND raw_json IS NOT NULL AND created_at >= ?",
                          (cid, get_setting(conn, "history_before", "") or "")):  # history-only sales don't count
        for it in stats.line_items(stats._order_of(r), cat):
            keys.add(it["key"])
    return len(keys)


def _paid_on_time(conn, cid):
    return sum(1 for ch in logic.tab_ledger(conn, cid)["charges"]
               if ch["unpaid_cents"] == 0 and ch["net_cents"] > 0 and not ch["paid_late"])


# Points for earning each badge, paid once (see award). Bigger for the harder ones.
BADGE_POINTS = {"First sip": 50, "Regular": 200, "Legend": 1000, "On a roll": 200, "Unstoppable": 750,
                "Explorer": 150, "Big spender": 300, "Treat yourself": 100, "Good for it": 200, "Double trouble": 100}


def award(conn, cid):
    """Pay the points for any badge earned and not yet paid. Safe to call any time: each badge pays
    once (a points_ledger row, reason 'badge', note "Badge: <name>"), and never gets taken back.
    Returns the names newly paid. The caller commits."""
    paid = {r[0] for r in conn.execute("SELECT note FROM points_ledger WHERE customer_id = ? AND reason = 'badge'", (cid,))}
    new = []
    for b in for_customer(conn, cid)["badges"]:
        note = f"Badge: {b['name']}"
        if b["earned"] and b["points"] and note not in paid:
            conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, note, created_at) VALUES (?, ?, 'badge', ?, ?)",
                         (cid, b["points"], note, now_iso()))
            new.append(b["name"])
    from . import rules  # custom badges and events from the Ask page pay at the same moments
    return new + rules.award(conn, cid)


def baseline(conn):
    """The first time badges pay points: badges people already had are marked as paid with 0 points,
    so switching it on doesn't suddenly hand out a pile of free rewards. Runs once."""
    if get_setting(conn, "badge_points_baseline"):
        return
    for (cid,) in conn.execute("SELECT id FROM customers WHERE merged_into IS NULL").fetchall():
        paid = {r[0] for r in conn.execute("SELECT note FROM points_ledger WHERE customer_id = ? AND reason = 'badge'", (cid,))}
        for b in for_customer(conn, cid)["badges"]:
            note = f"Badge: {b['name']}"
            if b["earned"] and note not in paid:
                conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, note, created_at) VALUES (?, 0, 'badge', ?, ?)",
                             (cid, note, now_iso()))
    set_setting(conn, "badge_points_baseline", now_iso())
    conn.commit()


def for_customer(conn, cid):
    visits = visit_days(conn, cid)
    current, best, today_done = streaks(visits, trading_days(conn), stats.today())
    spent = logic.spend_cents(conn, cid)
    redeemed = conn.execute("SELECT COUNT(*) FROM redemptions WHERE customer_id = ? AND undone_at IS NULL", (cid,)).fetchone()[0]
    doubled = conn.execute("SELECT 1 FROM points_ledger WHERE customer_id = ? AND event_id IS NOT NULL "
                           "GROUP BY event_id HAVING SUM(delta) > 0", (cid,)).fetchone() is not None

    def badge(icon, name, how, have, need, unit=""):
        return {"icon": icon, "name": name, "how": how, "earned": have >= need, "points": BADGE_POINTS.get(name, 0),
                "progress": min(100, round(100 * have / need)) if need else 100,
                "count": f"{min(have, need):,}{unit} / {need:,}{unit}" if need > 1 else ""}

    badges = [
        badge("🥤", "First sip", "Buy something at the stall", len(visits), 1),
        badge("🔁", "Regular", "Visit on 10 different days", len(visits), 10),
        badge("👑", "Legend", "Visit on 50 different days", len(visits), 50),
        badge("🔥", "On a roll", "A 5-day streak", best, 5),
        badge("☄️", "Unstoppable", "A 15-day streak", best, 15),
        badge("🧭", "Explorer", "Try 5 different things", _items_tried(conn, cid), 5),
        badge("💸", "Big spender", "Spend $50 in total", spent // 100, 50),
        badge("🎁", "Treat yourself", "Redeem a reward", redeemed, 1),
        badge("✅", "Good for it", "Pay off a tab on time", _paid_on_time(conn, cid), 1),
        badge("⚡", "Double trouble", "Buy something on a double points day", int(doubled), 1),
    ]
    for b in badges:
        if b["name"] == "Big spender":
            b["count"] = f"${min(spent // 100, 50)} / $50"
    badges.sort(key=lambda b: (not b["earned"], -b["progress"]))
    return {"streak": current, "best": best, "today": today_done, "visits": len(visits),
            "badges": badges, "earned": sum(b["earned"] for b in badges)}
