"""Custom badges and events, made from building blocks (usually by asking Gemini on the Ask page).

A rule is JSON that Fizzy checks and runs itself: no generated code ever runs. Two kinds:

  badge  "Buy 20 Chi Forest and 10 milk tea in a term" -> +500 points, once (or once a week/month/term)
  event  "Double points on noodles at lunch on Fridays", "every 5th Coke gets 100 bonus points"

Purchases are Fizzy's checkouts (stats.checkouts): when, items (name, qty, cents), money split
(sale / tab), tenders, customer. Points from rules go in points_ledger (reason 'rule', rule_key
unique per grant), so each grant is paid once and survives restarts. Nothing is taken back.

The spec below is also what Gemini is shown, so it's the one place that says what's possible.
"""
import json
import re
from datetime import date, datetime, time, timedelta, timezone

from . import config, logic, stats
from .db import audit, get_setting, now_iso

SPEC = r"""
RULE = {
  "kind": "badge" | "event",
  "name": short name students see, e.g. "Bubble Boss",
  "summary": one plain-English sentence students see, e.g. "Buy 20 Chi Forest and 10 milk teas this term",
  "icon": optional single emoji for the student site (badges only),
  "window": WINDOW (optional; default: from when the rule is created, for ever),
  "where": FILTER (optional; which purchases count at all),

  # badge only:
  "need": CONDITION,                 # when it's earned
  "points": int 1..5000,             # paid when earned
  "repeat": "once" | "day" | "week" | "month" | "term",   # default "once": can be earned again each period
  "count_past": true|false,          # default true: purchases before the rule existed (inside the window) count

  # event only (applies to each purchase matching "where", from when it's created):
  "bonus": {"type": "multiplier", "value": number 1.1..10, "on": "matching_items" | "whole_purchase"}
         | {"type": "points", "points": int 1..2000},   # flat bonus per qualifying purchase
  "every_nth": int (optional; only every Nth qualifying purchase by that student gets the bonus),
  "limit": {"per": "day" | "week" | "month" | "term" | "ever", "times": int} (optional; most bonuses per student)
}

WINDOW (pick one form):
  {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}   # either end optional, inclusive, Sydney dates
  {"term": "current" | "<term name as listed>"}
  {"this": "week" | "month" | "term" | "year"}  # moves with time, e.g. a monthly badge
  {"last_days": int}

FILTER (all keys optional, all must hold; matching is on a purchase, i.e. one checkout):
  "items": [item names]          # the purchase includes any of these (case-insensitive; partial names match,
                                 # e.g. "chi forest" matches "Chi Forest Peach"). Also limits which items count
                                 # for "units"/"spend"/"matching_items".
  "exclude_items": [item names]  # these never count
  "weekdays": ["mon","tue","wed","thu","fri","sat","sun"]
  "time_from": "HH:MM", "time_to": "HH:MM"     # Sydney time of the purchase
  "dates": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}
  "min_spend": dollars, "max_spend": dollars   # the purchase's total (sale + tab)
  "min_units": int                             # matching items in this one purchase
  "paid_with": ["card", "cash", "tab", "other"]
  "on_double_points": true|false               # during a double points day
  "first_of_day": int                          # only the first N purchases of the day (by anyone)
  "student_first_of_day": true                 # only the student's first purchase that day

CONDITION (badges):
  {"all": [CONDITION, ...]} | {"any": [CONDITION, ...]} | {"at_least": n, "of": [CONDITION, ...]} | {"not": CONDITION}
  | {"metric": METRIC, "op": ">=" | ">" | "<=" | "<" | "==", "value": number, "where": FILTER (optional, narrows further)}
  | {"metric": "tried_all", "items": [item names]}                 # bought every one of these at least once

METRIC (over the student's purchases inside window + where):
  "units"            items bought (respects "items")
  "spend"            dollars spent on matching items
  "purchases"        number of purchases
  "days"             different days with a purchase
  "weeks"            different weeks with a purchase
  "distinct_items"   different items bought
  "max_units_day"    most matching items in a single day
  "max_purchases_day" most purchases in a single day
  "streak"           best run of stall trading days in a row with a purchase
  "tabs_paid_on_time" tab charges paid off on time
  "rewards_redeemed" rewards redeemed
  "points_earned"    points earned from purchases
"""

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
METRICS = {"units", "spend", "purchases", "days", "weeks", "distinct_items", "max_units_day", "max_purchases_day", "streak",
           "tabs_paid_on_time", "rewards_redeemed", "points_earned"}
OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
       "==": lambda a, b: a == b}
REPEATS = {"once", "day", "week", "month", "term"}


class RuleError(Exception):
    pass


# ---- checking a rule ---------------------------------------------------------------------------

def _date(s, what):
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        raise RuleError(f"{what} must be a date like 2026-11-03.")


def _hhmm(s, what):
    if not re.fullmatch(r"\d{1,2}:\d{2}", str(s)):
        raise RuleError(f"{what} must be a time like 13:10.")
    h, m = map(int, str(s).split(":"))
    if h > 23 or m > 59:
        raise RuleError(f"{what} isn't a real time.")
    return h * 60 + m


def _num(v, what, lo, hi, whole=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (whole and v != int(v)) or not lo <= v <= hi:
        raise RuleError(f"{what} must be a {'whole ' if whole else ''}number from {lo} to {hi}.")
    return int(v) if whole else v


def _names(v, what):
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v) or len(v) > 50:
        raise RuleError(f"{what} must be a list of item names.")
    return [" ".join(x.split()).lower() for x in v]


def _check_window(w, conn):
    if w is None:
        return
    if not isinstance(w, dict) or len(w) == 0:
        raise RuleError("window must be an object.")
    if "from" in w or "to" in w:
        a = _date(w["from"], "window.from") if w.get("from") else None
        b = _date(w["to"], "window.to") if w.get("to") else None
        if a and b and b < a:
            raise RuleError("The window ends before it starts.")
    elif "term" in w:
        if w["term"] != "current" and not any(t[2].lower() == str(w["term"]).lower() for t in terms(conn)):
            raise RuleError(f"There's no term called {w['term']!r}. Add term dates in Settings first.")
        if w["term"] == "current" and not terms(conn):
            raise RuleError("Add term dates in Settings first (Settings, Term dates).")
    elif "this" in w:
        if w["this"] not in ("week", "month", "term", "year"):
            raise RuleError("window.this must be week, month, term or year.")
    elif "last_days" in w:
        _num(w["last_days"], "window.last_days", 1, 3650, True)
    else:
        raise RuleError("window needs from/to, term, this or last_days.")


def _check_filter(f, what="where"):
    if f is None:
        return
    if not isinstance(f, dict):
        raise RuleError(f"{what} must be an object.")
    known = {"items", "exclude_items", "weekdays", "time_from", "time_to", "dates", "min_spend", "max_spend", "min_units",
             "paid_with", "on_double_points", "first_of_day", "student_first_of_day"}
    extra = set(f) - known
    if extra:
        raise RuleError(f"{what} has something Fizzy doesn't know: {', '.join(sorted(extra))}.")
    for k in ("items", "exclude_items"):
        if k in f:
            _names(f[k], f"{what}.{k}")
    if "weekdays" in f and (not isinstance(f["weekdays"], list) or not set(f["weekdays"]) <= set(WEEKDAYS) or not f["weekdays"]):
        raise RuleError(f"{what}.weekdays must be a list like [\"mon\", \"fri\"].")
    for k in ("time_from", "time_to"):
        if k in f:
            _hhmm(f[k], f"{what}.{k}")
    if "dates" in f:
        if not isinstance(f["dates"], dict):
            raise RuleError(f"{what}.dates must be {{from, to}}.")
        for k in ("from", "to"):
            if f["dates"].get(k):
                _date(f["dates"][k], f"{what}.dates.{k}")
    for k in ("min_spend", "max_spend"):
        if k in f:
            _num(f[k], f"{what}.{k}", 0, 1000)
    if "min_units" in f:
        _num(f["min_units"], f"{what}.min_units", 1, 100, True)
    if "paid_with" in f and (not isinstance(f["paid_with"], list) or not set(f["paid_with"]) <= {"card", "cash", "tab", "other"}):
        raise RuleError(f"{what}.paid_with must be a list of card, cash, tab, other.")
    if "first_of_day" in f:
        _num(f["first_of_day"], f"{what}.first_of_day", 1, 500, True)
    for k in ("on_double_points", "student_first_of_day"):
        if k in f and not isinstance(f[k], bool):
            raise RuleError(f"{what}.{k} must be true or false.")


