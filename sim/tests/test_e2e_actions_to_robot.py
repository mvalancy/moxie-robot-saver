"""
Do `Reply.actions` actually reach the ROBOT? — the delivery half.

`test_action_tags.py` reads `response_actions` off a recording `FakeClient`. Here the REAL
`MoxieRuntime` and the SIL robot (`sim/virtual_moxie.py`) share `helpers_runtime.loopback()`,
the robot starts the turn, and assertions read `payload["response_actions"]` as the robot's
own handler decoded it. That the robot then ACTS on it is `test_actions_reach_the_robot.py`
(and `sim/test_bridge.mjs` for the browser), both held to
`goldens/cloud_to_robot_actions.json`.

Hermetic and instant: synchronous loopback, `LLMApp`'s canned `client=` seam, no sleeps.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))

pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")

from helpers_runtime import loopback, make_runtime          # noqa: E402
from virtual_moxie import FIRMWARE, VirtualMoxie            # noqa: E402

DEV = "d_actions_e2e"


class _CannedCompletion:
    """The OpenAI client's shape, one canned assistant message — the `client=` seam, so
    the real `LLMApp` (persona, JSON contract, tag parsing) runs with no network."""

    def __init__(self, content):
        self._content = content
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        msg = type("M", (), {"content": self._content})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def _brain(canned):
    from moxie_sdk.apps import LLMApp
    return LLMApp(base_url="http://127.0.0.1:1/v1", api_key="not-used", model="test",
                  client=_CannedCompletion(canned))


def _turn(canned, speech="can we draw?"):
    """One whole turn, robot-first, and what the ROBOT was left holding.

    Returns `(vm, runtime_side)` — `vm.reply_payload` is the RemoteChatResponse as the
    robot's own handler decoded it, not as the server recorded sending it.
    """
    rt, dev = make_runtime(_brain(canned), device_id=DEV, nickname="Sam")
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    rt_side, _ = loopback(rt, vm)
    vm.client.publish(vm.t_state, json.dumps(
        {"software_version": FIRMWARE, "state": "config"}))
    vm.client.publish(vm.t_event("remote-chat"), json.dumps(
        {"event_id": "evt-actions", "command": "prompt", "backend": "router",
         "speech": speech}))
    rt._pool.shutdown(wait=True)
    return vm, rt_side


# --------------------------------------------------------------------------- #
# A launch action, all the way into the robot's hands
# --------------------------------------------------------------------------- #
def test_a_launch_action_arrives_at_the_robot_in_the_recovered_shape():
    vm, _ = _turn('{"say": "Yes! Let\'s draw. <launch:DRAW:default>", '
                  '"mood": "positive", "gesture": "celebrate"}')
    assert not vm.errors, vm.errors
    payload = vm.reply_payload
    assert payload, "the robot received no remote_chat reply at all"
    assert payload["command"] == "remote_chat" and payload["result"] == "SUCCESS", payload

    ra = payload.get("response_actions")
    assert ra, f"the robot's payload carries no response_actions: {payload}"
    entry = next(a for a in ra if a.get("action"))
    assert entry["action"] == "launch", entry
    assert entry["module_id"] == "DRAW", entry
    assert entry["content_id"] == "default", entry
    # RemoteChatAction.output_type — every action entry the contract sends carries one.
    assert entry["output_type"] == "GLOBAL", entry


def test_the_child_never_hears_the_tag_the_action_came_from():
    """The action is a machine field; the tag must not survive into anything spoken.
    Asserted on the ROBOT's copy, because that is the text a client would read out."""
    vm, _ = _turn('{"say": "Yes! Let\'s draw. <launch:DRAW:default>", '
                  '"mood": "positive", "gesture": "celebrate"}')
    output = vm.reply_payload["output"]
    assert output["text"] == "Yes! Let's draw.", output
    assert "<launch" not in output["text"] and "<launch" not in output["markup"], output
    assert vm.reply_text == output["text"], (vm.reply_text, output["text"])


def test_an_exit_action_arrives_at_the_robot():
    vm, _ = _turn('{"say": "Bye Sam! <exit>", "mood": "positive", "gesture": "talk"}',
                  speech="bye moxie")
    actions = [a["action"] for a in vm.reply_payload.get("response_actions", [])
               if a.get("action")]
    assert actions == ["exit"], vm.reply_payload
    assert vm.reply_payload["output"]["text"] == "Bye Sam!"


def test_an_untagged_reply_leaves_the_robot_with_no_action_to_take():
    """The negative case, so the assertions above cannot pass by accident: an ordinary
    answer must not put a stray action in the robot's hands. `response_actions` may still
    exist carrying only the event subscription — what must not appear is an `action`."""
    vm, _ = _turn('{"say": "Tell me about it!", "mood": "positive", "gesture": "question"}',
                  speech="hi moxie")
    actions = [a for a in vm.reply_payload.get("response_actions", []) if a.get("action")]
    assert actions == [], vm.reply_payload


# --------------------------------------------------------------------------- #
# The external-brain path: actions a service declares outright
# --------------------------------------------------------------------------- #
def test_actions_from_an_external_brain_reach_the_robot_too():
    """`WebhookApp` is how something outside this repo becomes Moxie's brain, and its
    contract lets a service name `actions` directly rather than writing tags. The same
    wire, the same robot, so the action path is not an LLM-only feature."""
    from moxie_sdk.apps import WebhookApp

    class _Webhook(WebhookApp):
        """The real app with its one network call stubbed — everything after `_post`
        (the JSON→Reply decoding, the ActionType validation) is the shipped code."""

        def _post(self, path_hint, body):
            return {"text": "Let's play a game!",
                    "actions": [{"type": "launch", "module_id": "GAME",
                                 "content_id": "level1"},
                                {"type": "not-a-real-action"}]}

    rt, dev = make_runtime(_Webhook("http://127.0.0.1:1/turn"), device_id=DEV)
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    loopback(rt, vm)
    vm.client.publish(vm.t_event("remote-chat"), json.dumps(
        {"event_id": "evt-webhook", "command": "prompt", "backend": "router",
         "speech": "let's play"}))
    rt._pool.shutdown(wait=True)

    ra = [a for a in vm.reply_payload.get("response_actions", []) if a.get("action")]
    assert len(ra) == 1, f"the bogus action type should have been dropped: {ra}"
    assert (ra[0]["action"], ra[0]["module_id"], ra[0]["content_id"]) == (
        "launch", "GAME", "level1")
    assert vm.reply_payload["output"]["text"] == "Let's play a game!"
