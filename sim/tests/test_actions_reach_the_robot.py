"""
Does the ROBOT act on `response_actions`? — the half `test_e2e_actions_to_robot.py`
deliberately did not claim.

That file drives the real runtime and the real SIL robot over a loopback and asserts what
the robot was *handed*: it reads `payload["response_actions"]` off the wire. It says so in
its own docstring, because until 2026-09-03 there was nothing else to assert —
`sim/virtual_moxie.py` ignored the field entirely (`grep -c response_actions
sim/virtual_moxie.py` returned **0**) while `sim/web/bridge.js::applyAction` had acted on
it since PR #52. So the SIM client every SIL test, the smoke, the scenarios and the soak
drive on could not show that a launch DID anything, and DoD criterion 4
("interchangeable clients") was carrying one untrue clause.

This file asserts the other half, in two ways:

1. **A real turn.** The real `MoxieRuntime` with a real `LLMApp` (canned completion, the
   `client=` seam — no network) answers `"can we draw?"` with a `<launch:DRAW:default>`
   tag, and the assertion is on the ROBOT'S OWN STATE afterwards: it is *in* DRAW. Not
   that a payload contained a launch — that the client that received it launched.

2. **Both clients agree.** `sim/tests/goldens/cloud_to_robot_actions.json` holds the four
   responses `sim/test_bridge.mjs` emits at the browser SIM and the state that file
   already asserts the browser reached; the SIL robot is driven over the same four and
   must land in the same place, key for key.

Hermetic and instant: no broker, no network, no gateway, no node, no sleeps.
"""
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
from virtual_moxie import FIRMWARE, VirtualMoxie                 # noqa: E402

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "goldens",
                           "cloud_to_robot_actions.json")
with open(GOLDEN_PATH) as _fh:
    GOLDEN = json.load(_fh)

DEV = "d_acts_on_it"


class _CannedCompletion:
    """The OpenAI client's shape with one canned assistant message — the `client=` seam,
    so the real `LLMApp` (persona, JSON contract, tag parsing) runs with no network."""

    def __init__(self, content):
        self._content = content
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        msg = type("M", (), {"content": self._content})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def _real_turn(canned, speech="can we draw?"):
    """One whole turn, robot-first, through the shipped code on both ends.

    Returns the `VirtualMoxie` that lived it — `vm.action_stats()` is what the ROBOT did,
    `vm.reply_payload` is what it was handed.
    """
    from moxie_sdk.apps import LLMApp
    app = LLMApp(base_url="http://127.0.0.1:1/v1", api_key="not-used", model="test",
                 client=_CannedCompletion(canned))
    rt, dev = make_runtime(app, device_id=DEV, nickname="Sam")
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    loopback(rt, vm)
    vm.client.publish(vm.t_state, json.dumps(
        {"software_version": FIRMWARE, "state": "config"}))
    vm.client.publish(vm.t_event("remote-chat"), json.dumps(
        {"event_id": "evt-acts", "command": "prompt", "backend": "router",
         "speech": speech}))
    rt._pool.shutdown(wait=True)
    return vm


# --------------------------------------------------------------------------- #
# 1. A real turn: the robot does not merely RECEIVE the launch, it takes it
# --------------------------------------------------------------------------- #
def test_a_launch_on_a_real_turn_puts_the_robot_in_the_module():
    vm = _real_turn('{"say": "Yes! Let\'s draw. <launch:DRAW:default>", '
                    '"mood": "positive", "gesture": "celebrate"}')
    assert not vm.errors, vm.errors
    assert vm.reply_payload, "the robot received no remote_chat reply at all"

    acted = vm.action_stats()
    assert acted["launches"] == 1, f"the robot did not launch anything: {acted}"
    assert acted["module_id"] == "DRAW", acted
    assert acted["content_id"] == "default", acted
    assert acted["last"] == "launch", acted
    assert acted["unknown"] == 0, f"the robot did not understand its own reply: {acted}"
    # …and it is the SAME action the wire carried, not a coincidence.
    on_wire = next(a for a in vm.reply_payload["response_actions"] if a.get("action"))
    assert (on_wire["action"], on_wire["module_id"], on_wire["content_id"]) == (
        "launch", "DRAW", "default")
    assert acted["applied"][-1]["action"] == "launch"


def test_an_exit_on_a_real_turn_takes_the_robot_back_out():
    """Sequenced against a launch on the same client: a robot that only ever counted
    would pass a lone exit, so the assertion is that it is *out of the module it was in*."""
    vm = _real_turn('{"say": "Yes! Let\'s draw. <launch:DRAW:default>", '
                    '"mood": "positive", "gesture": "celebrate"}')
    assert vm.action_stats()["module_id"] == "DRAW"
    # a second reply on the same client, exactly as a second turn would deliver it
    vm._on_chat_reply({"command": "remote_chat", "result": "SUCCESS", "event_id": "e2",
                       "output": {"text": "Bye!"},
                       "response_actions": [{"output_type": "GLOBAL", "action": "exit"}]})
    acted = vm.action_stats()
    assert acted["exits"] == 1 and acted["module_id"] == "" and acted["content_id"] == ""
    assert acted["last"] == "exit", acted


