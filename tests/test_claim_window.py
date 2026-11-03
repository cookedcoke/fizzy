"""The Claim sale button: ring the sale up in Square, then press. A sale belongs to the first press
in the 30 seconds after it was rung up (square.claim_next / claim_waiting), whatever order or delay
Square sends it with. Also the bar on every page, Undo, and Pick a sale."""
import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from fizzy import config, db, logic, pin, square
from tests.test_app import client, login  # noqa: F401  (fixture)
from tests.test_review_fixes import FakeSquare
from tests.test_rules import order, pay


T = datetime.now(timezone.utc).replace(microsecond=0)


def at(sec):
    """T plus `sec` seconds, as Square writes times."""
    return (T + timedelta(seconds=sec)).isoformat(timespec="seconds").replace("+00:00", "Z")


def when(sec):
    return T + timedelta(seconds=sec)


def sale(conn, pid, cents, sec, oid=None, came=None):
    """A sale rung up in Square `sec` seconds after T with no customer, as it reaches Fizzy `came`
    seconds after T (by default as it's rung up)."""
    square.process_payment(conn, pay(pid, cents, cust=None, ts=at(sec), order_id=oid), order(("Coke", cents), oid=oid) if oid else None)
    conn.execute("UPDATE square_payments SET processed_at = ? WHERE square_payment_id IN (?, ?)",
                 (when(max(sec, 0) if came is None else came).isoformat(), pid, f"{pid}:sale"))


def owner(conn, pid):
    return conn.execute("SELECT customer_id FROM square_payments WHERE square_payment_id = ?", (pid,)).fetchone()[0]


def waits(conn):
    return conn.execute("SELECT * FROM claim_waits ORDER BY id").fetchall()


def ts(s):
    return logic.parse_ts(s)


W, GRACE = square.CLAIM_WINDOW, square.CLAIM_GRACE


# ---- pressing with the sale already in ---------------------------------------------------------

def test_a_sale_from_10_seconds_ago_is_claimed_at_once(conn, cust):
    c = cust()
    sale(conn, "p1", 450, -10, oid="o1")
    what, k = square.claim_next(conn, c, "Will", now=when(0))
    assert what == "claimed" and k["key"] == "o:o1" and k["sale_cents"] == 450
    assert logic.points_balance(conn, c["id"]) == 450
    [w] = waits(conn)                                                # kept: it owns its 30 seconds for later presses
    assert (w["status"], w["checkout_key"], w["done_by"]) == ("claimed", "o:o1", "at once")
    assert square.recent_waits(conn, now=when(1)) == []              # nothing for the bar: the page said so


def test_the_newest_sale_in_the_30_seconds_is_the_one_claimed(conn, cust):
    c = cust()
    sale(conn, "p1", 200, -25)
    sale(conn, "p2", 300, -5)
    sale(conn, "old", 100, -31)
    what, k = square.claim_next(conn, c, "Will", now=when(0))
    assert k["key"] == "p:p2" and owner(conn, "p1") is None and owner(conn, "old") is None


def test_nothing_in_the_last_30_seconds_means_it_waits(conn, cust):
    c = cust()
    sale(conn, "old", 200, -45)                                      # the last customer's, unclaimed
    what, w = square.claim_next(conn, c, "Will", now=when(0.25))
    assert what == "waiting" and w["status"] == "waiting" and w["staff"] == "Will"
    assert ts(w["pressed_at"]) == when(0.25) and ts(w["wait_until"]) == when(0.25 + GRACE)
    assert owner(conn, "old") is None


def test_a_sale_rung_up_after_the_press_is_never_its(conn, cust):
    """Pressing first links nothing (that's what Pick a sale is for)."""
    c = cust()
    assert square.claim_next(conn, c, "Will", now=when(0))[0] == "waiting"
    sale(conn, "p1", 450, 2)
    for sec in range(2, GRACE + 2):
        assert square.claim_waiting(conn, now=when(sec)) == 0
    assert owner(conn, "p1") is None and waits(conn)[0]["status"] == "expired"


