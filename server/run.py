#!/usr/bin/env python3
"""Launch the local Moxie parent-app server.

    python server/run.py            # listens on 0.0.0.0:8080
    HOST=127.0.0.1 PORT=9000 python server/run.py

Then open http://<this-machine-ip>:8080 from any device on your LAN/Tailscale.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Moxie setup server → http://{host}:{port}  (open this from your phone)")
    uvicorn.run("moxie_server.main:app", host=host, port=port, reload=False)
