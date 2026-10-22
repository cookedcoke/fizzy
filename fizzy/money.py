"""Money: restocks (what we bought, with receipts) and payouts to the partners.

- A restock records the shop, the items with quantities and line costs, what it was paid with,
  and optionally a receipt photo. Each item's cost each (line cost / quantity) becomes its cost
  on the Profit page, so margins follow real prices.
- The Money page shows money in and out for a period, split by where it is (cash box, bank),
  and a calculator that suggests how much each partner could take. Payouts are recorded as
  they actually happened - they don't have to match the calculator.
"""
import os
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from . import config, stats
from .db import audit, get_setting, now_iso, set_setting
from .web import back, get_conn, render, staff

router = APIRouter()

SOURCES = {"cash": "Cash box", "bank": "Bank (Square)", "other": "Someone's own money"}
RECEIPT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
                 "image/heif": ".heif", "application/pdf": ".pdf"}
RECEIPT_MAX = 15 * 1024 * 1024
DEFAULT_FEE_PCT = "1.6"     # Square AU in-person card rate; editable on the Money page


class MoneyError(ValueError):
    pass


def receipts_dir():
    return os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "receipts")


def _date(s, default=None):
    if not s and default:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise MoneyError("Pick a date.")


def _cents(v, what):
    v = (v or "").strip().replace("$", "").replace(",", "")
    try:
        c = round(float(v) * 100)
    except ValueError:
        raise MoneyError(f"'{v}' isn't a dollar amount ({what}).")
    if c < 0:
        raise MoneyError(f"{what} can't be negative.")
    return c


def _source(v):
    return v if v in SOURCES else "other"


def tender_source(tender):
    t = (tender or "").lower()
    if t.startswith("cash"):
        return "cash"
    if t.startswith(("card", "wallet")):
        return "bank"
    return "other"


# ---- restocks -----------------------------------------------------------------------------------

def add_restock(conn, day, shop, paid_from, note, items, staff_name, receipt=None):
    """items: [(name, qty, line_cents)]. Returns the restock id."""
    items = [(" ".join(n.split()), q, c) for n, q, c in items if n and n.strip()]
    if not items:
        raise MoneyError("Add at least one item.")
    for name, qty, _ in items:
        if qty <= 0:
            raise MoneyError(f"Quantity for {name} must be at least 1.")
    total = sum(c for _, _, c in items)
    cur = conn.execute("INSERT INTO restocks(day, shop, paid_from, note, total_cents, receipt, created_by, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (day.isoformat(), " ".join((shop or "").split()) or None, _source(paid_from),
                        (note or "").strip() or None, total, receipt, staff_name, now_iso()))
    rid = cur.lastrowid
    for name, qty, line in items:
        conn.execute("INSERT INTO restock_items(restock_id, name, qty, cost_cents) VALUES (?, ?, ?, ?)", (rid, name, qty, line))
        stats.set_cost(conn, name, round(line / qty), staff_name)
    audit(conn, staff_name, "restock", None, f"#{rid} {shop or ''} {total / 100:.2f} ({len(items)} items)")
    return rid


def save_receipt(data, content_type):
    ext = RECEIPT_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        raise MoneyError("The receipt must be a photo (JPEG, PNG, WebP, HEIC) or a PDF.")
    if len(data) > RECEIPT_MAX:
        raise MoneyError("That receipt is over 15 MB.")
    if not data:
        return None
    os.makedirs(receipts_dir(), exist_ok=True)
    name = f"{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(8)}{ext}"
    with open(os.path.join(receipts_dir(), name), "wb") as f:
        f.write(data)
    return name


def delete_restock(conn, rid, staff_name):
    r = conn.execute("SELECT * FROM restocks WHERE id = ?", (rid,)).fetchone()
    if r is None:
        return
    conn.execute("DELETE FROM restock_items WHERE restock_id = ?", (rid,))
    conn.execute("DELETE FROM restocks WHERE id = ?", (rid,))
    if r["receipt"]:
        try:
            os.remove(os.path.join(receipts_dir(), r["receipt"]))
        except OSError:
            pass
    audit(conn, staff_name, "restock_delete", None, f"#{rid} {r['shop'] or ''} {r['total_cents'] / 100:.2f}")


# ---- payouts ------------------------------------------------------------------------------------