def ms(sec):
    """T plus `sec` seconds to the millisecond, as Square sends times."""
    return (T + timedelta(seconds=sec)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_the_30_seconds_are_counted_to_the_millisecond(conn, cust):
    """Square's times have milliseconds and Fizzy keeps whole seconds: the window uses Square's own.
    Rung up at 10.9 and pressed at 40.4 is 29.5 seconds; rung up at 50.6 is after a press at 50.4."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    square.process_payment(conn, pay("a", 450, cust=None, ts=ms(10.9)), None)
    assert square.claim_next(conn, a, "Will", now=when(40.4))[0] == "claimed" and owner(conn, "a") == a["id"]
    square.process_payment(conn, pay("x", 300, cust=None, ts=ms(50.6)), None)
    assert square.claim_next(conn, b, "Will", now=when(50.4))[0] == "waiting"
    square.claim_waiting(conn, now=when(52))
    assert owner(conn, "x") is None


def test_a_later_tap_written_first_doesnt_take_an_earlier_ones_sale(conn, cust):
    """Amy's sale is rung up at -3 and in; she's tapped at 0. Ben's is rung up at 1, not in yet; he's
    tapped at 2.5. Both requests wait on the same sync, and Ben's gets written first. Amy's press is
    on its way in (square.pressing), so it still owns her sale."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    sale(conn, "a", 100, -3)
    with square.pressing(when(0)) as amy:
        with square.pressing(when(2.5)) as ben:
            assert square.claim_next(conn, b, "Will", now=when(2.5), press=ben)[0] == "waiting"
            assert square.claim_waiting(conn, now=when(3)) == 0 and owner(conn, "a") is None
        assert square.claim_next(conn, a, "Will", now=when(0), press=amy)[0] == "claimed"
    sale(conn, "b", 300, 1, came=6)
    assert square.claim_waiting(conn, now=when(6)) == 1
    assert (owner(conn, "a"), owner(conn, "b")) == (a["id"], b["id"])


def test_a_press_on_its_way_in_that_adds_nothing_stops_counting(conn, cust):
    """A tap that turns out to be a press again while waiting adds no press, so once its request is
    done it doesn't hold anything up."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    square.claim_next(conn, a, "Will", now=when(0))
    sale(conn, "b", 300, 4, came=4)
    with square.pressing(when(5)) as again:
        assert square.claim_next(conn, b, "Will", now=when(8))[0] == "waiting"   # Amy's tap at 5 owns b for now
        assert square.claim_next(conn, a, "Will", now=when(5), press=again)[0] == "still waiting"
    assert square.claim_waiting(conn, now=when(9)) == 1 and owner(conn, "b") == b["id"]


def test_pressed_again_after_the_last_one_finished_is_a_new_press(conn, cust):
    """The owner's rule, on purpose: a press after the last one finished is a press like any other. So
    a repeat tap (unsure the first worked) owns the next sale rung up before it, even the next
    customer's: Ben's sale from 8 goes to Amy's tap at 10, not to Ben's at 14. Press once."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    sale(conn, "a", 100, 0)
    assert square.claim_next(conn, a, "Will", now=when(5))[0] == "claimed"
    assert square.claim_next(conn, a, "Will", now=when(10))[0] == "waiting"
    assert square.claim_next(conn, b, "Will", now=when(14))[0] == "waiting"
    sale(conn, "b", 300, 8, came=20)
    assert square.claim_waiting(conn, now=when(20)) == 1 and owner(conn, "b") == a["id"]


# ---- the sale reaches Fizzy after the press ----------------------------------------------------

def test_a_sale_sent_late_is_claimed_when_it_comes_in(conn, cust):
    c = cust()
    square.claim_next(conn, c, "Will", now=when(0))
    sale(conn, "p1", 450, -3)
    conn.execute("UPDATE square_payments SET processed_at = ?", (when(150).isoformat(),))
    assert square.claim_waiting(conn, now=when(150)) == 1           # 2.5 minutes late
    [w] = waits(conn)
    assert (w["status"], w["checkout_key"]) == ("claimed", "p:p1") and ts(w["done_at"]) == when(150)
    assert owner(conn, "p1") == c["id"] and logic.points_balance(conn, c["id"]) == 450
    log = conn.execute("SELECT * FROM audit_log WHERE action = 'claim_wait' ORDER BY id").fetchall()
    assert [r["details"] for r in log] == ["waiting for Square to send the sale", "claimed p:p1"] and log[-1]["staff"] == "Will"


def test_the_wait_expires_and_a_sale_after_that_stays_unclaimed(conn, cust):
    c = cust()
    square.claim_next(conn, c, "Will", now=when(0))
    assert square.claim_waiting(conn, now=when(GRACE)) == 0 and waits(conn)[0]["status"] == "waiting"
    assert square.claim_waiting(conn, now=when(GRACE + 1)) == 0 and waits(conn)[0]["status"] == "expired"
    sale(conn, "late", 300, -3, came=GRACE + 2)                      # its sale, after the wait gave up
    square.claim_waiting(conn, now=when(GRACE + 2))
    assert owner(conn, "late") is None


def test_back_to_back_with_the_first_sale_sent_last(conn, cust):
    """Amy pays at -3 and is pressed at 0; Ben pays at 8 and is pressed at 10. Ben's is in at once,
    Amy's only after a minute."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    assert square.claim_next(conn, a, "Will", now=when(0))[0] == "waiting"
    sale(conn, "b", 300, 8, came=9)
    assert square.claim_next(conn, b, "Will", now=when(10))[0] == "claimed" and owner(conn, "b") == b["id"]
    for sec in range(11, 70):
        assert square.claim_waiting(conn, now=when(sec)) == 0
    sale(conn, "a", 450, -3, came=70)
    assert square.claim_waiting(conn, now=when(70)) == 1 and owner(conn, "a") == a["id"]


def test_a_sale_an_earlier_press_owns_is_never_a_later_ones(conn, cust):
    """Amy's sale is rung up at -5 and she's pressed at 0; Ben is pressed at 20, before his own sale
    is in. Amy's is in Ben's 30 seconds too, but Amy's press came first after it, so it's hers, even
    once her press has expired."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    square.claim_next(conn, a, "Will", now=when(0))
    square.claim_next(conn, b, "Will", now=when(20))
    sale(conn, "a", 450, -5, came=21)
    assert square.claim_waiting(conn, now=when(21)) == 1 and owner(conn, "a") == a["id"]
    square.process_payment(conn, pay("x", 200, cust=None, ts=at(-4)), None)   # a stray one, in only after Amy's press expired
    assert square.claim_waiting(conn, now=when(GRACE + 5)) == 0 and owner(conn, "x") is None
    sale(conn, "b", 300, 15, came=GRACE + 6)
    assert square.claim_waiting(conn, now=when(GRACE + 6)) == 1 and owner(conn, "b") == b["id"]


def test_a_sync_bringing_the_sale_claims_it(conn, cust):
    c = cust()
    square.claim_next(conn, c, "Will")
    now = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(timespec="seconds")   # taken, then pressed
    result = square.sync(conn, client=FakeSquare(payments=[pay("p1", 450, cust=None, ts=now)]), light=True)
    assert result["claimed_waiting"] == 1 and owner(conn, "p1") == c["id"]


# ---- pressed again, Undo, given another way ----------------------------------------------------

def test_pressed_again_while_waiting_adds_nothing(conn, cust):
    c = cust()
    _, w = square.claim_next(conn, c, "Will", now=when(0))
    what, again = square.claim_next(conn, c, "Will", now=when(20))
    assert what == "still waiting" and again["id"] == w["id"] and len(waits(conn)) == 1
    sale(conn, "p1", 450, -10, came=40)                             # in its first press's 30 seconds
    assert square.claim_waiting(conn, now=when(40)) == 1 and owner(conn, "p1") == c["id"]


def test_pressed_again_as_its_sale_comes_in(conn, cust):
    """Pressed again while waiting, and the quick sync that press asks for brings the sale: that's
    the answer, not a new press."""
    c = cust()
    square.claim_next(conn, c, "Will", now=when(0))
    sale(conn, "p1", 450, -3, came=20.4)
    assert square.claim_waiting(conn, now=when(20.4)) == 1           # the sync, after the tap at 20
    what, k = square.claim_next(conn, c, "Will", now=when(20))
    assert what == "claimed" and k["key"] == "p:p1" and len(waits(conn)) == 1


def test_pressed_again_while_waiting_doesnt_take_a_later_sale(conn, cust):
    """The press again isn't a press: the sale rung up at 10 (before it) is nobody's."""
    c = cust()
    square.claim_next(conn, c, "Will", now=when(0))
    sale(conn, "x", 450, 10, came=11)
    assert square.claim_next(conn, c, "Will", now=when(20))[0] == "still waiting"
    square.claim_waiting(conn, now=when(21))
    assert owner(conn, "x") is None


def test_a_second_purchase(conn, cust):
    """Amy pays at -3 and is pressed at 0, then buys something else (15) and is pressed again (20).
    The second press takes only sales rung up after her first: a stray one from -1 stays unclaimed."""
    a = cust()
    sale(conn, "a1", 450, -3)
    assert square.claim_next(conn, a, "Will", now=when(0))[0] == "claimed"
    sale(conn, "x", 100, -1, came=12)
    assert square.claim_next(conn, a, "Will", now=when(20))[0] == "waiting"
    sale(conn, "a2", 200, 15, came=40)                               # Square was slow with it
    assert square.claim_waiting(conn, now=when(40)) == 1
    assert (owner(conn, "a1"), owner(conn, "a2"), owner(conn, "x")) == (a["id"], a["id"], None)


def test_a_double_tap_claims_once(conn, cust):
    """The page sends a double tap once (data-once). If one gets through anyway, the second is a new
    press: it waits, and can only take a sale rung up after the first, so the next customer's is fine."""
    a, b = cust(name="Noah W", email="n@x", sq="SQN"), cust(name="Isla B", email="i@x", sq="SQI")
    sale(conn, "pa", 240, -3)
    assert square.claim_next(conn, a, "Will", now=when(0))[0] == "claimed"
    assert square.claim_next(conn, a, "Will", now=when(0.3))[0] == "waiting"   # the second tap
    sale(conn, "pb", 380, 10)
    assert square.claim_next(conn, b, "Will", now=when(12))[0] == "claimed"
    assert owner(conn, "pa") == a["id"] and owner(conn, "pb") == b["id"]


def test_undo_stops_the_wait_and_the_sale_stays_unclaimed(conn, cust):
    c = cust()
    _, w = square.claim_next(conn, c, "Will", now=when(0))
    square.cancel_wait(conn, w["id"], "Mike")
    sale(conn, "p1", 450, -3, came=10)
    assert square.claim_waiting(conn, now=when(10)) == 0 and owner(conn, "p1") is None
    w = waits(conn)[0]
    assert (w["status"], w["done_by"]) == ("cancelled", "Mike")


def test_an_undone_press_doesnt_count(conn, cust):
    """Pressed on the wrong person's page, Undo, then pressed on the right one: the sale before both
    is the second press's."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    _, w = square.claim_next(conn, a, "Will", now=when(0))
    square.cancel_wait(conn, w["id"], "Will")
    square.claim_next(conn, b, "Will", now=when(4))
    sale(conn, "b", 450, -2, came=6)
    assert square.claim_waiting(conn, now=when(6)) == 1 and owner(conn, "b") == b["id"]


def test_undo_after_the_sale_came_in_leaves_the_sale(conn, cust):
    c = cust()
    _, w = square.claim_next(conn, c, "Will", now=when(0))
    sale(conn, "p1", 450, -3, came=10)
    square.claim_waiting(conn, now=when(10))
    with pytest.raises(ValueError, match="already finished"):
        square.cancel_wait(conn, w["id"], "Mike")
    assert owner(conn, "p1") == c["id"] and waits(conn)[0]["status"] == "claimed"


@pytest.mark.parametrize("how", ["pick", "square page", "square profile"])
def test_a_sale_given_another_way_ends_the_wait(conn, cust, how):
    a = cust(name="Amy A", email="a@x", sq="SQA")
    square.claim_next(conn, a, "Will", now=when(0))
    if how == "square profile":                                      # attached in Square after all
        square.process_payment(conn, pay("a", 450, cust="SQA", ts=at(-5)), None)
    else:
        sale(conn, "a", 450, -5)
        if how == "pick":
            square.claim(conn, "p:a", a, "Will")
        else:
            square.assign_customer(conn, "a", a, "Will")
    sale(conn, "x", 300, -2)                                         # a stray sale from its 30 seconds
    square.claim_waiting(conn, now=when(5))
    assert owner(conn, "x") is None and owner(conn, "a") == a["id"]
    assert [(w["status"], w["checkout_key"]) for w in waits(conn)] == [("claimed", "p:a")]


def test_already_theirs_when_pressed(conn, cust):
    """Their Square profile was on the sale: pressing says so, and doesn't take a stray sale."""
    a = cust(name="Amy A", email="a@x", sq="SQA")
    sale(conn, "x", 300, -10)
    square.process_payment(conn, pay("a", 450, cust="SQA", ts=at(-5)), None)
    what, k = square.claim_next(conn, a, "Will", now=when(0))
    assert what == "already" and k["key"] == "p:a" and owner(conn, "x") is None and waits(conn) == []


def test_a_wait_for_someone_archived_is_cancelled(conn, cust):
    c = cust()
    square.claim_next(conn, c, "Will", now=when(0))
    conn.execute("UPDATE customers SET archived_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
    sale(conn, "p1", 450, -5, came=10)
    assert square.claim_waiting(conn, now=when(10)) == 0
    assert waits(conn)[0]["status"] == "cancelled" and owner(conn, "p1") is None


# ---- the one thing it can't tell apart (the README says so) -------------------------------------

def test_a_stray_sale_in_first_is_taken():
    """A sale nobody pressed for (x) rung up in the same 30 seconds as Amy's: with both in, the press
    takes the newest; with only the stray one in, it takes that."""
    presses, sales = [("Amy", 0)], {"x": (None, -20), "a": ("Amy", -3)}
    assert play(presses, sales, {"x": -19, "a": -2}) == {"x": None, "a": "Amy"}
    assert play(presses, sales, {"a": 1, "x": 2}) == {"x": None, "a": "Amy"}
    assert play(presses, sales, {"x": 1, "a": 2}) == {"x": "Amy", "a": None}


# ---- checkouts in several parts, tips, tab tenders ----------------------------------------------

def test_a_split_payment_is_matched_once_all_of_it_is_in(conn, cust):
    a = cust()
    o = order(("Coke", 450), ("Chips", 300), oid="o1")
    square.process_payment(conn, pay("p2", 300, cust=None, ts=at(-4), order_id="o1"), o)
    assert square.claim_next(conn, a, "Will", now=when(0))[0] == "waiting"   # half of it: not matched yet
    square.process_payment(conn, pay("p1", 450, cust=None, ts=at(-12), order_id="o1"), o)
    assert square.claim_waiting(conn, now=when(5)) == 1                 # by its first payment's time
    assert owner(conn, "p1") == a["id"] == owner(conn, "p2") and logic.points_balance(conn, a["id"]) == 750


def test_a_split_payment_whose_first_part_is_before_the_last_press_isnt_this_ones(conn, cust):
    """Amy's two cards at -8 and 2 (after her press at 0: ring-up still going), Ben pressed at 10."""
    a, b = cust(name="Amy A", email="a@x", sq="SQA"), cust(name="Ben B", email="b@x", sq="SQB")
    square.claim_next(conn, a, "Will", now=when(-1))
    square.claim_next(conn, b, "Will", now=when(10))
    o = order(("Coke", 600), oid="oA")
    square.process_payment(conn, pay("p2", 300, cust=None, ts=at(2), order_id="oA"), o)
    assert square.claim_waiting(conn, now=when(11)) == 0             # half in: its time isn't known
    square.process_payment(conn, pay("p1", 300, cust=None, ts=at(-8), order_id="oA"), o)
    assert square.claim_waiting(conn, now=when(12)) == 1 and owner(conn, "p1") == a["id"] == owner(conn, "p2")


def test_a_split_tender_order_listing_a_payment_not_yet_in_waits_for_it(conn, cust):
    a = cust()
    o = {**order(("Coke", 600), oid="o1"), "total_money": {"amount": 600},
         "tenders": [{"id": "t1", "payment_id": "p1"}, {"id": "t2", "payment_id": "p2"}]}
    square.process_payment(conn, pay("p2", 600, cust=None, ts=at(-2), order_id="o1"), o)   # says it's all paid, but p1 isn't in
    assert square.claim_next(conn, a, "Will", now=when(0))[0] == "waiting"
    square.process_payment(conn, pay("p1", 0, cust=None, ts=at(-6), order_id="o1"), o)
    assert square.claim_waiting(conn, now=when(1)) == 1 and owner(conn, "p1") == a["id"] == owner(conn, "p2")


def test_a_card_sale_with_a_tip(conn, cust):
    """Square's order total has the tip in it; the payment's amount_money doesn't (that's tip_money)."""
    a = cust()
    o = {**order(("Coke", 450), oid="o1"), "total_money": {"amount": 500}, "total_tip_money": {"amount": 50},
         "tenders": [{"id": "t1", "payment_id": "p1"}]}
    p = {**pay("p1", 450, cust=None, source="CARD", ts=at(-4), order_id="o1"), "tip_money": {"amount": 50},
         "total_money": {"amount": 500}}
    square.process_payment(conn, p, o)
    what, k = square.claim_next(conn, a, "Will", now=when(0))
    assert what == "claimed" and k["key"] == "o:o1" and owner(conn, "p1") == a["id"]


def test_a_sale_paid_with_a_tab_tender(conn, cust, monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TAB_TENDERS", {"EXTERNAL"})
    a = cust()
    o = {**order(("Coke", 600), oid="o1"), "total_money": {"amount": 600}}
    square.process_payment(conn, pay("p1", 200, cust=None, ts=at(-6), order_id="o1"), o)
    assert square.claim_next(conn, a, "Will", now=when(0))[0] == "waiting"     # the tab part isn't in yet
    square.process_payment(conn, pay("p2", 400, cust=None, source="EXTERNAL", ts=at(-5), order_id="o1"), o)
    assert square.claim_waiting(conn, now=when(3)) == 1
    k = square.checkout(conn, "o:o1")
    assert (k["sale_cents"], k["tab_cents"]) == (200, 400) and owner(conn, "p2") == a["id"]


def test_a_half_linked_checkout_doesnt_block_a_waiting_press(conn, cust):
    """Ben's checkout is two tenders: p1 with no customer syncs first, then p2 with Ben's Square
    profile. p1 goes to Ben with it (a checkout is never split), so Amy's press gets her own sale."""
    amy, ben = cust(name="Amy A", email="a@x"), cust(name="Ben B", email="b@x", sq="SQB")
    o = order(("Coke", 200), oid="o1")
    square.process_payment(conn, pay("p1", 100, cust=None, ts=at(-6), order_id="o1"), o)
    assert square.claim_next(conn, amy, "Will", now=when(0))[0] == "waiting"   # half of Ben's: not matched
    square.process_payment(conn, pay("p2", 100, cust="SQB", ts=at(-5), order_id="o1"), o)
    assert owner(conn, "p1") == ben["id"] == owner(conn, "p2") and logic.points_balance(conn, ben["id"]) == 200
    sale(conn, "pa", 300, -2, came=3)
    assert square.claim_waiting(conn, now=when(3)) == 1 and owner(conn, "pa") == amy["id"]


def test_a_checkout_claim_refuses_is_skipped(conn, cust):
    """If claim() refuses the newest (say part of it is someone's), the press takes the next."""
    amy = cust(name="Amy A", email="a@x")
    square.claim_next(conn, amy, "Will", now=when(0))
    sale(conn, "pa", 300, -10, came=5)
    sale(conn, "p1", 100, -2, oid="o1", came=5)
    real = square.claim

    def refuse(conn, key, *a):
        if key == "o:o1":
            raise ValueError("That sale has already been claimed.")
        return real(conn, key, *a)
    square.claim = refuse
    try:
        assert square.claim_waiting(conn, now=when(5)) == 1
    finally:
        square.claim = real
    assert owner(conn, "pa") == amy["id"] and owner(conn, "p1") is None


def test_the_bar_lists_waiting_and_just_finished(conn, cust):
    a, b = cust(name="Amy Ash", email="a@x", sq="SQA"), cust(name="Ben Bo", email="b@x", sq="SQB")
    square.claim_next(conn, a, "Will", now=when(0))
    square.claim_next(conn, b, "Will", now=when(10))
    sale(conn, "pa", 450, -3, came=12)
    square.claim_waiting(conn, now=when(12))
    ws = square.recent_waits(conn, now=when(13))
    assert [(w["name"], w["status"]) for w in ws] == [("Amy Ash", "claimed"), ("Ben Bo", "waiting")]
    assert ws[0]["sale"]["sale_cents"] == 450 and ts(ws[1]["pressed_at"]) == when(10)
    assert ts(ws[1]["wait_until"]) == when(10 + GRACE) and "window_to" not in ws[1]
    assert [w["status"] for w in square.recent_waits(conn, now=when(11 + GRACE))] == ["expired"]   # shown as given up
    assert square.recent_waits(conn, now=when(11 + GRACE + square.CLAIM_SHOWN)) == []


# ---- the rule doesn't care what order or delay Square sends sales with --------------------------
# Pay, then press: 1 to 4 customers back to back, each sale rung up a little before its press (and
# after the press before). Every order the sales could reach Fizzy in is played second by second:
# presses at their time, claim_waiting after each arrival and every second (the watcher). Deliveries:
# one a second (never before it was rung up); all after the last press; and one every 20, 40 or 49
# seconds (so the first can be about 150 seconds late).

def run(names, events, end=None):
    """events: (second, action...), played second by second with claim_waiting every second (the
    watcher). ("press", name); ("pay", pid, Square second) = that sale reaches Fizzy now.
    Returns {pid: whose}."""
    conn = db.connect(":memory:")
    try:
        db.init(conn)
        people = {}
        for i, n in enumerate(names):
            cid = conn.execute("INSERT INTO customers(name, email, created_at) VALUES (?, ?, ?)", (n, f"{i}@x", db.now_iso())).lastrowid
            people[n] = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
        by = {}
        for e in events:
            by.setdefault(e[0], []).append(e[1:])
        for sec in range(min(by), (end or max(by) + 5) + 1):
            for act in sorted(by.get(sec, []), key=lambda a: a[0] == "press"):   # what's in at a press is in first
                if act[0] == "press":
                    square.claim_next(conn, people[act[1]], "Will", now=when(sec))
                else:
                    square.process_payment(conn, pay(act[1], 100, cust=None, ts=at(act[2])), None)
                    square.claim_waiting(conn, now=when(sec))
            square.claim_waiting(conn, now=when(sec))
        names_by = {c["id"]: n for n, c in people.items()}
        return {e[2]: names_by.get(owner(conn, e[2])) for e in events if e[1] == "pay"}
    finally:
        conn.close()


def play(presses, sales, arrive):
    """Fresh database; `arrive`: {sale id: second it reaches Fizzy}. Returns {sale id: whose}."""
    events = [(p, "press", n) for n, p in presses] + [(arrive[s], "pay", s, sec) for s, (_, sec) in sales.items()]
    return run(sorted({n for n, _ in presses}), events, end=max(e[0] for e in events) + 2)


def back_to_back(gaps, lead=3):
    """Presses `gaps` seconds apart from 0, each sale rung up `lead` seconds before its press (less
    when the gap is short, so it's after the press before)."""
    names = ["Amy", "Ben", "Cal", "Dee"][:len(gaps) + 1]
    times = list(itertools.accumulate([0, *gaps]))
    leads = [lead] + [min(lead, g - 1) for g in gaps]
    return [(n, p) for n, p in zip(names, times)], {n[0].lower(): (n, p - d) for n, p, d in zip(names, times, leads)}


SCENARIOS = {f"gaps {gaps}, rung up {lead}s before": (gaps, lead) for gaps, lead in [
    ((), 1), ((), 29),
    ((2,), 3), ((10,), 3), ((31,), 3), ((40,), 25),
    ((2, 2), 3), ((5, 40), 3), ((31, 3), 20),
    ((2, 2, 2), 3), ((10, 30, 5), 8), ((40, 40, 40), 3), ((3, 25, 2), 29),
]}
STYLES = {"as they happen": 1, "after the last press": 1, "every 20 seconds": 20, "every 40 seconds": 40, "every 49 seconds": 49}


def deliveries(presses, sales, came, style):
    arrive, clock, last = {}, -10 ** 6, max(p for _, p in presses)
    for sid in came:
        clock = max(clock + STYLES[style], sales[sid][1] + 1, last + 1 if style == "after the last press" else -10 ** 6)
        arrive[sid] = clock
    return arrive


@pytest.mark.parametrize("scenario,style", [(name, style) for name in SCENARIOS for style in STYLES])
def test_every_arrival_order_gives_the_same_owners(scenario, style):
    presses, sales = back_to_back(*SCENARIOS[scenario])
    want = {sid: whose for sid, (whose, _) in sales.items()}
    tried = 0
    for came in itertools.permutations(sales):
        arrive = deliveries(presses, sales, came, style)
        if max(arrive[s] - sales[s][1] for s in sales) > 150:  # up to 150 seconds late
            continue
        assert play(presses, sales, arrive) == want, (came, arrive)
        tried += 1
    assert tried


def test_the_press_counts_from_the_tap_not_from_when_square_answers(on, monkeypatch):
    """A sale rung up 28 seconds before the tap is in. Square takes 2.5 seconds to answer the sync
    the button asks for: the press still counts from the tap, so the sale is claimed."""
    import time
    monkeypatch.setattr(square, "sync", lambda conn, *a, **k: time.sleep(2.5) or {})
    tapped = datetime.now(timezone.utc)
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=(tapped - timedelta(seconds=28)).isoformat()), None))
    r = on.post("/customers/1/claim/next")
    assert "Claimed the " in r.text and put(lambda c: owner(c, "p1")) == 1
    pressed = put(lambda c: c.execute("SELECT pressed_at FROM claim_waits").fetchone()[0])
    assert (logic.parse_ts(pressed) - tapped).total_seconds() < 1


