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
import threading
import time
import uuid
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


STREAM = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"


def _stream(contents, system, tools=True):
    """Like _call, but yields ("delta", text) as Gemini writes, then ("content", the whole reply)."""
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "tools": TOOLS,
            "generationConfig": {"temperature": 0.3}}
    if not tools:
        body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
    texts, others, signature, finish, blocked = [], [], None, None, None
    try:
        with httpx.stream("POST", STREAM.format(model=model()), params={"alt": "sse"}, headers={"x-goog-api-key": api_key()},
                          json=body, timeout=httpx.Timeout(90, read=120)) as r:
            if r.status_code >= 400:
                r.read()
                _raise_for(r.status_code, r.text)
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:].strip())
                except ValueError:
                    continue
                blocked = (chunk.get("promptFeedback") or {}).get("blockReason") or blocked
                for cand in chunk.get("candidates") or []:
                    finish = cand.get("finishReason") or finish
                    for part in (cand.get("content") or {}).get("parts") or []:
                        if "text" in part and not part.get("thought"):
                            texts.append(part["text"])
                            signature = part.get("thoughtSignature") or signature
                            if part["text"]:
                                yield "delta", part["text"]
                        elif "text" not in part:
                            others.append(part)  # function calls (whole, with their signature)
    except httpx.HTTPError as e:
        raise AskError(f"Couldn't reach Gemini ({e.__class__.__name__}). Try again.")
    parts = ([{"text": "".join(texts), **({"thoughtSignature": signature} if signature else {})}] if texts else []) + others
    if not parts:
        raise AskError(f"Gemini didn't answer ({blocked or finish or 'no answer'}). Try rewording it.")
    yield "content", {"role": "model", "parts": parts}


def _raise_for(status, text):
    if status in (400, 401, 403) and "key" in text.lower():
        raise AskError(f"Gemini didn't accept the API key. Check it in Settings, Ask. (Google said: {text[:200]})")
    if status == 429:
        raise AskError("Gemini says too many requests right now. Wait a minute and try again.")
    raise AskError(f"Gemini returned an error ({status}): {text[:300]}")


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


STATUS = {"sales_summary": lambda a: f"Adding up sales by {a.get('group_by', '...')}",
          "sales": lambda a: f"Looking through sales from {a.get('start', '?')} to {a.get('end', '?')}",
          "sql": lambda a: "Checking the database",
          "list_rules": lambda a: "Looking at your custom badges and events",
          "preview_rule": lambda a: "Checking the rule and who'd qualify",
          "propose": lambda a: "Writing up a suggestion"}


def steps(conn, who, text, stream=True, stop=lambda: False):
    """Answer a question, yielding what's happening: ("status", "Adding up sales by weekday"),
    ("delta", next bit of the answer), ("reset", None) when a draft turns into lookups, and
    finally ("done", whole answer). Everything is saved as it goes."""
    if not enabled():
        raise AskError("Ask isn't set up: add a Gemini API key in Settings, Ask.")
    text = text.strip()[:4000]
    if not text:
        raise AskError("Type a question first.")
    system = system_prompt(conn, who)
    _save(conn, who, "user", [{"text": text}], text)
    contents = _history(conn, who)

    for _ in range(MAX_STEPS):
        if stop():  # given up on: write nothing more into the conversation
            return
        if stream:  # pass the words through as they arrive
            content = None
            for kind, value in _stream(contents, system):
                if kind == "delta":
                    yield "delta", value
                else:
                    content = value
        else:
            content = _call(contents, system)
        parts = content["parts"]
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        said = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")).strip()
        _save(conn, who, "model", parts, said or None)
        contents.append({"role": "model", "parts": parts})
        if not calls:
            yield "done", said
            return
        if said:  # it wrote a bit, then went to look things up: that text stays as its own message
            yield "reset", None
        answers = []
        for c in calls:
            yield "status", STATUS.get(c["name"], lambda a: "Looking things up")(c.get("args") or {})
            answers.append({"functionResponse": {"name": c["name"], "response": run_tool(conn, who, c["name"], c.get("args") or {})}})
        if stop():
            return
        _save(conn, who, "tool", answers)
        contents.append({"role": "user", "parts": answers})
    # Out of steps: have it answer from what it found rather than give up.
    contents.append({"role": "user", "parts": [{"text": "(Fizzy: no more lookups. Answer now from what you've found, and say if it's incomplete.)"}]})
    yield "status", "Putting it together"
    content = _call(contents, system, tools=False)
    if stop():
        return
    said = "".join(p.get("text", "") for p in content["parts"] if "text" in p and not p.get("thought")).strip() \
        or "I couldn't get to an answer. Try asking a narrower question."
    _save(conn, who, "model", [{"text": said}], said)
    yield "done", said


