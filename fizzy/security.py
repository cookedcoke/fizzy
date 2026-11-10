"""Security: the fridge camera's incidents, and the Blink cameras' arm/disarm switch.

- Incidents come from the clip analyser on the home server (clip-ai on Proxmox). It has an AI
  (Gemini) watch every fridge-camera clip, with the room camera's clips from around the same time,
  and rate how likely it was a theft. Clips rated high enough are sent here: first the rating
  (POST /security/ingest), then the videos (PUT /security/ingest/<id>/<file>), with the key in
  FIZZY_SECURITY_TOKEN. Staff watch them on the Security page and mark each one OK or theft.
- The videos live next to the database (data/incidents/<id>/), not in the backups. Past
  FIZZY_INCIDENT_MAX_GB the oldest incidents' videos are deleted; their ratings stay.
- The cameras only record while they're armed, so staff disarm them while selling. The switch
  asks Home Assistant (FIZZY_HA_URL, FIZZY_HA_TOKEN), which runs the Blink cameras.
"""
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import config
from .db import audit, get_setting, now_iso, set_setting
from .web import back, get_conn, render, staff

log = logging.getLogger(__name__)
router = APIRouter()

CLIP = re.compile(r"^\d{8}_\d{6}_Security\d+\.mp4$")
CLIP_MAX = 100 * 1024 * 1024        # Cloudflare's limit for one upload
PENDING_SECONDS = 6 * 60             # Home Assistant re-reads Blink every few minutes: show "Arming..." meanwhile
VERDICTS = {"ok": "OK", "theft": "Theft"}
ALARM_STATES = {"armed_away": "Armed", "armed_home": "Armed", "armed_night": "Armed", "armed_custom_bypass": "Armed",
                "disarmed": "Disarmed", "arming": "Arming", "disarming": "Disarming", "pending": "Arming",
                "triggered": "Triggered"}


def incidents_dir():
    return os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "incidents")


def clip_path(iid, name):
    if not CLIP.match(name or ""):
        raise HTTPException(404)
    return os.path.join(incidents_dir(), str(int(iid)), name)


def score_class(score):
    return "red" if score >= 7 else "eligible" if score >= 4 else "approved"


# ---- from the clip analyser ------------------------------------------------------------------

def _check_token(request):
    if not config.SECURITY_TOKEN:
        raise HTTPException(404)
    got = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(got.encode(), config.SECURITY_TOKEN.encode()):
        raise HTTPException(403)


def save_incident(conn, d):
    """Add or update one incident from the analyser's rating. Returns its id."""
    clip = str(d.get("clip") or "")
    if not CLIP.match(clip):
        raise ValueError("clip must be a clip file name like 20261110_121301_Security02.mp4")
    context = [c for c in d.get("context") or [] if isinstance(c, str) and CLIP.match(c)][:6]
    score = max(0, min(10, int(d.get("score", 0))))
    row = (str(d.get("camera") or clip.split("_")[2].removesuffix(".mp4")), str(d.get("time") or ""), score,
           str(d.get("summary") or "")[:500], int(d.get("people") or 0), 1 if d.get("items_taken") else 0, json.dumps(context))
    cur = conn.execute("SELECT id FROM incidents WHERE clip = ?", (clip,)).fetchone()
    if cur:
        conn.execute("UPDATE incidents SET camera = ?, happened_at = ?, score = ?, summary = ?, people = ?, items_taken = ?,"
                     " context_json = ? WHERE id = ?", row + (cur["id"],))
        conn.commit()
        return cur["id"]
    iid = conn.execute("INSERT INTO incidents(clip, camera, happened_at, score, summary, people, items_taken, context_json,"
                       " received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (clip,) + row + (now_iso(),)).lastrowid
    conn.commit()
    return iid


def prune_clips(conn, limit_bytes=None):
    """Delete the oldest incidents' videos until the folder is under the limit. Ratings stay."""
    limit = limit_bytes if limit_bytes is not None else int(config.INCIDENT_MAX_GB * 1024 ** 3)
    root = incidents_dir()
    if not os.path.isdir(root):
        return 0

    def size(d):
        return sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
    dirs = {int(n): os.path.join(root, n) for n in os.listdir(root) if n.isdigit()}
    used = sum(size(d) for d in dirs.values())
    removed = 0
    for row in conn.execute("SELECT id FROM incidents ORDER BY happened_at, id").fetchall():
        if used <= limit:
            break
        d = dirs.get(row["id"])
        if not d:
            continue
        used -= size(d)
        shutil.rmtree(d)
        conn.execute("UPDATE incidents SET clips_gone = 1 WHERE id = ?", (row["id"],))
        removed += 1
    conn.commit()
    return removed


@router.post("/security/ingest")
async def ingest(request: Request, conn=Depends(get_conn)):
    _check_token(request)
    try:
        iid = save_incident(conn, await request.json())
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"id": iid})


