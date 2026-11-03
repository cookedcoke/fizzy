"""Challenges: the third kind of custom rule (rules.py), next to badges and events.

A challenge is a list of steps and a prize. A step is either a code to scan (a QR code stuck up
somewhere, for a scavenger hunt) or anything a badge can count ("buy 3 Chi Forest"). Finishing every
step wins the prize: points, something handed over at the counter (a free drink), or both.

Staff make them on Rewards (a hunt: one stop per line) or by asking Fizz (any mix of steps). They're
stored like every other rule, so pause, restart and delete work the same way.

Both student apps draw a challenge from plain data (card() below: a title, text, steps with ticks,
progress, the prize and a Scan button), so a new challenge, or a new mix of steps, never needs an
app update. A scan sends the code's text to POST /me/scan, and the server decides what it means.
"""
import json
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config, stats
from .db import audit, now_iso
from .web import back, get_conn, render, staff

router = APIRouter()

MAX_STEPS = 30
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
FLAG = "\U0001F3C1"   # the default icon on the student apps (the staff site's source stays emoji-free)


# ---- checking and storing --------------------------------------------------------------------------------

def validate(conn, rule):
    """Called from rules.validate once the name, summary, window and where are checked."""
    from .rules import RuleError, _check_condition, _num
    steps = rule.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RuleError("A challenge needs a list of steps.")
    if len(steps) > MAX_STEPS:
        raise RuleError(f"A challenge can have at most {MAX_STEPS} steps.")
    out = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            raise RuleError(f"Step {i} must be an object.")
        label = " ".join(str(s.get("label") or "").split())[:80]
        if not label:
            raise RuleError(f"Step {i} needs a label students see, e.g. \"The library\".")
        if s.get("scan") is True:
            step = {"label": label, "scan": True}
            hint = " ".join(str(s.get("hint") or "").split())[:120]
            if hint:
                step["hint"] = hint
            if isinstance(s.get("code"), str) and CODE_RE.match(s["code"]):
                step["code"] = s["code"]   # kept on an edit, so codes already printed still work
        elif "need" in s:
            _check_condition(s["need"])
            step = {"label": label, "need": s["need"]}
        else:
            raise RuleError(f"Step {i} needs \"scan\": true or a \"need\" condition.")
        out.append(step)
    given = [s["code"].upper() for s in out if s.get("code")]
    if len(given) != len(set(given)):
        raise RuleError("Two steps have the same code.")
    prize = rule.get("prize")
    if not isinstance(prize, dict):
        raise RuleError("A challenge needs a prize: points, text (something given at the counter), or both.")
    p = {}
    if prize.get("points"):
        p["points"] = _num(prize["points"], "prize.points", 1, 5000, True)
    text = " ".join(str(prize.get("text") or "").split())[:60]
    if text:
        p["text"] = text
    if not p:
        raise RuleError("The prize needs points, text (e.g. \"A free drink\"), or both.")
    rule = dict(rule, steps=out, prize=p)
    if "icon" in rule and (not isinstance(rule["icon"], str) or len(rule["icon"]) > 8):
        rule.pop("icon")
    return rule


def _label_key(label):
    return " ".join(str(label or "").split()).casefold()


def give_codes(rule, old=None):
    """Every scan step gets a secret code. An edit keeps the codes of steps already there, matched by
    label (Fizz sends steps back without their codes, and may add or take out steps anywhere), so a
    printed code, and who's found it, stays with its stop."""
    before = [s for s in (old or {}).get("steps", []) if s.get("scan") and s.get("code")]
    taken = {s["code"].upper() for s in rule["steps"] if s.get("scan") and s.get("code")}
    free = [s for s in before if s["code"].upper() not in taken]
    new_labels = {_label_key(s["label"]) for s in rule["steps"]}
    waiting = [s for s in rule["steps"] if s.get("scan") and not s.get("code")]
    for s in waiting:
        prev = next((p for p in free if _label_key(p["label"]) == _label_key(s["label"])), None)
        if prev:
            s["code"] = prev["code"]
            free.remove(prev)
    # A stop renamed where it stood (a typo fixed) keeps its code too: same place, and its old label is gone.
    old_steps = (old or {}).get("steps", [])
    for i, s in enumerate(rule["steps"]):
        if s.get("scan") and not s.get("code") and i < len(old_steps):
            prev = old_steps[i]
            if prev in free and _label_key(prev["label"]) not in new_labels:
                s["code"] = prev["code"]
                free.remove(prev)
    for s in rule["steps"]:
        if s.get("scan") and not s.get("code"):
            s["code"] = secrets.token_urlsafe(9)
    return rule


