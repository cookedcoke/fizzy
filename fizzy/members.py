"""Red Coke and Black Coke: memberships bought for a term that take a percentage off every sale.

They're sold in Square as items ("Red Coke membership", "Black Coke membership"). When Fizzy sees
one in a customer's sale it grants the membership until the end of the term (Settings, Term dates);
the fee itself earns no points. The discount is given in Square by staff with a discount whose name
says the tier ("Black Coke 20%"): Fizzy never changes a sale, it only checks afterwards that members
got their discount and nobody else did (issues()).
"""
import json
from datetime import date, datetime, timedelta, timezone

from . import config, logic, rules
from .db import audit, now_iso

TIERS = {
    "black": {"name": "Black Coke", "pct": 20, "price_cents": 1200},
    "red": {"name": "Red Coke", "pct": 10, "price_cents": 500},
}
RANK = {"black": 2, "red": 1}
NO_TERM_DAYS = 70   # with no term dates set, a membership lasts ten weeks
CHECK_DAYS = 7      # how far back the counter is told about a missed or wrong discount


def tier_of_item(name):
    n = (name or "").strip().lower()
    for key, t in TIERS.items():
        if n in (t["name"].lower(), t["name"].lower() + " membership"):
            return key
    return None


def tier_of_discount(name):
    n = (name or "").lower()
    for key, t in TIERS.items():
        if t["name"].lower() in n:
            return key
    return None


def _money(obj, key):
    return int(((obj or {}).get(key) or {}).get("amount", 0))


def bought_in(order):
    """(tier, fee in cents) for the best membership in an order, or None."""
    best = None
    for li in (order or {}).get("line_items", []) or []:
        key = tier_of_item(li.get("name"))
        if key and _money(li, "total_money") > 0 and (best is None or RANK[key] > RANK[best[0]]):
            best = (key, _money(li, "total_money"))
    return best


def fee_lines_total(order):
    """What an order paid for memberships (left out of points and of the discount check)."""
    return sum(_money(li, "total_money") for li in (order or {}).get("line_items", []) or [] if tier_of_item(li.get("name")))


def term_end(conn, on):
    """The last day of the term `on` (a date) is in, or of the next one when it's the holidays."""
    upcoming = sorted((a, b) for a, b, _ in rules.terms(conn) if b >= on.isoformat())
    if upcoming:
        return upcoming[0][1]
    return (on + timedelta(days=NO_TERM_DAYS)).isoformat()


def _local_day(ts):
    return logic.parse_ts(ts).astimezone(config.TZ).date()


def _end_utc(day):
    """The end of a local day (an ISO date) as a UTC timestamp, for comparing with sale times."""
    d = date.fromisoformat(day) + timedelta(days=1)
    return datetime(d.year, d.month, d.day, tzinfo=config.TZ).astimezone(timezone.utc).isoformat(timespec="seconds")