def _check_condition(c, depth=0):
    if depth > 8:
        raise RuleError("The conditions are nested too deep.")
    if not isinstance(c, dict):
        raise RuleError("Each condition must be an object.")
    if "all" in c or "any" in c:
        parts = c.get("all", c.get("any"))
        if not isinstance(parts, list) or not parts:
            raise RuleError("all/any need a list of conditions.")
        for p in parts:
            _check_condition(p, depth + 1)
    elif "at_least" in c:
        _num(c["at_least"], "at_least", 1, 50, True)
        if not isinstance(c.get("of"), list) or len(c["of"]) < c["at_least"]:
            raise RuleError("at_least needs an \"of\" list at least that long.")
        for p in c["of"]:
            _check_condition(p, depth + 1)
    elif "not" in c:
        _check_condition(c["not"], depth + 1)
    elif c.get("metric") == "tried_all":
        _names(c.get("items"), "tried_all.items")
    elif "metric" in c:
        if c["metric"] not in METRICS:
            raise RuleError(f"Unknown metric {c['metric']!r}.")
        if c.get("op", ">=") not in OPS:
            raise RuleError("op must be one of >= > <= < ==.")
        _num(c.get("value"), f"{c['metric']} value", 0, 1_000_000)
        _check_filter(c.get("where"), f"{c['metric']}.where")
    else:
        raise RuleError("A condition needs all, any, at_least, not or metric.")


def validate(conn, rule):
    """Raises RuleError with a plain message if the rule isn't usable; returns it tidied."""
    if not isinstance(rule, dict):
        raise RuleError("A rule must be an object.")
    if len(json.dumps(rule)) > 8000:
        raise RuleError("That rule is too big.")
    kind = rule.get("kind")
    if kind not in ("badge", "event"):
        raise RuleError("kind must be badge or event.")
    name = " ".join(str(rule.get("name") or "").split())[:40]
    summary = " ".join(str(rule.get("summary") or "").split())[:200]
    if not name or not summary:
        raise RuleError("A rule needs a name and a one-sentence summary.")
    rule = dict(rule, name=name, summary=summary)
    _check_window(rule.get("window"), conn)
    _check_filter(rule.get("where"))
    if kind == "badge":
        if "need" not in rule:
            raise RuleError("A badge needs a \"need\" condition.")
        _check_condition(rule["need"])
        rule["points"] = _num(rule.get("points"), "points", 1, 5000, True)
        rule["repeat"] = rule.get("repeat") or "once"
        if rule["repeat"] not in REPEATS:
            raise RuleError("repeat must be once, day, week, month or term.")
        if rule["repeat"] == "term" and not terms(conn):
            raise RuleError("Add term dates in Settings first (Settings, Term dates).")
        if "icon" in rule and (not isinstance(rule["icon"], str) or len(rule["icon"]) > 8):
            rule.pop("icon")
    else:
        b = rule.get("bonus")
        if not isinstance(b, dict) or b.get("type") not in ("multiplier", "points"):
            raise RuleError("An event needs a bonus: multiplier or points.")
        if b["type"] == "multiplier":
            _num(b.get("value"), "bonus.value", 1.1, 10)
            if b.get("on", "matching_items") not in ("matching_items", "whole_purchase"):
                raise RuleError("bonus.on must be matching_items or whole_purchase.")
        else:
            _num(b.get("points"), "bonus.points", 1, 2000, True)
        if "every_nth" in rule:
            _num(rule["every_nth"], "every_nth", 2, 100, True)
        if "limit" in rule:
            lim = rule["limit"]
            if not isinstance(lim, dict) or lim.get("per") not in ("day", "week", "month", "term", "ever"):
                raise RuleError("limit.per must be day, week, month, term or ever.")
            _num(lim.get("times"), "limit.times", 1, 1000, True)
    return rule


