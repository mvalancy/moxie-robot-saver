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
from moxie_sdk import telemetry as telemetry_seam        # 📈 Packet envelope + durable history
from moxie_sdk import vocab as vocab_seam                # the frozen mood/markup catalog
from moxie_sdk import performance as performance_seam   # the behavior planner (§2)
from moxie_sdk import voice_settings as voice_seam       # 🎚️ which voice / which ears
from moxie_sdk import brains as brain_seam             # 🧠 which brain, per robot
from moxie_sdk.content import packs as content_packs  # 📦 content packs (ADOPT #5)
from moxie_sdk.cloud_config import LoggingPolicy         # the child-privacy gate
from markup import make_markup, perform  # the behavior planner / markup floor seam

# paho is imported lazily in _build_client() so the runtime + turn pipeline can be
# imported and integration-tested without the broker client installed.

CONNECT_RE = re.compile(r"connected from (.*) as (d_[a-f0-9-]+)", re.I)
DISCONNECT_RE = re.compile(r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected)", re.I)

# --- staying connected (docs/architecture/backlog/production-hardening.md §4.1) ---
#
# The third argument of paho's `connect(host, port, keepalive)` is the **keepalive**, not
# a timeout — the audit read it as a timeout and it is not (assumption A1). 30 s is kept
# deliberately: the broker declares us dead at 1.5× keepalive (45 s) and paho notices a
# missing PINGRESP within one keepalive, so halving paho's default 60 halves the
# worst-case detection of a *half-open* socket — the failure a NAT table or a Wi-Fi drop
# actually produces, where the connection is gone but nobody has said so.
KEEPALIVE_S = 30

# The reconnect ladder paho walks after a drop: 1 s, doubling, capped. **Chosen, not
# measured** (A14). 60 rather than paho's own 120 because a house's router reboot is
# ~30-60 s and a 120 s ceiling is up to two minutes of a child talking to nothing; rather
# than Fork A's 30 because we would rather not hammer a broker that has been down for an
# hour. No jitter: paho has none, and with a single supervisor there is no herd.
RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 60



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

