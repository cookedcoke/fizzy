"""Business rules: tabs, repayments, points and tab status.

Repayments are applied to charges oldest-first (FIFO). Allocation is derived on the fly
from the totals, so refunds and corrections never leave stale allocation rows behind.
Points for tab purchases are earned only when the tab is repaid.
"""
from datetime import datetime, timedelta, timezone

from .db import audit, get_setting, now_iso

STATUS_LABELS = {
    "locked": "Locked",
    "eligible": "Eligible",
    "approved": "Approved",
    "rejected": "Rejected",
    "revoked": "Revoked",
    "blocked_overdue": "Blocked (overdue)",
    "paused": "Paused (all tabs)",
}


def parse_ts(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def settings(conn):
    return {
        "tab_limit_cents": int(get_setting(conn, "tab_limit_cents", 1000)),
        "tab_due_days": int(get_setting(conn, "tab_due_days", 7)),
        "unlock_spend_cents": int(get_setting(conn, "unlock_spend_cents", 3000)),
        "tab_score_needed": int(get_setting(conn, "tab_score_needed", 60)),
        "tabs_paused": get_setting(conn, "tabs_paused", "0") == "1",
    }


def tab_ledger(conn, customer_id, at=None):
    """Charges with paid/unpaid/overdue state, worked out FIFO from all repayments."""
    at = at or datetime.now(timezone.utc)
    charges = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tab_charges WHERE customer_id = ? ORDER BY charged_at, id", (customer_id,)
        )
    ]
    repayments = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tab_repayments WHERE customer_id = ? ORDER BY created_at, id", (customer_id,)
        )
    ]
    total_repaid = sum(r["amount_cents"] for r in repayments)

    # Running total of repayments over time, to find when each charge was fully covered.
    running, timeline = 0, []
    for r in repayments:
        running += r["amount_cents"]
        timeline.append((r["created_at"], running))

    cum = 0
    overdue_cents = 0
    for c in charges:
        net = c["amount_cents"] - c["refunded_cents"]
        before = cum
        cum += net
        c["net_cents"] = net
        c["paid_cents"] = max(0, min(net, total_repaid - before))
        c["unpaid_cents"] = net - c["paid_cents"]
        c["settled_at"] = None
        if c["unpaid_cents"] == 0:
            if net <= 0:
                c["settled_at"] = c["charged_at"]
            else:
                # First moment the running total covered this charge (and stayed covered).
                for ts, total in timeline:
                    if total < cum:
                        c["settled_at"] = None
                    elif c["settled_at"] is None:
                        c["settled_at"] = max(ts, c["charged_at"])
        due = parse_ts(c["due_at"])
        c["overdue"] = c["unpaid_cents"] > 0 and due < at
        c["paid_late"] = c["settled_at"] is not None and parse_ts(c["settled_at"]) > due
        if c["overdue"]:
            overdue_cents += c["unpaid_cents"]

    balance = sum(c["net_cents"] for c in charges) - total_repaid
    return {
        "charges": charges,
        "repayments": repayments,
        "balance_cents": balance,  # negative means the customer is in credit
        "owing_cents": max(0, balance),
        "overdue_cents": overdue_cents,
        "has_overdue": overdue_cents > 0,
        "late_count": sum(1 for c in charges if c["paid_late"] or c["overdue"]),
    }


def points_balance(conn, customer_id):
    return conn.execute(
        "SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ?", (customer_id,)
    ).fetchone()[0]


def spend_cents(conn, customer_id):
    return conn.execute(
        "SELECT COALESCE(SUM(spend_cents), 0) FROM points_ledger WHERE customer_id = ?", (customer_id,)
    ).fetchone()[0]


# ---- tab score -----------------------------------------------------------------------------------
# A 0-100 score from someone's own history at the stall. It decides who's ready for a tab (staff
# still approve the first time) and how much an approved person can owe. Every point is
# explained, for staff and for the student.

