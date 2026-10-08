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
the proto knows. The ONLY field excepted is `wire.NON_PROTO_FIELDS`: `command`, which
OpenMoxie (MIT, field-proven on real robots) also sends on every response (volley.py
`create_response`; moxie_server.py:176). `end_turn`, an SDK hint with no proto home that
nothing on the robot side reads, left the wire on 2026-10-08: a robot that consumes
`command` and parses the rest strictly would have rejected every reply over it.

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
from moxie_sdk.wire import (MODULES_QUERY_VALUE, NON_PROTO_FIELDS,        # noqa: E402
                            build_chat_response, build_remote_modules,
                            is_module_query)

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


def test_every_reply_carries_the_global_response_entry_openmoxie_sends():
    """OpenMoxie's field-proven envelope (volley.py `create_response`,
    `add_response_action`): every reply has `response_actions[0]` and its mirror
    `response_action` with `output_type: GLOBAL_RESPONSE` — an action-less entry on a plain
    reply, so a robot reading `output_type` sees GLOBAL_RESPONSE rather than the default
    CATCH_ALL; the action itself when there is one. `module_id` / `content_id` ride only
    when set, as OpenMoxie sends them."""
    plain = build_chat_response("e", "Hi!")
    assert plain["response_actions"] == [{"output_type": "GLOBAL_RESPONSE"}]
    assert plain["response_action"] == plain["response_actions"][0]
    msg = strict(plain)
    assert output_types(msg) == {"GLOBAL_RESPONSE"} and action_names(msg) == ["UNSET_ACTION_ID"]
    assert CR.OutputType.Name(msg.response_action.output_type) == "GLOBAL_RESPONSE"
    acted = build_chat_response("e", "Bye!", actions=[
        Action(type=ActionType.EXIT), Action(type=ActionType.LAUNCH, module_id="DRAW")])
    assert acted["response_actions"] == [
        {"output_type": "GLOBAL_RESPONSE", "action": "exit_module"},
        {"output_type": "GLOBAL_RESPONSE", "action": "launch", "module_id": "DRAW"}]
    assert acted["response_action"] == acted["response_actions"][0]
    msg = strict(acted)
    assert action_names(msg) == ["exit_module", "launch"]
    assert output_types(msg) == {"GLOBAL_RESPONSE"}
    assert RC.RemoteChatAction.ActionID.Name(msg.response_action.action) == "exit_module"
    # Through the runtime, on a reply with no subscription left to send.
    rt, dev = make_runtime(_Say("Again!"))
    rt.vision = False
    assert drive_turn(rt, dev, "again")["response_actions"] == [{"output_type": "GLOBAL_RESPONSE"}]
    # …and the SIL robot reads the bare entry as what it is: no action, nothing unknown.
    from virtual_moxie import VirtualMoxie
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_bare", verbose=False)
    vm._on_chat_reply(plain)
    assert vm.action_stats()["unknown"] == 0 and vm.action_stats()["applied"] == []


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