def grant(conn, customer_id, tier, at, staff, square_payment_id=None, square_order_id=None, fee_cents=0, note=None):
    if tier not in TIERS:
        raise ValueError("No such membership.")
    ends = term_end(conn, _local_day(at))
    cur = conn.execute(
        "INSERT INTO memberships(customer_id, tier, starts_at, ends_on, fee_cents, square_payment_id, square_order_id, staff, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (customer_id, tier, at, ends, fee_cents, square_payment_id, square_order_id, staff, note, now_iso()))
    audit(conn, staff or "square", "membership", customer_id, f"{TIERS[tier]['name']} until {ends}")
    return cur.lastrowid


def cancel(conn, mid, staff, why="Cancelled"):
    m = conn.execute("SELECT * FROM memberships WHERE id = ? AND cancelled_at IS NULL", (mid,)).fetchone()
    if m is None:
        return None
    conn.execute("UPDATE memberships SET cancelled_at = ?, cancelled_by = ?, note = COALESCE(note || ' · ', '') || ? WHERE id = ?",
                 (now_iso(), staff or "square", why, mid))
    audit(conn, staff or "square", "membership_cancelled", m["customer_id"], f"{TIERS[m['tier']]['name']}: {why}")
    return m


def _row(m):
    t = TIERS[m["tier"]]
    return {"id": m["id"], "tier": m["tier"], "name": t["name"], "pct": t["pct"], "starts_at": m["starts_at"],
            "ends_on": m["ends_on"], "staff": m["staff"], "from_square": bool(m["square_payment_id"])}


def active(conn, customer_id, at=None):
    """The membership a customer has at `at` (now by default), the better tier if two, or None."""
    at = at or now_iso()
    best = None
    for m in conn.execute("SELECT * FROM memberships WHERE customer_id = ? AND cancelled_at IS NULL AND starts_at <= ? "
                          "ORDER BY starts_at", (customer_id, at)):
        if _end_utc(m["ends_on"]) > at and (best is None or RANK[m["tier"]] > RANK[best["tier"]]):
            best = m
    return _row(best) if best else None


def history(conn, customer_id):
    rows = conn.execute("SELECT * FROM memberships WHERE customer_id = ? ORDER BY starts_at DESC", (customer_id,)).fetchall()
    now = now_iso()
    return [{**_row(m), "cancelled_at": m["cancelled_at"], "note": m["note"],
             "current": m["cancelled_at"] is None and m["starts_at"] <= now < _end_utc(m["ends_on"])} for m in rows]


# ---- following Square ------------------------------------------------------------------------------

def order_for(conn, sp):
    """The Square order behind a payment row (the purchase half of a split one keeps it on the other half)."""
    for pid in (sp["square_payment_id"], sp["square_payment_id"].removesuffix(":sale")):
        row = conn.execute("SELECT raw_json FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone()
        raw = (row and row["raw_json"]) or (sp["raw_json"] if pid == sp["square_payment_id"] else None)
        try:
            order = json.loads(raw or "{}").get("order")
        except (TypeError, ValueError):
            order = None
        if order:
            return order
    return None


def on_sale(conn, sp, amount):
    """A sale was recorded for a customer: grant any membership it bought (once per order). Returns
    the part of `amount` that was the membership fee, which earns no points."""
    order = order_for(conn, sp)
    got = bought_in(order)
    if not got or not sp["square_order_id"]:
        return 0
    if conn.execute("SELECT 1 FROM memberships WHERE square_order_id = ?", (sp["square_order_id"],)).fetchone():
        return 0
    tier, fee = got
    grant(conn, sp["customer_id"], tier, sp["created_at"], None, sp["square_payment_id"], sp["square_order_id"], fee)
    return min(fee, amount)


def on_undo(conn, pid):
    """The sale is no longer this customer's (unclaimed, marked not a real sale): neither is the membership."""
    conn.execute("DELETE FROM memberships WHERE square_payment_id = ?", (pid,))


def on_refund(conn, pid, amount):
    """A refund on a sale that bought a membership: the membership goes first. Returns the part of
    the refund that was the fee (so it takes no points away: the fee never earned any)."""
    m = conn.execute("SELECT * FROM memberships WHERE square_payment_id = ?", (pid,)).fetchone()
    if m is None:
        return 0
    part = min(amount, max(0, m["fee_cents"] - m["fee_refunded_cents"]))
    conn.execute("UPDATE memberships SET fee_refunded_cents = fee_refunded_cents + ? WHERE id = ?", (part, m["id"]))
    if m["cancelled_at"] is None:
        cancel(conn, m["id"], "square", "Refunded in Square")
    return part


# ---- the discount check ----------------------------------------------------------------------------

def issues(conn, customer_id, days=CHECK_DAYS):
    """Recent sales where the member discount was missed, or given to someone who isn't a member (or
    at the wrong tier). Worked out from the sales themselves, so a fix in Square (a refund of the
    difference) clears it."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    out, missed, seen = [], [], set()
    for sp in conn.execute("SELECT * FROM square_payments WHERE customer_id = ? AND created_at >= ? AND kind IN ('sale', 'tab_charge') "
                           "ORDER BY created_at DESC", (customer_id, since)).fetchall():
        oid = sp["square_order_id"]
        if not oid or oid in seen:
            continue
        seen.add(oid)
        order = order_for(conn, sp)
        if not order:
            continue
        given = {tier_of_discount(d.get("name")) for d in order.get("discounts", []) or []} - {None}
        given_tier = max(given, key=RANK.get) if given else None
        m = active(conn, customer_id, sp["created_at"])
        bought = bought_in(order)
        have = m["tier"] if m else None
        if bought and (have is None or RANK[bought[0]] >= RANK[have]):
            have = bought[0]   # bought it in this very sale: the discount may be on the rest of it
        when = sp["created_at"]
        if given_tier and have is None:
            out.append({"at": when, "kind": "not_member", "what": sp["items"],
                        "text": f"{TIERS[given_tier]['name']} discount given, but they aren't a member."})
        elif given_tier and RANK[given_tier] > RANK[have]:
            out.append({"at": when, "kind": "wrong_tier", "what": sp["items"],
                        "text": f"{TIERS[given_tier]['name']} discount given, but they're {TIERS[have]['name']}."})
        elif have and not given_tier and not bought and _discountable(order) > 0:
            refunded = conn.execute("SELECT 1 FROM square_refunds r JOIN square_payments p ON p.square_payment_id = r.square_payment_id "
                                    "WHERE p.square_order_id = ? LIMIT 1", (oid,)).fetchone()
            if not refunded:
                missed.append({"at": when, "what": sp["items"], "tier": have,
                               "owed_cents": round(_discountable(order) * TIERS[have]["pct"] / 100)})
    if missed:   # one line however many: a forgetful week shouldn't fill the page
        owed, t = sum(m["owed_cents"] for m in missed), TIERS[missed[0]["tier"]]
        n = len(missed)
        out.append({"at": missed[0]["at"], "kind": "missed", "owed_cents": owed, "sales": missed,
                    "what": missed[0]["what"] if n == 1 else f"{n} sales since {_local_day(missed[-1]['at']):%a %-d %b}",
                    "text": f"No {t['name']} discount{'' if n == 1 else f' on {n} sales'}: owed ${owed / 100:.2f} back (refund it in Square)."})
    return out


def _discountable(order):
    """What a member's percentage applies to: the items, less memberships, tab repayments and
    anything a reward made free."""
    lines = [li for li in order.get("line_items", []) or []
             if not tier_of_item(li.get("name")) and (li.get("name") or "").strip().lower() != (config.SQUARE_REPAYMENT_ITEM or "").lower()]
    gross = sum(_money(li, "gross_sales_money") or (_money(li, "total_money") + _money(li, "total_discount_money")) for li in lines)
    others = 0
    for d in order.get("discounts", []) or []:
        n = d.get("name") or ""
        if config.SQUARE_TAB_DISCOUNT and config.SQUARE_TAB_DISCOUNT.lower() in n.lower():
            continue
        others += _money(d, "applied_money")
    return max(0, gross - others)
