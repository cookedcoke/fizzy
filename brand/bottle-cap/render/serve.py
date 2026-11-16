import base64, http.server, os, re, sys
class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        name = re.sub(r"[^a-z0-9_-]", "", self.path.split("name=")[-1].lower()) or "out"
        data = self.rfile.read(int(self.headers["Content-Length"])).decode()
        open(f"{name}.png", "wb").write(base64.b64decode(data.split(",", 1)[1]))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", 8766), H).serve_forever()
