"""The Ask page: chat with Gemini about the stall, and have it set things up.

Gemini can read everything (sales, customers, tabs: the owner uses a paid key, so Google doesn't
train on it) through a few tools, including read-only SQL. It can't change anything itself: a
change comes back as a suggestion card with a "Do it" button, which goes through the same
passcode rules as doing it by hand and is logged "via Ask". New badges and events are rules
(rules.py), never code.
"""
import json
import re
import sqlite3
import time
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup, escape

from . import config, db, events, rewards, rules, stats
from .web import back, get_conn, money, render, staff

router = APIRouter()

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
HISTORY = 40          # messages of context sent each time
MAX_STEPS = 60        # lookups in one answer (a safety net: the time limit below is what normally stops it)
TIME_LIMIT = 75       # seconds of looking things up, then it answers with what it has (Cloudflare cuts a page off at ~100s)
ROW_CAP = 300


class AskError(Exception):
    pass


def _fernet():
    import base64, hashlib
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"fizzy-gemini:" + config.SECRET_KEY.encode()).digest()))


def api_key():
    """GEMINI_API_KEY from .env if set, else the one saved in Settings (encrypted with FIZZY_SECRET_KEY)."""
    if config.GEMINI_API_KEY:
        return config.GEMINI_API_KEY
    stored = db_setting("gemini_key_enc")
    if not stored:
        return ""
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except Exception:  # the secret key changed: it has to be entered again
        return ""


def model():
    return db_setting("gemini_model") or config.GEMINI_MODEL


def db_setting(key):
    conn = db.connect()
    try:
        return db.get_setting(conn, key, "") or ""
    finally:
        conn.close()


def enabled():
    return bool(api_key())


# ---- what Gemini is told ------------------------------------------------------------------------

def _schema(conn):
    out = []
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL ORDER BY name"):
        if name.startswith("sqlite_") or name in ("ask_messages", "ask_proposals", "passkeys", "device_links", "devices", "qr_logins"):
            continue
        out.append(sql)
    return "\n".join(out)


def system_prompt(conn, who):
    today = stats.today()
    items = [r[0] for r in conn.execute("SELECT DISTINCT item_name FROM catalog_items WHERE deleted = 0 ORDER BY item_name")]
    terms = rules.terms(conn)
    cfg = {k: db.get_setting(conn, k) for k in ("tab_limit_cents", "tab_due_days", "unlock_spend_cents", "tab_score_needed")}
    return f"""You are the assistant inside Fizzy, the staff app for Cooked Coke Co., a student-run tuck shop (drinks and
noodles) at Reddam House North Shore, Sydney. You're talking to {who}, one of the student owners. Use Australian
English, be brief and plain, no emojis. Money is AUD. Today is {today:%A %-d %B %Y}, Sydney time.

How the stall works: students earn 1 point per cent spent (tab purchases earn theirs when paid off), swap points for
rewards, can have a tab once they've spent ${int(cfg['unlock_spend_cents'] or 3000) / 100:.0f} and their tab score is
{cfg['tab_score_needed']}+ (pay within {cfg['tab_due_days']} days). Double points days multiply points. Built-in badges pay
points once. Open at recess (11:00-11:15) and lunch (13:10-13:45) on school days, no lunch on Tuesdays.

You can look anything up with the tools. Start with `sales_summary` for totals by weekday, hour, day, week, month or item
(one call answers most questions). `sales` lists individual purchases; `sql` is for anything else (read-only SQLite).
Use as few lookups as you can.
Amounts in the database are in cents; times are UTC ISO strings (Sydney is UTC+10/+11). square_payments.raw_json holds
Square's payment and order JSON. Check numbers with a tool rather than guessing, and say what period you looked at.
When you show data, a short markdown table or list is good.

You can't change anything directly. To change something, call `propose` with the action: the owner sees a card with a
"Do it" button. Say in one line what you've suggested. Don't propose without being asked to set something up.

Custom badges and events: when the owner describes one (however unusual), turn it into a RULE, call `preview_rule` to
check it and see who'd qualify, fix any error it reports, then `propose` it with action "create_rule". If an idea needs
something the rule format can't express (weather, things Fizzy doesn't record), say so plainly and suggest the closest
thing that is possible. Item names must match the menu below (partial names are fine).

{rules.SPEC}

Fizzy launched {db.get_setting(conn, "history_before", "") or "(no launch date set: every sale counts)"} (UTC): sales before that were
imported as history only, with no points or tabs, so use them for sales trends but not for anything about points.

Menu items in Square: {', '.join(items) if items else '(none synced yet)'}
Term dates: {'; '.join(f'{n}: {a} to {b}' for a, b, n in terms) if terms else 'none set (Settings, Term dates)'}
Existing custom rules: use `list_rules`.

Database tables:
{_schema(conn)}
"""