# ---- terms ---------------------------------------------------------------------------------------

def terms(conn):
    """[(start, end, name)] from Settings, Term dates (same format as closed days)."""
    try:
        return [tuple(t) for t in json.loads(get_setting(conn, "terms", "[]") or "[]")]
    except ValueError:
        return []


def _term_of(conn, d, name=None):
    for a, b, label in terms(conn):
        if (name is None and a <= d.isoformat() <= b) or (name and label.lower() == name.lower()):
            return date.fromisoformat(a), date.fromisoformat(b), label
    return None


# ---- purchases -------------------------------------------------------------------------------------

def _local(ts):
    return logic.parse_ts(ts).astimezone(config.TZ)


def _tender(t):
    t = (t or "").lower()
    return "card" if "card" in t else "cash" if "cash" in t else "other"


_cache = {"key": None, "rows": []}


def purchases(conn, cid=None, since=None):
    """Checkouts as plain dicts with Sydney times. For one student, or everyone (for first_of_day).
    Everyone's list is worked out once and kept until a payment comes in."""
    key = tuple(conn.execute("SELECT COUNT(*), MAX(processed_at), MAX(rowid) FROM square_payments").fetchone()) + (
        since, conn.execute("PRAGMA database_list").fetchone()[2])
    if _cache["key"] != key:
        _cache["rows"], _cache["key"] = _all_purchases(conn, since), key
    rows = [dict(p) for p in _cache["rows"]]
    if cid is not None:
        rows = [p for p in rows if p["customer_id"] == cid]
        seen = set()
        for p in rows:
            p["student_first"] = p["day"] not in seen
            seen.add(p["day"])
    return rows


def _all_purchases(conn, since=None):
    rows = []
    for c in stats.checkouts(conn, since or "", "9999"):
        if not (c["items"] or c["sold_cents"]):
            continue
        at = _local(c["at"])
        tenders = {_tender(t) for t in c["tenders"]}
        if c["tab_cents"]:
            tenders.add("tab")
        rows.append({"key": c["key"], "at": at, "day": at.date(), "customer_id": c["customer_id"], "items": c["items"],
                     "cents": c["sold_cents"], "tenders": tenders})
    n = {}
    for p in rows:  # position in the day, for first_of_day
        n[p["day"]] = n.get(p["day"], 0) + 1
        p["nth_today"] = n[p["day"]]
    return rows


def _matches_item(name, wanted):
    n = (name or "").lower()
    return any(w in n for w in wanted)


def _item_ok(it, f):
    if f.get("items") and not _matches_item(it["name"], [x.lower() for x in f["items"]]):
        return False
    if f.get("exclude_items") and _matches_item(it["name"], [x.lower() for x in f["exclude_items"]]):
        return False
    return True


def _double_days(conn):
    return [(e["start_date"], e["end_date"]) for e in conn.execute("SELECT start_date, end_date FROM points_events")]


