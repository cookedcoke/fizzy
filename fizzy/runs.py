"""Clue-chain hunts: a timed run through shuffled stops (challenges.py calls in here for a hunt with "order": "shuffled").

How a run goes. The start step (the poster at our stall) starts the player's clock and deals them the other
stops in a random order. They are only ever told the next stop's clue (its hint); scanning any other stop
says "Not this one" and counts for nothing. Scans that count must be a few seconds apart, so photos of
posters passed around don't make it trivial. The last stop finishes the run: the time is finish minus start.
The fastest few are decided when the hunt closes (a second prize), the rest just get the usual prize.

A player is a phone (a guest id in its session) or a signed-in student. A run is kept in hunt_runs; the
scans themselves go in the same tables as every other hunt (challenge_scans / guest_scans), so claiming,
merging a phone onto an account and the prize all work as they always have.

The spec keys (all optional, checked in validate_extras):
  "order": "shuffled"                         the clue chain; one step is {"start": true, "scan": true}
  "hours": {"open", "last_start", "close"}    "HH:MM" each, school time, on the days the window allows
  "prize_top": {"count": 10, "text": "..."}   a second prize for the fastest few, decided at close
  "scan_gap": 20                              seconds between scans that count
"""
import json
import random
import re
from datetime import datetime, time, timezone

from . import config
from .db import audit

SCAN_GAP = 20
HM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _now():
    """School time. One place to ask, so tests can set the clock."""
    return datetime.now(config.TZ)


def ordered(rule):
    return rule.get("order") == "shuffled"


# ---- checking the spec ------------------------------------------------------------------------------

def validate_extras(rule, steps):
    """The clue-chain keys of a challenge, tidied (raises RuleError). `steps` are already cleaned."""
    from .rules import RuleError, _num
    out = {}
    order = rule.get("order")
    if order not in (None, "", "shuffled"):
        raise RuleError("order must be \"shuffled\" (or left out).")
    if order == "shuffled":
        out["order"] = "shuffled"
        if any(not s.get("scan") for s in steps):
            raise RuleError("A shuffled hunt can only have scan steps.")
        starts = [s for s in steps if s.get("start")]
        if len(starts) != 1:
            raise RuleError("A shuffled hunt needs exactly one start step (the poster at the stall that starts the clock).")
        if len(steps) < 2:
            raise RuleError("A shuffled hunt needs the start and at least one more stop.")
    elif any(s.get("start") for s in steps):
        raise RuleError("A start step only works in a shuffled hunt.")
    hours = rule.get("hours")
    if hours:
        if not isinstance(hours, dict):
            raise RuleError("hours must be an object with open, last_start and close as HH:MM.")
        h = {}
        for k in ("open", "last_start", "close"):
            v = str(hours.get(k) or "").strip()
            if not v:
                continue
            m = HM.match(v)
            if not m:
                raise RuleError(f"hours.{k} must look like 12:35.")
            h[k] = f"{int(m[1]):02d}:{m[2]}"
        seq = [h[k] for k in ("open", "last_start", "close") if k in h]
        if seq != sorted(seq):
            raise RuleError("The times must go in order: open, then last start, then close.")
        if h:
            out["hours"] = h
    top = rule.get("prize_top")
    if top:
        if not isinstance(top, dict):
            raise RuleError("prize_top needs a count and some text.")
        text = " ".join(str(top.get("text") or "").split())[:80]
        count = _num(top.get("count"), "prize_top.count", 1, 100, True)
        if text:
            out["prize_top"] = {"count": count, "text": text}
        elif top.get("text") is None or top.get("text") == "":
            out["prize_top"] = {"count": count, "text": ""}   # the coupon isn't decided yet: still ranks
    if rule.get("scan_gap") not in (None, ""):
        out["scan_gap"] = _num(rule["scan_gap"], "scan_gap", 0, 300, True)
    return out


def gap(rule):
    return int(rule.get("scan_gap", SCAN_GAP))


def _t(hm):
    h, m = hm.split(":")
    return time(int(h), int(m))


def show_time(hm):
    t = _t(hm)
    return f"{t.hour % 12 or 12}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def closed(rule):
    """The hunt's day is over: past its close time today, or past its last day."""
    h = rule.get("hours") or {}
    if h.get("close") and _now().time() > _t(h["close"]):
        return True
    return False