SCORE_BANDS = [(80, 1.0), (65, 0.75), (50, 0.5), (0, 0.25)]  # score at least -> share of the max limit


def _days_ago(ts, at):
    return (at - parse_ts(ts)).days


def tab_score(conn, customer_id, ledger=None, spent=None, cfg=None, at=None):
    at = at or datetime.now(timezone.utc)
    cfg = cfg or settings(conn)
    ledger = ledger or tab_ledger(conn, customer_id, at)
    spent = spend_cents(conn, customer_id) if spent is None else spent
    days, first = conn.execute(
        "SELECT COUNT(DISTINCT substr(created_at, 1, 10)), MIN(created_at) FROM ("
        "SELECT created_at FROM points_ledger WHERE customer_id = ? AND reason = 'purchase' AND delta > 0 "
        "UNION ALL SELECT charged_at FROM tab_charges WHERE customer_id = ?)", (customer_id, customer_id)).fetchone()
    charges = [c for c in ledger["charges"] if c["net_cents"] > 0]
    on_time = sum(1 for c in charges if c["unpaid_cents"] == 0 and not c["paid_late"])
    late_recent = sum(1 for c in charges if (c["paid_late"] and _days_ago(c["settled_at"], at) <= 60) or c["overdue"])
    late_old = sum(1 for c in charges if c["paid_late"] and _days_ago(c["settled_at"], at) > 60)
    writeoffs = sum(1 for r in ledger["repayments"] if r["method"] == "writeoff")

    f = [("Everyone starts at", 40)]
    if spent:
        f.append((f"Spent ${spent / 100:,.2f} at the stall", min(20, spent // 250)))
    if days:
        f.append((f"Bought something on {days} different day{'' if days == 1 else 's'}", min(15, days)))
    if on_time:
        f.append((f"Paid off {on_time} tab{'' if on_time == 1 else 's'} on time", min(25, 5 * on_time)))
    if late_recent:
        f.append((f"Paid late {late_recent} time{'' if late_recent == 1 else 's'} in the last 2 months", -12 * late_recent))
    if late_old:
        f.append((f"Paid late {late_old} time{'' if late_old == 1 else 's'} before that", -5 * late_old))
    if ledger["has_overdue"]:
        f.append(("Has money overdue right now", -25))
    if writeoffs:
        f.append((f"Had a debt written off{'' if writeoffs == 1 else f' {writeoffs} times'}", -15 * writeoffs))
    if first is None or _days_ago(first, at) < 14:
        f.append(("New here (under 2 weeks)", -10))
    score = max(0, min(100, sum(p for _, p in f)))
    share = next(sh for floor, sh in SCORE_BANDS if score >= floor)
    limit = int(cfg["tab_limit_cents"] * share) // 50 * 50
    next_band = next(((floor, sh) for floor, sh in reversed(SCORE_BANDS) if floor > score), None)
    tips = []
    if ledger["has_overdue"]:
        tips.append("Pay what's overdue - it's the biggest thing holding the score back.")
    if on_time < 5:
        tips.append("Pay tabs off before they're due.")
    if days < 15:
        tips.append("Keep coming back - every different day counts.")
    return {"score": score, "factors": f, "limit_cents": limit, "share": share, "tips": tips[:2],
            "next": {"score": next_band[0], "limit_cents": int(cfg["tab_limit_cents"] * next_band[1]) // 50 * 50} if next_band else None,
            "ready": score >= cfg["tab_score_needed"]}


def status_for(decision, has_overdue, spent, cfg, score=None):
    """The tab status rules in one place. score: from tab_score (None means only spend counts)."""
    if decision == "approved":
        if has_overdue:
            return "blocked_overdue"
        return "paused" if cfg["tabs_paused"] else "approved"
    if decision in ("rejected", "revoked"):
        return decision
    ready = score is None or score >= cfg["tab_score_needed"]
    return "eligible" if spent >= cfg["unlock_spend_cents"] and ready else "locked"


def tab_status(conn, customer, ledger=None, cfg=None):
    cfg = cfg or settings(conn)
    ledger = ledger or tab_ledger(conn, customer["id"])
    spent = spend_cents(conn, customer["id"])
    sc = tab_score(conn, customer["id"], ledger, spent, cfg)
    status = status_for(customer["tab_decision"], ledger["has_overdue"], spent, cfg, sc["score"])
    can_charge = status == "approved"
    available = max(0, sc["limit_cents"] - ledger["owing_cents"]) if can_charge else 0
    return {
        "status": status,
        "label": STATUS_LABELS[status],
        "can_charge": can_charge and available > 0,
        "available_cents": available,
        "spend_cents": spent,
        "unlock_spend_cents": cfg["unlock_spend_cents"],
        "limit_cents": sc["limit_cents"],          # this person's limit, from their score
        "max_limit_cents": cfg["tab_limit_cents"],
        "score": sc,
        "score_needed": cfg["tab_score_needed"],
        "spent_enough": spent >= cfg["unlock_spend_cents"],
    }


def set_tab_decision(conn, customer_id, decision, note, staff):
    if decision not in ("approved", "rejected", "revoked", None):
        raise ValueError("bad decision")
    ts = now_iso()
    conn.execute(
        "UPDATE customers SET tab_decision = ?, tab_decision_note = ?, tab_decision_by = ?, tab_decision_at = ? "
        "WHERE id = ?",
        (decision, note or None, staff, ts, customer_id),
    )
    conn.execute(
        "INSERT INTO tab_decisions(customer_id, decision, note, staff, created_at) VALUES (?, ?, ?, ?, ?)",
        (customer_id, decision or "reset", note or None, staff, ts),
    )
    audit(conn, staff, f"tab_{decision or 'reset'}", customer_id, note or "")


MAX_POINTS_CHANGE = 100_000


def whole_points(value):
    """Staff-typed points ("1,000", "-50") as an int, or ValueError with a message for the page."""
    v = str(value or "").replace(",", "").replace(" ", "").strip()
    try:
        n = int(v)
    except ValueError:
        raise ValueError(f"'{value}' isn't a whole number of points." if v else "Type how many points.")
    if abs(n) > MAX_POINTS_CHANGE:
        raise ValueError(f"That's too many points. The most in one go is {MAX_POINTS_CHANGE:,}.")
    return n


def adjust_points(conn, customer_id, delta, note, staff):
    if abs(delta) > MAX_POINTS_CHANGE:
        raise ValueError(f"That's too many points. The most in one go is {MAX_POINTS_CHANGE:,}.")
    if not note or not note.strip():
        raise ValueError("A reason is required for manual points changes.")
    if delta == 0:
        raise ValueError("Points change can't be zero.")
    conn.execute(
        "INSERT INTO points_ledger(customer_id, delta, reason, note, staff, created_at) VALUES (?, ?, 'manual', ?, ?, ?)",
        (customer_id, delta, note.strip(), staff, now_iso()),
    )
    audit(conn, staff, "points_adjust", customer_id, f"{delta:+d}: {note.strip()}")


TAB_POINT_REASONS = ("tab_repaid", "tab_refund", "credit_used", "tab_adjust")


def _tab_points_target(conn, customer_id):
    """The money that actually paid off tabs: real money in, but no more than was charged after
    write-offs. Shared by the points rule and the bonus tool so they always agree."""
    money, written_off = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN method != 'writeoff' THEN amount_cents END), 0), "
        "COALESCE(SUM(CASE WHEN method = 'writeoff' THEN amount_cents END), 0) FROM tab_repayments WHERE customer_id = ?",
        (customer_id,)).fetchone()
    charged = conn.execute("SELECT COALESCE(SUM(amount_cents - refunded_cents), 0) FROM tab_charges WHERE customer_id = ?",
                           (customer_id,)).fetchone()[0]
    return max(0, min(money, charged - written_off))


def reconcile_tab_points(conn, customer_id, ts=None, staff="square"):
    """Make a customer's tab points match the rule: points for the money that actually paid off
    tabs (not credit, not write-offs). The specific entries (tab_repaid, credit_used, ...) cover
    the normal cases; this corrects anything left over after refunds, undos and re-marking."""
    target = _tab_points_target(conn, customer_id)
    held = conn.execute(f"SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ? AND reason IN "
                        f"({','.join('?' * len(TAB_POINT_REASONS))})", (customer_id, *TAB_POINT_REASONS)).fetchone()[0]
    if target != held:
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, staff, created_at) "
            "VALUES (?, ?, ?, 'tab_adjust', 'Tab points corrected after a refund or change', ?, ?)",
            (customer_id, target - held, target - held, staff, ts or now_iso()))


