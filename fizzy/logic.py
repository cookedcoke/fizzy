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


def tab_status(conn, customer, ledger=None, cfg=None):
    cfg = cfg or settings(conn)
    ledger = ledger or tab_ledger(conn, customer["id"])
    spent = spend_cents(conn, customer["id"])
    decision = customer["tab_decision"]
    if decision == "approved":
        status = "blocked_overdue" if ledger["has_overdue"] else "approved"
        if status == "approved" and cfg["tabs_paused"]:
            status = "paused"
    elif decision in ("rejected", "revoked"):
        status = decision
    else:
        status = "eligible" if spent >= cfg["unlock_spend_cents"] else "locked"
    can_charge = status == "approved"
    available = max(0, cfg["tab_limit_cents"] - ledger["owing_cents"]) if can_charge else 0
    return {
        "status": status,
        "label": STATUS_LABELS[status],
        "can_charge": can_charge and available > 0,
        "available_cents": available,
        "spend_cents": spent,
        "unlock_spend_cents": cfg["unlock_spend_cents"],
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


def adjust_points(conn, customer_id, delta, note, staff):
    if not note or not note.strip():
        raise ValueError("A reason is required for manual points changes.")
    if delta == 0:
        raise ValueError("Points change can't be zero.")
    conn.execute(
        "INSERT INTO points_ledger(customer_id, delta, reason, note, staff, created_at) VALUES (?, ?, 'manual', ?, ?, ?)",
        (customer_id, delta, note.strip(), staff, now_iso()),
    )
    audit(conn, staff, "points_adjust", customer_id, f"{delta:+d}: {note.strip()}")


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
    cur = conn.execute(
        "INSERT INTO tab_charges(customer_id, amount_cents, items, charged_at, due_at, square_payment_id, staff, breach) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (customer["id"], amount_cents, items, charged_at, due_at, square_payment_id, staff, breach),
    )
    audit(conn, staff or "square", "tab_charge", customer["id"],
          f"${amount_cents / 100:.2f} {items or ''}" + (f" [{breach}]" if breach else ""))
    return cur.lastrowid


def add_repayment(conn, customer_id, amount_cents, method, note, staff, square_payment_id=None, created_at=None):
    """Record a repayment and award points for the part that paid off owed tab (not credit).
    method "writeoff" clears debt without money changing hands, so it earns nothing."""
    if amount_cents <= 0:
        raise ValueError("Repayment must be more than $0.")
    owing = tab_ledger(conn, customer_id)["owing_cents"]
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
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'tab_refund', ?, ?, ?, ?)",
            (charge["customer_id"], -credit_increase, -credit_increase, "Refund of repaid tab items",
             charge["square_payment_id"], staff, created_at or now_iso()),
        )
    audit(conn, staff, "tab_charge_refund", charge["customer_id"], f"-${refund_cents / 100:.2f} on charge #{charge['id']}")


def customer_summary(conn, customer, cfg=None):
    cfg = cfg or settings(conn)
    ledger = tab_ledger(conn, customer["id"])
    return {
        "customer": customer,
        "ledger": ledger,
        "status": tab_status(conn, customer, ledger, cfg),
        "points": points_balance(conn, customer["id"]),
    }