TOOLS = [{"functionDeclarations": [
    {"name": "sales", "description": "Purchases between two Sydney dates (inclusive), oldest first: time, customer, items, total, tab part, how paid. At most 1500.",
     "parameters": {"type": "OBJECT", "properties": {"start": {"type": "STRING", "description": "YYYY-MM-DD"}, "end": {"type": "STRING", "description": "YYYY-MM-DD"}},
                    "required": ["start", "end"]}},
    {"name": "sales_summary", "description": "Sales added up, ready to compare: total sold, purchases and items, grouped by "
     "weekday, hour, day, week, month or item, or two of them (e.g. weekday,item). Use this first for 'which day/time/item' questions.",
     "parameters": {"type": "OBJECT", "properties": {"start": {"type": "STRING", "description": "YYYY-MM-DD"}, "end": {"type": "STRING", "description": "YYYY-MM-DD"},
                                                     "group_by": {"type": "STRING", "description": "weekday | hour | day | week | month | item, or two joined by a comma"}},
                    "required": ["start", "end", "group_by"]}},
    {"name": "sql", "description": f"Run one read-only SQLite SELECT. Returns up to {ROW_CAP} rows as JSON.",
     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
    {"name": "list_rules", "description": "The custom badges and events that exist, with their JSON and how many students got them."},
    {"name": "preview_rule", "description": "Check a RULE (JSON string) and see who would qualify right now / what an event would pay. Returns an error to fix if it's not valid.",
     "parameters": {"type": "OBJECT", "properties": {"rule_json": {"type": "STRING"}}, "required": ["rule_json"]}},
    {"name": "propose", "description": "Suggest a change; the owner confirms it with a button. Actions and payload_json: "
     "create_rule {rule}; update_rule {id, rule}; pause_rule {id}; resume_rule {id}; delete_rule {id}; "
     "double_points {name, multiplier (e.g. 2), start YYYY-MM-DD, end YYYY-MM-DD}; "
     "reward {name, points, square_note (what staff apply in Square, e.g. 'Points: Free Coke'), limit (per student, optional)}; "
     "display_message {text} (the customer display's banner, '' to clear).",
     "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}, "payload_json": {"type": "STRING"},
                                                     "summary": {"type": "STRING", "description": "One line the owner sees on the card"}},
                    "required": ["action", "payload_json", "summary"]}},
]}]


# ---- tools ----------------------------------------------------------------------------------------

def _sales(conn, start, end):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return {"error": "Dates must be YYYY-MM-DD."}
    lo, _ = stats.day_bounds(a)
    _, hi = stats.day_bounds(b)
    rows = []
    for c in stats.checkouts(conn, lo, hi)[:1500]:
        if not (c["items"] or c["sold_cents"] or c["repay_cents"]):
            continue
        rows.append({"at": rules._local(c["at"]).strftime("%a %Y-%m-%d %H:%M"), "customer": c["customer"],
                     "items": [f"{i['qty']}x {i['name']} ${i['cents'] / 100:.2f}" for i in c["items"]],
                     "total": round(c["sold_cents"] / 100, 2), "on_tab": round(c["tab_cents"] / 100, 2),
                     "tab_repaid": round(c["repay_cents"] / 100, 2), "paid_with": c["tenders"]})
    return {"purchases": rows, "count": len(rows)}


