"""
Moxie robot-cloud runtime (supervisor): speaks the robot's MQTT protocol and turns it into
MoxieApp calls (docs/architecture/mqtt-and-conversation.md, verified against OpenMoxie, MIT).

  * subscribe to every device's events/state + the broker log; detect connect/disconnect
  * push each robot its config on connect — the full one only to a *permitted* device;
    an unpermitted one is pending and gets a minimal, child-free config
  * route `events/remote-chat` turns -> MoxieApp.respond -> reply; keep per-device history
  * STT (`events/zmq`) via an optional transcriber (see `handle_zmq`)

This is the transport; the brain is whatever MoxieApp is injected. Each concern lives in
its own mixin module; this class only composes them and owns the shared state.
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
from .tryit import TryItMixin


_OFF = ("0", "off", "false", "no")


def _seconds(arg, env: str, default: float) -> float:
    """A duration: the ctor argument, else `env`, else `default` (also on a bad value)."""
    try:
        return float(arg if arg is not None else os.environ.get(env) or default)
    except (TypeError, ValueError):
        return default


def _env_on(env: str) -> bool:
    """A switch that is on unless set to a falsy spelling."""
    return (os.environ.get(env) or "1").strip().lower() not in _OFF


class MoxieRuntime(LifecycleMixin, StatusServerMixin, ConnectionMixin, FleetMixin, BrainMixin,
                   MemoryMixin, SafetyMixin, TelemetryMixin, PresenceMixin, TurnsMixin,
                   VoiceMixin, ContentMixin, ScheduleMixin, TelehealthMixin, TryItMixin):
    """The supervisor: one instance serves every robot on the broker (see module doc)."""
    def __init__(self, app, host="127.0.0.1", port=1883, child: ChildProfile | None = None,
                 store: JsonStore | None = None, brain_budget_s=None, streaming=None,
                 safety=None, allow_unverified_bots=None, greet_after_s=None):
        self.app = app
        self.child = child or ChildProfile()
        self.host, self.port = host, port
        # Durable per-robot state: JSON files under MOXIE_DATA_DIR.
        self.store = store if store is not None else JsonStore()
        # A write refused because another process held the record's lock is recorded (§5.3).
        self.store.on_lock_timeout = self._on_store_lock_timeout
        # Broker state (§4.1). `broker_connected` = CONNACK seen; `subscriptions_acked` =
        # SUBACK seen. Between the two the appliance is connected but deaf (a robot's
        # `/state` would be dropped), so anything gating robot traffic waits for the SUBACK.
        self.broker_connected = False
        self.subscriptions_acked = threading.Event()
        self.last_broker_connect = 0.0
        self.last_broker_disconnect = 0.0
        self.last_connect_error = ""
        #: Publishes refused for want of a socket (QoS 0: paho does not queue them).
        self.publish_drops = 0
        # Re-entrancy guard for `_record_conn`: recording a lock timeout writes to the
        # store, and that write may itself time out.
        self._recording_conn = False
        #: Set once a stop signal runs, so the resulting disconnect is logged as clean.
        self._stopping = False
        #: Bumped per CONNACK; keys the roster resume so a reconnect storm queues one burst.
        self._connect_generation = 0
        #: Robots heard from since the *current* socket came up. `self.robots` is "who we
        #: have served"; after a broker restart (which loses the `$SYS` disconnect lines) a
        #: returning robot must still be re-onboarded, which this set makes possible.
        self._seen_since_connect: set = set()
        self.robots: dict[str, RobotContext] = {}
        self.history: dict[str, list] = {}
        # Per-device RobotCloudConfig overrides. Must exist before `_load_memory()`: the
        # transcript privacy gate resolves through `effective_config`.
        self._config_overrides = {}
        self._memory_dir = os.environ.get("MOXIE_MEMORY_DIR", "").strip()
        self._max_memory = int(os.environ.get("MOXIE_MEMORY_TURNS", "40"))
        self._load_memory()
        # Brain latency budget before a filler.
        self.brain_budget_s = _seconds(brain_budget_s, "MOXIE_BRAIN_BUDGET_S",
                                       DEFAULT_BRAIN_BUDGET_S)
        # Stream answers sentence by sentence when the app can: ctor arg, MOXIE_STREAMING, on.
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
        # Built lazily in run() so tests can inject a fake transport.
        self.client = None
        # STT (ai-seam §1): optional transcriber + per-device VAD accumulator.
        self._transcriber = None
        self._stt_sessions = {}
        self._stt_uuid = {}      # utterance uuid per device (set on any frame that has one)
        # Pairing gate: `None` reads the policy at call time (env, fleet record, closed);
        # True/False pins it for this process (SIL harness, tests).
        self._allow_unverified_bots = allow_unverified_bots
        self._permits_cache = None       # ((path, mtime, size), flag, devices)
        # TTS (ai-seam §3): optional server voice (the SIM; a real robot self-synthesizes).
        self._synth = None
        # Voice picker engine builders + discovery, injected by run.py. None = built-ins only
        # (this module never imports `config`).
        self._voice_engines = None
        self._voice_lock = threading.Lock()      # one swap at a time; never held in a turn
        # Brain picker: `self.app` is the default brain; `_brains` caches other brains by
        # NAME (one app object serves every robot on it), seeded with the default.
        self._brain_engines = None
        self._brains = {getattr(app, "name", ""): app} if app is not None else {}
        self._brain_lock = threading.Lock()      # builds only — NEVER held during a turn
        self._brain_failed: dict[str, str] = {}  # a brain that would not build, said once
        # One content import/undo at a time; never held inside a turn.
        self._content_lock = threading.Lock()
        # 💬 Try it (tryit.py): the rolling hour's spends and the tries still running.
        self._try_lock = threading.Lock()
        self._try_spent = deque()
        self._try_inflight = 0
        # Child safety (ai-seam §2) on both sides of a turn. Ctor arg wins; MOXIE_SAFETY=0
        # turns the stage off.
        if safety is None and _env_on("MOXIE_SAFETY"):
            try:
                safety = safety_seam.default_classifier()
            except Exception as e:                # a broken rules file must be LOUD
                print(f"[runtime] ⚠️  safety rules failed to load: {e}", flush=True)
                safety = None
        self.safety = safety or None
        self._last_redirect: dict[str, str] = {}   # never the same redirect twice running
        if self.safety is None:
            print("[runtime] ⚠️  input safety is OFF (MOXIE_SAFETY=0)", flush=True)
        # Presence (vision.md): the robot's vision events arrive as RemoteChatRequests.
        self._presence_lock = threading.Lock()
        self._busy: set = set()                    # robots with a turn in flight
        self._last_greeting: dict[str, str] = {}   # never the same hello twice running
        self._pending_opener: dict[str, str] = {}  # hello queued for the next turn
        self._vision_subscribed: dict[str, str] = {}   # device -> module we subscribed for
        #: What the app layer subscribed to, `{device: {event: module}}`. Keyed to the
        #: module because subscriptions end when the module exits (RemoteModuleAPI).
        self._pack_subscribed: dict[str, dict] = {}
        self.greet_after_s = _seconds(greet_after_s, "MOXIE_GREET_AFTER_S",
                                      DEFAULT_GREET_AFTER_S)
        self.vision = _env_on("MOXIE_VISION")
        # Telehealth puppet state per robot; runtime-level so it outlives a Wi-Fi drop.
        self._telehealth: dict = {}
        # Long-term memory: the app owns the store, the runtime owns the privacy switch.
        self._wire_memory_policy()
        # Boot sweep of the activity record under NO_DATA (needs store + overrides; last).
        self.purge_telemetry()
