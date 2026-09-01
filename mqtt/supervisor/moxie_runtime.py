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
from moxie_sdk.types import Turn, Reply, RobotContext, ChildProfile, Action, ResultCode  # noqa
from moxie_sdk.wire import build_chat_response  # pure RemoteChat response encoder
from markup import make_markup  # simple passthrough markup (automarkup pluggable)

# paho is imported lazily in _build_client() so the runtime + turn pipeline can be
# imported and integration-tested without the broker client installed.

CONNECT_RE = re.compile(r"connected from (.*) as (d_[a-f0-9-]+)", re.I)
DISCONNECT_RE = re.compile(r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected)", re.I)


def default_config(child: ChildProfile) -> dict:
    """The config pushed to a robot on connect. pairing_status MUST stay 'paired'."""
    return {
        "pairing_status": "paired",
        "audio_volume": "0.6",
        "screen_brightness": "1.0",
        "audio_wake_set": "off",
        "timezone_id": "America/Los_Angeles",
        "child_pii": {"nickname": child.nickname, "input_speed": child.input_speed},
        "settings": {"props": {
            "touch_wake": "1", "wake_alarms": "1", "wake_button": "1", "doa_range": "80",
            "target_all": "1", "gcp_upload_disable": "1", "local_stt": "on",
            "max_enroll": "2", "audio_wake": "1", "cloud_schedule_reset_threshold": "5",
            "brain_entrances_available": "1", "default_loglevel": "warning",
            # stt "4" = stream audio to us over ZMQ (our STT path); needs handle_zmq wired.
            "stt": "4",
        }},
    }


class MoxieRuntime:
    def __init__(self, app, host="127.0.0.1", port=1883, child: ChildProfile | None = None):
        self.app = app
        self.child = child or ChildProfile()
        self.host, self.port = host, port
        self.robots: dict[str, RobotContext] = {}
        self.history: dict[str, list] = {}
        self._memory_dir = os.environ.get("MOXIE_MEMORY_DIR", "").strip()
        self._max_memory = int(os.environ.get("MOXIE_MEMORY_TURNS", "40"))
        self._load_memory()
        from concurrent.futures import ThreadPoolExecutor
        from collections import deque
        self._pool = ThreadPoolExecutor(max_workers=8)
        self.recent = deque(maxlen=120)          # rolling broker/runtime activity for the UI
        self.started_at = time.time()
        # The MQTT client is created lazily in run() so the runtime can be constructed
        # + integration-tested with an injected fake transport (no broker required).
        self.client = None

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

            def do_GET(self):
                if self.path.split("?")[0] != "/status":
                    self.send_response(404); self.end_headers(); return
                body = _json.dumps({
                    "ok": True, "app": rt.app.name,
                    "uptime_s": int(time.time() - rt.started_at),
                    "robots": [{"device_id": r.device_id, "firmware": r.firmware,
                                "child": r.child.nickname} for r in rt.robots.values()],
                    "recent": list(rt.recent)[-60:],
                }).encode()
                self.send_response(200)
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
            state = json.loads(payload)
            fw = state.get("software_version") or state.get("version")
            if fw and self.robots.get(device_id):
                self.robots[device_id].firmware = fw
        except Exception:
            pass

    # ---- config push ----
    def _push_config(self, device_id):
        cfg = default_config(self.child)
        self.client.publish(f"/devices/{device_id}/config", json.dumps(cfg))
        print(f"[runtime] → pushed config to {device_id} (pairing_status=paired)")

    # ---- events ----
    def _on_event(self, device_id, name, payload):
        robot = self.robots.get(device_id) or RobotContext(device_id=device_id, child=self.child)
        if name.startswith("remote-chat"):
            return self._on_remote_chat(device_id, robot, payload)
        if name == "zmq":
            return self.handle_zmq(device_id, payload)
        if name == "client-service-activity-log":
            return self._on_activity(device_id, payload)
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
        # Run the (possibly slow) app + LLM off the MQTT loop so we never block it.
        self._pool.submit(self._handle_turn, device_id, event_id, speech, turn)

    def _handle_turn(self, device_id, event_id, speech, turn):
        try:
            reply = self.app.respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            reply = Reply(text="Hmm, let me think about that.")
        h = self.history.setdefault(device_id, [])
        if speech:
            h.append({"role": "user", "content": speech})
        h.append({"role": "assistant", "content": reply.text})
        self._save_memory(device_id)
        markup = reply.markup if reply.markup is not None else make_markup(reply.text)
        self._note("chat", f"💬 '{speech[:30]}' → '{reply.text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{reply.text[:60]}'", flush=True)
        self._publish_chat(device_id, event_id, "router", reply.text, markup,
                           actions=reply.actions, end_turn=reply.end_turn,
                           result=reply.result_code, mood=reply.mood,
                           dialog_act=reply.dialog_act)

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

    def _on_activity(self, device_id, payload):
        try:
            data = json.loads(payload)
        except Exception:
            return
        query = data.get("query")
        subtopic = data.get("subtopic")
        # answer schedule / mentor_behaviors / license queries minimally
        if query in ("schedule", "mentor_behaviors", "license"):
            result = {"schedule": {}, "mentor_behaviors": [], "license": {}}.get(query, {})
            self.client.publish(f"/devices/{device_id}/commands/query_result",
                                json.dumps({"command": "query_result", "query": query,
                                            "result": result}))

    # ---- STT extension point ----
    def handle_zmq(self, device_id, payload):
        """STT audio (embodied.perception.audio.zmqSTTRequest) arrives here as
        `b'<proto.full_name>:' + protobuf_bytes`. Wiring faster-whisper here (decode
        the proto, accumulate audio_content until END_OF_SPEECH, transcribe, publish
        a zmqSTTResponse to /devices/{id}/commands/zmq) is the next build step —
        see docs/architecture/mqtt-and-conversation.md §4.3/§5.2. Not yet implemented."""
        if not getattr(self, "_warned_stt", False):
            print("[runtime] ⚠️  received STT audio (events/zmq) — faster-whisper STT "
                  "not yet wired; see handle_zmq(). Text turns still work.")
            self._warned_stt = True

    # ---- publish a chat response ----
    def _publish_chat(self, device_id, event_id, backend, text, markup="",
                      actions=None, end_turn=False, result=ResultCode.SUCCESS,
                      modules=None, mood=None, dialog_act=None):
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood, dialog_act=dialog_act, modules=modules)
        self.client.publish(f"/devices/{device_id}/commands/remote_chat", json.dumps(resp))