# ---- the pages ----------------------------------------------------------------------------------

@pytest.fixture
def on(client, monkeypatch):
    """Signed in, one customer (Nora, id 1), and Square "connected" without asking the real one."""
    monkeypatch.setattr(config, "SQUARE_TOKEN", "fake")
    monkeypatch.setattr(square, "sync", lambda conn, *a, **k: {})
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    return client


def put(fn):
    conn = db.connect()
    try:
        out = fn(conn)
        conn.commit()
        return out
    finally:
        conn.close()


JSON = {"X-Fizzy-Format": "json"}


def test_customer_page_has_both_buttons(on):
    page = on.get("/customers/1").text
    assert 'action="/customers/1/claim/next"' in page and ">Claim sale</button>" in page
    assert 'href="/customers/1/claim"' in page and "Pick a sale" in page
    assert "last 30 seconds" in page


def test_not_connected(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Nora Le Fevre", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    r = client.post("/customers/1/claim/next")
    assert "Square isn&#39;t connected, so there&#39;s nothing to claim." in r.text
    assert client.post("/customers/1/claim/next", headers=JSON).json() == {"error": "Square isn't connected, so there's nothing to claim."}


def test_button_claims_a_sale_just_taken(on):
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=datetime.now(timezone.utc).isoformat()), None))
    r = on.post("/customers/1/claim/next")
    assert r.url.path == "/customers/1"
    assert "sale for Nora ($4.50 paid)." in r.text and "Claimed the " in r.text
    assert put(lambda c: owner(c, "p1")) == 1


