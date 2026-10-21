"""Square integration: customer sync, payment/refund ingestion, webhook verification.

Everything is idempotent on Square IDs, so webhooks and polling can overlap safely.
Square is the record of sales; Fizzy derives points and tab charges from it.
"""
import base64
import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx

from . import config, logic
from .db import audit, get_setting, now_iso, set_setting

log = logging.getLogger("fizzy.square")
SQUARE_VERSION = "2025-01-23"
_sync_lock = threading.Lock()


def norm_ts(s):
    """Square timestamps ('...123Z') -> Fizzy's UTC ISO format, so string comparisons work."""
    return logic.parse_ts(s).astimezone(timezone.utc).isoformat(timespec="seconds")


class SquareClient:
    def __init__(self, token=None, base=None, transport=None):
        self.http = httpx.Client(
            base_url=base or config.SQUARE_BASE,
            headers={"Authorization": f"Bearer {token or config.SQUARE_TOKEN}", "Square-Version": SQUARE_VERSION},
            timeout=20,
            transport=transport,
        )

    def _get(self, path, params=None):
        r = self.http.get(path, params={k: v for k, v in (params or {}).items() if v})
        r.raise_for_status()
        return r.json()

    def _paged(self, path, key, params):
        cursor = None
        while True:
            data = self._get(path, {**params, "cursor": cursor})
            yield from data.get(key, [])
            cursor = data.get("cursor")
            if not cursor:
                return

    def payments(self, begin_time):
        return self._paged("/v2/payments", "payments",
                           {"begin_time": begin_time, "sort_order": "ASC", "location_id": config.SQUARE_LOCATION_ID})

    def refunds(self, begin_time):
        return self._paged("/v2/refunds", "refunds",
                           {"begin_time": begin_time, "sort_order": "ASC", "location_id": config.SQUARE_LOCATION_ID})

    def customers(self):
        return self._paged("/v2/customers", "customers", {})

    def payment(self, payment_id):
        return self._get(f"/v2/payments/{payment_id}").get("payment")

    def order(self, order_id):
        if not order_id:
            return None
        try:
            return self._get(f"/v2/orders/{order_id}").get("order")
        except httpx.HTTPStatusError:
            return None


# ---- classification -------------------------------------------------------------------------

def classify(payment, order):
    """Decide whether a payment is a normal sale, a tab charge, or a tab repayment.

    - Tab repayment: the order contains the repayment item (SQUARE_REPAYMENT_ITEM, e.g. "Tab repayment").
    - Tab charge: an "Other"/external tender whose name or note contains SQUARE_TAB_MATCH (e.g. "Fizzy Tab").
    - Otherwise a sale.
    """
    for li in (order or {}).get("line_items", []) or []:
        if (li.get("name") or "").strip().lower() == config.SQUARE_REPAYMENT_ITEM.lower():
            return "tab_repayment"
    if payment.get("source_type") in ("EXTERNAL", "OTHER") or payment.get("external_details"):
        ext = payment.get("external_details") or {}
        hay = [ext.get("source", ""), ext.get("type", ""), payment.get("note", "")]
        for t in (order or {}).get("tenders", []) or []:
            if t.get("id") == payment.get("id") or t.get("payment_id") == payment.get("id"):
                hay.append(t.get("note", ""))
        if config.SQUARE_TAB_MATCH.lower() in " ".join(h or "" for h in hay).lower():
            return "tab_charge"
    return "sale"


def describe_tender(payment):
    st = payment.get("source_type", "")
    if st == "CARD":
        brand = ((payment.get("card_details") or {}).get("card") or {}).get("card_brand", "")
        return f"card {brand}".strip().lower()
    if st == "EXTERNAL":
        ext = payment.get("external_details") or {}
        return f"other: {ext.get('source') or ext.get('type') or ''}".strip()
    return st.lower() or "unknown"


def repayment_method(payment):
    st = payment.get("source_type", "")
    if st == "CASH":
        return "cash"
    if st in ("CARD", "WALLET"):
        return "card"
    src = ((payment.get("external_details") or {}).get("source") or "").lower()
    return "payid" if "payid" in src else "other"


