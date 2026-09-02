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

CHAT_TOPIC = "/devices/{device_id}/commands/remote_chat"


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


def make_runtime(app, *, device_id: str = "d_test", nickname: str = "Sam",
                 module_id: str = "FREE_CHAT", content_id: str = "default"):
    """A real `MoxieRuntime` wired to `app`, with a fake transport and one robot
    already 'connected'. Returns `(runtime, device_id)`."""
    import moxie_runtime
    from moxie_sdk.types import ChildProfile, RobotContext

    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname=nickname))
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
