"""
🎴 T10 — a launch card over the wire, from the robot's publish to the robot's hands.

`test_launch_cards.py` proves the decoder. `test_launch_cards_runtime.py` proves the
runtime's one call site, reading the reply back off a `FakeClient` that records the
publish and stops there. Neither of them puts a **client** on the other end, so until
this file the whole feature was unit truth: nothing had ever published an `eb-qr-event`
and then *received* a launch.

This file closes that. The real `MoxieRuntime` and the real protocol-faithful SIL robot
(`sim/virtual_moxie.py`) are wired together by `helpers_runtime.loopback()` — every
publish is delivered byte for byte to the other end's own `_on_message` — the ROBOT
starts the turn, and every assertion below reads the robot's **own state**
(`VirtualMoxie.action_stats()`), not the server's record of what it sent. That is the
difference this file exists for: `action_stats()` is written by
`virtual_moxie._apply_action`, which only ever runs because a payload arrived on
`/devices/<id>/commands/remote_chat` and the client decoded it.

The refusals are asserted on the same wire, because a suite that only showed a good card
working would pass just as well with the allowlist deleted. So `<launch_if_confirmed:…>`,
`<sleep>` and an id outside the catalog are each driven all the way through and the
robot is required to end up holding **nothing** — and to have been *answered*
(`NOREPLY_ACK`), because a refusal that left the robot waiting would be its own bug.

**The hardware ceiling has not moved.** No physical Moxie has ever sent us an
`eb-qr-event`, and a SIL robot is not a robot: it has no camera, it starts no module, and
`_apply_action` deliberately RECORDS rather than runs (see its docstring). What is proven
here is the round trip between our runtime and our simulated client, in the recovered
wire shape — not that paper works.

Hermetic: no broker, no network, no model, no sleeps. The loopback is synchronous.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")

from helpers_runtime import loopback, make_runtime          # noqa: E402
from virtual_moxie import FIRMWARE, VirtualMoxie            # noqa: E402

from moxie_sdk import launch_cards as cards                 # noqa: E402
from moxie_sdk import presence as P                         # noqa: E402
from moxie_sdk.app import MoxieApp                          # noqa: E402
from moxie_sdk.types import Reply                           # noqa: E402

DEV = "d_cards_sil"
QR = P.QR_EVENT


class _App(MoxieApp):
    """A brain that would answer anything — and must never be asked.

    A vision event is intercepted before any brain sees it, so `turns` staying empty is
    itself part of the claim: no model call, no billing, no conversation history for a
    piece of paper.
    """

    name = "sil-cards"

    def __init__(self):
        self.turns = []

    def respond(self, turn):
        self.turns.append(turn)
        return Reply(text=f"You said: {turn.speech}")


def _pair(*, greet_after_s=300.0, app=None):
    """A real runtime and a real SIL robot on one in-process wire.

    `greet_after_s` is large by default so nothing in this file can accidentally be
    passing because of an unprompted hello: the only thing that may put an action in the
    robot's hands here is the card.
    """
    app = app or _App()
    rt, dev = make_runtime(app, device_id=DEV, nickname="Sam")
    rt.greet_after_s = greet_after_s
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    rt_side, robot_side = loopback(rt, vm)
    # The robot announces itself the way a real one does, so the runtime is answering a
    # registered device rather than a hand-placed dict entry.
    vm.client.publish(vm.t_state, json.dumps(
        {"software_version": FIRMWARE, "state": "config"}))
    return rt, vm, dev, app


def _scan(vm, rt, value, *, event=QR):
    """The robot holds a card up to its camera: publish the marker event carrying
    `value`, then drain the runtime's worker pool so the turn is settled."""
    event_id = vm.send_face_event(event, value=value)
    rt._pool.shutdown(wait=True)
    return event_id