def describe_items(order):
    parts = []
    for li in (order or {}).get("line_items", []) or []:
        q = li.get("quantity", "1")
        parts.append(f"{q}x {li.get('name') or 'Custom amount'}")
    return ", ".join(parts)


# ---- applying effects -----------------------------------------------------------------------

def _customer_for(conn, square_customer_id):
    if not square_customer_id:
        return None
    return conn.execute("SELECT * FROM customers WHERE square_customer_id = ?", (square_customer_id,)).fetchone()


def apply_payment_effects(conn, sp):
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (sp["customer_id"],)).fetchone()
    if customer is None:
        return
    kind, amt, pid = sp["kind"], sp["amount_cents"], sp["square_payment_id"]
    if kind == "sale":
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'purchase', ?, ?, 'square', ?)",
            (customer["id"], amt, amt, sp["items"] or "Purchase", pid, sp["created_at"]),
        )
    elif kind == "tab_charge":
        logic.add_tab_charge(conn, customer, amt, sp["items"], sp["created_at"], None, pid)
    elif kind == "tab_repayment":
        # If staff already recorded this repayment by hand in Fizzy, link to it rather than double-count.
        since = (logic.parse_ts(sp["created_at"]) - timedelta(hours=24)).isoformat(timespec="seconds")
        manual = conn.execute(
            "SELECT id FROM tab_repayments WHERE customer_id = ? AND amount_cents = ? AND square_payment_id IS NULL "
            "AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
            (customer["id"], amt, since),
        ).fetchone()
        if manual:
            conn.execute("UPDATE tab_repayments SET square_payment_id = ? WHERE id = ?", (pid, manual["id"]))
            conn.execute("UPDATE points_ledger SET square_payment_id = ? WHERE repayment_id = ?", (pid, manual["id"]))
        else:
            logic.add_repayment(conn, customer["id"], amt, repayment_method(sp_raw(sp)), "Recorded in Square",
                                None, pid, sp["created_at"])


def sp_raw(sp):
    try:
        return json.loads(sp["raw_json"] or "{}")
    except ValueError:
        return {}


def undo_payment_effects(conn, sp):
    pid = sp["square_payment_id"]
    conn.execute("DELETE FROM points_ledger WHERE square_payment_id = ? AND reason IN ('purchase', 'refund', 'tab_refund')", (pid,))
    conn.execute("DELETE FROM tab_charges WHERE square_payment_id = ?", (pid,))
    for r in conn.execute("SELECT id, note FROM tab_repayments WHERE square_payment_id = ?", (pid,)).fetchall():
        if r["note"] == "Recorded in Square" or r["note"] == "Square refund":
            conn.execute("DELETE FROM points_ledger WHERE repayment_id = ?", (r["id"],))
            conn.execute("DELETE FROM tab_repayments WHERE id = ?", (r["id"],))
        else:  # hand-entered in Fizzy: keep it, just unlink
            conn.execute("UPDATE tab_repayments SET square_payment_id = NULL WHERE id = ?", (r["id"],))
            conn.execute("UPDATE points_ledger SET square_payment_id = NULL WHERE repayment_id = ?", (r["id"],))


def apply_refund_effects(conn, sp, amount, created_at):
    if sp["customer_id"] is None:
        return
    pid, cid = sp["square_payment_id"], sp["customer_id"]
    if sp["kind"] == "sale":
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'refund', 'Square refund', ?, 'square', ?)",
            (cid, -amount, -amount, pid, created_at),
        )
        audit(conn, "square", "refund", cid, f"-${amount / 100:.2f} sale refunded")
    elif sp["kind"] == "tab_charge":
        charge = conn.execute("SELECT * FROM tab_charges WHERE square_payment_id = ?", (pid,)).fetchone()
        if charge:
            logic.refund_tab_charge(conn, charge, amount, "square", created_at)
    elif sp["kind"] == "tab_repayment":
        cur = conn.execute(
            "INSERT INTO tab_repayments(customer_id, amount_cents, method, note, staff, square_payment_id, created_at) "
            "VALUES (?, ?, 'other', 'Square refund', 'square', ?, ?)",
            (cid, -amount, pid, created_at),
        )
        earned = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE square_payment_id = ? AND reason = 'tab_repaid'", (pid,)
        ).fetchone()[0]
        take = min(amount, earned)
        if take > 0:
            conn.execute(
                "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, repayment_id, square_payment_id, staff, created_at) "
                "VALUES (?, ?, ?, 'tab_refund', 'Repayment refunded', ?, ?, 'square', ?)",
                (cid, -take, -take, cur.lastrowid, pid, created_at),
            )
        audit(conn, "square", "repayment_refund", cid, f"-${amount / 100:.2f}")


