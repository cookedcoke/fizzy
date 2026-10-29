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
SQUARE_VERSION = "2026-09-16"
_sync_lock = threading.Lock()
_sync_pending = threading.Event()   # a sync was requested while one was running: run again after


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

    def _post(self, path, body):
        r = self.http.post(path, json=body)
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

    # Windows are by *last update*, not creation: a sale taken offline on the Terminal is uploaded
    # later with its original created_at, and a refund can complete after it was created.
    def payments(self, since):
        return self._paged("/v2/payments", "payments", {
            "updated_at_begin_time": since, "sort_field": "UPDATED_AT", "sort_order": "ASC",
            "location_id": config.SQUARE_LOCATION_ID})

    def refunds(self, since):
        return self._paged("/v2/refunds", "refunds", {
            "updated_at_begin_time": since, "sort_field": "UPDATED_AT", "sort_order": "ASC",
            "location_id": config.SQUARE_LOCATION_ID})

    def location_ids(self):
        if config.SQUARE_LOCATION_ID:
            return [config.SQUARE_LOCATION_ID]
        return [loc["id"] for loc in self._get("/v2/locations").get("locations", [])]

    def orders(self, since):
        """Completed orders updated since `since`, oldest first. Tab sales are $0 orders with
        no payment, so they only show up here."""
        body = {
            "location_ids": self.location_ids(),
            "limit": 500,
            "query": {
                "filter": {"state_filter": {"states": ["COMPLETED"]},
                           "date_time_filter": {"updated_at": {"start_at": since}}},
                "sort": {"sort_field": "UPDATED_AT", "sort_order": "ASC"},
            },
        }
        while True:
            data = self._post("/v2/orders/search", body)
            yield from data.get("orders", [])
            if not data.get("cursor"):
                return
            body["cursor"] = data["cursor"]

    def customers(self):
        return self._paged("/v2/customers", "customers", {})

    def catalog_items(self):
        """Every catalogue item (with its variations). Needs the ITEMS_READ permission."""
        return self._paged("/v2/catalog/list", "objects", {"types": "ITEM"})

    def refund(self, refund_id):
        return self._get(f"/v2/refunds/{refund_id}").get("refund")

    def payment(self, payment_id):
        return self._get(f"/v2/payments/{payment_id}").get("payment")

    def order(self, order_id):
        if not order_id:
            return None
        try:
            return self._get(f"/v2/orders/{order_id}").get("order")
        except httpx.HTTPStatusError as e:
            # Only "not found" means there's no order. Anything else (a 5xx, rate limiting) must fail
            # the item so it's retried - otherwise a repayment would be read as a plain sale.
            if e.response.status_code == 404:
                return None
            raise


# ---- classification -------------------------------------------------------------------------

def classify(payment, order):
    """What a payment is for: "tab_repayment" if its order has the repayment item, "tab_charge"
    if paid with one of SQUARE_TAB_TENDERS (off by default), otherwise "sale".
    Tab sales themselves are found from orders (see process_order), not payments."""
    if repayment_total(order):
        return "tab_repayment"
    if (payment.get("source_type") or "").upper() in config.SQUARE_TAB_TENDERS:
        return "tab_charge"
    return "sale"


def _money(obj, key):
    return int(((obj or {}).get(key) or {}).get("amount", 0))


def repayment_total(order):
    """Total of the "Tab repayment" line items in an order (0 if none or not configured)."""
    if not config.SQUARE_REPAYMENT_ITEM:
        return 0
    return sum(_money(li, "total_money") for li in (order or {}).get("line_items", []) or []
               if (li.get("name") or "").strip().lower() == config.SQUARE_REPAYMENT_ITEM.lower())


def tab_discount_total(order):
    """How much of an order was put on the tab: the amount the "Fizzy Tab" discount took off."""
    if not config.SQUARE_TAB_DISCOUNT:
        return 0
    return sum(_money(d, "applied_money") for d in (order or {}).get("discounts", []) or []
               if _is_tab_discount(d.get("name")))


def _is_tab_discount(name):
    return bool(config.SQUARE_TAB_DISCOUNT) and config.SQUARE_TAB_DISCOUNT.lower() in (name or "").lower()


def return_tab_total(ret):
    """How much of a return came off the tab: only the "Fizzy Tab" discount on the returned items,
    not other discounts (a returned item that was partly paid only reduces the tab part)."""
    discounts = {d.get("uid"): d for d in ret.get("return_discounts", []) or []}
    total, itemised = 0, False
    for li in ret.get("return_line_items", []) or []:
        for ad in li.get("applied_discounts", []) or []:
            itemised = True
            d = discounts.get(ad.get("discount_uid"))
            if d and _is_tab_discount(d.get("name")):
                total += _money(ad, "applied_money")
    if itemised:
        return total
    # Older/simpler payloads without per-discount detail: fall back to the line's discount total,
    # but only when every discount on the return is the tab discount.
    if discounts and not all(_is_tab_discount(d.get("name")) for d in discounts.values()):
        return 0
    return sum(_money(li, "total_discount_money") for li in ret.get("return_line_items", []) or [])


