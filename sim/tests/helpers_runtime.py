"""
Shared harness for tests that drive a turn through the REAL `MoxieRuntime`.

`FakeClient` records `publish()` calls and the runtime's MQTT client is never built
(`MoxieRuntime` creates it lazily in `run()`), so nothing here touches a network. The app
may be a live one — that is how the live e2e tests reach the gateway over a fake transport.
Also: the live tier's credential loader, a status-HTTP server on a free port, an in-process
robot↔runtime loopback, and source/`run.py` loaders for guards.
"""
from __future__ import annotations
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT_DIR = os.path.join(REPO, "mqtt")
SUPERVISOR_DIR = os.path.join(MQTT_DIR, "supervisor")
for _p in (MQTT_DIR, SUPERVISOR_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from moxie_sdk.tts import Synthesizer          # noqa: E402  (needs the path above)

CHAT_TOPIC = "/devices/{device_id}/commands/remote_chat"
RUNTIME_PKG = os.path.join(SUPERVISOR_DIR, "moxie_runtime")


def reload_config(monkeypatch, clear=(), **env):
    """`mqtt/config.py` re-imported under a controlled environment (it caches at import):
    `MOXIE_SKIP_DOTENV` first, so a developer's `mqtt/.env` cannot refill the `clear`ed
    variables, then `env` applied."""
    import importlib
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    for k in clear:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config
    return importlib.reload(config)


def fresh_pool(rt):
    """Re-arm the runtime's worker pool: `drive_turn` shuts it down when it drains it, so a
    SECOND turn through the same runtime needs a live one."""
    from concurrent.futures import ThreadPoolExecutor
    rt._pool = ThreadPoolExecutor(max_workers=4)
    return rt


def seed_absent(rt, dev, away_s, *, greeted=False):
    """Seed presence as if the robot left `away_s` seconds ago. Clock-relative because
    presence is scored as an AGE against `greet_after_s`; a pinned epoch would make every
    robot look long gone."""
    import time
    from moxie_sdk import presence
    now = time.time()
    state = presence.new_state()
    state.update({"face_present": False, "announced": "left",
                  "last_seen_at": now - away_s - 30.0,
                  "present_since": now - away_s - 60.0,
                  "last_lost_at": now - away_s, "absent_since": now - away_s,
                  "faces_seen": 1, "events": 2})
    if greeted:
        state["greeted_at"] = now - away_s + 0.1
    rt.robots[dev].extra["presence"] = state
    return state


def load_mqtt_run():
    """A fresh `mqtt/run.py` module, loaded by PATH: `server/run.py` shares the name `run`,
    so a bare `import run` depends on which suite last prepended its directory."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run", os.path.join(MQTT_DIR, "run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def runtime_sources() -> dict:
    """`{path: text}` for every module of the `moxie_runtime` package, for tests that
    assert over the supervisor's source (a guard over one file would miss the others)."""
    out = {}
    for name in sorted(os.listdir(RUNTIME_PKG)):
        if name.endswith(".py"):
            path = os.path.join(RUNTIME_PKG, name)
            with open(path, encoding="utf-8") as fh:
                out[path] = fh.read()
    return out


def runtime_source() -> str:
    """The whole supervisor package's source as one string (for substring guards)."""
    return "\n".join(runtime_sources().values())


# ---------------------------------------------------------------------------
# Credentials for the live tests: mqtt/.env, found from ANY worktree
# ---------------------------------------------------------------------------
# `mqtt/.env` is git-ignored and exists only in the main checkout, so these look in this
# tree first and then the MAIN worktree (else the creds-gated tier silently skips in a
# `git worktree`).

def main_worktree(tree: str) -> str:
    """The main checkout's root, given any worktree root: a linked worktree's `.git` is a
    FILE (`gitdir: <main>/.git/worktrees/<name>`). Pure path work; surprises return `tree`."""
    dotgit = os.path.join(tree, ".git")
    if os.path.isfile(dotgit):
        try:
            line = open(dotgit).read().strip()
        except OSError:
            return tree
        if line.startswith("gitdir:"):
            gitdir = os.path.abspath(line.split(":", 1)[1].strip())
            marker = os.sep + ".git" + os.sep + "worktrees" + os.sep
            head = gitdir.split(marker)[0]
            if head != gitdir and os.path.isdir(head):
                return head
    return tree


def find_repo_dotenv(start: str = REPO) -> str | None:
    """Path to `mqtt/.env` — this tree's if present, else the main worktree's, else None."""
    for root in (start, main_worktree(start)):
        path = os.path.join(root, "mqtt", ".env")
        if os.path.isfile(path):
            return path
    return None


def dotenv_values(path: str) -> dict:
    """`KEY=VALUE` lines of a .env file as a dict (blank lines + `#` comments skipped)."""
    values = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip()
    except OSError:
        pass
    return values


#: The ONLY keys a deployment's `mqtt/.env` may export into the suite's environment:
#: credentials, endpoints and model names. `setdefault` at collection time would otherwise
#: promote every key for the whole session, so a hermetic test "with nothing configured"
#: would run on the developer's settings (e.g. `MOXIE_ALLOW_UNVERIFIED_BOTS=1` made
#: "an unpermitted stranger is refused" tests pass with the gate open). Behavioural knobs
#: (`MOXIE_APP`, `MOXIE_STT`, `MOXIE_TTS`, …) never cross; no live suite reads them.
#:
#: Derived, not remembered: every name is one an AST walk finds a `test_live_*.py` READING
#: from `os.environ` (names a module only writes are absent).
#: `test_dotenv_cannot_perturb_the_suite.py` re-derives the list and fails on a mismatch.
LIVE_KEYS = (
    # --- the brain -------------------------------------------------------------
    "MOXIE_LLM_API_KEY", "LITELLM_MASTER_KEY",   # credential, and the gateway's own name
    "MOXIE_LLM_BASE_URL", "MOXIE_LLM_MODEL",
    # --- the voice -------------------------------------------------------------
    "MOXIE_VOICE_API_KEY", "MOXIE_VOICE_BASE_URL",
    "MOXIE_VOICE_MODEL", "MOXIE_VOICE_ALT_MODEL",   # the model name IS the voice
    # --- the ears --------------------------------------------------------------
    "MOXIE_STT_API_KEY", "MOXIE_STT_BASE_URL", "MOXIE_STT_MODEL",
    # --- what a live run may be aimed at ---------------------------------------
    #: A deployed origin to POST at (`test_live_hosted_ears.py` tier B) and a WAV to reuse
    #: instead of synthesising one. Endpoint and input, not appliance behaviour: neither
    #: can make a hermetic test assert something different.
    "MOXIE_DEMO_ORIGIN", "MOXIE_EARS_WAV",
)


def load_repo_dotenv(path: str | None = None, *, allow=LIVE_KEYS) -> str | None:
    """Best-effort: load the live tier's credentials from the git-ignored `mqtt/.env` into
    `os.environ`; returns the file used or None. Values are never printed.

    Only keys in `allow` (default `LIVE_KEYS`) cross, and the existing environment wins
    (`setdefault`, like `config._load_env`). `allow` exists so a guard can pass the file's
    own keys and prove the narrowing is load-bearing; no production caller passes it."""
    path = path or find_repo_dotenv()
    if not path:
        return None
    allow = frozenset(allow)
    for k, v in dotenv_values(path).items():
        if k in allow:
            os.environ.setdefault(k, v)
    return path


#: paho's `MQTT_ERR_SUCCESS` / `MQTT_ERR_NO_CONN`, spelled out so this module keeps its
#: no-hard-dependency shape (the fast tier installs paho, the helper does not require it).
MQTT_ERR_SUCCESS = 0
MQTT_ERR_NO_CONN = 4


class FakeInfo:
    """paho's `MQTTMessageInfo`, as much of it as `_publish()` reads."""

    def __init__(self, rc=MQTT_ERR_SUCCESS):
        self.rc = rc
        self.mid = 0

    def is_published(self) -> bool:
        return self.rc == MQTT_ERR_SUCCESS


class FakeClient:
    """Stands in for the paho client: records `(topic, decoded_payload)` publishes, and
    models the connection — a QoS 0 publish with no socket is dropped and returns
    `MQTT_ERR_NO_CONN` (A3). `drop()` / `up()` / `refuse()` are §5.1's fault-injection
    verbs and drive the runtime's real callbacks.
    """

    def __init__(self, runtime=None):
        self.published: list = []
        #: Publishes the fake refused because there was no socket — the messages a real
        #: broker never saw. Kept apart from `published` for exactly that reason.
        self.dropped: list = []
        self.subscribed: list = []
        #: How many `subscribe()` CALLS were made, as distinct from how many topics they
        #: covered. One call is one SUBACK, which is what lets the runtime treat the ack
        #: as a single unambiguous event (see `_on_subscribe`).
        self.subscribe_calls = 0
        self._mid = 0
        #: Whether there is a socket — True by default (a working transport). Distinct from
        #: the runtime's own `broker_connected`, which starts False until a CONNACK.
        self.connected = True
        #: The runtime whose callbacks the verbs drive. `make_runtime` sets it.
        self.runtime = runtime

    def publish(self, topic, payload):
        if not self.connected:
            self.dropped.append((topic, payload))
            return FakeInfo(MQTT_ERR_NO_CONN)
        self.published.append((topic, json.loads(payload)))
        return FakeInfo(MQTT_ERR_SUCCESS)

    def subscribe(self, topic, qos=0):
        """paho's signature: ONE topic, or the `[(topic, qos), …]` list the runtime sends
        so that one SUBSCRIBE is answered by one SUBACK. `subscribed` stays a flat list of
        topics either way — what a test asks is *which topics*, not how they were batched.
        """
        self.subscribe_calls += 1
        if isinstance(topic, str):
            self.subscribed.append(topic)
        else:
            self.subscribed.extend(t if isinstance(t, str) else t[0] for t in topic)
        self._mid += 1
        return (MQTT_ERR_SUCCESS, self._mid)

    def is_connected(self) -> bool:
        return self.connected

    # -- fault injection (production-hardening.md §5.1) -----------------------
    def _reason(self, rc):
        """A paho `ReasonCode` when paho is importable, else the bare int — so a test can
        say `refuse(rc=5)` and get what a real CONNACK would hand the callback."""
        try:
            import paho.mqtt.client as mqtt
            from paho.mqtt.reasoncodes import ReasonCode
            return ReasonCode(mqtt.CONNACK >> 4, identifier=0 if rc == 0 else 135)
        except Exception:
            return rc

    def up(self, rc=0):
        """A successful CONNACK: socket live, runtime re-subscribes, broker acknowledges — in
        that order. The SUBACK comes AFTER `_on_connect` returns, as paho's does, so the
        connected-but-deaf window (see `_on_subscribe`) is modelled rather than closed.
        """
        before = len(self.subscribed)
        self.connected = True
        if self.runtime is not None:
            self.runtime._on_connect(self, None, {}, self._reason(rc), None)
            if len(self.subscribed) > before:     # a refusal subscribes nothing to ack
                self.runtime._on_subscribe(self, None, self._mid, [0], None)
        return self

    def drop(self, rc=7):
        """The socket went away (broker restart, Wi-Fi, NAT). Publishes now fail."""
        self.connected = False
        if self.runtime is not None:
            self.runtime._on_disconnect(self, None, {}, self._reason(rc), None)
        return self

    def refuse(self, rc=5):
        """A CONNACK **refusal** — `rc=5`, not authorised, which PR #44's broker
        credential made reachable for the first time. The socket is closing; nothing may
        be subscribed on it."""
        self.connected = False
        if self.runtime is not None:
            self.runtime._on_connect(self, None, {}, self._reason(rc), None)
        return self

    # -- convenience readers -------------------------------------------------
    def on(self, topic: str) -> list:
        """Every payload published to `topic`, in order."""
        return [p for (t, p) in self.published if t == topic]

    def chat_replies(self, device_id: str) -> list:
        return self.on(CHAT_TOPIC.format(device_id=device_id))


class LatchClient(FakeClient):
    """A `FakeClient` a test can wait on (`wait_for(predicate)`) instead of sleeping, for
    turns that publish several times from several threads."""

    def __init__(self, runtime=None):
        super().__init__(runtime)
        import threading
        self._cond = threading.Condition()

    def publish(self, topic, payload):
        with self._cond:
            info = super().publish(topic, payload)
            self._cond.notify_all()
            return info

    def wait_for(self, predicate, timeout=10.0) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: predicate(list(self.published)), timeout)