def ask(conn, who, text):
    """The whole answer at once (no streaming): for the plain form post and tests."""
    for kind, value in steps(conn, who, text, stream=False):
        if kind == "done":
            return value


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


_md = None


def md(text):
    """Gemini's markdown as HTML (mistune: tables, lists, quotes, code, links...). Raw HTML is escaped
    and javascript: links are blocked, so an answer can't put anything live on the page. Number
    columns line up on the right, wide tables scroll, and emojis are dropped (the staff site has none)."""
    global _md
    if _md is None:
        import mistune
        _md = mistune.create_markdown(escape=True, plugins=["table", "strikethrough", "url", "task_lists"])
    html = _md(EMOJI.sub("", text or ""))
    html = html.replace("<table>", '<div class="scroll"><table>').replace("</table>", "</table></div>")
    html = re.sub(r'<a href="(?!#harmful)', '<a target="_blank" rel="noopener" href="', html)

    def cell(m):  # numbers (even bold ones) on the right, like every other table in Fizzy
        tag, attrs, inner = m.group(1), m.group(2), m.group(3)
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        if "text-align" not in attrs and re.fullmatch(r"[-+]?\$?[\d,]+(\.\d+)?%?", plain):
            attrs += ' class="n"'
        return f"<{tag}{attrs}>{inner}</{tag}>"
    html = re.sub(r"<(td|th)([^>]*)>(.*?)</\1>", cell, html, flags=re.S)
    return Markup(html)


