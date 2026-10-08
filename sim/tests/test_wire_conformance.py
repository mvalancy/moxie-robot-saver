"""
Can a ROBOT that parses our JSON as protobuf read what we publish? — wire conformance.

A Moxie decodes `commands/remote_chat` as `embodied.robotbrain.RemoteChatResponse`
(RemoteChat.proto:306-350). Until 2026-10-08 every reply this runtime published failed
that parse: `result` carried the enum NAME where the proto declares `uint32 result = 2`
(:320) — `ParseDict` raised `invalid literal for int(): 'SUCCESS'` — and, under a parser
that skips what it does not know, `output_type: "GLOBAL"` and `action: "exit"` silently
became 0 (CATCH_ALL / UNSET_ACTION_ID); the module query compared `rcr["query"]` to a
string where the robot sends `RemoteDataQuery{query: modules}` (:41-51, :79); the answer
sat in a top-level `modules` the proto lacks instead of `query_data` (:296-300, :339);
and `output.signals` was a list where the proto has a `RemoteSignals` message (:176).

The oracle is the committed pb2 (`tools/robot-toolkit/moxie_toolkit/embodied/robotbrain/
RemoteChat_pb2.py`) through `google.protobuf.json_format.ParseDict` with
`ignore_unknown_fields=False`: every field name, value type and enum name must be one
the proto knows. The ONLY fields excepted are `wire.NON_PROTO_FIELDS`: `command`, which
OpenMoxie (MIT, field-proven on real robots) also sends on every response (volley.py
`create_response`; moxie_server.py:176), and `end_turn`, ours alone (an SDK hint with no
proto home, kept for the Sim and webhook contracts; a robot skips it as it skips
`command`).

Field reference, cited not copied: OpenMoxie volley.py `create_response` (result 0,
`GLOBAL_RESPONSE`), `add_launch_or_exit` (`exit_module`), moxie_server.py:170-176
(`rcr['query']['query']`, `query_data`), moxie_remote_chat.py:73-77 (module info).

Hermetic: the real `MoxieRuntime` over fake transports; no broker, network or brain.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))
# APPENDED, not inserted: the pb2 package root also holds a `markup.py` that would shadow
# the supervisor's `markup` for every other module collected in the same session.
sys.path.append(os.path.join(REPO, "tools", "robot-toolkit", "moxie_toolkit"))

pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")

from google.protobuf import json_format                                   # noqa: E402
from embodied.robotbrain import RemoteChat_pb2 as RC                      # noqa: E402
from embodied.robotbrain import ChatResponse_pb2 as CR                    # noqa: E402
from embodied.robotbrain import ContentModule_pb2 as CM                   # noqa: E402

from helpers_runtime import drive_turn, loopback, make_runtime            # noqa: E402
from moxie_sdk.app import MoxieApp                                        # noqa: E402
from moxie_sdk.types import (ACTION_IDS, Action, ActionType, Reply,       # noqa: E402
                             ReplyChunk, ResultCode)
from moxie_sdk.wire import (NON_PROTO_FIELDS, build_chat_response,        # noqa: E402
                            build_remote_modules, is_module_query)

PROTO_FIELDS = {f.name for f in RC.RemoteChatResponse.DESCRIPTOR.fields}
SHIPPED = ("starter.json", "memory_chat.json")          # mqtt/content_modules/
SHIPPED_IDS = {"FREE_CHAT": ["default"], "MEMORY_CHAT": ["default", "aboutme"]}
PROTO_QUERY = {"query": {"query": "modules"}}            # RemoteDataQuery{query: modules}


def strict(resp: dict) -> RC.RemoteChatResponse:
    """Parse one published `remote_chat` payload as the robot would, strictly: no unknown
    field, type or enum name survives — except exactly `NON_PROTO_FIELDS`, removed here
    and nowhere else, so an unlisted extra field is a failure, not a silent skip."""
    extra = set(resp) - PROTO_FIELDS
    assert extra <= set(NON_PROTO_FIELDS), f"fields the proto lacks: {sorted(extra)}"
    body = {k: v for k, v in resp.items() if k not in NON_PROTO_FIELDS}
    return json_format.ParseDict(body, RC.RemoteChatResponse(), ignore_unknown_fields=False)


def action_names(msg) -> list:
    return [RC.RemoteChatAction.ActionID.Name(a.action) for a in msg.response_actions]


def output_types(msg) -> set:
    return {CR.OutputType.Name(a.output_type) for a in msg.response_actions}


# --------------------------------------------------------------------------- #
# brains: each publishes one reply shape through the real runtime
# --------------------------------------------------------------------------- #
class _Say(MoxieApp):
    name = "say"

    def __init__(self, text="Hi Sam!", **kw):
        self.text, self.kw, self.calls = text, kw, 0

    def respond(self, turn):
        self.calls += 1
        return Reply(text=self.text, **self.kw)


class _Stream(_Say):
    name = "stream"

    def respond_stream(self, turn):
        self.calls += 1
        yield ReplyChunk(text="One moment.", final=False)
        yield ReplyChunk(text="Here it is!", final=True)


class _Offline(_Say):
    name = "offline"

    def respond(self, turn):
        self.calls += 1
        return Reply.offline("")


def _shipped_content_app():
    """A `ContentApp` over the two shipped modules, with a brain that must never run."""
    from moxie_sdk.content import ContentApp, load_modules
    modules = []
    for name in SHIPPED:
        with open(os.path.join(REPO, "mqtt", "content_modules", name)) as fh:
            modules.append(json.load(fh))
    calls = []
    app = ContentApp(load_modules(modules), lambda m: calls.append(m) or "never", memory=False)
    return app, calls


def _module_ids(resp: dict) -> dict:
    return {m["info"]["id"]: [c["id"] for c in m["content_infos"]]
            for m in resp["query_data"]["modules"]}


# --------------------------------------------------------------------------- #
# 1. every reply shape the runtime publishes parses strictly, enum names and all
# --------------------------------------------------------------------------- #
def test_a_plain_reply_parses_strictly_with_the_vision_subscription_on_it():
    app = _Say("Hi Sam!")
    rt, dev = make_runtime(app)
    rt.vision = True
    resp = drive_turn(rt, dev, "hi")
    msg = strict(resp)
    assert msg.result == ResultCode.SUCCESS == 0 and msg.event_id == "evt-1"
    assert msg.output.text == "Hi Sam!" and msg.output.markup
    # the runtime's own subscription rides an action-LESS entry, mirrored on the singular
    assert msg.response_actions[0].event_subscription.active, resp
    assert action_names(msg) == ["UNSET_ACTION_ID"] and output_types(msg) == {"GLOBAL_RESPONSE"}
    assert msg.response_action.event_subscription.active == \
        msg.response_actions[0].event_subscription.active


def test_a_streamed_turn_parses_chunk_by_chunk():
    rt, dev = make_runtime(_Stream())
    rt.streaming = True
    drive_turn(rt, dev, "why?")
    replies = rt.client.chat_replies(dev)
    assert len(replies) == 2, replies
    first, last = (strict(r) for r in replies)
    assert first.result == ResultCode.REPLY_PENDING == 9 and first.chunk_num == 0
    assert first.consistency_control.is_completed is False
    assert last.result == ResultCode.SUCCESS and last.chunk_num == 1
    assert last.consistency_control.is_completed is True
    assert (first.output.text, last.output.text) == ("One moment.", "Here it is!")


def test_a_filler_parses_as_a_pending_chunk():
    rt, dev = make_runtime(_Say())
    rt._say_filler(dev, "evt-filler", 0, "conformance")
    (resp,) = rt.client.chat_replies(dev)
    msg = strict(resp)
    assert msg.result == ResultCode.REPLY_PENDING and msg.chunk_num == 0
    assert msg.consistency_control.is_completed is False and msg.output.text


def test_the_offline_line_parses_with_error_offline():
    rt, dev = make_runtime(_Offline())
    msg = strict(drive_turn(rt, dev, "hello"))
    assert msg.result == ResultCode.ERROR_OFFLINE == 4


def test_every_action_goes_out_under_a_recovered_action_id():
    """launch / exit_module / sleep / execute — and ENABLE_QR, which has no ActionID and
    goes out as the contract's `execute eb_enable_qr("true")` (qr-launch-cards.md §P0-a)."""
    rt, dev = make_runtime(_Say("Let's go!", actions=[
        Action(type=ActionType.LAUNCH, module_id="DRAW", content_id="default"),
        Action(type=ActionType.EXIT),
        Action(type=ActionType.SLEEP),
        Action(type=ActionType.EXECUTE, function="eb_wake"),
        Action(type=ActionType.ENABLE_QR)]))
    resp = drive_turn(rt, dev, "draw?")
    msg = strict(resp)
    assert action_names(msg) == ["launch", "exit_module", "sleep", "execute", "execute"]
    assert output_types(msg) == {"GLOBAL_RESPONSE"}
    assert set(action_names(msg)) == set(ACTION_IDS)
    launch, _, _, wake, qr = msg.response_actions
    assert (launch.module_id, launch.content_id) == ("DRAW", "default")
    assert wake.function_id == "eb_wake" and list(wake.function_args) == []
    assert qr.function_id == "eb_enable_qr" and list(qr.function_args) == ["true"]
    # the JSON spellings themselves, not only what the parser made of them
    assert [a["action"] for a in resp["response_actions"]] == action_names(msg)
    assert {a["output_type"] for a in resp["response_actions"]} == {"GLOBAL_RESPONSE"}