class CountingSynth(Synthesizer):
    """A `moxie_sdk.tts.Synthesizer` that records every line it was asked to speak."""
    name = "counting"
    sample_rate = 16000

    def __init__(self):
        self.spoken = []

    def synthesize(self, text, voice=None):
        self.spoken.append(text)
        return b"\x01\x02" * 8


def make_runtime(app, *, device_id: str = "d_test", nickname: str = "Sam",
                 module_id: str = "FREE_CHAT", content_id: str = "default",
                 allow_unverified_bots: bool = True, store=None):
    """A real `MoxieRuntime` wired to `app`, with a fake transport and one robot already
    'connected'. Returns `(runtime, device_id)`.

    Pass `store=JsonStore(str(tmp_path))` so durable writes stay out of the developer's data
    dir (None keeps the runtime's default). `allow_unverified_bots` defaults True because the
    robot is hand-placed into `rt.robots`; pairing-gate tests build their own runtime.
    """
    import moxie_runtime
    from moxie_sdk.types import ChildProfile, RobotContext

    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname=nickname),
                                    allow_unverified_bots=allow_unverified_bots,
                                    store=store)
    rt.client = FakeClient(runtime=rt)
    rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child,
                                        module_id=module_id, content_id=content_id)
    return rt, device_id


def drive_turn(rt, device_id: str, speech: str, *, event_id: str = "evt-1",
               command: str = "prompt", backend: str = "router", **extra) -> dict:
    """Push one `events/remote-chat` payload through the runtime and return the last
    `commands/remote_chat` response. Waits for the worker pool to drain, so the runtime is
    spent afterwards — build a fresh one per turn (`drive_once`).
    """
    robot = rt.robots[device_id]
    payload = dict(command=command, backend=backend, event_id=event_id, speech=speech)
    payload.update(extra)
    rt._on_remote_chat(device_id, robot, json.dumps(payload))
    rt._pool.shutdown(wait=True)
    replies = rt.client.chat_replies(device_id)
    assert replies, f"runtime published no remote_chat; saw {rt.client.published!r}"
    return replies[-1]


