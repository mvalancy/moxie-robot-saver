"""
Integration test — a turn round-trips through the REAL MoxieRuntime pipeline with a
fake MQTT transport (no broker). Covers M1: that a brain's Reply (text + actions +
ResultCode) reaches the robot as a spec-conformant RemoteChatResponse on
/devices/{id}/commands/remote_chat. Exercises _on_remote_chat → _handle_turn →
_publish_chat → build_chat_response → client.publish — the actual runtime code.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

from moxie_sdk.app import MoxieApp                       # noqa: E402
from moxie_sdk.types import Reply, Action, ActionType, RobotContext, ChildProfile  # noqa: E402
import moxie_runtime                                     # noqa: E402


class _FakeClient:
    """Records publishes; no network."""
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


class _ActionApp(MoxieApp):
    name = "test-action"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}",
                     actions=[Action(type=ActionType.LAUNCH, module_id="DRAW",
                                     content_id="default")])


class _OfflineApp(MoxieApp):
    name = "test-offline"

    def respond(self, turn):
        return Reply.offline()


def _drive(app, device_id="d_test", speech="hello"):
    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()                             # inject fake transport
    rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child)
    event = json.dumps({"command": "prompt", "backend": "router",
                        "event_id": "evt-9", "speech": speech})
    rt._on_remote_chat(device_id, rt.robots[device_id], event)
    rt._pool.shutdown(wait=True)                          # flush the turn off the pool
    return rt.client.published


def _chat(published, device_id="d_test"):
    topic = f"/devices/{device_id}/commands/remote_chat"
    msgs = [p for (t, p) in published if t == topic]
    assert msgs, f"no remote_chat published; got {published}"
    return msgs[-1]


def test_turn_roundtrips_text_actions_and_success():
    published = _drive(_ActionApp(), speech="let's draw")
    resp = _chat(published)
    assert resp["command"] == "remote_chat"
    assert resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "You said: let's draw"
    assert resp["output"]["markup"]                       # markup auto-generated
    ra = resp["response_actions"]
    assert len(ra) == 1 and ra[0]["action"] == "launch"
    assert ra[0]["module_id"] == "DRAW" and ra[0]["content_id"] == "default"


def test_offline_brain_signals_error_offline_over_the_wire():
    published = _drive(_OfflineApp())
    resp = _chat(published)
    assert resp["result"] == "ERROR_OFFLINE"              # robot uses local fallback


def test_content_module_runs_through_the_runtime():
    """End-to-end: a shipped content module, driven by ContentApp inside the real
    runtime, produces the module's reply on the wire (event→runtime→ContentApp→publish)."""
    from moxie_sdk.content import load_modules, ContentApp
    starter = os.path.join(REPO, "mqtt", "content_modules", "starter.json")
    with open(starter) as fh:
        module = load_modules(json.load(fh))
    app = ContentApp(module, lambda messages: "Dinosaurs are amazing!")
    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()
    did = "d_content"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child,
                                  module_id="FREE_CHAT", content_id="default")
    rt._on_remote_chat(did, rt.robots[did],
                       json.dumps({"command": "prompt", "event_id": "e",
                                   "speech": "tell me about dinosaurs"}))
    rt._pool.shutdown(wait=True)
    resp = _chat(rt.client.published, did)
    assert resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "Dinosaurs are amazing!"


def test_history_accumulates_across_the_pipeline():
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_hist"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt._on_remote_chat(did, rt.robots[did],
                       json.dumps({"command": "prompt", "event_id": "e", "speech": "hi"}))
    rt._pool.shutdown(wait=True)
    h = rt.history.get(did, [])
    assert {"role": "user", "content": "hi"} in h
    assert any(m["role"] == "assistant" for m in h)
