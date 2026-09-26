"""Server-side calls to the MQTT supervisor's localhost status server.

Every `/local/*` console card is a thin proxy: the browser never talks to the supervisor
(no CORS, no supervisor port exposed), and the supervisor owns validation, persistence
and the protocol. A supervisor that is down is a 503 carrying the card's own shape,
never a 500; a refusal keeps the supervisor's status code so the card can say why.
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional
from urllib.parse import quote

from fastapi.responses import JSONResponse

STATUS_URL = os.environ.get("MOXIE_SUPERVISOR_STATUS", "http://127.0.0.1:8930/status")
UNREACHABLE = "supervisor not reachable"


def url(path: str) -> str:
    return STATUS_URL.rsplit("/status", 1)[0] + path


def device_query(path: str, device_id: str, **params) -> str:
    """`path?device_id=…&k=v` with every value URL-quoted; `None` params are dropped."""
    q = f"device_id={quote(device_id)}"
    for k, v in params.items():
        if v is not None:
            q += f"&{k}={quote(str(v))}"
    return f"{path}?{q}"


def call(method: str, path: str, data: Optional[bytes] = None, timeout: float = 3,
         device_id: Optional[str] = None) -> tuple:
    """`(payload, status)` from one supervisor call. Never raises.

    A refusal is its own body and code (a non-JSON body becomes `{ok:false, error}`);
    an unreachable supervisor is `({ok:false, error, detail}, 503)`."""
    req = urllib.request.Request(url(path), data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}"), 200
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return json.loads(raw or "{}"), e.code
        except ValueError:
            return {"ok": False, "error": raw[:200] or f"supervisor said {e.code}"}, e.code
    except Exception as e:
        out = {"ok": False, "error": UNREACHABLE, "detail": str(e)}
        if device_id is not None:
            out["device_id"] = device_id
        return out, 503


def reply(payload, code: int):
    """The payload as-is on 200, else a JSONResponse carrying the supervisor's code."""
    return payload if code == 200 else JSONResponse(status_code=code, content=payload)


def proxy(method: str, path: str, normalize: Callable = lambda p: p, *,
          data: Optional[bytes] = None, timeout: float = 3,
          device_id: Optional[str] = None):
    """One card's round trip: call, normalize the body either way, keep the code."""
    payload, code = call(method, path, data, timeout, device_id)
    return reply(normalize(payload), code)


def post_json(path: str, payload: dict, timeout: float = 3) -> tuple:
    return call("POST", path, json.dumps(payload).encode(), timeout)


def fetch_status() -> dict:
    """The supervisor's `/status` snapshot; `{ok:false, robots:[], recent:[]}` if down."""
    out, code = call("GET", "/status", timeout=2)
    if code != 200:
        return {"ok": False, "error": UNREACHABLE, "detail": out.get("detail") or
                out.get("error"), "robots": [], "recent": []}
    return out