def describe_tender(payment):
    st = payment.get("source_type", "")
    if st == "CARD":
        brand = ((payment.get("card_details") or {}).get("card") or {}).get("card_brand", "")
        return f"card {brand}".strip().lower()
    if st == "EXTERNAL":
        ext = payment.get("external_details") or {}
        return f"other {ext.get('source') or ''}".strip()
    if st == "SQUARE_ACCOUNT":
        return "house account"
    return st.lower() or "unknown"


def repayment_method(payment):
    st = payment.get("source_type", "")
    if st == "CASH":
        return "cash"
    if st in ("CARD", "WALLET"):
        return "card"
    return "other"


def describe_items(order, skip_repayment=False):
    parts = []
    for li in (order or {}).get("line_items", []) or []:
        if skip_repayment and (li.get("name") or "").strip().lower() == config.SQUARE_REPAYMENT_ITEM.lower():
            continue
        q = li.get("quantity", "1")
        var = (li.get("variation_name") or "").strip()
        name = (li.get("name") or "Custom amount") + (f" ({var})" if var and var.lower() != "regular" else "")
        parts.append(f"{q}× {name}")
    return ", ".join(parts)


# ---- applying effects -----------------------------------------------------------------------

def _customer_for(conn, square_customer_id):
    """The Fizzy customer for a Square profile, following merges (square_aliases)."""
    if not square_customer_id:
        return None
    row = conn.execute("SELECT * FROM customers WHERE square_customer_id = ?", (square_customer_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT c.* FROM square_aliases a JOIN customers c ON c.id = a.customer_id "
                           "WHERE a.square_customer_id = ?", (square_customer_id,)).fetchone()
    return row


MATCH_WINDOW = timedelta(hours=2)


def apply_payment_effects(conn, sp):
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (sp["customer_id"],)).fetchone()
    if customer is None:
        return
    kind, amt, pid = sp["kind"], sp["amount_cents"], sp["square_payment_id"]
    if amt <= 0:
        return  # e.g. the $0 cash "payment" that completes a fully tabbed sale - nothing to record
    if kind == "sale":
        conn.execute(
            "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, square_payment_id, staff, created_at) "
            "VALUES (?, ?, ?, 'purchase', ?, ?, 'square', ?)",
            (customer["id"], amt, amt, sp["items"] or "Purchase", pid, sp["created_at"]),
        )
    elif kind == "tab_charge":
        # If staff added this same charge by hand around the same time (Tools), link to it rather
        # than charge twice. Kept to a short window so a genuinely separate sale isn't swallowed.
        lo = (logic.parse_ts(sp["created_at"]) - MATCH_WINDOW).isoformat(timespec="seconds")
        hi = (logic.parse_ts(sp["created_at"]) + MATCH_WINDOW).isoformat(timespec="seconds")
        manual = conn.execute(
            "SELECT id FROM tab_charges WHERE customer_id = ? AND amount_cents = ? AND square_payment_id IS NULL "
            "AND charged_at BETWEEN ? AND ? ORDER BY charged_at LIMIT 1", (customer["id"], amt, lo, hi)).fetchone()
        if manual:
            conn.execute("UPDATE tab_charges SET square_payment_id = ? WHERE id = ?", (pid, manual["id"]))
            audit(conn, "square", "linked_hand_charge", customer["id"], f"{pid} -> charge #{manual['id']}")
        else:
            logic.add_tab_charge(conn, customer, amt, sp["items"], sp["created_at"], None, pid)
    elif kind == "tab_repayment":
        # If staff already recorded this repayment by hand in Fizzy, link to it rather than double-count.
        lo = (logic.parse_ts(sp["created_at"]) - MATCH_WINDOW).isoformat(timespec="seconds")
        hi = (logic.parse_ts(sp["created_at"]) + MATCH_WINDOW).isoformat(timespec="seconds")
        manual = conn.execute(
            "SELECT id FROM tab_repayments WHERE customer_id = ? AND amount_cents = ? AND square_payment_id IS NULL "
            "AND method != 'writeoff' AND created_at BETWEEN ? AND ? ORDER BY created_at DESC LIMIT 1",
            (customer["id"], amt, lo, hi),
        ).fetchone()
        if manual:
            conn.execute("UPDATE tab_repayments SET square_payment_id = ? WHERE id = ?", (pid, manual["id"]))
            conn.execute("UPDATE points_ledger SET square_payment_id = ? WHERE repayment_id = ?", (pid, manual["id"]))
            audit(conn, "square", "linked_hand_repayment", customer["id"], f"{pid} -> repayment #{manual['id']}")
        else:
            logic.add_repayment(conn, customer["id"], amt, repayment_method(sp_raw(sp).get("payment") or {}), "Recorded in Square",
                                None, pid, sp["created_at"])


