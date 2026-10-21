"""Regression tests for the full-stack review findings (one or more per fix)."""
import json
import sqlite3
import threading

import pytest

from fizzy import config, db, logic, square, tools
from tests.test_rules import iso, order, pay, tab_order


class FakeSquare:
    """Stands in for SquareClient: records the query params it was asked for."""

    def __init__(self, customers=(), payments=(), orders=(), refunds=(), order_by_id=None):
        self._c, self._p, self._o, self._r = list(customers), list(payments), list(orders), list(refunds)
        self._orders = order_by_id or {}
        self.calls = []

    def customers(self):
        return iter(self._c)

    def payments(self, since):
        self.calls.append(("payments", since))
        return iter(self._p)

    def orders(self, since):
        self.calls.append(("orders", since))
        return iter(self._o)

    def refunds(self, since):
        self.calls.append(("refunds", since))
        return iter(self._r)

    def order(self, oid):
        return self._orders.get(oid)

    def payment(self, pid):
        return next((p for p in self._p if p["id"] == pid), None)

    def refund(self, rid):
        return next((r for r in self._r if r["id"] == rid), None)


def reload(conn, c):
    return conn.execute("SELECT * FROM customers WHERE id = ?", (c["id"],)).fetchone()


# ---- refunds of a repayment + purchase sale ---------------------------------------------------

def _mixed_sale(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 500))
    square.process_payment(conn, pay("p1", 800, source="CARD", order_id="o2"),
                           order(("Tab repayment", 500), ("Coke", 300), oid="o2"))
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0 and logic.points_balance(conn, c["id"]) == 800
    return c


def refund(rid, pid, amount, oid=None):
    return {"id": rid, "payment_id": pid, "status": "COMPLETED", "amount_money": {"amount": amount},
            "created_at": iso(), "order_id": oid}


def test_refunding_the_drink_in_a_repayment_sale_leaves_the_tab_paid(conn, cust):
    c = _mixed_sale(conn, cust)
    square.process_refund(conn, refund("r1", "p1", 300))            # Square doesn't say which item: drink first
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0
    assert logic.points_balance(conn, c["id"]) == 500


def test_refund_naming_the_repayment_item_reopens_the_tab(conn, cust):
    c = _mixed_sale(conn, cust)
    ret = {"returns": [{"return_line_items": [{"name": "Tab repayment", "total_money": {"amount": 500}}]}]}
    square.process_refund(conn, refund("r1", "p1", 500), ret)
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 500
    assert logic.points_balance(conn, c["id"]) == 300                # the drink keeps its points


def test_full_refund_of_mixed_sale_is_capped_per_part(conn, cust):
    c = _mixed_sale(conn, cust)
    square.process_refund(conn, refund("r1", "p1", 800))
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 500      # not 800
    assert logic.points_balance(conn, c["id"]) == 0
    assert not square.process_refund(conn, refund("r1", "p1", 800))   # still idempotent


def test_partial_refunds_of_a_repayment_never_take_more_points_than_it_earned(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 300))
    square.process_payment(conn, pay("p1", 500, source="CARD", order_id="o2"), order(("Tab repayment", 500), oid="o2"))
    assert logic.points_balance(conn, c["id"]) == 300                 # $2 of it is credit, no points
    square.process_refund(conn, refund("r1", "p1", 200))
    square.process_refund(conn, refund("r2", "p1", 200))
    # $5 paid, $4 refunded: $1 of real money still paid the $3 tab, so 100 points, $2 owing.
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 200
    assert logic.points_balance(conn, c["id"]) == 100                 # was -100 before the fix


# ---- points ordering, write-offs, returns -----------------------------------------------------

def test_repayment_seen_before_its_tab_sale_still_earns_points(conn, cust):
    c = cust()
    square.process_payment(conn, pay("p1", 400, source="CARD", order_id="o2"), order(("Tab repayment", 400), oid="o2"))
    assert logic.points_balance(conn, c["id"]) == 0
    square.process_order(conn, tab_order("o1", 400))
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0
    assert logic.points_balance(conn, c["id"]) == 400