def test_an_untagged_reply_leaves_the_robot_where_it_was():
    """The negative case, so nothing above can pass by accident."""
    vm = _real_turn('{"say": "Tell me about it!", "mood": "positive", '
                    '"gesture": "question"}', speech="hi moxie")
    acted = vm.action_stats()
    assert acted["applied"] == [], acted
    assert (acted["launches"], acted["exits"], acted["unknown"]) == (0, 0, 0), acted
    assert acted["module_id"] == "" and acted["last"] == "", acted


def test_the_robot_records_the_event_subscription_the_brain_asked_for():
    """`RemoteChatAction.EventSubscription` rides an action-LESS entry, which is the one
    shape a naive reader would treat as an error. The runtime subscribes every robot it
    answers, so a real turn already carries one."""
    vm = _real_turn('{"say": "Hello!", "mood": "positive", "gesture": "talk"}',
                    speech="hi")
    subscribed = vm.action_stats()["subscribed"]
    assert "eb-found-face" in subscribed, subscribed
    assert vm.action_stats()["unknown"] == 0, "the subscription entry was read as junk"


# --------------------------------------------------------------------------- #
# 2. The two clients agree — the golden, held from both ends
# --------------------------------------------------------------------------- #
def _drive_golden_script():
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_golden", verbose=False)
    for response in GOLDEN["script"]:
        vm._on_chat_reply({k: v for k, v in response.items() if k != "_why"})
    return vm


def test_the_sil_robot_ends_where_the_browser_sim_ends():
    """The same four responses `sim/test_bridge.mjs` emits at the browser SIM, and the
    state that file asserts the browser reached."""
    got = _drive_golden_script().action_stats()
    want = GOLDEN["expected_state"]
    shared = GOLDEN["applied_keys"]
    got_applied = [{k: a[k] for k in shared} for a in got["applied"]]
    assert got_applied == want["applied"], (got_applied, want["applied"])
    for key in GOLDEN["stat_keys"]:
        if key == "applied":
            continue
        assert got[key] == want[key], f"{key}: robot {got[key]!r} != golden {want[key]!r}"


def test_an_unknown_verb_is_counted_and_skipped_rather_than_raised():
    """A future server verb must not be able to break an old client's turn — and the
    unknown verb must never reach the robot's state."""
    acted = _drive_golden_script().action_stats()
    assert acted["unknown"] == 2, acted          # the bogus verb AND the junk entry
    assert all(a["action"] != "teleport_to_mars" for a in acted["applied"]), acted


def test_the_legacy_singular_never_fires_the_same_action_twice():
    """`response_action` mirrors `response_actions[0]`, so a client that read both would
    launch twice (mqtt-and-conversation.md §4.1). Golden entry act-2 carries both."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_dup", verbose=False)
    entry = {"output_type": "GLOBAL", "action": "launch", "module_id": "DM"}
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_action": entry, "response_actions": [entry]})
    assert vm.action_stats()["launches"] == 1, vm.action_stats()


def test_the_singular_alone_is_still_read():
    """…and the mirror is not simply ignored: a response that carries ONLY the legacy
    singular still moves the robot."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_legacy", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_action": {"output_type": "GLOBAL", "action": "launch",
                                           "module_id": "DM"}})
    assert vm.action_stats()["module_id"] == "DM", vm.action_stats()


# --------------------------------------------------------------------------- #
# 3. What it deliberately does NOT do
# --------------------------------------------------------------------------- #
def test_an_execute_is_recorded_by_name_and_never_run():
    """The contract's `execute` runs a robot-side `function_id(function_args…)` and
    returns the result next turn in `execute_returns[]`. This client has no such function
    and does not invent one: it records the name and sends no `execute_returns`."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_exec", verbose=False)
    sent = []
    vm.client = type("C", (), {"publish": lambda _s, t, p: sent.append((t, p))})()
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [
                           {"output_type": "GLOBAL", "action": "execute",
                            "function_id": "eb_enable_qr", "function_args": ["true"]}]})
    applied = vm.action_stats()["applied"]
    assert applied == [{"action": "execute", "module_id": "", "content_id": "",
                        "function": "eb_enable_qr", "args": ["true"]}], applied
    assert sent == [], f"an execute must not make this client publish anything: {sent}"


def test_execute_reads_the_sims_spelling_too():
    """`RemoteChat.proto`:255-281 names the field `function_id`; `sim/web/bridge.js`:258
    reads `entry.function`. Our own `build_chat_response` emits NEITHER today, so both
    spellings are accepted and an unnamed execute records `""` rather than a guess."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_exec2", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [
                           {"output_type": "GLOBAL", "action": "execute",
                            "function": "eb_enable_qr"},
                           {"output_type": "GLOBAL", "action": "execute"}]})
    assert [a["function"] for a in vm.action_stats()["applied"]] == ["eb_enable_qr", ""]