def sp_raw(sp):
    try:
        return json.loads(sp["raw_json"] or "{}")
    except ValueError:
        return {}


def undo_payment_effects(conn, sp):
    pid = sp["square_payment_id"]
    conn.execute("DELETE FROM points_ledger WHERE square_payment_id = ? AND reason IN ('purchase', 'refund', 'tab_refund', 'credit_used')", (pid,))
    conn.execute("DELETE FROM tab_charges WHERE square_payment_id = ? AND staff IS NULL", (pid,))
    # A hand-entered charge linked to this payment is kept, but the refunds this payment applied
    # to it come off again (they're re-applied wherever the payment ends up).
    refunded = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM square_refunds WHERE square_payment_id = ?", (pid,)).fetchone()[0]
    conn.execute("UPDATE tab_charges SET refunded_cents = MAX(0, refunded_cents - ?), square_payment_id = NULL "
                 "WHERE square_payment_id = ?", (refunded, pid))
    for r in conn.execute("SELECT id, note FROM tab_repayments WHERE square_payment_id = ?", (pid,)).fetchall():
        if r["note"] == "Recorded in Square" or r["note"] == "Square refund":
            conn.execute("DELETE FROM points_ledger WHERE repayment_id = ?", (r["id"],))
            conn.execute("DELETE FROM tab_repayments WHERE id = ?", (r["id"],))
        else:  # hand-entered in Fizzy: keep it, just unlink
            conn.execute("UPDATE tab_repayments SET square_payment_id = NULL WHERE id = ?", (r["id"],))
            conn.execute("UPDATE points_ledger SET square_payment_id = NULL WHERE repayment_id = ?", (r["id"],))
    if sp["customer_id"] is not None:
        logic.reconcile_tab_points(conn, sp["customer_id"])


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
        held = conn.execute(  # points this repayment still has, after any earlier partial refunds
            "SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE square_payment_id = ? AND reason IN ('tab_repaid', 'tab_refund')",
            (pid,)).fetchone()[0]
        take = min(amount, max(0, held))
        if take > 0:
            conn.execute(
                "INSERT INTO points_ledger(customer_id, delta, spend_cents, reason, note, repayment_id, square_payment_id, staff, created_at) "
                "VALUES (?, ?, ?, 'tab_refund', 'Repayment refunded', ?, ?, 'square', ?)",
                (cid, -take, -take, cur.lastrowid, pid, created_at),
            )
        audit(conn, "square", "repayment_refund", cid, f"-${amount / 100:.2f}")
        logic.reconcile_tab_points(conn, cid, created_at)


def _insert_sp(conn, sp):
    conn.execute(
        "INSERT INTO square_payments VALUES (:square_payment_id, :square_order_id, :square_customer_id, :customer_id, "
        ":kind, :amount_cents, :tender, :items, :raw_json, :created_at, :processed_at)",
        sp,
    )
    apply_payment_effects(conn, sp)


def process_payment(conn, payment, order):
    """Record one Square payment. Returns the kind, or None if skipped/duplicate.

    A payment for an order that both repays the tab and buys something is split in two:
    the repayment part (row id = payment id) and the rest as a sale (row id = "<id>:sale")."""
    if payment.get("status") != "COMPLETED":
        return None
    pid = payment["id"]
    if conn.execute("SELECT 1 FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone():
        return None
    sq_cust = payment.get("customer_id") or (order or {}).get("customer_id")
    customer = _customer_for(conn, sq_cust)
    kind = classify(payment, order)
    amount = int((payment.get("amount_money") or {}).get("amount", 0))
    base = {
        "square_order_id": payment.get("order_id"),
        "square_customer_id": sq_cust,
        "customer_id": customer["id"] if customer else None,
        "tender": describe_tender(payment),
        "raw_json": json.dumps({"payment": payment, "order": order}),
        "created_at": norm_ts(payment["created_at"]),
        "processed_at": now_iso(),
    }
    sale_part = 0
    if kind == "tab_repayment":
        # With split tenders, earlier payments for the same order may already cover the repayment.
        done = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM square_payments WHERE square_order_id = ? AND kind = 'tab_repayment'",
            (payment.get("order_id"),),
        ).fetchone()[0]
        rep_part = max(0, min(amount, repayment_total(order) - done))
        sale_part = amount - rep_part
        if rep_part == 0:
            kind, sale_part = "sale", 0
        else:
            amount = rep_part
    _insert_sp(conn, {**base, "square_payment_id": pid, "kind": kind, "amount_cents": amount,
                      "items": "Tab repayment" if kind == "tab_repayment" else describe_items(order)})
    if sale_part > 0:
        _insert_sp(conn, {**base, "square_payment_id": f"{pid}:sale", "kind": "sale", "amount_cents": sale_part,
                          "items": describe_items(order, skip_repayment=True), "raw_json": None})
    return kind


