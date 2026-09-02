"""
Moxie robot-cloud runtime (supervisor). Speaks the robot's MQTT protocol and turns
it into clean MoxieApp calls. Based on the protocol documented in
docs/architecture/mqtt-and-conversation.md (verified against OpenMoxie, MIT).

Responsibilities:
  * subscribe to all devices' events/state + the broker log
  * detect robot connect/disconnect (regex on $SYS/broker/log)
  * push each robot its config on connect — the full one
    (pairing_status="paired" + child_pii) only to a **permitted** device; an
    unpermitted one is pending and gets a minimal, child-free config
  * route `events/remote-chat` (backend:router) turns → MoxieApp.respond → reply
  * maintain per-device conversation history from `notify` events
  * STT (events/zmq) is a documented extension point (see handle_zmq)

This is the transport; the *brain* is whatever MoxieApp is injected.
"""
from __future__ import annotations
import json, re, sys, os, threading, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from moxie_sdk.types import (Turn, Reply, ReplyChunk, RobotContext,  # noqa
                             ChildProfile, Action, ActionType, ResultCode)
from moxie_sdk.wire import (build_chat_response, build_activity_response,  # pure encoders
                            parse_mentor_behavior)
from moxie_sdk.store import JsonStore, MemoryStore       # durable per-robot JSON store
from moxie_sdk.filler import pick_filler                 # "let me think" lines + markup
from moxie_sdk import safety as safety_seam              # InputSafety classifier (ai-seam §2)
from moxie_sdk import presence as presence_seam          # vision events -> presence (vision.md)
from moxie_sdk import telehealth as telehealth_seam      # 🎭 puppet mode wire (audit ADOPT #7)
from moxie_sdk import vocab as vocab_seam                # the frozen mood/markup catalog
from moxie_sdk import voice_settings as voice_seam       # 🎚️ which voice / which ears
from moxie_sdk.cloud_config import LoggingPolicy         # the child-privacy gate
from markup import make_markup  # the markup floor (moxie_sdk.automarkup) behind the seam

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

# Long-term memory's own LoggingPolicy default, for the same reason as the safety
# journal's: the RobotCloudConfig we push defaults to NO_DATA, which is about what the
# *robot uploads*. Memory is text our own server derives from turns that already reached
# it, so it defaults to NO_MEDIA (allowed) — and a parent who explicitly sets
# `logging_policy=NO_DATA` turns writing off entirely (reads and erase still work).
MEMORY_POLICY = LoggingPolicy.NO_MEDIA

# How long a child must have been out of sight before Moxie says hello on its own when
# they walk back in front of it (`eb-found-face` after an `eb-lost-target`). Short enough
# that leaving the room and coming back is noticed, long enough that stepping out of frame
# for a moment is not. 0 turns the unprompted greeting off entirely; `MOXIE_GREET_AFTER_S`
# overrides. See docs/architecture/vision.md "The greeting rule".
DEFAULT_GREET_AFTER_S = 300.0


