"""The loopback-only HTTP status/control server the parent console talks to.

Binds 127.0.0.1 only; the console reaches it through `status_proxy.py`. Every route is a
thin adapter onto one runtime method, which owns the behaviour and the refusal wording.

GET     /status /conn /permits /voice /brain /content /content/export
        /telemetry /safety /schedule /config /memory /telehealth   (`?device_id=…`)
POST    /config /safety /permits /telehealth /voice /voice/test /brain /preview /wakeup
        /memory /content/{review,import,undo,item,render}
DELETE  /memory /telemetry   (erases are never policy-gated)
"""
from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def _first(q: dict, key: str, default: str = "") -> str:
    return (q.get(key) or [default])[0]


def _int_param(q: dict, key: str, default: int) -> int:
    try:
        return int(_first(q, key, str(default)))
    except ValueError:
        return default


def _flag(q: dict, key: str) -> bool:
    return _first(q, key, "0") not in ("", "0", "false")


_CONTENT_POSTS = ("/content/review", "/content/import", "/content/undo", "/content/item",
                  "/content/render")


def _code(out: dict, otherwise: int = 400) -> int:
    """200 on ok; 404 for an unknown device; else `otherwise`."""
    if out.get("ok"):
        return 200
    return 404 if "unknown device_id" in str(out.get("error")) else otherwise


