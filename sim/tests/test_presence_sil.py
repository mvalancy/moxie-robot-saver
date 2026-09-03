"""
SIL round-trip for the vision events: `sim/virtual_moxie.py --face-event`.

The SIL robot publishes `eb-found-face` / `eb-lost-target` exactly the way a real Moxie
delivers a *subscribed* perception event — as the `speech` of a `RemoteChatRequest` on
`/devices/{id}/events/remote-chat` (docs/architecture/vision.md §1.1; OpenMoxie
`doc/RemoteModuleAPI.md` §Event Handling, MIT). There is no new topic and no new
envelope, which is the point: presence rides the contract we already implement.

Hermetic — a two-subscriber in-process loopback stands in for the broker, so this runs
with no network, no sleeps and no mosquitto. Elapsed absence is seeded, never waited for.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

pytest.importorskip("paho.mqtt.client")            # the SIL client needs paho
from virtual_moxie import VirtualMoxie              # noqa: E402

from helpers_runtime import make_runtime            # noqa: E402
from moxie_sdk import presence as P                 # noqa: E402
from moxie_sdk.app import MoxieApp                  # noqa: E402
from moxie_sdk.types import Reply                   # noqa: E402


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
    vm.client = _RuntimeSide(vm)            # unused by the SIM, kept for symmetry
    rt.client = _RuntimeSide(vm)
    vm.client = _RobotSide(rt)
    return rt, vm, dev


def _seed_absent(rt, dev, away_s):
    # Offsets from now, not dates — presence is scored as an age. Same reasoning as
    # `test_presence_runtime._seed_absent`; reviewed in `test_clock_dependence.py`.
    now = time.time()
    rt.robots[dev].extra["presence"] = dict(
        P.new_state(), face_present=False, announced="left", faces_seen=1,
        last_seen_at=now - away_s - 30.0, present_since=now - away_s - 60.0,
        last_lost_at=now - away_s, absent_since=now - away_s)


# --------------------------------------------------------------------------- #
# The wire shape the SIL robot emits
# --------------------------------------------------------------------------- #
def test_the_sil_robot_publishes_the_recovered_event_names():
    assert VirtualMoxie.FACE_EVENTS == {"found": "eb-found-face",
                                        "lost": "eb-lost-target"}


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


# --------------------------------------------------------------------------- #
# The round trip
# --------------------------------------------------------------------------- #
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
    assert all(r["result"] in ("NOREPLY_ACK", "SUCCESS") for r in replies)


def test_walking_back_in_after_a_long_absence_reaches_the_sil_robot_as_a_spoken_line():
    rt, vm, dev = _loopback(greet_after_s=300.0)
    _seed_absent(rt, dev, away_s=900.0)
    vm.send_face_event("found")
    assert vm.got_reply.is_set(), "the SIL robot never saw a response"
    assert vm.reply_payload["result"] == "SUCCESS", vm.reply_payload
    assert "Sam" in vm.reply_text, vm.reply_text
    assert vm.reply_payload["output"]["markup"], "the hello arrives performed"


def test_a_silent_acknowledgement_still_wakes_the_sil_robot():
    """`NOREPLY_ACK` carries no words, but it IS the terminal response for that
    event_id — a client that waited for text forever would hang."""
    rt, vm, dev = _loopback()
    vm.send_face_event("found")
    assert vm.got_reply.is_set()
    assert vm.reply_payload["result"] == "NOREPLY_ACK"
    assert vm.reply_text == ""


def test_run_face_events_records_what_the_server_answered():
    rt, vm, dev = _loopback(greet_after_s=300.0)
    _seed_absent(rt, dev, away_s=900.0)
    # The live sequence is `lost` -> wait -> `found`; the wait is the only part a
    # hermetic test may not actually spend, so it is re-seeded onto the record the
    # `lost` just wrote. Drive it by hand: `run_face_events` owns connect/loop_start,
    # which an in-process loopback has no use for.
    for kind in ("lost", "found"):
        vm._reset_turn()
        if kind == "found":
            _seed_absent(rt, dev, away_s=900.0)      # stands in for the wait
        vm.send_face_event(kind)
        vm.face_replies.append({"kind": kind,
                                "result": (vm.reply_payload or {}).get("result"),
                                "text": vm.reply_text})
    assert [r["kind"] for r in vm.face_replies] == ["lost", "found"]
    assert vm.face_replies[0]["result"] == "NOREPLY_ACK"
    assert vm.face_replies[1]["result"] == "SUCCESS" and vm.face_replies[1]["text"]


def test_the_cli_exposes_face_event_and_face_gap():
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(REPO, "sim", "virtual_moxie.py"),
                          "--help"], capture_output=True, text=True).stdout
    assert "--face-event" in out and "--face-gap" in out
