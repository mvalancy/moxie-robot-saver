"""SIL round-trip for vision events: the SIL robot sends `eb-found-face`/`eb-lost-target`
as the `speech` of a RemoteChatRequest, exactly as a real Moxie delivers a subscribed
perception event (docs/architecture/vision.md §1.1). Hermetic in-process loopback."""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))

pytest.importorskip("paho.mqtt.client")            # the SIL client needs paho
from virtual_moxie import VirtualMoxie              # noqa: E402

from helpers_runtime import seed_absent  # noqa: E402
from helpers_runtime import make_runtime            # noqa: E402
from moxie_sdk.app import MoxieApp                  # noqa: E402
from moxie_sdk.types import Reply, ResultCode       # noqa: E402


class _Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload if isinstance(payload, bytes) else str(payload).encode()


class _RuntimeSide:
    """The broker, as far as the runtime is concerned: hand every publish to the SIL
    robot's own `_on_message`, byte for byte."""

    def __init__(self, vm):
        self.vm = vm
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))
        self.vm._on_message(None, None, _Msg(topic, payload))


class _RobotSide:
    """...and the mirror image: the SIL robot's publishes reach the runtime's router."""

    def __init__(self, rt):
        self.rt = rt
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        self.rt._on_message(None, None, _Msg(topic, payload))


class _App(MoxieApp):
    name = "sil-presence"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


def _loopback(greet_after_s=300.0):
    rt, dev = make_runtime(_App(), device_id="d_sil")
    rt.greet_after_s = greet_after_s
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    rt.client = _RuntimeSide(vm)
    vm.client = _RobotSide(rt)
    return rt, vm, dev


def test_a_face_event_goes_out_on_the_remote_chat_topic_as_the_speech():
    rt, vm, dev = _loopback()
    event_id = vm.send_face_event("found")
    topic, payload = vm.client.published[-1]
    assert topic == f"/devices/{dev}/events/remote-chat"
    msg = json.loads(payload)
    assert msg["speech"] == "eb-found-face"
    assert msg["command"] == "prompt" and msg["backend"] == "router"
    assert msg["event_id"] == event_id


def test_a_raw_event_name_is_passed_through_unchanged():
    rt, vm, dev = _loopback()
    vm.send_face_event("eb-br-event", input_vars={"$eb_br_value": "The Gruffalo"})
    msg = json.loads(vm.client.published[-1][1])
    assert msg["speech"] == "eb-br-event"
    assert msg["input_vars"] == {"$eb_br_value": "The Gruffalo"}
    assert rt.robots[dev].extra["presence"]["book"]["value"] == "The Gruffalo"


def test_lost_then_found_round_trips_through_the_real_runtime():
    rt, vm, dev = _loopback()
    vm.send_face_event("lost")
    vm.send_face_event("found")
    state = rt.robots[dev].extra["presence"]
    assert state["face_present"] is True
    assert [h["event"] for h in state["history"]] == ["eb-lost-target", "eb-found-face"]
    # both were answered — the contract requires a response to a subscribed event
    replies = [p for (t, p) in rt.client.published if t.endswith("/commands/remote_chat")]
    assert len(replies) == 2
    assert all(r["result"] in (ResultCode.NOREPLY_ACK, ResultCode.SUCCESS) for r in replies)


def test_walking_back_in_after_a_long_absence_reaches_the_sil_robot_as_a_spoken_line():
    rt, vm, dev = _loopback(greet_after_s=300.0)
    seed_absent(rt, dev, away_s=900.0)
    vm.send_face_event("found")
    assert vm.got_reply.is_set(), "the SIL robot never saw a response"
    assert vm.reply_payload["result"] == ResultCode.SUCCESS, vm.reply_payload
    assert "Sam" in vm.reply_text, vm.reply_text
    assert vm.reply_payload["output"]["markup"], "the hello arrives performed"


def test_a_silent_acknowledgement_still_wakes_the_sil_robot():
    """`NOREPLY_ACK` carries no words, but it IS the terminal response for that
    event_id — a client that waited for text forever would hang."""
    rt, vm, dev = _loopback()
    vm.send_face_event("found")
    assert vm.got_reply.is_set()
    assert vm.reply_payload["result"] == ResultCode.NOREPLY_ACK
    assert vm.reply_text == ""