def _summary(conn, start, end, group_by):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return {"error": "Dates must be YYYY-MM-DD."}
    keys = [k.strip() for k in (group_by or "").split(",") if k.strip()]
    ok = {"weekday", "hour", "day", "week", "month", "item"}
    if not keys or len(keys) > 2 or not set(keys) <= ok:
        return {"error": f"group_by must be one or two of {', '.join(sorted(ok))}."}
    lo, _ = stats.day_bounds(a)
    _, hi = stats.day_bounds(b)
    out, days = {}, {}
    for c in stats.checkouts(conn, lo, hi):
        if not (c["items"] or c["sold_cents"]):
            continue
        t = rules._local(c["at"])
        when = {"weekday": t.strftime("%a"), "hour": f"{t.hour:02d}:00", "day": t.date().isoformat(),
                "week": (t.date() - timedelta(days=t.weekday())).isoformat(), "month": t.strftime("%Y-%m")}
        days.setdefault(when["weekday"], set()).add(t.date())
        lines = c["items"] if "item" in keys else [{"name": None, "qty": sum(i["qty"] for i in c["items"]), "cents": c["sold_cents"]}]
        for it in lines:
            k = tuple(it["name"] if g == "item" else when[g] for g in keys)
            row = out.setdefault(k, {"sold": 0, "purchases": 0, "items": 0})
            row["sold"] += it["cents"]
            row["items"] += it["qty"]
            row["purchases"] += 1
    rows = [{**{g: v for g, v in zip(keys, k)}, "sold": round(r["sold"] / 100, 2), "purchases": r["purchases"], "items": r["items"]}
            for k, r in out.items()]
    rows.sort(key=lambda r: -r["sold"])
    return {"rows": rows[:300], "period": f"{a} to {b}",
            "trading_days_per_weekday": {d: len(s) for d, s in days.items()},
            "note": "To compare weekdays fairly, divide by trading_days_per_weekday (some weekdays had more trading days)."}


def _sql(query):
    q = query.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
        return {"error": "Only one SELECT (or WITH ... SELECT) at a time."}
    ro = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=5)
    ro.row_factory = sqlite3.Row
    started = time.monotonic()
    ro.set_progress_handler(lambda: 1 if time.monotonic() - started > 5 else 0, 10000)  # 5 second cap
    try:
        ro.execute("PRAGMA query_only = 1")
        cur = ro.execute(q)
        rows = cur.fetchmany(ROW_CAP + 1)
        out = [dict(r) for r in rows[:ROW_CAP]]
        for r in out:  # raw Square JSON is huge; trim it
            for k, v in r.items():
                if isinstance(v, str) and len(v) > 2000:
                    r[k] = v[:2000] + "...(cut)"
        return {"rows": out, "more": len(rows) > ROW_CAP}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        ro.close()


def _parse(js, what="rule_json"):
    try:
        v = json.loads(js)
    except (TypeError, ValueError) as e:
        raise AskError(f"{what} isn't valid JSON: {e}")
    return v


def _preview(conn, rule_json):
    try:
        p = rules.preview(conn, _parse(rule_json))
    except (AskError, rules.RuleError) as e:
        return {"error": str(e)}
    return {"ok": True, "students_who_qualify_now": p["students"], "points_it_would_pay_now": p["points_total"],
            "top": p["top"], "counts_purchases_between": p["window"], "rule_as_checked": p["rule"]}


def _check_proposal(conn, action, payload):
    """Validate now, so the card only ever offers something that will work."""
    if action in ("create_rule", "update_rule"):
        rules.validate(conn, payload.get("rule") if isinstance(payload.get("rule"), dict) else payload)
        if action == "update_rule" and not conn.execute("SELECT 1 FROM rules WHERE id = ? AND deleted_at IS NULL", (payload.get("id"),)).fetchone():
            raise AskError("There's no rule with that id.")
    elif action in ("pause_rule", "resume_rule", "delete_rule"):
        if not conn.execute("SELECT 1 FROM rules WHERE id = ? AND deleted_at IS NULL", (payload.get("id"),)).fetchone():
            raise AskError("There's no rule with that id.")
    elif action == "double_points":
        for k in ("start", "multiplier"):
            if k not in payload:
                raise AskError(f"double_points needs {k}.")
    elif action == "reward":
        if not payload.get("name") or not isinstance(payload.get("points"), int):
            raise AskError("reward needs name and a whole number of points.")
    elif action == "display_message":
        if not isinstance(payload.get("text"), str):
            raise AskError("display_message needs text.")
    else:
        raise AskError(f"Unknown action {action!r}.")