def _purchase_ok(p, f, ctx):
    if not f:
        return True
    items = [it for it in p["items"] if _item_ok(it, f)]
    if (f.get("items") or f.get("exclude_items")) and not items:
        return False
    if f.get("weekdays") and WEEKDAYS[p["at"].weekday()] not in f["weekdays"]:
        return False
    mins = p["at"].hour * 60 + p["at"].minute
    if f.get("time_from") and mins < _hhmm(f["time_from"], ""):
        return False
    if f.get("time_to") and mins > _hhmm(f["time_to"], ""):
        return False
    d = f.get("dates") or {}
    if d.get("from") and p["day"] < date.fromisoformat(d["from"]):
        return False
    if d.get("to") and p["day"] > date.fromisoformat(d["to"]):
        return False
    if "min_spend" in f and p["cents"] < f["min_spend"] * 100:
        return False
    if "max_spend" in f and p["cents"] > f["max_spend"] * 100:
        return False
    if "min_units" in f and sum(it["qty"] for it in items) < f["min_units"]:
        return False
    if f.get("paid_with") and not (p["tenders"] & set(f["paid_with"])):
        return False
    if "on_double_points" in f:
        on = any(a <= p["day"].isoformat() <= b for a, b in ctx["doubles"])
        if on != f["on_double_points"]:
            return False
    if f.get("first_of_day") and p["nth_today"] > f["first_of_day"]:
        return False
    if f.get("student_first_of_day") and not p.get("student_first"):
        return False
    return True


# ---- windows and periods -----------------------------------------------------------------------------

def _week_start(d):
    return d - timedelta(days=d.weekday())


def _period(conn, kind, d):
    """(start, end) dates of the day/week/month/term/year containing d, or None (no term)."""
    if kind == "day":
        return d, d
    if kind == "week":
        return _week_start(d), _week_start(d) + timedelta(days=6)
    if kind == "month":
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return d.replace(day=1), nxt - timedelta(days=1)
    if kind == "year":
        return d.replace(month=1, day=1), d.replace(month=12, day=31)
    if kind == "term":
        t = _term_of(conn, d)
        return (t[0], t[1]) if t else None
    return None


def window_range(conn, rule, today=None):
    """(first, last) dates the rule counts purchases in. last None = open-ended."""
    today = today or stats.today()
    created = _local(rule["created_at"]).date() if rule.get("created_at") else today
    w = rule.get("window") or {}
    if "from" in w or "to" in w:
        a = date.fromisoformat(w["from"]) if w.get("from") else None
        b = date.fromisoformat(w["to"]) if w.get("to") else None
    elif "term" in w:
        t = _term_of(conn, today) if w["term"] == "current" else _term_of(conn, today, w["term"])
        if w["term"] == "current" and rule.get("created_at"):  # "current" means the term it was made in
            t = _term_of(conn, created) or t
        a, b = (t[0], t[1]) if t else (today + timedelta(days=1), today)  # no term: nothing counts
    elif "this" in w:
        r = _period(conn, w["this"], today)
        a, b = r if r else (today + timedelta(days=1), today)
    elif "last_days" in w:
        a, b = today - timedelta(days=w["last_days"] - 1), today
    else:
        a, b = None, None
    if rule["kind"] == "event" or rule.get("count_past") is False:
        a = max(a, created) if a else created  # events never pay for purchases before they existed
    return a, b


def _in(d, a, b):
    return (a is None or d >= a) and (b is None or d <= b)


# ---- metrics -------------------------------------------------------------------------------------------