def test_button_waits_and_the_bar_shows_on_other_pages(on):
    r = on.post("/customers/1/claim/next")
    assert "Waiting for Nora&#39;s sale. It&#39;ll link as soon as Square sends it." in r.text
    page = on.get("/transactions").text
    assert 'id="claimbar"' in page and "Waiting for Square to send <b>Nora Le Fevre</b>'s sale" in page
    assert 'action="/claim-waits/1/cancel"' in page and ">Undo</button>" in page and "data-to" not in page
    api = on.get("/api/claim-waits")
    assert api.headers["cache-control"] == "no-store"
    [w] = api.json()["waits"]
    assert (w["status"], w["name"], w["customer_id"], w["first"]) == ("waiting", "Nora Le Fevre", 1, "Nora")
    assert "window_to" not in w and "left" not in w


def test_pressed_again_while_waiting_says_so(on):
    on.post("/customers/1/claim/next")
    r = on.post("/customers/1/claim/next")
    assert "Already waiting for Nora&#39;s sale." in r.text
    d = on.post("/customers/1/claim/next", headers=JSON).json()
    assert d["waiting"]["id"] == 1 and d["already"] is True
    assert put(lambda c: c.execute("SELECT COUNT(*) FROM claim_waits").fetchone()[0]) == 1