def _propose(conn, who, action, payload_json, summary):
    try:
        payload = _parse(payload_json, "payload_json")
        if not isinstance(payload, dict):
            raise AskError("payload_json must be an object.")
        if action == "create_rule" and "rule" not in payload:
            payload = {"rule": payload}
        _check_proposal(conn, action, payload)
    except (AskError, rules.RuleError) as e:
        return {"error": str(e)}
    cur = conn.execute("INSERT INTO ask_proposals(staff, action, payload, summary, created_at) VALUES (?, ?, ?, ?, ?)",
                       (who, action, json.dumps(payload), EMOJI.sub("", " ".join(str(summary).split()))[:300], db.now_iso()))
    conn.commit()
    return {"ok": True, "proposal": cur.lastrowid, "note": "Shown to the owner as a card with a Do it button. Nothing has changed yet."}


def run_tool(conn, who, name, args):
    try:
        if name == "sales":
            return _sales(conn, args.get("start", ""), args.get("end", ""))
        if name == "sales_summary":
            return _summary(conn, args.get("start", ""), args.get("end", ""), args.get("group_by", ""))
        if name == "sql":
            return _sql(args.get("query", ""))
        if name == "list_rules":
            return {"rules": [{k: r[k] for k in ("id", "kind", "name", "summary", "paused", "students", "points", "spec")} for r in rules.listing(conn)]}
        if name == "preview_rule":
            return _preview(conn, args.get("rule_json", ""))
        if name == "propose":
            return _propose(conn, who, args.get("action", ""), args.get("payload_json", ""), args.get("summary", ""))
        return {"error": f"No tool called {name}."}
    except Exception as e:  # a tool error goes back to Gemini to deal with, never a crash
        return {"error": f"{e.__class__.__name__}: {e}"}


# ---- talking to Gemini ---------------------------------------------------------------------------------

def _call(contents, system, tools=True):
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "tools": TOOLS,
            "generationConfig": {"temperature": 0.3}}
    if not tools:  # out of steps: answer now with what's been found, no more lookups
        body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
    try:
        r = httpx.post(API.format(model=model()), headers={"x-goog-api-key": api_key()}, json=body, timeout=90)
    except httpx.HTTPError as e:
        raise AskError(f"Couldn't reach Gemini ({e.__class__.__name__}). Try again.")
    if r.status_code in (400, 401, 403) and "key" in r.text.lower():
        raise AskError(f"Gemini didn't accept the API key. Check it in Settings, Ask. (Google said: {r.text[:200]})")
    if r.status_code == 429:
        raise AskError("Gemini says too many requests right now. Wait a minute and try again.")
    if r.status_code >= 400:
        raise AskError(f"Gemini returned an error ({r.status_code}): {r.text[:300]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands or not (cands[0].get("content") or {}).get("parts"):
        why = (data.get("promptFeedback") or {}).get("blockReason") or (cands[0].get("finishReason") if cands else "no answer")
        raise AskError(f"Gemini didn't answer ({why}). Try rewording it.")
    return cands[0]["content"]


def _history(conn, who):
    rows = conn.execute("SELECT role, content FROM ask_messages WHERE staff = ? ORDER BY id DESC LIMIT ?", (who, HISTORY)).fetchall()
    rows = list(reversed(rows))
    while rows and rows[0]["role"] != "user":  # never start mid tool-call
        rows.pop(0)
    out = []
    for r in rows:
        parts = json.loads(r["content"])
        if r["role"] == "user" and any("functionResponse" in p for p in parts) and out and out[-1]["role"] == "user":
            out[-1]["parts"].extend(parts)
        else:
            out.append({"role": "model" if r["role"] == "model" else "user", "parts": parts})
    # Drop a dangling call with no answer (e.g. an error last time).
    while out and out[-1]["role"] == "model" and any("functionCall" in p for p in out[-1]["parts"]):
        out.pop()
    return out


def _save(conn, who, role, parts, shown=None):
    conn.execute("INSERT INTO ask_messages(staff, role, content, shown, created_at) VALUES (?, ?, ?, ?, ?)",
                 (who, role, json.dumps(parts), shown, db.now_iso()))
    conn.commit()


def ask(conn, who, text):
    if not enabled():
        raise AskError("Ask isn't set up: add a Gemini API key in Settings, Ask.")
    text = text.strip()[:4000]
    if not text:
        raise AskError("Type a question first.")
    system = system_prompt(conn, who)
    _save(conn, who, "user", [{"text": text}], text)
    contents = _history(conn, who)
    started = time.monotonic()
    for _ in range(MAX_STEPS):
        if time.monotonic() - started > TIME_LIMIT:
            break
        content = _call(contents, system)
        parts = content["parts"]
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        said = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")).strip()
        _save(conn, who, "model", parts, said or None)
        contents.append({"role": "model", "parts": parts})
        if not calls:
            return said
        answers = [{"functionResponse": {"name": c["name"], "response": run_tool(conn, who, c["name"], c.get("args") or {})}} for c in calls]
        _save(conn, who, "tool", answers)
        contents.append({"role": "user", "parts": answers})
    # Out of steps: have it answer from what it found rather than give up.
    contents.append({"role": "user", "parts": [{"text": "(Fizzy: no more lookups. Answer now from what you've found, and say if it's incomplete.)"}]})
    content = _call(contents, system, tools=False)
    said = "".join(p.get("text", "") for p in content["parts"] if "text" in p and not p.get("thought")).strip() \
        or "I couldn't get to an answer. Try asking a narrower question."
    _save(conn, who, "model", [{"text": said}], said)
    return said


# ---- doing a suggestion -----------------------------------------------------------------------------------

def apply(conn, pid, who):
    p = conn.execute("SELECT * FROM ask_proposals WHERE id = ?", (pid,)).fetchone()
    if p is None or p["status"] != "pending":
        raise AskError("That suggestion has already been dealt with.")
    payload = json.loads(p["payload"])
    a = p["action"]
    if a == "create_rule":
        rules.create(conn, payload["rule"], who)
    elif a == "update_rule":
        rules.update(conn, int(payload["id"]), payload["rule"], who)
    elif a in ("pause_rule", "resume_rule", "delete_rule"):
        rules.set_state(conn, int(payload["id"]), a.split("_")[0], who)
    elif a == "double_points":
        try:
            events.create(conn, payload.get("name") or "Double points", str(payload["multiplier"]), payload["start"],
                          payload.get("end") or "", who)
        except events.EventError as e:
            raise AskError(str(e))
    elif a == "reward":
        rewards.create(conn, payload["name"], payload["points"], payload.get("square_note") or f"Points: {payload['name']}",
                       payload.get("limit"), who)
    elif a == "display_message":
        db.set_setting(conn, "display_message", " ".join(payload["text"].split())[:120])
        db.audit(conn, who, "settings", None, f"display message (via Ask): {payload['text'][:120]}")
    conn.execute("UPDATE ask_proposals SET status = 'done', done_at = ? WHERE id = ?", (db.now_iso(), pid))
    if a in ("create_rule", "update_rule", "resume_rule"):
        _pay_everyone(conn)
    return p["summary"]


def _pay_everyone(conn):
    """A new or changed rule can pay students straight away (e.g. a badge they already qualify for)."""
    for (cid,) in conn.execute("SELECT DISTINCT customer_id FROM square_payments WHERE customer_id IS NOT NULL").fetchall():
        rules.award(conn, cid)


# ---- the page ---------------------------------------------------------------------------------------

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F]")