# Telemetry's own LoggingPolicy default, for the same reason as the two above: what a
# robot uploads is gated on the robot by `RobotCloudConfig.data_sharing`, and a Packet
# that reached us already passed that gate. What THIS constant governs is narrower and
# stricter — whether the packet is written to disk, and with its `event_data` payload or
# without. NO_MEDIA keeps the envelope (event name + timestamps + session) and withholds
# every opaque payload, because `Packet.event_data` is `bytes` with no recovered type
# vocabulary and a store that guessed "this blob is not audio" would be a privacy
# incident, not a bug. A parent who sets `logging_policy=NO_DATA` gets nothing on disk at
# all; one who sets FULL gets payloads too. See `moxie_sdk/telemetry.py::storable_packet`
# and `docs/architecture/config-and-telemetry-contract.md` §③.
TELEMETRY_POLICY = LoggingPolicy.NO_MEDIA

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
        # A store write refused because another PROCESS held the record is the one failure
        # the cross-process lock newly makes possible, so it is recorded where an operator
        # already looks instead of being a counter nobody reads (§5.3 A11).
        self.store.on_lock_timeout = self._on_store_lock_timeout
        # --- what we actually know about the broker connection (§4.1 C4) ---
        #: True only between a **successful** CONNACK and the next disconnect. A client
        #: object is not a connection — that confusion is what let the wakeup route
        #: report success into a dead socket.
        self.broker_connected = False
        self.last_broker_connect = 0.0
        self.last_broker_disconnect = 0.0
        self.last_connect_error = ""
        #: Publishes the transport refused because there was no socket. At QoS 0 paho
        #: does not queue them (A3), so this is a count of messages the robot never got.
        self.publish_drops = 0
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
        # 🧠 The brain picker (`moxie_sdk/brains.py`): `self.app` is the appliance's own
        # brain — the `defaults` layer — and `_brains` caches every OTHER brain a robot's
        # `fleet ⊕ per-robot` layers have asked for, keyed by name. Keyed by NAME rather
        # than by device because that is exactly today's semantics: one app object serves
        # every robot on it, and two children on `content` share the module set the
        # console installed. The default is seeded under its own name so it is never
        # rebuilt (and so `reload_content()`'s attribute swap is never bypassed).
        self._brain_engines = None
        self._brains = {getattr(app, "name", ""): app} if app is not None else {}
        self._brain_lock = threading.Lock()      # builds only — NEVER held during a turn
        self._brain_failed: dict[str, str] = {}  # a brain that would not build, said once
        # 📦 Content packs (backlog/content-packs.md): one import or undo at a time, so a
        # snapshot can never be taken between another import's write and its own snapshot.
        # Like the voice lock, it is NEVER held inside a turn — the live swap it guards is
        # a single attribute assignment.
        self._content_lock = threading.Lock()
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
        # After a successful first connect paho already reconnects on its own; what it
        # does NOT do is tell anyone. These two callbacks are the difference between "the
        # appliance recovered" and "nobody knows why the robot went quiet" (§4.1 C4).
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,
                                        max_delay=RECONNECT_MAX_DELAY_S)
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

    def _wire_memory_policy(self, app=None):
        """Hand an app's memory store this runtime's per-device privacy gate.

        Done here rather than at construction so an app built by `config.build_app()`
        (which knows nothing about a device's config overrides) still honours them.
        `app` defaults to the appliance's own brain; `app_for` passes every brain it
        builds later, so a per-child brain's memory obeys the same parent switch as the
        default one — a privacy gate that applied to only one of them would be worse
        than none, because nobody would know which."""
        mem = getattr(app if app is not None else self.app, "memory", None)
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
                self.app_for(device_id).on_session_end(robot, history, reason)
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
                # 🧠 Which brain answers this child, and which layer decided — the console
                # renders it beside the robot, and the SIL smoke asserts a per-robot swap
                # without reading the runtime's internals.
                "brain": self.brain_for(r.device_id)["brain"],
                "brain_source": self.brain_for(r.device_id)["source"],
                # Hydrated from `telemetry_packets.json` on first touch, so this is how
                # many events we *hold* for this robot — history included, not just what
                # arrived since the supervisor started.
                "telemetry_count": len(self._telemetry_buffer(r.device_id, r)),
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
                # The appliance's own brain (`MOXIE_APP` ⊕ the fleet layer, resolved at
                # boot) and what the environment pins. `app` stays what it always was —
                # the object that is running — so nothing that read it has to change.
                "brain": brain_seam.sanitize_brain(self.app.name),
                "brain_pin": self.brain_pin(),
                "uptime_s": int(time.time() - self.started_at),
                "fleet_config": self.fleet_config(),
                "allow_unverified_bots": open_fleet,
                "pending_count": sum(1 for r in robots if r["pending"]),
                "schedule_modules": list(schedulable_module_ids()),
                # The appearance catalog the 🎨 card renders (audit ADOPT #9). Published
                # rather than hard-coded in the console so the two can never disagree
                # about which slots exist or which options are actually cited.
                "face_catalog": face_catalog(),
                # What we actually know about the broker (§4.1 C4 / §8 file 8). The
                # console's existing connection monitor renders these with **no console
                # change**, and `broker_connected` is the honest answer to the question
                # every other status field silently assumed: is there a socket at all?
                # The **recorded** state (what a CONNACK last told us), not a live probe
                # of the transport: a status page reports what happened, and the two only
                # ever differ for a test double with no socket to have an opinion about.
                "broker_connected": self.broker_connected,
                "last_broker_connect": self.last_broker_connect,
                "last_broker_disconnect": self.last_broker_disconnect,
                "last_connect_error": self.last_connect_error,
                "publish_drops": self.publish_drops,
                "store_lock_timeouts": getattr(self.store, "lock_timeouts", 0),
                "robots": robots, "recent": list(self.recent)[-60:]}

    # ---- lifecycle ----
    def run(self, status_port: int = 8930):
        if self.client is None:
            self._build_client()
        self._start_status_server(status_port)
        print(f"[runtime] connecting to broker {self.host}:{self.port} · app={self.app.name}")
        self._note("info", f"supervisor started (app={self.app.name})")
        # **All three of these, or none of them.** A plain blocking `connect()` raises
        # `ConnectionRefusedError` / `socket.gaierror` straight out of `run()` when the
        # broker is not listening yet, and the supervisor process dies — survivable under
        # `docker compose up` only because `depends_on: condition: service_healthy` holds
        # the container back, and not survivable at all on bare metal, in the SIL harness,
        # or any time the broker restarts before our first connect.
        #
        # And `connect_async` **alone changes nothing**: `loop_forever()` defaults to
        # `retry_first_connection=False` and re-raises the first `OSError` from
        # `reconnect()` (A2, read out of the installed paho). `loop_start()` gets it right
        # only because its thread body passes the flag — which is why porting "add
        # connect_async" from a `loop_start()` codebase onto this one is a no-op. S6 in
        # `sim/tests/test_connection_resilience.py` exists to fail on exactly that
        # half-done fix rather than only on no fix at all.
        self.client.connect_async(self.host, self.port, KEEPALIVE_S)
        self.client.loop_forever(retry_first_connection=True)

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
                (`{"pack", "accept", "expect_digest"}`) and `POST /content/undo`.

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
                if path in ("/content/review", "/content/import", "/content/undo"):
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

    #: Everything the supervisor listens to. Re-installed on **every** successful CONNACK,
    #: because the broker drops subscriptions with the session.
    SUBSCRIPTIONS = ("/devices/+/events/#", "/devices/+/state",
                     "$SYS/broker/log/#", "$SYS/broker/clients/#")

    @staticmethod
    def _connack_failed(rc) -> bool:
        """True when a CONNACK refused us.

        `rc` is a paho `ReasonCode` under `CallbackAPIVersion.VERSION2` and a plain int
        under VERSION1, so ask the object first and fall back to the integer comparison.
        """
        failed = getattr(rc, "is_failure", None)
        if failed is not None:
            return bool(failed)
        try:
            return int(rc) != 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _connack_reason(rc) -> str:
        """`connack_string(rc)` when paho is importable, else the code as written."""
        try:
            import paho.mqtt.client as mqtt
            return str(mqtt.connack_string(rc))
        except Exception:
            return str(rc)

    def _on_connect(self, c, u, flags, rc, props=None):
        """A CONNACK arrived — and it is not necessarily a *yes*.

        This used to print `broker connected rc={rc}` and subscribe unconditionally, so a
        refusal (`rc=5`, *not authorised*, which the supervisor's broker credential made
        reachable for the first time) logged the words **"broker connected"** and then
        subscribed into a socket the broker was closing. Same class of bug as a route that
        reports success for a publish that never happened: a comfortable lie in the one
        place an operator looks. Behaviour ported from Fork A's `moxie_server.py`:206-215
        (MIT, © Justin Beghtol — read as prior art, no code copied).
        """
        if self._connack_failed(rc):
            self.broker_connected = False
            self.last_connect_error = self._connack_reason(rc)
            print(f"[runtime] ⛔ broker REFUSED the connection: "
                  f"{self.last_connect_error}", flush=True)
            self._note("error", f"⛔ broker refused the connection: "
                                f"{self.last_connect_error}")
            return                            # and subscribe to nothing
        self.broker_connected = True
        self.last_broker_connect = time.time()
        self.last_connect_error = ""
        print(f"[runtime] broker connected rc={rc}")
        self._note("conn", "broker connected")
        for t in self.SUBSCRIPTIONS:
            c.subscribe(t)

    def _on_disconnect(self, c, u, flags=None, rc=None, props=None):
        """The socket went away. Two jobs, and the second is the subtle one.

        1. Record it, so a gap is a thing an operator can see rather than a silence.
        2. **Stale every in-flight turn.** `_turn_seq` already numbers turns per robot and
           `_is_stale` already suppresses an answer whose child has moved on, at seven call
           sites; bumping the sequence here reuses that machinery whole. The alternative —
           letting the answer out after the gap — is actively harmful under the recovered
           contract: the robot re-prompts (~20 s) with a **new** `event_id`, so the child
           would hear the answer to the question they gave up on arriving after the answer
           to the one they asked instead. That is precisely what `_is_stale` was written to
           prevent, and a reconnect is not a reason to re-open it.

        The `_turn_seq` invariant (*"the MQTT loop is the only writer here"*) survives:
        `on_disconnect` is dispatched from the network loop, which under `loop_forever()`
        is that same thread (A19).
        """
        was = self.broker_connected
        self.broker_connected = False
        self.last_broker_disconnect = time.time()
        reason = self._connack_reason(rc) if rc is not None else "connection lost"
        for device_id in set(self._turn_seq) | set(self.robots):
            self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        if was:
            print(f"[runtime] ⚠️  broker disconnected: {reason}", flush=True)
            self._note("conn", f"⚠️ broker disconnected: {reason} — "
                               f"{len(self.robots)} robot(s) in flight abandoned")

    def _on_connect_fail(self, c, u=None):
        """The socket never opened (broker down, DNS gone). Distinct from a CONNACK
        refusal and from a disconnect, and without it the retry loop is invisible — which
        makes *"it is just sitting there"* the bug report."""
        self.broker_connected = False
        self.last_connect_error = f"could not reach the broker at {self.host}:{self.port}"
        # Printed as well as `_note`d. Found by starting a real supervisor before a real
        # broker: `recent` had the four retries and **stdout had nothing**, so anyone
        # tailing `docker logs` saw a process that had said "connecting to broker" and
        # then gone silent — which reads exactly like the hang this change removes. The
        # backoff throttles it for us: at 1, 2, 4 … 60 s this is at worst a line a minute.
        print(f"[runtime] ⛔ {self.last_connect_error} — retrying", flush=True)
        self._note("error", f"⛔ {self.last_connect_error} — retrying")

    def _on_store_lock_timeout(self, lock_path, waited):
        """A store write another **process** would not let go of. Recorded, never retried
        forever and never swallowed (production-hardening.md §3.3 #3)."""
        if getattr(self, "recent", None) is None:
            return
        self._note("error", f"⏳ a store write was refused after {waited:.1f}s — another "
                            f"process holds {os.path.basename(lock_path)}")

    # ---- publishing, with the return code read (§4.1 C5) ----
    def _broker_connected(self) -> bool:
        """Is there really a socket?

        `if self.client is None` — the guard this replaces — asks whether an *object*
        exists, which stays true for the whole life of the process. paho's
        `is_connected()` is the transport's own answer. A transport double with no opinion
        (the SIL loopback) is trusted, because it has no socket to be wrong about.
        """
        client = self.client
        if client is None:
            return False
        checker = getattr(client, "is_connected", None)
        if not callable(checker):
            return True
        try:
            return bool(checker())
        except Exception:
            return False

    #: The sentence a route hands a parent when the appliance has no broker. One place,
    #: because it is the same fact whichever button they pressed.
    NO_BROKER_REASON = "The supervisor is not connected to the broker."

    def _publish(self, topic: str, payload, *, device_id: str = "", what: str = ""):
        """Publish one message and **read the return code**. Returns `(ok, reason)`.

        All eight publish sites used to be `self.client.publish(...)` with the result
        thrown away. At QoS 0 paho calls `_send_publish` directly and returns
        `MQTT_ERR_NO_CONN` on `info.rc` when there is no socket — the message is *not*
        queued (A3) — so a reply published during a gap was discarded and nothing in this
        process knew. QoS 0 stays (§4.3: a QoS 1 queue would deliver exactly the stale
        answers §4.2 just decided are harmful); what changes is that a drop is now a fact
        the appliance holds rather than one it never learns.
        """
        body = payload if isinstance(payload, str) else json.dumps(payload)
        label = what or topic.rsplit("/", 1)[-1]
        if not self._broker_connected():
            self._record_drop(topic, device_id, label, self.NO_BROKER_REASON)
            return False, self.NO_BROKER_REASON
        try:
            info = self.client.publish(topic, body)
        except Exception as e:                # a transport that raises is still a drop
            reason = f"the transport refused the message ({type(e).__name__})"
            self._record_drop(topic, device_id, label, reason)
            return False, reason
        rc = getattr(info, "rc", 0)           # a double that returns None means success
        if rc:
            reason = f"the broker connection dropped the message (rc={rc})"
            self._record_drop(topic, device_id, label, reason)
            return False, reason
        return True, ""

    def _record_drop(self, topic, device_id, label, reason):
        self.publish_drops += 1
        who = f"{device_id} " if device_id else ""
        print(f"[runtime] ⚠️  dropped {label} for {who}— {reason}", flush=True)
        self._note("drop", f"⚠️ dropped {label} for {who or 'the fleet'}— {reason}")

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
                self.app_for(device_id).on_connect(robot)
            except Exception as e:
                print(f"[runtime] app.on_connect error: {e}", flush=True)
        threading.Timer(1.0, _settle).start()

    def _device_disconnect(self, device_id: str):
        robot = self.robots.pop(device_id, None)
        if robot:
            print(f"[runtime] robot disconnected: {device_id}")
            self._end_conversation(device_id, "disconnect", robot=robot)
            try:
                self.app_for(device_id).on_disconnect(robot)
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

    # ---- 🧠 the brain picker: any brain, hot-swappable, per child -----------------
    #
    # `ai-seam.md` §2 calls the brain a seam and says any AI can wear the shell. It was
    # true of the drawing and false of the box: `MOXIE_APP` chose one brain, once, at
    # import, for every child on the appliance. These few methods are the whole feature,
    # and each half is something this codebase already does:
    #
    #   * **the registry** — `moxie_sdk/brains.py`, a closed positive list (the idiom of
    #     `content/packs.py::SPEC` and `content/ext.py::OPS`). A name resolves to a
    #     builder; an unknown name is refused, never guessed;
    #   * **the selection** — `brain` is an ordinary key in the ordinary config layers
    #     (`fleet/config.json` ⊕ the per-robot overrides, audit ADOPT #6). There is no
    #     second store and no second layering: `POST /config?scope=fleet` already writes
    #     the house rule and `POST /config?device_id=` already writes one robot's;
    #   * **the swap** — the 🎚️ voice picker's rule, exactly: the choice is resolved ONCE
    #     at the top of a turn (`_handle_turn`), so the next turn uses the new brain and a
    #     turn already in flight finishes with the one it started with. Same shape as
    #     `reload_content()`'s attribute swap: no restart, no reconnect, no dropped turn;
    #   * **the pin** — an explicit `MOXIE_APP` wins over any per-child pick (PR #77's
    #     owner rule). It is enforced in `brains.resolve_brain`, which every read goes
    #     through, so a pick stored before the pin appeared cannot install anything.
    #
    # A brain that cannot be built on this box (a `webhook` with no endpoint, an `llm`
    # with no `MOXIE_LLM_BASE_URL`) keeps the appliance TALKING with the brain it already
    # had, and says so once — the same trade `_install_voice` makes, for the same reason:
    # a downgrade caused by an attempt to improve things is the worst shape a failure can
    # take.
    BRAIN_KEY = brain_seam.CONFIG_KEY

    def set_brain_engines(self, engines):
        """Install the appliance's brain builders (`config.brain_engines()`).

        Without one the card still renders and the appliance keeps its boot brain — an
        honest floor rather than a picker that offers what this box cannot build."""
        self._brain_engines = engines

    def _brain_availability(self) -> dict:
        """`{available, pin, pin_note, default}` — never raises.

        No discovery and no network: unlike the gateway's voice catalog, the set of
        brains is a table in this repo. With no engines installed the answer is still the
        real table, marked with the brain this runtime actually booted with.
        """
        boot = brain_seam.sanitize_brain(getattr(self.app, "name", "")) \
            or brain_seam.DEFAULT_BRAIN
        engines = self._brain_engines
        if engines is not None:
            try:
                out = engines.available() or {}
                return {"available": list(out.get("available")
                                          or brain_seam.options(default=boot)),
                        "pin": brain_seam.sanitize_brain(out.get("pin")),
                        "pin_note": str(out.get("pin_note") or ""),
                        "default": brain_seam.sanitize_brain(out.get("default")) or boot}
            except (Exception, SystemExit) as e:   # a broken seam is local-only, and a
                # misconfigured `MOXIE_APP` raises SystemExit rather than Exception — a
                # card that 500s is a worse answer than a card that shows the real table.
                self._note("brain", f"🧠 brain options unavailable: {type(e).__name__}")
        return {"available": brain_seam.options(default=boot), "pin": "",
                "pin_note": "", "default": boot}

    def brain_pin(self) -> str:
        """The brain `MOXIE_APP` pins right now — `""` when it pins nothing."""
        return self._brain_availability()["pin"]

    def brain_for(self, device_id) -> dict:
        """Which brain answers THIS robot, and which layer said so.

        `{brain, source, requested, pinned, note}` — `brains.resolve_brain` over
        `defaults ⊕ fleet ⊕ per-robot`, read from the store each time so an edit made in
        another process (or by hand in `fleet/config.json`) is picked up on the next turn.
        """
        avail = self._brain_availability()
        return brain_seam.resolve_brain(
            default=avail["default"],
            fleet=self.fleet_config().get(self.BRAIN_KEY),
            robot=(self._config_overrides.get(device_id) or {}).get(self.BRAIN_KEY),
            pin=avail["pin"])

    def app_for(self, device_id):
        """The `MoxieApp` in force for one robot — built on first use, then cached.

        Called ONCE per turn, at the top, and the result is carried through the turn: a
        parent who swaps a brain mid-answer gets the new one on the child's *next*
        sentence, never halfway through this one.

        The lock covers the BUILD, never the turn: constructing a brain is a client
        object, not a network round trip, and `respond()` runs outside it.
        """
        name = self.brain_for(device_id)["brain"]
        app = self._brains.get(name)
        if app is not None:
            return app
        with self._brain_lock:
            app = self._brains.get(name)
            if app is not None:
                return app
            engines = self._brain_engines
            note = ""
            if engines is None:
                note = "no brain builders installed (set_brain_engines)"
            else:
                try:
                    app = engines.build(name)
                except SystemExit as e:          # a brain whose environment is missing
                    note = str(e)
                except Exception as e:           # noqa: BLE001 — a bad pick must not kill us
                    note = f"{type(e).__name__}: {e}"
            if app is None:
                # Keep talking with the brain we already have, and say it ONCE per name
                # rather than once per turn — a child must not pay for a parent's typo,
                # and an operator must not have to read the same line every ten seconds.
                if self._brain_failed.get(name) != note:
                    self._brain_failed[name] = note
                    self._note("brain", f"🧠 {name} could not be built — keeping "
                                        f"{getattr(self.app, 'name', '?')}: {note}")
                    print(f"[runtime] 🧠 {name} could not be built — keeping "
                          f"{getattr(self.app, 'name', '?')}: {note}", flush=True)
                return self.app
            self._wire_memory_policy(app)
            self._brains[name] = app
            self._brain_failed.pop(name, None)
            self._note("brain", f"🧠 built {brain_seam.describe_brain(name)}")
            print(f"[runtime] 🧠 built {brain_seam.describe_brain(name)}", flush=True)
            return app

    def brain_view(self) -> dict:
        """What the 🧠 card renders: every brain this appliance can run, the house rule,
        and which one answers each robot — with the layer that decided it.

        Fleet-level *and* per-robot in one document, because the whole point of the
        feature is the difference between the two: a card that showed only the appliance
        value could not show that one child is on a different brain.
        """
        avail = self._brain_availability()
        fleet = brain_seam.sanitize_brain(self.fleet_config().get(self.BRAIN_KEY))
        robots = []
        for device_id in self.robots:
            if not self.is_permitted(device_id):
                continue
            r = self.brain_for(device_id)
            override = brain_seam.sanitize_brain(
                (self._config_overrides.get(device_id) or {}).get(self.BRAIN_KEY))
            robots.append({
                "device_id": device_id,
                "child": self.robots[device_id].child.nickname,
                "brain": r["brain"], "source": r["source"],
                "requested": r["requested"], "note": r["note"],
                "label": brain_seam.describe_brain(r["brain"]),
                "override": override,
                "line": brain_seam.boot_line(r, device_id=device_id)})
        return {"ok": True, "available": avail["available"],
                "pin": avail["pin"], "pin_note": avail["pin_note"],
                "default": avail["default"], "fleet": fleet,
                "appliance": getattr(self.app, "name", ""),
                "installed": sorted(k for k in self._brains if k),
                "env_var": brain_seam.ENV_VAR, "robots": robots}

    def brain_update(self, patch, device_id: str = "", scope: str = "robot") -> dict:
        """Persist a brain pick — the house rule (`scope="fleet"`) or one robot's.

        The pick is checked against the registry AND against the environment's pin, and a
        refusal carries the sentence the card shows, naming `MOXIE_APP`. It then goes
        through the ordinary config write (`update_fleet_config` / `update_config`), so
        there is one code path that stores a parent's setting and one that pushes a
        robot's document — this method adds a validation and a log line, not a store.

        Nothing is "installed" here: the next turn resolves the layers and builds what it
        finds. That is what makes the swap free of a restart and safe mid-conversation.
        """
        try:
            name = brain_seam.normalize_brain_patch(patch, pin=self.brain_pin())
        except ValueError as e:
            return {"ok": False, "error": str(e), "reason": str(e)}
        if scope == "fleet":
            self.update_fleet_config(**{self.BRAIN_KEY: name})
            target = "fleet"
        else:
            if not device_id or device_id not in self.robots:
                err = f"unknown device_id {device_id!r}"
                return {"ok": False, "error": err, "reason": err}
            self.update_config(device_id, **{self.BRAIN_KEY: name})
            target = device_id
        line = (f"🧠 {target}: brain → {brain_seam.describe_brain(name)}" if name
                else f"🧠 {target}: brain cleared — the layer underneath decides")
        self._note("brain", line)
        print(f"[runtime] {line}", flush=True)
        out = self.brain_view()
        out["applied"] = {"scope": "fleet" if scope == "fleet" else "robot",
                          "device_id": "" if scope == "fleet" else device_id,
                          self.BRAIN_KEY: name}
        return out

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
                    self.app_for(device_id).on_connect(self.robots[device_id])
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
            line, scored = self._stage(self.NOT_PAIRED_LINE)
            return self._publish_chat(device_id, rcr.get("event_id"), backend,
                                      self.NOT_PAIRED_LINE, markup=line,
                                      end_turn=True, scored=scored)
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
                self._publish(f"/devices/{device_id}/commands/query_result", resp,
                              device_id=device_id, what="query_result")
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
                                            build_unpaired_cloud_config,
                                            robot_config_kwargs)
        if self.is_permitted(device_id):
            # `robot_config_kwargs` drops the keys that are the SERVER's business — today
            # exactly `brain`, which rides these layers because they are the one layering
            # this codebase has, and which the robot has no field for.
            cfg = build_robot_cloud_config(
                self.child, **robot_config_kwargs(self.effective_config(device_id)))
        else:
            cfg = build_unpaired_cloud_config()
            self._note("permit", f"⛔ {device_id} is not permitted — pending "
                                 f"(minimal config, no child data)")
        self._publish(f"/devices/{device_id}/config", cfg,
                      device_id=device_id, what="config")
        print(f"[runtime] → pushed config to {device_id} "
              f"(pairing_status={cfg.get('pairing_status')})")
        return cfg

    # ---- device commands: wake (the one the console used to fake) ------------------
    #
    # `POST /api/robots/{id}/wakeup` in the parent console returned `{"error": null}` and
    # published nothing — a button that reported success for an action that never
    # happened. The command itself is real and recovered:
    #
    #   topic   /devices/{device_id}/commands/wakeup
    #   payload {"command": "wakeup"}
    #
    # `docs/architecture/mqtt-and-conversation.md` §3.5 (the cloud→robot command table,
    # "wake a `wake_button_enabled` robot from screen-off") on the topic shape
    # `cloud-protocol.md`:147 establishes for every command. What the corpus does NOT
    # establish is an acknowledgement: no `commands/wakeup` reply, no state field that
    # flips. So this method reports what it truly knows — that the command was published —
    # and says plainly that the robot never confirms. Nothing here has run against a
    # physical robot.
    WAKEUP_COMMAND = "wakeup"

    def wake_robot(self, device_id) -> dict:
        """Publish the recovered `wakeup` command at one robot.

        `{ok:true, published:true, acknowledged:false}` when it went out — never a claim
        that the robot woke. `ok:false` (with a reason a parent can act on) for an unknown
        device, a robot still pending a permit, or no broker connection."""
        robot = self.robots.get(device_id)
        if robot is None:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "No robot with that id has connected to this appliance."}
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "robot is pending",
                    "reason": "Let this robot in first (Permit it in the fleet panel)."}
        # `if self.client is None` asked whether an OBJECT existed, not whether there was
        # a connection — so a live client over a dead socket answered `published: true`,
        # which is the exact failure PR #55 shipped to kill, surviving in the one place
        # that fix did not look. `published` is the only true thing this route can ever
        # say (the command has no acknowledgement in the recovered corpus), so saying it
        # falsely is the whole bug.
        if not self._broker_connected():
            return {"ok": False, "device_id": device_id, "published": False,
                    "acknowledged": False, "error": "no broker connection",
                    "reason": self.NO_BROKER_REASON}
        topic = f"/devices/{device_id}/commands/{self.WAKEUP_COMMAND}"
        payload = {"command": self.WAKEUP_COMMAND}
        ok, why = self._publish(topic, payload, device_id=device_id, what="wakeup")
        if not ok:
            # The socket died between the check and the write. Still not a success.
            return {"ok": False, "device_id": device_id, "published": False,
                    "acknowledged": False, "error": "publish failed", "reason": why}
        cfg = self.effective_config(device_id) or {}
        # `wake_button_enabled` defaults True in the config we build (cloud_config.py),
        # so "absent" means "on", and only an explicit False is a warning worth showing.
        wake_button = cfg.get("wake_button_enabled", True)
        note = ("Sent. The robot sends no acknowledgement for this command, so this "
                "confirms the message left the appliance, not that Moxie woke up.")
        if wake_button is False:
            note += (" Heads up: this robot's wake button is switched off in Settings, "
                     "which is the setting the recovered command depends on.")
        self._note("robot", f"⏰ wakeup published to {device_id}")
        print(f"[runtime] ⏰ → {topic} {payload}", flush=True)
        return {"ok": True, "device_id": device_id, "published": True,
                "acknowledged": False, "topic": topic, "payload": payload,
                "wake_button_enabled": bool(wake_button), "note": note}

    # ---- rehearsal: watch a line perform before a child does ----
    def preview(self, device_id, text, *, speak=False, **opts) -> dict:
        """Stage one line and publish it as an ordinary turn — the preview hook (C7).

        `sim-as-a-client.md`'s guarantee is that the SIM is **not a special case**, so
        this adds no SIM-specific API and no SIM-specific message. It plans the line,
        validates it, renders it and publishes a perfectly ordinary
        `/devices/<id>/commands/remote_chat` with `result=SUCCESS` — byte-identical in
        shape to what a real turn produces. Whatever is subscribed as that device renders
        it: the browser SIM, `virtual_moxie.py`, or a robot paired as a rehearsal device.

        **Nothing else happens.** No brain is called, no history is written, no turn is
        recorded, no memory is folded, no safety journal row is added beyond the ordinary
        assessment below. That is what makes it a rehearsal: an author can iterate on a
        line and *see* the performance before a child does.

        `speak=True` also synthesizes, so a rehearsal can be heard as well as seen. It is
        off by default because a preview must not spend a voice call unless it was asked
        to — an author is usually watching the body, not listening.

        The returned `performance` is the staged structure as JSON (mood/act/gesture/
        gaze/icon/sfx per beat) plus `dropped`, the ids `validate` refused, so a console
        can flag them in red instead of leaving an author wondering why nothing played.
        """
        robot = self.robots.get(device_id)
        if robot is None:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "No robot with that id has connected to this appliance."}
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "robot is pending",
                    "reason": "Let this robot in first (Permit it in the fleet panel)."}
        line = str(text or "").strip()
        if not line:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "empty line", "reason": "Type a line to rehearse."}
        # A rehearsal line is still a line a child could hear, so it passes the same
        # output-side classifier a brain's own line does — and, like telehealth, a BLOCK
        # comes back to the author with its reason instead of being replaced by a
        # redirect. There is a human at the keyboard; substituting for them helps nobody.
        verdict = self._assess(line, safety_seam.MOXIE)
        if verdict and verdict.action == safety_seam.BLOCK:
            self._record_safety(device_id, verdict)
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "blocked", "blocked": True,
                    "categories": list(verdict.categories),
                    "reason": "Moxie will not say that. Nothing was published — "
                              "please rephrase."}
        event_id = f"preview-{int(time.time() * 1000)}"
        staged = perform(line, turn_key=event_id, chunk_index=0, **opts)
        scored = dict(staged.scored)
        self._publish_chat(device_id, event_id, "router", line, staged.markup,
                           result=ResultCode.SUCCESS, scored=scored)
        if speak:
            self._maybe_synthesize(device_id, staged.markup, event_id, chunk_num=0)
        self._note("preview", f"🎬 rehearsed '{line[:40]}' on {device_id}")
        print(f"[runtime] 🎬 preview → {device_id}: '{line[:60]}'", flush=True)
        out = {"ok": True, "device_id": device_id, "published": True, "spoke": speak,
               "event_id": event_id, "text": line, "markup": staged.markup,
               "mode": staged.mode, "scored": scored,
               "performance": performance_seam.to_json(staged.performance),
               "dropped": list(getattr(staged.performance, "dropped", ()) or ())}
        if verdict:
            out["flagged"] = list(verdict.categories)
        return out

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
        _, red_scored = self._stage(red.text, red, turn_key=event_id,
                                    markup=red.markup)
        self._publish_chat(device_id, event_id, "router", red.text, red.markup,
                           result=ResultCode.SUCCESS, safety=verdict,
                           scored=red_scored)
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
    #
    # Durable since 2026-09-02. Until then an ingested Packet lived only in
    # `RobotContext.extra["telemetry"]` — a list in this process's RAM, capped at 50 —
    # so the 📈 card was an event log over one supervisor lifetime and a restart erased
    # every answer to "what did Moxie do last week". Two collections now back it
    # (`moxie_sdk/telemetry.py` owns both shapes, both caps and the policy filter):
    #
    #   robots/<id>/telemetry_packets.json  a ring of the newest envelopes  ("just now")
    #   robots/<id>/telemetry_daily.json    one row per calendar day        ("last week")
    #
    # The in-memory buffer stays — every existing read path still uses it — but it is now
    # a **cache of the ring, hydrated from disk on first touch**, so `telemetry_count`,
    # the insights view and the schedule planner all see history after a restart.
    def telemetry_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy governing what may be *written to disk* about this robot's
        telemetry — the parent's explicit `logging_policy` if there is one, else
        `TELEMETRY_POLICY`.

        Read from the **effective** config (fleet ⊕ per-robot) like `memory_policy`, so a
        house rule set once for the appliance governs every robot on it and one robot can
        still be set apart."""
        raw = (self.effective_config(device_id) or {}).get("logging_policy")
        if raw is None:
            return TELEMETRY_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return TELEMETRY_POLICY

    def telemetry_persists(self, device_id) -> bool:
        """False under `NO_DATA` — nothing about this child's telemetry is stored."""
        return self.telemetry_policy(device_id) != LoggingPolicy.NO_DATA

    def _telemetry_buffer(self, device_id, robot=None) -> list:
        """This robot's live packet buffer, **hydrated from the durable ring on first
        touch**. Returns the list itself, so callers may append to it.

        Load-on-boot lives here rather than in `_device_connect` on purpose: tests and
        the SIL harness register robots directly (`rt.robots[id] = RobotContext(...)`),
        and a robot that reconnects mid-session must see the same history as one the
        supervisor met at startup. Hydrating at the single read point makes every path
        durable without a second place to forget."""
        robot = robot if robot is not None else self.robots.get(device_id)
        if robot is None:
            return []
        buf = robot.extra.get("telemetry")
        if buf is None:
            stored = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, [])
            buf = [p for p in stored if isinstance(p, dict)] if isinstance(stored, list) else []
            robot.extra["telemetry"] = buf
        return buf

    def telemetry_rollup(self, device_id) -> dict:
        """This robot's daily roll-up record as stored (`{}`-safe)."""
        return self.store.read(device_id, telemetry_seam.DAILY_COLLECTION,
                               telemetry_seam.new_rollup())

    def ingest_telemetry(self, device_id, payload):
        """Parse an incoming telemetry Packet, keep it live, and persist it per policy.
        Returns the parsed packet (or None on parse failure)."""
        try:
            pkt = telemetry_seam.parse_packet(payload)
        except Exception:
            return None
        robot = self.robots.get(device_id)
        if robot is not None:
            buf = self._telemetry_buffer(device_id, robot)
            buf.append(pkt)
            del buf[: max(0, len(buf) - telemetry_seam.max_packets())]
            self._persist_telemetry(device_id, pkt)
        self._note("telemetry", f"📈 {pkt.get('event_name', 'event')}")
        return pkt

    def _persist_telemetry(self, device_id, pkt) -> bool:
        """Write one Packet through the privacy gate. True when something was stored.

        A telemetry write must never cost a child their turn, so every failure here is
        printed and swallowed — this runs on the MQTT thread."""
        row = telemetry_seam.storable_packet(pkt, self.telemetry_policy(device_id))
        if row is None:                       # LoggingPolicy.NO_DATA — nothing on disk
            return False
        try:
            self.store.append(device_id, telemetry_seam.PACKETS_COLLECTION, row,
                              cap=telemetry_seam.max_packets())
            self.store.write(device_id, telemetry_seam.DAILY_COLLECTION,
                             telemetry_seam.roll_up_packet(
                                 self.telemetry_rollup(device_id), row))
            return True
        except Exception as e:
            print(f"[runtime] telemetry write failed: {e}", flush=True)
            return False

    def telemetry_view(self, device_id, limit: int = 20, days: int = 7) -> dict:
        """The parent console's per-robot insights view (M6): the ring rolled up by
        `summarize_events` + the newest `limit` events + the last `days` days of daily
        history from the durable roll-up.

        Known to the store but not connected is still a real answer — a parent asking
        what happened last week should get it whether or not the robot is on the broker
        right now — so a device with stored history is `ok:true` even when it is absent
        from `self.robots`. Neither → `{ok:false}` (the HTTP layer answers 404)."""
        robot = self.robots.get(device_id)
        rollup = self.telemetry_rollup(device_id)
        totals = telemetry_seam.rollup_totals(rollup)
        if robot is None:
            stored = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, None)
            if stored is None and not totals["days_kept"]:
                return {"ok": False, "device_id": device_id,
                        "error": f"unknown device_id {device_id!r}"}
            packets = [p for p in stored if isinstance(p, dict)] if isinstance(stored, list) else []
        else:
            packets = self._telemetry_buffer(device_id, robot)
        summary = telemetry_seam.summarize_events(packets, limit=limit)
        policy = self.telemetry_policy(device_id)
        return {"ok": True, "device_id": device_id,
                "summary": summary, "events": summary["latest"],
                # What a parent needs to read the card honestly: how far back the store
                # really goes, the lifetime total behind the sliding window, and whether
                # anything is being written at all.
                "policy": policy.name,
                "persisted": policy != LoggingPolicy.NO_DATA,
                "connected": robot is not None,
                "retention": telemetry_seam.retention(),
                "history": telemetry_seam.history_view(rollup, days=days),
                "totals": totals}

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
            self.app_for(device_id).on_event(
                robot, name, dict(payload) if isinstance(payload, dict) else {})
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
        _, greet_scored = self._stage(text, turn_key=event_id, markup=markup)
        self._publish_chat(device_id, event_id, backend, text, markup,
                           result=ResultCode.SUCCESS, scored=greet_scored)
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
        markup, scored = self._stage(text, turn_key=f"greet|{event_id}", chunk_index=0)
        self._note("chat", f"hello (queued): '{text[:40]}'")
        print(f"[runtime] 👋 delivering queued opener on {device_id}: '{text}'", flush=True)
        self._publish_chat(device_id, event_id, "router", text, markup,
                           result=ResultCode.REPLY_PENDING, chunk_num=0,
                           is_completed=False, scored=scored)
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
        # An event from a robot we do not know about **registers** it, exactly as
        # `_on_state` has always done ("fallback if we missed the log line"). The two
        # ingress paths were asymmetric and it mattered: `$SYS/broker/log` is published
        # live and never replayed (A15), and a real Moxie publishes `/state` on *its*
        # connect — so after a supervisor restart, with the robot still happily connected,
        # there is nothing to re-read and nothing to wait for. This path used to build an
        # **ephemeral** RobotContext and answer the turn from it forever: no config push,
        # no `app.on_connect`, no presence state, invisible in `/status`. Three lines, and
        # the difference between "the appliance recovered" and "the appliance is answering
        # a robot it does not know it has". The pairing gate is unaffected — it lives on
        # the transport boundary in `_on_message` and has already run by here.
        if device_id not in self.robots:
            self._device_connect(device_id)
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
            self.app_for(device_id).on_event(robot, name, data)
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
        # 🧠 Which brain answers THIS child (`app_for`) — resolved exactly once, here, and
        # carried through the turn. A parent who swaps brains while Moxie is mid-sentence
        # gets the new one on the next turn; this one finishes with what it started with.
        app = self.app_for(device_id)
        if self.streaming:
            stream = None
            try:
                stream = app.respond_stream(turn)
            except Exception as e:
                print(f"[runtime] app.respond_stream error: {e}", flush=True)
            if stream is not None:
                return self._handle_stream_turn(device_id, event_id, speech, turn,
                                                seq, stream, app=app)
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
            reply = app.respond(turn)
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
        markup, scored = self._stage(reply.text, reply, turn_key=event_id,
                                     chunk_index=0, markup=reply.markup)
        self._note("chat", f"💬 '{speech[:30]}' → '{reply.text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{reply.text[:60]}'", flush=True)
        # A filler already went out → this is chunk 1 and it ends the sequence. No
        # filler → the single-chunk reply we have always sent, unchanged on the wire.
        chunk = 1 if filler is not None else None
        # `scored` already carries the app's own mood/dialog_act — validated, because
        # `_stage` puts them through the same catalog every other id goes through. Passing
        # the raw `reply.mood`/`reply.dialog_act` here as well would let an app's invented
        # act win over that check inside `_publish_chat` (mutation M28's other half).
        self._publish_chat(device_id, event_id, "router", reply.text, markup,
                           actions=reply.actions, end_turn=reply.end_turn,
                           result=reply.result_code, chunk_num=chunk,
                           is_completed=None if chunk is None else True,
                           scored=scored)
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
            _, scored = self._stage(text, turn_key=event_id, markup=markup)
            self._publish_chat(device_id, event_id, "router", text, markup,
                               result=ResultCode.REPLY_PENDING, chunk_num=0,
                               is_completed=False, scored=scored)
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
            return text

    # ---- one turn, streamed sentence by sentence ----
    def _handle_stream_turn(self, device_id, event_id, speech, turn, seq, stream,
                            app=None):
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
                reply = (self._safe_respond(turn, app=app) if failed is not None
                         else Reply(text=""))
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

    def _safe_respond(self, turn, app=None):
        """One non-streamed answer, from the brain this turn resolved (`app_for`).

        `app=None` means the appliance's own — the only callers that pass nothing are the
        ones that have no device in hand."""
        try:
            return (app if app is not None else self.app).respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            return Reply(text="Hmm, let me think about that.")

    def _stage(self, text, obj=None, *, turn_key="", chunk_index=0, markup=None, **kw):
        """`(markup, scored)` for one spoken line — the seam's answer, plus the app's own.

        This is the single place a published turn becomes a *scored* turn. Before the
        behavior planner, `Reply.mood`/`dialog_act` were plumbed end to end and **no app
        ever set them**, and `ReplyChunk` did not have the fields at all — so a streamed
        answer could not carry scored output even in principle
        (docs/architecture/backlog/expressiveness.md §2.3, C4/C5). Now every path through
        `_publish_chat` that says words comes through here.

        Precedence, and the reason for it: **the app's own scoring wins**, field by field,
        and the seam fills in only what the app left None. A brain that knows its line is
        an `apology` is not second-guessed by a rule engine — but a brain that says
        nothing still ships a scored turn. Anything the app *did* say is a HINT into the
        planner as well, so the staged performance agrees with the wire fields rather than
        contradicting them, and an id it invents is dropped by `validate` like any other.

        `markup` is an app's authored markup: it is spoken verbatim (the idempotence rule),
        and the line is scored anyway.
        """
        hints = dict(kw)
        for attr, key in (("mood", "mood_hint"), ("gesture", "gesture_hint"),
                          ("dialog_act", "dialog_act"), ("emotion", "emotion"),
                          ("signal", "signal"), ("gaze", "look"), ("icon", "icon"),
                          ("sfx", "sfx")):
            value = getattr(obj, attr, None)
            if value:
                hints.setdefault(key, value)
        if getattr(obj, "mood_intensity", 0):
            hints.setdefault("intensity", obj.mood_intensity)
        staged = perform(text, turn_key=turn_key, chunk_index=chunk_index, **hints)
        scored = dict(staged.scored)
        # The app's own values win — but they take the SAME positive list every other id
        # takes. An app is a brain by another name, and a brain may suggest, it may never
        # authorize: a `dialog_act` that is not one of the recovered 22 is dropped here
        # rather than forwarded onto `RemoteChatOutput`. (Found by mutation M28.)
        for key, catalog in (("mood", vocab_seam.MOODS),
                             ("dialog_act", vocab_seam.DIALOG_ACTS),
                             ("emotion", vocab_seam.EMOTION_STATES),
                             ("signal", vocab_seam.SIGNALS)):
            value = getattr(obj, key, None)
            if value and value in catalog:
                scored[key] = value
        strength = getattr(obj, "mood_intensity", 0)
        if strength and 0 < int(strength) <= vocab_seam.MAX_INTENSITY:
            scored["mood_intensity"] = int(strength)
        if obj is not None and getattr(obj, "performance", None) is None:
            try:                      # diagnostics + the preview panel; never the wire
                object.__setattr__(obj, "performance", staged.performance)
            except Exception:
                pass
        return (staged.markup if markup is None else markup), scored

    def _publish_stream_chunk(self, device_id, event_id, chunk, n, final,
                              synthesize=True, ann=None):
        """One `ReplyChunk` (or `Reply`) onto the wire, with its chunk bookkeeping.

        `ann` is the chunk's index *within the answer* (fillers excluded). The markup
        floor emits the mood on index 0 only, so a streamed answer holds one face all
        the way through instead of flipping it every sentence — and a "let me think"
        line ahead of the answer does not cost the answer its mood.
        """
        markup, scored = self._stage(chunk.text, chunk, turn_key=event_id,
                                     chunk_index=n if ann is None else ann,
                                     markup=chunk.markup)
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
                           is_completed=None if solo else bool(final),
                           scored=scored)
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
            _, scored = self._stage(text, turn_key=event_id, chunk_index=n,
                                    markup=markup)
            self._publish_chat(device_id, event_id, "router", text, markup,
                               result=ResultCode.REPLY_PENDING, chunk_num=n,
                               is_completed=False, scored=scored)
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
            self._publish(f"/devices/{device_id}/commands/tts", resp,
                          device_id=device_id, what="tts")
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
    #
    # And one thing the card is NOT allowed to do: overrule the operator. An explicit
    # `MOXIE_TTS`/`MOXIE_STT` pins the engine (`voice_settings.pin_for_env`), the pinned
    # side's dropdown offers only that engine's entries, and `pin_notes` carries the
    # sentence that says which variable did it. A picker that silently moved a deployment
    # off local Piper would be a bug — see the owner rule in `voice_settings`' header.

    DEFAULT_VOICE_TEST_LINE = "Hi, I'm Moxie."
    #: How long a console WRITE may wait for the first gateway listing (seconds). Only
    #: `voice_update` uses it — see `_voice_discovery`. Generous because it is paid once,
    #: by a parent who just pressed Save, and the alternative is refusing their pick.
    VOICE_SETTLE_S = 10.0

    def set_voice_engines(self, engines):
        """Install the appliance's engine builders + discovery (`config.voice_engines()`).

        Without one the picker still works and offers `tone` / `off` — an honest floor
        rather than a card that claims models this box cannot build."""
        self._voice_engines = engines

    def _voice_discovery(self, *, refresh: bool = False,
                         settle_s: float = 0.0) -> dict:
        """`{available, discovering, gateway_error}` — never raises.

        A discovery that throws is reported as `gateway_error` beside the local entries,
        because a card that empties itself when a proxy hiccups is worse than one that
        says the gateway is unreachable next to the options it already had.

        `settle_s` is the only way this waits, it is bounded, and only `voice_update`
        passes it: a WRITE has to be judged against the real list, or a supervisor that
        booted three seconds ago refuses a perfectly good pick with "choose one of: tone"
        (seen live on 2026-09-02). Reads — the card's poll, and anything a turn touches —
        pass 0 and get whatever is cached, instantly.
        """
        blank = {k: "" for k in voice_seam.KINDS}
        engines = self._voice_engines
        if engines is None:
            return {"available": voice_seam.build_available(), "discovering": False,
                    "gateway_error": "", "pins": dict(blank), "pin_notes": dict(blank)}
        try:
            out = engines.available(refresh=refresh, settle_s=settle_s)
        except Exception as e:              # noqa: BLE001 — any failure is local-only
            return {"available": voice_seam.build_available(), "discovering": False,
                    "gateway_error": type(e).__name__,
                    "pins": dict(blank), "pin_notes": dict(blank)}

        def _side(field):
            src = out.get(field) if isinstance(out.get(field), dict) else {}
            return {k: str(src.get(k) or "") for k in voice_seam.KINDS}

        # `pins`/`pin_notes` are what an explicit `MOXIE_TTS`/`MOXIE_STT` has taken off
        # the table (`config.VoiceEngines.available`). They travel with the availability
        # they explain, so the card can never show a filtered list without its reason.
        return {"available": out.get("available") or voice_seam.build_available(),
                "discovering": bool(out.get("discovering")),
                "gateway_error": str(out.get("gateway_error") or ""),
                "pins": _side("pins"), "pin_notes": _side("pin_notes")}

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
                "pins": disc["pins"], "pin_notes": disc["pin_notes"],
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
            disc = self._voice_discovery(settle_s=self.VOICE_SETTLE_S)
            stored = voice_seam.read_settings(self.store)
            try:
                settings = voice_seam.normalize_voice_settings(
                    patch, disc["available"], current=stored)
            except ValueError as e:
                # A refusal that names only the surviving options reads as "the gateway
                # lost your voice"; when the environment is what removed it, say that.
                notes = " ".join(n for k, n in sorted(disc["pin_notes"].items())
                                 if n and k in (patch if isinstance(patch, dict) else {}))
                why = f"{e} {notes}".strip()
                return {"ok": False, "error": why, "reason": why}
            voice_seam.write_settings(self.store, settings)
            resolved = voice_seam.resolve_settings(settings, disc["available"])
            applied = self._install_voice(resolved["current"], chosen=resolved["chosen"],
                                          pins=disc["pins"])
        out = self.voice_view()
        out["applied"] = applied
        return out

    def _install_voice(self, current: dict, *, chosen: dict | None = None,
                       pins: dict | None = None) -> dict:
        """Build both engines for `current` and bind them. Returns one report per side.

        **A build that fails keeps the engine that is already speaking.** Losing the voice
        because a newly chosen one could not be constructed would be a downgrade caused by
        an *attempt to improve things*, which is the worst shape a failure can take. `off`
        is the one intentional `None`, so it is spelled out rather than inferred.

        `pins` is what `MOXIE_TTS`/`MOXIE_STT` allow (`config.engine_pins`). It changes
        nothing here — the builders enforce it themselves — but a choice the pin will
        ignore gets a note saying so, because a log line reading `speech: piper-ryan
        (gateway, chosen)` next to a box that is speaking with Piper is a lie.
        """
        chosen, pins = chosen or {}, pins or {}
        engines = self._voice_engines
        report = {}
        for kind in voice_seam.KINDS:
            choice = current.get(kind) or voice_seam.make_choice(
                voice_seam.BUILTIN_ENGINE[kind])
            engine, note = None, ""
            if not voice_seam.honours_pin(kind, choice, pins.get(kind) or ""):
                note = (f"{voice_seam.ENV_VAR[kind]} pins the engine to "
                        f"{pins.get(kind)} — this pick is not installed")
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

    # ---- 📦 content packs (backlog/content-packs.md) ----
    # Content stops being a file in our repository and becomes a thing a parent installs
    # and a stranger publishes: one JSON file, reviewed before it changes anything, undoable
    # afterwards. Everything hard is in the pure `moxie_sdk/content/packs.py` — this region
    # is the store, the clock and the live swap, and nothing else.
    #
    # Three properties are load-bearing here, and each is asserted by a test:
    #   * **Review writes nothing.** `content_review` is a pure read; only `content_import`
    #     touches the store, and only after it has taken the one-slot snapshot `undo`
    #     restores (R1: one atomic `write_shared`, so a crash leaves the old set or the new
    #     one, never a mixture).
    #   * **The overlay is written, never the merged view.** Effective content is *shipped
    #     defaults ⊕ overlay*; an import writes only the accepted items into the overlay, so
    #     a future release's improved starter chat is still an upgrade rather than something
    #     the overlay silently shadows.
    #   * **The swap is one attribute.** `reload_content()` reassigns `self.app.module`; a
    #     turn already in flight finishes on the module object it started with and the NEXT
    #     turn uses the new one. There is no lock in the turn loop — the same rule the voice
    #     picker adopted for engine swaps — and that is documented behaviour, not an
    #     oversight. `_push_config` is untouched: nothing a P0 pack carries reaches
    #     `RobotCloudConfig`, which is exactly why face/config packs are P2.

    CONTENT_ITEMS_COLLECTION = "content_items"    # → $MOXIE_DATA_DIR/fleet/content_items.json
    CONTENT_PACKS_COLLECTION = "content_packs"    # the ledger the 📦 card lists
    CONTENT_BACKUP_COLLECTION = "content_backup"  # the ONE-slot pre-import snapshot

    @staticmethod
    def pack_max_bytes() -> int:
        """Largest pack body this appliance will buffer (`MOXIE_PACK_MAX_BYTES`, 1 MiB).

        Read per call rather than at import, so the cap is testable and a deployment can
        raise it without a code change. Upstream has no cap at all and round-trips the
        pack through a hidden form field twice."""
        try:
            value = int(os.environ.get("MOXIE_PACK_MAX_BYTES", "").strip() or 0)
        except ValueError:
            value = 0
        return value if value > 0 else content_packs.DEFAULT_MAX_BYTES

    def _content_apps(self) -> list:
        """Every live app that carries a content module.

        Since 🧠 per-child brains, "the content app" is not necessarily `self.app`: an
        appliance whose default is `llm` can still have one child on `content`, built
        lazily by `app_for` and held in `_brains`. A pack import that swapped only
        `self.app.module` would install content that the child who is actually running it
        never sees — so the swap iterates. De-duplicated by identity, because the
        appliance's own brain is also cached under its own name.
        """
        apps, seen = [], set()
        for app in [getattr(self, "app", None)] + list(self._brains.values()):
            if app is None or id(app) in seen:
                continue
            seen.add(id(app))
            if (getattr(app, "module", None) is not None
                    or getattr(app, "content_defaults", None) is not None):
                apps.append(app)
        return apps

    def _content_defaults(self) -> dict:
        """The SHIPPED baseline the overlay sits on top of.

        `config.build_content_app()` records it on the app (`content_defaults`) *before* it
        applies the overlay, which is the only way an `undo` can put a shipped item back
        after a pack replaced it. Without it — a bare `MoxieApp`, or an app built some other
        way — we fall back to the loaded module itself, which is the same answer on a fresh
        appliance and an honest approximation on one that has already imported (the merge is
        idempotent, and overlay entries win either way)."""
        for app in self._content_apps():
            recorded = getattr(app, "content_defaults", None)
            if isinstance(recorded, dict):
                return recorded
        for app in self._content_apps():
            items = content_packs.items_from_module(getattr(app, "module", None))
            if items:
                return items
        return content_packs.items_from_module(getattr(self.app, "module", None))

    def _content_overlay(self) -> dict:
        """The installed overlay (`fleet/content_items.json`) — `{}` when nothing imported."""
        rec = self.store.read_shared(self.CONTENT_ITEMS_COLLECTION, {}) or {}
        items = rec.get("items") if isinstance(rec, dict) else None
        return items if isinstance(items, dict) else {}

    def _write_content_overlay(self, items: dict) -> bool:
        """One atomic write of the whole overlay (R1) — never a partial merge."""
        return self.store.write_shared(self.CONTENT_ITEMS_COLLECTION,
                                       {"items": items, "updated_at": int(time.time())})

    def _content_packs(self) -> list:
        rec = self.store.read_shared(self.CONTENT_PACKS_COLLECTION, {}) or {}
        rows = rec.get("packs") if isinstance(rec, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def content_items(self) -> dict:
        """**Effective content**: shipped defaults, then the overlay by `kind:key`."""
        return content_packs.merge_items(self._content_defaults(), self._content_overlay())

    def _known_child_names(self) -> list:
        """Names this appliance knows, for the export-time PII flag.

        The child profile the supervisor was started with, every connected robot's, and any
        name-ish string in the fleet config. It catches the names we know and **nothing
        else** — a prompt naming a sibling or a school sails straight through, and the card
        says so."""
        names = []
        for child in ([getattr(self, "child", None)]
                      + [getattr(r, "child", None) for r in self.robots.values()]):
            nick = str(getattr(child, "nickname", "") or "").strip()
            if nick and nick not in names:
                names.append(nick)
        for key, value in (self.fleet_config() or {}).items():
            if isinstance(value, str) and ("name" in key or "nickname" in key):
                value = value.strip()
                if value and value not in names:
                    names.append(value)
        return names

    def reload_content(self) -> dict:
        """Rebuild the live `ContentModule` from defaults ⊕ overlay and swap it in.

        One attribute assignment. The next turn renders the new prompt; a turn already in
        flight finishes on the module it started with, and a conversation session keeps its
        `Conversation` for that session (brief §2.5). No restart, and nothing on the wire —
        a pack is server-side data.
        """
        defaults, overlay = self._content_defaults(), self._content_overlay()
        module = content_packs.build_module(defaults, overlay)
        live = False
        for app in self._content_apps():
            if getattr(app, "module", None) is not None:
                app.module = module              # ← the whole swap, per live content brain
                live = True
        return {"ok": True, "live": live,
                "conversations": len(module.conversations),
                "globals": len(module.globals),
                "schedules": len(module.schedules),
                "overlay": len(overlay), "shipped": len(defaults)}

    def content_view(self) -> dict:
        """The 📦 card's poll: the inventory, the pack ledger, and whether undo is armed."""
        items = self.content_items()
        backup = self.store.read_shared(self.CONTENT_BACKUP_COLLECTION, {}) or {}
        rows = content_packs.inventory(items, known_names=self._known_child_names())
        return {
            "ok": True,
            "items": rows,
            "packs": self._content_packs(),
            "counts": {"total": len(rows),
                       "edited": sum(1 for r in rows if r["local_edited"]),
                       "with_code": sum(1 for r in rows if r["has_code"]),
                       "from_packs": sum(1 for r in rows if r["origin"] == "pack")},
            "undo_available": bool(isinstance(backup, dict) and backup.get("items") is not None),
            "undo_label": str((backup or {}).get("label") or ""),
            "max_bytes": self.pack_max_bytes(),
            "pack_format": content_packs.PACK_FORMAT,
        }

    def content_export(self, keys=None, *, name: str = "", pack_id: str = "",
                       details: str = "", author: str = "", now=None) -> dict:
        """Build a pack from the named installed items (`kind:key`), or from all of them.

        Returns the pack itself — the HTTP layer serializes it and the browser saves it.
        A key that is not installed is an error rather than a quietly smaller file.
        """
        items = self.content_items()
        wanted = [str(k).strip() for k in (keys or []) if str(k or "").strip()]
        if wanted:
            missing = [k for k in wanted if k not in items]
            if missing:
                raise content_packs.PackError(
                    "not installed: " + ", ".join(sorted(missing)))
            items = {k: items[k] for k in wanted}
        if not items:
            raise content_packs.PackError("there is nothing to export")
        label = str(name or "").strip() or "Moxie content"
        return content_packs.export_pack(items, name=label,
                                         pack_id=pack_id or label, details=details,
                                         author=author, now=now)

    def content_review(self, body) -> dict:
        """What WOULD happen if this pack were imported. Writes nothing, reads no clock.

        `expect_digest` in the answer is the digest of the body as reviewed; echoing it back
        on import is what closes the review-one-file-import-another gap that upstream's
        hidden form field leaves open.
        """
        pack, meta = content_packs.parse_pack(body)
        rows = content_packs.review_pack(pack, self.content_items(),
                                         digest=meta["digest"])
        return {
            "ok": True,
            "pack": {k: v for k, v in pack.items() if k != "items"},
            "digest": meta["digest"],
            "expect_digest": meta["computed"],
            "warnings": meta["warnings"],
            "items": rows,
            "accept": [r["id"] for r in rows if r["default"]],
            "counts": {"total": len(rows),
                       "default": sum(1 for r in rows if r["default"]),
                       "conflicts": sum(1 for r in rows
                                        if r["state"] in (content_packs.CONFLICT,
                                                          content_packs.DOWNGRADE_CONFLICT)),
                       "invalid": sum(1 for r in rows
                                      if r["state"] == content_packs.INVALID)},
        }

    def content_import(self, body, accept=None, expect_digest: str = "") -> dict:
        """Apply the accepted items, then make them live. The only verb here that writes.

        Refuses with `conflict: True` (HTTP **409**) when `expect_digest` — the digest the
        reviewer was shown — is not the digest of the body now being imported: the pack is
        re-sent between review and import (the server holds no session state), so the two
        can genuinely be different files.
        """
        pack, meta = content_packs.parse_pack(body)
        if expect_digest and str(expect_digest) != meta["computed"]:
            return {"ok": False, "conflict": True,
                    "error": "this is not the pack that was reviewed",
                    "reason": "The file changed between the review and the import. "
                              "Review it again before installing.",
                    "expect_digest": meta["computed"]}
        with self._content_lock:
            overlay = self._content_overlay()
            merged, summary = content_packs.apply_pack(pack, overlay, accept or [],
                                                       now=int(time.time()))
            if summary["applied"]:
                self.store.write_shared(self.CONTENT_BACKUP_COLLECTION, {
                    "items": overlay, "packs": self._content_packs(),
                    "label": f"before importing {pack.get('name') or pack.get('id')}",
                    "at": int(time.time())})
                if not self._write_content_overlay(merged):
                    return {"ok": False, "error": "could not write the content overlay",
                            "reason": "The appliance could not save the imported items."}
                ledger = [r for r in self._content_packs()
                          if r.get("id") != summary["pack"]["id"]]
                ledger.append(summary["pack"])
                self.store.write_shared(self.CONTENT_PACKS_COLLECTION, {"packs": ledger})
            reload = self.reload_content()
        self._note("content", f"📦 imported {summary['count']} item(s) "
                              f"from {pack.get('id')}")
        return {"ok": True, "digest": meta["digest"], **summary, "reload": reload,
                "undo_available": bool(summary["applied"])}

    def content_undo(self) -> dict:
        """Put the one-slot snapshot back — the overlay AND the ledger, byte for byte."""
        with self._content_lock:
            backup = self.store.read_shared(self.CONTENT_BACKUP_COLLECTION, {}) or {}
            items = backup.get("items") if isinstance(backup, dict) else None
            if not isinstance(items, dict):
                return {"ok": False, "error": "nothing to undo",
                        "reason": "No import has been made since this appliance started "
                                  "keeping a snapshot."}
            self._write_content_overlay(items)
            packs_before = backup.get("packs")
            if isinstance(packs_before, list):
                self.store.write_shared(self.CONTENT_PACKS_COLLECTION,
                                        {"packs": packs_before})
            self.store.delete_shared(self.CONTENT_BACKUP_COLLECTION)   # one slot, used up
            reload = self.reload_content()
        self._note("content", "📦 undo — content restored")
        return {"ok": True, "restored": len(items), "reload": reload,
                "label": str(backup.get("label") or ""), "undo_available": False}

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
            apps = self._content_apps() or [self.app]
            schedules = getattr(getattr(apps[0], "module", None), "schedules", None)
        except Exception as e:
            print(f"[runtime] schedule template unavailable ({e}); using the default")
        robot = self.robots.get(device_id)
        packets = self._telemetry_buffer(device_id, robot) if robot else []
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
        self._publish(telehealth_seam.telehealth_topic(device_id), command,
                      device_id=device_id, what="telehealth")
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
            self._publish(f"/devices/{device_id}/commands/query_result", resp,
                          device_id=device_id, what="query_result")
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
        self._publish(f"/devices/{device_id}/commands/zmq", resp,
                      device_id=device_id, what="stt_result")
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
                      chunk_num=None, is_completed=None, safety=None, scored=None):
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
        # `scored` is the seam's answer for this line (`_stage`); explicit mood/
        # dialog_act arguments still win, because a caller that passed one meant it.
        sc = dict(scored or {})
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood or sc.get("mood"),
                                   dialog_act=dialog_act or sc.get("dialog_act"),
                                   modules=modules,
                                   chunk_num=chunk_num, is_completed=is_completed,
                                   safety=safety, subscribe_events=subscribe,
                                   mood_intensity=sc.get("mood_intensity"),
                                   emotion=sc.get("emotion"),
                                   signals=sc.get("signal"))
        self._publish(f"/devices/{device_id}/commands/remote_chat", resp,
                      device_id=device_id, what="remote_chat")