class MoxieRuntime:
    def __init__(self, app, host="127.0.0.1", port=1883, child: ChildProfile | None = None,
                 store: JsonStore | None = None, brain_budget_s=None, streaming=None,
                 safety=None, allow_unverified_bots=None, greet_after_s=None):
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
        # Device allowlist (the pairing gate). A robot that is not permitted is tracked
        # as *pending* and served a minimal, child-free config — see `_push_config` and
        # `_serve_unpermitted`. `None` = read the policy at call time (env, then the
        # durable fleet record, then closed); True/False pins it for this process, which
        # is what the SIL harness and the turn-loop tests use.
        self._allow_unverified_bots = allow_unverified_bots
        self._permits_cache = None       # ((path, mtime, size), flag, devices)
        # TTS (AI seam §3): an optional server voice (for the SIM; a real robot self-synthesizes).
        self._synth = None
        # 🎚️ Voice picker (backlog/voice-picker.md): the appliance's engine builders +
        # cached gateway discovery, injected by `run.py` (`config.voice_engines()`). None
        # means the picker offers the built-ins only — this module never imports `config`,
        # so a test drives the whole card with a fake and spends no gateway request.
        self._voice_engines = None
        self._voice_lock = threading.Lock()      # one swap at a time; never held in a turn
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
        # Presence (audit BEYOND #9). The robot's own eyes reach us as ordinary
        # RemoteChatRequests whose `speech` IS the event string — but only after the brain
        # subscribes (`EventSubscription.active[]`), which is why nobody has ever seen one.
        # See `moxie_sdk/presence.py` and docs/architecture/vision.md.
        self._presence_lock = threading.Lock()
        self._busy: set = set()                    # robots with a turn in flight
        self._last_greeting: dict[str, str] = {}   # never the same hello twice running
        self._pending_opener: dict[str, str] = {}  # hello queued for the next turn
        self._vision_subscribed: dict[str, str] = {}   # device -> module we subscribed for
        try:
            self.greet_after_s = float(
                greet_after_s if greet_after_s is not None
                else os.environ.get("MOXIE_GREET_AFTER_S") or DEFAULT_GREET_AFTER_S)
        except (TypeError, ValueError):
            self.greet_after_s = DEFAULT_GREET_AFTER_S
        self.vision = (os.environ.get("MOXIE_VISION") or "1").strip().lower() \
            not in ("0", "off", "false", "no")
        # Telehealth / "Be Moxie" (audit ADOPT #7): per-robot puppet state — the minted
        # session id, the state the ROBOT last reported, and a bounded in-memory
        # transcript ring. Runtime-level, not on RobotContext, so an operator can still
        # read the session after the robot drops off Wi-Fi. See the telehealth region.
        self._telehealth: dict = {}
        # Long-term memory (content-module-contract.md → `volley.persist_data`): the app
        # owns the store; the runtime owns the parent's privacy switch. See the memory
        # region below.
        self._wire_memory_policy()

    def _build_client(self):
        import paho.mqtt.client as mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="supervisor")
        # The supervisor's broker credential (security-broker-auth.md §2.2). It is the
        # ONE fleet-wide identity: `$SYS/broker/log` — the connect watch below — and any
        # write outside a client's own device subtree are supervisor-only once the ACL
        # is loaded. Unset (a bare-metal dev broker, the SIL harness, CI) leaves this an
        # anonymous client, byte-for-byte what it was before.
        try:
            from config import broker_credentials
            username, password = broker_credentials()
        except Exception:                      # config not importable → anonymous
            username, password = "", ""
        if username and password:
            self.client.username_pw_set(username, password)
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

    # ---- long-term memory (persist_data + what a parent may read/erase) ----
    # The *conversation history* above is the rolling transcript. This is the other
    # memory: the durable facts a content module keeps between conversations
    # (docs/architecture/content-module-contract.md → `volley.persist_data` /
    # `session.summarize()`), stored by `moxie_sdk/store.py::MemoryStore`.
    #
    # The app owns the store (ContentApp builds one); the runtime owns two things the
    # app cannot know: the parent's per-device privacy switch, and *when a conversation
    # ended* — which is the only moment the whole transcript still exists.
    #
    # BEYOND #4 (openmoxie-feature-audit.md §4.2) says a memory a parent cannot read or
    # erase is not acceptable on a child's device. `/memory` is that floor: GET to read
    # what Moxie remembers (every item with its id and provenance), DELETE to forget one
    # item, one namespace or all of it, POST to erase the same way or to **correct** one
    # item in place. The console's 🧠 card is the browser over exactly these.

    def memory_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy governing what may be *remembered* about this child — the
        parent's explicit `logging_policy` if there is one, else `MEMORY_POLICY`.
        `NO_DATA` means no memory is written at all (reads and erase still work).

        Read from the **effective** config (fleet ⊕ per-robot), so a house rule set once
        for the appliance turns memory off for every robot on it, and a single robot can
        still be set apart."""
        raw = (self.effective_config(device_id) or {}).get("logging_policy")
        if raw is None:
            return MEMORY_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return MEMORY_POLICY

    def _wire_memory_policy(self):
        """Hand the app's memory store this runtime's per-device privacy gate.

        Done here rather than at construction so an app built by `config.build_app()`
        (which knows nothing about a device's config overrides) still honours them."""
        mem = getattr(self.app, "memory", None)
        if mem is not None and getattr(mem, "policy", None) is None:
            try:
                mem.policy = self.memory_policy
            except Exception:
                pass

    def memory_store(self):
        """The app's memory store, or a read-only view of the same files for an app
        that has none (so `/memory` answers for any app)."""
        mem = getattr(self.app, "memory", None)
        if mem is not None:
            return mem
        return MemoryStore(self.store, policy=self.memory_policy)

    def memory_view(self, device_id) -> dict:
        """What Moxie remembers about one child, by namespace, with provenance."""
        mem = self.memory_store()
        view = mem.view(device_id)
        if device_id not in self.robots and not view.get("namespaces"):
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}"}
        view.update({"ok": True, "device_id": device_id,
                     "policy": self.memory_policy(device_id).name})
        return view

    def erase_memory(self, device_id, namespace=None, item=None) -> dict:
        """Forget one item, one namespace, or everything for this robot.

        Never policy-gated: a parent must always be able to delete. `item` is the finest
        cut — one wrong line goes without costing the rest of what that activity learned
        (BEYOND #4's other half)."""
        if item:
            removed = self.memory_store().erase_item(device_id, namespace, item)
            what = f"{namespace}/{item}"
        else:
            removed = self.memory_store().erase(device_id, namespace)
            what = namespace or "all"
        self._note("memory", f"🧽 erased memory: {what}")
        print(f"[runtime] 🧽 erased memory for {device_id} ({what}): {removed}",
              flush=True)
        out = self.memory_view(device_id)
        if not out.get("ok"):                     # erasing the last of it is still a hit
            out = {"ok": True, "device_id": device_id, "namespaces": {}, "bytes": 0,
                   "policy": self.memory_policy(device_id).name}
        out["erased"] = bool(removed)
        out["namespace"] = namespace or "all"
        if item:
            out["item"] = str(item)
        return out

    def edit_memory_item(self, device_id, namespace, item, text) -> dict:
        """Correct one remembered item — the other thing a parent needs when a summary is
        wrong but not worthless ("Puppy sleeps on **his** bed" → "…my bed").

        The store keeps the item's id, **pins** it (a human decision outranks decay) and
        re-runs the two rules that decide what may live in a prompt: the safety
        classifier, and the no-verbatim check against this robot's recent conversation —
        so a parent cannot paste the child's own words back in. A refusal raises, and the
        handler turns it into a 400 with the reason. Not policy-gated: fixing a wrong line
        must work even on a `NO_DATA` robot, where the only alternative is deleting it."""
        edited = self.memory_store().edit_item(
            device_id, namespace, item, text,
            history=list(self.history.get(device_id) or []))
        self._note("memory", f"✏️ corrected memory: {namespace}/{item}")
        print(f"[runtime] ✏️ corrected memory for {device_id} ({namespace}/{item})",
              flush=True)
        out = self.memory_view(device_id)
        out["edited"] = True
        out["namespace"] = str(namespace)
        out["item"] = str(item)
        return out

    # ---- end of a conversation (the contract's complete_handler moment) ----
    def _maybe_end_conversation(self, device_id, actions):
        """End the conversation if the answer carried an EXIT action (`<exit>`)."""
        for a in actions or []:
            if getattr(a, "type", None) == ActionType.EXIT:
                return self._end_conversation(device_id, "exit", inline=True)
        return None

    def _end_conversation(self, device_id, reason: str, *, robot=None, inline=False):
        """Tell the app a conversation finished, so it can write long-term memory.

        `inline=True` when we are already on a worker thread (the turn path); otherwise
        the work is submitted to the pool, because this can make a brain call and the
        MQTT loop must never block on one. A failure here is logged and dropped: a
        summary is a nice-to-have, and a child's session must not end badly for it."""
        robot = robot or self.robots.get(device_id)
        history = list(self.history.get(device_id) or [])
        if robot is None or not history:
            return None
        def _run():
            try:
                self.app.on_session_end(robot, history, reason)
            except Exception as e:
                print(f"[runtime] app.on_session_end error: {e}", flush=True)
        if inline:
            return _run()
        try:
            return self._pool.submit(_run)
        except RuntimeError:                      # pool already shutting down
            return _run()

    def status_snapshot(self) -> dict:
        """The supervisor + robots snapshot the parent console reads (JSON). Each robot
        carries its live state (battery/volume/wifi/mode/firmware) from the last /state."""
        robots = []
        permits = self.permits()
        open_fleet = self.allow_unverified_bots()
        for r in self.robots.values():
            st = r.extra.get("status", {})
            permitted = open_fleet or r.device_id in permits["devices"]
            robots.append({
                "device_id": r.device_id, "child": r.child.nickname,
                # The pairing gate, per robot: `pending` is a robot that reached the
                # broker but is not on the permit list — it is being served the minimal
                # child-free config and nothing else until a parent lets it in.
                "permitted": permitted, "pending": not permitted,
                "permit_label": (permits["devices"].get(r.device_id) or {}).get("label", ""),
                "firmware": r.firmware or st.get("robot_firmware_version"),
                "battery_level": st.get("battery_level"),
                "audio_volume": st.get("audio_volume"),
                "wifi_ssid": st.get("wifi_ssid"), "mode": st.get("mode"),
                "ota_reboot_required": st.get("ota_reboot_required"),
                "config_overrides": self._config_overrides.get(r.device_id, {}),
                "config_effective": self.effective_config(r.device_id),
                # The face cache-buster as this robot's next /config push will carry it
                # (`child_pii.id`) — "" when no face is chosen and the field is omitted.
                # Surfaced so a parent (and a test) can see that changing the look really
                # did re-key the texture, without reading the MQTT wire.
                "face_cache_id": self.face_cache_id(r.device_id),
                "telemetry_count": len(r.extra.get("telemetry", [])),
                "safety_total": int((self.store.read(
                    r.device_id, safety_seam.COUNTS_COLLECTION, {}) or {}).get("total", 0)),
                "safety_unreviewed": sum(
                    1 for e in (self.store.read(
                        r.device_id, safety_seam.EVENTS_COLLECTION, []) or [])
                    if isinstance(e, dict) and not e.get("reviewed")),
            })
        from moxie_sdk.cloud_config import schedulable_module_ids
        from moxie_sdk.faces import face_catalog
        return {"ok": True, "app": self.app.name,
                "uptime_s": int(time.time() - self.started_at),
                "fleet_config": self.fleet_config(),
                "allow_unverified_bots": open_fleet,
                "pending_count": sum(1 for r in robots if r["pending"]),
                "schedule_modules": list(schedulable_module_ids()),
                # The appearance catalog the 🎨 card renders (audit ADOPT #9). Published
                # rather than hard-coded in the console so the two can never disagree
                # about which slots exist or which options are actually cited.
                "face_catalog": face_catalog(),
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
                → that robot's stored telemetry Packets rolled up for the insights view;
                GET /schedule?device_id=… → the planned day + why each activity is on it;
                GET /telehealth?device_id=… → 🎭 puppet mode + the live transcript;
                GET /voice → 🎚️ the speech/listening pickers: what this appliance can
                use, what is in force and what the default would be (fleet-level);
                GET /permits → the device allowlist + who is pending.
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

            def do_DELETE(self):
                """`DELETE /memory?device_id=…[&namespace=…[&item=…]]` — a parent erasing
                what Moxie remembers. With `item`, exactly that one line goes; with only
                a namespace, one activity; with neither, everything for that robot."""
                from urllib.parse import urlparse
                u = urlparse(self.path)
                if u.path != "/memory":
                    self.send_response(404); self.end_headers(); return
                return self._memory_write(u.query)

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

                `POST /voice` with `{"speech": "gateway:piper-amy", "listening": …}`
                persists the 🎚️ picker's choice and swaps the live engines; the next turn
                uses them. `POST /voice/test?device_id=…` speaks one line through the
                engine actually installed and publishes it to that robot — see `_voice`.

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
                    # The pairing gate lives on the transport boundary, so there is ONE
                    # place a device that is not permitted can be refused service — no
                    # handler can forget it. `/state` is deliberately still processed:
                    # it is how an unknown robot becomes visible as *pending* at all.
                    if not self.is_permitted(device_id):
                        return self._serve_unpermitted(device_id, parts[4], msg.payload)
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
            # A pending (unpermitted) robot never reaches the app: the brain is a service
            # this appliance provides to the family's robot, not to whatever connected.
            # `set_permit` runs `on_connect` at the moment a parent lets it in.
            if not self.is_permitted(device_id):
                return
            try:
                self.app.on_connect(robot)
            except Exception as e:
                print(f"[runtime] app.on_connect error: {e}", flush=True)
        threading.Timer(1.0, _settle).start()

    def _device_disconnect(self, device_id: str):
        robot = self.robots.pop(device_id, None)
        if robot:
            print(f"[runtime] robot disconnected: {device_id}")
            self._end_conversation(device_id, "disconnect", robot=robot)
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
    FLEET_CONFIG_COLLECTION = "config"          # → $MOXIE_DATA_DIR/fleet/config.json

    def fleet_config(self) -> dict:
        """The appliance-wide default overrides — one place to set house rules for every
        robot on this box (audit ADOPT #6). Read from the store each time so an edit from
        another process (or a hand-edited `fleet/config.json`) is picked up on the next
        push. `{}` when none was ever set, which is the pre-fleet behavior exactly."""
        cfg = self.store.read_shared(self.FLEET_CONFIG_COLLECTION, {})
        return dict(cfg) if isinstance(cfg, dict) else {}

    def effective_config(self, device_id) -> dict:
        """`fleet ⊕ per-robot` — the override layer stack this robot's config is built
        from (the builder's own kwarg defaults are the layer underneath)."""
        from moxie_sdk.cloud_config import merge_config_layers
        return merge_config_layers(self.fleet_config(),
                                   self._config_overrides.get(device_id, {}))

    def face_cache_id(self, device_id) -> str:
        """The `child_pii.id` this robot's next `/config` push will carry — the face
        cache-buster (`moxie_sdk/faces.py`, "the cache-buster"). `""` when no face is
        chosen, which is exactly when the field is omitted from the document.

        Read off the same `fleet ⊕ per-robot` layers `_push_config` builds from, so it is
        the value that will actually go out, not a second opinion about it."""
        face = (self.effective_config(device_id) or {}).get("face")
        if not face:
            return ""
        from moxie_sdk.faces import face_child_id, face_options_list, validate_face
        try:
            labels = face_options_list(validate_face(face))
        except ValueError:
            return ""
        if not labels:
            return ""
        child = (self.robots[device_id].child if device_id in self.robots else self.child)
        return face_child_id(labels, child_key=child.nickname)

    def update_fleet_config(self, **overrides):
        """Parent-console *fleet* config edit: merge overrides into the appliance-wide
        defaults, persist them, and re-push every connected robot's config so the change
        lands everywhere at once. Per-robot overrides still win."""
        cfg = self.fleet_config()
        cfg.update(overrides)
        self.store.write_shared(self.FLEET_CONFIG_COLLECTION, cfg)
        self._note("config", f"⚙️  fleet config updated: {', '.join(overrides) or '—'}")
        for device_id in list(self.robots):
            self._push_config(device_id)
        return cfg

    # ---- the pairing gate: which devices this appliance serves --------------------
    #
    # The broker accepts anonymous connections (mqtt-and-conversation.md §3b — the robot's
    # RS256 JWT is never verified, exactly as in the original LAN model), so "reached the
    # port" must not mean "is my child's robot". Without a gate the supervisor pushes
    # `pairing_status:"paired"` **plus the child's `child_pii`** to whatever announces
    # itself on `/devices/{id}/state`. On a home network that is a real exposure.
    #
    # So: a durable permit list, closed by default. The idea is OpenMoxie's
    # `MoxieDevice.permit` + `HiveConfiguration.allow_unverified_bots` (MIT — credited in
    # ATTRIBUTION.md; no code copied, and note that in OpenMoxie the flag is stored but
    # never enforced on the MQTT path, so this is the idea taken further, not a port).
    FLEET_PERMITS_COLLECTION = "permits"        # → $MOXIE_DATA_DIR/fleet/permits.json

    def permits(self) -> dict:
        """The durable permit record, normalized:
        `{"allow_unverified_bots": bool, "devices": {device_id: {permitted_at, label}}}`.

        Like `fleet_config`, this reflects the file rather than a load-time snapshot, so a
        permit granted in another process — or hand-edited into `fleet/permits.json` — is
        picked up without a restart. Unlike `fleet_config` it is on a **hot** path (the
        gate runs on every inbound message, including each audio frame), so the parse is
        memoized against the file's `(mtime, size)`: a changed file re-reads, an unchanged
        one costs a `stat`. A missing/corrupt file reads as "nothing permitted", which
        fails **closed**."""
        path = self.store.shared_path(self.FLEET_PERMITS_COLLECTION)
        try:
            st = os.stat(path)
            key = (path, st.st_mtime_ns, st.st_size)
        except OSError:
            key = (path, None, None)
        cached = self._permits_cache
        if cached is None or cached[0] != key:
            rec = self.store.read_shared(self.FLEET_PERMITS_COLLECTION, {})
            if not isinstance(rec, dict):
                rec = {}
            devices = rec.get("devices")
            if not isinstance(devices, dict):
                devices = {}
            cached = (key, bool(rec.get("allow_unverified_bots")),
                      {str(k): (v if isinstance(v, dict) else {})
                       for k, v in devices.items()})
            self._permits_cache = cached
        # A fresh mapping every call: `set_permit` mutates what it gets back, and the
        # cache must never be edited through a caller's reference.
        return {"allow_unverified_bots": cached[1], "devices": dict(cached[2])}

    def allow_unverified_bots(self) -> bool:
        """True when this appliance serves **any** robot that connects (the pre-gate
        behavior). Precedence, most explicit first:

          1. the constructor argument (`MoxieRuntime(..., allow_unverified_bots=True)`);
          2. `MOXIE_ALLOW_UNVERIFIED_BOTS` — the migration switch for a deployment that
             was running before the gate existed (`1/true/on/yes` opens, `0/off/...`
             pins it shut);
          3. the durable fleet flag a parent toggles in the console;
          4. **False** — the safe default."""
        if self._allow_unverified_bots is not None:
            return bool(self._allow_unverified_bots)
        env = (os.environ.get("MOXIE_ALLOW_UNVERIFIED_BOTS") or "").strip().lower()
        if env:
            return env not in ("0", "off", "false", "no")
        return self.permits()["allow_unverified_bots"]

    def is_permitted(self, device_id) -> bool:
        """May this device be served the child's config and the brain?"""
        return self.allow_unverified_bots() or str(device_id) in self.permits()["devices"]

    def pending_robots(self) -> list:
        """Connected-but-unpermitted device ids — what the console's "Pending robots"
        list shows, and the only place a parent needs to look to let a new robot in."""
        return [d for d in self.robots if not self.is_permitted(d)]

    def set_permit(self, device_id, permitted: bool = True, label: str = "") -> dict:
        """Permit or revoke one device, durably, and make it true on the wire *now*:
        permitting a pending robot re-pushes its full config immediately (no reconnect,
        no restart), revoking one re-pushes the minimal un-paired document so the child's
        data stops being served to it on the same tick."""
        device_id = str(device_id or "").strip()
        if not device_id:
            raise ValueError("device_id is required")
        rec = self.permits()
        if permitted:
            rec["devices"][device_id] = {"permitted_at": int(time.time()),
                                         "label": str(label or "")}
        else:
            rec["devices"].pop(device_id, None)
        self.store.write_shared(self.FLEET_PERMITS_COLLECTION, rec)
        self._permits_cache = None      # our own write invalidates outright,
                                        # never trusting mtime granularity
        self._note("permit", f"{'✅ permitted' if permitted else '⛔ revoked'} {device_id}")
        if device_id in self.robots:
            self._push_config(device_id)
            if permitted:
                try:
                    self.app.on_connect(self.robots[device_id])
                except Exception as e:
                    print(f"[runtime] app.on_connect error: {e}", flush=True)
        return self.permits_view()

    def set_allow_unverified_bots(self, allowed: bool) -> dict:
        """The fleet-wide "serve any robot that connects" toggle. Flipping it re-pushes
        every connected robot's config, so a robot that was pending starts (or stops)
        being served without waiting for a reconnect."""
        rec = self.permits()
        rec["allow_unverified_bots"] = bool(allowed)
        self.store.write_shared(self.FLEET_PERMITS_COLLECTION, rec)
        self._permits_cache = None      # our own write invalidates outright,
                                        # never trusting mtime granularity
        self._note("permit", f"🔓 allow_unverified_bots={bool(allowed)}"
                             if allowed else "🔒 allow_unverified_bots=False")
        for device_id in list(self.robots):
            self._push_config(device_id)
        return self.permits_view()

    def permits_view(self) -> dict:
        """The console-facing permit view: the flag (as *enforced*, env included), the
        stored flag, the permit list, and which connected robots are still pending."""
        rec = self.permits()
        return {"ok": True,
                "allow_unverified_bots": self.allow_unverified_bots(),
                "allow_unverified_bots_stored": rec["allow_unverified_bots"],
                "permits": [{"device_id": d,
                             "permitted_at": v.get("permitted_at"),
                             "label": v.get("label") or ""}
                            for d, v in sorted(rec["devices"].items())],
                "pending": sorted(self.pending_robots()),
                "connected": sorted(self.robots)}

    # What a *pending* device is allowed to receive. Nothing about the child, nothing
    # from the brain, nothing from the store — one fixed line so an owner watching their
    # own robot hears why it is quiet instead of nothing at all. It names no one.
    NOT_PAIRED_LINE = ("I'm not connected to a family yet. "
                       "Ask a grown-up to add me in the Moxie console.")

    def _serve_unpermitted(self, device_id, name, payload):
        """Everything a not-permitted device gets on `/events/…`, in one place.

        * `remote-chat` prompt → one fixed, child-free line; the brain is never called,
          no history is kept, nothing is stored. `notify` (the robot telling us what it
          said) is dropped — a pending device does not get a conversation record.
        * `client-service-activity-log` **queries** (`schedule`, `mentor_behaviors`,
          `license`) → the CloudQueryResponse envelope with its *empty* value, so the
          robot's pull resolves instead of hanging; the reports on the same topic (what
          the child finished) are dropped rather than written to the store.
        * everything else — the microphone stream (`zmq`), telemetry, vision, module
          lifecycle — is dropped on the floor.
        """
        if name.startswith("remote-chat"):
            try:
                rcr = json.loads(payload)
            except Exception:
                return
            if rcr.get("command") == "notify":
                return
            backend = rcr.get("backend", "router")
            if backend == "data" and rcr.get("query") == "modules":
                return self._publish_chat(device_id, rcr.get("event_id"), backend, "",
                                          markup="", result=ResultCode.SUCCESS, modules=[])
            self._note("permit", f"⛔ turn refused — {device_id} is pending")
            return self._publish_chat(device_id, rcr.get("event_id"), backend,
                                      self.NOT_PAIRED_LINE,
                                      markup=make_markup(self.NOT_PAIRED_LINE),
                                      end_turn=True)
        if name == "client-service-activity-log":
            try:
                data = json.loads(payload)
            except Exception:
                return
            query = data.get("query")
            if data.get("subtopic") in (None, "", "query") and query in (
                    "schedule", "mentor_behaviors", "license"):
                resp = build_activity_response(query, None,
                                               request_id=data.get("request_id"))
                if self.client:
                    self.client.publish(f"/devices/{device_id}/commands/query_result",
                                        json.dumps(resp))
                return resp
            return None
        return None

    def _push_config(self, device_id):
        """Publish this robot's `/config`.

        **Permitted** (or the fleet allows unverified bots) → the full RobotCloudConfig,
        `pairing_status:"paired"` + `child_pii` + the parent's settings, exactly as
        before the gate existed. **Not permitted** → `build_unpaired_cloud_config()`: the
        not-paired status, no `child_pii`, no household settings, privacy gate shut."""
        from moxie_sdk.cloud_config import (build_robot_cloud_config,
                                            build_unpaired_cloud_config)
        if self.is_permitted(device_id):
            cfg = build_robot_cloud_config(self.child, **self.effective_config(device_id))
        else:
            cfg = build_unpaired_cloud_config()
            self._note("permit", f"⛔ {device_id} is not permitted — pending "
                                 f"(minimal config, no child data)")
        if self.client:
            self.client.publish(f"/devices/{device_id}/config", json.dumps(cfg))
        print(f"[runtime] → pushed config to {device_id} "
              f"(pairing_status={cfg.get('pairing_status')})")
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
        if "face" in overrides:
            # Worth its own line in the console's activity feed: this is the one config
            # edit whose result a child sees on the robot's face.
            from moxie_sdk.faces import describe_face
            look = describe_face(overrides["face"] or {}) or "the default look"
            self._note("config", f"🎨 look updated: {look}")
        return self._push_config(device_id)

    # ---- presence: the robot's own eyes (audit BEYOND #9) ------------------------
    #
    # Moxie runs its vision ON-DEVICE and never sends pixels; what it can send is a
    # handful of semantic strings — `eb-found-face`, `eb-lost-target`, `eb-qr-event`,
    # `eb-dr-event`, `eb-br-event` — with no bounding box, no position, no identity
    # (docs/architecture/vision.md §1.1-1.2, :47-58). Two facts shape everything here:
    #
    #  1. **They are not their own topic.** A subscribed event is delivered to the brain
    #     as the `speech` of an ordinary `RemoteChatRequest` ("instead of the modules
    #     receiving something the user said, it receives a special event string like
    #     `eb-found-face`" — OpenMoxie `doc/RemoteModuleAPI.md` §Event Handling, MIT; the
    #     same shape content-and-conversation.md:385-390 shows for QR). So the ingest
    #     point is the chat router, and a reply to that request is not merely legal, it is
    #     REQUIRED: "the remote module must produce some response for this input to
    #     continue the interaction."
    #  2. **Nothing arrives until we ask.** The events are "discarded by the application
    #     stack unless the active module is specifically interested" — the brain opts in
    #     with `RemoteChatAction.EventSubscription{clear, active[]}`
    #     (remote-chat-protocol.md:103-106). `_vision_subscription` attaches that.
    #
    # Everything below is INFERRED from that recovered catalog. No physical robot has ever
    # sent us one of these events.

    def _presence_state(self, robot) -> dict:
        """This robot's raw presence record (`moxie_sdk.presence.new_state()` shape)."""
        st = robot.extra.get("presence")
        return st if isinstance(st, dict) else presence_seam.new_state()

    def _ingest_vision(self, device_id, robot, name, payload, now=None) -> list:
        """Fold one vision event into the robot's presence state; return its signals.

        The state lives on `RobotContext.extra["presence"]` — bounded, JSON-safe, and
        rebuilt (never mutated) by the pure helper, so the MQTT loop only ever swaps a
        reference. The app's `on_event` hook is called for every one of them, so a game
        or agent can react to perception without knowing the wire at all."""
        now = time.time() if now is None else now
        with self._presence_lock:
            state, signals = presence_seam.update_presence(
                self._presence_state(robot), name, payload, now)
            robot.extra["presence"] = state
        for sig in signals:
            detail = ""
            if sig.get("away_s") is not None:
                detail = f" after {presence_seam.human_duration(sig['away_s'])}"
            elif sig.get("present_s") is not None:
                detail = f" after {presence_seam.human_duration(sig['present_s'])}"
            elif sig.get("value"):
                detail = f": {str(sig['value'])[:32]}"
            self._note("vision", f"eye {sig['name']}{detail} ({device_id})")
            print(f"[runtime] eye {name} -> {sig['name']}{detail} on {device_id}",
                  flush=True)
        try:
            self.app.on_event(robot, name, dict(payload) if isinstance(payload, dict) else {})
        except Exception as e:
            print(f"[runtime] app.on_event error: {e}", flush=True)
        return signals

    def _on_vision_turn(self, device_id, robot, rcr, name):
        """A vision event that arrived as a chat turn — the protocol-faithful path.

        We answer the request the robot is waiting on, and we never spend a brain call on
        it: either the greeting below (`SUCCESS`) or `NOREPLY_ACK` — ResultCode 6,
        "acknowledge only, no spoken line" (remote-chat-protocol.md:60), which is exactly
        the contract's field for "heard you, saying nothing"."""
        event_id = rcr.get("event_id")
        backend = rcr.get("backend", "router")
        signals = self._ingest_vision(device_id, robot, name, rcr.get("input_vars") or {})
        greeting = self._greeting_for(device_id, robot, signals)
        if greeting is None:
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.NOREPLY_ACK)
        text, markup = greeting
        self._note("chat", f"hello (unprompted): '{text[:40]}'")
        print(f"[runtime] 👋 {device_id} walked back in -> '{text}'", flush=True)
        self._publish_chat(device_id, event_id, backend, text, markup,
                           result=ResultCode.SUCCESS)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
        return None

    def _greeting_for(self, device_id, robot, signals):
        """`(text, markup)` if this robot has earned an unprompted hello, else None.

        The rule, and every gate on it:

        * an **`arrived`** signal whose `away_s` is at least `greet_after_s`
          (`MOXIE_GREET_AFTER_S`, default 300 s; **0 = off**). A first-ever sighting has
          `away_s = None` and never greets — Moxie does not shout at a stranger.
        * **once per absence** — `greeted_at` is stamped on the presence record and must
          predate the next `eb-lost-target` before another hello is possible.
        * **never over a turn** — a robot with a turn in flight gets the line *queued* for
          the start of the next turn instead (`_speak_opener`), so Moxie never talks over
          its own answer.
        * **never to an unpermitted robot** — the pairing gate already refuses their
          events upstream (`_serve_unpermitted`); this is the belt to that's braces.
        * **never in bedtime hours** — read-only use of `effective_config`.
        """
        if self.greet_after_s <= 0:
            return None
        arrived = next((s for s in signals if s.get("name") == "arrived"), None)
        if arrived is None:
            return None
        away = arrived.get("away_s")
        if away is None or away < self.greet_after_s:
            return None
        if not self.is_permitted(device_id):
            return None
        if self._in_bedtime(device_id):
            self._note("vision", f"hello suppressed (bedtime) for {device_id}")
            return None
        now = time.time()
        with self._presence_lock:
            state = self._presence_state(robot)
            greeted_at = state.get("greeted_at")
            lost_at = state.get("last_lost_at") or 0.0
            if greeted_at is not None and greeted_at >= lost_at:
                return None                       # already said hello for this absence
            text = presence_seam.pick_greeting(robot.child.nickname,
                                               self._last_greeting.get(device_id, ""))
            self._last_greeting[device_id] = text
            state = dict(state)
            state["greeted_at"] = now
            robot.extra["presence"] = state
            busy = device_id in self._busy
            if busy:
                self._pending_opener[device_id] = text
        if busy:
            self._note("vision", f"hello queued (turn in flight) for {device_id}")
            print(f"[runtime] 👋 queued opener for {device_id} (turn in flight)", flush=True)
            return None
        return text, make_markup(text, turn_key=f"greet|{device_id}|{now:.0f}",
                                 chunk_index=0)

    def _speak_opener(self, device_id, event_id, seq):
        """Deliver a queued hello as chunk 0 of the turn that is starting.

        Same wire shape a latency filler uses — `result=REPLY_PENDING` + `chunk_num=0`
        (RemoteChat.proto ResultCode 9 / field 22) — so the real answer follows as chunk 1
        and closes the sequence. Returns the text, or None if nothing was queued."""
        with self._presence_lock:
            text = self._pending_opener.pop(device_id, None)
        if not text or self._is_stale(device_id, seq):
            return None
        markup = make_markup(text, turn_key=f"greet|{event_id}", chunk_index=0)
        self._note("chat", f"hello (queued): '{text[:40]}'")
        print(f"[runtime] 👋 delivering queued opener on {device_id}: '{text}'", flush=True)
        self._publish_chat(device_id, event_id, "router", text, markup,
                           result=ResultCode.REPLY_PENDING, chunk_num=0,
                           is_completed=False)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
        return text

    def _in_bedtime(self, device_id, now=None) -> bool:
        """True when this robot's *effective* config puts it inside its bedtime window.

        Read-only use of `effective_config` (fleet ⊕ per-robot). The window is the pair of
        `"HH:MM"` local wall-clock strings the RobotCloudConfig already carries
        (`weekday_bedtime` / `weekend_bedtime`, cloud_config.py), weekday vs weekend by
        `datetime.weekday()` — the convention `WAKE_DAY_NAMES` fixes (0 = Monday). A
        window that wraps midnight (20:30-07:00, the normal case) is handled. No window
        configured -> never bedtime, which is the pre-presence behavior exactly."""
        import datetime
        from moxie_sdk.cloud_config import in_bedtime
        dt = (datetime.datetime.fromtimestamp(now) if now is not None
              else datetime.datetime.now())
        try:
            cfg = self.effective_config(device_id)
        except Exception:
            return False
        return in_bedtime(cfg, dt)

    def _vision_subscription(self, device_id, robot=None):
        """The `EventSubscription.active[]` list to attach to this response, or None.

        The robot discards its own vision events unless the *active module* subscribed,
        and "events are automatically unsubscribed when the module exits"
        (RemoteModuleAPI §Unsubscribing) — so the subscription is (re-)sent once per
        `(device, module_id)`, not once per process. `MOXIE_VISION=0` turns it off."""
        if not self.vision:
            return None
        robot = robot or self.robots.get(device_id)
        module = (getattr(robot, "module_id", None) or "") if robot else ""
        with self._presence_lock:
            if self._vision_subscribed.get(device_id) == module:
                return None
            if not self.is_permitted(device_id):
                return None
            self._vision_subscribed[device_id] = module
        self._note("vision", f"subscribed to vision events on {device_id}")
        print(f"[runtime] 👁️  subscribing {device_id} to "
              f"{', '.join(presence_seam.VISION_EVENTS)}", flush=True)
        return list(presence_seam.VISION_EVENTS)

    def _turn_worker(self, device_id, event_id, speech, turn, seq):
        """`_handle_turn` with an in-flight marker around it.

        The marker is what `_greeting_for` reads to decide "queue the hello" instead of
        "say it now" — nothing else about the turn loop changes."""
        with self._presence_lock:
            self._busy.add(device_id)
        try:
            self._handle_turn(device_id, event_id, speech, turn, seq)
        finally:
            with self._presence_lock:
                self._busy.discard(device_id)

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
        try:
            data = json.loads(payload)
        except Exception:
            data = {"raw": True}
        # A vision event on its own `events/<name>` subtopic. The recovered contract
        # delivers these inside a RemoteChatRequest instead (see the presence region), so
        # this branch is a defensive extra rather than an observed shape: it updates
        # presence and, because there is no request to answer, any hello it earns is
        # QUEUED for the next turn rather than published unsolicited.
        if presence_seam.is_vision_event(name):
            self.robots.setdefault(device_id, robot)
            signals = self._ingest_vision(device_id, robot, name,
                                          data.get("input_vars") or data)
            greeting = self._greeting_for(device_id, robot, signals)
            if greeting is not None:
                with self._presence_lock:
                    self._pending_opener[device_id] = greeting[0]
                self._note("vision", f"hello queued (no request to answer) for {device_id}")
            return None
        # everything else → surface to the app as an event (module lifecycle…)
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
        # A module switch ends the previous conversation — the `complete_handler`
        # moment for whatever was running before (see `_end_conversation`).
        new_module = rcr.get("module_id")
        if new_module and robot.module_id and new_module != robot.module_id:
            self._end_conversation(device_id, "module_switch", robot=robot)
        robot.module_id = rcr.get("module_id") or robot.module_id
        robot.content_id = rcr.get("content_id") or robot.content_id

        # module list query (backend:data / query:modules) → empty list for v1
        if backend == "data" and rcr.get("query") == "modules":
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.SUCCESS, modules=[])

        # rebuild history from notify events (Moxie is authoritative about what it said)
        if command == "notify":
            return self._ingest_notify(device_id, rcr)

        # 🎭 No brain while a telehealth session is open. Whether a brain-less robot in
        # STATE_TELEBRAIN still emits `events/remote-chat` at all is unknown
        # (`backlog/telehealth.md` B3) — this `if` makes the design correct either way. A
        # brain reply racing the operator's line is the one failure a child would see as
        # broken, and two voices in one mouth is exactly what puppet mode exists to avoid.
        if self._telehealth.get(device_id, {}).get("session_id"):
            self._note("telehealth", "ignored a remote-chat during a session")
            return None

        speech = rcr.get("speech") or ""
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") == "input" and ln.get("text"):
                speech = ln["text"]
        # The robot's own eyes: a subscribed perception event arrives in the `speech`
        # slot, not as words a child said (RemoteModuleAPI §Event Handling). It is
        # answered here — never handed to a brain, never written to history, never
        # assessed as a child's utterance.
        if presence_seam.is_vision_event(speech):
            return self._on_vision_turn(device_id, robot, rcr, speech.strip())
        turn = Turn(robot=robot, speech=speech, history=list(self.history.get(device_id, [])),
                    command=command, input_vars=rcr.get("input_vars", {}),
                    presence=presence_seam.snapshot(self._presence_state(robot)))
        # Number the turn so a slow brain's answer can be recognized as stale if the
        # child has moved on by the time it lands (_is_stale). The MQTT loop is the only
        # writer here, so a plain increment is enough.
        seq = self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        # Run the (possibly slow) app + LLM off the MQTT loop so we never block it.
        self._pool.submit(self._turn_worker, device_id, event_id, speech, turn, seq)

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
        # A hello queued while a previous turn was in flight (someone walked in mid-answer)
        # rides out as this turn's chunk 0 — the filler's own wire shape, so the answer
        # below closes the sequence as chunk 1. Nothing else about the turn changes.
        state["filler"] = self._speak_opener(device_id, event_id, seq)
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
        markup = (reply.markup if reply.markup is not None
                  else make_markup(reply.text, turn_key=event_id, chunk_index=0))
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
        # `<exit>` in the model's own line (or a handler's) ended the activity: this
        # worker is already off the MQTT loop, so summarize inline.
        self._maybe_end_conversation(device_id, reply.actions)

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
        state = {"lock": threading.Lock(), "done": False, "chunk": 0, "ans": 0,
                 "fillers": 0, "gen": 0, "timer": None}
        said, closed, failed = [], False, None
        acts: list = []                 # every action the stream asked for (e.g. <exit>)
        # Same queued hello as the non-streaming path: it takes chunk 0, the stream's own
        # sentences start at chunk 1. `ans` stays 0, so the answer keeps its mood.
        opener = self._speak_opener(device_id, event_id, seq)
        if opener:
            state["chunk"] = 1
            said.append(opener)
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
                        a = state["ans"]
                        state["ans"] = a + 1
                        if blocked:
                            safe = ReplyChunk(text=red.text, markup=red.markup, final=True)
                            self._publish_stream_chunk(device_id, event_id, safe, n, True,
                                                       ann=a)
                            said.append(red.text)
                        else:
                            self._publish_stream_chunk(device_id, event_id, chunk, n,
                                                       final, ann=a)
                            if chunk.text:
                                said.append(chunk.text)
                            acts += list(getattr(chunk, "actions", None) or [])
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
                self._publish_stream_chunk(device_id, event_id, reply, 0, True, ann=0)
            else:
                self._publish_stream_chunk(
                    device_id, event_id, Reply(text=""), n, True, synthesize=False)
        text = " ".join(t for t in said if t).strip()
        self._remember(device_id, speech, text)
        self._note("chat", f"💬 '{speech[:30]}' → '{text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{text[:60]}' "
              f"({state['chunk']} chunk(s))", flush=True)
        self._maybe_end_conversation(device_id, acts)

    def _safe_respond(self, turn):
        try:
            return self.app.respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            return Reply(text="Hmm, let me think about that.")

    def _publish_stream_chunk(self, device_id, event_id, chunk, n, final,
                              synthesize=True, ann=None):
        """One `ReplyChunk` (or `Reply`) onto the wire, with its chunk bookkeeping.

        `ann` is the chunk's index *within the answer* (fillers excluded). The markup
        floor emits the mood on index 0 only, so a streamed answer holds one face all
        the way through instead of flipping it every sentence — and a "let me think"
        line ahead of the answer does not cost the answer its mood.
        """
        markup = (chunk.markup if chunk.markup is not None else
                  make_markup(chunk.text, turn_key=event_id,
                              chunk_index=n if ann is None else ann))
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

    # ---- 🎚️ the voice picker (backlog/voice-picker.md) ----
    # Two dropdowns — **Speech** and **Listening** — over what this appliance can really
    # use: the gateway's audio models, the local engines installed on the box, and the two
    # built-ins. The pick is FLEET-level (`fleet/voice.json`), because a voice is a
    # property of the house rather than of one robot, and it survives a restart because
    # `run.py` reads the same record before it builds either engine.
    #
    # Two properties are load-bearing and neither costs the turn loop anything:
    #   * **Discovery never blocks a turn.** `voice_settings.GatewayCatalog` caches one
    #     `GET /v1/models` for `MOXIE_VOICE_DISCOVERY_TTL_S` and refreshes it on a
    #     background thread; the first call after boot answers with the local entries and
    #     `discovering: true`.
    #   * **A swap takes effect on the NEXT turn.** `set_synthesizer` / `set_transcriber`
    #     rebind one attribute; a turn already in flight finishes on the engine it started
    #     with. That is the whole reason there is no lock inside the turn loop — the lock
    #     below serializes concurrent *swaps*, nothing else.

    DEFAULT_VOICE_TEST_LINE = "Hi, I'm Moxie."

    def set_voice_engines(self, engines):
        """Install the appliance's engine builders + discovery (`config.voice_engines()`).

        Without one the picker still works and offers `tone` / `off` — an honest floor
        rather than a card that claims models this box cannot build."""
        self._voice_engines = engines

    def _voice_discovery(self, *, refresh: bool = False) -> dict:
        """`{available, discovering, gateway_error}` — never raises, never blocks.

        A discovery that throws is reported as `gateway_error` beside the local entries,
        because a card that empties itself when a proxy hiccups is worse than one that
        says the gateway is unreachable next to the options it already had.
        """
        engines = self._voice_engines
        if engines is None:
            return {"available": voice_seam.build_available(), "discovering": False,
                    "gateway_error": ""}
        try:
            out = engines.available(refresh=refresh)
        except Exception as e:              # noqa: BLE001 — any failure is local-only
            return {"available": voice_seam.build_available(), "discovering": False,
                    "gateway_error": type(e).__name__}
        return {"available": out.get("available") or voice_seam.build_available(),
                "discovering": bool(out.get("discovering")),
                "gateway_error": str(out.get("gateway_error") or "")}

    def voice_settings(self) -> dict:
        """The stored fleet record (`fleet/voice.json`) — `{}` when nobody has picked."""
        return voice_seam.read_settings(self.store)

    def voice_view(self, *, refresh: bool = False) -> dict:
        """What the 🎚️ card renders: every option, which one is in force, which one is
        the default, whether discovery is still running and whether the gateway answered.

        `current` is what is IN FORCE — a stored pick when there is one, otherwise the
        default computed from this moment's availability. A stored pick the gateway can no
        longer confirm stays current on purpose (`voice_settings.sanitize_choice`): an
        outage must not silently revert a parent's choice.
        """
        disc = self._voice_discovery(refresh=refresh)
        stored = voice_seam.read_settings(self.store)
        resolved = voice_seam.resolve_settings(stored, disc["available"])
        installed = {
            voice_seam.SPEECH: (self._synth.describe() if self._synth is not None else ""),
            voice_seam.LISTENING: (self._transcriber.describe()
                                   if self._transcriber is not None else ""),
        }
        return {"ok": True,
                "available": voice_seam.mark_defaults(disc["available"],
                                                      resolved["defaults"]),
                "current": resolved["current"], "defaults": resolved["defaults"],
                "chosen": resolved["chosen"],
                "selected": {k: voice_seam.choice_id(resolved["current"][k])
                             for k in voice_seam.KINDS},
                "labels": {k: voice_seam.describe_choice(resolved["current"][k])
                           for k in voice_seam.KINDS},
                "installed": installed,
                "discovering": disc["discovering"],
                "gateway_error": disc["gateway_error"],
                "updated_at": int(stored.get("updated_at") or 0),
                "robots": [d for d in self.robots if self.is_permitted(d)]}

    def voice_update(self, patch) -> dict:
        """Persist a parent's pick and swap the live engines to match.

        The patch is checked against what is available RIGHT NOW
        (`normalize_voice_settings`), so a stale page cannot install a model this gateway
        stopped serving; the refusal carries the sentence the card shows. Order —
        validate, persist, install — means a supervisor that dies mid-swap comes back with
        the choice a parent was told was saved.
        """
        with self._voice_lock:
            disc = self._voice_discovery()
            stored = voice_seam.read_settings(self.store)
            try:
                settings = voice_seam.normalize_voice_settings(
                    patch, disc["available"], current=stored)
            except ValueError as e:
                return {"ok": False, "error": str(e), "reason": str(e)}
            voice_seam.write_settings(self.store, settings)
            resolved = voice_seam.resolve_settings(settings, disc["available"])
            applied = self._install_voice(resolved["current"], chosen=resolved["chosen"])
        out = self.voice_view()
        out["applied"] = applied
        return out

    def _install_voice(self, current: dict, *, chosen: dict | None = None) -> dict:
        """Build both engines for `current` and bind them. Returns one report per side.

        **A build that fails keeps the engine that is already speaking.** Losing the voice
        because a newly chosen one could not be constructed would be a downgrade caused by
        an *attempt to improve things*, which is the worst shape a failure can take. `off`
        is the one intentional `None`, so it is spelled out rather than inferred.
        """
        chosen = chosen or {}
        engines = self._voice_engines
        report = {}
        for kind in voice_seam.KINDS:
            choice = current.get(kind) or voice_seam.make_choice(
                voice_seam.BUILTIN_ENGINE[kind])
            engine, note = None, ""
            if engines is None:
                note = "no engine builders installed (set_voice_engines)"
            else:
                build = (engines.build_speech if kind == voice_seam.SPEECH
                         else engines.build_listening)
                try:
                    engine = build(dict(choice))
                except SystemExit as e:      # an env engine that refuses to be built
                    note = str(e)
                except Exception as e:       # noqa: BLE001 — a bad pick must not kill us
                    note = f"{type(e).__name__}: {e}"
            silent = choice["engine"] in ("off",)
            if engine is not None or silent:
                if kind == voice_seam.SPEECH:
                    self.set_synthesizer(engine)
                else:
                    self.set_transcriber(engine)
            elif not note:
                note = "could not be built on this box — keeping the current engine"
            line = voice_seam.boot_line(kind, choice, chosen=bool(chosen.get(kind)),
                                        note=note)
            report[kind] = {"id": voice_seam.choice_id(choice), "choice": dict(choice),
                            "label": voice_seam.describe_choice(choice),
                            "installed": engine.describe() if engine is not None else "",
                            "note": note, "line": line}
            self._note("voice", f"🎚️ {line}")
            print(f"[runtime] 🎚️ {line}", flush=True)
        return report

    def voice_test(self, device_id, text: str = "") -> dict:
        """Speak one line with the CURRENT speech engine and send it to one robot.

        This is the card's **Test** button, and it is the only honest answer to "did my
        pick work": it exercises the engine that is actually installed, on the wire the
        SIM really plays (`commands/tts`, a `CloudTTSResponse`), rather than reporting the
        record back to the page that just wrote it.
        """
        line = str(text or "").strip() or self.DEFAULT_VOICE_TEST_LINE
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted. Let it in on the "
                              "Robot access card first."}
        if device_id not in self.robots:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "That robot is not connected."}
        if self._synth is None:
            return {"ok": False, "device_id": device_id, "error": "no voice",
                    "reason": "No speech engine is installed — pick one, or check "
                              "MOXIE_TTS."}
        event_id = f"voice-test-{int(time.time())}"
        markup = make_markup(line, turn_key=event_id, chunk_index=0)
        resp = self._maybe_synthesize(device_id, markup, event_id=event_id, chunk_num=0)
        if not resp:
            return {"ok": False, "device_id": device_id, "error": "synthesis failed",
                    "reason": "The voice engine could not speak that line — see the "
                              "supervisor log."}
        audio = resp.get("audio") or {}
        self._note("voice", f"🎚️ test '{line[:40]}' → {device_id}")
        return {"ok": True, "device_id": device_id, "spoke": line, "event_id": event_id,
                "engine": self._synth.describe(),
                "sample_rate": int(audio.get("sample_rate") or 0),
                "channels": int(audio.get("channels") or 1),
                "bytes": len(audio.get("buffer") or "")}

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
    SCHEDULE_EXPLAIN_COLLECTION = "schedule_explain"   # robots/<id>/schedule_explain.json

    def plan_schedule_for(self, device_id, *, now=None) -> tuple:
        """Plan this robot's day → `(ContentSchedule, explanations, inputs)`.

        The recommender (audit §4.2 BEYOND #7) is a pure function; this method is the
        only place that gathers its signals from live state:
          * the running content module's `schedules[]` (read-only authoring templates),
          * this robot's stored `mentor_behaviors` (what the child finished vs. quit),
          * its effective config — `schedule_preferences.parent_requests[]` and the
            bedtime windows (read-only; the config path itself is untouched),
          * its buffered telemetry Packets (see `moxie_sdk.schedule.telemetry_signals`
            for what those can honestly contribute — no module-scoped event vocabulary
            is recovered, so they are context, not a score).
        See `moxie_sdk/schedule.py` for the shape, the weights and the citations.
        """
        from moxie_sdk.schedule import plan
        schedules = None
        try:
            schedules = getattr(getattr(self.app, "module", None), "schedules", None)
        except Exception as e:
            print(f"[runtime] schedule template unavailable ({e}); using the default")
        robot = self.robots.get(device_id)
        packets = (robot.extra.get("telemetry") or []) if robot else []
        try:
            config = self.effective_config(device_id)
        except Exception as e:
            print(f"[runtime] effective config unavailable ({e}); planning without it")
            config = {}
        child = getattr(self.child, "nickname", "") or ""
        return plan(device_id, content_schedules=schedules,
                    mentor_behaviors=self.mentor_behaviors(device_id),
                    effective_config=config, telemetry_packets=packets,
                    child_name=child, now=now)

    def build_schedule_for(self, device_id) -> dict:
        """The ContentSchedule this robot gets for this session — the planner's output,
        and the value of `CloudQueryResponse.schedule`. The parent-readable "why this
        activity today" lines are stored alongside it (never on the wire) so
        `GET /schedule` can show them after the robot has pulled its day."""
        sched, explanations, inputs = self.plan_schedule_for(device_id)
        try:
            self.store.write(device_id, self.SCHEDULE_EXPLAIN_COLLECTION,
                             {"day": inputs.get("day"), "planned_at": inputs.get("now"),
                              "schedule": sched, "explanations": explanations,
                              "inputs": self._schedule_inputs_summary(inputs)})
        except Exception as e:                     # a plan must never fail on its audit
            print(f"[runtime] could not store schedule explanations: {e}", flush=True)
        return sched

    @staticmethod
    def _schedule_inputs_summary(inputs) -> dict:
        """The parent-facing slice of the planner's inputs: what it knew, not the whole
        catalog. Everything here is already JSON-safe (`plan_inputs` guarantees it)."""
        keys = ("device_id", "day", "now", "bucket", "slot_minutes", "child_name",
                "bedtime", "slots", "parent_requests", "ftue_skips", "telemetry",
                "planned")
        out = {k: inputs.get(k) for k in keys if k in inputs}
        history = inputs.get("history") or {}
        out["history"] = {k: history[k] for k in sorted(history)}
        return out

    def schedule_view(self, device_id, *, refresh: bool = False) -> dict:
        """`GET /schedule?device_id=…` — the day this robot was served, the "why this
        activity today" line behind every entry, and a summary of the signals the planner
        had. Read-only: with nothing stored yet (the robot has not pulled a schedule this
        run) it plans one on the spot rather than answering empty."""
        stored = self.store.read(device_id, self.SCHEDULE_EXPLAIN_COLLECTION, None)
        if refresh or not isinstance(stored, dict) or not stored.get("explanations"):
            if device_id not in self.robots and not self.store.read(
                    device_id, "mentor_behaviors", None):
                return {"ok": False, "error": f"unknown device_id {device_id!r}"}
            sched, explanations, inputs = self.plan_schedule_for(device_id)
            stored = {"day": inputs.get("day"), "planned_at": inputs.get("now"),
                      "schedule": sched, "explanations": explanations,
                      "inputs": self._schedule_inputs_summary(inputs), "served": False}
        else:
            stored = dict(stored)
            stored.setdefault("served", True)
        return {"ok": True, "device_id": device_id, **stored}

    # ---- 🎭 telehealth / "Be Moxie": the operator drives the body (audit ADOPT #7) ----
    #
    # A remote human replaces the robot's brain and says the lines themselves. The whole
    # protocol is recovered (`moxie_sdk/telehealth.py` carries the citations); what lives
    # here is the six verbs, the state the console polls, and the one gate that keeps two
    # voices out of one mouth.
    #
    # THE SHAPE OF A SESSION (telehealth.md:66-79):
    #     enable  → moxie_mode:"TELEHEALTH" in this robot's /config  (ASSUMPTION B1)
    #     start   → START_SESSION, a session_id is minted
    #     speak*  → safety(MOXIE) → automarkup → PLAY_OUTPUT (+ commands/tts for the SIM)
    #     end     → END_SESSION, the session_id is cleared
    # and the robot reports READY → IN_SESSION → EXITING → READY on the activity log.
    #
    # EVERY verb refuses a device that is not on the permit list. A *pending* robot is by
    # definition one we have not identified; puppeting it would be the pairing gate's
    # exact failure mode with a microphone attached.

    def _th(self, device_id) -> dict:
        """This robot's live telehealth state — created on first use.

        Runtime-level rather than on `RobotContext.extra` on purpose: `_device_disconnect`
        pops the robot, and an operator whose robot just dropped off Wi-Fi should still see
        the transcript of what was said and the last state the robot reported, not an empty
        card. In memory, bounded, never written through `store.py` (`backlog/telehealth.md`
        R6)."""
        from collections import deque
        st = self._telehealth.get(device_id)
        if st is None:
            st = {"session_id": "", "state": "", "state_at": None, "lines": 0,
                  "transcript": deque(maxlen=telehealth_seam.TRANSCRIPT_MAX)}
            self._telehealth[device_id] = st
        return st

    def telehealth_enabled(self, device_id) -> bool:
        """True when this robot's *effective* config puts it in TELEHEALTH mode.

        Read off `effective_config` rather than a flag of our own, so the card can never
        disagree with the document that actually went down the wire."""
        try:
            mode = (self.effective_config(device_id) or {}).get(
                telehealth_seam.MOXIE_MODE_KEY)
        except Exception:
            return False
        try:
            return int(mode) == telehealth_seam.TELEHEALTH_MOXIE_MODE
        except (TypeError, ValueError):
            return str(mode).upper() == "TELEHEALTH"

    def _telehealth_guard(self, device_id, *, need_mode: bool = False):
        """`None` when the call may proceed, else the refusal the console renders."""
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted. Let it in on the "
                              "Robot access card first."}
        if device_id not in self.robots:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "That robot is not connected."}
        if need_mode and not self.telehealth_enabled(device_id):
            # Publishing PLAY_OUTPUT at a robot still running its own brain would put two
            # voices in one mouth (`backlog/telehealth.md` R1/R2).
            return {"ok": False, "device_id": device_id, "error": "not in telehealth mode",
                    "reason": "Turn on Be Moxie first."}
        return None

    def _telehealth_publish(self, device_id, command: dict):
        """Publish one `TelehealthRobotCommand` on `commands/telehealth`."""
        if self.client:
            self.client.publish(telehealth_seam.telehealth_topic(device_id),
                                json.dumps(command))
        return command

    def _telehealth_note(self, device_id, who: str, text: str):
        """Append one line to this robot's transcript ring.

        A **child** line is subject to the same `LoggingPolicy` check the safety journal
        uses: under `NO_DATA` the ring keeps operator lines only, because a transcript of
        a child's words is exactly the thing that gate exists to withhold. Operator lines
        are always kept — they are the record of what a third party said to a child, and
        that record is the mitigation for this feature's inherent risk (R3)."""
        if who == telehealth_seam.CHILD and not self._safety_keeps_rows(device_id):
            return None
        entry = telehealth_seam.transcript_entry(who, text)
        self._th(device_id)["transcript"].append(entry)
        return entry

    def telehealth_enable(self, device_id, on: bool = True) -> dict:
        """Turn puppet mode on or off for one robot.

        **ASSUMPTION B1**: this writes `moxie_mode` into the robot's own override layer and
        re-pushes `/config` through the ordinary `update_config` path — so the fleet⊕robot
        merge, the console's layer labels and every existing config test apply unchanged,
        and `sanitize_config_overrides` (which does *not* whitelist `moxie_mode`) still
        makes it impossible for the ⚙️ form's "Apply to all robots" to put a whole fleet
        into puppet mode.

        Turning it **off** ends an open session first: leaving a brain-less robot holding a
        session nobody will send lines to is the one state worse than either end."""
        refusal = self._telehealth_guard(device_id)
        if refusal:
            return refusal
        on = bool(on)
        if not on and self._th(device_id)["session_id"]:
            self.telehealth_session(device_id, "END_SESSION")
        mode = (telehealth_seam.TELEHEALTH_MOXIE_MODE if on
                else telehealth_seam.DEFAULT_MOXIE_MODE)
        self.update_config(device_id, **{telehealth_seam.MOXIE_MODE_KEY: mode})
        self._note("telehealth",
                   f"🎭 Be Moxie {'ON' if on else 'off'} for {device_id}")
        print(f"[runtime] 🎭 telehealth {'enabled' if on else 'disabled'} on {device_id}",
              flush=True)
        return self.telehealth_view(device_id)

    def telehealth_session(self, device_id, action: str) -> dict:
        """`START_SESSION` / `END_SESSION` / `UPDATE_STATE`.

        A start mints a `session_id` and remembers it; an end clears it. `UPDATE_STATE` is
        the "tell me what you are" poke — it changes nothing here and simply asks the robot
        to report on the activity log."""
        name = str(action or "").strip().upper()
        if name not in ("START_SESSION", "END_SESSION", "UPDATE_STATE"):
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown session action {action!r}",
                    "reason": "Expected START_SESSION, END_SESSION or UPDATE_STATE."}
        refusal = self._telehealth_guard(device_id, need_mode=(name == "START_SESSION"))
        if refusal:
            return refusal
        st = self._th(device_id)
        if name == "START_SESSION":
            st["session_id"] = telehealth_seam.new_session_id()
            st["lines"] = 0
        session_id = st["session_id"]
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            name, session_id=session_id))
        if name == "END_SESSION":
            st["session_id"] = ""
        self._note("telehealth", f"🎭 {name.lower().replace('_', ' ')} "
                                 f"{session_id or '(no session)'}")
        return self.telehealth_view(device_id)

    def telehealth_speak(self, device_id, text, *, mood=None, intensity=None,
                         gesture=None) -> dict:
        """The hot path: an operator's line becomes something the robot says.

        Order — permit, mode, **safety**, markup, publish, voice, journal — is
        `backlog/telehealth.md` §2.3 and the order matters:

        **The operator's text IS checked**, as `role=MOXIE`, by the same classifier that
        guards the brain's own output. The `MOXIE` role is defined as *text about to be
        spoken to a child*; the author is not part of that definition and the child cannot
        tell the difference. Telehealth's whole premise is that the operator is a third
        party, and a channel that skipped the parent's journal would make that journal a
        lie.

        **But the handling differs from the brain path, deliberately.** When a model
        produces an unsafe line there is nobody to tell, so the runtime substitutes a
        redirect. Here a human is at the keyboard: a BLOCK is **returned to the operator
        with its reason and nothing is spoken**, so they can rephrase. Substituting a
        redirect for a clinician's sentence would be both useless and dishonest. A FLAG
        passes through and is journaled.
        """
        refusal = self._telehealth_guard(device_id, need_mode=True)
        if refusal:
            return refusal
        line = str(text or "").strip()
        if not line:
            return {"ok": False, "device_id": device_id, "error": "empty line",
                    "reason": "Type something for Moxie to say."}
        try:
            mood = telehealth_seam.validate_mood(mood)
            intensity = telehealth_seam.validate_intensity(intensity)
        except ValueError as e:
            return {"ok": False, "device_id": device_id, "error": str(e),
                    "reason": str(e)}

        verdict = self._assess(line, safety_seam.MOXIE)
        if verdict:
            self._record_safety(device_id, verdict)
            if verdict.action == safety_seam.BLOCK:
                labels = safety_seam.category_labels(self.safety) if self.safety else {}
                named = [str(labels.get(c) or c) for c in verdict.categories]
                return {"ok": False, "device_id": device_id, "error": "blocked",
                        "blocked": True, "categories": list(verdict.categories),
                        "labels": named,
                        "reason": "Moxie will not say that (%s). Nothing was spoken — "
                                  "please rephrase." % (", ".join(named) or "safety")}

        st = self._th(device_id)
        session_id = st["session_id"]
        st["lines"] = n = int(st.get("lines") or 0) + 1
        # One PLAY_OUTPUT per line, always chunk 0 of its own utterance. Telehealth never
        # streams (there is no brain and no token stream — the line is complete when the
        # operator presses send), and the SIM's player requires an utterance's first chunk
        # to be `chunk_num` 0 (`sim/web/audio.js`, the ORDERING/EVENT rules). Numbering the
        # LINE rather than a chunk keeps every line its own utterance and keeps the mood
        # mark — which `annotate` emits on chunk 0 only — on every one of them.
        line_key = f"{session_id or device_id}#{n}"
        markup = make_markup(line, mood_hint=mood, gesture_hint=gesture,
                             intensity=intensity, turn_key=line_key, chunk_index=0)
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            "PLAY_OUTPUT", text=line, markup=markup, session_id=session_id))
        # A real robot self-synthesizes from the markup and ignores this; it is how the SIM
        # (and any voice-enabled client) gets an actual voice (mqtt-and-conversation §5.3).
        self._maybe_synthesize(device_id, markup, event_id=line_key, chunk_num=0)
        self._telehealth_note(device_id, telehealth_seam.OPERATOR, line)
        self._note("telehealth", f"🎭 said '{line[:40]}'")
        out = self.telehealth_view(device_id)
        out["spoke"] = line
        out["markup"] = markup
        out["mood"] = mood
        out["intensity"] = intensity
        if verdict:
            out["flagged"] = list(verdict.categories)
        return out

    def telehealth_interrupt(self, device_id) -> dict:
        """Cut Moxie off mid-line — barge-in from the operator side.

        **INFERRED (B2).** Our corpus records the verb and that it *"cuts Moxie off
        mid-line"*; what that looks like physically (clean cut, fade, ignored mid-phoneme)
        has never been observed. The message carries **no `output`**, which is the one
        thing the proto makes unambiguous."""
        refusal = self._telehealth_guard(device_id, need_mode=True)
        if refusal:
            return refusal
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            "INTERRUPT", session_id=self._th(device_id)["session_id"]))
        self._note("telehealth", "🎭 interrupt")
        return self.telehealth_view(device_id)

    def telehealth_view(self, device_id) -> dict:
        """What the 🎭 card renders: mode, session, the robot's own reported state, whether
        it is inside its bedtime window, and the live transcript.

        `state` is **empty until the robot says otherwise** — the console renders that as
        *"never reported"* rather than inventing `READY`. Whether a robot reports on this
        subtopic at all is one of the open questions (`backlog/telehealth.md` §6).

        `in_bedtime` is a **warning, not a gate**: we do not know whether the robot
        suppresses a puppet line inside its bedtime window (B4), so the operator is told
        the truth and the line is sent anyway."""
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted.",
                    "enabled": False, "online": device_id in self.robots,
                    "transcript": [], "moods": telehealth_seam.moods(),
                    "max_intensity": vocab_seam.MAX_INTENSITY}
        if device_id not in self.robots and device_id not in self._telehealth:
            # A device we have never seen and hold nothing about — the same shape
            # `safety_view` and `memory_view` return, so the console's proxy 404s.
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "That robot is not connected.",
                    "enabled": False, "online": False, "transcript": [],
                    "moods": telehealth_seam.moods(),
                    "max_intensity": vocab_seam.MAX_INTENSITY}
        st = self._th(device_id)
        return {
            "ok": True, "device_id": device_id,
            "enabled": self.telehealth_enabled(device_id),
            "online": device_id in self.robots,
            "session_id": st["session_id"],
            "in_session": bool(st["session_id"]),
            "state": st["state"], "state_at": st["state_at"],
            "in_bedtime": self._in_bedtime(device_id),
            "transcript": list(st["transcript"]),
            "moods": telehealth_seam.moods(),
            "max_intensity": vocab_seam.MAX_INTENSITY,
        }

    def ingest_telehealth_event(self, device_id, payload) -> dict:
        """A `TelehealthRobotEvent` off `client-service-activity-log` (`subtopic:
        "telehealth"`) → this robot's reported state.

        An unrecognised state name is stored verbatim and flagged rather than coerced: a
        robot telling us something new must not be rounded off to something we already
        believe."""
        event = telehealth_seam.parse_telehealth_event(payload)
        st = self._th(device_id)
        if event["state"]:
            st["state"] = event["state"]
            st["state_at"] = event["at"] if event["at"] is not None else time.time()
            flag = "" if event["known"] else " (not a state we know)"
            self._note("telehealth", f"🎭 robot reports {event['state']}{flag}")
        # Adopt a session id we do not have ONLY from a robot that says it is in one — a
        # supervisor restarted mid-session should pick the session back up, but an
        # `EXITING` report arriving after we closed the session must never resurrect it.
        if (event["session_id"] and not st["session_id"]
                and event["state"] == "IN_SESSION"):
            st["session_id"] = event["session_id"]
        return event

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
        # 🎭 The robot's own report of where it is in a telehealth session
        # (`TelehealthRobotEvent`, telehealth.md:88-91). READY / IN_SESSION / EXITING —
        # stored, never assumed: the card says "never reported" until this arrives.
        if subtopic == telehealth_seam.EVENT_SUBTOPIC:
            return self.ingest_telehealth_event(device_id, data)
        # The same topic also carries *reports*: `mentor_behavior` is what the child just
        # finished (or quit). Ingest it — that history is what stops the robot repeating
        # the same missions forever and lets FTUE end.
        if isinstance(data.get("mentor_behavior"), dict):
            return self.ingest_mentor_behavior(device_id, data)

    # ---- STT extension point ----
    # ---- STT (AI seam §1) ----
    def set_transcriber(self, transcriber):
        """Install an STT engine (moxie_sdk.stt.Transcriber). Without one, audio
        frames are ignored (text turns still work).

        Live VAD accumulators are dropped with the old engine: an `SttSession` captures the
        transcriber it was built with, so a 🎚️ swap mid-utterance would otherwise finish
        that utterance on the engine a parent just replaced. Losing a half-spoken sentence
        at the exact moment someone changes the ears is the right trade."""
        self._transcriber = transcriber
        self._stt_sessions.clear()

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
        # 🎭 During a telehealth session the child's side of the conversation is the only
        # thing the operator can see (text only — no audio and no video reach them this
        # phase; `backlog/telehealth.md` §2.5). This is a READ of transcript the STT path
        # already produced, not a new capture: outside a session nothing is kept.
        if self._telehealth.get(device_id, {}).get("session_id"):
            self._telehealth_note(device_id, telehealth_seam.CHILD, transcript)
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
        # Ask the robot to start pushing us its vision events, once per module. It rides
        # a spoken reply because that is the only cloud→robot message the contract gives
        # a `RemoteChatAction` to hang `EventSubscription` on — and it is attached only to
        # a plain, action-free closing reply so no reply that already carries a
        # launch/exit changes shape (see `_vision_subscription`).
        subscribe = None
        if (self.vision and modules is None and backend == "router" and not actions
                and result == ResultCode.SUCCESS and chunk_num in (None, 0)
                and self._vision_subscribed.get(device_id) !=
                    (getattr(self.robots.get(device_id), "module_id", None) or "")):
            subscribe = self._vision_subscription(device_id)
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood, dialog_act=dialog_act, modules=modules,
                                   chunk_num=chunk_num, is_completed=is_completed,
                                   safety=safety, subscribe_events=subscribe)
        self.client.publish(f"/devices/{device_id}/commands/remote_chat", json.dumps(resp))