def _metric(conn, cid, m, ps, f, ctx, a, b):
    ps = [p for p in ps if _purchase_ok(p, f, ctx)]

    def items(p):
        return [it for it in p["items"] if _item_ok(it, f or {})]
    if m == "units":
        return sum(it["qty"] for p in ps for it in items(p))
    if m == "spend":
        return sum(it["cents"] for p in ps for it in items(p)) / 100
    if m == "purchases":
        return len(ps)
    if m == "days":
        return len({p["day"] for p in ps})
    if m == "weeks":
        return len({_week_start(p["day"]) for p in ps})
    if m == "distinct_items":
        return len({it["name"].lower() for p in ps for it in items(p)})
    if m == "max_units_day":
        per = {}
        for p in ps:
            per[p["day"]] = per.get(p["day"], 0) + sum(it["qty"] for it in items(p))
        return max(per.values(), default=0)
    if m == "max_purchases_day":
        per = {}
        for p in ps:
            per[p["day"]] = per.get(p["day"], 0) + 1
        return max(per.values(), default=0)
    if m == "streak":
        from .badges import streaks
        trading = {d for d in ctx["trading"] if _in(d, a, b)}
        return streaks({p["day"] for p in ps}, trading, stats.today())[1]
    lo = datetime.combine(a, time(), config.TZ).astimezone(timezone.utc).isoformat() if a else ""
    hi = datetime.combine(b + timedelta(days=1), time(), config.TZ).astimezone(timezone.utc).isoformat() if b else "9999"
    if m == "tabs_paid_on_time":
        return sum(1 for ch in logic.tab_ledger(conn, cid)["charges"]
                   if ch["unpaid_cents"] == 0 and ch["net_cents"] > 0 and not ch["paid_late"] and lo <= ch["charged_at"] < hi)
    if m == "rewards_redeemed":
        return conn.execute("SELECT COUNT(*) FROM redemptions WHERE customer_id = ? AND undone_at IS NULL AND created_at >= ? "
                            "AND created_at < ?", (cid, lo, hi)).fetchone()[0]
    if m == "points_earned":
        return conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE customer_id = ? AND reason = 'purchase' "
                            "AND created_at >= ? AND created_at < ?", (cid, lo, hi)).fetchone()[0]
    return 0


def _holds(conn, cid, c, ps, base, ctx, a, b):
    if "all" in c:
        return all(_holds(conn, cid, x, ps, base, ctx, a, b) for x in c["all"])
    if "any" in c:
        return any(_holds(conn, cid, x, ps, base, ctx, a, b) for x in c["any"])
    if "at_least" in c:
        return sum(_holds(conn, cid, x, ps, base, ctx, a, b) for x in c["of"]) >= c["at_least"]
    if "not" in c:
        return not _holds(conn, cid, c["not"], ps, base, ctx, a, b)
    if c["metric"] == "tried_all":
        got = {it["name"].lower() for p in ps if _purchase_ok(p, base, ctx) for it in p["items"]}
        return all(any(w.lower() in g for g in got) for w in c["items"])
    f = dict(base or {})
    for k, v in (c.get("where") or {}).items():  # the condition's own filter narrows the rule's
        f[k] = v
    return OPS[c.get("op", ">=")](_metric(conn, cid, c["metric"], ps, f, ctx, a, b), c["value"])


def progress(conn, cid, rule, ps=None, ctx=None):
    """For a single-metric badge: (have, need) so the student site can show a bar. Else None."""
    c = rule.get("need") or {}
    if "metric" not in c or c["metric"] == "tried_all" or c.get("op", ">=") not in (">=", ">"):
        return None
    ctx = ctx or _ctx(conn)
    a, b = window_range(conn, rule)
    ps = [p for p in (ps if ps is not None else purchases(conn, cid)) if _in(p["day"], a, b)]
    f = dict(rule.get("where") or {}, **(c.get("where") or {}))
    return _metric(conn, cid, c["metric"], ps, f, ctx, a, b), c["value"]


def _ctx(conn):
    from .badges import trading_days
    return {"doubles": _double_days(conn), "trading": trading_days(conn)}


# ---- who gets what -----------------------------------------------------------------------------------

def active(conn, kind=None):
    rows = conn.execute("SELECT * FROM rules WHERE deleted_at IS NULL AND paused_at IS NULL ORDER BY id").fetchall()
    out = []
    for r in rows:
        rule = json.loads(r["spec"])
        rule.update(id=r["id"], created_at=r["created_at"])
        if kind is None or rule["kind"] == kind:
            out.append(rule)
    return out


