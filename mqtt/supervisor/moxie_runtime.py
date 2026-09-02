"""
Moxie robot-cloud runtime (supervisor). Speaks the robot's MQTT protocol and turns
it into clean MoxieApp calls. Based on the protocol documented in
docs/architecture/mqtt-and-conversation.md (verified against OpenMoxie, MIT).

Responsibilities:
  * subscribe to all devices' events/state + the broker log
  * detect robot connect/disconnect (regex on $SYS/broker/log)
  * push each robot its config on connect (pairing_status="paired" + child_pii)
  * route `events/remote-chat` (backend:router) turns → MoxieApp.respond → reply
  * maintain per-device conversation history from `notify` events
  * STT (events/zmq) is a documented extension point (see handle_zmq)

This is the transport; the *brain* is whatever MoxieApp is injected.
"""
from __future__ import annotations
import json, re, sys, os, threading, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from moxie_sdk.types import (Turn, Reply, ReplyChunk, RobotContext,  # noqa
                             ChildProfile, Action, ResultCode)
from moxie_sdk.wire import (build_chat_response, build_activity_response,  # pure encoders
                            parse_mentor_behavior)
from moxie_sdk.store import JsonStore                    # durable per-robot JSON store
from moxie_sdk.filler import pick_filler                 # "let me think" lines + markup
from moxie_sdk import safety as safety_seam              # InputSafety classifier (ai-seam §2)
from moxie_sdk.cloud_config import LoggingPolicy         # the child-privacy gate
from markup import make_markup  # simple passthrough markup (automarkup pluggable)

# paho is imported lazily in _build_client() so the runtime + turn pipeline can be
# imported and integration-tested without the broker client installed.

CONNECT_RE = re.compile(r"connected from (.*) as (d_[a-f0-9-]+)", re.I)
DISCONNECT_RE = re.compile(r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected)", re.I)



# How many MentorBehavior records we keep (and serve back) per robot. The history is a
# rolling window, not an archive — the recommender/FTUE checks only need recent activity.
MAX_MENTOR_BEHAVIORS = 500

# How long a turn's brain call may run before we say *something*. The robot re-prompts
# if the cloud stays silent for ~20 s (openmoxie-feature-audit.md:347) and a live gateway
# turn was measured at 45 s healthy / 18 s degraded (implementation-plan.md:138), so the
# default leaves room for a filler + the real answer inside one window. 0 disables it.
DEFAULT_BRAIN_BUDGET_S = 6.0

# How many filler lines one turn may spend. One buys a ~20 s window; a 45 s brain
# outlives it, so a stalled stream may re-arm exactly once more. Past that the child is
# better served by silence than by a robot that only ever says it is thinking.
MAX_FILLERS_PER_TURN = 2

# The safety journal's own LoggingPolicy default. The RobotCloudConfig we push defaults to
# `NO_DATA` (cloud_config.py), but that gate is about what the *robot uploads to us*; the
# review queue is a record our own server keeps about turns that already reached it. So the
# journal keeps rows (category + timestamp + a redacted excerpt) unless a parent explicitly
# sets data sharing to NO_DATA, which switches it to counts only.
SAFETY_JOURNAL_POLICY = LoggingPolicy.NO_MEDIA


