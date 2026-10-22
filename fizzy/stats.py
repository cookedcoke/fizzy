"""Sales numbers for the dashboard, worked out from what Square sync
has already stored (square_payments, square_refunds, tab_repayments). Nothing here calls Square.

Terms used on screen:
- Sold: the value of goods sold - paid sales plus what went on tabs.
- Taken: money actually received - Square sales and tab repayments, plus repayments recorded
  by hand, minus Square refunds. Tab sales add nothing until they're repaid.
- Orders: checkouts with something to make or hand over (repayment-only checkouts don't count).
"""
import json
from collections import Counter, OrderedDict
from datetime import datetime, time, timedelta, timezone

from . import config
from .logic import parse_ts

KINDS = {"sale": "sale_cents", "tab_charge": "tab_cents", "tab_repayment": "repay_cents"}


def day_bounds(day):
    """UTC ISO strings for the start and end of a local calendar day."""
    start = datetime.combine(day, time.min, config.TZ)
    return (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            (start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds"))


def today():
    return datetime.now(config.TZ).date()


def _key(row):
    return f"o:{row['square_order_id']}" if row["square_order_id"] else f"p:{row['square_payment_id'].removesuffix(':sale')}"


def _order_of(row):
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except ValueError:
        return None
    return raw.get("order")


def line_items(order):
    """What was sold in an order (quantity, name, value) - not the tab repayment item."""
    out = []
    for li in (order or {}).get("line_items", []) or []:
        name = (li.get("name") or "Custom amount").strip()
        if name.lower() == config.SQUARE_REPAYMENT_ITEM.lower():
            continue
        try:
            qty = int(float(li.get("quantity") or 1))
        except ValueError:
            qty = 1
        out.append({"qty": qty, "name": name, "cents": _line_revenue(order, li)})
    return out


def _amt(obj, key):
    return ((obj or {}).get(key) or {}).get("amount", 0) or 0


def _line_revenue(order, li):
    """What a line sold for: before the Fizzy Tab discount (that money comes later, as a
    repayment) but after any other discount, e.g. a reward."""
    if not li.get("gross_sales_money"):
        return _amt(li, "total_money")
    discounts = {d.get("uid"): d for d in order.get("discounts", []) or []}
    applied = li.get("applied_discounts") or []
    if applied:
        other = sum(_amt(a, "applied_money") for a in applied
                    if not square_is_tab(discounts.get(a.get("discount_uid"), {}).get("name")))
    else:  # no per-discount detail: count it all as the tab's if the order used the tab discount
        other = 0 if any(square_is_tab(d.get("name")) for d in discounts.values()) else _amt(li, "total_discount_money")
    return _amt(li, "gross_sales_money") - other


def square_is_tab(name):
    return bool(config.SQUARE_TAB_DISCOUNT) and config.SQUARE_TAB_DISCOUNT.lower() in (name or "").lower()


def checkouts(conn, start, end):
    """Checkouts (one per Square order, or per payment without an order) in [start, end), oldest
    first, each with its money split, items and customer."""
    groups = OrderedDict()
    for r in conn.execute("SELECT sp.*, c.name AS customer_name FROM square_payments sp "
                          "LEFT JOIN customers c ON c.id = sp.customer_id "
                          "WHERE sp.created_at >= ? AND sp.created_at < ? ORDER BY sp.created_at, sp.square_payment_id",
                          (start, end)):
        k = _key(r)
        g = groups.get(k)
        if g is None:
            g = groups[k] = {"key": k, "at": r["created_at"], "sale_cents": 0, "tab_cents": 0, "repay_cents": 0,
                             "items": [], "customer_id": None, "customer": None, "tenders": []}
        g[KINDS[r["kind"]]] += r["amount_cents"]
        if r["customer_id"] and not g["customer_id"]:
            g["customer_id"], g["customer"] = r["customer_id"], r["customer_name"]
        if r["tender"] and r["tender"] not in g["tenders"]:
            g["tenders"].append(r["tender"])
        if not g["items"]:
            g["items"] = line_items(_order_of(r))
    for g in groups.values():
        g["sold_cents"] = g["sale_cents"] + g["tab_cents"]
    return list(groups.values())


def refunds_cents(conn, start, end):
    return conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM square_refunds WHERE created_at >= ? AND created_at < ? "
                        "AND square_refund_id NOT LIKE 'return:%'", (start, end)).fetchone()[0]


def hand_repayments_cents(conn, start, end):
    return conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM tab_repayments WHERE created_at >= ? AND created_at < ? "
                        "AND square_payment_id IS NULL AND method != 'writeoff'", (start, end)).fetchone()[0]


def summary(conn, day):
    start, end = day_bounds(day)
    cos = checkouts(conn, start, end)
    orders = [c for c in cos if c["items"] or c["sold_cents"]]
    sold = sum(c["sold_cents"] for c in cos)
    taken = (sum(c["sale_cents"] + c["repay_cents"] for c in cos) + hand_repayments_cents(conn, start, end)
             - refunds_cents(conn, start, end))
    return {
        "day": day, "checkouts": cos, "orders": len(orders), "sold_cents": sold, "taken_cents": taken,
        "avg_cents": round(sold / len(orders)) if orders else 0,
        "tab_cents": sum(c["tab_cents"] for c in cos), "repaid_cents": sum(c["repay_cents"] for c in cos),
        "customers": len({c["customer_id"] for c in orders if c["customer_id"]}),
        "walkups": sum(1 for c in orders if not c["customer_id"]),
    }