def paid_by_money(conn, customer_id):
    """How much of each tab charge was paid with real money, as opposed to written off.
    {charge_id: cents}. Repayments and write-offs fill charges oldest-first in the order they
    happened (the same FIFO as tab_ledger); a negative row (a refunded repayment or a reversed
    write-off) takes back the most recent cover of its own kind."""
    charges = [dict(id=r["id"], net=r["net"], money=0, writeoff=0) for r in conn.execute(
        "SELECT id, amount_cents - refunded_cents AS net FROM tab_charges WHERE customer_id = ? ORDER BY charged_at, id",
        (customer_id,))]
    spare = {"money": 0, "writeoff": 0}  # credit not yet used by any charge
    for r in conn.execute("SELECT amount_cents, method FROM tab_repayments WHERE customer_id = ? ORDER BY created_at, id",
                          (customer_id,)):
        kind = "writeoff" if r["method"] == "writeoff" else "money"
        amount = r["amount_cents"]
        if amount >= 0:
            for c in charges:
                if amount <= 0:
                    break
                take = min(amount, c["net"] - c["money"] - c["writeoff"])
                if take > 0:
                    c[kind] += take
                    amount -= take
            spare[kind] += amount
        else:
            back = -amount
            used = min(back, spare[kind])
            spare[kind] -= used
            back -= used
            for c in reversed(charges):
                if back <= 0:
                    break
                used = min(back, c[kind])
                c[kind] -= used
                back -= used
    # Credit that arrived before a charge pays it once the charge exists.
    for c in charges:
        for kind in ("money", "writeoff"):
            take = min(spare[kind], c["net"] - c["money"] - c["writeoff"])
            if take > 0:
                c[kind] += take
                spare[kind] -= take
    # The replay above only decides *which* charges were paid with money. The amounts must agree
    # with the ledger (each charge's paid share, oldest first) and with the points rule
    # (reconcile_tab_points' target), so clamp to both: never more than a charge's paid share,
    # and in total exactly the money that paid off tabs - trimming the newest first, topping up
    # the oldest first.
    paid = {c["id"]: c["paid_cents"] for c in tab_ledger(conn, customer_id)["charges"]}
    out = {c["id"]: min(c["money"], paid.get(c["id"], 0)) for c in charges}
    target = _tab_points_target(conn, customer_id)
    excess = sum(out.values()) - target
    for c in reversed(charges):
        if excess <= 0:
            break
        cut = min(excess, out[c["id"]])
        out[c["id"]] -= cut
        excess -= cut
    for c in charges:
        if excess >= 0:
            break
        add = min(-excess, paid.get(c["id"], 0) - out[c["id"]])
        out[c["id"]] += add
        excess += add
    return out