class MoxieRuntime:
    def __init__(self, app, host="127.0.0.1", port=1883, child: ChildProfile | None = None,
                 store: JsonStore | None = None, brain_budget_s=None, streaming=None,
                 safety=None):
        self.app = app
        self.child = child or ChildProfile()
        self.host, self.port = host, port
        # Durable per-robot state (mentor behaviors today). JSON files under
        # MOXIE_DATA_DIR — a stepping stone toward a real DB (audit ADOPT #8).
        self.store = store if store is not None else JsonStore()
        self.robots: dict[str, RobotContext] = {}
        self.history: dict[str, list] = {}
        self._memory_dir = os.environ.get("MOXIE_MEMORY_DIR", "").strip()
        self._max_memory = int(os.environ.get("MOXIE_MEMORY_TURNS", "40"))
        self._load_memory()
        # Brain latency: how long app.respond() may run before we speak a filler.
        # Constructor arg wins, then MOXIE_BRAIN_BUDGET_S, then the default.
        try:
            self.brain_budget_s = float(
                brain_budget_s if brain_budget_s is not None
                else os.environ.get("MOXIE_BRAIN_BUDGET_S") or DEFAULT_BRAIN_BUDGET_S)
        except (TypeError, ValueError):
            self.brain_budget_s = DEFAULT_BRAIN_BUDGET_S
        # Streaming: publish an answer sentence by sentence when the app can produce one
        # (MoxieApp.respond_stream). Constructor arg wins, then MOXIE_STREAMING, then on.
        if streaming is None:
            streaming = (os.environ.get("MOXIE_STREAMING") or "1").strip().lower()
        self.streaming = streaming not in (False, 0, "0", "off", "false", "no", "")
        self._turn_seq: dict[str, int] = {}      # newest turn per robot (stale guard)
        self._last_filler: dict[str, str] = {}   # last filler spoken (never repeat it)
        from concurrent.futures import ThreadPoolExecutor
        from collections import deque
        self._pool = ThreadPoolExecutor(max_workers=8)
        self.recent = deque(maxlen=120)          # rolling broker/runtime activity for the UI
        self.started_at = time.time()
        # The MQTT client is created lazily in run() so the runtime can be constructed
        # + integration-tested with an injected fake transport (no broker required).
        self.client = None
        # STT (AI seam §1): an optional transcriber + a per-device VAD accumulator.
        self._transcriber = None
        self._stt_sessions = {}
        self._stt_uuid = {}      # utterance uuid per device (set on any frame that has one)
        # Parent-console config editing: per-device RobotCloudConfig overrides.
        self._config_overrides = {}
        # TTS (AI seam §3): an optional server voice (for the SIM; a real robot self-synthesizes).
        self._synth = None
        # Child safety (AI seam §2): the InputSafety classifier applied to BOTH sides of a
        # turn — the child's utterance before the brain is called, and every chunk the
        # brain produces before it is published. Constructor arg wins (a local-model
        # `Classifier` drops in here); `MOXIE_SAFETY=0` turns the stage off entirely.
        if safety is None and (os.environ.get("MOXIE_SAFETY") or "1").strip().lower() \
                not in ("0", "off", "false", "no"):
            try:
                safety = safety_seam.default_classifier()
            except Exception as e:                # a broken rules file must be LOUD
                print(f"[runtime] ⚠️  safety rules failed to load: {e}", flush=True)
                safety = None
        self.safety = safety or None
        self._last_redirect: dict[str, str] = {}   # never the same redirect twice running
        if self.safety is None:
            print("[runtime] ⚠️  input safety is OFF (MOXIE_SAFETY=0)", flush=True)

    def _build_client(self):
        import paho.mqtt.client as mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="supervisor")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        return self.client

    # ---- conversation memory (survives restarts) ----
    def _memory_path(self, device_id: str) -> str:
        safe = "".join(c for c in device_id if c.isalnum() or c in "-_")
        return os.path.join(self._memory_dir, f"{safe}.json")

    def _load_memory(self):
        """Restore per-device conversation history from disk, if configured."""
        if not self._memory_dir:
            return
        try:
            os.makedirs(self._memory_dir, exist_ok=True)
            for name in os.listdir(self._memory_dir):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(self._memory_dir, name)) as fh:
                    self.history[name[:-5]] = json.load(fh)
            if self.history:
                print(f"[runtime] restored memory for {len(self.history)} robot(s)")
        except Exception as e:
            print(f"[runtime] memory load failed: {e}")

    def _save_memory(self, device_id: str):
        """Persist one robot's history (trimmed) so it survives a restart."""
        if not self._memory_dir:
            return
        h = self.history.get(device_id) or []
        if len(h) > self._max_memory:
            del h[: len(h) - self._max_memory]
        try:
            os.makedirs(self._memory_dir, exist_ok=True)
            tmp = self._memory_path(device_id) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(h, fh)
            os.replace(tmp, self._memory_path(device_id))
        except Exception as e:
            print(f"[runtime] memory save failed: {e}")

    def status_snapshot(self) -> dict:
        """The supervisor + robots snapshot the parent console reads (JSON). Each robot
        carries its live state (battery/volume/wifi/mode/firmware) from the last /state."""
        robots = []
        for r in self.robots.values():
            st = r.extra.get("status", {})
            robots.append({
                "device_id": r.device_id, "child": r.child.nickname,
                "firmware": r.firmware or st.get("robot_firmware_version"),
                "battery_level": st.get("battery_level"),
                "audio_volume": st.get("audio_volume"),
                "wifi_ssid": st.get("wifi_ssid"), "mode": st.get("mode"),
                "ota_reboot_required": st.get("ota_reboot_required"),
                "config_overrides": self._config_overrides.get(r.device_id, {}),
                "telemetry_count": len(r.extra.get("telemetry", [])),
                "safety_total": int((self.store.read(
                    r.device_id, safety_seam.COUNTS_COLLECTION, {}) or {}).get("total", 0)),
                "safety_unreviewed": sum(
                    1 for e in (self.store.read(
                        r.device_id, safety_seam.EVENTS_COLLECTION, []) or [])
                    if isinstance(e, dict) and not e.get("reviewed")),
            })
        return {"ok": True, "app": self.app.name,
                "uptime_s": int(time.time() - self.started_at),
                "robots": robots, "recent": list(self.recent)[-60:]}

    # ---- lifecycle ----
    def run(self, status_port: int = 8930):
        if self.client is None:
            self._build_client()
        self._start_status_server(status_port)
        print(f"[runtime] connecting to broker {self.host}:{self.port} · app={self.app.name}")
        self._note("info", f"supervisor started (app={self.app.name})")
        self.client.connect(self.host, self.port, 30)
        self.client.loop_forever()

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
                """GET /status → the console snapshot; GET /telemetry?device_id=…&limit=N
                → that robot's stored telemetry Packets rolled up for the insights view.
                Localhost-only (the server binds 127.0.0.1)."""
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                if u.path == "/status":
                    return self._json_out(rt.status_snapshot())
                if u.path == "/telemetry":
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    try:
                        limit = int((q.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    out = rt.telemetry_view(device_id, limit=limit)
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
                self.send_response(404); self.end_headers()

            def do_POST(self):
                """Parent-console writes.

                `POST /config?device_id=…` with a JSON body of overrides (audio_volume,
                weekday_bedtime, wake toggles, …), validated by sanitize_config_overrides,
                then update_config re-pushes RobotCloudConfig.

                `POST /safety?device_id=…` with `{"event_id": "sfe-…"}` (or `{}` / `"all"`)
                marks queued safety events reviewed — the parent's "I have seen this".

                Localhost-only (the server binds 127.0.0.1)."""
                from urllib.parse import urlparse, parse_qs
                path = urlparse(self.path).path
                if path not in ("/config", "/safety"):
                    self.send_response(404); self.end_headers(); return
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
                try:
                    from moxie_sdk.cloud_config import sanitize_config_overrides
                    overrides = sanitize_config_overrides(_json.loads(raw or b"{}"))
                    if not device_id or device_id not in rt.robots:
                        raise ValueError(f"unknown device_id {device_id!r}")
                    rt.update_config(device_id, **overrides)
                    out, code = {"ok": True, "device_id": device_id, "applied": overrides,
                                 "config_overrides": rt._config_overrides.get(device_id, {})}, 200
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

    def _on_connect(self, c, u, flags, rc, props=None):
        print(f"[runtime] broker connected rc={rc}")
        for t in ("/devices/+/events/#", "/devices/+/state",
                  "$SYS/broker/log/#", "$SYS/broker/clients/#"):
            c.subscribe(t)

    # ---- message router ----
    def _on_message(self, c, u, msg):
        topic = msg.topic
        try:
            if topic.startswith("$SYS/broker/log/"):
                return self._on_log(msg.payload.decode("utf-8", "replace"))
            parts = topic.split("/")            # ['', 'devices', d_id, 'events', name...]
            if len(parts) >= 4 and parts[1] == "devices":
                device_id = parts[2]
                kind = parts[3]
                if kind == "state":
                    return self._on_state(device_id, msg.payload)
                if kind == "events" and len(parts) >= 5:
                    return self._on_event(device_id, parts[4], msg.payload)
        except Exception as e:
            print(f"[runtime] error handling {topic}: {e}")

    # ---- connect detection via broker log ----
    def _note(self, kind: str, text: str):
        """Record a line for the UI's connection monitor."""
        self.recent.append({"t": time.time(), "kind": kind, "text": text})

    def _on_log(self, line: str):
        # surface interesting broker activity to the UI (any sign of life)
        low = line.lower()
        if any(k in low for k in ("new connection", "new client", "disconnect",
                                  "closed its connection", "error", "socket", "denied")):
            kind = "error" if ("error" in low or "socket" in low) else "conn"
            self._note(kind, line.split(": ", 1)[-1] if ": " in line else line)
        m = CONNECT_RE.search(line)
        if m:
            return self._device_connect(m.group(2))
        m = DISCONNECT_RE.search(line)
        if m:
            return self._device_disconnect(m.group(1))

    def _device_connect(self, device_id: str):
        if device_id in self.robots:
            return
        print(f"[runtime] 🤖 robot connected: {device_id}", flush=True)
        self._note("robot", f"🤖 robot connected: {device_id}")
        robot = RobotContext(device_id=device_id, child=self.child)
        self.robots[device_id] = robot
        self.history.setdefault(device_id, [])
        # Push config after a short settle delay, WITHOUT blocking the MQTT loop.
        def _settle():
            self._push_config(device_id)
            try:
                self.app.on_connect(robot)
            except Exception as e:
                print(f"[runtime] app.on_connect error: {e}", flush=True)
        threading.Timer(1.0, _settle).start()

    def _device_disconnect(self, device_id: str):
        robot = self.robots.pop(device_id, None)
        if robot:
            print(f"[runtime] robot disconnected: {device_id}")
            try:
                self.app.on_disconnect(robot)
            except Exception:
                pass

    def _on_state(self, device_id, payload):
        if device_id not in self.robots:
            self._device_connect(device_id)      # fallback if we missed the log line
        try:
            from moxie_sdk.cloud_config import parse_robot_status
            status = parse_robot_status(payload)
            robot = self.robots.get(device_id)
            if robot:
                if status.get("robot_firmware_version"):
                    robot.firmware = status["robot_firmware_version"]
                robot.extra["status"] = status      # battery/volume/wifi/mode for the UI
        except Exception:
            pass

    # ---- config push / edit (parent console) ----
    def _push_config(self, device_id):
        from moxie_sdk.cloud_config import build_robot_cloud_config
        cfg = build_robot_cloud_config(self.child, **self._config_overrides.get(device_id, {}))
        if self.client:
            self.client.publish(f"/devices/{device_id}/config", json.dumps(cfg))
        print(f"[runtime] → pushed config to {device_id} (pairing_status=paired)")
        return cfg

    # ---- child safety (AI seam §2 — InputSafety) ----
    def safety_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy governing this robot's safety journal — the parent's explicit
        `logging_policy` override if there is one, else `SAFETY_JOURNAL_POLICY`."""
        raw = (self._config_overrides.get(device_id) or {}).get("logging_policy")
        if raw is None:
            return SAFETY_JOURNAL_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return SAFETY_JOURNAL_POLICY

    def _safety_keeps_rows(self, device_id) -> bool:
        """False under `NO_DATA`: the journal then keeps counts and nothing else — no
        excerpt, no per-event row, so none of the child's words are stored at all."""
        return self.safety_policy(device_id) != LoggingPolicy.NO_DATA

    def _assess(self, text, role):
        """Run the classifier, or None when it is off / the text is empty. A classifier
        that raises is treated as "allow": a broken safety stage must never silence Moxie
        (it is a layer under the model's own alignment, not the only one)."""
        if self.safety is None or not (text or "").strip():
            return None
        try:
            return self.safety.assess(text, role=role)
        except Exception as e:
            print(f"[runtime] safety classifier failed (allowing): {e}", flush=True)
            return None

    def _record_safety(self, device_id, verdict) -> dict | None:
        """Put one verdict in the parent review queue. Returns the stored row (or None
        when the policy keeps counts only)."""
        row = None
        try:
            counts = self.store.read(device_id, safety_seam.COUNTS_COLLECTION, {})
            self.store.write(device_id, safety_seam.COUNTS_COLLECTION,
                             safety_seam.roll_up(counts if isinstance(counts, dict) else {},
                                                 verdict))
            if self._safety_keeps_rows(device_id):
                row = safety_seam.event_from(verdict)
                self.store.append(device_id, safety_seam.EVENTS_COLLECTION, row,
                                  cap=safety_seam.MAX_EVENTS)
        except Exception as e:
            print(f"[runtime] safety journal write failed: {e}", flush=True)
        side = "Moxie" if verdict.role == safety_seam.MOXIE else "child"
        cats = ", ".join(verdict.categories) or "?"
        icon = "🛑" if verdict.action == safety_seam.BLOCK else "⚠️"
        self._note("safety", f"{icon} {verdict.action} ({side}): {cats}")
        print(f"[runtime] {icon} safety {verdict.action} on {device_id} "
              f"[{side}]: {cats}", flush=True)
        return row

    def _safety_redirect(self, device_id, verdict):
        """The line Moxie says instead of blocked text: pick it, stamp its id onto the
        verdict as `InputSafety.phrase_id`, and record the block for a parent."""
        red = safety_seam.redirect_for(verdict,
                                       last=self._last_redirect.get(device_id, ""),
                                       classifier=self.safety)
        verdict.phrase_id = red.phrase_id
        self._last_redirect[device_id] = red.text
        self._record_safety(device_id, verdict)
        return red

    def _safety_gate_input(self, device_id, event_id, speech, seq) -> bool:
        """Pre-inference gate: assess what the CHILD said before any brain call.

        Hard-blocked → the brain is never called; Moxie speaks a gentle, kid-appropriate
        redirect as a spec-conformant `RemoteChatResponse` carrying
        `input.safety` (`RemoteChatInput.InputSafety`, RemoteChat.proto:180-186/:198/:335).
        Flagged → allowed through to the brain and recorded for a parent.
        Returns True when the turn was answered here and the caller must stop.
        """
        verdict = self._assess(speech, safety_seam.CHILD)
        if not verdict:
            return False
        if verdict.action != safety_seam.BLOCK:
            self._record_safety(device_id, verdict)
            return False
        red = self._safety_redirect(device_id, verdict)
        if self._is_stale(device_id, seq):
            return True
        # Deliberately remember only OUR line: putting the blocked utterance in the
        # history would feed it to the brain as context on the very next turn.
        self._remember(device_id, "", red.text)
        self._publish_chat(device_id, event_id, "router", red.text, red.markup,
                           result=ResultCode.SUCCESS, safety=verdict)
        self._maybe_synthesize(device_id, red.markup, event_id, chunk_num=0)
        return True

    def safety_view(self, device_id, limit: int = 20) -> dict:
        """The parent console's review queue for one robot: counts by category plus the
        newest events, newest first. Unknown device with nothing stored → ok:false."""
        counts = self.store.read(device_id, safety_seam.COUNTS_COLLECTION, None)
        if device_id not in self.robots and counts is None:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}"}
        rows = self.store.read(device_id, safety_seam.EVENTS_COLLECTION, []) or []
        if not isinstance(rows, list):
            rows = []
        newest = list(reversed(rows))[:max(0, int(limit))]
        return {
            "ok": True, "device_id": device_id,
            "policy": self.safety_policy(device_id).name,
            "detail": self._safety_keeps_rows(device_id),
            "enabled": self.safety is not None,
            "classifier": getattr(self.safety, "name", None),
            "counts": counts if isinstance(counts, dict) else {},
            "unreviewed": sum(1 for r in rows if not r.get("reviewed")),
            "labels": safety_seam.category_labels(self.safety) if self.safety else {},
            "events": newest,
        }

    def acknowledge_safety(self, device_id, event_id=None, limit: int = 20) -> dict:
        """Mark one queued event reviewed (or every one when `event_id` is None/"all") —
        the parent's "I have seen this". Returns the refreshed view."""
        rows = self.store.read(device_id, safety_seam.EVENTS_COLLECTION, []) or []
        if not isinstance(rows, list):
            rows = []
        want_all = event_id in (None, "", "all", "*")
        hit = 0
        for r in rows:
            if want_all or r.get("id") == event_id:
                if not r.get("reviewed"):
                    r["reviewed"] = True
                    r["reviewed_at"] = time.time()
                hit += 1
        if not hit and not want_all:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown safety event {event_id!r}"}
        self.store.write(device_id, safety_seam.EVENTS_COLLECTION, rows)
        self._note("safety", f"✅ reviewed {hit} safety event(s)")
        out = self.safety_view(device_id, limit=limit)
        out["acknowledged"] = hit
        return out

    # ---- telemetry ingest (parent-console insights) ----
    def ingest_telemetry(self, device_id, payload):
        """Parse an incoming telemetry Packet and store it per-device for insights.
        Returns the parsed packet (or None on parse failure)."""
        try:
            from moxie_sdk.telemetry import parse_packet
            pkt = parse_packet(payload)
        except Exception:
            return None
        robot = self.robots.get(device_id)
        if robot is not None:
            buf = robot.extra.setdefault("telemetry", [])
            buf.append(pkt)
            del buf[:-50]                       # keep the last 50 events
        self._note("telemetry", f"📈 {pkt.get('event_name', 'event')}")
        return pkt

    def telemetry_view(self, device_id, limit: int = 20) -> dict:
        """The parent console's per-robot insights view (M6): the stored Packets for
        one device, rolled up by summarize_events + the newest `limit` events.
        Unknown device → {ok:false, error} (the HTTP layer answers 404)."""
        robot = self.robots.get(device_id)
        if robot is None:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}"}
        from moxie_sdk.telemetry import summarize_events
        summary = summarize_events(robot.extra.get("telemetry", []), limit=limit)
        return {"ok": True, "device_id": device_id,
                "summary": summary, "events": summary["latest"]}

    def update_config(self, device_id, **overrides):
        """Parent-console config edit: merge overrides (audio_volume, screen_brightness,
        timezone_id, logging_policy, weekday_bedtime, wake toggles, …) into this device's
        RobotCloudConfig and re-publish it. Overrides persist across re-pushes."""
        self._config_overrides.setdefault(device_id, {}).update(overrides)
        self._note("config", f"⚙️  config updated: {', '.join(overrides)}")
        return self._push_config(device_id)

    # ---- events ----
    def _on_event(self, device_id, name, payload):
        robot = self.robots.get(device_id) or RobotContext(device_id=device_id, child=self.child)
        if name.startswith("remote-chat"):
            return self._on_remote_chat(device_id, robot, payload)
        if name == "zmq":
            return self.handle_zmq(device_id, payload)
        if name == "client-service-activity-log":
            return self._on_activity(device_id, payload)
        if name in ("telemetry", "analytics") or name.startswith("packet"):
            return self.ingest_telemetry(device_id, payload)
        # everything else → surface to the app as an event (vision, module lifecycle…)
        try:
            data = json.loads(payload)
        except Exception:
            data = {"raw": True}
        try:
            self.app.on_event(robot, name, data)
        except Exception:
            pass

    def _on_remote_chat(self, device_id, robot, payload):
        try:
            rcr = json.loads(payload)
        except Exception:
            return
        command = rcr.get("command", "prompt")
        backend = rcr.get("backend", "router")
        event_id = rcr.get("event_id")
        robot.module_id = rcr.get("module_id") or robot.module_id
        robot.content_id = rcr.get("content_id") or robot.content_id

        # module list query (backend:data / query:modules) → empty list for v1
        if backend == "data" and rcr.get("query") == "modules":
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.SUCCESS, modules=[])

        # rebuild history from notify events (Moxie is authoritative about what it said)
        if command == "notify":
            return self._ingest_notify(device_id, rcr)

        speech = rcr.get("speech") or ""
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") == "input" and ln.get("text"):
                speech = ln["text"]
        turn = Turn(robot=robot, speech=speech, history=list(self.history.get(device_id, [])),
                    command=command, input_vars=rcr.get("input_vars", {}))
        # Number the turn so a slow brain's answer can be recognized as stale if the
        # child has moved on by the time it lands (_is_stale). The MQTT loop is the only
        # writer here, so a plain increment is enough.
        seq = self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        # Run the (possibly slow) app + LLM off the MQTT loop so we never block it.
        self._pool.submit(self._handle_turn, device_id, event_id, speech, turn, seq)

    # ---- one turn, with a latency budget ----
    def _is_stale(self, device_id, seq) -> bool:
        """True when a newer turn for this robot started after `seq` — its answer must
        never be spoken: the child asked something else in the meantime."""
        return seq is not None and self._turn_seq.get(device_id, seq) != seq

    def _handle_turn(self, device_id, event_id, speech, turn, seq=None):
        """Answer one turn. Fast brain → exactly one SUCCESS reply, as always.

        **Slow brain (over `brain_budget_s`)** → the child hears a short filler *now*
        instead of silence: chunk 0 with `result=REPLY_PENDING` ("more chunks to come",
        RemoteChat.proto ResultCode 9 — remote-chat-protocol.md:63), the inference keeps
        running on this worker, and the real line follows as chunk 1 with
        `result=SUCCESS` + `consistency_control.is_completed` to close the sequence
        (RemoteChat.proto fields 22/18). Without this a 45 s brain overruns the robot's
        ~20 s reprompt window (openmoxie-feature-audit.md:347) and Moxie just goes quiet.

        **Streaming brain** (`MOXIE_STREAMING`, default on) → if the app offers a
        `respond_stream`, each finished sentence goes out as its own chunk the moment the
        model writes it, so the child hears real words at first-token latency instead of
        at whole-completion latency. See `_handle_stream_turn`.

        **Safety (ai-seam §2)** wraps both ends and is app-agnostic, because it lives here
        rather than in any `MoxieApp`: the child's utterance is assessed BEFORE the brain
        is called (`_safety_gate_input` — a hard block never reaches a model), and the
        brain's answer is assessed before it is published (per chunk when streaming).

        Pattern credit: OpenMoxie Fork A's `ReasoningChatSession` runs the long inference
        on a pool and speaks rotating interludes meanwhile; the idea is theirs, this code
        and the multi-chunk wire shape are ours.
        """
        if self._safety_gate_input(device_id, event_id, speech, seq):
            return
        if self.streaming:
            stream = None
            try:
                stream = self.app.respond_stream(turn)
            except Exception as e:
                print(f"[runtime] app.respond_stream error: {e}", flush=True)
            if stream is not None:
                return self._handle_stream_turn(device_id, event_id, speech, turn,
                                                seq, stream)
        state = {"lock": threading.Lock(), "done": False, "filler": None}
        timer = None
        if self.brain_budget_s > 0:
            timer = threading.Timer(self.brain_budget_s, self._speak_filler,
                                    args=(device_id, event_id, seq, state))
            timer.daemon = True
            timer.start()
        try:
            reply = self.app.respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            reply = Reply(text="Hmm, let me think about that.")
        finally:
            if timer is not None:
                timer.cancel()
        # Closing the door on the filler and reading it back is one atomic step: the
        # timer holds this same lock while it publishes, so chunk 0 can never land after
        # chunk 1.
        with state["lock"]:
            state["done"] = True
            filler = state["filler"]
        if self._is_stale(device_id, seq):
            self._note("chat", f"⏭️  dropped a stale answer for {device_id}")
            print(f"[runtime] ⏭️  turn {seq} superseded on {device_id}; "
                  f"dropping '{reply.text[:40]}'", flush=True)
            return
        # Post-inference: a non-streamed answer is assessed whole. A blocked answer is
        # never published — the child hears a safe line instead and it goes in the queue.
        out_verdict = self._assess(reply.text, safety_seam.MOXIE)
        if out_verdict:
            if out_verdict.action == safety_seam.BLOCK:
                red = self._safety_redirect(device_id, out_verdict)
                reply = Reply(text=red.text, markup=red.markup,
                              result_code=reply.result_code)
            else:
                self._record_safety(device_id, out_verdict)
        self._remember(device_id, speech, reply.text)
        markup = reply.markup if reply.markup is not None else make_markup(reply.text)
        self._note("chat", f"💬 '{speech[:30]}' → '{reply.text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{reply.text[:60]}'", flush=True)
        # A filler already went out → this is chunk 1 and it ends the sequence. No
        # filler → the single-chunk reply we have always sent, unchanged on the wire.
        chunk = 1 if filler is not None else None
        self._publish_chat(device_id, event_id, "router", reply.text, markup,
                           actions=reply.actions, end_turn=reply.end_turn,
                           result=reply.result_code, mood=reply.mood,
                           dialog_act=reply.dialog_act, chunk_num=chunk,
                           is_completed=None if chunk is None else True)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=chunk or 0)

    def _speak_filler(self, device_id, event_id, seq, state):
        """The budget expired with the brain still thinking → say something kind now.

        Published as chunk 0 / REPLY_PENDING, and synthesized like any other line so the
        SIM (and a robot without on-device TTS) actually hears it. Never the same line
        twice in a row for one robot. Returns the filler text, or None if the brain beat
        the budget or the turn is already stale."""
        with state["lock"]:
            if state["done"]:
                return None                       # brain won the race — say nothing
            if self._is_stale(device_id, seq):
                return None
            text, markup = pick_filler(self._last_filler.get(device_id, ""))
            state["filler"] = text
            self._last_filler[device_id] = text
            self._note("chat", f"⏳ '{text[:40]}'")
            print(f"[runtime] ⏳ brain over budget ({self.brain_budget_s:g}s) on "
                  f"{device_id} → filler: '{text}'", flush=True)
            self._publish_chat(device_id, event_id, "router", text, markup,
                               result=ResultCode.REPLY_PENDING, chunk_num=0,
                               is_completed=False)
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
            return text

    # ---- one turn, streamed sentence by sentence ----
    def _handle_stream_turn(self, device_id, event_id, speech, turn, seq, stream):
        """Answer a turn from an `Iterator[ReplyChunk]`, publishing as the model writes.

        Each finished sentence goes out immediately as `result=REPLY_PENDING` with its
        `chunk_num` (RemoteChat.proto field 22); the chunk the app marks `final` closes
        the sequence with `result=SUCCESS` + `consistency_control.is_completed`
        (field 18) — the contract's own "one event_id, several responses" shape
        (docs/reverse-engineering/protocol/remote-chat-protocol.md:26,:63). A turn that
        fits in ONE chunk is published exactly as it always was: no `chunk_num`, no
        `consistency_control`, so nothing downstream has to know about streaming.

        Latency cover: the filler timer is (re-)armed after every chunk, so a brain whose
        FIRST token is late gets a "let me think" line, and a stream that stalls
        mid-answer gets at most one more (`MAX_FILLERS_PER_TURN`). Fillers take the next
        `chunk_num` like any other chunk, so ordering on the wire is still total.

        Stale guard: a newer turn for this robot cancels the stream — we stop consuming
        it, close it, and publish nothing further for the old `event_id`.
        """
        state = {"lock": threading.Lock(), "done": False, "chunk": 0,
                 "fillers": 0, "gen": 0, "timer": None}
        said, closed, failed = [], False, None
        self._arm_filler(device_id, event_id, seq, state)
        try:
            for chunk in stream:
                final = bool(getattr(chunk, "final", False))
                # Post-inference, per chunk: assessed BEFORE it is published, because a
                # streamed sentence is on the wire while the rest of the answer does not
                # exist yet. A blocked chunk is never spoken; the sequence closes on a
                # short safe line and the rest of the stream is cancelled.
                verdict = self._assess(chunk.text, safety_seam.MOXIE)
                blocked = bool(verdict) and verdict.action == safety_seam.BLOCK
                red = None
                if blocked:
                    red = self._safety_redirect(device_id, verdict)
                elif verdict:
                    self._record_safety(device_id, verdict)
                with state["lock"]:
                    state["gen"] += 1                 # invalidate any in-flight timer
                    self._cancel_filler(state)
                    stale = self._is_stale(device_id, seq)
                    if not stale:
                        if final or blocked:
                            state["done"] = True
                        n = state["chunk"]
                        state["chunk"] = n + 1
                        if blocked:
                            safe = ReplyChunk(text=red.text, markup=red.markup, final=True)
                            self._publish_stream_chunk(device_id, event_id, safe, n, True)
                            said.append(red.text)
                        else:
                            self._publish_stream_chunk(device_id, event_id, chunk, n, final)
                            if chunk.text:
                                said.append(chunk.text)
                if stale:
                    self._note("chat", f"⏭️  cancelled a stale stream for {device_id}")
                    print(f"[runtime] ⏭️  turn {seq} superseded on {device_id}; "
                          f"cancelling the stream mid-answer", flush=True)
                    return
                if final or blocked:
                    closed = True
                    break
                self._arm_filler(device_id, event_id, seq, state)
        except Exception as e:
            failed = e
            print(f"[runtime] app.respond_stream error: {e}", flush=True)
        finally:
            with state["lock"]:
                state["done"] = True
                self._cancel_filler(state)
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if self._is_stale(device_id, seq):
            self._note("chat", f"⏭️  dropped a stale answer for {device_id}")
            return
        if not closed:
            # The stream ended (or died) without a final chunk. Nothing spoken yet →
            # the whole answer is still recoverable on the ordinary non-streaming path;
            # otherwise close the sequence so the robot is not left waiting.
            with state["lock"]:
                n = state["chunk"]
            if n == 0:
                reply = self._safe_respond(turn) if failed is not None else Reply(text="")
                said.append(reply.text)
                self._publish_stream_chunk(device_id, event_id, reply, 0, True)
            else:
                self._publish_stream_chunk(
                    device_id, event_id, Reply(text=""), n, True, synthesize=False)
        text = " ".join(t for t in said if t).strip()
        self._remember(device_id, speech, text)
        self._note("chat", f"💬 '{speech[:30]}' → '{text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{text[:60]}' "
              f"({state['chunk']} chunk(s))", flush=True)

    def _safe_respond(self, turn):
        try:
            return self.app.respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            return Reply(text="Hmm, let me think about that.")

    def _publish_stream_chunk(self, device_id, event_id, chunk, n, final, synthesize=True):
        """One `ReplyChunk` (or `Reply`) onto the wire, with its chunk bookkeeping."""
        markup = chunk.markup if chunk.markup is not None else make_markup(chunk.text)
        result = getattr(chunk, "result_code", None)
        if result is None:
            result = ResultCode.SUCCESS if final else ResultCode.REPLY_PENDING
        # A one-chunk answer keeps the exact wire shape we have always sent: chunk 0 /
        # not-streaming is the proto default, so both fields stay off.
        solo = final and n == 0
        self._publish_chat(device_id, event_id, "router", chunk.text, markup,
                           actions=chunk.actions, end_turn=chunk.end_turn,
                           result=result,
                           chunk_num=None if solo else n,
                           is_completed=None if solo else bool(final))
        if synthesize:
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=n)

    def _remember(self, device_id, speech, text):
        """Fold one finished turn into the robot's conversation history."""
        h = self.history.setdefault(device_id, [])
        if speech:
            h.append({"role": "user", "content": speech})
        h.append({"role": "assistant", "content": text})
        self._save_memory(device_id)

    # ---- filler timer (shared by the streaming path) ----
    def _cancel_filler(self, state):
        timer = state.get("timer")
        state["timer"] = None
        if timer is not None:
            timer.cancel()

    def _arm_filler(self, device_id, event_id, seq, state):
        """(Re)start the latency timer for a streaming turn. No-op once the turn is done
        or the per-turn filler budget is spent."""
        if self.brain_budget_s <= 0:
            return
        with state["lock"]:
            if state["done"] or state["fillers"] >= MAX_FILLERS_PER_TURN:
                return
            timer = threading.Timer(self.brain_budget_s, self._speak_stream_filler,
                                    args=(device_id, event_id, seq, state, state["gen"]))
            timer.daemon = True
            state["timer"] = timer
        timer.start()

    def _speak_stream_filler(self, device_id, event_id, seq, state, gen):
        """The stream produced nothing for a whole budget → say something kind now.

        Fires for a late FIRST token and, once more at most, for a mid-answer stall.
        `gen` is the chunk counter this timer was armed against: if a chunk landed in the
        meantime the timer is stale and says nothing."""
        with state["lock"]:
            if state["done"] or state["gen"] != gen:
                return None                       # a chunk arrived — nothing to cover
            if state["fillers"] >= MAX_FILLERS_PER_TURN:
                return None
            if self._is_stale(device_id, seq):
                return None
            text, markup = pick_filler(self._last_filler.get(device_id, ""))
            self._last_filler[device_id] = text
            state["fillers"] += 1
            state["gen"] += 1
            state["timer"] = None
            n = state["chunk"]
            state["chunk"] = n + 1
            self._note("chat", f"⏳ '{text[:40]}'")
            print(f"[runtime] ⏳ stream quiet for {self.brain_budget_s:g}s on "
                  f"{device_id} → filler {state['fillers']}: '{text}'", flush=True)
            self._publish_chat(device_id, event_id, "router", text, markup,
                               result=ResultCode.REPLY_PENDING, chunk_num=n,
                               is_completed=False)
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=n)
        self._arm_filler(device_id, event_id, seq, state)   # another stall? one more line
        return text

    # ---- TTS (AI seam §3) — server voice for the SIM ----
    def set_synthesizer(self, synth):
        """Install a server-side TTS engine (moxie_sdk.tts.Synthesizer). The SIM plays
        the resulting audio; a real robot self-synthesizes so this is SIM-only."""
        self._synth = synth

    def _maybe_synthesize(self, device_id, markup, event_id="", chunk_num=0):
        """If a synthesizer is set, render the line and publish a CloudTTSResponse to
        /devices/{id}/commands/tts. TTS failure never breaks the turn. `chunk_num` keeps
        a multi-chunk turn (filler then answer) in playback order for the client."""
        if self._synth is None:
            return None
        try:
            from moxie_sdk.tts import synthesize_cloud_tts
            resp = synthesize_cloud_tts(self._synth, markup, event_id=event_id,
                                        chunk_num=chunk_num)
            if self.client:
                self.client.publish(f"/devices/{device_id}/commands/tts", json.dumps(resp))
            return resp
        except Exception as e:
            print(f"[runtime] TTS synth failed (non-fatal): {e}", flush=True)
            return None

    def _ingest_notify(self, device_id, rcr):
        h = self.history.setdefault(device_id, [])
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") == "input" and ln.get("text"):
                h.append({"role": "user", "content": ln["text"]})
        if rcr.get("speech"):
            spoken = "\n".join(l for l in rcr["speech"].splitlines()
                               if not l.startswith(("animation:", "silent:")))
            if spoken.strip():
                h.append({"role": "assistant", "content": spoken.strip()})
        self._save_memory(device_id)

    # ---- mentor behaviors (what the child has already done) ----
    def mentor_behaviors(self, device_id) -> list:
        """This robot's stored MentorBehavior history, newest first.

        Newest-first mirrors OpenMoxie's field-proven server (`robot_data.py::get_mbh`
        orders by `-timestamp`); our docs record the record shape but not an ordering."""
        records = self.store.read(device_id, "mentor_behaviors", []) or []
        if not isinstance(records, list):
            return []
        return sorted(records, key=lambda r: (r or {}).get("timestamp") or 0, reverse=True)

    def ingest_mentor_behavior(self, device_id, report):
        """Store one reported MentorBehavior (`ActivityUpdate.mentor_behavior`, Cloud.proto
        :241 — see wire.parse_mentor_behavior). Returns the stored record, or None if the
        report carried nothing usable. Publishes nothing: a report is not a query."""
        rec = parse_mentor_behavior(report)
        if rec is None:
            return None
        self.store.append(device_id, "mentor_behaviors", rec, cap=MAX_MENTOR_BEHAVIORS)
        self._note("behavior", f"🏁 {rec.get('module_id')}"
                               f"{'/' + rec['content_id'] if rec.get('content_id') else ''}"
                               f" {rec.get('action', '')}".rstrip())
        return rec

    # ---- the day plan ----
    def build_schedule_for(self, device_id) -> dict:
        """The ContentSchedule this robot gets for this session: the running content
        module's `schedules[]` template (read-only) planned against what this robot has
        already completed. See moxie_sdk/schedule.py for the shape + citations."""
        from moxie_sdk.schedule import build_schedule, schedule_template
        try:
            template = schedule_template(getattr(self.app, "module", None))
        except Exception as e:
            print(f"[runtime] schedule template unavailable ({e}); using the default")
            template = None
        return build_schedule(template, mentor_behaviors=self.mentor_behaviors(device_id),
                              device_id=device_id)

    def _query_payload(self, device_id, query):
        """The value for a CloudQuery — None means "send this field's empty value"."""
        if query == "schedule":
            try:
                return self.build_schedule_for(device_id)
            except Exception as e:
                print(f"[runtime] schedule build failed: {e}", flush=True)
                return None
        if query == "mentor_behaviors":
            return self.mentor_behaviors(device_id)
        return None                       # license: no license blobs to share (yet)

    def _on_activity(self, device_id, payload):
        try:
            data = json.loads(payload)
        except Exception:
            return
        query = data.get("query")
        subtopic = data.get("subtopic")
        # `client-service-activity-log` is multiplexed by `subtopic`; the pull queries
        # ride subtopic="query" (mqtt-and-conversation.md:274). Older/looser senders omit
        # it, so a bare `query` still counts.
        if subtopic in (None, "", "query") and query in ("schedule", "mentor_behaviors",
                                                         "license"):
            # Answer as a CloudQueryResponse: echo `request_id` and key the payload by its
            # own proto field (schedule / mentor_behaviors / license_values) — see
            # build_activity_response.
            resp = build_activity_response(query, self._query_payload(device_id, query),
                                           request_id=data.get("request_id"))
            self.client.publish(f"/devices/{device_id}/commands/query_result",
                                json.dumps(resp))
            return resp
        # The same topic also carries *reports*: `mentor_behavior` is what the child just
        # finished (or quit). Ingest it — that history is what stops the robot repeating
        # the same missions forever and lets FTUE end.
        if isinstance(data.get("mentor_behavior"), dict):
            return self.ingest_mentor_behavior(device_id, data)

    # ---- STT extension point ----
    # ---- STT (AI seam §1) ----
    def set_transcriber(self, transcriber):
        """Install an STT engine (moxie_sdk.stt.Transcriber). Without one, audio
        frames are ignored (text turns still work)."""
        self._transcriber = transcriber

    def _stt_session(self, device_id):
        from moxie_sdk.stt import SttSession
        s = self._stt_sessions.get(device_id)
        if s is None:
            s = SttSession(self._transcriber)
            self._stt_sessions[device_id] = s
        return s

    def feed_stt(self, device_id, vad, audio: bytes = b"", uuid: str = ""):
        """Feed one VAD-tagged audio frame; on END_OF_SPEECH, transcribe and publish a
        zmqSTTResponse back to the robot (/devices/{id}/commands/zmq). Returns the
        transcript when final, else None. No transcriber → no-op."""
        if self._transcriber is None:
            return None
        from moxie_sdk.stt import build_stt_response
        if uuid:
            self._stt_uuid[device_id] = uuid          # frames of one utterance share it
        transcript = self._stt_session(device_id).feed(vad, audio)
        if transcript is None:
            return None
        resp = build_stt_response(self._stt_uuid.pop(device_id, device_id), transcript)
        if self.client:
            self.client.publish(f"/devices/{device_id}/commands/zmq", json.dumps(resp))
        self._note("stt", f"👂 heard: '{transcript[:40]}'")
        return transcript

    def handle_zmq(self, device_id, payload):
        """STT audio arrives on events/zmq. The real robot sends
        `b'<proto.full_name>:' + zmqSTTRequest_bytes` (needs the compiled proto to
        decode — the remaining wire step). A JSON frame
        `{vad, audio_content(base64), uuid}` is accepted here too, so the STT pipeline
        (accumulate → transcribe → publish zmqSTTResponse) is exercised end-to-end."""
        try:
            data = json.loads(payload)
        except Exception:
            data = None
        if isinstance(data, dict) and "vad" in data:
            import base64
            audio = b""
            if data.get("audio_content"):
                try:
                    audio = base64.b64decode(data["audio_content"])
                except Exception:
                    audio = b""
            return self.feed_stt(device_id, data["vad"], audio, data.get("uuid", ""))
        # real robot: `b'<full_name>:' + zmqSTTRequest` protobuf
        raw = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode()
        from moxie_sdk.stt import decode_zmq_stt_frame
        frame = decode_zmq_stt_frame(raw)
        if frame is not None:
            return self.feed_stt(device_id, frame["vad"], frame["audio"], frame["uuid"])
        if not getattr(self, "_warned_stt", False):
            note = ("STT audio but no transcriber is set (text turns still work)"
                    if not self._transcriber else "unrecognized events/zmq frame")
            print(f"[runtime] ⚠️  {note}; see handle_zmq().")
            self._warned_stt = True

    # ---- publish a chat response ----
    def _publish_chat(self, device_id, event_id, backend, text, markup="",
                      actions=None, end_turn=False, result=ResultCode.SUCCESS,
                      modules=None, mood=None, dialog_act=None,
                      chunk_num=None, is_completed=None, safety=None):
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood, dialog_act=dialog_act, modules=modules,
                                   chunk_num=chunk_num, is_completed=is_completed,
                                   safety=safety)
        self.client.publish(f"/devices/{device_id}/commands/remote_chat", json.dumps(resp))
