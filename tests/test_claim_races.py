"""Claim sale: several taps in flight at once (found by the simulation review)."""
import random
from datetime import timedelta

import pytest

from fizzy import config, db, square
from tests.test_claim_window import T, owner
from tests.test_rules import order, pay


def W(sec):
    return T + timedelta(seconds=sec)


def sq(sec):
    """Square writes milliseconds."""
    return W(sec).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fresh(names):
    conn = db.connect(":memory:"); db.init(conn)
    ppl = {}
    for i, n in enumerate(names):
        cid = conn.execute("INSERT INTO customers(name, email, created_at) VALUES (?, ?, ?)", (n, f"{i}@x", db.now_iso())).lastrowid
        ppl[n] = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    return conn, ppl


def deliver(conn, pid, cents, ring, oid=None, tip=0, source="CASH", total=None, tenders=None):
    p = pay(pid, cents, cust=None, source=source, ts=sq(ring), order_id=oid)
    if tip:
        p["tip_money"] = {"amount": tip}; p["total_money"] = {"amount": cents + tip}
    o = None
    if oid:
        o = {**order(("Coke", (total or cents)), oid=oid), "total_money": {"amount": (total or cents) + tip}}
        if tenders:
            o["tenders"] = tenders
    square.process_payment(conn, p, o)


def simulate(seed):
    rnd = random.Random(seed)
    n = rnd.randint(1, 5)
    names = [f"C{i}" for i in range(n)]
    conn, ppl = fresh(names)
    t, events, want = 0.0, [], {}
    for i, nm in enumerate(names):
        ring = t + rnd.uniform(0, 3)
        press = ring + rnd.uniform(0.2, 29.0)
        pid = f"s{i}"
        want[pid] = nm
        delay = rnd.uniform(0, 150)
        events.append((press, 0, "press", nm))
        events.append((ring + delay, 1, "sale", pid, ring))
        t = press + rnd.uniform(1, 60)
    events.sort()
    end = max(e[0] for e in events) + 5
    clock = events[0][0]
    ei = 0
    while clock <= end:
        while ei < len(events) and events[ei][0] <= clock:
            e = events[ei]; ei += 1
            if e[2] == "press":
                square.claim_next(conn, ppl[e[3]], "Will", now=W(e[0]))
            else:
                deliver(conn, e[3], 100, e[4], oid=f"o{e[3]}")
                square.claim_waiting(conn, now=W(e[0]))
        square.claim_waiting(conn, now=W(clock))
        clock += 1
    by = {c["id"]: nm for nm, c in ppl.items()}
    got = {pid: by.get(owner(conn, pid)) for pid in want}
    conn.close()
    return want, got, events


def test_repress_while_waiting_made_new_press_by_stale_done_at():
    """Carl pressed at 0 (waiting). Amy's sale rung up at 15, Amy pressed at 17 (waiting).
    Carl re-tapped at 16 (in flight until 19.5). Amy's sale reaches Fizzy at 17.5: Carl's
    in-flight tap blocks it. Dan tapped at 18 (in flight, runs at 21). Amy re-tapped at 19
    (her press is waiting), runs at 21.5. Eve's sale rung at 18.5, Eve pressed at 25."""
    conn, p = fresh(["Amy", "Carl", "Dan", "Eve"])
    square.claim_next(conn, p["Carl"], "Will", now=W(0))
    c2 = square.pressing(W(16)); carl2 = c2.__enter__()
    square.claim_next(conn, p["Amy"], "Will", now=W(17))
    deliver(conn, "a", 100, 15, oid="oa")
    square.claim_waiting(conn, now=W(17.5))
    d = square.pressing(W(18)); dan = d.__enter__()
    a2 = square.pressing(W(19)); amy2 = a2.__enter__()
    r = square.claim_next(conn, p["Carl"], "Will", now=W(16), press=carl2); c2.__exit__(None, None, None)
    assert r[0] == "still waiting"
    square.claim_next(conn, p["Dan"], "Will", now=W(18), press=dan); d.__exit__(None, None, None)
    r2 = square.claim_next(conn, p["Amy"], "Will", now=W(19), press=amy2); a2.__exit__(None, None, None)
    deliver(conn, "e", 300, 18.5, oid="oe")
    square.claim_next(conn, p["Eve"], "Will", now=W(25))
    for s in range(25, 40):
        square.claim_waiting(conn, now=W(s))
    assert owner(conn, "a") == p["Amy"]["id"]
    assert (r2[0], owner(conn, "e")) == ("claimed", p["Eve"]["id"]), (r2[0], owner(conn, "e"))