def add_tab_charge(conn, customer, amount_cents, items, charged_at, staff=None, square_payment_id=None):
    """Record a tab charge. Breaking a rule doesn't stop it being owed; it's flagged instead."""
    cfg = settings(conn)
    ledger = tab_ledger(conn, customer["id"])
    st = tab_status(conn, customer, ledger, cfg)
    breach = None
    if st["status"] != "approved":
        breach = f"Tab not allowed (status: {st['label']})"
    elif amount_cents > st["available_cents"]:
        breach = f"Over limit (available ${st['available_cents'] / 100:.2f})"
    due_at = (parse_ts(charged_at) + timedelta(days=cfg["tab_due_days"])).isoformat(timespec="seconds")
    # Credit the customer already had (they paid before this was charged, e.g. a repayment seen
    # before its tab sale on a re-sync) pays this charge now - and earns the points now.
    credit_used = min(amount_cents, max(0, -ledger["balance_cents"]))
    cur = conn.execute(
        "INSERT INTO tab_charges(customer_id, amount_cents, items, charged_at, due_at, square_payment_id, staff, breach) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (customer["id"], amount_cents, items, charged_at, due_at, square_payment_id, staff, breach),
    )
    if credit_used > 0:
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'credit_used', 'Tab paid from credit', ?, ?, ?)",
            (customer["id"], credit_used, credit_used, square_payment_id, staff or "square", charged_at),
        )
    audit(conn, staff or "square", "tab_charge", customer["id"],
          f"${amount_cents / 100:.2f} {items or ''}" + (f" [{breach}]" if breach else ""))
    reconcile_tab_points(conn, customer["id"], charged_at)
    return cur.lastrowid