def process_order(conn, order):
    """Record the tab part of a completed order (the "Fizzy Tab" discount), and any returns of
    items from earlier tab orders. Returns True if anything new was recorded."""
    if order.get("state") != "COMPLETED":
        return False
    new = False
    tab = tab_discount_total(order)
    oid = f"order:{order['id']}"
    if tab > 0 and not conn.execute("SELECT 1 FROM square_payments WHERE square_payment_id = ?", (oid,)).fetchone():
        sq_cust = order.get("customer_id")
        customer = _customer_for(conn, sq_cust)
        if customer is None:  # payment already claimed by staff before this tab part synced: same person
            owner = conn.execute("SELECT customer_id FROM square_payments WHERE square_order_id = ? AND customer_id IS NOT NULL "
                                 "LIMIT 1", (order["id"],)).fetchone()
            customer = {"id": owner[0]} if owner else None
        _insert_sp(conn, {
            "square_payment_id": oid, "square_order_id": order["id"], "square_customer_id": sq_cust,
            "customer_id": customer["id"] if customer else None, "kind": "tab_charge", "amount_cents": tab,
            "tender": "Fizzy Tab discount", "items": describe_items(order), "raw_json": json.dumps({"order": order}),
            "created_at": norm_ts(order.get("closed_at") or order["created_at"]), "processed_at": now_iso(),
        })
        new = True
    # Returns: items handed back from a tab order reduce what's owed by the tab discount on them.
    for ret in order.get("returns", []) or []:
        src = ret.get("source_order_id")
        rid = f"return:{order['id']}:{src}"
        if not src or conn.execute("SELECT 1 FROM square_refunds WHERE square_refund_id = ?", (rid,)).fetchone():
            continue
        sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (f"order:{src}",)).fetchone()
        if sp is None:
            continue
        amount = return_tab_total(ret)
        if amount <= 0:
            continue
        ts = norm_ts(order.get("closed_at") or order["created_at"])
        conn.execute("INSERT INTO square_refunds VALUES (?, ?, ?, ?, ?, ?)",
                     (rid, sp["square_payment_id"], amount, json.dumps(ret), ts, now_iso()))
        apply_refund_effects(conn, sp, amount, ts)
        new = True
    return new


def _refunded_so_far(conn, row_id):
    return conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM square_refunds WHERE square_payment_id = ?",
                        (row_id,)).fetchone()[0]


def _record_refund(conn, refund_id, sp, amount, raw, ts):
    amount = min(amount, max(0, sp["amount_cents"] - _refunded_so_far(conn, sp["square_payment_id"])))
    if amount <= 0:
        return
    conn.execute("INSERT INTO square_refunds VALUES (?, ?, ?, ?, ?, ?)",
                 (refund_id, sp["square_payment_id"], amount, raw, ts, now_iso()))
    apply_refund_effects(conn, sp, amount, ts)


def process_refund(conn, refund, return_order=None):
    """Record a completed refund against a payment Fizzy already knows. Returns True if new.

    For a payment that was split into a tab repayment plus a purchase, the refund is shared out
    using the returned items when Square says which they were (return_order), otherwise the
    purchase part is refunded first. Each part is capped at what's left of it."""
    if refund.get("status") != "COMPLETED":
        return False
    rid = refund["id"]
    if conn.execute("SELECT 1 FROM square_refunds WHERE square_refund_id IN (?, ?, ?)",
                    (rid, f"{rid}:sale", f"{rid}:rep")).fetchone():
        return False
    sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (refund["payment_id"],)).fetchone()
    if sp is None:
        return False
    amount = int((refund.get("amount_money") or {}).get("amount", 0))
    ts = norm_ts(refund.get("updated_at") or refund["created_at"])
    raw = json.dumps(refund)
    sale = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (f"{sp['square_payment_id']}:sale",)).fetchone()
    if sale is None or sp["kind"] != "tab_repayment":
        _record_refund(conn, rid, sp, amount, raw, ts)
        return True
    sale_left = sale["amount_cents"] - _refunded_so_far(conn, sale["square_payment_id"])
    rep_left = sp["amount_cents"] - _refunded_so_far(conn, sp["square_payment_id"])
    lines = [li for ret in (return_order or {}).get("returns", []) or [] for li in ret.get("return_line_items", []) or []]
    if lines:  # Square says which items came back
        returned_rep = sum(_money(li, "total_money") for li in lines if config.SQUARE_REPAYMENT_ITEM
                           and (li.get("name") or "").strip().lower() == config.SQUARE_REPAYMENT_ITEM.lower())
        # Whatever the purchase part can't absorb must be repayment money, whatever the lines say.
        rep_part = min(max(returned_rep, amount - sale_left), amount, rep_left)
        sale_part = min(amount - rep_part, sale_left)
    else:  # no item detail: the purchase part first, the rest from the repayment
        sale_part = min(amount, sale_left)
        rep_part = min(amount - sale_part, rep_left)
    if sale_part > 0:
        _record_refund(conn, f"{rid}:sale", sale, sale_part, raw, ts)
    if rep_part > 0:
        _record_refund(conn, f"{rid}:rep", sp, rep_part, raw, ts)
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