def test_no_bar_when_nothing_is_waiting(on):
    page = on.get("/transactions").text
    assert '<div class="claimbar" id="claimbar" role="status" aria-live="polite" hidden>' in page and 'class="cw' not in page
    assert '" hidden>' in page.split('id="claimbar"')[1].split("\n")[0]


def test_undo_from_the_bar(on):
    on.post("/customers/1/claim/next")
    r = on.post("/claim-waits/1/cancel", headers={"Referer": "http://testserver/transactions"})
    assert r.url.path == "/transactions" and "Stopped waiting for Nora&#39;s sale." in r.text
    r = on.post("/claim-waits/1/cancel")
    assert r.url.path == "/customers/1" and "That claim already finished." in r.text
    assert on.get("/api/claim-waits").json()["waits"] == []


def test_undo_on_a_claimed_one_keeps_the_sale(on):
    on.post("/customers/1/claim/next")
    taken = datetime.now(timezone.utc) - timedelta(seconds=2)      # taken just before the press
    put(lambda c: (square.process_payment(c, pay("p1", 450, cust=None, ts=taken.isoformat()), None), square.claim_waiting(c)))
    r = on.post("/claim-waits/1/cancel")
    assert "That claim already finished." in r.text and put(lambda c: owner(c, "p1")) == 1
    [w] = on.get("/api/claim-waits").json()["waits"]
    assert w["status"] == "claimed" and w["amounts"] == "$4.50 paid"
    assert "Nora Le Fevre</b>'s sale is in: $4.50 paid." in on.get("/").text


