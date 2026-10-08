"""
Does the ROBOT act on `response_actions`? — beyond what it was handed.

`test_action_tags.py` reads `response_actions` off a recording client; here the REAL runtime
and the SIL robot (`sim/virtual_moxie.py`) share `helpers_runtime.loopback()`, and the robot
must both RECEIVE the recovered shape and ACT on it, as `sim/web/bridge/actions.js::applyAction`
does (DoD criterion 4, interchangeable clients):

1. A real turn: the real `MoxieRuntime` + `LLMApp` (canned completion via `client=`)
   answers "can we draw?" with `<launch:DRAW:default>`, and the ROBOT'S OWN STATE is then
   in DRAW.
2. Both clients agree: `goldens/cloud_to_robot_actions.json` holds the four responses
   `sim/test_bridge.mjs` drives the browser SIM with and the state it reaches; the SIL
   robot must land in the same place, key for key.

Hermetic and instant: no broker, network, gateway, node or sleeps.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))

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
    """One whole turn, robot-first, through shipped code on both ends. Returns the
    `VirtualMoxie`: `vm.action_stats()` is what it did, `vm.reply_payload` what it got."""
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
    # …and it is the SAME action the wire carried, in the recovered shape.
    on_wire = next(a for a in vm.reply_payload["response_actions"] if a.get("action"))
    assert on_wire == {"output_type": "GLOBAL_RESPONSE", "action": "launch", "module_id": "DRAW",
                       "content_id": "default"}, on_wire
    assert acted["applied"][-1]["action"] == "launch"
    # The child never hears the tag: asserted on the ROBOT's copy, the text it reads out.
    output = vm.reply_payload["output"]
    assert output["text"] == vm.reply_text == "Yes! Let's draw.", output
    assert "<launch" not in output["markup"], output


def test_an_exit_tag_on_a_real_turn_arrives_as_the_only_action():
    vm = _real_turn('{"say": "Bye Sam! <exit>", "mood": "positive", "gesture": "talk"}',
                    speech="bye moxie")
    actions = [a["action"] for a in vm.reply_payload.get("response_actions", [])
               if a.get("action")]
    assert actions == ["exit_module"], vm.reply_payload     # the ActionID name
    assert vm.reply_payload["output"]["text"] == "Bye Sam!"
    assert vm.action_stats()["exits"] == 1


def test_an_exit_on_a_real_turn_takes_the_robot_back_out():
    """Sequenced against a launch on the same client: a robot that only ever counted
    would pass a lone exit, so the assertion is that it is *out of the module it was in*."""
    vm = _real_turn('{"say": "Yes! Let\'s draw. <launch:DRAW:default>", '
                    '"mood": "positive", "gesture": "celebrate"}')
    assert vm.action_stats()["module_id"] == "DRAW"
    # a second reply on the same client, exactly as a second turn would deliver it
    vm._on_chat_reply({"command": "remote_chat", "result": 0, "event_id": "e2",
                       "output": {"text": "Bye!"},
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE",
                                             "action": "exit_module"}]})
    acted = vm.action_stats()
    assert acted["exits"] == 1 and acted["module_id"] == "" and acted["content_id"] == ""
    assert acted["last"] == "exit_module", acted


def test_an_untagged_reply_leaves_the_robot_where_it_was():
    """The negative case, so nothing above can pass by accident."""
    vm = _real_turn('{"say": "Tell me about it!", "mood": "positive", '
                    '"gesture": "question"}', speech="hi moxie")
    acted = vm.action_stats()
    assert acted["applied"] == [], acted
    assert (acted["launches"], acted["exits"], acted["unknown"]) == (0, 0, 0), acted
    assert acted["module_id"] == "" and acted["last"] == "", acted


def test_actions_from_an_external_brain_reach_the_robot_too():
    """`WebhookApp` lets a service declare `actions` outright rather than writing tags —
    the same wire and robot, so the action path is not an LLM-only feature."""
    from moxie_sdk.apps import WebhookApp

    class _Webhook(WebhookApp):
        """The real app with its one network call stubbed."""

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
    assert [(a["action"], a["module_id"], a["content_id"]) for a in ra] == [
        ("launch", "GAME", "level1")], "the bogus action type should have been dropped"
    assert vm.reply_payload["output"]["text"] == "Let's play a game!"
    assert vm.action_stats()["module_id"] == "GAME"


def test_a_webhooks_older_exit_spelling_leaves_the_module_as_exit_module():
    """`{"type": "exit"}` is the alias the webhook contract documents (webhook_app.py:11-12);
    `ActionType._missing_` reads it, the wire carries `exit_module` (RemoteChat.proto:260)
    and the robot leaves the module it was in."""
    from moxie_sdk.apps import WebhookApp

    class _Webhook(WebhookApp):
        def _post(self, path_hint, body):
            return {"text": "Bye Sam!", "actions": [{"type": "exit"}]}

    rt, dev = make_runtime(_Webhook("http://127.0.0.1:1/turn"), device_id=DEV)
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    loopback(rt, vm)
    vm._on_chat_reply({"command": "remote_chat", "result": 0, "event_id": "e0",
                       "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE",
                                             "action": "launch", "module_id": "DM"}]})
    vm.client.publish(vm.t_event("remote-chat"), json.dumps(
        {"event_id": "evt-webhook-exit", "command": "prompt", "backend": "router",
         "speech": "bye"}))
    rt._pool.shutdown(wait=True)
    ra = [a["action"] for a in vm.reply_payload.get("response_actions", []) if a.get("action")]
    assert ra == ["exit_module"], vm.reply_payload
    assert vm.action_stats()["exits"] == 1 and vm.action_stats()["module_id"] == ""


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
    entry = {"output_type": "GLOBAL_RESPONSE", "action": "launch", "module_id": "DM"}
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_action": entry, "response_actions": [entry]})
    assert vm.action_stats()["launches"] == 1, vm.action_stats()


def test_the_singular_alone_is_still_read():
    """…and the mirror is not simply ignored: a response that carries ONLY the legacy
    singular still moves the robot."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_legacy", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_action": {"output_type": "GLOBAL_RESPONSE", "action": "launch",
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
                           {"output_type": "GLOBAL_RESPONSE", "action": "execute",
                            "function_id": "eb_enable_qr", "function_args": ["true"]}]})
    applied = vm.action_stats()["applied"]
    assert applied == [{"action": "execute", "module_id": "", "content_id": "",
                        "function": "eb_enable_qr", "args": ["true"]}], applied
    assert sent == [], f"an execute must not make this client publish anything: {sent}"