def assign_customer(conn, square_payment_id, customer, staff):
    """Staff fix for a Square payment with the wrong (usually no) customer attached: move it,
    and anything split from it, to `customer` and redo its points / tab charge / repayment."""
    base = square_payment_id.removesuffix(":sale")
    _write_lock(conn)  # read and move under the write lock, so a sync can't link it at the same time
    rows = conn.execute("SELECT * FROM square_payments WHERE square_payment_id IN (?, ?)", (base, f"{base}:sale")).fetchall()
    if not rows:
        raise ValueError("Fizzy hasn't seen that payment.")
    order = next((r["square_order_id"] for r in rows if r["square_order_id"]), None)
    if order:  # the rest of the same checkout (e.g. the tab charge from the order) moves with it
        rows = conn.execute("SELECT * FROM square_payments WHERE square_payment_id IN (?, ?) OR square_order_id = ?",
                            (base, f"{base}:sale", order)).fetchall()
    for sp in rows:
        if sp["customer_id"] is not None:
            undo_payment_effects(conn, sp)
        conn.execute("UPDATE square_payments SET customer_id = ? WHERE square_payment_id = ?", (customer["id"], sp["square_payment_id"]))
        sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (sp["square_payment_id"],)).fetchone()
        apply_payment_effects(conn, sp)
        for rf in conn.execute("SELECT * FROM square_refunds WHERE square_payment_id = ?", (sp["square_payment_id"],)).fetchall():
            apply_refund_effects(conn, sp, rf["amount_cents"], rf["created_at"])
    audit(conn, staff, "assign_customer", customer["id"], f"{base} ({', '.join(r['kind'] for r in rows)})")


# ---- claiming a sale at the counter -----------------------------------------------------------
# Staff ring a sale up in Square without picking a customer, then claim it from the customer's
# page in Fizzy. One checkout can be several rows (a tab charge from the order, the payment, and
# a ":sale" split), so they're grouped by order and claimed together.

CLAIM_HOURS = 6


def _checkout_key(row):
    return f"o:{row['square_order_id']}" if row["square_order_id"] else f"p:{row['square_payment_id'].removesuffix(':sale')}"


def unclaimed(conn, hours=CLAIM_HOURS, limit=6):
    """Recent checkouts with nobody attached, newest first."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    groups = {}
    for r in conn.execute("SELECT * FROM square_payments WHERE customer_id IS NULL AND created_at >= ? "
                          "ORDER BY created_at DESC", (since,)):
        g = groups.setdefault(_checkout_key(r), {"key": _checkout_key(r), "at": r["created_at"], "items": "",
                                                 "sale_cents": 0, "tab_cents": 0, "repay_cents": 0, "tenders": []})
        g[{"sale": "sale_cents", "tab_charge": "tab_cents", "tab_repayment": "repay_cents"}[r["kind"]]] += r["amount_cents"]
        g["at"] = max(g["at"], r["created_at"])
        if r["kind"] != "tab_repayment" and r["items"] and not g["items"]:
            g["items"] = r["items"]
        if r["tender"] and r["tender"] not in g["tenders"]:
            g["tenders"].append(r["tender"])
    out = [g for g in groups.values() if g["sale_cents"] or g["tab_cents"] or g["repay_cents"]]
    out.sort(key=lambda g: g["at"], reverse=True)
    return out[:limit]


def _write_lock(conn):
    """Take SQLite's write lock before reading, so check-then-write can't race another connection."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def claim(conn, key, customer, staff):
    """Attach one unclaimed checkout (see unclaimed) to a customer. Raises ValueError if it's gone,
    or if any part of it already belongs to someone (a checkout is never split between people)."""
    _write_lock(conn)
    kind, _, ident = key.partition(":")
    if kind == "o":
        rows = conn.execute("SELECT * FROM square_payments WHERE square_order_id = ?", (ident,)).fetchall()
    elif kind == "p":
        rows = conn.execute("SELECT * FROM square_payments WHERE square_payment_id IN (?, ?)",
                            (ident, f"{ident}:sale")).fetchall()
    else:
        rows = []
    if not rows or any(r["customer_id"] is not None for r in rows):
        # someone (or a sync) got there first: never move a sale that's already somebody's
        raise ValueError("That sale has already been claimed.")
    bases = {r["square_payment_id"].removesuffix(":sale") for r in rows}
    for base in sorted(bases):
        assign_customer(conn, base, customer, staff)
    return len(bases)


