"""Install-to-home-screen (PWA) bits for both the staff app and the student site.

The service worker never stores pages: they hold students' details, and showing stale points or
tabs would be worse than nothing. It caches the static files (fonts, CSS, icons) so the app opens
quickly, and shows a friendly "you're offline" page instead of the browser's dinosaur.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .web import templates

router = APIRouter()

SW = """// Fizzy service worker (version %(v)s)
const CACHE = "fizzy-%(v)s";
const OFFLINE = "/static/offline.html?v=%(v)s";
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll([OFFLINE, "/static/icon-192.png"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname === "/static/icon-192.png") { e.respondWith(caches.match(req).then(hit => hit || fetch(req))); return; }
  if (req.mode === "navigate") {  // pages: always fresh from Fizzy; offline page if it can't be reached
    e.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
  } else if (url.pathname.startsWith("/static/") && url.searchParams.get("v")) {  // versioned, never changes
    e.respondWith(caches.open(CACHE).then(c => c.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) c.put(req, res.clone());
      return res;
    }))));
  }
});
"""


def _icons(maskable):
    v = templates.env.globals["V"]
    return [
        {"src": f"/static/icon-192.png?v={v}", "sizes": "192x192", "type": "image/png"},
        {"src": f"/static/icon-512.png?v={v}", "sizes": "512x512", "type": "image/png"},
        {"src": f"/static/{maskable}?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]


def _manifest(data):
    return Response(json.dumps(data), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest")
def staff_manifest():
    return _manifest({
        "name": "Fizzy", "short_name": "Fizzy", "id": "/", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#15171B", "theme_color": "#15171B",
        "icons": _icons("icon-maskable-staff.png"),
        "shortcuts": [{"name": "Customers", "url": "/customers"}, {"name": "Rewards", "url": "/rewards"}],
    })


@router.get("/me/manifest.webmanifest")
def student_manifest(request: Request):
    base = "/" if getattr(request.state, "public", False) else "/me"
    return _manifest({
        "name": "My Cooked Coke", "short_name": "Cooked Coke", "id": base, "start_url": base, "scope": base,
        "display": "standalone", "background_color": "#FFF7EE", "theme_color": "#FFF7EE",
        "icons": _icons("icon-maskable-me.png"),
    })


@router.get("/sw.js")
@router.get("/me/sw.js")  # the student host maps /sw.js here
def service_worker():
    return Response(SW % {"v": templates.env.globals["V"]}, media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