def process_payment(conn, payment, order):
    """Record one Square payment. Returns the kind, or None if skipped/duplicate."""
    if payment.get("status") != "COMPLETED":
        return None
    pid = payment["id"]
    if conn.execute("SELECT 1 FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone():
        return None
    sq_cust = payment.get("customer_id") or (order or {}).get("customer_id")
    customer = _customer_for(conn, sq_cust)
    kind = classify(payment, order)
    sp = {
        "square_payment_id": pid,
        "square_order_id": payment.get("order_id"),
        "square_customer_id": sq_cust,
        "customer_id": customer["id"] if customer else None,
        "kind": kind,
        "amount_cents": int((payment.get("amount_money") or {}).get("amount", 0)),
        "tender": describe_tender(payment),
        "items": describe_items(order),
        "raw_json": json.dumps({"payment": payment, "order": order}),
        "created_at": norm_ts(payment["created_at"]),
        "processed_at": now_iso(),
    }
    conn.execute(
        "INSERT INTO square_payments VALUES (:square_payment_id, :square_order_id, :square_customer_id, :customer_id, "
        ":kind, :amount_cents, :tender, :items, :raw_json, :created_at, :processed_at)",
        sp,
    )
    apply_payment_effects(conn, sp)
    return kind


def process_refund(conn, refund):
    """Record a completed refund against a payment Fizzy already knows. Returns True if new."""
    if refund.get("status") != "COMPLETED":
        return False
    if conn.execute("SELECT 1 FROM square_refunds WHERE square_refund_id = ?", (refund["id"],)).fetchone():
        return False
    sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (refund["payment_id"],)).fetchone()
    if sp is None:
        return False
    amount = int((refund.get("amount_money") or {}).get("amount", 0))
    ts = norm_ts(refund.get("updated_at") or refund["created_at"])
    conn.execute(
        "INSERT INTO square_refunds VALUES (?, ?, ?, ?, ?, ?)",
        (refund["id"], refund["payment_id"], amount, json.dumps(refund), ts, now_iso()),
    )
    apply_refund_effects(conn, sp, amount, ts)
    return True


def reclassify(conn, square_payment_id, kind, staff):
    """Staff override when Square detection got a payment wrong."""
    sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (square_payment_id,)).fetchone()
    if sp is None or kind not in ("sale", "tab_charge", "tab_repayment") or sp["kind"] == kind:
        return
    undo_payment_effects(conn, sp)
    conn.execute("UPDATE square_payments SET kind = ? WHERE square_payment_id = ?", (kind, square_payment_id))
    sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (square_payment_id,)).fetchone()
    apply_payment_effects(conn, sp)
    for rf in conn.execute("SELECT * FROM square_refunds WHERE square_payment_id = ?", (square_payment_id,)).fetchall():
        apply_refund_effects(conn, sp, rf["amount_cents"], rf["created_at"])
    audit(conn, staff, "reclassify", sp["customer_id"], f"{square_payment_id} -> {kind}")


def link_pending(conn):
    """After customers are linked, apply payments that arrived before their customer was known."""
    rows = conn.execute(
        "SELECT sp.square_payment_id, c.id AS cid FROM square_payments sp JOIN customers c "
        "ON c.square_customer_id = sp.square_customer_id WHERE sp.customer_id IS NULL ORDER BY sp.created_at"
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE square_payments SET customer_id = ? WHERE square_payment_id = ?", (r["cid"], r["square_payment_id"]))
        sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (r["square_payment_id"],)).fetchone()
        apply_payment_effects(conn, sp)
        for rf in conn.execute("SELECT * FROM square_refunds WHERE square_payment_id = ?", (sp["square_payment_id"],)).fetchall():
            apply_refund_effects(conn, sp, rf["amount_cents"], rf["created_at"])
    return len(rows)


