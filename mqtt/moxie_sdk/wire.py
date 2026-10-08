"""
Wire encoders — the JSON shapes on the robot-cloud MQTT bus, kept in the SDK (no
transport deps) so they're pure + unit-testable. Today: the RemoteChat response.

These match the recovered protos (docs/reverse-engineering/protocol/) and the
implementation contract (docs/architecture/ai-seam.md §2).
"""
from __future__ import annotations
from collections.abc import Mapping, Sequence
import hashlib, json

from .types import (ActionType, ResultCode, ENABLE_QR_ARGS, ENABLE_QR_FUNCTION,
                    OUTPUT_TYPE_RESPONSE)


def _arg_str(value) -> str:
    """One `function_args` / `ActionArgsEntry.value` element as a wire string (both are
    `string` in RemoteChat.proto:271-273,:280). Booleans go out JSON-style: `"true"`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else str(value)


def encode_action(a) -> dict:
    """One `moxie_sdk.types.Action` as one `RemoteChatAction` JSON entry.

    `{output_type, action}` plus `module_id` / `content_id` when set (as OpenMoxie's
    `add_response_action` sends them, volley.py:125-130; proto3 JSON reads an absent
    field and a `null` alike) plus, for an `execute`, the recovered fields
    (RemoteChat.proto:255-281): `function_id` (7), and `Action.args` by type — a
    sequence → `function_args` (8, `repeated string`), a mapping → `action_args`
    (10, `repeated ActionArgsEntry{key, value}`). Omitted when absent, so other actions
    stay byte-identical.

    `action` is the `ActionID` name (`types.ACTION_IDS`) and `output_type` is
    `GLOBAL_RESPONSE`, both enum names the robot's protobuf-JSON parser knows; an
    `ENABLE_QR` has no ActionID and goes out as `execute eb_enable_qr("true")` — its own
    `function`/`args` are not consulted.

    An `Action` whose `type` is a name the enum does not know (a raw string an app built
    it with) has no ActionID to go out under: returns None after one logged line, and
    `build_chat_response` drops that entry alone, never the reply. The older `"exit"`
    spelling is a known name (`ActionType._missing_`).
    """
    try:
        kind = ActionType(a.type)
    except ValueError:
        print(f"[wire] dropped an action with no ActionID: {a.type!r}", flush=True)
        return None
    entry = {"output_type": OUTPUT_TYPE_RESPONSE, "action": kind.value}
    if a.module_id:
        entry["module_id"] = a.module_id
    if a.content_id:
        entry["content_id"] = a.content_id
    function = getattr(a, "function", None)
    args = getattr(a, "args", None)
    if kind is ActionType.ENABLE_QR:
        entry["action"] = ActionType.EXECUTE.value
        function, args = ENABLE_QR_FUNCTION, list(ENABLE_QR_ARGS)
    if function:
        entry["function_id"] = function
    if args:
        if isinstance(args, Mapping):
            entry["action_args"] = [{"key": str(k), "value": _arg_str(v)}
                                    for k, v in args.items()]
        elif isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
            entry["function_args"] = [_arg_str(args)]   # a lone scalar is one argument
        else:
            entry["function_args"] = [_arg_str(v) for v in args]
    return entry


def encode_signals(signals) -> dict:
    """`RemoteChatOutput.signals` is a `RemoteSignals` MESSAGE (RemoteChat.proto:137-155,
    field 15 at :176) — `{single_signal, volley_signal}`, two strings — not a list. One
    name fills `single_signal`; a `(single, volley)` pair fills both; a third has no field.
    """
    names = [signals] if isinstance(signals, str) else [s for s in signals if s]
    out = {}
    if names:
        out["single_signal"] = str(names[0])
    if len(names) > 1:
        out["volley_signal"] = str(names[1])
    return out


#: Fields on our `remote_chat` JSON that `RemoteChatResponse` does not declare: exactly
#: the one OpenMoxie also sends on every response (field-proven on real robots), the
#: `command` dispatch key. A robot that consumes `command` and parses the rest strictly
#: would reject any other extra, so nothing else is added — `Reply.end_turn`, an SDK
#: hint with no proto field, left the wire on 2026-10-08. The conformance test excepts
#: exactly this list and nothing else.
NON_PROTO_FIELDS = ("command",)


def build_chat_response(event_id, text, markup="", *, backend="router",
                        result=ResultCode.SUCCESS, actions=None, end_turn=False,
                        mood=None, dialog_act=None, query_data=None,
                        chunk_num=None, is_completed=None, safety=None,
                        subscribe_events=None, mood_intensity=None, emotion=None,
                        signals=None) -> dict:
    """Build the RemoteChatResponse JSON (embodied/robotbrain/RemoteChat.proto).

    Every optional part is omitted when empty, so a plain reply stays byte-identical.
    Every value is what the robot's protobuf-JSON parser accepts for its field: `result`
    is the integer (`uint32`, :320), enum-typed fields carry enum NAMES, and the whole
    document parses strictly through the committed pb2 files but for `NON_PROTO_FIELDS`
    (`sim/tests/test_wire_conformance.py`). `end_turn` is accepted for `Reply` /
    `ReplyChunk` symmetry and is NOT written: it has no proto field, nothing on the
    robot side reads it, and `REPLY_PENDING` already says more is coming.

    * **Chunks.** `result=REPLY_PENDING` + `chunk_num` (field 22) order a streamed turn;
      `is_completed` sets `consistency_control.is_completed` (field 18) on the last.
    * **Scored output** (ai-seam.md §2): `mood`/`mood_intensity` (ePlaybackMood label +
      0-2), `dialog_act`, `emotion` (EmotionState label), `signals` (a `RemoteSignals`
      message, see `encode_signals`). Filled by the behavior planner
      (`supervisor/markup.py::perform`).
    * **Moderation.** `safety` (an `InputSafety`) fills `input.safety` (field 17 → 12)
      and mirrors its intents onto `input_intents` (field 10). Child-side verdicts only;
      an output-side block has no contract field (it goes to the parent review queue).
    * **Actions** via `encode_action`. Every reply carries `response_actions` — the
      actions, or one action-less `{output_type: GLOBAL_RESPONSE}` entry — and the legacy
      singular `response_action` mirrors `[0]`: OpenMoxie's field-proven envelope
      (volley.py `create_response`, `add_response_action`), so a robot reading
      `output_type` sees GLOBAL_RESPONSE on a plain reply, not the default CATCH_ALL.
    * **Event subscription.** `subscribe_events` fills
      `RemoteChatAction.EventSubscription{clear, active[]}` (remote-chat-protocol.md:81-84)
      on `response_actions[0]` (mqtt-and-conversation.md §4.1). Without it the robot
      discards its own vision events.
    * **Module list.** `query_data` is the `RemoteDataBlock` (field 21) answering a
      module query — see `build_remote_modules`.
    """
    rc = result if isinstance(result, ResultCode) else ResultCode(result)
    output = {"text": text, "markup": markup or text}
    if mood:
        output["mood"] = mood
    if dialog_act:
        output["dialog_act"] = dialog_act
    if mood_intensity:
        output["mood_intensity"] = int(mood_intensity)
    if emotion:
        output["emotion"] = emotion
    if signals:
        output["signals"] = encode_signals(signals)
    resp = {"command": "remote_chat", "result": int(rc), "backend": backend,
            "event_id": event_id, "output": output}
    ra = [e for e in map(encode_action, actions or []) if e is not None]
    if not ra:
        ra.append({"output_type": OUTPUT_TYPE_RESPONSE})     # action-less, as OpenMoxie
    if subscribe_events:
        ra[0]["event_subscription"] = {"active": list(subscribe_events), "clear": False}
    resp["response_action"] = ra[0]              # legacy singular, a mirror of [0]
    resp["response_actions"] = ra
    if query_data is not None:
        resp["query_data"] = query_data
    if chunk_num is not None:
        resp["chunk_num"] = int(chunk_num)
    if is_completed is not None:
        resp["consistency_control"] = {"is_completed": bool(is_completed)}
    if safety is not None:
        wire = safety.to_wire() if hasattr(safety, "to_wire") else dict(safety)
        resp["input"] = {"safety": wire}
        if wire.get("intents"):
            resp["input_intents"] = list(wire["intents"])
    return resp


# ---- the remote module list: RemoteDataQuery in, RemoteDataBlock out ----
DATA_BACKEND = "data"
MODULES_QUERY = "modules"
#: `RemoteDataQuery.Query.modules` by number (RemoteChat.proto:42-46): protobuf JSON spells
#: an enum by name or by number, so a robot may send either. Pinned against the committed
#: pb2 by `test_wire_conformance.py`.
MODULES_QUERY_VALUE = 2


def is_data_query(rcr) -> bool:
    """A `backend: "data"` RemoteChatRequest: a data request, never a conversational turn.
    OpenMoxie answers only the module query and `backend == "router"` turns
    (moxie_server.py:170-179)."""
    return isinstance(rcr, dict) and rcr.get("backend") == DATA_BACKEND


def query_name(rcr):
    """What a data request asks for, as sent: `RemoteDataQuery.query` by name or by number
    (`{"query": {"query": "modules"}}`, `{"query": {"query": 2}}`), or the plain string
    older test doubles send (`{"query": "modules"}`); None when there is no query."""
    query = rcr.get("query") if isinstance(rcr, dict) else None
    if isinstance(query, dict):
        query = query.get("query")
    return query


def is_module_query(rcr) -> bool:
    """Is this RemoteChatRequest the robot asking which modules the cloud serves?

    The recovered request is `backend: "data"` with `query: RemoteDataQuery{query:
    modules}` (RemoteChat.proto:41-51, field 23 at :79), which OpenMoxie reads as
    `rcr['query']['query'] == "modules"` (moxie_server.py:170); the enum's number, 2, is
    the other spelling protobuf JSON allows. The plain `query: "modules"` string is
    accepted too — only older test doubles send it (the browser Sim sends no module query).
    """
    return is_data_query(rcr) and query_name(rcr) in (MODULES_QUERY, MODULES_QUERY_VALUE)


def build_remote_modules(modules) -> dict:
    """The `RemoteDataBlock` (RemoteChat.proto:296-300) answering a module query, carried
    as `RemoteChatResponse.query_data` (field 21, :339): `modules[]` of `ModuleDetail`
    (ContentModule.proto:24-73), each `{info: ContentDetail{id}, rules, source,
    content_infos[]: ContentDetail{id}}`, plus `version` (field 1).

    `modules` is `[(module_id, [content_id, …]), …]`. Every entry is a remote-chat
    module — `source: REMOTE_CHAT` (ContentSource 1, :38) — with `rules: RANDOM`
    (ContentRules 3, :29): the "bare bones mandatory fields" of OpenMoxie's field-proven
    answer (moxie_remote_chat.py:73-77, sent by moxie_server.py:176). One deliberate
    difference: OpenMoxie nests each content id as `content_infos[].info.id`, but
    `ModuleDetail.content_infos` is `repeated ContentDetail` (:66) and `ContentDetail`
    carries `id` directly (:10), so a protobuf parse of OpenMoxie's shape yields empty
    content ids; this emits the proto's shape. `version` is a digest of the ids, so a
    robot that caches by version sees a change exactly when the list changes.
    """
    entries = [{"info": {"id": str(module_id)}, "rules": "RANDOM", "source": "REMOTE_CHAT",
                "content_infos": [{"id": str(c)} for c in content_ids]}
               for module_id, content_ids in modules]
    ids = [[m["info"]["id"], [c["id"] for c in m["content_infos"]]] for m in entries]
    digest = hashlib.sha1(json.dumps(ids).encode()).hexdigest()[:12]
    return {"version": f"mrs-{digest}", "modules": entries}


# CloudQuery -> (CloudQueryResponse field, its empty value), per the recovered
# Cloud.proto:310-352.
_QUERY_PAYLOAD = {
    "idf":              ("idf_values",         []),   # field 4,  repeated IDFRecord
    "license":          ("license_values",     []),   # field 5,  repeated LicenseRecord
    "schedule":         ("schedule",           {}),   # field 6,  ContentSchedule
    "contexts":         ("contexts",           {}),   # field 7,  Contexts
    "context_store":    ("versioned_contexts", []),   # field 9,  repeated VersionedContextsEntry
    "mentor_behaviors": ("mentor_behaviors",   []),   # field 10, repeated MentorBehavior
    "remote_lines":     ("remote_lines",       []),   # field 12, repeated DynamicLine
}


def build_activity_response(query, payload=None, request_id=None, *,
                            response_code=None) -> dict:
    """Build the `query_result` CloudQueryResponse answering a robot's activity-log
    `subtopic:"query"` request (published to `/devices/{id}/commands/query_result`).

    Echoes `request_id` (when given) and keys the payload by its own response field
    (`schedule`, `mentor_behaviors`, …); `payload=None` sends that field's empty value.
    `response_code` (field 99) is omitted by default — its JSON spelling is unrecorded.
    """
    try:
        key, empty = _QUERY_PAYLOAD[query]
    except (KeyError, TypeError):
        raise ValueError(f"unknown CloudQuery {query!r}") from None
    resp = {"command": "query_result", "query": query}
    if request_id is not None:
        resp["request_id"] = request_id
    resp[key] = empty.copy() if payload is None else payload
    if response_code is not None:
        resp["response_code"] = response_code
    return resp


# `MentorBehavior` fields 1-7 (MentorBehavior.proto:26-36) — what the child did. Enum
# values are kept verbatim (JSON spelling unrecorded); envelope fields 100/101 dropped.
MENTOR_BEHAVIOR_FIELDS = ("module_id", "content_id", "content_day", "timestamp",
                          "action", "instance_id", "ended_reason")


def parse_mentor_behavior(report):
    """Extract one MentorBehavior from an activity-log report (`ActivityUpdate` field 14,
    Cloud.proto:241), given the envelope or a bare record. Reduced to
    `MENTOR_BEHAVIOR_FIELDS`; None without a `module_id`.
    """
    if isinstance(report, dict) and isinstance(report.get("mentor_behavior"), dict):
        report = report["mentor_behavior"]
    if not isinstance(report, dict):
        return None
    rec = {k: report[k] for k in MENTOR_BEHAVIOR_FIELDS
           if k in report and report[k] not in (None, "")}
    return rec if rec.get("module_id") else None