def grants(conn, cid, rule, ps=None, ctx=None, today=None):
    """[(rule_key, points, note)] this student has earned from this rule so far."""
    ctx = ctx or _ctx(conn)
    ps = ps if ps is not None else purchases(conn, cid)
    a, b = window_range(conn, rule, today)
    inside = [p for p in ps if _in(p["day"], a, b) and _purchase_ok(p, rule.get("where"), ctx)]
    out = []
    if rule["kind"] == "badge":
        rep = rule.get("repeat", "once")
        if rep == "once":
            if _holds(conn, cid, rule["need"], [p for p in ps if _in(p["day"], a, b)], rule.get("where"), ctx, a, b):
                out.append((f"rule:{rule['id']}", rule["points"], rule["name"]))
        else:
            periods = sorted({_period(conn, rep, p["day"]) for p in inside} - {None})
            for pa, pb in periods:
                pa2, pb2 = max(pa, a) if a else pa, min(pb, b) if b else pb
                sub = [p for p in ps if _in(p["day"], pa2, pb2)]
                if _holds(conn, cid, rule["need"], sub, rule.get("where"), ctx, pa2, pb2):
                    out.append((f"rule:{rule['id']}:{pa.isoformat()}", rule["points"], rule["name"]))
        return out
    # event: a bonus per qualifying purchase
    bonus, nth, lim = rule["bonus"], rule.get("every_nth"), rule.get("limit")
    used = {}
    for i, p in enumerate(inside, 1):
        if nth and i % nth:
            continue
        if lim:
            per = "ever" if lim["per"] == "ever" else _period(conn, lim["per"], p["day"])
            if used.get(per, 0) >= lim["times"]:
                continue
        if bonus["type"] == "points":
            pts = bonus["points"]
        else:
            base = (sum(it["cents"] for it in p["items"] if _item_ok(it, rule.get("where") or {}))
                    if bonus.get("on", "matching_items") == "matching_items" else p["cents"])
            pts = round(base * (bonus["value"] - 1))
        if pts <= 0:
            continue
        if lim:
            used[per] = used.get(per, 0) + 1
        out.append((f"rule:{rule['id']}:{p['key']}", pts, rule["name"]))
    return out


def award(conn, cid):
    """Pay every rule grant this student has earned and not been paid. The caller commits."""
    rules = active(conn)
    if not rules:
        return []
    paid = {r[0] for r in conn.execute("SELECT rule_key FROM points_ledger WHERE customer_id = ? AND rule_key IS NOT NULL", (cid,))}
    ps, ctx, new = purchases(conn, cid), _ctx(conn), []
    for rule in rules:
        for key, pts, note in grants(conn, cid, rule, ps, ctx):
            if key not in paid:
                conn.execute("INSERT INTO points_ledger(customer_id, delta, reason, note, rule_key, created_at) "
                             "VALUES (?, ?, 'rule', ?, ?, ?)", (cid, pts, note, key, now_iso()))
                new.append(note)
    return new


def preview(conn, rule):
    """Who would have it (badge) or how many bonus points it would pay (event) right now."""
    rule = dict(validate(conn, rule), id="preview", created_at=rule.get("created_at") or now_iso())
    ctx, everyone = _ctx(conn), purchases(conn)
    by = {}
    for p in everyone:
        if p["customer_id"]:
            by.setdefault(p["customer_id"], []).append(p)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM customers WHERE merged_into IS NULL AND archived_at IS NULL")}
    who, total = [], 0
    for cid, ps in by.items():
        if cid not in names:
            continue
        seen = set()
        for p in ps:
            p["student_first"] = p["day"] not in seen
            seen.add(p["day"])
        g = grants(conn, cid, rule, ps, ctx)
        if g:
            who.append({"name": names[cid], "points": sum(x[1] for x in g), "times": len(g)})
            total += sum(x[1] for x in g)
    who.sort(key=lambda w: -w["points"])
    return {"rule": rule, "students": len(who), "points_total": total, "top": who[:10],
            "window": [str(x) if x else None for x in window_range(conn, rule)]}


# ---- storing -----------------------------------------------------------------------------------------

