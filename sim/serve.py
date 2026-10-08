#!/usr/bin/env python3
"""Dev server for sim/web. No-cache headers + automatic cache-busting: rewrites
index.html on the fly so <script>/<link> URLs for local JS/CSS get a ?t=<mtime>
query, forcing the browser to refetch whenever a file changes (defeats ES-module
caching). Serves 127.0.0.1:8080 by default."""
import http.server, socketserver, os, sys, re
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
os.chdir(WEB)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

def bust(html: str) -> str:
    """Stamp every local <script src>/<link href> .js/.css with ?t=<mtime> (replacing any
    ?v= the page ships), so an edited file is refetched even by an ES-module cache."""
    def stamp(m):
        p = os.path.join(WEB, m.group(2))
        t = str(int(os.path.getmtime(p))) if os.path.exists(p) else "0"
        return f'{m.group(1)}="{m.group(2)}?t={t}"'
    return re.sub(r'\b(src|href)="((?!https?:|//)[\w./-]+\.(?:js|css))(?:\?[^"]*)?"', stamp, html)

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        # Serve any HTML page with on-the-fly cache-busting. "/" is the hub (index.html).
        rel = "index.html" if path == "/" else path.lstrip("/")
        # "/sim" is "/sim.html", as Cloudflare Pages serves it: the hub links /sim directly.
        if rel and not os.path.splitext(rel)[1] and os.path.isfile(os.path.join(WEB, rel + ".html")):
            rel += ".html"
        if rel.endswith(".html") and os.path.isfile(os.path.join(WEB, rel)):
            body = bust(open(os.path.join(WEB, rel), encoding="utf-8").read()).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        return super().do_GET()
    def log_message(self, *a): pass

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True; daemon_threads = True

print(f"[serve] sim/web on http://127.0.0.1:{PORT}  (no-cache + auto cache-bust)", flush=True)
Server(("127.0.0.1", PORT), H).serve_forever()