def code_url(code, base=None):
    """What a code's QR holds: the short link host (the iPhone camera opens the App Clip or the app
    there, and a browser is sent on to the student site), else the student site, so any phone camera opens it."""
    if config.LINK_HOST:
        return f"https://{config.LINK_HOST}/{code}"
    if config.PUBLIC_HOST:
        return f"https://{config.PUBLIC_HOST}/c/{code}"
    return f"{(base or f'https://{config.HOST}').rstrip('/')}/me/c/{code}"


def token_of(text):
    """The code from what a scanner read: the last part of a link (ours, .../c/<code>, or any other,
    like posters printed for a hunt), or the bare code."""
    t = (text or "").strip()
    if "://" in t or t.startswith("/"):
        t = re.split(r"[?#]", t, maxsplit=1)[0].rstrip("/").rsplit("/", 1)[-1]
    return t if CODE_RE.match(t) else None


# ---- progress ------------------------------------------------------------------------------------

def _window(conn, rule):
    from . import rules
    return rules.window_range(conn, rule)


def is_on(conn, rule, today=None):
    from .rules import _in
    a, b = _window(conn, rule)
    return _in(today or stats.today(), a, b)


def done_steps(conn, cid, rule, ps=None, ctx=None):
    """A tick for each step."""
    from . import rules
    scanned = {r[0] for r in conn.execute("SELECT step_code FROM challenge_scans WHERE customer_id = ? AND rule_id = ?",
                                          (cid, rule["id"]))}
    out, inside = [], None
    for s in rule["steps"]:
        if s.get("scan"):
            out.append(s.get("code") in scanned)
            continue
        if inside is None:
            ctx = ctx or rules._ctx(conn)
            a, b = _window(conn, rule)
            inside = [p for p in (ps if ps is not None else rules.purchases(conn, cid)) if rules._in(p["day"], a, b)]
        out.append(rules._holds(conn, cid, s["need"], inside, rule.get("where"), ctx, *_window(conn, rule)))
    return out


def _won(conn, cid, rule_id):
    return conn.execute("SELECT * FROM prizes WHERE customer_id = ? AND rule_id = ?", (cid, rule_id)).fetchone()


def award(conn, cid, ps=None, ctx=None):
    """Give the prize for every challenge this student has finished. Each is won once. The caller commits."""
    from . import rules
    new = []
    for rule in rules.active(conn, "challenge"):
        if _won(conn, cid, rule["id"]) or not all(done_steps(conn, cid, rule, ps, ctx)):
            continue
        prize = rule["prize"]
        conn.execute("INSERT OR IGNORE INTO prizes(customer_id, rule_id, text, points, won_at) VALUES (?, ?, ?, ?, ?)",
                     (cid, rule["id"], prize.get("text") or "", prize.get("points") or 0, now_iso()))
        if prize.get("points"):
            conn.execute("INSERT OR IGNORE INTO points_ledger(customer_id, delta, reason, note, rule_key, created_at) "
                         "VALUES (?, ?, 'rule', ?, ?, ?)", (cid, prize["points"], f"Challenge: {rule['name']}",
                                                            f"rule:{rule['id']}", now_iso()))
        audit(conn, "student", "challenge_won", cid, rule["name"])
        new.append(rule["name"])
    return new


def find(conn, code):
    """(rule, step index) for a code, among challenges that aren't paused or deleted."""
    from . import rules
    for rule in rules.active(conn, "challenge"):
        for i, s in enumerate(rule["steps"]):
            if s.get("scan") and s.get("code") and secrets.compare_digest(s["code"], code):
                return rule, i
    # A printed code read as all capitals (QR codes often are): match ignoring case.
    for rule in rules.active(conn, "challenge"):
        for i, s in enumerate(rule["steps"]):
            if s.get("scan") and s.get("code") and secrets.compare_digest(s["code"].upper(), code.upper()):
                return rule, i
    return None, None