def sync_customers(conn, sq_customers):
    """Link Square profiles to Fizzy customers by Reference ID (fizzy-N) or email."""
    linked = created = 0
    for sc in sq_customers:
        sid = sc["id"]
        if conn.execute("SELECT 1 FROM customers WHERE square_customer_id = ?", (sid,)).fetchone():
            continue
        ref = (sc.get("reference_id") or "").strip()
        email = (sc.get("email_address") or "").strip().lower()
        row = None
        if ref.startswith("fizzy-") and ref[6:].isdigit():
            row = conn.execute("SELECT id FROM customers WHERE id = ? AND square_customer_id IS NULL", (int(ref[6:]),)).fetchone()
        if row is None and email:
            row = conn.execute("SELECT id FROM customers WHERE email = ? AND square_customer_id IS NULL", (email,)).fetchone()
        if row:
            conn.execute("UPDATE customers SET square_customer_id = ? WHERE id = ?", (sid, row["id"]))
            linked += 1
        else:
            name = " ".join(filter(None, [sc.get("given_name"), sc.get("family_name")])).strip()
            if not name:
                continue
            conn.execute(
                "INSERT INTO customers(name, email, square_customer_id, created_at) VALUES (?, ?, ?, ?)",
                (name, email or f"square:{sid}", sid, now_iso()),
            )
            created += 1
    return {"linked": linked, "created": created}


def sync(conn, client=None):
    """Full poll: customers, then payments and refunds since the cursor."""
    if not _sync_lock.acquire(blocking=False):
        return {"skipped": "sync already running"}
    try:
        client = client or SquareClient()
        result = sync_customers(conn, client.customers())
        conn.commit()
        start = get_setting(conn, "square_cursor")
        if not start:
            start = config_start()
        # Overlap the window by an hour; processing is idempotent.
        begin = (logic.parse_ts(start) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        newest = start
        counts = {"sale": 0, "tab_charge": 0, "tab_repayment": 0, "refunds": 0}
        for p in client.payments(begin):
            kind = process_payment(conn, p, client.order(p.get("order_id")) if p.get("status") == "COMPLETED" else None)
            if kind:
                counts[kind] += 1
            newest = max(newest, norm_ts(p["created_at"]))
            conn.commit()
        for rf in client.refunds(begin):
            if rf.get("payment_id") and not conn.execute(
                "SELECT 1 FROM square_payments WHERE square_payment_id = ?", (rf["payment_id"],)
            ).fetchone():
                p = client.payment(rf["payment_id"])
                if p:
                    process_payment(conn, p, client.order(p.get("order_id")))
            if process_refund(conn, rf):
                counts["refunds"] += 1
            conn.commit()
        result.update(counts)
        result["relinked"] = link_pending(conn)
        set_setting(conn, "square_cursor", newest)
        set_setting(conn, "square_last_sync", now_iso())
        set_setting(conn, "square_last_error", "")
        conn.commit()
        return result
    except Exception as e:  # keep the poller alive; show the error on the Square page
        conn.rollback()
        log.exception("Square sync failed")
        set_setting(conn, "square_last_error", f"{now_iso()}: {e}")
        conn.commit()
        return {"error": str(e)}
    finally:
        _sync_lock.release()


def config_start():
    import os
    s = os.environ.get("SQUARE_SYNC_FROM", "").strip()
    if s:
        return norm_ts(s if "T" in s else s + "T00:00:00+10:00")
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")


def verify_webhook(body: bytes, signature: str, key=None, url=None):
    key = key or config.SQUARE_WEBHOOK_KEY
    url = url or config.SQUARE_WEBHOOK_URL
    if not key or not signature:
        return False
    digest = hmac.new(key.encode(), url.encode() + body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