def gate(rule, starting, has_run=False):
    """A message if the time of day rules this scan out, else None. Date windows are checked by the caller."""
    h = rule.get("hours") or {}
    now = _now().time()
    name = rule["name"]
    if h.get("open") and now < _t(h["open"]):
        return f"{name} opens at {show_time(h['open'])} today."
    if h.get("close") and now > _t(h["close"]):
        return f"{name} has closed for today. Thanks for playing!"
    if starting and not has_run and h.get("last_start") and now > _t(h["last_start"]):
        return f"Too late to start {name}: starting closed at {show_time(h['last_start'])}."
    return None


# ---- who is playing -------------------------------------------------------------------------------------

def guest_who(guest):
    return ("g", guest)


def cust_who(cid):
    return ("c", cid)


def _key(who):
    return f"{who[0]}:{who[1]}"


def get_run(conn, rid, who):
    return conn.execute("SELECT * FROM hunt_runs WHERE rule_id = ? AND player = ?", (rid, _key(who))).fetchone()


def found(conn, who, rid):
    """Codes counted in this player's current run, in no order. Scans from before the run started (an
    earlier test, or the hunt before it was timed) don't count."""
    if who[0] == "c":
        rows = conn.execute("SELECT step_code, scanned_at FROM challenge_scans WHERE customer_id = ? AND rule_id = ?", (who[1], rid))
    else:
        rows = conn.execute("SELECT step_code, scanned_at FROM guest_scans WHERE guest = ? AND rule_id = ?", (who[1], rid))
    rows = rows.fetchall()
    run = get_run(conn, rid, who)
    if run is None:
        return {r[0] for r in rows}
    since = _aware(_parse(run["started_at"]))
    return {r[0] for r in rows if _aware(_parse(r[1])) >= since}


def _aware(t):
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _record(conn, who, rid, code, at):
    if who[0] == "c":
        conn.execute("INSERT INTO challenge_scans(customer_id, rule_id, step_code, scanned_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(customer_id, rule_id, step_code) DO UPDATE SET scanned_at = excluded.scanned_at",
                     (who[1], rid, code, at))
    else:
        conn.execute("INSERT INTO guest_scans(guest, rule_id, step_code, scanned_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(guest, rule_id, step_code) DO UPDATE SET scanned_at = excluded.scanned_at",
                     (who[1], rid, code, at))


def adopt(conn, guest, cid, rule_id=None):
    """A phone signed in (or staff claimed it): its run is the student's now, unless they have one already."""
    only, args = (" AND rule_id = ?", (rule_id,)) if rule_id is not None else ("", ())
    for r in conn.execute(f"SELECT * FROM hunt_runs WHERE player = ?{only}", (f"g:{guest}",) + args).fetchall():
        if conn.execute("SELECT 1 FROM hunt_runs WHERE rule_id = ? AND player = ?", (r["rule_id"], f"c:{cid}")).fetchone():
            continue
        conn.execute("UPDATE hunt_runs SET player = ?, customer_id = ? WHERE id = ?", (f"c:{cid}", cid, r["id"]))


# ---- the run ---------------------------------------------------------------------------------------------

def _steps_by_code(rule):
    return {s["code"]: s for s in rule["steps"] if s.get("code")}


def start_step(rule):
    return next(s for s in rule["steps"] if s.get("start"))


def clue_for(rule, code):
    s = _steps_by_code(rule).get(code) or {}
    return s.get("hint") or "Find the next poster"


def next_code(rule, run, got):
    """The stop this player should find now, or None when the order is used up."""
    for code in json.loads(run["order_json"]):
        if code not in got:
            return code
    return None


def fmt_ms(ms):
    s = max(0, int(ms)) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _parse(ts):
    return datetime.fromisoformat(ts)


def rank_of(conn, run):
    """1-based place among finished, not-removed runs; None if this one hasn't finished or was removed."""
    if run["finished_at"] is None or run["void_at"]:
        return None
    ahead = conn.execute("SELECT COUNT(*) FROM hunt_runs WHERE rule_id = ? AND finished_at IS NOT NULL AND void_at IS NULL AND "
                         "(elapsed_ms < ? OR (elapsed_ms = ? AND finished_at < ?))",
                         (run["rule_id"], run["elapsed_ms"], run["elapsed_ms"], run["finished_at"])).fetchone()[0]
    return ahead + 1


def board(conn, rid, limit=10):
    """The fastest finished runs, quickest first; a tie goes to whoever finished first."""
    return conn.execute("SELECT * FROM hunt_runs WHERE rule_id = ? AND finished_at IS NOT NULL AND void_at IS NULL "
                        "ORDER BY elapsed_ms, finished_at LIMIT ?", (rid, limit)).fetchall()