def scan(conn, cid, text):
    """A student scanned something. Returns (ok, message). The caller commits."""
    code = token_of(text)
    if "/q/" in (text or ""):
        return False, "That's a sign-in code. You're already signed in."
    rule, i = find(conn, code) if code else (None, None)
    if rule is None:
        return False, "That code isn't part of a challenge that's on."
    a, b = _window(conn, rule)
    today = stats.today()
    if a and today < a:
        return False, f"{rule['name']} starts on {a.strftime('%A %-d %b')}. Come back then."
    if b and today > b:
        return False, f"{rule['name']} has finished."
    if _won(conn, cid, rule["id"]):
        return True, f"You've already finished {rule['name']}."
    added = conn.execute("INSERT OR IGNORE INTO challenge_scans(customer_id, rule_id, step_code, scanned_at) VALUES (?, ?, ?, ?)",
                         (cid, rule["id"], rule["steps"][i]["code"], now_iso())).rowcount
    label = rule["steps"][i]["label"]
    if added:
        audit(conn, "student", "challenge_scan", cid, f"{rule['name']}: {label}")
    if award(conn, cid):
        return True, f"Found it: {label}. That's everything - you've finished {rule['name']}! {_collect(rule['prize'])}"
    ticks = done_steps(conn, cid, rule)
    left = f"{sum(ticks)} of {len(ticks)} done."
    return True, (f"Found it: {label}. {left}" if added else f"You've already found {label}. {left}")


def _collect(prize):
    if prize.get("text") and prize.get("points"):
        return f"Show this at the counter for {prize['text'].lower()}, and you've got {prize['points']:,} points."
    if prize.get("text"):
        return f"Show this at the counter for {prize['text'].lower()}."
    return f"You've got {prize['points']:,} points."


def prize_line(prize):
    parts = [prize["text"]] if prize.get("text") else []
    if prize.get("points"):
        parts.append(f"{prize['points']:,} points")
    return " + ".join(parts)


# ---- for the student apps ----------------------------------------------------------------------------

def card(conn, cid, rule, ps=None, ctx=None):
    """One challenge, as plain data either student app can draw without knowing what kind of thing it is."""
    today = stats.today()
    a, b = _window(conn, rule)
    soon = bool(a and a > today)
    won = _won(conn, cid, rule["id"])
    ticks = [False] * len(rule["steps"]) if soon else done_steps(conn, cid, rule, ps, ctx)
    if won:
        ticks = [True] * len(ticks)
    when = (f"Starts {a.strftime('%a %-d %b')}" if soon else
            f"Until {b.strftime('%a %-d %b')}" if b else "")
    if won:
        status = ("Prize collected." if won["given_at"] or not won["text"] else
                  f"Finished! Show this at the counter for {won['text'].lower()}.")
    else:
        status = ""
    steps = []
    for s, t in zip(rule["steps"], ticks):
        steps.append({"label": s["label"], "detail": s.get("hint", ""), "done": t,
                      "kind": "scan" if s.get("scan") else "buy"})
    can_scan = (not won and not soon and any(s.get("scan") and not t for s, t in zip(rule["steps"], ticks)))
    return {
        "id": f"challenge:{rule['id']}", "kicker": "Challenge", "title": rule["name"], "text": rule["summary"],
        "icon": rule.get("icon") or FLAG, "when": when, "steps": steps,
        "progress": {"done": sum(ticks), "total": len(ticks)}, "prize": prize_line(rule["prize"]),
        "done": bool(won), "status": status,
        "actions": [{"kind": "scan", "label": "Scan a code"}] if can_scan else [],
    }


def cards(conn, cid):
    """Challenges that are on (or start in the next two weeks), for this student. Finished ones stay
    while they're on, and after that until the prize is handed over, so the student can show it at the counter."""
    from . import rules
    today = stats.today()
    out, ps, ctx = [], None, None
    for rule in rules.active(conn, "challenge"):
        a, b = _window(conn, rule)
        if b and b < today:
            won = _won(conn, cid, rule["id"])
            if not (won and won["text"] and not won["given_at"]):
                continue
        if a and a > today + timedelta(days=14):
            continue
        if ps is None:
            ps, ctx = rules.purchases(conn, cid), rules._ctx(conn)
        out.append(card(conn, cid, rule, ps, ctx))
    out.sort(key=lambda c: (c["when"].startswith("Starts"), c["done"]))
    return out