@router.put("/security/ingest/{iid:int}/{name}")
async def ingest_clip(request: Request, iid: int, name: str, conn=Depends(get_conn)):
    """One video for an incident: the fridge clip or one of its room-camera clips."""
    _check_token(request)
    row = conn.execute("SELECT clip, context_json FROM incidents WHERE id = ?", (iid,)).fetchone()
    if not row or name not in [row["clip"]] + json.loads(row["context_json"]):
        raise HTTPException(404)
    path = clip_path(iid, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    got, head = 0, b""
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"   # its own name: two uploads of the same clip can overlap
    with open(tmp, "wb") as f:
        async for chunk in request.stream():
            got += len(chunk)
            if got > CLIP_MAX:
                f.close()
                os.remove(tmp)
                raise HTTPException(413, "That video is too big.")
            if len(head) < 12:
                head += chunk[:12]
            f.write(chunk)
    if head[4:8] != b"ftyp":   # every MP4 starts with an ftyp box
        os.remove(tmp)
        raise HTTPException(400, "That isn't an MP4 video.")
    os.replace(tmp, path)
    conn.execute("UPDATE incidents SET clips_gone = 0 WHERE id = ?", (iid,))
    conn.commit()
    await run_in_threadpool(prune_clips, conn)
    return JSONResponse({"ok": True, "bytes": got})


# ---- the cameras' switch (Home Assistant) ----------------------------------------------------

def ha_on():
    return bool(config.HA_URL and config.HA_TOKEN)


def _ha(method, path, body=None):
    with httpx.Client(timeout=8) as c:
        r = c.request(method, config.HA_URL + path, json=body, headers={"Authorization": f"Bearer {config.HA_TOKEN}"})
        r.raise_for_status()
        return r.json()


def alarm_state():
    """('Armed' / 'Disarmed' / ..., raw state), or (None, reason) when Home Assistant can't say."""
    if not ha_on():
        return None, "not set up"
    try:
        state = _ha("GET", f"/api/states/{config.HA_ALARM_ENTITY}").get("state", "")
    except httpx.HTTPError as e:
        log.warning("Home Assistant: %s", e)
        return None, "Home Assistant didn't answer"
    if state in ALARM_STATES:
        return ALARM_STATES[state], state
    return None, "the cameras are offline" if state == "unavailable" else f"Home Assistant says {state or 'nothing'}"


def set_alarm(arm):
    service = "alarm_arm_away" if arm else "alarm_disarm"
    _ha("POST", f"/api/services/alarm_control_panel/{service}", {"entity_id": config.HA_ALARM_ENTITY})
    try:   # Blink takes the change at once, but Home Assistant only re-reads it every few minutes: ask it now
        _ha("POST", "/api/services/homeassistant/update_entity", {"entity_id": config.HA_ALARM_ENTITY})
    except httpx.HTTPError:
        pass


def shown_state(conn, status, raw):
    """What the page shows: right after the switch was pressed, "Arming" / "Disarming" until Home
    Assistant catches up (or PENDING_SECONDS pass), instead of the state from before the press."""
    pending = json.loads(get_setting(conn, "cameras_pending", "") or "null")
    if not pending:
        return status, raw
    want = "Armed" if pending["arm"] else "Disarmed"
    if status == want or time.time() - pending["at"] > PENDING_SECONDS:
        set_setting(conn, "cameras_pending", "")
        conn.commit()
        return status, raw
    return ("Arming" if pending["arm"] else "Disarming"), "Blink can take a few minutes to confirm"


@router.post("/security/arm")
async def arm(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    return await _switch(request, True, who, conn)


@router.post("/security/disarm")
async def disarm(request: Request, who=Depends(staff), conn=Depends(get_conn)):
    return await _switch(request, False, who, conn)


async def _switch(request, on, who, conn):
    if not ha_on():
        return back(request, "/security", "The camera switch isn't set up yet (FIZZY_HA_TOKEN).", "bad")
    try:
        await run_in_threadpool(set_alarm, on)
    except httpx.HTTPError as e:
        log.warning("Home Assistant: %s", e)
        return back(request, "/security", "Couldn't reach Home Assistant. Nothing changed; try again or use the Blink app.", "bad")
    audit(conn, who, "cameras_armed" if on else "cameras_disarmed")
    set_setting(conn, "cameras_pending", json.dumps({"arm": on, "at": time.time()}))
    conn.commit()
    return back(request, "/security", "Arming the cameras. Blink can take a few minutes to confirm." if on else
                "Disarming the cameras. Blink can take a few minutes to confirm. Remember to arm them when you close.")


# ---- the pages -------------------------------------------------------------------------------

def _incident(row):
    d = dict(row)
    d["context"] = json.loads(d.pop("context_json") or "[]")
    d["tag"] = score_class(d["score"])
    try:
        d["when"] = datetime.strptime(d["happened_at"], "%Y-%m-%d %H:%M:%S").strftime("%a %-d %b, %-I:%M %p")
    except ValueError:
        d["when"] = d["happened_at"]
    folder = os.path.join(incidents_dir(), str(d["id"]))
    d["videos"] = [n for n in [d["clip"]] + d["context"] if os.path.isfile(os.path.join(folder, n))]
    return d


@router.get("/security", response_class=HTMLResponse)
async def security_page(request: Request, show: str = "open", who=Depends(staff), conn=Depends(get_conn)):
    status, raw = shown_state(conn, *await run_in_threadpool(alarm_state))
    where = "WHERE verdict IS NULL" if show == "open" else ""
    rows = conn.execute(f"SELECT * FROM incidents {where} ORDER BY happened_at DESC, id DESC LIMIT 200").fetchall()
    counts = conn.execute("SELECT COUNT(*) AS n, SUM(verdict IS NULL) AS open, SUM(verdict = 'theft') AS theft,"
                          " SUM(verdict IS NULL AND score >= 7) AS high FROM incidents").fetchone()
    return render(request, "security.html", status=status, raw=raw, ha_on=ha_on(), show=show,
                  incidents=[_incident(r) for r in rows], counts=dict(counts), verdicts=VERDICTS)


@router.get("/security/incidents/{iid:int}", response_class=HTMLResponse)
def incident_page(request: Request, iid: int, who=Depends(staff), conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return render(request, "security_incident.html", i=_incident(row), verdicts=VERDICTS)


@router.post("/security/incidents/{iid:int}/verdict")
def incident_verdict(request: Request, iid: int, verdict: str = Form(""), who=Depends(staff), conn=Depends(get_conn)):
    if verdict and verdict not in VERDICTS:
        raise HTTPException(400, "Pick OK or theft.")
    if not conn.execute("SELECT 1 FROM incidents WHERE id = ?", (iid,)).fetchone():
        raise HTTPException(404)
    conn.execute("UPDATE incidents SET verdict = ?, verdict_by = ?, verdict_at = ? WHERE id = ?",
                 (verdict or None, who if verdict else None, now_iso() if verdict else None, iid))
    audit(conn, who, "incident_verdict", None, f"#{iid}: {verdict or 'reopened'}")
    conn.commit()
    msg = {"ok": "Marked OK.", "theft": "Marked as theft.", "": "Back on the open list."}[verdict]
    return back(request, f"/security/incidents/{iid}", msg)


@router.get("/security/clips/{iid:int}/{name}")
def clip_file(iid: int, name: str, who=Depends(staff)):
    path = clip_path(iid, name)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600",
                                                               "X-Content-Type-Options": "nosniff"})