def add_repayment(conn, customer_id, amount_cents, method, note, staff, square_payment_id=None, created_at=None):
    """Record a repayment and award points for the part that paid off owed tab (not credit).
    method "writeoff" clears debt without money changing hands, so it earns nothing."""
    if amount_cents <= 0:
        raise ValueError("Repayment must be more than $0.")
    owing = tab_ledger(conn, customer_id)["owing_cents"]
    if method == "writeoff" and amount_cents > owing:
        raise ValueError(f"They only owe ${owing / 100:.2f}, so there's nothing more to write off.")
    ts = created_at or now_iso()
    cur = conn.execute(
        "INSERT INTO tab_repayments(customer_id, amount_cents, method, note, staff, square_payment_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (customer_id, amount_cents, method, note or None, staff, square_payment_id, ts),
    )
    rid = cur.lastrowid
    applied = min(amount_cents, owing)
    if applied > 0 and method != "writeoff":
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, repayment_id, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'tab_repaid', ?, ?, ?, ?, ?)",
            (customer_id, applied, applied, f"Tab repayment ({method})", rid, square_payment_id, staff, ts),
        )
    audit(conn, staff or "square", "tab_repayment", customer_id, f"${amount_cents / 100:.2f} {method} {note or ''}".strip())
    reconcile_tab_points(conn, customer_id, ts)
    return rid


def refund_tab_charge(conn, charge, refund_cents, staff="square", created_at=None):
    """Reduce a charge after a Square refund. If that leaves the customer in credit (they had already
    repaid for the refunded items), take back the points those repayments earned."""
    refund_cents = min(refund_cents, charge["amount_cents"] - charge["refunded_cents"])
    if refund_cents <= 0:
        return
    before = tab_ledger(conn, charge["customer_id"])["balance_cents"]
    conn.execute("UPDATE tab_charges SET refunded_cents = refunded_cents + ? WHERE id = ?", (refund_cents, charge["id"]))
    after = before - refund_cents
    credit_increase = max(0, -after) - max(0, -before)
    if credit_increase > 0:
        # Items returned from a debt that was written off: undo the write-off first, so the
        # customer isn't left in credit for money they never paid.
        # Only write-offs made since this charge (net of earlier reversals) can have covered it.
        written_off = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM tab_repayments "
                                   "WHERE customer_id = ? AND method = 'writeoff' AND created_at >= ?",
                                   (charge["customer_id"], charge["charged_at"])).fetchone()[0]
        undo = min(credit_increase, max(0, written_off))
        if undo > 0:
            conn.execute(
                "INSERT INTO tab_repayments(customer_id, amount_cents, method, note, staff, created_at) "
                "VALUES (?, ?, 'writeoff', 'Write-off reversed: items returned', ?, ?)",
                (charge["customer_id"], -undo, staff, created_at or now_iso()))
            credit_increase -= undo
    if credit_increase > 0:
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'tab_refund', ?, ?, ?, ?)",
            (charge["customer_id"], -credit_increase, -credit_increase, "Refund of repaid tab items",
             charge["square_payment_id"], staff, created_at or now_iso()),
        )
    audit(conn, staff, "tab_charge_refund", charge["customer_id"], f"-${refund_cents / 100:.2f} on charge #{charge['id']}")
    reconcile_tab_points(conn, charge["customer_id"], created_at)