# ---- playing without signing in ----------------------------------------------------------------------
# A phone camera opens a code's link without anyone signed in (a new student, or one with no passcode
# yet). Their scans are kept against that phone (a random id in its session). Finishing every scan step
# gives a short claim code; at the stall, staff type it on the student's page (adding them first if
# they're new) and the prize is theirs. Signing in on the student site in that browser moves its progress
# onto their account, once (the App Clip keeps its own, so it shows the claim code instead).

CLAIM_ALPHABET = "ACDEFGHJKMNPQRTUVWXY3479"   # nothing that looks like something else


def _guest_steps(conn, guest, rid):
    """The codes this phone has found for a challenge."""
    return {r[0] for r in conn.execute("SELECT step_code FROM guest_scans WHERE guest = ? AND rule_id = ?", (guest, rid))}


def guest_scan(conn, guest, text):
    """(ok, message, rule) for a code opened with no one signed in. The caller commits."""
    code = token_of(text)
    rule, i = find(conn, code) if code else (None, None)
    if rule is None:
        return False, "That code isn't part of a challenge that's on.", None
    a, b = _window(conn, rule)
    today = stats.today()
    if a and today < a:
        return False, f"{rule['name']} starts on {a.strftime('%A %-d %b')}. Come back then.", rule
    if b and today > b:
        return False, f"{rule['name']} has finished.", rule
    added = conn.execute("INSERT OR IGNORE INTO guest_scans(guest, rule_id, step_code, scanned_at) VALUES (?, ?, ?, ?)",
                         (guest, rule["id"], rule["steps"][i]["code"], now_iso())).rowcount
    label = rule["steps"][i]["label"]
    done = _guest_steps(conn, guest, rule["id"])
    scans = [s["code"] for s in rule["steps"] if s.get("scan")]
    if all(c in done for c in scans) and len(scans) == len(rule["steps"]):
        claim = _claim_for(conn, guest, rule["id"])
        return True, f"Found it: {label}. That's everything! Show your claim code {claim} at the stall.", rule
    left = f"{sum(c in done for c in scans)} of {len(rule['steps'])} found."
    return True, (f"Found it: {label}. {left}" if added else f"You've already found {label}. {left}"), rule


def _claim_for(conn, guest, rid):
    row = conn.execute("SELECT claim FROM guest_finishes WHERE guest = ? AND rule_id = ?", (guest, rid)).fetchone()
    if row:
        return row[0]
    while True:
        claim = "".join(secrets.choice(CLAIM_ALPHABET) for _ in range(5))
        if not conn.execute("SELECT 1 FROM guest_finishes WHERE claim = ?", (claim,)).fetchone():
            break
    conn.execute("INSERT INTO guest_finishes(guest, rule_id, claim, finished_at) VALUES (?, ?, ?, ?)", (guest, rid, claim, now_iso()))
    return claim


def guest_cards(conn, guest):
    """What this phone has found, for challenges that are on (the student site's hunt page)."""
    from . import rules
    out = []
    for rule in rules.active(conn, "challenge"):
        if not is_on(conn, rule):
            continue
        done = _guest_steps(conn, guest, rule["id"]) if guest else set()
        if not done:
            continue
        fin = conn.execute("SELECT * FROM guest_finishes WHERE guest = ? AND rule_id = ?", (guest, rule["id"])).fetchone()
        steps = [{"label": s["label"], "detail": s.get("hint", ""), "done": bool(s.get("code")) and s["code"] in done}
                 for s in rule["steps"]]
        out.append({"title": rule["name"], "text": rule["summary"], "prize": prize_line(rule["prize"]), "steps": steps,
                    "progress": {"done": sum(x["done"] for x in steps), "total": len(steps)},
                    "claim": fin["claim"] if fin else None, "claimed": bool(fin and fin["claimed_at"])})
    return out


