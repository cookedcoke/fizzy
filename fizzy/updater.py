"""The Settings → Updates panel. Fizzy never updates itself (that would mean giving it control of
Docker, which is control of the whole PC). Instead the owner's "Update now" leaves a note in the
data folder, and deploy/update.sh (or update.ps1 on Windows), running on the home PC with
--watch, sees it, pulls, rebuilds and checks Fizzy came back, rolling back if not. The script
writes how it went to update-status.json, which this page reads.
"""
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config, db
from .web import back, render, staff

router = APIRouter()

VERSION = os.environ.get("FIZZY_VERSION", "dev")
WATCHER_STALE = 15 * 60  # the script checks in every minute or so; older than this and it isn't running


def _dir():
    # Its own folder (./updates on the PC), shared with the update script; not ./data, whose
    # ownership the container takes over at start.
    return Path(os.environ.get("FIZZY_UPDATES_DIR") or Path(config.DB_PATH).resolve().parent)


def status():
    """What the updater on the home PC last said, plus whether it's still running."""
    try:
        s = json.loads((_dir() / "update-status.json").read_text())
    except (OSError, ValueError):
        s = {}
    s["watching"] = time.time() - float(s.get("seen", 0) or 0) < WATCHER_STALE
    s["requested"] = (_dir() / "update-requested").exists()
    s["version"] = VERSION
    s["log"] = ""
    if s.get("state") == "failed":
        try:
            s["log"] = (_dir() / "last-failure.log").read_text(errors="replace")[-4000:]
        except OSError:
            pass
    return s


@router.post("/settings/update")
def request_update(request: Request, who=Depends(staff)):
    s = status()
    if not s["watching"]:
        return back(request, "/settings#updates", "The updater isn't running on the home PC, so nothing would happen. See the README.")
    (_dir() / "update-requested").write_text(f"{who} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    conn = db.connect()
    try:
        db.audit(conn, who, "update_requested", None, "")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/settings/update/progress?since={int(time.time())}", status_code=303)


@router.get("/settings/update/progress", response_class=HTMLResponse)
def progress(request: Request, since: int = 0, who=Depends(staff)):
    return render(request, "update_progress.html", since=since or int(time.time()), version=VERSION)


@router.get("/settings/update/status")
def progress_status(who=Depends(staff)):
    """Polled by the progress page. While Fizzy restarts this doesn't answer, and the page knows that."""
    s = status()
    return JSONResponse({k: s.get(k) for k in ("state", "step", "message", "from", "to", "started", "requested", "watching", "version", "log")},
                        headers={"Cache-Control": "no-store"})