def link_pending(conn):
    """After customers are linked, apply payments that arrived before their customer was known."""
    rows = conn.execute(
        "SELECT sp.square_payment_id, c.id AS cid FROM square_payments sp JOIN customers c "
        "ON c.square_customer_id = sp.square_customer_id WHERE sp.customer_id IS NULL ORDER BY sp.created_at"
    ).fetchall()
    linked = 0
    for r in rows:
        # Only if nobody claimed it in the meantime (claims hold the write lock while they work).
        if conn.execute("UPDATE square_payments SET customer_id = ? WHERE square_payment_id = ? AND customer_id IS NULL",
                        (r["cid"], r["square_payment_id"])).rowcount == 0:
            continue
        linked += 1
        sp = conn.execute("SELECT * FROM square_payments WHERE square_payment_id = ?", (r["square_payment_id"],)).fetchone()
        apply_payment_effects(conn, sp)
        for rf in conn.execute("SELECT * FROM square_refunds WHERE square_payment_id = ?", (sp["square_payment_id"],)).fetchall():
            apply_refund_effects(conn, sp, rf["amount_cents"], rf["created_at"])
    return linked


def sync_customers(conn, sq_customers):
    """Link Square profiles to Fizzy customers by Reference ID (fizzy-N) or email; add new people.

    A profile that can't be linked or added (e.g. a second Square profile with an email already
    used by someone in Fizzy) is skipped and listed on the Square page instead of failing the sync."""
    linked = created = 0
    conflicts = []
    for sc in sq_customers:
        sid = sc["id"]
        if conn.execute("SELECT 1 FROM customers WHERE square_customer_id = ? UNION "
                        "SELECT 1 FROM square_aliases WHERE square_customer_id = ?", (sid, sid)).fetchone():
            continue
        ref = (sc.get("reference_id") or "").strip()
        email = (sc.get("email_address") or "").strip().lower()
        name = " ".join(" ".join(filter(None, [sc.get("given_name"), sc.get("family_name")])).split())
        row = None
        if ref.startswith("fizzy-") and ref[6:].isdigit():
            row = conn.execute("SELECT id, merged_into, square_customer_id, archived_at FROM customers WHERE id = ?",
                               (int(ref[6:]),)).fetchone()
            for _ in range(10):  # exported before one or more merges: follow them to the survivor
                if row is None or not row["merged_into"]:
                    break
                row = conn.execute("SELECT id, merged_into, square_customer_id, archived_at FROM customers WHERE id = ?",
                                   (row["merged_into"],)).fetchone()
            if row is not None and (row["square_customer_id"] or row["archived_at"]):
                row = None
        if row is None and email:
            row = conn.execute("SELECT id FROM customers WHERE email = ? AND square_customer_id IS NULL "
                               "AND archived_at IS NULL", (email,)).fetchone()
        if row:
            conn.execute("UPDATE customers SET square_customer_id = ? WHERE id = ?", (sid, row["id"]))
            linked += 1
            continue
        if not name:
            continue
        clash = email and conn.execute("SELECT name FROM customers WHERE email = ?", (email,)).fetchone()
        if clash:
            conflicts.append({"square_id": sid, "name": name, "email": email, "existing": clash["name"]})
            continue
        conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES (?, ?, ?, ?)",
                     (name, email or f"square:{sid}", sid, now_iso()))
        created += 1
    set_setting(conn, "square_customer_conflicts", json.dumps(conflicts[:50]))
    return {"linked": linked, "created": created, "conflicts": len(conflicts)}


def _safely(conn, failed, key, fn):
    """Run one item's processing in a savepoint. If it fails it's rolled back and added to
    `failed` (key -> error); the caller keeps it on a retry list so it's never lost."""
    conn.execute("SAVEPOINT item")
    try:
        result = fn()
        conn.execute("RELEASE item")
        conn.commit()
        return True, result
    except Exception as e:
        conn.execute("ROLLBACK TO item")
        conn.execute("RELEASE item")
        log.exception("Square sync: %s failed", key)
        failed[key] = str(e) or e.__class__.__name__
        return False, None