def merge_guest(conn, guest, cid, rule_id=None):
    """A phone's progress onto the student's account (they signed in, or staff claimed it). Each scan moves
    once, and never a hunt that's been claimed or moved onto someone else's account already. The caller commits."""
    only = " AND rule_id = ?" if rule_id is not None else ""
    args = (guest,) + ((rule_id,) if rule_id is not None else ())
    gone = {r[0] for r in conn.execute(f"SELECT rule_id FROM guest_finishes WHERE guest = ?{only} "
                                       "AND (claimed_at IS NOT NULL OR (customer_id IS NOT NULL AND customer_id != ?))",
                                       args + (cid,))}
    for r in conn.execute(f"SELECT rule_id, step_code, scanned_at FROM guest_scans WHERE guest = ?{only} AND customer_id IS NULL",
                          args).fetchall():
        if r["rule_id"] in gone:
            continue
        conn.execute("INSERT OR IGNORE INTO challenge_scans(customer_id, rule_id, step_code, scanned_at) VALUES (?, ?, ?, ?)",
                     (cid, r["rule_id"], r["step_code"], r["scanned_at"]))
        conn.execute("UPDATE guest_scans SET customer_id = ? WHERE guest = ? AND rule_id = ? AND step_code = ?",
                     (cid, guest, r["rule_id"], r["step_code"]))
    conn.execute(f"UPDATE guest_finishes SET customer_id = ? WHERE guest = ?{only} AND customer_id IS NULL AND claimed_at IS NULL",
                 (cid,) + args)
    return award(conn, cid)


def _day(ts):
    from .web import local
    return local(ts, "%a %-d %b")


def claim(conn, code, cid, staff_name):
    """Staff typed a claim code on a student's page: the phone's progress is theirs, and so is the
    prize, handed over now. Returns (ok, message). The caller commits."""
    from . import rules
    code = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    fin = conn.execute("SELECT f.*, r.name FROM guest_finishes f LEFT JOIN rules r ON r.id = f.rule_id WHERE f.claim = ?",
                       (code,)).fetchone()
    if fin is None:
        return False, f"No hunt finished with the claim code {code}."
    if fin["claimed_at"]:
        return False, f"{code} was already claimed."
    if fin["customer_id"] and fin["customer_id"] != cid:
        return False, f"{code} belongs to someone else's account."
    who = conn.execute("SELECT archived_at, merged_into FROM customers WHERE id = ?", (cid,)).fetchone()
    if who is None or who["merged_into"] or who["archived_at"]:
        return False, "This account is archived. Claim it on their current account."
    prize = _won(conn, cid, fin["rule_id"])
    if prize is not None and (prize["given_at"] or not prize["text"]):
        return False, f"{fin['name']}: already given on {_day(prize['given_at'] or prize['won_at'])}."
    if prize is None:
        # Check the phone's finds finish it for them before moving anything onto their account.
        rule = next((r for r in rules.active(conn, "challenge") if r["id"] == fin["rule_id"]), None)
        found = _guest_steps(conn, fin["guest"], fin["rule_id"])
        ticks = done_steps(conn, cid, rule) if rule else []
        if not rule or not all(t or (s.get("scan") and s.get("code") in found) for s, t in zip(rule["steps"], ticks)):
            return False, f"That doesn't finish {fin['name'] or 'that hunt'} for them yet."
        merge_guest(conn, fin["guest"], cid, fin["rule_id"])
        prize = _won(conn, cid, fin["rule_id"])
        if prize is None:
            return False, f"That doesn't finish {fin['name']} for them yet."
    conn.execute("UPDATE guest_finishes SET customer_id = ?, claimed_at = ?, claimed_by = ? WHERE id = ?",
                 (cid, now_iso(), staff_name, fin["id"]))
    if prize["text"]:
        conn.execute("UPDATE prizes SET given_at = ?, given_by = ? WHERE id = ?", (now_iso(), staff_name, prize["id"]))
    audit(conn, staff_name, "prize_claimed", cid, f"{fin['name']} ({code})")
    what = prize["text"] or f"{prize['points']:,} points"
    return True, f"{fin['name']}: claimed. Give them {what.lower()}." + (f" They got {prize['points']:,} points too." if prize["text"] and prize["points"] else "")


# ---- for staff -----------------------------------------------------------------------------------------

def prizes_for(conn, cid):
    """Prizes this customer has won, to give at the counter (customer page)."""
    return [dict(r) for r in conn.execute(
        "SELECT p.*, r.name FROM prizes p LEFT JOIN rules r ON r.id = p.rule_id WHERE p.customer_id = ? AND p.text != '' "
        "ORDER BY p.given_at IS NOT NULL, p.won_at DESC LIMIT 20", (cid,))]