def test_the_pop_up_stays_quiet_while_a_press_is_waiting(on):
    since = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="seconds")
    old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds")
    put(lambda c: square.process_payment(c, pay("p0", 200, cust=None, ts=old), None))   # outside the 30 seconds
    assert len(on.get(f"/api/customers/1/new-sales?since={since}").json()) == 1
    on.post("/tools/add_customer", data={"name": "Ivy Other", "email": "rns6@reddamnorthshore.nsw.edu.au"})
    on.post("/customers/2/claim/next")                               # waiting for someone else's sale
    assert on.get(f"/api/customers/1/new-sales?since={since}").json() == []
    on.post("/claim-waits/1/cancel")
    assert len(on.get(f"/api/customers/1/new-sales?since={since}").json()) == 1


def test_json_answers_for_the_app(on):
    w = on.post("/customers/1/claim/next", headers=JSON).json()["waiting"]
    assert (w["id"], w["customer_id"], w["name"]) == (1, 1, "Nora Le Fevre") and w["pressed_at"] < w["wait_until"]
    assert on.post("/claim-waits/1/cancel", headers=JSON).json() == {"cancelled": 1}
    r = on.post("/claim-waits/1/cancel", headers=JSON)
    assert r.status_code == 409 and r.json() == {"error": "That claim already finished."}
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=datetime.now(timezone.utc).isoformat()), None))
    k = on.post("/customers/1/claim/next", headers=JSON).json()["claimed"]
    assert k["key"] == "p:p1" and k["paid"] == "$4.50"
    # The JSON pages the app already uses don't change.
    page = on.get("/customers/1", headers=JSON).json()
    assert "claim_waits" not in page