def test_an_execute_with_mapped_args_parses_as_action_args_entries():
    resp = build_chat_response("e", "", actions=[
        Action(type=ActionType.EXECUTE, function="eb_set_volume", args={"level": 3})])
    (entry,) = strict(resp).response_actions
    assert entry.function_id == "eb_set_volume"
    assert [(e.key, e.value) for e in entry.action_args] == [("level", "3")]


def test_a_safety_redirect_parses_with_input_safety():
    app = _Say()
    rt, dev = make_runtime(app)
    resp = drive_turn(rt, dev, "I want to kill myself")
    assert app.calls == 0, "the blocked utterance reached the brain"
    msg = strict(resp)
    assert msg.input.safety.is_unsafe is True
    assert list(msg.input.safety.blocked_by) == ["self_harm"]
    assert list(msg.input_intents) == list(msg.input.safety.intents)
    assert msg.output.text


def test_the_not_paired_line_parses():
    rt, dev = make_runtime(_Say(), allow_unverified_bots=False)
    rt._serve_unpermitted(dev, "remote-chat", json.dumps(
        {"event_id": "evt-pending", "command": "prompt", "backend": "router",
         "speech": "hello"}))
    (resp,) = rt.client.chat_replies(dev)
    msg = strict(resp)
    assert msg.result == ResultCode.SUCCESS and msg.event_id == "evt-pending"
    assert msg.output.text == rt.NOT_PAIRED_LINE