def test_execute_reads_the_sims_spelling_too():
    """`RemoteChat.proto`:255-281 names the field `function_id`, and that is what our own
    `build_chat_response` now emits; `sim/web/bridge/actions.js::applyAction` read `entry.function`. Both
    spellings stay accepted — a client that only understood the one server it was written
    against would not be a client — and an unnamed execute records `""`, not a guess."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_exec2", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [
                           {"output_type": "GLOBAL_RESPONSE", "action": "execute",
                            "function": "eb_enable_qr"},
                           {"output_type": "GLOBAL_RESPONSE", "action": "execute"}]})
    assert [a["function"] for a in vm.action_stats()["applied"]] == ["eb_enable_qr", ""]


def test_what_our_own_server_sends_now_names_the_function_it_wants_run():
    """An `execute` reaches the robot NAMED: `wire.encode_action` emits `function_id`
    (RemoteChat.proto:271, field 7) and, for a dict, `action_args` (field 10). Built as our
    server would send it, handed to the SIL robot, and the robot is asked what to run.
    """
    from moxie_sdk.types import Action, ActionType
    from moxie_sdk.wire import build_chat_response
    resp = build_chat_response("e", "hi", actions=[
        Action(type=ActionType.EXECUTE, function="eb_enable_qr", args={"run": True})])
    entry = resp["response_actions"][0]
    assert entry["function_id"] == "eb_enable_qr", entry
    assert entry["action_args"] == [{"key": "run", "value": "true"}], entry
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_exec3", verbose=False)
    vm._on_chat_reply(resp)
    applied = vm.action_stats()["applied"][0]
    assert applied["function"] == "eb_enable_qr", vm.action_stats()
    assert applied["args"] == {"run": "true"}, vm.action_stats()


def test_the_briefs_own_worked_example_is_the_shape_that_goes_out():
    """qr-launch-cards.md §P0-a / §4 T9's exact JSON, key for key:
    `{"output_type": "GLOBAL_RESPONSE", "action": "execute", "function_id": "eb_enable_qr",
    "function_args": ["true"]}` — a list of args is `function_args` (field 8)."""
    from moxie_sdk.types import Action, ActionType
    from moxie_sdk.wire import build_chat_response
    resp = build_chat_response("e", "hi", actions=[
        Action(type=ActionType.EXECUTE, function="eb_enable_qr", args=["true"])])
    assert resp["response_actions"] == [
        {"output_type": "GLOBAL_RESPONSE", "action": "execute",
         "function_id": "eb_enable_qr", "function_args": ["true"]}]
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_exec4", verbose=False)
    vm._on_chat_reply(resp)
    assert vm.action_stats()["applied"][0]["args"] == ["true"], vm.action_stats()


def test_an_action_with_no_function_gains_no_empty_keys():
    """The other half of "emit only when present": a launch must serialise exactly as it
    did before this landed, or every golden holding a plain response moves for free."""
    from moxie_sdk.types import Action, ActionType
    from moxie_sdk.wire import build_chat_response
    for action in (Action(type=ActionType.LAUNCH, module_id="DRAW", content_id="default"),
                   Action(type=ActionType.EXIT),
                   Action(type=ActionType.EXECUTE, function="eb_wake"),   # named, no args
                   Action(type=ActionType.EXECUTE, args=[])):             # args, but empty
        entry = build_chat_response("e", "hi", actions=[action])["response_actions"][0]
        assert "function_args" not in entry and "action_args" not in entry, entry
        if not action.function:
            assert "function_id" not in entry, entry
    plain = build_chat_response("e", "hi", actions=[
        Action(type=ActionType.LAUNCH, module_id="DRAW", content_id="default")])
    assert plain["response_actions"] == [{"output_type": "GLOBAL_RESPONSE", "action": "launch",
                                          "module_id": "DRAW", "content_id": "default"}]


def test_arg_values_go_out_as_the_strings_the_proto_declares():
    """`function_args` is `repeated string` and `ActionArgsEntry.value` is a `string`
    (RemoteChat.proto:271-273,:280), so a caller's `True`/`3` cannot ride as JSON types.
    Booleans go out lowercase — the brief's own `["true"]`, and JSON's spelling, never
    Python's `"True"`."""
    from moxie_sdk.types import Action, ActionType
    from moxie_sdk.wire import build_chat_response

    def entry(**kw):
        return build_chat_response("e", "hi", actions=[
            Action(type=ActionType.EXECUTE, function="f", **kw)])["response_actions"][0]

    assert entry(args=[True, False, 3, "x"])["function_args"] == ["true", "false", "3", "x"]
    assert entry(args={"run": True, "n": 3})["action_args"] == [
        {"key": "run", "value": "true"}, {"key": "n", "value": "3"}]
    # a lone scalar is ONE argument, not one per character — the trap a bare `list(args)`
    # would walk into on a string.
    assert entry(args="true")["function_args"] == ["true"]