@router.get("/ask", response_class=HTMLResponse)
def page(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    pend = pending(who)   # before the messages: an answer finishing in between is then in the timeline, and joining it finds it done
    msgs = conn.execute("SELECT * FROM ask_messages WHERE staff = ? AND shown IS NOT NULL ORDER BY id DESC LIMIT 60", (who,)).fetchall()
    props = conn.execute("SELECT * FROM ask_proposals WHERE staff = ? ORDER BY id DESC LIMIT 30", (who,)).fetchall()
    timeline = [{"at": m["created_at"], "n": (0, m["id"]), "kind": "msg", "role": m["role"],
                 "html": md(m["shown"]) if m["role"] == "model" else None, "text": m["shown"]} for m in msgs]
    timeline += [{"at": p["created_at"], "n": (1, p["id"]), "kind": "proposal", "p": p} for p in props]
    timeline.sort(key=lambda t: (t["at"], t["n"]))  # oldest first; a card after the words on the same second
    return render(request, "ask.html", timeline=timeline[-80:], enabled=enabled(), model=model(), pending=pend,
                  examples=["How did each item sell this month compared to last month?",
                            "Which day of the week is slowest, and what sells then?",
                            "Make a badge for buying 20 Chi Forest and 10 milk teas in a term, worth 500 points",
                            "Every 5th noodles a student buys gets 100 bonus points",
                            "Who hasn't bought anything in two weeks but used to come every week?"])


@router.post("/ask")
def send(request: Request, text: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    """The plain form (no script): waits for the whole answer."""
    if pending(who):
        return back(request, "/ask#end", "Still answering your last question. Wait for it, then ask again.", "bad")
    try:
        ask(conn, who, text)
    except AskError as e:
        return back(request, "/ask#end", str(e), "bad")
    return RedirectResponse("/ask#end", status_code=303)


# ---- answering in the background ------------------------------------------------------------------------
#
# A question is answered in its own thread, not inside the request that asked it, so leaving the page or
# losing the connection (a phone in the shop) doesn't lose the answer. The stream request only follows
# along; it can be joined again (GET) and the page says what's still being answered (`pending`).

STUCK_AFTER = 15 * 60        # seconds before an answer that never finished is given up on


class _Job:
    def __init__(self, who, text, client=""):
        self.who, self.text, self.started = who, text, db.now_iso()
        self.id, self.t0 = uuid.uuid4().hex[:12], time.time()
        # Every event so far, so a client joining late gets the whole answer. The first says which
        # question this is (and the asking client's own tag), so a client can tell it from an older one.
        self.events = [{"t": "start", "id": self.id, "text": text, "client": client}]
        self.answer, self.status, self.done = "", None, False
        self.abandoned = False      # given up on: its thread stops writing into the conversation
        self.cond = threading.Condition()

    def push(self, ev):
        with self.cond:
            t = ev["t"]
            if t == "delta":
                self.answer += ev["text"]
                self.status = None
            elif t == "status":
                self.status = ev["text"]
            elif t == "reset":
                self.answer = ""
            self.events.append(ev)
            if t in ("done", "error"):
                self.done = True
            self.cond.notify_all()

    def follow(self):
        """The events so far, then each new one as it happens, until the answer is done. A comment line
        every 15 quiet seconds keeps the connection open through Cloudflare."""
        n = 0
        while True:
            with self.cond:
                if n >= len(self.events) and not self.done:
                    self.cond.wait(15)
                batch, done = self.events[n:], self.done
            n += len(batch)
            for ev in batch:
                yield _sse(ev)
            if done:
                return
            if not batch:
                yield ": ping\n\n"


_jobs = {}                   # staff name -> their latest _Job
_jobs_lock = threading.Lock()


def pending(who):
    """What's still being answered for this person, for the page: None when nothing is."""
    j = _jobs.get(who)
    if j is None or j.done:
        return None
    return {"id": j.id, "text": j.text, "answer": j.answer, "status": j.status, "at": j.started}


def start(who, text, client=""):
    """Start answering in the background. One question at a time per person. `client` is the asker's
    own tag for the question, echoed in the stream's first event."""
    text = text.strip()[:4000]   # as steps() saves it
    with _jobs_lock:
        j = _jobs.get(who)
        if j is not None and not j.done:
            if time.time() - j.t0 < STUCK_AFTER:
                raise AskError("Still answering your last question. Wait for it, then ask again.")
            j.abandoned = True
            j.push({"t": "error", "text": "That took too long and was given up on. Ask again."})
        j = _jobs[who] = _Job(who, text, client)

    def run():
        conn = None
        try:
            conn = db.connect()
            for kind, value in steps(conn, who, text, stop=lambda: j.abandoned):
                if j.abandoned:
                    break
                if kind == "delta":  # raw text: the page reveals it smoothly and formats it as it goes
                    j.push({"t": "delta", "text": value})
                elif kind == "status":
                    j.push({"t": "status", "text": value + "..."})
                elif kind == "reset":
                    j.push({"t": "reset"})
                else:
                    j.push({"t": "done"})
        except AskError as e:
            j.push({"t": "error", "text": str(e)})
        except Exception as e:  # never leave the page hanging
            j.push({"t": "error", "text": f"Something went wrong ({e.__class__.__name__}). Try again."})
        finally:
            if conn is not None:
                conn.close()
            if not j.done:
                j.push({"t": "done"})
    try:
        threading.Thread(target=run, daemon=True, name=f"ask:{who}").start()
    except Exception:
        j.push({"t": "error", "text": "Couldn't start answering. Try again."})
        raise AskError("Couldn't start answering. Try again.")
    return j


def _events(gen):
    from fastapi.responses import StreamingResponse
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/ask/stream")
def send_stream(request: Request, text: str = Form(""), client: str = Form(""), who=Depends(staff)):
    """The answer as it's written, as server-sent events (one JSON object per event)."""
    try:
        job = start(who, text, client[:64])
    except AskError as e:
        return _events(iter([_sse({"t": "error", "text": str(e)})]))
    return _events(job.follow())


@router.get("/ask/stream")
def join_stream(who=Depends(staff)):
    """Join the latest answer, from the start: after leaving the page, or when the connection dropped. A
    finished one is replayed too (the connection may have dropped right at the end); its "start" event
    tells the client whether it's the one it wants. {"t": "none"} when nothing has been asked since the
    server started."""
    j = _jobs.get(who)
    if j is None:
        return _events(iter([_sse({"t": "none"})]))
    return _events(j.follow())


def _sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


@router.post("/ask/clear")
def clear(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    """A clean slate: the words and the suggestion cards both go. What a card did (a rule, a reward, a
    double points day) stays, and the activity log still says who pressed Do it."""
    if pending(who):   # the answer would land in the new conversation
        return back(request, "/ask", "Still answering your last question. Wait for it, then start a new chat.", "bad")
    conn.execute("DELETE FROM ask_messages WHERE staff = ?", (who,))
    conn.execute("DELETE FROM ask_proposals WHERE staff = ?", (who,))
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