def add_payout(conn, day, partner, amount_cents, paid_from, note, staff_name):
    if partner not in config.STAFF_NAMES:
        raise MoneyError("Pick who was paid.")
    if amount_cents <= 0:
        raise MoneyError("The amount must be more than $0.")
    conn.execute("INSERT INTO payouts(day, partner, amount_cents, paid_from, note, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (day.isoformat(), partner, amount_cents, _source(paid_from),
                                                  (note or "").strip() or None, staff_name, now_iso()))
    audit(conn, staff_name, "payout", None, f"{partner} {amount_cents / 100:.2f} from {_source(paid_from)}")


def delete_payout(conn, pid, staff_name):
    p = conn.execute("SELECT * FROM payouts WHERE id = ?", (pid,)).fetchone()
    if p:
        conn.execute("DELETE FROM payouts WHERE id = ?", (pid,))
        audit(conn, staff_name, "payout_delete", None, f"{p['partner']} {p['amount_cents'] / 100:.2f} on {p['day']}")


# ---- the money picture --------------------------------------------------------------------------

def fee_pct(conn):
    try:
        return float(get_setting(conn, "card_fee_pct") or DEFAULT_FEE_PCT)
    except ValueError:
        return float(DEFAULT_FEE_PCT)


def picture(conn, start_day, end_day):
    """Money in and out between two local dates (inclusive), by where the money is."""
    start, _ = stats.day_bounds(start_day)
    _, end = stats.day_bounds(end_day)
    by = {k: {"in": 0, "refunds": 0, "fees": 0, "restocks": 0, "payouts": 0} for k in SOURCES}
    for r in conn.execute("SELECT tender, SUM(amount_cents) FROM square_payments WHERE kind != 'tab_charge' "
                          "AND created_at >= ? AND created_at < ? GROUP BY tender", (start, end)):
        by[tender_source(r[0])]["in"] += r[1] or 0
    for r in conn.execute("SELECT sp.tender, SUM(rf.amount_cents) FROM square_refunds rf JOIN square_payments sp "
                          "ON sp.square_payment_id = rf.square_payment_id WHERE rf.square_refund_id NOT LIKE 'return:%' "
                          "AND rf.created_at >= ? AND rf.created_at < ? GROUP BY sp.tender", (start, end)):
        by[tender_source(r[0])]["refunds"] += r[1] or 0
    for r in conn.execute("SELECT method, SUM(amount_cents) FROM tab_repayments WHERE square_payment_id IS NULL "
                          "AND method != 'writeoff' AND created_at >= ? AND created_at < ? GROUP BY method", (start, end)):
        by["cash" if r[0] == "cash" else "bank" if r[0] == "card" else "other"]["in"] += r[1] or 0
    pct = fee_pct(conn)
    by["bank"]["fees"] = round((by["bank"]["in"] - by["bank"]["refunds"]) * pct / 100)
    s, e = start_day.isoformat(), end_day.isoformat()
    for r in conn.execute("SELECT paid_from, SUM(total_cents) FROM restocks WHERE day >= ? AND day <= ? GROUP BY paid_from", (s, e)):
        by[_source(r[0])]["restocks"] += r[1] or 0
    for r in conn.execute("SELECT paid_from, SUM(amount_cents) FROM payouts WHERE day >= ? AND day <= ? GROUP BY paid_from", (s, e)):
        by[_source(r[0])]["payouts"] += r[1] or 0
    for v in by.values():
        v["net"] = v["in"] - v["refunds"] - v["fees"] - v["restocks"] - v["payouts"]
    total = {k: sum(v[k] for v in by.values()) for k in ("in", "refunds", "fees", "restocks", "payouts", "net")}
    # "Other" money (house account, someone's own money) only matters if it was used.
    shown = {k: v for k, v in by.items() if k != "other" or any(v[x] for x in ("in", "restocks", "payouts"))}
    paid = {n: 0 for n in config.STAFF_NAMES}
    for r in conn.execute("SELECT partner, SUM(amount_cents) FROM payouts WHERE day >= ? AND day <= ? GROUP BY partner", (s, e)):
        paid[r[0]] = paid.get(r[0], 0) + (r[1] or 0)
    return {"by": shown, "total": total, "fee_pct": pct, "paid": paid,
            "profit_before_payouts": total["net"] + total["payouts"]}


# ---- pages --------------------------------------------------------------------------------------

def _period(start, end):
    t = stats.today()
    return _date(start, t.replace(day=1)), _date(end, t)


