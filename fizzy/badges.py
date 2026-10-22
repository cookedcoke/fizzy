"""Streaks and badges for the student site. Worked out on the fly from what Fizzy already
records; nothing is stored.

A streak counts the stall's trading days (days it sold anything) in a row on which the student
bought something, so weekends, holidays and closed lunches don't break it. Today doesn't break a
streak until it's over: no purchase yet today just means "keep it going".
"""
from datetime import timedelta

from . import config, logic, stats
from .logic import parse_ts


def _local_day(ts):
    return parse_ts(ts).astimezone(config.TZ).date()


def visit_days(conn, cid):
    days = {_local_day(r[0]) for r in conn.execute(
        "SELECT created_at FROM points_ledger WHERE customer_id = ? AND reason = 'purchase' AND delta > 0", (cid,))}
    days |= {_local_day(r[0]) for r in conn.execute("SELECT charged_at FROM tab_charges WHERE customer_id = ?", (cid,))}
    return days


def trading_days(conn, since_days=400):
    since = (stats.today() - timedelta(days=since_days)).isoformat()
    return {_local_day(r[0]) for r in conn.execute("SELECT created_at FROM square_payments WHERE created_at >= ?", (since,))}


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
    for r in conn.execute("SELECT raw_json FROM square_payments WHERE customer_id = ? AND raw_json IS NOT NULL", (cid,)):
        for it in stats.line_items(stats._order_of(r), cat):
            keys.add(it["key"])
    return len(keys)


def _paid_on_time(conn, cid):
    return sum(1 for ch in logic.tab_ledger(conn, cid)["charges"]
               if ch["unpaid_cents"] == 0 and ch["net_cents"] > 0 and not ch["paid_late"])


def for_customer(conn, cid):
    visits = visit_days(conn, cid)
    current, best, today_done = streaks(visits, trading_days(conn), stats.today())
    spent = logic.spend_cents(conn, cid)
    redeemed = conn.execute("SELECT COUNT(*) FROM redemptions WHERE customer_id = ? AND undone_at IS NULL", (cid,)).fetchone()[0]
    doubled = conn.execute("SELECT 1 FROM points_ledger WHERE customer_id = ? AND event_id IS NOT NULL "
                           "GROUP BY event_id HAVING SUM(delta) > 0", (cid,)).fetchone() is not None

    def badge(icon, name, how, have, need, unit=""):
        return {"icon": icon, "name": name, "how": how, "earned": have >= need,
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