def _published_event(vm):
    """The last `events/remote-chat` payload the ROBOT put on the wire, decoded."""
    for topic, payload in reversed(vm.client.published):
        if topic.endswith("/events/remote-chat"):
            return json.loads(payload)
    raise AssertionError(f"the robot published no vision event: {vm.client.published!r}")


def _wire_actions(vm):
    """The `RemoteChatAction`s naming a verb on the payload the ROBOT decoded. Filtered,
    because a reply may also carry the runtime's vision `event_subscription` — an entry
    with no `action` at all, which is not something a card did."""
    return [a for a in (vm.reply_payload or {}).get("response_actions", [])
            if a.get("action")]


# --------------------------------------------------------------------------- #
# 1. The card's value actually travels — the half `--face-value` adds
# --------------------------------------------------------------------------- #
def test_the_robot_publishes_the_scanned_string_in_the_recovered_input_var():
    """A card is only a card if the value reaches the server. The event name alone says
    "a QR code was seen"; `$eb_qr_value` is the part that says *which*."""
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, "GO<launch:DM>")
    msg = _published_event(vm)
    assert msg["speech"] == QR, msg
    assert msg["input_vars"] == {"$eb_qr_value": "GO<launch:DM>"}, msg
    assert msg["command"] == "prompt" and msg["backend"] == "router", msg


def test_each_marker_event_carries_its_own_value_key():
    assert VirtualMoxie.EVENT_VALUE_KEYS == dict(P.VALUE_KEYS), (
        "the SIL robot and the server disagree about where a marker payload rides")
    assert VirtualMoxie.value_vars("eb-found-face", "GO<launch:DM>") is None, \
        "a plain face event has no value slot and must not grow one"
    assert VirtualMoxie.value_vars(QR, "") is None, \
        "an empty value must publish the bare envelope, not an empty input_vars"


# --------------------------------------------------------------------------- #
# 2. T10 itself: the launch ends up in the robot's hands
# --------------------------------------------------------------------------- #
def test_a_scanned_card_leaves_the_robot_holding_the_launch():
    rt, vm, dev, app = _pair()
    _scan(vm, rt, "GO<launch:DM>")
    assert not vm.errors, vm.errors

    # (a) what the ROBOT is holding — its own client state, written by `_apply_action`
    stats = vm.action_stats()
    assert stats["launches"] == 1, stats
    assert stats["module_id"] == "DM", stats
    assert stats["last"] == "launch", stats
    assert stats["applied"] == [{"action": "launch", "module_id": "DM",
                                 "content_id": "", "function": "", "args": []}], stats
    assert vm.got_action.is_set(), "the robot never registered an action at all"

    # (b) and the wire it read that off, in the recovered shape
    entry, = _wire_actions(vm)
    assert entry["action"] == "launch" and entry["module_id"] == "DM", entry
    assert entry["output_type"] == "GLOBAL", entry
    assert vm.reply_payload["result"] == "SUCCESS", vm.reply_payload
    assert vm.reply_payload["event_id"] == _published_event(vm)["event_id"], \
        "the launch came back on some other turn than the scan"

    # (c) and no brain was ever asked for a piece of paper
    assert app.turns == [], app.turns


def test_a_card_with_a_content_id_reaches_the_robot_with_it():
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, cards.encode("DRAW", "default"))
    stats = vm.action_stats()
    assert (stats["module_id"], stats["content_id"]) == ("DRAW", "default"), stats


def test_the_robot_hears_nothing_when_it_scans_a_card():
    """The launch is a machine field. A child holding up paper must not be read a
    decoding artefact, so the reply that carries the action carries no words."""
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, "GO<launch:DM>")
    assert vm.reply_text == "", vm.reply_text
    assert (vm.reply_payload["output"] or {}).get("text", "") == "", vm.reply_payload
    assert vm.spoke is None, "the card was spoken out loud"
    assert vm.action_stats()["launches"] == 1, "…and it did not even launch"