def test_the_two_naming_defects_p0a_owned_are_fixed_on_the_wire():
    """Until 2026-10-08 this test pinned two wrong wire spellings (qr-launch-cards.md
    §P0-a / §7 R3); now it holds the fix. Neither `exit` nor `enable_qr` is a name in the
    recovered `ActionID` enum (RemoteChat.proto:256-266), so:

      * `ActionType.EXIT` goes out as `exit_module` (:260);
      * `ActionType.ENABLE_QR` goes out as the contract's `execute` (:263) +
        `function_id: "eb_enable_qr"`, `function_args: ["true"]` — the §P0-a shape.

    Both clients still accept the older spellings (`test_sim_client_parity.py`), and the
    SIL robot reads the execute by name exactly as it reads any other."""
    from moxie_sdk.types import Action, ActionType
    from moxie_sdk.wire import build_chat_response
    resp = build_chat_response("e", "hi", actions=[Action(type=ActionType.ENABLE_QR),
                                                   Action(type=ActionType.EXIT)])
    assert [a["action"] for a in resp["response_actions"]] == ["execute", "exit_module"]
    assert resp["response_actions"][0] == {
        "output_type": "GLOBAL_RESPONSE", "action": "execute",
        "function_id": "eb_enable_qr", "function_args": ["true"]}
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_qr", verbose=False)
    vm._on_chat_reply(resp)
    applied = vm.action_stats()["applied"]
    assert [a["action"] for a in applied] == ["execute", "exit_module"], applied
    assert applied[0]["function"] == "eb_enable_qr" and applied[0]["args"] == ["true"]
    assert vm.action_stats()["exits"] == 1 and vm.action_stats()["unknown"] == 0