def test_a_silent_acknowledgement_parses():
    msg = strict(build_chat_response("e", "", result=ResultCode.NOREPLY_ACK))
    assert msg.result == ResultCode.NOREPLY_ACK == 6 and msg.output.text == ""


def test_scored_output_parses_with_signals_as_the_remote_signals_message():
    """`RemoteChatOutput.signals` (field 15) is a `RemoteSignals{single_signal,
    volley_signal}` message (RemoteChat.proto:137-155, :176) — a list was a type error."""
    resp = build_chat_response("e", "Bye!", mood="happy", dialog_act="closing",
                               mood_intensity=1, emotion="joy", signals="closing")
    assert resp["output"]["signals"] == {"single_signal": "closing"}
    msg = strict(resp)
    assert msg.output.signals.single_signal == "closing"
    assert (msg.output.mood, msg.output.dialog_act, msg.output.emotion) == \
        ("happy", "closing", "joy")
    assert msg.output.mood_intensity == 1.0
    with pytest.raises(json_format.ParseError):     # the old list shape, measured
        json_format.ParseDict({"output": {"signals": ["closing"]}}, RC.RemoteChatResponse(),
                              ignore_unknown_fields=True)


# --------------------------------------------------------------------------- #
# 2. the result code is the integer the proto declares
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code", list(ResultCode))
def test_result_is_the_uint32_value_not_the_name(code):
    resp = build_chat_response("e", "hi", result=code)
    assert resp["result"] == int(code) and type(resp["result"]) is int
    msg = strict(resp)
    assert msg.result == code
    assert RC.RemoteChatResponse.ResultCode.Name(msg.result) == code.name