def test_sync_processes_tab_sales_before_later_repayments(conn, cust):
    cust()
    early, late = iso(hours=2), iso()
    fake = FakeSquare(payments=[pay("p1", 400, source="CARD", order_id="o2", ts=late)],
                      orders=[tab_order("o1", 400, ts=early)],
                      order_by_id={"o2": order(("Tab repayment", 400), oid="o2")})
    assert "error" not in square.sync(conn, fake)
    assert conn.execute("SELECT SUM(delta) FROM points_ledger WHERE reason = 'tab_repaid'").fetchone()[0] == 400


def test_square_repayment_is_never_matched_to_a_write_off(conn, cust):
    c = cust()
    logic.add_tab_charge(conn, c, 300, "x", iso(hours=1))
    logic.add_repayment(conn, c["id"], 300, "writeoff", "forgiven", "Bru")
    square.process_payment(conn, pay("p1", 300, order_id="o2"), order(("Tab repayment", 300), oid="o2"))
    assert conn.execute("SELECT COUNT(*) FROM tab_repayments").fetchone()[0] == 2
    assert logic.tab_ledger(conn, c["id"])["balance_cents"] == -300


def test_return_from_a_written_off_tab_reverses_the_write_off_not_credit(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 400))
    logic.add_repayment(conn, c["id"], 400, "writeoff", "forgiven", "Bru")
    ret = {"source_order_id": "o1", "return_line_items": [{"total_discount_money": {"amount": 400}}]}
    square.process_order(conn, {"id": "o9", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    assert logic.tab_ledger(conn, c["id"])["balance_cents"] == 0       # not $4 credit
    assert logic.points_balance(conn, c["id"]) == 0


def test_return_only_reduces_the_tab_part_of_a_discounted_item(conn, cust):
    c = cust()
    # Pie $6 ($1 staff discount, $3 on the tab, $2 paid) + Coke $3 on the tab = $6 owed.
    # Returning the pie should take $3 off the tab, leaving the Coke's $3.
    src = tab_order("o1", 600, ("Pie", 600), ("Coke", 300))
    src["discounts"].append({"name": "Staff", "uid": "d2", "applied_money": {"amount": 100}})
    square.process_order(conn, src)
    ret = {"source_order_id": "o1",
           "return_discounts": [{"uid": "rd1", "name": "Fizzy Tab"}, {"uid": "rd2", "name": "Staff"}],
           "return_line_items": [{"name": "Pie", "total_discount_money": {"amount": 400},
                                  "applied_discounts": [{"discount_uid": "rd1", "applied_money": {"amount": 300}},
                                                        {"discount_uid": "rd2", "applied_money": {"amount": 100}}]}]}
    square.process_order(conn, {"id": "o9", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 300     # was 200: the $1 staff discount came off the tab too


def test_reclassify_round_trip_does_not_double_refund_a_hand_charge(conn, cust):
    c = cust()
    logic.add_tab_charge(conn, c, 400, "by hand", iso(), "Mike")
    square.process_order(conn, tab_order("o1", 400))                  # links to the hand charge
    ret = {"source_order_id": "o1", "return_line_items": [{"total_discount_money": {"amount": 100}}]}
    square.process_order(conn, {"id": "o9", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    square.reclassify(conn, "order:o1", "sale", "Bru")
    square.reclassify(conn, "order:o1", "tab_charge", "Bru")
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 300


# ---- sync robustness --------------------------------------------------------------------------

def test_duplicate_square_profile_does_not_stop_syncing(conn, cust):
    c = cust(email="rns1@reddamnorthshore.nsw.edu.au", sq="SQ1")
    fake = FakeSquare(customers=[{"id": "SQ2", "given_name": "Dup", "email_address": "RNS1@reddamnorthshore.nsw.edu.au"}],
                      payments=[pay("p1", 250)])
    result = square.sync(conn, fake)
    assert "error" not in result and result["conflicts"] == 1
    assert logic.points_balance(conn, c["id"]) == 250
    assert json.loads(db.get_setting(conn, "square_customer_conflicts"))[0]["existing"] == c["name"]


def test_one_bad_payment_is_skipped_and_reported(conn, cust, monkeypatch):
    c = cust()
    real = square.process_payment

    def flaky(conn_, p, o):
        if p["id"] == "bad":
            raise RuntimeError("boom")
        return real(conn_, p, o)
    monkeypatch.setattr(square, "process_payment", flaky)
    result = square.sync(conn, FakeSquare(payments=[pay("bad", 100), pay("good", 200)]))
    assert result["skipped_items"] == 1 and logic.points_balance(conn, c["id"]) == 200
    assert "boom" in db.get_setting(conn, "square_item_errors")


def test_sync_window_is_by_last_update(conn, cust):
    cust()
    fake = FakeSquare()
    square.sync(conn, fake)
    assert {k for k, _ in fake.calls} == {"payments", "orders", "refunds"}
    # The client is asked by update time (see SquareClient); offline sales uploaded late have an
    # old created_at but a new updated_at, so they're still inside the window.
    import inspect
    src = inspect.getsource(square.SquareClient)
    assert '"updated_at_begin_time": since' in src and '"sort_field": "UPDATED_AT"' in src
    assert '"updated_at": {"start_at": since}' in src


def test_resync_requested_during_a_running_sync_is_not_lost(conn, cust):
    cust()
    square._sync_lock.acquire()
    try:
        assert "queued" in square.sync(conn, FakeSquare())               # busy: queued, not dropped
        square.request_resync(conn, "2020-01-01T00:00:00+00:00")
    finally:
        square._sync_lock.release()
    fake = FakeSquare()
    square.sync(conn, fake)
    assert fake.calls[0][1].startswith("2019-12-31T23")                  # honoured (minus the 1h overlap)
    assert db.get_setting(conn, "square_resync_from") == ""


def test_merge_keeps_the_duplicates_square_profile_pointing_at_the_kept_customer(conn, cust):
    keep = cust("Keep Kid", "rns1@reddamnorthshore.nsw.edu.au", "SQK")
    dup = cust("Dup Kid", "rns2@reddamnorthshore.nsw.edu.au", "SQD")
    logic.set_tab_decision(conn, dup["id"], "approved", "ok", "Bru")
    req = type("R", (), {"session": {}})()
    tools.h_merge(req, conn, "Bru", {"keep": "Keep Kid <rns1@reddamnorthshore.nsw.edu.au>",
                                     "dup": "Dup Kid <rns2@reddamnorthshore.nsw.edu.au>"})
    # Next sync sees both Square profiles again; the duplicate's must not attach to the archived record.
    square.sync(conn, FakeSquare(customers=[{"id": "SQK", "email_address": "rns1@reddamnorthshore.nsw.edu.au"},
                                            {"id": "SQD", "email_address": "rns2@reddamnorthshore.nsw.edu.au", "reference_id": f"fizzy-{dup['id']}"}],
                                 payments=[pay("p1", 300, cust="SQD")]))
    assert logic.points_balance(conn, keep["id"]) == 300
    k, d = reload(conn, keep), reload(conn, dup)
    assert k["tab_decision"] == "approved" and d["tab_decision"] is None and d["merged_into"] == keep["id"]


# ---- tools -----------------------------------------------------------------------------------

def test_takings_puts_tab_returns_on_their_own_line(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 400))
    ret = {"source_order_id": "o1", "return_line_items": [{"total_discount_money": {"amount": 400}}]}
    square.process_order(conn, {"id": "o9", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    captured = {}
    monkey_report = tools.report
    tools.report = lambda request, title, headers, rows, **kw: captured.setdefault("rows", rows)
    try:
        tools.h_takings(None, conn, "Bru", {"start": "2020-01-01", "end": "2030-01-01"})
    finally:
        tools.report = monkey_report
    labels = [r[0] for r in captured["rows"]]
    assert "Returned from tabs (not money)" in labels and "Square: refunds" not in labels


def test_bonus_points_count_tab_purchases_by_purchase_date(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 400, ts="2026-01-05T01:00:00+00:00"))   # bought in the bonus week
    logic.add_repayment(conn, c["id"], 400, "cash", "", "Bru", created_at="2026-02-01T01:00:00+00:00")  # repaid later
    tools.h_bonus_points(type("R", (), {"session": {}})(), conn, "Bru",
                         {"multiplier": "2", "start": "2026-01-01", "end": "2026-01-10"})
    assert logic.points_balance(conn, c["id"]) == 800


def test_strict_customer_matching(conn, cust):
    c = cust("Nora Zhang", "rns00000042@reddamnorthshore.nsw.edu.au")
    assert tools.resolve(conn, "nora zhang")["id"] == c["id"]
    assert tools.resolve(conn, "Nora Zhang <rns00000042@reddamnorthshore.nsw.edu.au>")["id"] == c["id"]
    assert tools.resolve(conn, "00000042")["id"] == c["id"]
    for fuzzy in ("Nora Zang", "nora", "42"):
        with pytest.raises(tools.ToolError):
            tools.resolve(conn, fuzzy)
    found, missing = tools.resolve_many(conn, "Zhang, Nora")
    assert [f["id"] for f in found] == [c["id"]] and not missing


def test_move_points_cannot_overdraw(conn, cust):
    a = cust("Ann A", "rns1@reddamnorthshore.nsw.edu.au", "S1")
    cust("Bob B", "rns2@reddamnorthshore.nsw.edu.au", "S2")
    logic.adjust_points(conn, a["id"], 50, "seed", "Bru")
    with pytest.raises(tools.ToolError):
        tools.h_transfer_points(None, conn, "Bru", {"from": "Ann A", "to": "Bob B", "points": "60"})


def test_csv_row_with_no_name_is_skipped_not_blanked():
    from fizzy.customers import parse_directory_csv
    rows, errors = parse_directory_csv("Cleaned Name,Email\n,rns1@reddamnorthshore.nsw.edu.au\n")
    assert rows == [] and "no name" in errors[0]


def test_blank_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "")
    assert config._env("SOME_SETTING", "300") == "300"
    monkeypatch.setenv("SOME_SETTING", "  ")
    assert config._env("SOME_SETTING", "x") == "x"


def test_old_database_gets_new_columns(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, "
                      "square_customer_id TEXT UNIQUE, tab_decision TEXT, tab_decision_note TEXT, tab_decision_by TEXT, "
                      "tab_decision_at TEXT, created_at TEXT NOT NULL, archived_at TEXT);")
    raw.close()
    conn = db.connect(path)
    db.init(conn)
    assert "merged_into" in {r["name"] for r in conn.execute("PRAGMA table_info(customers)")}


# ---- second round: issues found by the check over the first round of fixes -------------------

def rep_order(oid, amt):
    return order(("Tab repayment", amt), oid=oid)


def test_return_of_paid_items_doesnt_revive_an_unrelated_old_write_off(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 500, ts=iso(30)))
    logic.add_repayment(conn, c["id"], 500, "writeoff", "old debt", "Bru", created_at=iso(29))
    square.process_order(conn, tab_order("o2", 500))
    square.process_payment(conn, pay("p1", 500, source="CARD", order_id="r1"), rep_order("r1", 500))
    assert logic.points_balance(conn, c["id"]) == 500
    ret = {"source_order_id": "o2", "return_line_items": [{"total_discount_money": {"amount": 500}}]}
    square.process_order(conn, {"id": "o9", "state": "COMPLETED", "closed_at": iso(), "created_at": iso(), "returns": [ret]})
    assert logic.tab_ledger(conn, c["id"])["balance_cents"] == -500   # the card $5 is now credit
    assert logic.points_balance(conn, c["id"]) == 0                   # and earns nothing


def test_refunding_the_repayment_that_funded_a_later_tab_takes_its_points_back(conn, cust):
    c = cust()
    square.process_payment(conn, pay("p1", 500, source="CARD", order_id="r1"), rep_order("r1", 500))
    square.process_order(conn, tab_order("o1", 500))
    assert logic.points_balance(conn, c["id"]) == 500
    square.process_refund(conn, refund("rf1", "p1", 500))
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 500
    assert logic.points_balance(conn, c["id"]) == 0
    square.process_payment(conn, pay("p2", 500, source="CARD", order_id="r2"), rep_order("r2", 500))
    assert logic.points_balance(conn, c["id"]) == 500                 # not 1000


def test_remarking_the_funding_repayment_keeps_points_right(conn, cust):
    c = cust()
    square.process_payment(conn, pay("p1", 500, source="CARD", order_id="r1"), rep_order("r1", 500))
    square.process_order(conn, tab_order("o1", 500))
    square.reclassify(conn, "p1", "sale", "Bru")
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 500
    assert logic.points_balance(conn, c["id"]) == 500                 # 500 for the sale, none for the unpaid tab


def test_item_that_fails_is_retried_on_the_next_sync(conn, cust):
    import httpx
    c = cust()
    t_bad = iso(hours=3)

    class Flaky(FakeSquare):
        fail = True

        def order(self, oid):
            if oid == "obad" and Flaky.fail:
                raise httpx.ReadTimeout("timeout")
            return super().order(oid)
    p_bad = pay("bad", 700, ts=t_bad, order_id="obad")
    p_bad["updated_at"] = t_bad
    p_new = pay("new", 100)
    db.set_setting(conn, "square_cursor", iso(hours=4))
    conn.commit()
    assert square.sync(conn, Flaky(payments=[p_bad, p_new]))["skipped_items"] == 1
    assert "payment:bad" in db.get_setting(conn, "square_item_errors")
    Flaky.fail = False
    # The window has moved past 'bad', so it isn't in the list any more - it's fetched by id.
    fake = Flaky(payments=[p_new])
    fake.payment = lambda pid: p_bad if pid == "bad" else None
    square.sync(conn, fake)
    assert logic.points_balance(conn, c["id"]) == 800
    assert db.get_setting(conn, "square_item_errors") == "[]"


def test_a_newer_resync_request_made_during_a_resync_survives(conn, cust, tmp_path):
    cust()
    square.request_resync(conn, "2026-09-01T00:00:00+00:00")
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    other = db.connect(path)

    class Slow(FakeSquare):
        def payments(self, since):
            square.request_resync(other, "2026-06-01T00:00:00+00:00")
            return super().payments(since)
    square.sync(conn, Slow())
    assert db.get_setting(conn, "square_resync_from") == "2026-06-01T00:00:00+00:00"


def test_full_refund_with_an_order_but_no_return_lines_refunds_both_parts(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 500))
    o2 = order(("Tab repayment", 500), ("Coke", 300), oid="o2")
    p1 = pay("p1", 800, source="CARD", order_id="o2")
    square.process_payment(conn, p1, o2)
    square.process_refund(conn, refund("rf1", "p1", 800, oid="o2"), o2)   # o2 has no 'returns'
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 500


def test_bonus_points_ignore_written_off_tab_purchases(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 400, ts="2026-01-05T01:00:00+00:00"))
    logic.add_repayment(conn, c["id"], 400, "writeoff", "forgiven", "Bru", created_at="2026-01-06T01:00:00+00:00")
    tools.h_bonus_points(type("R", (), {"session": {}})(), conn, "Bru",
                         {"multiplier": "2", "start": "2026-01-01", "end": "2026-01-10"})
    assert logic.points_balance(conn, c["id"]) == 0


def test_merge_chain_is_followed(conn, cust):
    a = cust("Ann A", "rns1@reddamnorthshore.nsw.edu.au", None)
    b = cust("Ann B", "rns2@reddamnorthshore.nsw.edu.au", None)
    c = cust("Ann C", "rns3@reddamnorthshore.nsw.edu.au", None)
    req = type("R", (), {"session": {}})()
    tools.h_merge(req, conn, "Bru", {"keep": "Ann B", "dup": "Ann A"})
    tools.h_merge(req, conn, "Bru", {"keep": "Ann C", "dup": "Ann B"})
    square.sync(conn, FakeSquare(customers=[{"id": "SQA", "given_name": "Ann", "reference_id": f"fizzy-{a['id']}",
                                             "email_address": "rns1@reddamnorthshore.nsw.edu.au"}],
                                 payments=[pay("p1", 200, cust="SQA")]))
    assert logic.points_balance(conn, c["id"]) == 200


def test_names_and_square_ids_match_however_they_are_spaced_or_cased(conn, cust):
    c = cust("Nora  Zhang ", "square:SQABC", None)
    assert tools.resolve(conn, "Nora Zhang")["id"] == c["id"]
    assert tools.resolve(conn, "Nora Zhang <square:SQABC>")["id"] == c["id"]


# ---- third round ---------------------------------------------------------------------------

def _mock_square(handlers):
    """A real SquareClient talking to a fake Square: handlers maps path -> (status, json)."""
    import httpx

    def handle(request):
        status, body = handlers.get(request.url.path, (200, {}))
        return httpx.Response(status, json=body)
    return square.SquareClient(token="t", base="https://sq.test", transport=httpx.MockTransport(handle))


EMPTY = {"/v2/customers": (200, {}), "/v2/payments": (200, {}), "/v2/refunds": (200, {}),
         "/v2/locations": (200, {"locations": [{"id": "L1"}]}), "/v2/orders/search": (200, {})}


def test_retried_tab_sale_is_kept_when_square_is_briefly_down(conn, cust):
    cust()
    db.set_setting(conn, "square_retry", json.dumps({"order:o1": "database is locked"}))
    conn.commit()
    square.sync(conn, _mock_square({**EMPTY, "/v2/orders/o1": (503, {"errors": []})}))
    assert "order:o1" in json.loads(db.get_setting(conn, "square_retry"))      # not dropped as "gone"
    square.sync(conn, _mock_square({**EMPTY, "/v2/orders/o1": (200, {"order": tab_order("o1", 300)})}))
    assert json.loads(db.get_setting(conn, "square_retry")) == {}
    assert conn.execute("SELECT COUNT(*) FROM tab_charges").fetchone()[0] == 1
    square.sync(conn, _mock_square({**EMPTY, "/v2/orders/gone": (404, {"errors": []})}))   # a real 404 is "gone"


def test_repayment_isnt_recorded_as_a_sale_when_its_order_cant_be_fetched(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 500))
    p = pay("p1", 500, source="CARD", order_id="o2")
    down = _mock_square({**EMPTY, "/v2/payments": (200, {"payments": [p]}), "/v2/orders/o2": (503, {})})
    square.sync(conn, down)
    assert conn.execute("SELECT COUNT(*) FROM square_payments WHERE square_payment_id = 'p1'").fetchone()[0] == 0
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 500
    up = _mock_square({**EMPTY, "/v2/payments/p1": (200, {"payment": p}),
                       "/v2/orders/o2": (200, {"order": order(("Tab repayment", 500), oid="o2")})})
    square.sync(conn, up)                                   # retried by id, now read properly
    assert logic.tab_ledger(conn, c["id"])["owing_cents"] == 0


def test_retry_item_that_goes_through_in_the_window_isnt_reported(conn, cust):
    cust()
    p = pay("p1", 300)
    db.set_setting(conn, "square_retry", json.dumps({"payment:p1": "old error"}))
    conn.commit()
    fake = FakeSquare(payments=[p])

    def blip(pid):
        raise RuntimeError("blip")
    fake.payment = blip
    result = square.sync(conn, fake)
    assert "skipped_items" not in result and json.loads(db.get_setting(conn, "square_retry")) == {}


def test_bonus_follows_fifo_with_an_older_write_off_and_newer_cash_purchase(conn, cust):
    c = cust()
    square.process_order(conn, tab_order("o1", 400, ts="2026-01-05T01:00:00+00:00"))
    logic.add_repayment(conn, c["id"], 400, "writeoff", "forgiven", "Bru", created_at="2026-02-01T01:00:00+00:00")
    square.process_order(conn, tab_order("o2", 400, ts="2026-03-05T01:00:00+00:00"))
    logic.add_repayment(conn, c["id"], 400, "cash", "", "Bru", created_at="2026-03-06T01:00:00+00:00")
    req = type("R", (), {"session": {}})()
    before = logic.points_balance(conn, c["id"])
    tools.h_bonus_points(req, conn, "Bru", {"multiplier": "2", "start": "2026-01-01", "end": "2026-01-10"})
    assert logic.points_balance(conn, c["id"]) == before                # written off: no bonus
    tools.h_bonus_points(req, conn, "Bru", {"multiplier": "2", "start": "2026-03-01", "end": "2026-03-10"})
    assert logic.points_balance(conn, c["id"]) == before + 400          # paid in cash: bonus


def test_merge_reconciles_points_when_credit_meets_a_charge(conn, cust):
    keep = cust("Keep Kid", "rns1@reddamnorthshore.nsw.edu.au", "SQK")
    dup = cust("Dup Kid", "rns2@reddamnorthshore.nsw.edu.au", "SQD")
    logic.add_tab_charge(conn, keep, 500, "x", iso(1))
    logic.add_repayment(conn, dup["id"], 500, "cash", "", "Bru")       # landed on the duplicate: credit, 0 points
    tools.h_merge(type("R", (), {"session": {}})(), conn, "Bru", {"keep": "Keep Kid", "dup": "Dup Kid"})
    assert logic.tab_ledger(conn, keep["id"])["owing_cents"] == 0
    assert logic.points_balance(conn, keep["id"]) == 500


# ---- fourth round: the money / write-off split always agrees with the ledger and the points ----

def test_unpaid_newer_charge_never_counts_as_paid_after_a_write_off_reversal(conn, cust):
    c = cust()
    logic.add_tab_charge(conn, c, 200, "A", "2026-01-05T01:00:00+00:00")
    b = logic.add_tab_charge(conn, c, 200, "B", "2026-01-06T01:00:00+00:00")
    logic.add_repayment(conn, c["id"], 200, "writeoff", "x", "Bru", created_at="2026-01-07T01:00:00+00:00")
    logic.add_repayment(conn, c["id"], 100, "cash", "", "Bru", created_at="2026-01-08T01:00:00+00:00")
    charge_b = conn.execute("SELECT * FROM tab_charges WHERE id = ?", (b,)).fetchone()
    logic.refund_tab_charge(conn, charge_b, 156, "Bru", "2026-01-09T01:00:00+00:00")
    cc = logic.add_tab_charge(conn, c, 200, "C", "2026-03-05T01:00:00+00:00")
    assert logic.paid_by_money(conn, c["id"])[cc] == 0


@pytest.mark.parametrize("seed", range(300))
def test_money_split_agrees_with_ledger_and_points(conn, cust, seed):
    import random
    rng = random.Random(seed)
    c = cust()
    day = 0
    for _ in range(rng.randint(3, 12)):
        day += 1
        ts = f"2026-01-{day:02d}T01:00:00+00:00" if day <= 28 else f"2026-02-{day - 28:02d}T01:00:00+00:00"
        op = rng.choice(["charge", "charge", "cash", "cash", "writeoff", "refund_charge", "refund_cash"])
        owing = logic.tab_ledger(conn, c["id"])["owing_cents"]
        if op == "charge":
            logic.add_tab_charge(conn, c, rng.randint(50, 600), "x", ts)
        elif op == "cash":
            logic.add_repayment(conn, c["id"], rng.randint(50, 600), "cash", "", "Bru", created_at=ts)
        elif op == "writeoff" and owing > 0:
            logic.add_repayment(conn, c["id"], rng.randint(1, owing), "writeoff", "x", "Bru", created_at=ts)
        elif op == "refund_charge":
            rows = conn.execute("SELECT * FROM tab_charges WHERE customer_id = ? AND amount_cents > refunded_cents",
                                (c["id"],)).fetchall()
            if rows:
                ch = rng.choice(rows)
                logic.refund_tab_charge(conn, ch, rng.randint(1, ch["amount_cents"] - ch["refunded_cents"]), "Bru", ts)
        elif op == "refund_cash":
            paid = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM tab_repayments WHERE customer_id = ? "
                                "AND method != 'writeoff'", (c["id"],)).fetchone()[0]
            if paid > 0:
                conn.execute("INSERT INTO tab_repayments(customer_id, amount_cents, method, note, staff, created_at) "
                             "VALUES (?, ?, 'other', 'Square refund', 'square', ?)", (c["id"], -rng.randint(1, paid), ts))
                logic.reconcile_tab_points(conn, c["id"], ts)
    split = logic.paid_by_money(conn, c["id"])
    paid = {ch["id"]: ch["paid_cents"] for ch in logic.tab_ledger(conn, c["id"])["charges"]}
    held = conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ? AND reason IN "
                        "('tab_repaid', 'tab_refund', 'credit_used', 'tab_adjust')", (c["id"],)).fetchone()[0]
    assert all(0 <= split[k] <= paid[k] for k in split), (split, paid)
    assert sum(split.values()) == held                       # bonus split == points rule
