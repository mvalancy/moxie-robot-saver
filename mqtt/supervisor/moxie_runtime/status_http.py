"""The loopback-only HTTP status/control server the parent console talks to."""
from __future__ import annotations
import threading



class StatusServerMixin:
    def _start_status_server(self, port):
        """Tiny HTTP status endpoint for the web UI's connection monitor."""
        import json as _json
        from http.server import BaseHTTPRequestHandler, HTTPServer
        rt = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _json_out(self, payload, code=200):
                body = _json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                """GET /status → the console snapshot;
                GET /conn?limit=N → 🔌 the broker connection's durable history: connects,
                disconnects, CONNACK refusals, gap durations, dropped publishes and store
                lock timeouts, rolled up beside the live connection scalars;
                GET /telemetry?device_id=…&limit=N&days=D → that robot's stored telemetry
                Packets rolled up for the insights view, plus D days of durable daily
                history and the retention window behind it;
                GET /schedule?device_id=… → the planned day + why each activity is on it;
                GET /telehealth?device_id=… → 🎭 puppet mode + the live transcript;
                GET /voice → 🎚️ the speech/listening pickers: what this appliance can
                use, what is in force and what the default would be (fleet-level);
                GET /brain → 🧠 the brain picker: every brain this box can run, the house
                rule, and which one answers each robot (with the layer that chose it);
                GET /content → 📦 the installed content inventory + the pack ledger;
                GET /content/export?items=… → one pack file built from those items;
                GET /permits → the device allowlist + who is pending.
                Localhost-only (the server binds 127.0.0.1)."""
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                if u.path == "/status":
                    return self._json_out(rt.status_snapshot())
                if u.path == "/conn":
                    # 🔌 The broker connection's durable history (§8 P1): P0's live
                    # scalars beside the ring of what actually happened, so "is it down
                    # now" and "has it been flapping" are one request, not a guess.
                    q = parse_qs(u.query)
                    try:
                        limit = int((q.get("limit") or ["40"])[0])
                    except ValueError:
                        limit = 40
                    return self._json_out(rt.conn_view(limit=limit))
                if u.path == "/telemetry":
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    try:
                        limit = int((q.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    try:                       # `days` of daily history (durable ⑤)
                        days = int((q.get("days") or ["7"])[0])
                    except ValueError:
                        days = 7
                    out = rt.telemetry_view(device_id, limit=limit, days=days)
                    return self._json_out(out, 200 if out.get("ok") else 404)
                if u.path == "/safety":
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    try:
                        limit = int((q.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    out = rt.safety_view(device_id, limit=limit)
                    return self._json_out(out, 200 if out.get("ok") else 404)
                if u.path == "/schedule":
                    # The day this robot was planned, with the "why this activity today"
                    # line behind every entry (audit §4.2 BEYOND #7). Read-only.
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    refresh = (q.get("refresh") or ["0"])[0] not in ("", "0", "false")
                    out = rt.schedule_view(device_id, refresh=refresh)
                    return self._json_out(out, 200 if out.get("ok") else 404)
                if u.path == "/permits":
                    return self._json_out(rt.permits_view())
                if u.path == "/config":
                    q = parse_qs(u.query)
                    scope = (q.get("scope") or ["robot"])[0]
                    if scope == "fleet":
                        return self._json_out({"ok": True, "scope": "fleet",
                                               "fleet_config": rt.fleet_config()})
                    device_id = (q.get("device_id") or [""])[0]
                    if device_id not in rt.robots:
                        return self._json_out(
                            {"ok": False, "error": f"unknown device_id {device_id!r}"}, 404)
                    return self._json_out({
                        "ok": True, "scope": "robot", "device_id": device_id,
                        "fleet_config": rt.fleet_config(),
                        "config_overrides": rt._config_overrides.get(device_id, {}),
                        "config_effective": rt.effective_config(device_id)})
                if u.path == "/memory":
                    # BEYOND #4's floor: what Moxie remembers about this child, by
                    # namespace, with the provenance of every entry.
                    q = parse_qs(u.query)
                    out = rt.memory_view((q.get("device_id") or [""])[0])
                    return self._json_out(out, 200 if out.get("ok") else 404)
                if u.path == "/telehealth":
                    # 🎭 "Be Moxie" (audit ADOPT #7): whether puppet mode is on, the open
                    # session, the state the ROBOT reported (empty = never reported), the
                    # bedtime warning and the live transcript.
                    q = parse_qs(u.query)
                    out = rt.telehealth_view((q.get("device_id") or [""])[0])
                    return self._json_out(out, 200 if out.get("ok") else 404)
                if u.path == "/voice":
                    # 🎚️ The picker: every speech/listening option this appliance can
                    # really use, which one is in force, which is the default, and whether
                    # the gateway listing is still on its way. Fleet-level — no device_id.
                    q = parse_qs(u.query)
                    refresh = (q.get("refresh") or ["0"])[0] not in ("", "0", "false")
                    return self._json_out(rt.voice_view(refresh=refresh))
                if u.path == "/brain":
                    # 🧠 Every brain this appliance can run, the house rule, and which
                    # one answers each robot — with the layer that decided it. Fleet AND
                    # per-robot in one document: the difference between them IS the
                    # feature.
                    return self._json_out(rt.brain_view())
                if u.path == "/content":
                    # 📦 The inventory + the pack ledger + whether an undo is armed.
                    # Fleet-level: content is a property of the appliance, not of a robot.
                    return self._json_out(rt.content_view())
                if u.path == "/content/export":
                    # `?items=kind:key,…&name=…&id=…` → the pack JSON itself, so `curl -o`
                    # and the browser both get a file they can hand to somebody else.
                    # No `items` means everything installed.
                    q = parse_qs(u.query)
                    keys = [k for part in (q.get("items") or [])
                            for k in part.split(",") if k.strip()]
                    try:
                        pack = rt.content_export(
                            keys, name=(q.get("name") or [""])[0],
                            pack_id=(q.get("id") or [""])[0],
                            details=(q.get("details") or [""])[0],
                            author=(q.get("author") or [""])[0])
                    except Exception as e:
                        return self._json_out({"ok": False, "error": str(e),
                                               "reason": str(e)}, 400)
                    return self._json_out(pack)
                self.send_response(404); self.end_headers()

            def _memory_write(self, query):
                """A parent's erase or correction. Shared by DELETE /memory and
                POST /memory — localhost-only like every handler.

                Three cuts, finest first: `item` (one wrong line), `namespace` (one
                activity), neither (everything for that robot). A POST body may carry
                `{"edit": {"namespace", "item", "text"}}` instead, which corrects the
                item in place rather than deleting it."""
                from urllib.parse import parse_qs
                q = parse_qs(query)
                device_id = (q.get("device_id") or [""])[0]
                namespace = (q.get("namespace") or [""])[0]
                item = (q.get("item") or [""])[0]
                body = {}
                if not namespace or not item:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        body = _json.loads(raw or b"{}") or {}
                    except Exception:
                        body = {}
                    if not isinstance(body, dict):
                        body = {}
                edit = body.get("edit") if isinstance(body.get("edit"), dict) else None
                if edit is None:
                    namespace = namespace or body.get("namespace") or body.get("erase") or ""
                    item = item or body.get("item") or ""
                try:
                    if edit is not None:
                        out = rt.edit_memory_item(device_id,
                                                  edit.get("namespace") or namespace,
                                                  edit.get("item") or item,
                                                  edit.get("text"))
                    else:
                        out = rt.erase_memory(device_id, namespace or None, item or None)
                    code = 200 if out.get("ok") else 404
                except Exception as e:
                    out, code = {"ok": False, "error": str(e)}, 400
                return self._json_out(out, code)

            def _telehealth(self, query):
                """🎭 One operator verb. `POST /telehealth?device_id=…` with
                `{"action": "enable"|"disable"|"start"|"end"|"state"|"speak"|"interrupt"}`
                — `speak` also takes `{"text", "mood", "intensity", "gesture"}`.

                A safety BLOCK on the operator's line comes back as **400 with the reason**
                and nothing is spoken, which is the whole point of checking a human's text
                rather than rewriting it (`backlog/telehealth.md` §2.3)."""
                from urllib.parse import parse_qs
                device_id = (parse_qs(query).get("device_id") or [""])[0]
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = _json.loads(raw or b"{}") or {}
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
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
                        raise ValueError(
                            "expected action: enable, disable, start, end, state, "
                            "speak or interrupt")
                    if out.get("ok"):
                        code = 200
                    else:
                        code = 404 if "unknown device_id" in str(out.get("error")) else 400
                except Exception as e:
                    out, code = {"ok": False, "error": str(e), "reason": str(e)}, 400
                return self._json_out(out, code)

            def _voice(self, path: str, query: str):
                """🎚️ `POST /voice` with `{"speech": …, "listening": …}` (either side an
                option `id` like `"gateway:piper-amy"`, the `{engine, model}` dict, or
                `null` to fall back to the default) — persisted, then swapped live.

                `POST /voice/test?device_id=…` with an optional `{"text": …}` speaks one
                line through the engine that is ACTUALLY installed and publishes it to
                that robot, which is the only honest answer to "did my pick work".

                A pick that is not among the current options comes back **400 with the
                reason**, so a stale page cannot install a model the gateway stopped
                serving."""
                from urllib.parse import parse_qs
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = _json.loads(raw or b"{}") or {}
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
                    if path == "/voice/test":
                        device_id = (parse_qs(query).get("device_id")
                                     or [body.get("device_id") or ""])[0]
                        out = rt.voice_test(device_id, body.get("text") or "")
                        code = (200 if out.get("ok")
                                else (404 if "unknown device_id" in str(out.get("error"))
                                      else 400))
                    else:
                        out = rt.voice_update(body)
                        code = 200 if out.get("ok") else 400
                except Exception as e:
                    out, code = {"ok": False, "error": str(e), "reason": str(e)}, 400
                return self._json_out(out, code)

            def _content(self, path: str):
                """📦 `POST /content/review` (the pack itself), `POST /content/import`
                (`{"pack", "accept", "expect_digest"}`) and `POST /content/undo`; ✍️
                `POST /content/item` (`{"kind", "data", "phrases", "local_rev"}`) and
                `POST /content/render` (`{"kind", "data", "context"}`).

                Review writes nothing; import is the one verb that changes the store, and
                it refuses with **409** when `expect_digest` does not match the body now
                being imported — the pack is re-sent between the two calls, so they can
                genuinely be different files. A body over `MOXIE_PACK_MAX_BYTES` is
                **413**, refused before it is buffered rather than after."""
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
                    body = _json.loads(raw or b"{}") or {}
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
                    if path == "/content/render":
                        # ✍️ Rung 1: resolve the draft prompt. Reads nothing, writes
                        # nothing, calls no brain — so it cannot fail with anything but a
                        # malformed draft.
                        out = rt.content_render(body)
                        return self._json_out(out, 200 if out.get("ok") else 400)
                    if path == "/content/item":
                        # ✍️ Rung 4: keep it. The one authoring verb that writes, and the
                        # one that owns `validate_item` — deliberately HERE and not in the
                        # console proxy, so a direct `curl` at this port cannot skip it
                        # (brief R6).
                        out = rt.content_save_item(body)
                        if out.get("ok"):
                            return self._json_out(out, 200)
                        return self._json_out(out, 409 if out.get("conflict") else 400)
                    out = rt.content_import(body.get("pack"),
                                            body.get("accept") or [],
                                            str(body.get("expect_digest") or ""))
                    if out.get("ok"):
                        return self._json_out(out, 200)
                    return self._json_out(out, 409 if out.get("conflict") else 400)
                except Exception as e:
                    return self._json_out({"ok": False, "error": str(e),
                                           "reason": str(e)}, 400)

            def do_DELETE(self):
                """A parent erasing what this appliance stored about their child.

                `DELETE /memory?device_id=…[&namespace=…[&item=…]]` — what Moxie
                *remembers*. With `item`, exactly that one line goes; with only a
                namespace, one activity; with neither, everything for that robot.

                `DELETE /telemetry?device_id=…` — what Moxie *recorded*: the packet ring,
                the daily roll-up and the mentor-behavior history, all three at once (see
                `erase_telemetry`). Deliberately not carved finer: a Packet envelope has
                no per-item meaning to a parent the way one remembered sentence does, and
                a partial erase of a bounded ring is a promise nobody could check.

                Neither is policy-gated — an erase always works, under every
                `LoggingPolicy` value."""
                from urllib.parse import parse_qs, urlparse
                u = urlparse(self.path)
                if u.path == "/memory":
                    return self._memory_write(u.query)
                if u.path == "/telemetry":
                    device_id = (parse_qs(u.query).get("device_id") or [""])[0]
                    if not device_id:
                        return self._json_out({"ok": False,
                                               "error": "device_id is required"}, 400)
                    try:
                        out = rt.erase_telemetry(device_id)
                    except Exception as e:
                        return self._json_out({"ok": False, "device_id": device_id,
                                               "error": str(e)}, 400)
                    return self._json_out(out, 200 if out.get("ok") else 404)
                self.send_response(404); self.end_headers()

            def do_POST(self):
                """Parent-console writes.

                `POST /config?device_id=…` with a JSON body of overrides (audio_volume,
                weekday_bedtime, alarms, wake toggles, `face`, …), validated by
                sanitize_config_overrides, then update_config re-pushes RobotCloudConfig.
                A `face` edit re-pushes like any other override, and because the pushed
                `child_pii.id` is derived from the chosen layers, the change also re-keys
                the robot's face-texture cache (`moxie_sdk/faces.py`).
                `POST /config?scope=fleet` writes the same whitelisted overrides as the
                **appliance-wide defaults** (audit ADOPT #6) and re-pushes every connected
                robot; a per-robot override still wins over the fleet value.

                `POST /safety?device_id=…` with `{"event_id": "sfe-…"}` (or `{}` / `"all"`)
                marks queued safety events reviewed — the parent's "I have seen this".

                `POST /telehealth?device_id=…` with `{"action": …}` drives 🎭 puppet mode
                (audit ADOPT #7) — enable/disable, start/end a session, speak a line
                (with `text`, `mood`, `intensity`), or interrupt. An operator line the
                safety classifier BLOCKS comes back **400 with the reason** and is never
                spoken; see `_telehealth`.

                `POST /brain?device_id=…` (or `?scope=fleet`) with `{"brain": "echo"}`
                picks which brain answers one child — or, at fleet scope, the house rule.
                `{"brain": null}` clears that layer. The next turn uses it; a turn already
                in flight keeps the brain it started with. A pick the environment has
                pinned is refused **naming `MOXIE_APP`** — see `brain_update`.

                `POST /voice` with `{"speech": "gateway:piper-amy", "listening": …}`
                persists the 🎚️ picker's choice and swaps the live engines; the next turn
                uses them. `POST /voice/test?device_id=…` speaks one line through the
                engine actually installed and publishes it to that robot — see `_voice`.

                `POST /content/review` with a pack file says what WOULD happen to every
                item in it — new, upgrade, conflict with a local edit, fork, downgrade —
                and writes nothing. `POST /content/import` with
                `{"pack", "accept": ["kind:key", …], "expect_digest"}` applies exactly the
                accepted items, snapshots what they replaced and makes them live on the
                next turn; a body whose digest is not the reviewed one is **409**.
                `POST /content/undo` puts the snapshot back. See `_content`.

                `POST /content/item` with `{"kind", "data", …}` saves ONE authored item —
                the ✍️ editor's Save. It validates exactly as an import does, snapshots
                what it replaced into the same one undo slot, writes the overlay and
                reloads, so the next turn uses it. A schedule is refused by kind, a change
                to `code` or `extension` is refused, and a stale `local_rev` is **409**.
                `POST /content/render` resolves a draft prompt against a sample context
                and calls **no brain** — see `content_save_item` / `content_render`.

                `POST /preview?device_id=…` with `{"text": "…"}` rehearses one line:
                the behavior planner stages it and the supervisor publishes it as an
                ordinary `remote_chat`, so the SIM (or a robot paired as a rehearsal
                device) performs it. No brain is called and no turn is recorded; the reply
                carries the staged `Performance` JSON and any id `validate` dropped, so an
                author sees the performance before a child does — see `preview`.

                `POST /wakeup?device_id=…` publishes the recovered `wakeup` command
                (`{"command":"wakeup"}` on `/devices/{id}/commands/wakeup`) at one robot.
                The robot acknowledges nothing, so the reply says `published`, never
                "awake" — see `wake_robot`.

                `POST /permits` with `{"device_id": "d_…", "permitted": true, "label": …}`
                lets one pending robot in (or `permitted:false` to revoke it) and re-pushes
                its config on the spot; with `{"allow_unverified_bots": true}` it flips the
                appliance-wide "serve anything that connects" switch.

                Localhost-only (the server binds 127.0.0.1)."""
                from urllib.parse import urlparse, parse_qs
                path = urlparse(self.path).path
                if path == "/memory":
                    # `POST /memory?device_id=…` `{"erase": "<namespace>"|"all"}` —
                    # the same erase as DELETE, for clients that cannot send one — or
                    # `{"edit": {"namespace", "item", "text"}}`, a parent correcting one
                    # remembered line instead of losing the whole activity to it.
                    return self._memory_write(urlparse(self.path).query)
                if path in ("/content/review", "/content/import", "/content/undo",
                            "/content/item", "/content/render"):
                    return self._content(path)
                if path == "/preview":
                    # `POST /preview?device_id=…` `{"text": …, "speak": false}` — the
                    # rehearsal hook (backlog/expressiveness.md §2.4). Plans one line and
                    # publishes it as an ORDINARY remote_chat so any client subscribed as
                    # that device performs it; no brain, no history, no turn recorded.
                    # 404 unknown device, 400 empty/blocked line.
                    q = parse_qs(urlparse(self.path).query)
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        body = _json.loads(raw or b"{}") or {}
                    except Exception as e:
                        return self._json_out({"ok": False, "error": str(e)}, 400)
                    device_id = ((q.get("device_id") or [""])[0]
                                 or str(body.get("device_id") or ""))
                    out = rt.preview(device_id, body.get("text"),
                                     speak=bool(body.get("speak")),
                                     icons=bool(body.get("icons")),
                                     sfx=bool(body.get("sfx")))
                    if out.get("ok"):
                        return self._json_out(out, 200)
                    code = 404 if "unknown device_id" in str(out.get("error")) else 400
                    return self._json_out(out, code)
                if path == "/wakeup":
                    # `POST /wakeup?device_id=…` — publish the recovered `wakeup`
                    # command. 404 unknown device, 409 pending/no broker, and on success
                    # a body that says "published", never "the robot woke up".
                    q = parse_qs(urlparse(self.path).query)
                    out = rt.wake_robot((q.get("device_id") or [""])[0])
                    if out.get("ok"):
                        return self._json_out(out, 200)
                    code = 404 if "unknown device_id" in str(out.get("error")) else 409
                    return self._json_out(out, code)
                if path == "/brain":
                    # `POST /brain?device_id=…` or `?scope=fleet` with {"brain": "echo"}.
                    # A thin, validating front door onto the ordinary config write — the
                    # store and the push are `update_config` / `update_fleet_config`,
                    # exactly as if a parent had posted to /config. What it adds is the
                    # registry check and the pin, so a refusal names `MOXIE_APP`.
                    q = parse_qs(urlparse(self.path).query)
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        body = _json.loads(raw or b"{}") or {}
                    except Exception as e:
                        return self._json_out({"ok": False, "error": str(e)}, 400)
                    out = rt.brain_update(body,
                                          device_id=(q.get("device_id") or [""])[0],
                                          scope=(q.get("scope") or ["robot"])[0])
                    if out.get("ok"):
                        return self._json_out(out, 200)
                    code = 404 if "unknown device_id" in str(out.get("error")) else 400
                    return self._json_out(out, code)
                if path not in ("/config", "/safety", "/permits", "/telehealth",
                                "/voice", "/voice/test"):
                    self.send_response(404); self.end_headers(); return
                if path in ("/voice", "/voice/test"):
                    return self._voice(path, urlparse(self.path).query)
                if path == "/telehealth":
                    return self._telehealth(urlparse(self.path).query)
                if path == "/permits":
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        body = _json.loads(raw or b"{}") or {}
                        if "allow_unverified_bots" in body:
                            out = rt.set_allow_unverified_bots(
                                bool(body["allow_unverified_bots"]))
                        elif body.get("device_id"):
                            out = rt.set_permit(body["device_id"],
                                                permitted=bool(body.get("permitted", True)),
                                                label=body.get("label") or "")
                        else:
                            raise ValueError(
                                "expected {device_id, permitted, label} "
                                "or {allow_unverified_bots}")
                        code = 200
                    except Exception as e:
                        out, code = {"ok": False, "error": str(e)}, 400
                    return self._json_out(out, code)
                device_id = (parse_qs(urlparse(self.path).query).get("device_id") or [""])[0]
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                if path == "/safety":
                    try:
                        body = _json.loads(raw or b"{}") or {}
                        out = rt.acknowledge_safety(device_id, body.get("event_id"))
                        code = 200 if out.get("ok") else 404
                    except Exception as e:
                        out, code = {"ok": False, "error": str(e)}, 400
                    return self._json_out(out, code)
                scope = (parse_qs(urlparse(self.path).query).get("scope") or ["robot"])[0]
                try:
                    from moxie_sdk.cloud_config import sanitize_config_overrides
                    overrides = sanitize_config_overrides(_json.loads(raw or b"{}"))
                    if scope == "fleet":
                        fleet = rt.update_fleet_config(**overrides)
                        out, code = {"ok": True, "scope": "fleet", "applied": overrides,
                                     "fleet_config": fleet,
                                     "robots": list(rt.robots)}, 200
                    else:
                        if not device_id or device_id not in rt.robots:
                            raise ValueError(f"unknown device_id {device_id!r}")
                        rt.update_config(device_id, **overrides)
                        out, code = {
                            "ok": True, "scope": "robot", "device_id": device_id,
                            "applied": overrides,
                            "config_overrides": rt._config_overrides.get(device_id, {}),
                            "config_effective": rt.effective_config(device_id)}, 200
                except Exception as e:
                    out, code = {"ok": False, "error": str(e)}, 400
                body = _json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(body)

        try:
            srv = HTTPServer(("127.0.0.1", port), H)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            print(f"[runtime] status endpoint on http://127.0.0.1:{port}/status")
        except Exception as e:
            print(f"[runtime] status server failed: {e}")