def drive_once(app, speech: str, **kw) -> dict:
    """`make_runtime` + `drive_turn` in one call — the common case."""
    turn_kw = {k: kw.pop(k) for k in ("event_id", "command", "backend") if k in kw}
    input_vars = kw.pop("input_vars", None)
    if input_vars is not None:
        turn_kw["input_vars"] = input_vars
    rt, device_id = make_runtime(app, **kw)
    return drive_turn(rt, device_id, speech, **turn_kw)


def assert_spec_response(resp: dict, *, device_id: str = None, event_id: str = None):
    """Assert a published payload really is a spec-conformant RemoteChatResponse
    (embodied/robotbrain/RemoteChat.proto — see moxie_sdk/wire.py::build_chat_response).
    Returns the response so callers can chain."""
    from moxie_sdk.types import ResultCode
    assert resp.get("command") == "remote_chat", resp
    assert resp.get("result") == ResultCode.SUCCESS, resp   # the uint32 value, 0
    assert resp.get("backend") == "router", resp
    if event_id is not None:
        assert resp.get("event_id") == event_id, resp
    out = resp.get("output") or {}
    assert isinstance(out, dict), resp
    assert out.get("text", "").strip(), f"empty spoken text: {resp!r}"
    assert out.get("markup", "").strip(), f"empty markup: {resp!r}"
    # A spec response is the whole turn: no open chunk (REPLY_PENDING / is_completed:false
    # say "more is coming"; `end_turn` has no proto field and is not on the wire).
    assert (resp.get("consistency_control") or {}).get("is_completed") is not False, resp
    assert "end_turn" not in resp, resp
    return resp


