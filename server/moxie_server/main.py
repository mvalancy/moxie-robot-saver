"""Local Moxie parent-app server: a clean-room `client-service-api.embodied.com`
(`docs/architecture/rest-api-contract.md`) plus `/local/*` helpers for our web client,
the supervisor-backed console cards, and the static client at `/`.

Run:  python server/run.py   (or: uvicorn moxie_server.main:app)
"""
from __future__ import annotations
import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import db
from .routes import account, console, content, pairing, robots

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")

app = FastAPI(title="Local Moxie Parent-App Server")
db.init()


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """The client has no build step and no hashed filenames, so never cache it."""
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


for _module in (account, robots, pairing, console, content):
    app.include_router(_module.router)

if os.path.isdir(STATIC):       # last: the static mount at / would shadow any route after it
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