def test_the_tag_is_read_case_insensitively_all_the_way_to_the_robot():
    """`<LAUNCH:DM>` is the same tag — the grammar normalises tag NAMES (and only those;
    the `GO` marker is literal, which the refusal list proves from the other side). Here
    so that the case rules are pinned in both directions on the wire, not just one."""
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, "GO<LAUNCH:DM>")
    assert vm.action_stats()["module_id"] == "DM", vm.action_stats()


@pytest.mark.parametrize("module_id", sorted(cards.LAUNCHABLE_MODULE_IDS))
def test_every_catalog_id_round_trips_all_the_way_to_the_robot(module_id):
    """The encoder's whole output, one card at a time, ending on the client. `encode` is
    what the printed sheet will use, so this is the paper→robot path minus the paper."""
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, cards.encode(module_id))
    assert vm.action_stats()["module_id"] == module_id, vm.action_stats()


# --------------------------------------------------------------------------- #
# 3. The refusals, on the same wire — the half that makes the section above mean
#    something. Each of these is a card a stranger could print.
# --------------------------------------------------------------------------- #
REFUSED = [
    ("launch_if_confirmed", "GO<launch_if_confirmed:DM>"),
    ("sleep", "GO<sleep>"),
    ("exit", "GO<exit>"),
    ("an id outside the catalog", "GO<launch:NOPE>"),
    ("a real id, no GO marker", "<launch:DM>"),
    ("a lowercased marker", "go<launch:DM>"),
    ("a launch smuggled after words", "GO please <launch:DM>"),
    ("two tags", "GO<launch:DM><launch:DRAW>"),
    ("more than a QR symbol can hold", "GO<launch:DM:" + "x" * 5000 + ">"),
]


@pytest.mark.parametrize("label,value", REFUSED, ids=[r[0] for r in REFUSED])
def test_a_refused_card_leaves_the_robot_holding_nothing_and_still_answers(label, value):
    rt, vm, dev, app = _pair()
    _scan(vm, rt, value)
    assert not vm.errors, vm.errors

    stats = vm.action_stats()
    assert stats["applied"] == [], f"{label}: the robot acted on a refused card: {stats}"
    assert stats["launches"] == 0 and stats["module_id"] == "", stats
    assert stats["asleep"] is False, f"{label}: a printed card put the robot to sleep"
    assert stats["exits"] == 0 and stats["qr_enabled"] is False, stats
    assert not vm.got_action.is_set(), stats
    assert _wire_actions(vm) == [], "an action reached the wire for a refused card"

    # The turn was ANSWERED. A refusal that hung would leave a real robot waiting on an
    # event_id forever — the contract requires a response to every subscribed event.
    assert vm.got_reply.is_set(), f"{label}: the robot was never answered"
    assert vm.reply_payload["result"] == "NOREPLY_ACK", vm.reply_payload
    assert vm.reply_payload["event_id"] == _published_event(vm)["event_id"], \
        vm.reply_payload
    assert vm.reply_text == "", vm.reply_text
    assert app.turns == [], f"{label}: a refused card was handed to a brain"


def test_the_confirm_variant_does_not_arrive_as_a_launch_by_another_name():
    """`<launch_if_confirmed:DM>` parses to `ActionType.LAUNCH` — the two tags are
    indistinguishable by the time the grammar is done, which is why the decoder gates on
    the tag NAME. On the wire that difference has to show up as the robot NOT being in
    DM, so this asserts the destination rather than the parse."""
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, "GO<launch_if_confirmed:DM>")
    assert vm.action_stats()["module_id"] != "DM", vm.action_stats()
    # …and the same string with the permitted tag does launch, so the refusal above is
    # about the tag and not about DM being unreachable.
    rt2, vm2, _, _ = _pair()
    _scan(vm2, rt2, "GO<launch:DM>")
    assert vm2.action_stats()["module_id"] == "DM", vm2.action_stats()