class _Handler(BaseHTTPRequestHandler):
    rt = None                              # the MoxieRuntime, bound per server

    def log_message(self, *a):             # silence
        pass

    def _json_out(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(body)

    def _not_found(self):
        self.send_response(404); self.end_headers()

    def _raw(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b"{}"

    def _query(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    def _body(self, *, strict: bool = False):
        """The JSON request body (`{}` when empty). Raises on malformed JSON and, when
        `strict`, on anything but an object."""
        body = json.loads(self._raw() or b"{}") or {}
        if strict and not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        return body

    def _refuse(self, e: Exception, *, reason: bool = True):
        out = {"ok": False, "error": str(e)}
        if reason:
            out["reason"] = str(e)
        return self._json_out(out, 400)

    def _robot_config(self, device_id: str) -> dict:
        rt = self.rt
        return {"ok": True, "scope": "robot", "device_id": device_id,
                "config_overrides": rt._config_overrides.get(device_id, {}),
                "config_effective": rt.effective_config(device_id)}

    # ---- GET ----
    def do_GET(self):
        rt = self.rt
        u = urlparse(self.path)
        q = parse_qs(u.query)
        device_id = _first(q, "device_id")
        views = {                          # always 200
            "/status": lambda: rt.status_snapshot(),
            "/conn": lambda: rt.conn_view(limit=_int_param(q, "limit", 40)),
            "/permits": lambda: rt.permits_view(),
            "/voice": lambda: rt.voice_view(refresh=_flag(q, "refresh")),
            "/brain": lambda: rt.brain_view(),
            "/content": lambda: rt.content_view(),
        }
        robot_views = {                    # 404 unless `ok` (an unknown device)
            "/telemetry": lambda: rt.telemetry_view(device_id,
                                                    limit=_int_param(q, "limit", 20),
                                                    days=_int_param(q, "days", 7)),
            "/safety": lambda: rt.safety_view(device_id, limit=_int_param(q, "limit", 20)),
            "/schedule": lambda: rt.schedule_view(device_id, refresh=_flag(q, "refresh")),
            "/memory": lambda: rt.memory_view(device_id),
            "/telehealth": lambda: rt.telehealth_view(device_id),
        }
        if u.path in views:
            return self._json_out(views[u.path]())
        if u.path in robot_views:
            out = robot_views[u.path]()
            return self._json_out(out, 200 if out.get("ok") else 404)
        if u.path == "/config":
            if _first(q, "scope", "robot") == "fleet":
                return self._json_out({"ok": True, "scope": "fleet",
                                       "fleet_config": rt.fleet_config()})
            if device_id not in rt.robots:
                return self._json_out(
                    {"ok": False, "error": f"unknown device_id {device_id!r}"}, 404)
            return self._json_out({**self._robot_config(device_id),
                                   "fleet_config": rt.fleet_config()})
        if u.path == "/content/export":
            # `?items=kind:key,…&name=…&id=…` -> the pack JSON itself; no items = all.
            keys = [k for part in (q.get("items") or [])
                    for k in part.split(",") if k.strip()]
            try:
                pack = rt.content_export(
                    keys, name=_first(q, "name"), pack_id=_first(q, "id"),
                    details=_first(q, "details"), author=_first(q, "author"))
            except Exception as e:
                return self._refuse(e)
            return self._json_out(pack)
        self._not_found()

    # ---- DELETE ----
    def do_DELETE(self):
        """`/memory?device_id=…[&namespace=…[&item=…]]` (finest cut given) or
        `/telemetry?device_id=…` (the whole activity record, `erase_telemetry`)."""
        u = urlparse(self.path)
        if u.path == "/memory":
            return self._memory_write(u.query)
        if u.path == "/telemetry":
            device_id = _first(parse_qs(u.query), "device_id")
            if not device_id:
                return self._json_out({"ok": False, "error": "device_id is required"}, 400)
            try:
                out = self.rt.erase_telemetry(device_id)
            except Exception as e:
                return self._json_out({"ok": False, "device_id": device_id,
                                       "error": str(e)}, 400)
            return self._json_out(out, 200 if out.get("ok") else 404)
        self._not_found()

    # ---- POST ----
    def do_POST(self):
        """Parent-console writes; each route documents itself on the runtime method."""
        u = urlparse(self.path)
        if u.path in _CONTENT_POSTS:
            return self._content(u.path)
        if u.path in ("/voice", "/voice/test"):
            return self._voice(u.path, u.query)
        route = {"/memory": self._memory_write, "/telehealth": self._telehealth,
                 "/preview": self._preview, "/wakeup": self._wakeup, "/brain": self._brain,
                 "/permits": self._permits, "/safety": self._safety,
                 "/config": self._config}.get(u.path)
        return route(u.query) if route else self._not_found()

    def _preview(self, query):
        """`{"text", "speak", "icons", "sfx"}`: rehearse one line (expressiveness.md §2.4)."""
        try:
            body = self._body()
        except Exception as e:
            return self._refuse(e, reason=False)
        device_id = _first(parse_qs(query), "device_id") or str(body.get("device_id") or "")
        out = self.rt.preview(device_id, body.get("text"), speak=bool(body.get("speak")),
                              icons=bool(body.get("icons")), sfx=bool(body.get("sfx")))
        return self._json_out(out, _code(out))

    def _wakeup(self, query):
        out = self.rt.wake_robot(_first(parse_qs(query), "device_id"))
        return self._json_out(out, _code(out, 409))

    def _brain(self, query):
        """`?device_id=…` or `?scope=fleet` with `{"brain": name|null}` (`brain_update`)."""
        q = parse_qs(query)
        try:
            body = self._body()
        except Exception as e:
            return self._refuse(e, reason=False)
        out = self.rt.brain_update(body, device_id=_first(q, "device_id"),
                                   scope=_first(q, "scope", "robot"))
        return self._json_out(out, _code(out))

    def _permits(self, _query):
        """`{device_id, permitted, label}` or `{allow_unverified_bots}`."""
        rt = self.rt
        try:
            body = self._body()
            if "allow_unverified_bots" in body:
                out = rt.set_allow_unverified_bots(bool(body["allow_unverified_bots"]))
            elif body.get("device_id"):
                out = rt.set_permit(body["device_id"],
                                    permitted=bool(body.get("permitted", True)),
                                    label=body.get("label") or "")
            else:
                raise ValueError("expected {device_id, permitted, label} "
                                 "or {allow_unverified_bots}")
        except Exception as e:
            return self._refuse(e, reason=False)
        return self._json_out(out)

    def _safety(self, query):
        """`{"event_id": "sfe-…"}` (or `{}` / "all"): mark reviewed."""
        try:
            out = self.rt.acknowledge_safety(_first(parse_qs(query), "device_id"),
                                             self._body().get("event_id"))
        except Exception as e:
            return self._refuse(e, reason=False)
        return self._json_out(out, 200 if out.get("ok") else 404)

    def _config(self, query):
        """Whitelisted overrides for one robot, or `?scope=fleet` for the appliance-wide
        defaults (a per-robot override still wins); both re-push."""
        from moxie_sdk.cloud_config import sanitize_config_overrides
        rt = self.rt
        q = parse_qs(query)
        device_id = _first(q, "device_id")
        try:
            overrides = sanitize_config_overrides(json.loads(self._raw() or b"{}"))
            if _first(q, "scope", "robot") == "fleet":
                fleet = rt.update_fleet_config(**overrides)
                out = {"ok": True, "scope": "fleet", "applied": overrides,
                       "fleet_config": fleet, "robots": list(rt.robots)}
            else:
                if not device_id or device_id not in rt.robots:
                    raise ValueError(f"unknown device_id {device_id!r}")
                rt.update_config(device_id, **overrides)
                out = {**self._robot_config(device_id), "applied": overrides}
        except Exception as e:
            return self._refuse(e, reason=False)
        return self._json_out(out)

    # ---- POST/DELETE helpers ----
    def _memory_write(self, query):
        """Erase (`item` < `namespace` < everything) or correct one item in place
        (`{"edit": {"namespace", "item", "text"}}`). Shared by DELETE and POST /memory."""
        q = parse_qs(query)
        device_id = _first(q, "device_id")
        namespace = _first(q, "namespace")
        item = _first(q, "item")
        body = {}
        if not namespace or not item:
            try:
                body = self._body(strict=True)
            except Exception:
                body = {}
        edit = body.get("edit") if isinstance(body.get("edit"), dict) else None
        if edit is None:
            namespace = namespace or body.get("namespace") or body.get("erase") or ""
            item = item or body.get("item") or ""
        try:
            if edit is not None:
                out = self.rt.edit_memory_item(device_id, edit.get("namespace") or namespace,
                                               edit.get("item") or item, edit.get("text"))
            else:
                out = self.rt.erase_memory(device_id, namespace or None, item or None)
            code = 200 if out.get("ok") else 404
        except Exception as e:
            return self._refuse(e, reason=False)
        return self._json_out(out, code)

    def _telehealth(self, query):
        """`{"action": enable|disable|start|end|state|speak|interrupt}`; `speak` also takes
        `text`, `mood`, `intensity`, `gesture`. A blocked operator line is a 400 with the
        reason and nothing is spoken (backlog/telehealth.md §2.3)."""
        rt = self.rt
        device_id = _first(parse_qs(query), "device_id")
        try:
            body = self._body(strict=True)
            action = str(body.get("action") or "").strip().lower()
            if action in ("enable", "disable"):
                out = rt.telehealth_enable(device_id, action == "enable")
            elif action in ("start", "start_session"):
                out = rt.telehealth_session(device_id, "START_SESSION")
            elif action in ("end", "end_session"):
                out = rt.telehealth_session(device_id, "END_SESSION")
            elif action in ("state", "update_state"):
                out = rt.telehealth_session(device_id, "UPDATE_STATE")
            elif action in ("speak", "play_output", "say"):
                out = rt.telehealth_speak(
                    device_id, body.get("text") or body.get("speech") or "",
                    mood=body.get("mood"), intensity=body.get("intensity"),
                    gesture=body.get("gesture"))
            elif action == "interrupt":
                out = rt.telehealth_interrupt(device_id)
            else:
                raise ValueError("expected action: enable, disable, start, end, state, "
                                 "speak or interrupt")
        except Exception as e:
            return self._refuse(e)
        return self._json_out(out, _code(out))

    def _voice(self, path: str, query: str):
        """`POST /voice` `{"speech": …, "listening": …}` (an option id, `{engine, model}`,
        or null for the default) persists and swaps live; a pick not currently offered is
        a 400. `POST /voice/test?device_id=…` `{"text"}` speaks through the installed engine."""
        try:
            body = self._body(strict=True)
            if path == "/voice/test":
                device_id = (parse_qs(query).get("device_id")
                             or [body.get("device_id") or ""])[0]
                out = self.rt.voice_test(device_id, body.get("text") or "")
                code = _code(out)
            else:
                out = self.rt.voice_update(body)
                code = 200 if out.get("ok") else 400
        except Exception as e:
            return self._refuse(e)
        return self._json_out(out, code)

    def _content(self, path: str):
        """Packs: `/content/review` (the pack; writes nothing), `/content/import`
        (`{"pack", "accept", "expect_digest"}`; 409 on a digest mismatch), `/content/undo`.
        Authoring: `/content/item` (`{"kind", "data", "phrases", "local_rev"}`) and
        `/content/render` (`{"kind", "data", "context"}`; no brain). A body over
        `MOXIE_PACK_MAX_BYTES` is a 413, refused before it is buffered."""
        rt = self.rt
        cap = rt.pack_max_bytes()
        length = int(self.headers.get("Content-Length") or 0)
        if length > cap:
            return self._json_out(
                {"ok": False, "error": f"pack is larger than {cap} bytes",
                 "reason": "That file is too big to be a content pack.",
                 "max_bytes": cap}, 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            if path == "/content/undo":
                out = rt.content_undo()
                return self._json_out(out, 200 if out.get("ok") else 404)
            if path == "/content/review":
                return self._json_out(rt.content_review(raw), 200)
            body = json.loads(raw or b"{}") or {}
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            if path == "/content/render":
                out = rt.content_render(body)
                return self._json_out(out, 200 if out.get("ok") else 400)
            if path == "/content/item":
                # Validation lives here, not in the console proxy, so a direct curl
                # cannot skip it (brief R6).
                out = rt.content_save_item(body)
            else:
                out = rt.content_import(body.get("pack"), body.get("accept") or [],
                                        str(body.get("expect_digest") or ""))
            if out.get("ok"):
                return self._json_out(out, 200)
            return self._json_out(out, 409 if out.get("conflict") else 400)
        except Exception as e:
            return self._refuse(e)


class StatusServerMixin:
    def _start_status_server(self, port):
        """Serve the status/control API on 127.0.0.1:`port` in a daemon thread."""
        handler = type("StatusHandler", (_Handler,), {"rt": self})
        try:
            srv = HTTPServer(("127.0.0.1", port), handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            print(f"[runtime] status endpoint on http://127.0.0.1:{port}/status")
        except Exception as e:
            print(f"[runtime] status server failed: {e}")
