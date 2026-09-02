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
        # STT (AI seam §1): an optional transcriber + a per-device VAD accumulator.
        self._transcriber = None
        self._stt_sessions = {}
        self._stt_uuid = {}      # utterance uuid per device (set on any frame that has one)
        # Parent-console config editing: per-device RobotCloudConfig overrides.
        self._config_overrides = {}
        # TTS (AI seam §3): an optional server voice (for the SIM; a real robot self-synthesizes).
        self._synth = None

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

            def do_GET(self):
                if self.path.split("?")[0] != "/status":
                    self.send_response(404); self.end_headers(); return
                body = _json.dumps(rt.status_snapshot()).encode()
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
        self._maybe_synthesize(device_id, markup, event_id)

    # ---- TTS (AI seam §3) — server voice for the SIM ----
    def set_synthesizer(self, synth):
        """Install a server-side TTS engine (moxie_sdk.tts.Synthesizer). The SIM plays
        the resulting audio; a real robot self-synthesizes so this is SIM-only."""
        self._synth = synth

    def _maybe_synthesize(self, device_id, markup, event_id=""):
        """If a synthesizer is set, render the line and publish a CloudTTSResponse to
        /devices/{id}/commands/tts. TTS failure never breaks the turn."""
        if self._synth is None:
            return None
        try:
            from moxie_sdk.tts import synthesize_cloud_tts
            resp = synthesize_cloud_tts(self._synth, markup, event_id=event_id)
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
        if not getattr(self, "_warned_stt", False):
            note = ("received protobuf STT audio (events/zmq) — decoding zmqSTTRequest "
                    "needs the compiled proto (remaining wire step)") if self._transcriber \
                else "received STT audio but no transcriber is set (text turns still work)"
            print(f"[runtime] ⚠️  {note}; see handle_zmq().")
            self._warned_stt = True

    # ---- publish a chat response ----
    def _publish_chat(self, device_id, event_id, backend, text, markup="",
                      actions=None, end_turn=False, result=ResultCode.SUCCESS,
                      modules=None, mood=None, dialog_act=None):
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood, dialog_act=dialog_act, modules=modules)
        self.client.publish(f"/devices/{device_id}/commands/remote_chat", json.dumps(resp))