@router.get("/money", response_class=HTMLResponse)
def money_page(request: Request, start: str = "", end: str = "", who=Depends(staff), conn=Depends(get_conn)):
    try:
        s, e = _period(start, end)
    except MoneyError:
        s, e = _period("", "")
    t = stats.today()
    restocks = conn.execute("SELECT * FROM restocks WHERE day >= ? AND day <= ? ORDER BY day DESC, id DESC",
                            (s.isoformat(), e.isoformat())).fetchall()
    items = {}
    for r in conn.execute("SELECT * FROM restock_items WHERE restock_id IN (SELECT id FROM restocks WHERE day >= ? AND day <= ?) "
                          "ORDER BY id", (s.isoformat(), e.isoformat())):
        items.setdefault(r["restock_id"], []).append(r)
    payouts = conn.execute("SELECT * FROM payouts WHERE day >= ? AND day <= ? ORDER BY day DESC, id DESC",
                           (s.isoformat(), e.isoformat())).fetchall()
    week_start, _ = stats.day_bounds(t - timedelta(days=60))
    _, week_end = stats.day_bounds(t)
    known = sorted({r[0] for r in conn.execute("SELECT name FROM item_costs")}
                   | {i["name"] for i in stats.top_items(stats.checkouts(conn, week_start, week_end), limit=200)})
    quick = {"This month": (t.replace(day=1), t), "Last 30 days": (t - timedelta(days=29), t),
             "This year": (date(t.year, 1, 1), t)}
    return render(request, "money.html", start=s, end=e, pic=picture(conn, s, e), restocks=restocks, items=items,
                  payouts=payouts, sources=SOURCES, partners=config.STAFF_NAMES, known=known, today=t, quick=quick)


@router.post("/money/restock")
async def restock_add(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    form = await request.form()
    names, qtys, costs = form.getlist("item"), form.getlist("qty"), form.getlist("cost")
    try:
        lines = []
        for n, q, c in zip(names, qtys, costs):
            if not (n or "").strip():
                continue
            try:
                qty = int(q or 0)
            except ValueError:
                raise MoneyError(f"Quantity for {n} must be a whole number.")
            lines.append((n, qty, _cents(c, n)))
        upload = form.get("receipt")
        receipt = None
        if upload is not None and getattr(upload, "filename", ""):
            receipt = save_receipt(await upload.read(), upload.content_type)
        add_restock(conn, _date(form.get("day"), stats.today()), form.get("shop"), form.get("paid_from"),
                    form.get("note"), lines, who, receipt)
    except MoneyError as e:
        return back(request, "/money#restock", str(e))
    conn.commit()
    return back(request, "/money", "Restock saved. Item costs on the Profit page are updated.")


@router.post("/money/restock/{rid}/delete")
def restock_delete(request: Request, rid: int, who=Depends(staff), conn=Depends(get_conn)):
    delete_restock(conn, rid, who)
    conn.commit()
    return back(request, request.headers.get("referer") or "/money", "Restock deleted. (Item costs stay as they are.)")


@router.get("/money/receipts/{name}")
def receipt_file(name: str, who=Depends(staff)):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404)
    path = os.path.join(receipts_dir(), name)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/money/payout")
async def payout_add(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    form = await request.form()
    try:
        day = _date(form.get("day"), stats.today())
        partners, amounts = form.getlist("partner"), form.getlist("amount")
        rows = [(p, _cents(a, p)) for p, a in zip(partners, amounts) if (a or "").strip()]
        if not rows:
            raise MoneyError("Enter an amount.")
        for p, a in rows:
            if a:
                add_payout(conn, day, p, a, form.get("paid_from"), form.get("note"), who)
    except MoneyError as e:
        return back(request, "/money#payout", str(e))
    conn.commit()
    return back(request, "/money", f"Recorded {len(rows)} payout{'' if len(rows) == 1 else 's'}.")


@router.post("/money/payout/{pid}/delete")
def payout_delete(request: Request, pid: int, who=Depends(staff), conn=Depends(get_conn)):
    delete_payout(conn, pid, who)
    conn.commit()
    return back(request, request.headers.get("referer") or "/money", "Payout deleted.")


@router.post("/money/fee")
async def fee_set(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    v = ((await request.form()).get("fee_pct") or "").strip().rstrip("%")
    try:
        pct = float(v)
        if not 0 <= pct <= 10:
            raise ValueError
    except ValueError:
        return back(request, request.headers.get("referer") or "/money", "The card fee must be a % between 0 and 10.")
    set_setting(conn, "card_fee_pct", f"{pct:g}")
    audit(conn, who, "card_fee", None, f"{pct:g}%")
    conn.commit()
    return back(request, request.headers.get("referer") or "/money", f"Card fee set to {pct:g}%.")