def by_hour(checkouts_, first=7, last=16):
    """Sold per local hour. The range widens to include any hour that had sales."""
    totals = Counter()
    for c in checkouts_:
        totals[parse_ts(c["at"]).astimezone(config.TZ).hour] += c["sold_cents"]
    if totals:
        first, last = min(first, min(totals)), max(last, max(totals))
    return [{"hour": h, "cents": totals.get(h, 0)} for h in range(first, last + 1)]


def top_items(checkouts_, limit=6):
    qty, cents = Counter(), Counter()
    for c in checkouts_:
        for it in c["items"]:
            qty[it["name"]] += it["qty"]
            cents[it["name"]] += it["cents"]
    return [{"name": n, "qty": q, "cents": cents[n]} for n, q in qty.most_common(limit)]


def dashboard(conn):
    d = today()
    now_local = datetime.now(config.TZ)
    t = summary(conn, d)
    last_week = summary(conn, d - timedelta(days=7))
    # Last week's numbers up to the same time of day, so "vs last week" is fair mid-day.
    cutoff = (now_local - timedelta(days=7)).astimezone(timezone.utc).isoformat(timespec="seconds")
    lw_so_far = [c for c in last_week["checkouts"] if c["at"] <= cutoff]
    hours_today = by_hour(t["checkouts"])
    hours_lw = {h["hour"]: h["cents"] for h in by_hour(last_week["checkouts"], hours_today[0]["hour"], hours_today[-1]["hour"])}
    for h in hours_today:
        h["last_week"] = hours_lw.get(h["hour"], 0)
    # The last 7 trading days: weekdays, plus any weekend day that actually had sales.
    week, day = [], d
    while len(week) < 7 and day > d - timedelta(days=21):
        s = summary(conn, day) if day != d else t
        if day.weekday() < 5 or s["orders"] or day == d:
            week.insert(0, {"day": day, "sold_cents": s["sold_cents"], "taken_cents": s["taken_cents"], "orders": s["orders"]})
        day -= timedelta(days=1)
    week_start, _ = day_bounds(week[0]["day"])
    _, week_end = day_bounds(d)
    week_checkouts = checkouts(conn, week_start, week_end)
    return {
        "today": t, "profit": profit(t["checkouts"], costs(conn)),
        "vs_last_week_cents": sum(c["sold_cents"] for c in lw_so_far),
        "hours": hours_today, "hour_max": max([h["cents"] for h in hours_today] + [h["last_week"] for h in hours_today] + [1]),
        "week": week, "week_max": max([w["sold_cents"] for w in week] + [1]),
        "week_sold_cents": sum(w["sold_cents"] for w in week), "week_orders": sum(w["orders"] for w in week),
        "top_today": top_items(t["checkouts"]), "top_week": top_items(week_checkouts),
        "recent": [c for c in reversed(t["checkouts"]) if c["items"] or c["sold_cents"] or c["repay_cents"]][:6],
    }


# ---- profit -----------------------------------------------------------------------------------
# Square's item costs need Retail Plus, so Fizzy keeps its own: what one of each item costs us.

def item_key(name):
    return " ".join((name or "").lower().split())


def costs(conn):
    return {r["name_key"]: r["cost_cents"] for r in conn.execute("SELECT * FROM item_costs")}


def set_cost(conn, name, cost_cents, staff):
    from .db import now_iso
    key = item_key(name)
    if cost_cents is None:
        conn.execute("DELETE FROM item_costs WHERE name_key = ?", (key,))
    else:
        conn.execute("INSERT INTO item_costs(name_key, name, cost_cents, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(name_key) DO UPDATE SET name = excluded.name, cost_cents = excluded.cost_cents, "
                     "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                     (key, name, cost_cents, staff, now_iso()))


def profit(checkouts_, cost_map):
    """Per item and in total: sold, revenue, cost and profit. Items without a cost are listed but
    left out of the profit totals (and counted, so the page can say what's missing)."""
    items = {}
    for c in checkouts_:
        for it in c["items"]:
            row = items.setdefault(item_key(it["name"]), {"name": it["name"], "qty": 0, "revenue": 0})
            row["qty"] += it["qty"]
            row["revenue"] += it["cents"]
    rev = cost = 0
    missing = 0
    for k, row in items.items():
        each = cost_map.get(k)
        row["cost_each"] = each
        if each is None:
            row["cost"] = row["profit"] = row["margin"] = None
            missing += row["revenue"]
            continue
        row["cost"] = each * row["qty"]
        row["profit"] = row["revenue"] - row["cost"]
        row["margin"] = round(100 * row["profit"] / row["revenue"]) if row["revenue"] else None
        rev += row["revenue"]
        cost += row["cost"]
    rows = sorted(items.values(), key=lambda r: (r["profit"] is None, -(r["profit"] or 0), -r["revenue"]))
    return {"items": rows, "revenue": rev, "cost": cost, "profit": rev - cost,
            "margin": round(100 * (rev - cost) / rev) if rev else None,
            "uncosted_revenue": missing, "uncosted": sum(1 for r in rows if r["cost_each"] is None)}