def test_an_action_with_no_action_id_is_dropped_alone_and_the_reply_still_goes_out(capsys):
    """An `Action` built with a name the enum does not know used to raise inside
    `_publish_chat` — an `assert` (stripped under -O) behind an attribute error — and the
    whole spoken reply was lost. That one entry is dropped with a logged line; the line
    and the actions the robot can read still go out, and the older `"exit"` spelling is
    not a stranger."""
    rt, dev = make_runtime(_Say("Let's go!", actions=[
        Action(type="teleport_to_mars"),
        Action(type=ActionType.LAUNCH, module_id="DRAW", content_id="default"),
        Action(type="exit")]))
    resp = drive_turn(rt, dev, "go?")
    assert resp["output"]["text"] == "Let's go!"
    assert action_names(strict(resp)) == ["launch", "exit_module"]
    assert "teleport_to_mars" in capsys.readouterr().out, "the drop must be logged"
    alone = build_chat_response("e", "hi", actions=[Action(type=None)])
    assert [a for a in alone["response_actions"] if a.get("action")] == []


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
    volley_signal}` message (RemoteChat.proto:137-155, :176), not a list. Measured through
    the pb2: the old `["closing"]` is read as field NAMES — a strict parse rejects it and
    a lenient one yields an EMPTY message, so the signal was silently lost."""
    resp = build_chat_response("e", "Bye!", mood="happy", dialog_act="closing",
                               mood_intensity=1, emotion="joy", signals="closing")
    assert resp["output"]["signals"] == {"single_signal": "closing"}
    msg = strict(resp)
    assert msg.output.signals.single_signal == "closing"
    assert (msg.output.mood, msg.output.dialog_act, msg.output.emotion) == \
        ("happy", "closing", "joy")
    assert msg.output.mood_intensity == 1.0
    old = {"output": {"signals": ["closing"]}}
    with pytest.raises(json_format.ParseError, match='no field named "closing"'):
        json_format.ParseDict(old, RC.RemoteChatResponse(), ignore_unknown_fields=False)
    lost = json_format.ParseDict(old, RC.RemoteChatResponse(), ignore_unknown_fields=True)
    assert lost.output.signals.single_signal == ""


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


def test_the_only_non_proto_field_is_command():
    """`strict` excepts `NON_PROTO_FIELDS` and nothing else; the list is exactly the one
    key OpenMoxie also sends, and load-bearing (a plain reply really does not parse with
    it left in). `end_turn` — accepted by the encoder for `Reply` symmetry — is never
    written: no proto field, no reader on the robot side."""
    assert NON_PROTO_FIELDS == ("command",)
    resp = build_chat_response("e", "hi", end_turn=True)
    assert set(resp) - PROTO_FIELDS == {"command"}
    assert "end_turn" not in resp
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


def test_the_plain_string_query_older_doubles_send_is_still_answered():
    """Only test doubles ever sent `query: "modules"` as a bare string; the browser Sim
    sends no module query at all (no `backend: "data"` anywhere in sim/web)."""
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


def test_the_module_query_by_enum_number_is_the_module_query():
    """`RemoteDataQuery.Query.modules` is 2 (RemoteChat.proto:45) and protobuf JSON may
    carry the number instead of the name; the committed pb2 says which number."""
    assert RC.RemoteDataQuery.Query.Value("modules") == MODULES_QUERY_VALUE == 2
    assert RC.RemoteDataQuery.Query.Value("contexts") == 1
    assert is_module_query({"backend": "data", "query": {"query": 2}})
    assert not is_module_query({"backend": "data", "query": {"query": 1}})
    req = json_format.ParseDict({"backend": "data", "query": {"query": 2}},
                                RC.RemoteChatRequest(), ignore_unknown_fields=False)
    assert RC.RemoteDataQuery.Query.Name(req.query.query) == "modules"
    app, calls = _shipped_content_app()
    rt, dev = make_runtime(app)
    resp = drive_turn(rt, dev, "", backend="data", event_id="q-2", query={"query": 2})
    assert calls == [] and _module_ids(resp) == SHIPPED_IDS
    strict(resp)


@pytest.mark.parametrize("query", [
    {"query": {"query": "contexts"}},          # RemoteDataQuery{query: contexts}, by name
    {"query": {"query": 1}},                   # …by number (RemoteChat.proto:44)
    {},                                        # a data request with no query at all
], ids=["contexts", "contexts-by-number", "no-query"])
def test_a_data_query_that_is_not_the_module_list_reaches_no_brain_and_says_nothing(query):
    """Measured on origin/dev and on this branch before the fix: each of these went to the
    brain and Moxie spoke its line, published as backend "router". OpenMoxie answers only
    the module query and `router` turns (moxie_server.py:170-179), and nothing in the
    recovered proto makes a reply mandatory (RemoteChat.proto:41-51, :296-300: every
    field optional). So: no brain, nothing published, one logged line."""
    app = _Say("Hi Sam!")
    rt, dev = make_runtime(app)
    rt._on_remote_chat(dev, rt.robots[dev], json.dumps(
        {"backend": "data", "event_id": "q-other", **query}))
    rt._pool.shutdown(wait=True)
    assert app.calls == 0, "the brain was asked"
    assert rt.client.published == [], "something was published"
    assert rt.history.get(dev, []) == []
    assert [n for n in rt.recent if "data query" in n["text"]], list(rt.recent)


def test_a_pending_robots_module_query_is_answered_empty_in_query_data():
    rt, dev = make_runtime(_Say(), allow_unverified_bots=False)
    rt._serve_unpermitted(dev, "remote-chat", json.dumps(
        {"event_id": "q-pending", "backend": "data", **PROTO_QUERY}))
    (resp,) = rt.client.chat_replies(dev)
    assert resp["query_data"]["modules"] == [] and "modules" not in resp
    msg = strict(resp)
    assert msg.event_id == "q-pending" and list(msg.query_data.modules) == []


def test_a_pending_robots_other_data_query_is_dropped_not_told_to_find_a_grown_up():
    """The pending path spoke NOT_PAIRED_LINE to a `contexts` query: spoken output for a
    data request. Dropped now, as on the permitted path; the module query by number is
    still answered empty."""
    rt, dev = make_runtime(_Say(), allow_unverified_bots=False)
    rt._serve_unpermitted(dev, "remote-chat", json.dumps(
        {"event_id": "q-c", "backend": "data", "query": {"query": "contexts"}}))
    assert rt.client.published == [], rt.client.published
    rt._serve_unpermitted(dev, "remote-chat", json.dumps(
        {"event_id": "q-2", "backend": "data", "query": {"query": 2}}))
    (resp,) = rt.client.chat_replies(dev)
    assert resp["event_id"] == "q-2" and resp["query_data"]["modules"] == []


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


def test_the_bench_robot_reads_the_integer_result_as_the_end_of_its_turn():
    """`sim/tools/first_audio_ab.py::TimedRobot` is the third robot double here, and the
    one `--brain live` runs through. It closed a turn on `result == "SUCCESS"` alone, so
    once `result` became the integer every one-response answer (one sentence, or
    `MOXIE_STREAMING=0`) waited the whole 120 s timeout and reported `ok=False`; CI never
    saw it because the stub brain streams four sentences. Read through `ResultCode`: the
    integer, and still the older name."""
    tools = os.path.join(REPO, "sim", "tools")
    if tools not in sys.path:
        sys.path.append(tools)
    import first_audio_ab as AB

    class _Msg:                      # paho's MQTTMessage, the two attributes read
        def __init__(self, payload):
            self.topic = "/devices/d_bench/commands/remote_chat"
            self.payload = json.dumps(payload).encode()

    robot = AB.TimedRobot("127.0.0.1", 1, device_id="d_bench")       # never connects
    robot._on_message(None, None, _Msg(build_chat_response("e", "Hi Sam!")))
    assert robot.done.is_set(), "a one-response answer must end the turn"
    for pending, closing in ((ResultCode.REPLY_PENDING, ResultCode.SUCCESS),
                             ("REPLY_PENDING", "SUCCESS")):           # an older server
        robot.done.clear()
        robot._on_message(None, None, _Msg({"result": pending, "chunk_num": 0,
                                            "output": {"text": "One moment."},
                                            "consistency_control": {"is_completed": False}}))
        assert not robot.done.is_set(), pending
        # the closing result alone (no `consistency_control`) ends the turn too
        robot._on_message(None, None, _Msg({"result": closing, "output": {"text": "Done."}}))
        assert robot.done.is_set(), closing
    robot.done.clear()
    robot._on_message(None, None, _Msg({"result": "nonsense", "output": {"text": "?"}}))
    assert not robot.done.is_set(), "a value that is no ResultCode neither closes nor raises"
    assert AB.result_code({"result": 0}) is AB.result_code({"result": "SUCCESS"}) is \
        ResultCode("SUCCESS") is ResultCode.SUCCESS