def md(text):
    """Just enough markdown for Gemini's answers: paragraphs, lists, **bold**, `code`, tables.
    Emojis are dropped: the staff site has none."""
    out, lines, i = [], EMOJI.sub("", text or "").splitlines(), 0

    def inline(s):
        s = str(escape(s))
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)   # *italic*, not 2*3*4
        s = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"<i>\1</i>", s)       # _italic_, not snake_case
        s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]*)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
        return s
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            num = lambda c: re.fullmatch(r"[-+$]?[\d,.]+%?", c or "") is not None
            out.append('<div class="scroll"><table><tr>' + "".join(f"<th>{inline(h)}</th>" for h in head) + "</tr>" +
                       "".join("<tr>" + "".join(f'<td class="{"n" if num(c) else ""}">{inline(c)}</td>' for c in r) + "</tr>" for r in body) +
                       "</table></div>")
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\s*\d+\.", ln) else "ul"
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(f"<li>{inline(re.sub(r'^\s*([-*]|\d+\.)\s+', '', lines[i]))}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        if re.match(r"^#{1,4}\s", ln):
            out.append(f"<p><b>{inline(ln.lstrip('#').strip())}</b></p>")
        elif ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    return Markup("".join(out))


@router.get("/ask", response_class=HTMLResponse)
def page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    msgs = conn.execute("SELECT * FROM ask_messages WHERE staff = ? AND shown IS NOT NULL ORDER BY id DESC LIMIT 60", (who,)).fetchall()
    props = conn.execute("SELECT * FROM ask_proposals WHERE staff = ? ORDER BY id DESC LIMIT 30", (who,)).fetchall()
    timeline = [{"at": m["created_at"], "n": (0, m["id"]), "kind": "msg", "role": m["role"],
                 "html": md(m["shown"]) if m["role"] == "model" else None, "text": m["shown"]} for m in msgs]
    timeline += [{"at": p["created_at"], "n": (1, p["id"]), "kind": "proposal", "p": p} for p in props]
    timeline.sort(key=lambda t: (t["at"], t["n"]))  # oldest first; a card after the words on the same second
    return render(request, "ask.html", timeline=timeline[-80:], enabled=enabled(), model=model(),
                  examples=["How did each item sell this month compared to last month?",
                            "Which day of the week is slowest, and what sells then?",
                            "Make a badge for buying 20 Chi Forest and 10 milk teas in a term, worth 500 points",
                            "Every 5th noodles a student buys gets 100 bonus points",
                            "Who hasn't bought anything in two weeks but used to come every week?"])


@router.post("/ask")
def send(request: Request, text: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    try:
        ask(conn, who, text)
    except AskError as e:
        return back(request, "/ask#end", str(e), "bad")
    return RedirectResponse("/ask#end", status_code=303)


@router.post("/ask/clear")
def clear(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    conn.execute("DELETE FROM ask_messages WHERE staff = ?", (who,))
    conn.execute("UPDATE ask_proposals SET status = 'dismissed' WHERE staff = ? AND status = 'pending'", (who,))
    conn.commit()
    return back(request, "/ask", "Started a new conversation.")


@router.post("/ask/apply/{pid:int}")
def do_it(request: Request, pid: int, who=Depends(staff), conn=Depends(get_conn)):
    try:
        summary = apply(conn, pid, who)
    except (AskError, rules.RuleError, KeyError, ValueError) as e:
        conn.rollback()
        return back(request, "/ask#end", f"Couldn't do that: {e}", "bad")
    conn.commit()
    return back(request, "/ask#end", f"Done: {summary}")


@router.post("/ask/dismiss/{pid:int}")
def dismiss(request: Request, pid: int, who=Depends(staff), conn=Depends(get_conn)):
    conn.execute("UPDATE ask_proposals SET status = 'dismissed' WHERE id = ? AND status = 'pending'", (pid,))
    conn.commit()
    return RedirectResponse("/ask#end", status_code=303)


# ---- the key, from Settings ---------------------------------------------------------------------------------

@router.post("/settings/ask")
def save_key(request: Request, key: str = Form(""), model_name: str = Form(""), remove: str = Form(""), who=Depends(staff),
             conn=Depends(get_conn)):
    key = key.strip()
    if remove:
        db.set_setting(conn, "gemini_key_enc", "")
        db.audit(conn, who, "settings", None, "Gemini key removed")
        conn.commit()
        return back(request, "/settings#ask", "Key removed. Ask is off.")
    if key:
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,300}", key):  # "AIza..." keys, and Google's newer "AQ." style
            return back(request, "/settings#ask", "That doesn't look like a Gemini API key. Copy it again from aistudio.google.com.", "bad")
        db.set_setting(conn, "gemini_key_enc", _fernet().encrypt(key.encode()).decode())
        db.set_setting(conn, "gemini_key_hint", key[-4:])
    m = model_name.strip()
    if m and not re.fullmatch(r"[a-z0-9.\-]{3,60}", m):
        return back(request, "/settings#ask", "That model name doesn't look right, e.g. gemini-flash-latest.", "bad")
    db.set_setting(conn, "gemini_model", m)
    db.audit(conn, who, "settings", None, "Gemini key saved" if key else f"Gemini model: {m or 'default'}")
    conn.commit()
    return back(request, "/settings#ask", "Saved. Ask is on." if key else "Saved.")


# ---- managing rules by hand (Rewards page) ----------------------------------------------------------------

@router.post("/rules/{rid:int}/{state}")
def rule_state(request: Request, rid: int, state: str, who=Depends(staff), conn=Depends(get_conn)):
    if state not in ("pause", "resume", "delete"):
        return back(request, "/rewards#rules", "Unknown action.", "bad")
    rules.set_state(conn, rid, state, who)
    if state == "resume":
        _pay_everyone(conn)
    conn.commit()
    return back(request, "/rewards#rules", {"pause": "Paused.", "resume": "Back on.", "delete": "Deleted. Points already given stay."}[state])