def search_index(conn):
    """Every active customer with what the counter needs, in a handful of queries.
    Feeds the in-browser search so typing never waits on the network."""
    cfg = settings(conn)
    pts = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT customer_id, SUM(delta), SUM(spend_cents) FROM points_ledger GROUP BY customer_id")}
    with_tab = {r[0] for r in conn.execute(
        "SELECT customer_id FROM tab_charges UNION SELECT customer_id FROM tab_repayments")}
    out = []
    for c in conn.execute("SELECT * FROM customers WHERE archived_at IS NULL ORDER BY name"):
        points, spent = pts.get(c["id"], (0, 0))
        ledger = tab_ledger(conn, c["id"]) if c["id"] in with_tab else {"owing_cents": 0, "has_overdue": False}
        score = None
        if c["tab_decision"] is None and spent >= cfg["unlock_spend_cents"]:  # only these can turn on the score
            score = tab_score(conn, c["id"], ledger if "charges" in ledger else None, spent, cfg)["score"]
        status = status_for(c["tab_decision"], ledger["has_overdue"], spent, cfg, score)
        out.append({
            "id": c["id"], "name": c["name"], "sid": c["email"].split("@")[0],
            "points": points, "owing": ledger["owing_cents"], "status": status, "label": STATUS_LABELS[status],
        })
    return out


def customer_summary(conn, customer, cfg=None):
    cfg = cfg or settings(conn)
    ledger = tab_ledger(conn, customer["id"])
    return {
        "customer": customer,
        "ledger": ledger,
        "status": tab_status(conn, customer, ledger, cfg),
        "points": points_balance(conn, customer["id"]),
    }


def purchases_in(conn, start, end):
    """Points-earning spend per customer for purchases made between start and end. A refund counts
    against the purchase it reverses (by that purchase's date, whenever the refund happened). Tab
    purchases count by when they were bought (not repaid), for the part that has been repaid so far."""
    totals = {}
    for r in conn.execute(
            "SELECT customer_id, SUM(delta) AS pts FROM points_ledger p WHERE (p.reason = 'purchase' AND p.created_at >= ? "
            "AND p.created_at < ?) OR (p.reason = 'refund' AND EXISTS (SELECT 1 FROM points_ledger b WHERE b.reason = 'purchase' "
            "AND b.square_payment_id = p.square_payment_id AND b.created_at >= ? AND b.created_at < ?)) GROUP BY customer_id",
            (start, end, start, end)):
        totals[r["customer_id"]] = r["pts"]
    for (cid,) in conn.execute("SELECT DISTINCT customer_id FROM tab_charges WHERE charged_at >= ? AND charged_at < ?",
                               (start, end)).fetchall():
        by_money = paid_by_money(conn, cid)  # written-off purchases earn nothing, bonus included
        paid = sum(by_money.get(r["id"], 0) for r in conn.execute(
            "SELECT id FROM tab_charges WHERE customer_id = ? AND charged_at >= ? AND charged_at < ?", (cid, start, end)))
        totals[cid] = totals.get(cid, 0) + paid
    return totals