def create(conn, rule, staff, source="Ask"):
    rule = validate(conn, rule)
    for k in ("id", "created_at"):
        rule.pop(k, None)
    cur = conn.execute("INSERT INTO rules(kind, name, spec, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                       (rule["kind"], rule["name"], json.dumps(rule), staff, now_iso()))
    audit(conn, staff, "rule_add", None, f"{rule['kind']} {rule['name']}: {rule['summary']} (via {source})")
    return cur.lastrowid


def update(conn, rule_id, rule, staff, source="Ask"):
    row = conn.execute("SELECT * FROM rules WHERE id = ? AND deleted_at IS NULL", (rule_id,)).fetchone()
    if row is None:
        raise RuleError("That rule doesn't exist.")
    rule = validate(conn, rule)
    for k in ("id", "created_at"):
        rule.pop(k, None)
    conn.execute("UPDATE rules SET kind = ?, name = ?, spec = ? WHERE id = ?", (rule["kind"], rule["name"], json.dumps(rule), rule_id))
    audit(conn, staff, "rule_change", None, f"{rule['name']}: {rule['summary']} (via {source})")


def set_state(conn, rule_id, state, staff):
    col = {"pause": "paused_at = ?", "resume": "paused_at = NULL", "delete": "deleted_at = ?"}[state]
    args = (now_iso(), rule_id) if "?" in col else (rule_id,)
    conn.execute(f"UPDATE rules SET {col} WHERE id = ?", args)
    row = conn.execute("SELECT name FROM rules WHERE id = ?", (rule_id,)).fetchone()
    audit(conn, staff, f"rule_{state}", None, row["name"] if row else str(rule_id))


def listing(conn):
    out = []
    for r in conn.execute("SELECT * FROM rules WHERE deleted_at IS NULL ORDER BY paused_at IS NOT NULL, id DESC"):
        spec = json.loads(r["spec"])
        n = conn.execute("SELECT COUNT(DISTINCT customer_id), COALESCE(SUM(delta), 0) FROM points_ledger WHERE rule_key LIKE ?",
                         (f"rule:{r['id']}%",)).fetchone()
        shown = {k: v for k, v in spec.items() if k != "icon"}  # no emojis on the staff site
        out.append({"id": r["id"], "kind": r["kind"], "name": r["name"], "summary": spec.get("summary", ""), "spec": shown,
                    "paused": bool(r["paused_at"]), "created_at": r["created_at"], "created_by": r["created_by"],
                    "students": n[0], "points": n[1]})
    return out


# ---- the student site -------------------------------------------------------------------------------------

def student_badges(conn, cid):
    """Custom badges shaped like badges.for_customer's, for the student's list."""
    out, ps, ctx = [], None, None
    got = {r[0] for r in conn.execute("SELECT rule_key FROM points_ledger WHERE customer_id = ? AND rule_key IS NOT NULL", (cid,))}
    for rule in active(conn, "badge"):
        earned = any(k == f"rule:{rule['id']}" or k.startswith(f"rule:{rule['id']}:") for k in got)
        if rule.get("repeat", "once") != "once":  # repeating: "earned" means this period
            pr = _period(conn, rule["repeat"], stats.today())
            earned = bool(pr) and f"rule:{rule['id']}:{pr[0].isoformat()}" in got
        b = {"icon": rule.get("icon") or "\u2b50",  # a star (the staff site's source stays emoji-free) "name": rule["name"], "how": rule["summary"], "earned": earned,
             "points": rule["points"], "progress": 0, "count": ""}
        if not earned:
            if ps is None:
                ps, ctx = purchases(conn, cid), _ctx(conn)
            pg = progress(conn, cid, rule, ps, ctx)
            if pg:
                have, need = pg
                b["progress"] = min(100, round(100 * have / need)) if need else 100
                b["count"] = f"{min(have, need):,.0f} / {need:,.0f}"
        out.append(b)
    return out


def student_events(conn):
    """Custom events on now, for the student site's "What's on"."""
    today = stats.today()
    out = []
    for rule in active(conn, "event"):
        a, b = window_range(conn, rule, today)
        if _in(today, a, b) or (a and a > today and (a - today).days <= 14):
            out.append({"name": rule["name"], "summary": rule["summary"], "soon": bool(a and a > today), "start": a})
    return out