def test_the_name_is_what_a_protobuf_robot_cannot_read():
    """The measured defect, pinned: `result: "SUCCESS"` is a type error the robot cannot
    skip, even with unknown fields ignored (`uint32 result = 2`, RemoteChat.proto:320)."""
    with pytest.raises(json_format.ParseError, match="result"):
        json_format.ParseDict({"result": "SUCCESS"}, RC.RemoteChatResponse(),
                              ignore_unknown_fields=True)


def test_the_non_proto_exceptions_are_exactly_command_and_end_turn():
    """`strict` excepts `NON_PROTO_FIELDS` and nothing else; the list must stay two long
    and load-bearing (a plain reply really does not parse with them left in)."""
    assert NON_PROTO_FIELDS == ("command", "end_turn")
    resp = build_chat_response("e", "hi")
    assert set(resp) - PROTO_FIELDS == set(NON_PROTO_FIELDS)
    with pytest.raises(json_format.ParseError, match="command"):
        json_format.ParseDict(resp, RC.RemoteChatResponse(), ignore_unknown_fields=False)


# --------------------------------------------------------------------------- #
# 3. the module query: RemoteDataQuery in, RemoteDataBlock out, no brain
# --------------------------------------------------------------------------- #
def test_the_proto_json_query_and_the_plain_string_are_both_module_queries():
    assert is_module_query({"backend": "data", **PROTO_QUERY})
    assert is_module_query({"backend": "data", "query": "modules"})
    assert not is_module_query({"backend": "router", **PROTO_QUERY})
    assert not is_module_query({"backend": "data", "query": {"query": "contexts"}})
    assert not is_module_query({"backend": "data"})
    # …and the proto-JSON request really is a RemoteChatRequest the robot could have sent
    req = json_format.ParseDict({"backend": "data", "event_id": "q", **PROTO_QUERY},
                                RC.RemoteChatRequest(), ignore_unknown_fields=False)
    assert RC.RemoteDataQuery.Query.Name(req.query.query) == "modules"


def test_a_permitted_robots_module_query_gets_the_shipped_modules_without_a_brain():
    app, calls = _shipped_content_app()
    rt, dev = make_runtime(app)
    resp = drive_turn(rt, dev, "", backend="data", event_id="q-1", **PROTO_QUERY)
    assert calls == [] and rt.history.get(dev, []) == [], "a module query is not a turn"
    assert resp["result"] == 0 and resp["backend"] == "data" and resp["event_id"] == "q-1"
    assert "modules" not in resp, "the list belongs in query_data, not at the top level"
    assert _module_ids(resp) == SHIPPED_IDS
    assert all(m["source"] == "REMOTE_CHAT" for m in resp["query_data"]["modules"])
    msg = strict(resp)
    assert [m.info.id for m in msg.query_data.modules] == list(SHIPPED_IDS)
    for m in msg.query_data.modules:
        assert CM.ModuleDetail.ContentSource.Name(m.source) == "REMOTE_CHAT"
        assert CM.ModuleDetail.ContentRules.Name(m.rules) == "RANDOM"
        assert [c.id for c in m.content_infos] == SHIPPED_IDS[m.info.id]
    assert msg.query_data.version


def test_the_plain_string_query_is_still_answered_for_the_browser_sim():
    app, calls = _shipped_content_app()
    rt, dev = make_runtime(app)
    resp = drive_turn(rt, dev, "", backend="data", query="modules")
    assert calls == [] and _module_ids(resp) == SHIPPED_IDS


def test_an_llm_only_appliance_lists_the_day_plans_chat_module():
    """No content module loaded: the schedule still hands `FREE_CHAT/default` to the cloud
    (schedule/catalog.py:131,:135), so that is the one module the robot is told is remote."""
    app = _Say()
    rt, dev = make_runtime(app)
    resp = drive_turn(rt, dev, "", backend="data", **PROTO_QUERY)
    assert app.calls == 0 and _module_ids(resp) == {"FREE_CHAT": ["default"]}
    strict(resp)


def test_a_pending_robots_module_query_is_answered_empty_in_query_data():
    rt, dev = make_runtime(_Say(), allow_unverified_bots=False)
    rt._serve_unpermitted(dev, "remote-chat", json.dumps(
        {"event_id": "q-pending", "backend": "data", **PROTO_QUERY}))
    (resp,) = rt.client.chat_replies(dev)
    assert resp["query_data"]["modules"] == [] and "modules" not in resp
    msg = strict(resp)
    assert msg.event_id == "q-pending" and list(msg.query_data.modules) == []