def test_a_second_send_after_a_claim_is_a_new_press(on):
    """The app sending the press twice (or a second till) after it claimed at once: the second is a new
    press, by the rule, so it waits (and hides the pop-up) until it expires or a sale rung up after the
    first comes in."""
    put(lambda c: square.process_payment(c, pay("p1", 220, cust=None, ts=datetime.now(timezone.utc).isoformat()), None))
    assert on.post("/customers/1/claim/next", headers=JSON).json()["claimed"]["key"] == "p:p1"
    w = on.post("/customers/1/claim/next", headers=JSON).json()
    assert "waiting" in w and "already" not in w and put(lambda c: owner(c, "p1")) == 1


def test_the_button_counts_its_press_while_it_waits_on_square(on, monkeypatch):
    """While a press's request waits on Square, a later tap for someone else (written first) can't take
    its sale."""
    on.post("/tools/add_customer", data={"name": "Ben Ng", "email": "ben@x"})
    tapped = datetime.now(timezone.utc)
    put(lambda c: square.process_payment(c, pay("a", 450, cust=None, ts=(tapped - timedelta(seconds=3)).isoformat()), None))

    def ben_meanwhile(conn, *a, **k):  # Ben's whole request runs during Nora's wait on Square
        ben = put(lambda c: c.execute("SELECT * FROM customers WHERE id = 2").fetchone())
        what = put(lambda c: square.claim_next(c, ben, "Will", now=datetime.now(timezone.utc))[0])
        assert what == "waiting"
        return {}
    monkeypatch.setattr(square, "sync", ben_meanwhile)
    r = on.post("/customers/1/claim/next")
    assert "Claimed the " in r.text and put(lambda c: owner(c, "a")) == 1


def test_passcode_level_is_the_claim_level(client):
    conn = db.connect()
    try:
        db.init(conn)
        cid = conn.execute("INSERT INTO customers(name, email, kind, created_at) VALUES ('Staff Kid', 's@x', 'admin', ?)",
                           (db.now_iso(),)).lastrowid
        pin.set_pin(conn, "4821", "t")
        r = lambda p: pin.required(conn, "POST", p, {})  # noqa: E731
        assert r(f"/customers/{cid}/claim/next") == pin.OPEN == r("/claim-waits/3/cancel")   # admin accounts too
        db.set_setting(conn, "permissions", json.dumps({"claim": "pin"}))
        assert r(f"/customers/{cid}/claim/next") == pin.PIN == r("/claim-waits/3/cancel") == r(f"/customers/{cid}/claim")
    finally:
        conn.close()
    from pathlib import Path
    app_js = (Path(__file__).resolve().parent.parent / "fizzy" / "static" / "app.js").read_text()
    assert r"/\/(claim|claim\/next|kind)$/" in app_js


def test_the_old_list_page_and_its_claim_still_work(on):
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, order_id="o1"), order(("Coke", 450), oid="o1")))
    d = on.get("/customers/1/claim", headers=JSON).json()
    assert [k["key"] for k in d["checkouts"]] == ["o:o1"] and d["square_on"] is True and d["hours"] == 6 and "note" in d
    assert 'name="key" value="o:o1"' in on.get("/customers/1/claim").text
    r = on.post("/customers/1/claim", data={"key": "o:o1"})
    assert "Sale claimed for Nora Le Fevre" in r.text


def test_archived_customer_has_no_button(on):
    put(lambda c: c.execute("UPDATE customers SET archived_at = ? WHERE id = 1", (db.now_iso(),)))
    assert 'action="/customers/1/claim/next"' not in on.get("/customers/1").text
    assert "archived" in on.post("/customers/1/claim/next").text


