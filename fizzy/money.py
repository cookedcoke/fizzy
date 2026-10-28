"""Money: a record of restocks (what we bought, with receipts) and payouts to the partners.

- A restock records the shop, the items with quantities and line costs, and optionally a
  receipt photo. Each item's cost of one (line cost / quantity) becomes its cost on the
  Profit page, so margins follow real prices.
- Payouts are recorded as they actually happened. The calculator only splits an amount you
  type in; Fizzy doesn't try to track how much money the stall has (Square's fees and
  transfers make that more confusing than useful).
"""
import os
import re
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from . import config, stats
from .db import audit, now_iso
from .web import back, get_conn, render, staff

router = APIRouter()

RECEIPT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
                 "image/heif": ".heif", "application/pdf": ".pdf"}
RECEIPT_MAX = 15 * 1024 * 1024
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


MAX_CENTS = 10_000_000      # $100,000: anything bigger is a typo
MAX_QTY = 100_000


def _cents(v, what):
    v = (v or "").strip().replace("$", "")
    if not v:
        raise MoneyError(f"Add a cost for {what}.")
    v = re.sub(r",(\d{1,2})$", r".\1", v).replace(",", "")  # "12,50" is $12.50
    try:
        c = round(float(v) * 100)
    except (ValueError, OverflowError):  # "abc", "inf"
        raise MoneyError(f"'{v[:20]}' isn't a dollar amount ({what}).")
    if c < 0:
        raise MoneyError(f"{what} can't be negative.")
    if c > MAX_CENTS:
        raise MoneyError(f"{what}: that's more than $100,000 - check the amount.")
    return c


# What each allowed receipt type starts with, so the declared type has to match the file itself.
MAGIC = {".jpg": [b"\xff\xd8\xff"], ".png": [b"\x89PNG\r\n\x1a\n"], ".pdf": [b"%PDF-"]}


def _looks_like(data, ext):
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext in (".heic", ".heif"):
        return data[4:8] == b"ftyp"
    return any(data.startswith(m) for m in MAGIC.get(ext, []))


# ---- restocks -----------------------------------------------------------------------------------