def scan(conn, rule, i, who, finish):
    """A scan of step i in a shuffled hunt. Returns (ok, message). The caller commits.
    finish(conn) is called when this scan finishes the run and gives back the sentence that says what they've won."""
    rid, name = rule["id"], rule["name"]
    step = rule["steps"][i]
    code = step["code"]
    run = get_run(conn, rid, who)
    late = gate(rule, bool(step.get("start")), run is not None)
    if late:
        return False, late
    now = _now()
    stamp = now.isoformat()
    total = len(rule["steps"])
    if step.get("start"):
        if run is None:
            order = [s["code"] for s in rule["steps"] if not s.get("start")]
            random.SystemRandom().shuffle(order)
            conn.execute("INSERT INTO hunt_runs(rule_id, player, customer_id, started_at, order_json, last_scan_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (rid, _key(who), who[1] if who[0] == "c" else None, stamp, json.dumps(order), stamp))
            _record(conn, who, rid, code, stamp)
            audit(conn, "student", "hunt_start", who[1] if who[0] == "c" else None, name)
            run = get_run(conn, rid, who)
            return True, f"Found it: {step['label']}. Your clock is running! 1 of {total} found. Your next clue: {clue_for(rule, order[0])}"
        if run["finished_at"]:
            return True, f"You've already finished {name}."
        got = found(conn, who, rid)
        return True, (f"You've already started: your clock has been running since you first scanned here. "
                      f"{len(got)} of {total} found. Your next clue: {clue_for(rule, next_code(rule, run, got))}")
    if run is None:
        return False, f"Scan the start code first (at {start_step(rule)['label']}), then come and find this."
    if run["finished_at"]:
        return True, f"You've already finished {name}."
    got = found(conn, who, rid)
    nxt = next_code(rule, run, got)
    label = step["label"]
    if code in got:
        return True, f"You've already found {label}. {len(got)} of {total} found. Your next clue: {clue_for(rule, nxt)}"
    if code != nxt:
        return False, f"Not this one - your clue is: {clue_for(rule, nxt)}"
    waited = (now - _parse(run["last_scan_at"])).total_seconds()
    if waited < gap(rule):
        n = int(gap(rule) - waited) + 1
        quip = random.choice(["are you teleporting?", "the poster isn't going anywhere.", "even Usain Bolt needs a breather.",
                              "slow down, speedrunner.", "save some legs for the next one."])
        return False, f"Hold on - {quip} Give it {n} more second{'s' if n != 1 else ''}, then scan again."
    _record(conn, who, rid, code, stamp)
    conn.execute("UPDATE hunt_runs SET last_scan_at = ? WHERE id = ?", (stamp, run["id"]))
    got.add(code)
    after = next_code(rule, run, got)
    if after is not None:
        return True, f"Found it: {label}. {len(got)} of {total} found. Your next clue: {clue_for(rule, after)}"
    ms = int((now - _parse(run["started_at"])).total_seconds() * 1000)
    conn.execute("UPDATE hunt_runs SET finished_at = ?, elapsed_ms = ? WHERE id = ?", (stamp, ms, run["id"]))
    audit(conn, "student", "hunt_finish", who[1] if who[0] == "c" else None, f"{name}: {fmt_ms(ms)}")
    return True, f"Found it: {label}. That's everything! {finish(conn)} Your time: {fmt_ms(ms)}."


def set_name(conn, who, nickname, klass):
    """Their nickname and class/year, on every run of theirs. Returns an error message or None."""
    nick = " ".join(str(nickname or "").split())[:20]
    cls = " ".join(str(klass or "").split())[:12]
    if len(nick) < 2:
        return "Pick a nickname (at least 2 letters)."
    if not cls:
        return "Add your class or year, so staff can find you if you win."
    cur = conn.execute("UPDATE hunt_runs SET nickname = ?, klass = ? WHERE player = ?", (nick, cls, _key(who)))
    return None if cur.rowcount else "Scan the start poster first."


# ---- what the apps draw -------------------------------------------------------------------------------------

def view(conn, rule, who, claim=None):
    """Steps, progress and a few extras for the player's card. Unfound stops are never named: only what's
    found, plus the current clue."""
    rid = rule["id"]
    total = len(rule["steps"])
    run = get_run(conn, rid, who)
    by = _steps_by_code(rule)
    start = start_step(rule)
    top = rule.get("prize_top") or {}
    if run is None:
        return {"steps": [{"label": start["label"], "detail": start.get("hint", ""), "done": False, "kind": "scan"}],
                "progress": {"done": 0, "total": total}, "run": None, "text": None}
    got = found(conn, who, rid)
    order = json.loads(run["order_json"])
    steps = [{"label": start["label"], "detail": "", "done": True, "kind": "scan"}]
    steps += [{"label": by[c]["label"], "detail": "", "done": True, "kind": "scan"} for c in order if c in got]
    nxt = next_code(rule, run, got)
    if nxt is not None:
        steps.append({"label": clue_for(rule, nxt), "detail": "Your next clue", "done": False, "kind": "scan"})
    done = len([c for c in order if c in got]) + 1
    r = rank_of(conn, run)
    info = {"nickname": run["nickname"], "klass": run["klass"], "needs_name": not run["nickname"],
            "started_ms": int(_parse(run["started_at"]).timestamp() * 1000), "finished": bool(run["finished_at"]),
            "elapsed_ms": run["elapsed_ms"], "elapsed": fmt_ms(run["elapsed_ms"]) if run["elapsed_ms"] is not None else None,
            "rank": r, "top": top.get("count"), "closed": closed(rule), "clue": clue_for(rule, nxt) if nxt else None}
    text = None
    if run["finished_at"]:
        text = f"Your time: {info['elapsed']}."
        if r:
            text += f" You're #{r} so far."
        if top.get("count"):
            text += f" The fastest {top['count']} are decided when the hunt closes."
    return {"steps": steps, "progress": {"done": done, "total": total}, "run": info, "text": text}


# ---- for staff and the stall screen -----------------------------------------------------------------------------

def board_rows(conn, rule, limit=10):
    return [{"rank": n, "name": r["nickname"] or "Anonymous", "time": fmt_ms(r["elapsed_ms"])}
            for n, r in enumerate(board(conn, rule["id"], limit), 1)]


def live_boards(conn):
    """For the stall screen: each shuffled hunt that's on, with its fastest times and how many are still running."""
    from . import challenges, rules
    out = []
    for rule in rules.active(conn, "challenge"):
        if not ordered(rule) or not challenges.is_on(conn, rule):
            continue
        running = conn.execute("SELECT COUNT(*) FROM hunt_runs WHERE rule_id = ? AND finished_at IS NULL AND void_at IS NULL", (rule["id"],)).fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM hunt_runs WHERE rule_id = ? AND finished_at IS NOT NULL AND void_at IS NULL", (rule["id"],)).fetchone()[0]
        top = (rule.get("prize_top") or {}).get("count") or 10
        out.append({"name": rule["name"], "rows": board_rows(conn, rule, top), "running": running, "finished": done,
                    "closed": closed(rule)})
    return out


def staff_rows(conn, rule):
    """The prize_top winners with what staff need to find them: class, claim code, whether the coupon's been given.
    Plus anyone sharing a nickname and class (the same person twice?)."""
    top = (rule.get("prize_top") or {}).get("count") or 10
    rows = []
    for n, r in enumerate(board(conn, rule["id"], top), 1):
        claim = conn.execute("SELECT claim, claimed_at FROM guest_finishes WHERE rule_id = ? AND (guest = ? OR customer_id = ?)",
                             (rule["id"], r["player"][2:], r["customer_id"] if r["customer_id"] else -1)).fetchone()
        who = None
        if r["customer_id"]:
            who = conn.execute("SELECT name FROM customers WHERE id = ?", (r["customer_id"],)).fetchone()
        rows.append({"run": r["id"], "rank": n, "name": r["nickname"] or "(no nickname)", "klass": r["klass"],
                     "time": fmt_ms(r["elapsed_ms"]), "claim": claim["claim"] if claim else "",
                     "claimed": bool(claim and claim["claimed_at"]), "account": who["name"] if who else "",
                     "given": r["top_given_at"], "given_by": r["top_given_by"]})
    seen = {}
    for r in conn.execute("SELECT nickname, klass FROM hunt_runs WHERE rule_id = ? AND void_at IS NULL AND nickname != ''", (rule["id"],)):
        k = (r["nickname"].casefold(), r["klass"].casefold())
        seen[k] = seen.get(k, 0) + 1
    dupes = sorted(f"{k[0]} ({k[1]})" for k, v in seen.items() if v > 1)
    return rows, dupes