def test_the_watcher_claims_and_stops(on, monkeypatch):
    from fizzy import app as appmod
    monkeypatch.setattr(appmod, "CLAIM_WATCH_SECONDS", 0.05)
    on.post("/customers/1/claim/next")
    calls = []

    def fake_sync(conn, *a, **k):
        calls.append(1)
        if len(calls) == 2:  # the sale reaches Fizzy on the second look
            taken = datetime.now(timezone.utc) - timedelta(seconds=2)   # taken just before the press: not held
            square.process_payment(conn, pay("p1", 450, cust=None, ts=taken.isoformat()), None)
            conn.commit()
        return {}
    monkeypatch.setattr(square, "sync", fake_sync)
    appmod.watch_claims()
    t = appmod._watch_thread
    t.join(5)
    assert not t.is_alive() and appmod._watch_thread is None
    assert put(lambda c: owner(c, "p1")) == 1 and len(calls) >= 2


def test_already_theirs_on_the_page(on):
    taken = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    put(lambda c: (c.execute("UPDATE customers SET square_customer_id = 'SQN' WHERE id = 1"),
                   square.process_payment(c, pay("p1", 450, cust="SQN", ts=taken), None)))   # on their Square profile
    r = on.post("/customers/1/claim/next")
    assert "Nora already has the " in r.text and "($4.50 paid), so nothing more was claimed." in r.text
    d = on.post("/customers/1/claim/next", headers=JSON).json()
    assert d["already"] is True and d["claimed"]["key"] == "p:p1"
    assert put(lambda c: c.execute("SELECT COUNT(*) FROM claim_waits").fetchone()[0]) == 0
    assert "data-once" in on.get("/customers/1").text


def test_pick_a_sale_ends_the_wait_at_once(on):
    on.post("/customers/1/claim/next")
    put(lambda c: square.process_payment(c, pay("p1", 450, cust=None, ts=datetime.now(timezone.utc).isoformat()), None))
    on.post("/customers/1/claim", data={"key": "p:p1"})
    [w] = on.get("/api/claim-waits").json()["waits"]
    assert w["status"] == "claimed" and w["amounts"] == "$4.50 paid"


def test_pick_a_sale_says_when_there_are_older_ones(on):
    now = datetime.now(timezone.utc)
    for i in range(square.PICK_SHOWN + 1):
        put(lambda c: square.process_payment(c, pay(f"p{i}", 100 + i, cust=None, ts=(now - timedelta(minutes=i)).isoformat()), None))
    page = on.get("/customers/1/claim").text
    assert page.count('<button name="key"') == square.PICK_SHOWN and "Only the latest 6 are here." in page
    assert 'href="/square?show=unlinked"' in page
    assert len(on.get("/customers/1/claim", headers=JSON).json()["checkouts"]) == square.PICK_SHOWN


# ---- what a sync costs while a press waits ------------------------------------------------------

class Counting(FakeSquare):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.got = []

    def customers(self):
        self.got.append("customers")
        return super().customers()

    def order(self, oid):
        self.got.append(("order", oid))
        return super().order(oid)


def test_a_sync_doesnt_fetch_orders_for_sales_it_already_has(conn):
    now = datetime.now(timezone.utc)
    ps = [pay(f"p{i}", 100, cust=None, ts=(now - timedelta(minutes=30, seconds=-i)).isoformat(), order_id=f"o{i}")
          for i in range(50)]
    fs = Counting(payments=ps)
    square._sync_once(conn, fs)
    assert fs.got.count("customers") == 1 and len(fs.got) == 51
    fs.got.clear()
    square._sync_once(conn, fs)                                      # the same hour again, nothing new
    assert fs.got == ["customers"]
    fs.got.clear()
    square._sync_once(conn, fs, light=True)                          # the watcher's: no customer list either
    assert fs.got == []


def test_a_light_sync_still_links_a_new_square_profile(conn):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fs = Counting(customers=[{"id": "SQNEW", "given_name": "Zoe", "family_name": "New", "email_address": "z@x"}],
                  payments=[pay("p1", 450, cust="SQNEW", ts=now), pay("p2", 100, cust=None, ts=now)])
    result = square._sync_once(conn, fs, light=True)
    assert fs.got.count("customers") == 1 and result["created"] == 1
    zoe = conn.execute("SELECT id FROM customers WHERE square_customer_id = 'SQNEW'").fetchone()["id"]
    assert owner(conn, "p1") == zoe and owner(conn, "p2") is None
    assert db.get_setting(conn, "square_full_sync") in (None, "")   # the poller still does its full one


def test_a_full_sync_asked_for_during_a_light_one_runs_full(conn, monkeypatch):
    runs = []

    def once(conn, client, catalog=False, light=False):
        runs.append(light)
        if len(runs) == 1:  # a webhook's full sync arrives while the watcher's light one runs
            assert square.sync(conn, client=client) == {"queued": "a sync is already running; this one will run straight after"}
        return {}
    monkeypatch.setattr(square, "_sync_once", once)
    square.sync(conn, client=FakeSquare(), light=True)
    assert runs == [True, False]


def test_a_light_sync_asked_for_during_a_full_one_runs_light(conn, monkeypatch):
    runs = []

    def once(conn, client, catalog=False, light=False):
        runs.append(light)
        if len(runs) == 1:  # the watcher asks while the poller's full sync runs
            assert "queued" in square.sync(conn, client=client, light=True)
        return {}
    monkeypatch.setattr(square, "_sync_once", once)
    square.sync(conn, client=FakeSquare())
    assert runs == [False, True]


def test_the_watcher_and_the_button_ask_for_light_syncs(on, monkeypatch):
    from fizzy import app as appmod
    monkeypatch.setattr(appmod, "CLAIM_WATCH_SECONDS", 0.05)
    seen = []
    monkeypatch.setattr(square, "sync", lambda conn, *a, **k: seen.append(k.get("light")) or {})
    monkeypatch.setattr(appmod.app.state, "background", True, raising=False)
    on.post("/customers/1/claim/next")
    t = appmod._watch_thread
    put(lambda c: square.cancel_wait(c, 1, "Will"))
    if t is not None:
        t.join(5)
    assert seen and all(seen)
