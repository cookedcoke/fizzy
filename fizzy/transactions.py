"""Transactions: every Square checkout with who it was, what they got and what it did (points,
tab), which Square's own list can't show. Plus tab money recorded by hand in Fizzy."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from . import stats
from .money import MoneyError, _period
from .web import get_conn, render, staff

router = APIRouter()

SHOW = {"": "Everything", "sales": "Sales", "tab": "Put on tabs", "repaid": "Tab repayments", "nobody": "No customer"}
LIMIT = 500


@router.get("/transactions", response_class=HTMLResponse)
def transactions(request: Request, start: str = "", end: str = "", show: str = "", q: str = "", who=Depends(staff),
                 conn=Depends(get_conn)):
    t = stats.today()
    try:
        s, e = _period(start or t.isoformat(), end or t.isoformat())
        s, e = max(s, date(2000, 1, 1)), min(e, date(9000, 1, 1))  # past these the time maths overflows
        lo, _ = stats.day_bounds(s)
        _, hi = stats.day_bounds(e)
    except (MoneyError, OverflowError, ValueError):
        s, e = t, t
        lo, _ = stats.day_bounds(s)
        _, hi = stats.day_bounds(e)
    rows = stats.checkouts(conn, lo, hi)
    pids = [p for r in rows for p in r["payment_ids"]]
    refunds, points = {}, {}
    for i in range(0, len(pids), 500):  # SQLite caps the number of ? in one query
        chunk = pids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for pid, cents in conn.execute(f"SELECT square_payment_id, SUM(amount_cents) FROM square_refunds "
                                       f"WHERE square_payment_id IN ({marks}) AND square_refund_id NOT LIKE 'return:%' "
                                       f"GROUP BY square_payment_id", chunk):  # return:... rows are item returns, not money back
            refunds[pid] = cents
        for pid, pts in conn.execute(f"SELECT square_payment_id, SUM(delta) FROM points_ledger "
                                     f"WHERE square_payment_id IN ({marks}) GROUP BY square_payment_id", chunk):
            points[pid] = pts
    for r in rows:
        r["refunded_cents"] = sum(refunds.get(p, 0) for p in r["payment_ids"])
        r["points"] = sum(points.get(p, 0) for p in r["payment_ids"])
        r["source"] = "square"
    # Tab money recorded by hand in Fizzy (not through Square).
    for h in conn.execute("SELECT r.*, c.name, c.kind FROM tab_repayments r JOIN customers c ON c.id = r.customer_id "
                          "WHERE r.square_payment_id IS NULL AND r.created_at >= ? AND r.created_at < ?", (lo, hi)):
        rows.append({"at": h["created_at"], "customer_id": h["customer_id"], "customer": h["name"], "items": [],
                     "sale_cents": 0, "tab_cents": 0, "repay_cents": h["amount_cents"], "sold_cents": 0,
                     "tenders": [h["method"]], "refunded_cents": 0, "points": None, "payment_ids": [],
                     "source": "writeoff" if h["method"] == "writeoff" else "hand", "note": h["note"], "staff": h["staff"],
                     "test": h["kind"] == "test"})
    nobody = sum(1 for r in rows if r["source"] == "square" and not r["customer_id"] and (r["sold_cents"] or r["repay_cents"]))
    keep = {"sales": lambda r: r["sale_cents"], "tab": lambda r: r["tab_cents"], "repaid": lambda r: r["repay_cents"],
            "nobody": lambda r: r["source"] == "square" and not r["customer_id"]}.get(show)
    if keep:
        rows = [r for r in rows if keep(r)]
    needle = " ".join(q.lower().split())
    if needle:
        rows = [r for r in rows if needle in (r["customer"] or "no customer").lower()
                or any(needle in i["name"].lower() for i in r["items"])]
    totals = {  # of what the filters leave
        "count": sum(1 for r in rows if r["source"] == "square"),
        "sold": sum(r["sold_cents"] for r in rows), "tab": sum(r["tab_cents"] for r in rows),
        "repaid": sum(r["repay_cents"] for r in rows if r["source"] != "writeoff" and not r.get("test")),  # as the dashboard
        "refunded": sum(r["refunded_cents"] for r in rows),
        "nobody": nobody,  # across the whole range, so "Show them" still makes sense when filtered
    }
    rows.sort(key=lambda r: r["at"], reverse=True)
    people = conn.execute("SELECT name, email FROM customers WHERE archived_at IS NULL ORDER BY name").fetchall()
    quick = {"Today": (t, t), "Yesterday": (t - timedelta(days=1), t - timedelta(days=1)),
             "Last 7 days": (t - timedelta(days=6), t), "This month": (t.replace(day=1), t)}
    return render(request, "transactions.html", rows=rows[:LIMIT], more=max(0, len(rows) - LIMIT), totals=totals,
                  start=s, end=e, show=show, q=q, shows=SHOW, quick=quick, people=people)
