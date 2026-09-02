"""
Shared harness for tests that drive a turn through the REAL `MoxieRuntime`.

Two suites had already grown their own private copy of "a fake MQTT transport plus
twenty lines that push one `events/remote-chat` payload through the runtime and dig
the reply back out" (`test_runtime_turn.py`, `test_action_tags.py`). A third copy
was about to appear for the live-gateway tests, so the helper lives here instead.

Deliberately NOT retrofitted into the existing two files: those are owned/edited
elsewhere, and a shared fixture that breaks them for reasons unrelated to the thing
under test is exactly the failure mode their own docstrings warn about. New tests
import this; the old copies stay until someone retires them on purpose.

Nothing here talks to a network: `FakeClient` records `publish()` calls, and the
runtime's MQTT client is never built (`MoxieRuntime` creates it lazily in `run()`).
The *app* passed in may of course be a live one — that is how the live e2e tests
reach the gateway while the transport stays fake.
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


# ---------------------------------------------------------------------------
# Credentials for the live tests: mqtt/.env, found from ANY worktree
# ---------------------------------------------------------------------------
# `mqtt/.env` is git-ignored, so it exists only in the main checkout. Every live test
# used to look for it beside its own file — which is right in the main tree and wrong in
# a `git worktree`, where the whole creds-gated tier silently skipped (PR #12 finding).
# These helpers look in this tree first and then in the MAIN worktree, so a live test
# run from a feature worktree finds the same key the main checkout uses.

def main_worktree(tree: str) -> str:
    """The main checkout's root, given any worktree root.

    A linked worktree's `.git` is a FILE holding `gitdir: <main>/.git/worktrees/<name>`;
    in the main checkout it is a directory. Pure path work — no subprocess, and any
    surprise (no .git at all, a bare/odd layout) just returns `tree`."""
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


def load_repo_dotenv(path: str | None = None) -> str | None:
    """Best-effort: load the repo's git-ignored `mqtt/.env` into `os.environ` (existing
    environment wins, exactly like the supervisor's own `config._load_env`). Returns the
    file it used, or None when there is none. Values are never printed."""
    path = path or find_repo_dotenv()
    if not path:
        return None
    for k, v in dotenv_values(path).items():
        os.environ.setdefault(k, v)
    return path


class FakeClient:
    """Stands in for the paho client: records `(topic, decoded_payload)` publishes."""

    def __init__(self):
        self.published: list = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))

    # -- convenience readers -------------------------------------------------
    def on(self, topic: str) -> list:
        """Every payload published to `topic`, in order."""
        return [p for (t, p) in self.published if t == topic]

    def chat_replies(self, device_id: str) -> list:
        return self.on(CHAT_TOPIC.format(device_id=device_id))


class LatchClient(FakeClient):
    """A `FakeClient` a test can *wait on* — `wait_for(predicate)` instead of sleeping.

    A streaming/filler turn publishes several times from several threads, so a test needs
    to block until the wire looks a certain way rather than guess how long that takes.
    (`test_brain_latency.py` has its own private copy from PR #14; new suites use this
    one — see this module's docstring on why the old copies stay put.)"""

    def __init__(self):
        super().__init__()
        import threading
        self._cond = threading.Condition()

    def publish(self, topic, payload):
        with self._cond:
            super().publish(topic, payload)
            self._cond.notify_all()

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
    """A real `MoxieRuntime` wired to `app`, with a fake transport and one robot
    already 'connected'. Returns `(runtime, device_id)`.

    `store` is the runtime's durable `JsonStore` (mentor behaviors, the schedule audit,
    permits). It defaults to None, which is exactly what `MoxieRuntime` already did —
    a store rooted at `MOXIE_DATA_DIR`/`mqtt/data`. Pass `JsonStore(str(tmp_path))` so a
    test that writes durable state cannot touch the developer's own data dir.

    `allow_unverified_bots` defaults to **True** — this harness exists to drive the turn
    loop, and its robot is hand-placed into `rt.robots` rather than let in through the
    device allowlist. Tests *about* the pairing gate build their own runtime with the
    default (closed) policy; see `sim/tests/test_device_permits.py`.
    """
    import moxie_runtime
    from moxie_sdk.types import ChildProfile, RobotContext

    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname=nickname),
                                    allow_unverified_bots=allow_unverified_bots,
                                    store=store)
    rt.client = FakeClient()
    rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child,
                                        module_id=module_id, content_id=content_id)
    return rt, device_id


def drive_turn(rt, device_id: str, speech: str, *, event_id: str = "evt-1",
               command: str = "prompt", backend: str = "router", **extra) -> dict:
    """Push one `events/remote-chat` payload through the runtime and return the last
    `commands/remote_chat` response it published (the RemoteChatResponse dict).

    The runtime answers on a worker pool, so this waits for it to drain — which also
    means the returned runtime is spent; build a fresh one per turn (`drive_once`).
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
    assert resp.get("command") == "remote_chat", resp
    assert resp.get("result") == "SUCCESS", resp
    assert resp.get("backend") == "router", resp
    if event_id is not None:
        assert resp.get("event_id") == event_id, resp
    out = resp.get("output") or {}
    assert isinstance(out, dict), resp
    assert out.get("text", "").strip(), f"empty spoken text: {resp!r}"
    assert out.get("markup", "").strip(), f"empty markup: {resp!r}"
    assert isinstance(resp.get("end_turn"), bool), resp
    return resp


# ---------------------------------------------------------------------------
# A supervisor on a scratch data dir, its real status HTTP server, and an
# in-process robot↔runtime loopback
# ---------------------------------------------------------------------------
# Four suites had already hand-rolled `socket(); bind(("127.0.0.1", 0))` to find a free
# port before `rt._start_status_server(port)` (test_memory_runtime ×2, test_runtime_turn
# ×2, test_console_roundtrip), and `test_presence_sil.py` hand-rolled the two-subscriber
# loopback that lets a `sim/virtual_moxie.py` robot talk to a real `MoxieRuntime` with no
# broker. New suites use these; the existing copies stay put for the reason this module's
# docstring gives (a shared fixture must not break a suite for reasons unrelated to the
# thing under test).

def free_port() -> int:
    """A port nothing is listening on right now — bind :0 and let the OS choose.

    Never a hard-coded number: the lab machine has stale supervisors on 8930/8932 and
    concurrent agents on 19xx, and a test that picks a port by hand eventually collides
    with one of them (see the port rules in docs/architecture/orchestration-plan.md).
    """
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def status_server(rt) -> str:
    """Start the runtime's REAL status HTTP server on a free port; return its base URL.

    This is `MoxieRuntime._start_status_server` itself — the same handlers the parent
    console talks to — so a test that goes through it proves the HTTP layer, not a double.
    The server is a daemon thread and dies with the process.
    """
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
    """Wire a real `MoxieRuntime` and a `sim/virtual_moxie.py` robot together in-process.

    Stands in for the broker: every runtime publish reaches the robot's `_on_message` and
    every robot publish reaches the runtime's, byte for byte on the real topics. No
    network, no mosquitto, no sleeps. Returns `(runtime_side, robot_side)` so a test can
    read what each end put on the wire.
    """
    rt.client = _LoopSide(vm._on_message)
    vm.client = _LoopSide(rt._on_message)
    return rt.client, vm.client
