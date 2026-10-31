"""Long jobs (a Square sync, a re-sync, importing history) run in the background, so the page that
started them answers straight away instead of waiting (Cloudflare gives up on a request after
about 100 seconds, which left the page "loading" for ever). One job at a time; every staff page
shows a bar while it runs and says how it went when it's done (static/app.js polls /api/job).
"""
import threading
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from . import db
from .web import staff

router = APIRouter()
_lock = threading.Lock()
_job = {"name": None, "running": False, "started": 0, "finished": 0, "message": "", "ok": True, "by": None}


def current():
    return dict(_job)


def start(name, fn, who):
    """Run fn(conn) -> message in a thread. Returns False if a job is already running."""
    with _lock:
        if _job["running"]:
            return False
        _job.update(name=name, running=True, started=time.time(), finished=0, message="", ok=True, by=who)

    def run():
        conn = db.connect()
        try:
            msg, ok = fn(conn), True
        except Exception as e:  # say what went wrong on the page, don't lose the thread silently
            conn.rollback()
            msg, ok = f"Didn't finish: {e}", False
        finally:
            conn.close()
        _job.update(running=False, finished=time.time(), message=msg or "Done.", ok=ok)
    threading.Thread(target=run, daemon=True, name=f"job:{name}").start()
    return True


@router.get("/api/job")
def job_status(who=Depends(staff)):
    j = current()
    # A finished job's result is shown for 10 minutes, then forgotten.
    if not j["running"] and (not j["finished"] or time.time() - j["finished"] > 600):
        return JSONResponse({"running": False, "name": None}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"running": j["running"], "name": j["name"], "message": j["message"], "ok": j["ok"],
                         "seconds": int((j["finished"] or time.time()) - j["started"]), "finished": j["finished"]},
                        headers={"Cache-Control": "no-store"})