def test_content_infos_use_the_protos_contentdetail_shape_not_openmoxies_nesting():
    """`ModuleDetail.content_infos` is `repeated ContentDetail` (ContentModule.proto:66)
    and `ContentDetail.id` is field 1 (:10). OpenMoxie nests `content_infos[].info.id`
    (moxie_remote_chat.py:75); measured through the pb2, a strict parse rejects that and
    a lenient one yields an EMPTY content id. We emit the proto's shape."""
    block = build_remote_modules([("FREE_CHAT", ["default"])])
    assert block["modules"][0]["content_infos"] == [{"id": "default"}]
    msg = strict({"result": 0, "query_data": block})
    assert msg.query_data.modules[0].content_infos[0].id == "default"
    nested = {"modules": [{"info": {"id": "FREE_CHAT"}, "rules": "RANDOM",
                           "source": "REMOTE_CHAT",
                           "content_infos": [{"info": {"id": "default"}}]}]}
    with pytest.raises(json_format.ParseError):
        json_format.ParseDict({"query_data": nested}, RC.RemoteChatResponse(),
                              ignore_unknown_fields=False)
    lenient = json_format.ParseDict({"query_data": nested}, RC.RemoteChatResponse(),
                                    ignore_unknown_fields=True)
    assert lenient.query_data.modules[0].content_infos[0].id == ""


def test_the_block_version_changes_exactly_when_the_list_does():
    one = build_remote_modules([("FREE_CHAT", ["default"])])
    same = build_remote_modules([("FREE_CHAT", ["default"])])
    more = build_remote_modules([("FREE_CHAT", ["default", "aboutme"])])
    assert one["version"] == same["version"] != more["version"]
    assert build_remote_modules([])["modules"] == []


# --------------------------------------------------------------------------- #
# 4. robot-first: the SIL robot asks and reads, through shipped code on both ends
# --------------------------------------------------------------------------- #
def test_the_sil_robot_asks_in_the_recovered_shape_and_reads_the_answer():
    from virtual_moxie import FIRMWARE, VirtualMoxie
    app, calls = _shipped_content_app()
    rt, dev = make_runtime(app, device_id="d_wire_sil")
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    loopback(rt, vm)
    vm.client.publish(vm.t_state, json.dumps({"software_version": FIRMWARE, "state": "config"}))
    vm.send_module_query()
    rt._pool.shutdown(wait=True)
    assert calls == []
    assert {m["info"]["id"]: [c["id"] for c in m["content_infos"]]
            for m in vm.module_list} == SHIPPED_IDS
    strict(vm.reply_payload)


def test_the_sil_robot_reads_the_integer_result_and_still_the_older_name():
    from virtual_moxie import VirtualMoxie
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_codes", verbose=False)
    for pending in (9, "REPLY_PENDING"):
        vm._reset_turn()
        vm._on_chat_reply({"command": "remote_chat", "result": pending, "chunk_num": 0,
                           "event_id": "s", "output": {"text": "One moment."}})
        assert not vm.got_reply.is_set(), pending
        vm._on_chat_reply({"command": "remote_chat", "result": 0, "chunk_num": 1,
                           "event_id": "s", "output": {"text": "Done."},
                           "consistency_control": {"is_completed": True}})
        assert vm.got_reply.is_set() and vm.reply_text == "One moment. Done."


def test_the_sil_robot_leaves_a_module_on_exit_module_and_on_the_older_exit():
    from virtual_moxie import VirtualMoxie
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_leave", verbose=False)
    for verb in ("exit_module", "exit"):
        vm._on_chat_reply({"command": "remote_chat", "result": 0, "event_id": "l",
                           "output": {"text": ""},
                           "response_actions": [{"output_type": "GLOBAL_RESPONSE",
                                                 "action": "launch", "module_id": "DM"},
                                                {"output_type": "GLOBAL_RESPONSE",
                                                 "action": verb}]})
        assert vm.action_stats()["module_id"] == "" and vm.action_stats()["last"] == verb
    assert vm.action_stats()["exits"] == 2 and vm.action_stats()["unknown"] == 0