def _ts(obj):
    return norm_ts(obj.get("updated_at") or obj.get("closed_at") or obj["created_at"])


def _process(conn, client, typ, obj):
    """Process one payment / order / refund. Returns a count key or None."""
    if typ == "payment":
        if obj.get("status") != "COMPLETED":
            return None
        return process_payment(conn, obj, client.order(obj.get("order_id")))
    if typ == "order":
        return "tab_orders" if process_order(conn, obj) else None
    if obj.get("payment_id") and not conn.execute(
            "SELECT 1 FROM square_payments WHERE square_payment_id = ?", (obj["payment_id"],)).fetchone():
        p = client.payment(obj["payment_id"])
        if p:
            process_payment(conn, p, client.order(p.get("order_id")))
    if obj.get("status") != "COMPLETED":
        return None
    return "refunds" if process_refund(conn, obj, client.order(obj.get("order_id"))) else None


def _refetch(client, typ, item_id):
    return {"payment": client.payment, "order": client.order, "refund": client.refund}[typ](item_id)


CATALOG_EVERY = timedelta(hours=6)


def sync_catalog(conn, client, force=False):
    """Copy Square's item list (each variation, with its price) so the Profit page and restocks
    can list every item - even ones never sold - and match sales by Square's ids, not names.
    Runs every few hours, or on demand. A token without ITEMS_READ just leaves it empty."""
    if not hasattr(client, "catalog_items"):
        return None
    last = get_setting(conn, "catalog_last_sync")
    if not force and last and datetime.now(timezone.utc) - logic.parse_ts(last) < CATALOG_EVERY:
        return None
    try:
        items = list(client.catalog_items())
    except Exception as e:  # don't hammer Square every sync: note it and try again in an hour
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403:
            why = "Square says this token can't read items - give it the ITEMS_READ permission."
        elif isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 401:
            why = "Square didn't accept the access token - check SQUARE_ACCESS_TOKEN."
        elif isinstance(e, httpx.HTTPStatusError):
            why = f"Square returned {e.response.status_code}."
        else:
            why = f"Couldn't reach Square ({e.__class__.__name__})."
        set_setting(conn, "catalog_error", why)
        set_setting(conn, "catalog_last_sync", (datetime.now(timezone.utc) - CATALOG_EVERY + timedelta(hours=1)).isoformat(timespec="seconds"))
        conn.commit()
        return 0
    ts = now_iso()
    conn.execute("UPDATE catalog_items SET deleted = 1")
    n = 0
    for it in items:
        if it.get("is_deleted") or it.get("type") != "ITEM":
            continue
        data = it.get("item_data") or {}
        name = " ".join((data.get("name") or "").split())
        variations = [v for v in data.get("variations", []) or [] if not v.get("is_deleted")]
        for v in variations:
            if not v.get("id"):
                continue
            vd = v.get("item_variation_data") or {}
            vname = " ".join((vd.get("name") or "").split())
            display = name if len(variations) == 1 and vname.lower() in ("", "regular") else f"{name} ({vname or 'Regular'})"
            conn.execute(
                "INSERT INTO catalog_items(variation_id, item_id, item_name, variation_name, display, price_cents, deleted, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?) ON CONFLICT(variation_id) DO UPDATE SET item_id = excluded.item_id, "
                "item_name = excluded.item_name, variation_name = excluded.variation_name, display = excluded.display, "
                "price_cents = excluded.price_cents, deleted = 0, updated_at = excluded.updated_at",
                (v["id"], it["id"], name, vname or None, display, (vd.get("price_money") or {}).get("amount"), ts))
            n += 1
    set_setting(conn, "catalog_error", "")
    set_setting(conn, "catalog_last_sync", ts)
    set_setting(conn, "catalog_last_ok", ts)
    conn.commit()
    return n


