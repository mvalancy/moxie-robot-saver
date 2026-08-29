#!/usr/bin/env python3
"""Static server for sim/web with no-cache headers (so the browser always shows
the current model during development). Serves on 127.0.0.1:8080 by default."""
import http.server, socketserver, os, sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()
    def log_message(self, *a): pass
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
print(f"[serve] sim/web on http://127.0.0.1:{PORT}  (no-cache)", flush=True)
Server(("127.0.0.1", PORT), H).serve_forever()