def test_what_our_own_server_can_actually_send_carries_no_function_at_all():
    """Named so it cannot be mistaken for a passing feature: `build_chat_response` drops
    `Action.function`/`Action.args`, so every `execute` this appliance can emit reaches a
    robot unnamed. Filed against the wire (qr-launch-cards.md §P0-a), not faked here."""
    from moxie_sdk.types import Action, ActionType
    from moxie_sdk.wire import build_chat_response
    resp = build_chat_response("e", "hi", actions=[
        Action(type=ActionType.EXECUTE, function="eb_enable_qr", args={"run": True})])
    entry = resp["response_actions"][0]
    assert "function_id" not in entry and "function" not in entry, entry
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_exec3", verbose=False)
    vm._on_chat_reply(resp)
    assert vm.action_stats()["applied"][0]["function"] == "", vm.action_stats()


def test_sleep_is_recorded_and_does_not_stop_the_client():
    """No wake handshake is recovered, so a SIL robot that shut itself down on `sleep`
    would be inventing the contract's other half."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_sleep", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL", "action": "sleep"}]})
    assert vm.action_stats()["asleep"] is True
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e2", "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL", "action": "launch",
                                             "module_id": "DM"}]})
    assert vm.action_stats()["asleep"] is False, "a launch wakes the client, as on the SIM"


# --------------------------------------------------------------------------- #
# 4. Lifetime + robustness
# --------------------------------------------------------------------------- #
def test_action_state_outlives_the_turn_that_set_it():
    """`_reset_turn` clears the per-turn edge, not the navigation state: the module the
    cloud put us in is still the module we are in when the next prompt goes out. The
    browser SIM's `actionState` has the same lifetime."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_life", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL", "action": "launch",
                                             "module_id": "DM", "content_id": "c"}]})
    vm._reset_turn()
    assert vm.action_stats()["module_id"] == "DM", vm.action_stats()
    assert vm.action_stats()["launches"] == 1
    assert not vm.got_action.is_set(), "the per-turn edge must be cleared"


def test_an_action_on_a_streamed_chunk_is_not_lost():
    """A streamed answer is several publishes; an action may ride any of them, including
    a `REPLY_PENDING` chunk that never becomes `reply_payload`."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_stream", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "result": "REPLY_PENDING", "chunk_num": 0,
                       "event_id": "s", "output": {"text": "One moment."},
                       "response_actions": [{"output_type": "GLOBAL", "action": "launch",
                                             "module_id": "DM"}]})
    vm._on_chat_reply({"command": "remote_chat", "result": "SUCCESS", "chunk_num": 1,
                       "event_id": "s", "output": {"text": "Here we go!"},
                       "consistency_control": {"is_completed": True}})
    assert vm.action_stats()["module_id"] == "DM", vm.action_stats()
    assert "launch" not in json.dumps(vm.reply_payload), "the action rode chunk 0"


def test_nothing_an_action_can_carry_makes_the_client_raise():
    """Junk of every shape the wire can hold. A client that throws here drops the turn."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_junk", verbose=False)
    for actions in ([None], ["string"], [42], [{}], [{"action": None}], [{"action": ""}],
                    [{"action": "launch", "module_id": None}], "not-a-list", 7, None,
                    [{"action": "launch", "event_subscription": "nope"}],
                    [{"event_subscription": {"clear": True, "active": None}}]):
        vm._on_chat_reply({"command": "remote_chat", "event_id": "j",
                           "output": {"text": ""}, "response_actions": actions})
    assert vm.action_stats()["last"] == "launch"          # the two valid ones landed
    assert vm.action_stats()["launches"] == 2


def test_the_applied_log_is_bounded_like_the_browser_sims():
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_bound", verbose=False)
    for i in range(60):
        vm._on_chat_reply({"command": "remote_chat", "event_id": f"e{i}",
                           "output": {"text": ""},
                           "response_actions": [{"output_type": "GLOBAL",
                                                 "action": "launch",
                                                 "module_id": f"M{i}"}]})
    stats = vm.action_stats()
    assert len(stats["applied"]) == 40, len(stats["applied"])
    assert stats["applied"][-1]["module_id"] == "M59"
    assert stats["launches"] == 60, "the counter is not bounded, only the log"


def test_a_clearing_subscription_replaces_rather_than_appends():
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_sub", verbose=False)
    def sub(active, clear):
        vm._on_chat_reply({"command": "remote_chat", "event_id": "e",
                           "output": {"text": ""},
                           "response_actions": [{"output_type": "GLOBAL",
                                                 "event_subscription": {
                                                     "active": active, "clear": clear}}]})
    sub(["eb-found-face", "eb-lost-target"], False)
    sub(["eb-found-face"], False)                 # dedup, not a second copy
    assert vm.action_stats()["subscribed"] == ["eb-found-face", "eb-lost-target"]
    sub(["eb-qr-event"], True)
    assert vm.action_stats()["subscribed"] == ["eb-qr-event"]


def test_the_client_implements_every_verb_the_golden_names():
    # Imported HERE, not at module scope, on purpose: against a `virtual_moxie.py` that
    # does not act on actions at all this file must still collect, so the failures read
    # as "the robot did not launch" rather than one import error hiding every claim.
    from virtual_moxie import ACTION_KINDS
    assert list(ACTION_KINDS) == GOLDEN["action_kinds"]