def test_a_card_value_on_a_different_marker_event_launches_nothing_at_the_robot():
    """Both other marker events reach the runtime in the identical envelope — an ArUco id
    and a book cover — and a value that happens to read as a card on one of those is
    still not a card. Asserted on the wire because the SIL robot is what chooses the
    `input_vars` key, so a client-side slip would fake this as convincingly as a
    server-side one."""
    for event in ("eb-dr-event", "eb-br-event"):
        rt, vm, dev, _ = _pair()
        _scan(vm, rt, "GO<launch:DM>", event=event)
        assert vm.action_stats()["applied"] == [], (event, vm.action_stats())
        assert vm.reply_payload["result"] == "NOREPLY_ACK", (event, vm.reply_payload)
        # the value did travel — it just travelled as what it is
        assert _published_event(vm)["input_vars"] == {
            P.VALUE_KEYS[event]: "GO<launch:DM>"}, event


def test_a_qr_value_smuggled_onto_another_marker_event_launches_nothing():
    """The hostile shape the test above cannot reach: the value under the **QR key** on
    an event that is not the QR one. Nothing stops a turn carrying several `input_vars`,
    so "which key was set" must not be what decides — the EVENT NAME is. Sent by hand
    (`input_vars=`) precisely because `value_vars` would never build this envelope, which
    is what makes it a test of the server rather than of the robot's own key routing."""
    rt, vm, dev, _ = _pair()
    vm.send_face_event("eb-dr-event", input_vars={"$eb_dr_value": "GO<launch:DM>",
                                                  "$eb_qr_value": "GO<launch:DM>"})
    rt._pool.shutdown(wait=True)
    assert vm.action_stats()["applied"] == [], vm.action_stats()
    assert vm.reply_payload["result"] == "NOREPLY_ACK", vm.reply_payload


def test_a_refused_card_does_not_disarm_the_next_real_one():
    """One wire, two scans. A refusal must be inert, not sticky: the robot that just
    ignored a stranger's `<sleep>` card still launches the parent's next real one."""
    rt, vm, dev, _ = _pair()
    _scan(vm, rt, "GO<sleep>")
    assert vm.action_stats()["applied"] == []
    vm._reset_turn()
    _scan(vm, rt, "GO<launch:DM>")
    stats = vm.action_stats()
    assert stats["launches"] == 1 and stats["module_id"] == "DM", stats
    assert stats["asleep"] is False, stats


# --------------------------------------------------------------------------- #
# 4. The CLI a person actually types
# --------------------------------------------------------------------------- #
def test_run_face_events_carries_the_face_value_and_records_what_arrived():
    """`--face-event eb-qr-event --face-value 'GO<launch:DM>'` end to end.

    `run_face_events` owns the paho lifecycle (connect / loop_start / SUBACK / disconnect)
    and the loopback has no broker to provide it, so those four calls are stubbed and the
    SUBACK is seeded. Everything the protocol consists of — the `/state` announce, the
    config push, the event publish, the reply — still goes over the real loopback and
    through the real runtime.
    """
    rt, vm, dev, _ = _pair()
    side = vm.client
    for name in ("connect", "loop_start", "loop_stop", "disconnect"):
        setattr(side, name, lambda *a, **k: None)
    vm.subscribed.set()          # the one thing only a broker can send

    ok = vm.run_face_events([QR], value="GO<launch:DM>")
    rt._pool.shutdown(wait=True)
    assert ok, vm.errors

    row, = vm.face_replies
    assert row["kind"] == QR and row["result"] == "SUCCESS", row
    assert row["text"] == "", row
    assert row["actions"] == [{"action": "launch", "module_id": "DM",
                               "content_id": "", "function": "", "args": []}], row
    assert vm.action_stats()["module_id"] == "DM", vm.action_stats()


def test_the_cli_exposes_face_value():
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(REPO, "sim", "virtual_moxie.py"),
                          "--help"], capture_output=True, text=True).stdout
    assert "--face-value" in out, out