def add_restock(conn, day, shop, note, items, staff_name, receipt=None):
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
                       (day.isoformat(), " ".join((shop or "").split()) or None, "",
                        (note or "").strip() or None, total, receipt, staff_name, now_iso()))
    rid = cur.lastrowid
    for typed, qty, line in items:
        key, name = stats.resolve_item(conn, typed)   # matched to Square's item list where possible
        conn.execute("INSERT INTO restock_items(restock_id, name, qty, cost_cents, item_key) VALUES (?, ?, ?, ?, ?)",
                     (rid, name, qty, line, key))
        stats.set_cost(conn, name, (line + qty // 2) // qty, staff_name, key=key)  # cost of one, halves up
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
    if not _looks_like(data, ext):
        raise MoneyError("That file doesn't look like the photo or PDF it says it is.")
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

def add_payout(conn, day, partner, amount_cents, note, staff_name):
    if partner not in config.STAFF_NAMES:
        raise MoneyError("Pick who was paid.")
    if amount_cents <= 0:
        raise MoneyError("The amount must be more than $0.")
    conn.execute("INSERT INTO payouts(day, partner, amount_cents, paid_from, note, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (day.isoformat(), partner, amount_cents, "",
                                                  (note or "").strip() or None, staff_name, now_iso()))
    audit(conn, staff_name, "payout", None, f"{partner} {amount_cents / 100:.2f}")


def delete_payout(conn, pid, staff_name):
    p = conn.execute("SELECT * FROM payouts WHERE id = ?", (pid,)).fetchone()
    if p:
        conn.execute("DELETE FROM payouts WHERE id = ?", (pid,))
        audit(conn, staff_name, "payout_delete", None, f"{p['partner']} {p['amount_cents'] / 100:.2f} on {p['day']}")


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
    # Square's item list first (exact names, every variation); then anything else seen or costed.
    known = [r[0] for r in conn.execute("SELECT display FROM catalog_items WHERE deleted = 0 ORDER BY display")]
    known += sorted(({r[0] for r in conn.execute("SELECT name FROM item_costs WHERE name_key NOT LIKE 'v:%'")}
                     | {i["name"] for i in stats.top_items(stats.checkouts(conn, week_start, week_end), limit=200)})
                    - set(known))
    quick = {"This month": (t.replace(day=1), t), "Last 30 days": (t - timedelta(days=29), t),
             "This year": (date(t.year, 1, 1), t)}
    paid = {n: 0 for n in config.STAFF_NAMES}
    for p in payouts:
        paid[p["partner"]] = paid.get(p["partner"], 0) + p["amount_cents"]
    return render(request, "money.html", start=s, end=e, restocks=restocks, items=items, payouts=payouts, paid=paid,
                  spent=sum(r["total_cents"] for r in restocks), paid_total=sum(paid.values()),
                  partners=config.STAFF_NAMES, known=known, today=t, quick=quick)


@router.post("/money/restock")
async def restock_add(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    form = await request.form()
    names, qtys, costs = form.getlist("item"), form.getlist("qty"), form.getlist("cost")
    receipt = None
    try:
        lines = []
        for n, q, c in zip(names, qtys, costs):
            if not (n or "").strip():
                continue
            try:
                qty = int(q or 0)
            except ValueError:
                raise MoneyError(f"Quantity for {n} must be a whole number.")
            if qty > MAX_QTY:
                raise MoneyError(f"Quantity for {n} is more than {MAX_QTY:,} - check it.")
            lines.append((n, qty, _cents(c, n)))
        day = _date(form.get("day"), stats.today())
        if not lines:
            raise MoneyError("Add at least one item.")
        if any(q <= 0 for _, q, _ in lines):
            raise MoneyError("Every quantity must be at least 1.")
        upload = form.get("receipt")
        if upload is not None and getattr(upload, "filename", ""):
            receipt = save_receipt(await upload.read(RECEIPT_MAX + 1), upload.content_type)  # only once the rest is valid
        try:
            add_restock(conn, day, form.get("shop"), form.get("note"), lines, who, receipt)
        except Exception:
            if receipt:  # don't leave a photo behind for a restock that wasn't saved
                os.remove(os.path.join(receipts_dir(), receipt))
            raise
    except MoneyError as e:
        return back(request, "/money#restock", str(e), "bad")
    conn.commit()
    return back(request, "/money", "Restock saved.")


@router.post("/money/restock/{rid:int}/delete")
def restock_delete(request: Request, rid: int, who=Depends(staff), conn=Depends(get_conn)):
    delete_restock(conn, rid, who)
    conn.commit()
    return back(request, request.headers.get("referer") or "/money", "Restock deleted.")


@router.get("/money/receipts/{name}")
def receipt_file(name: str, who=Depends(staff)):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404)
    path = os.path.join(receipts_dir(), name)
    if not os.path.isfile(path):
        raise HTTPException(404)
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"}
    if name.endswith(".pdf"):  # downloaded rather than opened inside the staff site
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(path, headers=headers)


@router.post("/money/payout")
async def payout_add(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    form = await request.form()
    try:
        day = _date(form.get("day"), stats.today())
        partners, amounts = form.getlist("partner"), form.getlist("amount")
        rows = [(p, _cents(a, p)) for p, a in zip(partners, amounts) if (a or "").strip()]
        rows = [(p, a) for p, a in rows if a]  # a "0" just means they weren't paid
        if not rows:
            raise MoneyError("Enter an amount.")
        for p, a in rows:
            add_payout(conn, day, p, a, form.get("note"), who)
    except MoneyError as e:
        return back(request, "/money#payout", str(e), "bad")
    conn.commit()
    return back(request, "/money", f"Recorded {len(rows)} payout{'' if len(rows) == 1 else 's'}.")


@router.post("/money/payout/{pid:int}/delete")
def payout_delete(request: Request, pid: int, who=Depends(staff), conn=Depends(get_conn)):
    delete_payout(conn, pid, who)
    conn.commit()
    return back(request, request.headers.get("referer") or "/money", "Payout deleted.")