def staff_info(conn, rule_id, spec, base=None):
    """What the Rewards page shows about a challenge: who's finished, and its codes to print."""
    won = conn.execute("SELECT COUNT(*), COALESCE(SUM(given_at IS NOT NULL), 0) FROM prizes WHERE rule_id = ?", (rule_id,)).fetchone()
    started = conn.execute("SELECT COUNT(DISTINCT customer_id) FROM challenge_scans WHERE rule_id = ?", (rule_id,)).fetchone()[0]
    codes = [{"label": s["label"], "hint": s.get("hint", ""), "url": code_url(s["code"], base)}
             for s in spec.get("steps", []) if s.get("scan") and s.get("code")]
    return {"finished": won[0], "given": won[1], "started": started, "codes": codes,
            "prize": prize_line(spec.get("prize") or {}), "steps": len(spec.get("steps", []))}


def new_hunt(name, summary, start, end, stops, prize_text, prize_points):
    """The Rewards page's form: a scavenger hunt, one stop per line ("Label | hint"), or with codes
    already printed on posters, "Label | hint | CODE" (the code, or the whole link it's in)."""
    steps = []
    for line in (stops or "").splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split("|")]
        step = {"label": parts[0], "scan": True, "hint": parts[1] if len(parts) > 1 else ""}
        if len(parts) > 2 and parts[2]:
            from .rules import RuleError
            code = token_of(parts[2])
            if not code:
                raise RuleError(f"\"{parts[2]}\" isn't a code (8 to 40 letters, numbers, - or _).")
            step["code"] = code
        steps.append(step)
    window = {k: v for k, v in (("from", start), ("to", end)) if v}
    rule = {"kind": "challenge", "name": name, "summary": summary or f"Find all {len(steps)} codes around the school.",
            "steps": steps, "prize": {"text": prize_text, "points": int(prize_points) if str(prize_points).strip().isdigit() else 0}}
    if window:
        rule["window"] = window
    return rule


@router.post("/challenges")
def challenge_create(request: Request, name: str = Form(""), summary: str = Form(""), start: str = Form(""), end: str = Form(""),
                     stops: str = Form(""), prize_text: str = Form(""), prize_points: str = Form(""),
                     who=Depends(staff), conn=Depends(get_conn)):
    from . import rules
    try:
        rid = rules.create(conn, new_hunt(name, summary, start, end, stops, prize_text, prize_points), who, source="Rewards")
    except rules.RuleError as e:
        return back(request, "/rewards#new-challenge", f"Challenge: {e}", "bad")
    conn.commit()
    return back(request, f"/challenges/{rid}/codes", "Challenge started. Print the codes and stick them up.")


@router.get("/challenges/{rid:int}/codes", response_class=HTMLResponse)
def challenge_codes(request: Request, rid: int, who=Depends(staff), conn=Depends(get_conn)):
    """A page of the challenge's QR codes, to print and stick up."""
    row = conn.execute("SELECT * FROM rules WHERE id = ? AND kind = 'challenge' AND deleted_at IS NULL", (rid,)).fetchone()
    if row is None:
        raise HTTPException(404)
    import segno
    spec = json.loads(row["spec"])
    info = staff_info(conn, rid, spec, str(request.base_url))
    for c in info["codes"]:
        c["svg"] = segno.make(c["url"], error="m").svg_inline(scale=1, border=2, dark="#1B1A1F", light="#FFFFFF", omitsize=True)
    return render(request, "challenge_codes.html", rule=dict(spec, id=rid), info=info)


@router.post("/customers/{cid:int}/claim-prize")
def prize_claim(request: Request, cid: int, code: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    if conn.execute("SELECT 1 FROM customers WHERE id = ?", (cid,)).fetchone() is None:
        raise HTTPException(404)
    ok, msg = claim(conn, code, cid, who)
    conn.commit()
    return back(request, f"/customers/{cid}", msg, "ok" if ok else "bad")


@router.post("/customers/{cid:int}/prizes/{pid:int}/given")
def prize_given(request: Request, cid: int, pid: int, who=Depends(staff), conn=Depends(get_conn)):
    row = conn.execute("SELECT p.*, r.name FROM prizes p LEFT JOIN rules r ON r.id = p.rule_id WHERE p.id = ? AND p.customer_id = ?",
                       (pid, cid)).fetchone()
    if row is None:
        raise HTTPException(404)
    if row["given_at"] is None:
        conn.execute("UPDATE prizes SET given_at = ?, given_by = ? WHERE id = ?", (now_iso(), who, pid))
        audit(conn, who, "prize_given", cid, f"{row['text']} ({row['name']})")
        conn.commit()
    return back(request, f"/customers/{cid}", f"Gave {row['text'].lower()}.")
