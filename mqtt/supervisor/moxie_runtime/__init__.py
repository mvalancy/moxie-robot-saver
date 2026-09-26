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
import os, sys, threading, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # mqtt/ -> moxie_sdk

from moxie_sdk.types import RobotContext, ChildProfile
from moxie_sdk.store import JsonStore
from moxie_sdk import safety as safety_seam
from .constants import (  # noqa: F401 -- re-exported: tests and callers read moxie_runtime.<CONST>
    CONNECT_RE, DISCONNECT_RE, KEEPALIVE_S, RECONNECT_MIN_DELAY_S, RECONNECT_MAX_DELAY_S,
    MAX_MENTOR_BEHAVIORS, MENTOR_BEHAVIORS_COLLECTION, DEFAULT_BRAIN_BUDGET_S,
    MAX_FILLERS_PER_TURN, SAFETY_JOURNAL_POLICY, MEMORY_POLICY, TELEMETRY_POLICY,
    DEFAULT_GREET_AFTER_S)
from .memory import MemoryMixin
from .lifecycle import LifecycleMixin
from .status_http import StatusServerMixin
from .connection import ConnectionMixin
from .fleet import FleetMixin
from .brain import BrainMixin
from .safety import SafetyMixin
from .telemetry import TelemetryMixin
from .presence import PresenceMixin
from .turns import TurnsMixin
from .voice import VoiceMixin
from .content import ContentMixin
from .schedule import ScheduleMixin
from .telehealth import TelehealthMixin


class MoxieRuntime(LifecycleMixin, StatusServerMixin, ConnectionMixin, FleetMixin, BrainMixin,
                   MemoryMixin, SafetyMixin, TelemetryMixin, PresenceMixin, TurnsMixin,
                   VoiceMixin, ContentMixin, ScheduleMixin, TelehealthMixin):
    """The supervisor: one instance serves every robot on the broker (see module doc)."""
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
        #: Set only when the broker has **acknowledged** our subscriptions (the SUBACK),
        #: cleared on every disconnect. `broker_connected` is the CONNACK; this is one
        #: handshake later, and between the two the appliance is connected and deaf — a
        #: robot's `/state` lands on a broker with no matching subscription and is dropped,
        #: taking the QoS-0 config push that would have answered it with it. Anything that
        #: gates *robot traffic* on readiness waits for this; anything that reports *the
        #: connection* still reads `broker_connected`.
        self.subscriptions_acked = threading.Event()
        self.last_broker_connect = 0.0
        self.last_broker_disconnect = 0.0
        self.last_connect_error = ""
        #: Publishes the transport refused because there was no socket. At QoS 0 paho
        #: does not queue them (A3), so this is a count of messages the robot never got.
        self.publish_drops = 0
        # --- the connection's own history (§8 P1) ---
        #: P0's six `/status` fields are scalars in RAM: they say what is true *now* and
        #: are erased by the restart that is often the interesting event. `conn_events` is
        #: the durable ring behind them (`moxie_sdk/conn_telemetry.py`).
        #:
        #: **The re-entrancy guard is not paranoia.** `_on_store_lock_timeout` records a
        #: `lock_timeout` row — by writing to the store. If *that* write is also refused,
        #: the recorder recurses into itself, and a lock the whole appliance is contending
        #: for is exactly when it would. One flag, checked on every path in, so the worst
        #: case is a row we did not write rather than a stack we cannot unwind.
        self._recording_conn = False
        #: True from the moment a SIGTERM/SIGINT handler runs, so the disconnect it causes
        #: is reported as the clean close it is instead of as a fault.
        self._stopping = False
        #: Bumped on every successful CONNACK. The roster resume is keyed on it so a
        #: reconnect storm cannot queue N overlapping resume bursts.
        self._connect_generation = 0
        #: Robots we have had **evidence of on the current broker connection**.
        #:
        #: `self.robots` answers *"who have we served"*; this answers *"who have we heard
        #: from since this socket came up"*, and conflating them is the defect this exists
        #: to close. `_device_connect` early-returned on `device_id in self.robots`, and
        #: the only thing that ever removed a robot was `_device_disconnect`, driven by a
        #: `$SYS/broker/log` line — which **dies with the broker**. So after a broker
        #: restart the returning robot was already "known", was never re-onboarded, and
        #: got no config push and no `app.on_connect`: silently half-connected, for the
        #: rest of the session.
        self._seen_since_connect: set = set()
        self.robots: dict[str, RobotContext] = {}
        self.history: dict[str, list] = {}
        # Parent-console config editing: per-device RobotCloudConfig overrides. Declared
        # HERE, before `_load_memory()`, because the transcript's privacy gate resolves
        # through `memory_policy` → `effective_config`, which reads this dict — and the
        # boot sweep must be able to ask "is this robot under NO_DATA?" before it puts a
        # single stored transcript back into RAM.
        self._config_overrides = {}
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
        # (`self._config_overrides` is declared above, before `_load_memory()`.)
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
        #: What the **app layer** asked to be told about, as `{device: {event: module}}`.
        #: Written by `_merge_subscriptions` at the moment a request is accepted, and read
        #: by `_wake_subscribed_pack` to answer the only question the inbound half needs:
        #: *did this pack, under this module, actually ask for this event?* The module is
        #: the value rather than a second dict because the recovered contract says
        #: *"events are automatically unsubscribed when the module exits"*
        #: (RemoteModuleAPI §Unsubscribing) — so a request made under module A must not
        #: wake whatever is running under module B, and comparing one string is the whole
        #: check. Same lock and same lifetime as `_vision_subscribed`, for the same reason
        #: (`_forget_robot_state`): a cached belief about a moving thing is this project's
        #: most-repeated bug, so both beliefs are dropped by one method.
        self._pack_subscribed: dict[str, dict] = {}
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
        # The activity record's boot sweep, the twin of `_load_memory`'s transcript one.
        # A fleet-wide NO_DATA rule is durable and therefore outlives this process; the
        # packets it forbids must not outlive it too. Last in `__init__` because it needs
        # the store, the overrides dict and `effective_config` — all set by now.
        self.purge_telemetry()