def _sync_once(conn, client, catalog=False):
    retry = json.loads(get_setting(conn, "square_retry") or "{}")   # "type:id" -> last error
    failed = {}
    result = sync_customers(conn, client.customers())
    conn.commit()
    try:
        sync_catalog(conn, client, force=catalog)
    except Exception:  # the item list is a nice-to-have: never let it stop sales syncing
        conn.rollback()
        log.exception("Square catalogue sync failed")
    start = get_setting(conn, "square_cursor") or config_start()
    resync = get_setting(conn, "square_resync_from")
    if resync:
        start = min(start, resync)
    # Overlap the window by an hour; processing is idempotent.
    since = (logic.parse_ts(start) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    newest = start
    counts = {"sale": 0, "tab_charge": 0, "tab_repayment": 0, "refunds": 0, "tab_orders": 0}

    def run(typ, obj):
        key = f"{typ}:{obj['id']}"
        ok, kind = _safely(conn, failed, key, lambda: _process(conn, client, typ, obj))
        if ok:
            retry.pop(key, None)
            failed.pop(key, None)  # e.g. its re-fetch blipped, but it was in the window and went through
            if kind:
                counts[kind] += 1

    # Items that failed last time are fetched again by id (the window may have moved past them).
    for key in list(retry):
        typ, item_id = key.split(":", 1)
        try:
            obj = _refetch(client, typ, item_id)
        except Exception as e:
            failed[key] = str(e) or e.__class__.__name__
            continue
        if obj:
            run(typ, obj)
        else:
            retry.pop(key, None)

    # Payments and tab orders are processed together in time order, so a tab sale is recorded
    # before the repayment that pays it off (matters on a re-sync or backfill).
    items = []
    for p in client.payments(since):
        items.append((norm_ts(p["created_at"]), "payment", p))
        newest = max(newest, _ts(p))
    if config.SQUARE_TAB_DISCOUNT:
        for o in client.orders(since):
            items.append((norm_ts(o.get("closed_at") or o["created_at"]), "order", o))
            newest = max(newest, _ts(o))
    items.sort(key=lambda x: x[0])
    for _, typ, obj in items:
        run(typ, obj)
    for rf in client.refunds(since):
        newest = max(newest, _ts(rf))
        run("refund", rf)

    retry.update(failed)
    result.update(counts)
    result["relinked"] = link_pending(conn)
    set_setting(conn, "square_cursor", newest)
    if resync:  # clear only the request we served; a newer one made meanwhile stays
        conn.execute("UPDATE settings SET value = '' WHERE key = 'square_resync_from' AND value = ?", (resync,))
    set_setting(conn, "square_last_sync", now_iso())
    set_setting(conn, "square_last_error", "")
    set_setting(conn, "square_retry", json.dumps(retry))
    set_setting(conn, "square_item_errors", json.dumps([f"{k}: {v}" for k, v in list(retry.items())[:20]]))
    conn.commit()
    if retry:
        result["skipped_items"] = len(retry)
    return result


def sync(conn, client=None, wait=False, catalog=False):
    """Full poll: customers, then payments, tab orders and refunds since the cursor.

    If a sync is already running, the request is queued and the running sync goes round once
    more when it finishes (wait=True blocks up to a minute instead, for staff-triggered syncs;
    a number waits that many seconds). catalog=True also refreshes the item list now, for "Sync
    now", so a renamed or new item shows up straight away instead of within 6 hours)."""
    got = _sync_lock.acquire(timeout=60 if wait is True else wait) if wait else _sync_lock.acquire(blocking=False)
    if not got:
        _sync_pending.set()
        # The running sync may have finished between our failed acquire and setting the flag;
        # if so, nobody would see the flag - so try once more and run it ourselves.
        if not _sync_lock.acquire(blocking=False):
            return {"queued": "a sync is already running; this one will run straight after"}
    result = {}
    while True:
        try:
            _sync_pending.clear()
            try:
                result = _sync_once(conn, client or SquareClient(), catalog)
            except Exception as e:  # keep the poller alive; show the error on the Square page
                conn.rollback()
                log.exception("Square sync failed")
                set_setting(conn, "square_last_error", f"{now_iso()}: {e}")
                set_setting(conn, "square_last_attempt", now_iso())
                conn.commit()
                result = {"error": str(e)}
        finally:
            _sync_lock.release()
        # Someone asked for a sync while this one ran: go again, unless another thread took over.
        if not _sync_pending.is_set() or not _sync_lock.acquire(blocking=False):
            return result


def describe_sync(result):
    """A sync result as one line for staff."""
    if "error" in result:
        return f"Sync failed: {result['error']}"
    if "queued" in result:
        return "A sync was already running - it'll go round again straight after with your request."
    parts = [f"{result.get('sale', 0)} sales", f"{result.get('tab_orders', 0)} tab sales",
             f"{result.get('tab_repayment', 0)} repayments", f"{result.get('refunds', 0)} refunds/returns"]
    msg = "Synced: " + ", ".join(parts) + "."
    if result.get("linked") or result.get("created"):
        msg += f" Customers: {result.get('linked', 0)} linked, {result.get('created', 0)} added."
    if result.get("conflicts"):
        msg += f" {result['conflicts']} Square profile(s) clash with existing emails - see below."
    if result.get("skipped_items"):
        msg += f" {result['skipped_items']} item(s) couldn't be processed yet - they're retried every sync (see below)."
    return msg


def request_resync(conn, start_iso):
    """Re-read Square from start_iso on the next sync (even if one is running right now)."""
    set_setting(conn, "square_resync_from", start_iso)
    conn.commit()


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