def test_sleep_is_recorded_and_does_not_stop_the_client():
    """No wake handshake is recovered, so a SIL robot that shut itself down on `sleep`
    would be inventing the contract's other half."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_sleep", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE", "action": "sleep"}]})
    assert vm.action_stats()["asleep"] is True
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e2", "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE", "action": "launch",
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
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE", "action": "launch",
                                             "module_id": "DM", "content_id": "c"}]})
    vm._reset_turn()
    assert vm.action_stats()["module_id"] == "DM", vm.action_stats()
    assert vm.action_stats()["launches"] == 1
    assert not vm.got_action.is_set(), "the per-turn edge must be cleared"


def test_an_action_on_a_streamed_chunk_is_not_lost():
    """A streamed answer is several publishes; an action may ride any of them, including
    a `REPLY_PENDING` chunk that never becomes `reply_payload`."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_stream", verbose=False)
    vm._on_chat_reply({"command": "remote_chat", "result": 9, "chunk_num": 0,
                       "event_id": "s", "output": {"text": "One moment."},
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE", "action": "launch",
                                             "module_id": "DM"}]})
    vm._on_chat_reply({"command": "remote_chat", "result": 0, "chunk_num": 1,
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


def test_no_shape_of_the_new_arg_fields_can_break_a_turn_either():
    """The same "never raise" rule, applied to the two fields this slice taught the wire.
    `action_args` is `repeated ActionArgsEntry{key, value}`; anything else on that key is
    unreadable, and unreadable must fall through to the next spelling and record nothing —
    never a partial guess, never an exception that drops the whole turn."""
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_args", verbose=False)
    for entry, want in (
            ({"action": "execute", "action_args": "nope"}, []),
            ({"action": "execute", "action_args": []}, []),
            ({"action": "execute", "action_args": ["nope", 7, None]}, []),
            ({"action": "execute", "action_args": [{"value": "orphan"}]}, []),
            ({"action": "execute", "function_args": 7}, 7),
            ({"action": "execute", "action_args": [{"key": "a", "value": "1"},
                                                   "junk"]}, {"a": "1"})):
        vm._on_chat_reply({"command": "remote_chat", "event_id": "a",
                           "output": {"text": ""},
                           "response_actions": [dict(entry, output_type="GLOBAL_RESPONSE")]})
        assert vm.action_stats()["applied"][-1]["args"] == want, entry
    assert vm.action_stats()["unknown"] == 0, "none of these is junk to be counted"


def test_the_applied_log_is_bounded_like_the_browser_sims():
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_bound", verbose=False)
    for i in range(60):
        vm._on_chat_reply({"command": "remote_chat", "event_id": f"e{i}",
                           "output": {"text": ""},
                           "response_actions": [{"output_type": "GLOBAL_RESPONSE",
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
                           "response_actions": [{"output_type": "GLOBAL_RESPONSE",
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