# ---------------------------------------------------------------------------
# A supervisor on a scratch data dir, its real status HTTP server, and an
# in-process robot↔runtime loopback
# ---------------------------------------------------------------------------

def free_port() -> int:
    """A port nothing listens on right now (bind :0). Never hard-code one: stale supervisors
    and concurrent agents hold 8930/8932/19xx on lab machines."""
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def status_server(rt) -> str:
    """Start the runtime's REAL status HTTP server (`_start_status_server`) on a free port and
    return its base URL. Daemon thread; dies with the process."""
    port = free_port()
    rt._start_status_server(port)
    return f"http://127.0.0.1:{port}"


def http_json(url: str, *, method: str = "GET", body=None, timeout: float = 5.0):
    """One JSON request against the status server → the decoded response.

    Raises `urllib.error.HTTPError` on 4xx/5xx so a test can assert the status code.
    """
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def http_call(url: str, *, method: str = "GET", body=None, timeout: float = 5.0):
    """`http_json` for tests that assert refusals: `(status, decoded body)`, 4xx/5xx
    included rather than raised."""
    import urllib.error
    try:
        return 200, http_json(url, method=method, body=body, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class _Msg:
    """paho's message object, as much of it as `_on_message` reads."""

    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload if isinstance(payload, bytes) else str(payload).encode()


class _LoopSide:
    """One direction of the loopback: record the publish, then hand the exact bytes to
    the other end's `_on_message`. Synchronous — when `publish()` returns, the far side
    has already answered, so a test never sleeps."""

    def __init__(self, peer_on_message):
        self._deliver = peer_on_message
        self.published: list = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        self._deliver(None, None, _Msg(topic, payload))


def loopback(rt, vm):
    """Wire a real `MoxieRuntime` and a `sim/virtual_moxie.py` robot together in-process: each
    side's publishes reach the other's `_on_message` on the real topics. No broker, network or
    sleeps. Returns `(runtime_side, robot_side)`.
    """
    rt.client = _LoopSide(vm._on_message)
    vm.client = _LoopSide(rt._on_message)
    return rt.client, vm.client
