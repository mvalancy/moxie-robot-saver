"""
Wire encoders — the JSON shapes on the robot-cloud MQTT bus, kept in the SDK (no
transport deps) so they're pure + unit-testable. Today: the RemoteChat response.

These match the recovered protos (docs/reverse-engineering/protocol/) and the
implementation contract (docs/architecture/ai-seam.md §2).
"""
from __future__ import annotations
from collections.abc import Mapping, Sequence

from .types import ResultCode


def _arg_str(value) -> str:
    """One `function_args` / `ActionArgsEntry.value` element as a wire string (both are
    `string` in RemoteChat.proto:271-273,:280). Booleans go out JSON-style: `"true"`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else str(value)


def encode_action(a) -> dict:
    """One `moxie_sdk.types.Action` as one `RemoteChatAction` JSON entry.

    `{output_type, action, module_id, content_id}` plus, for an `execute`, the recovered
    fields (RemoteChat.proto:255-281): `function_id` (7), and `Action.args` by type — a
    sequence → `function_args` (8, `repeated string`), a mapping → `action_args`
    (10, `repeated ActionArgsEntry{key, value}`). Omitted when absent, so other actions
    stay byte-identical.
    """
    entry = {"output_type": "GLOBAL", "action": a.type.value,
             "module_id": a.module_id, "content_id": a.content_id}
    function = getattr(a, "function", None)
    if function:
        entry["function_id"] = function
    args = getattr(a, "args", None)
    if args:
        if isinstance(args, Mapping):
            entry["action_args"] = [{"key": str(k), "value": _arg_str(v)}
                                    for k, v in args.items()]
        elif isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
            entry["function_args"] = [_arg_str(args)]   # a lone scalar is one argument
        else:
            entry["function_args"] = [_arg_str(v) for v in args]
    return entry


def build_chat_response(event_id, text, markup="", *, backend="router",
                        result=ResultCode.SUCCESS, actions=None, end_turn=False,
                        mood=None, dialog_act=None, modules=None,
                        chunk_num=None, is_completed=None, safety=None,
                        subscribe_events=None, mood_intensity=None, emotion=None,
                        signals=None) -> dict:
    """Build the RemoteChatResponse JSON (embodied/robotbrain/RemoteChat.proto).

    Every optional part is omitted when empty, so a plain reply stays byte-identical.

    * **Chunks.** `result=REPLY_PENDING` + `chunk_num` (field 22) order a streamed turn;
      `is_completed` sets `consistency_control.is_completed` (field 18) on the last.
    * **Scored output** (ai-seam.md §2): `mood`/`mood_intensity` (ePlaybackMood label +
      0-2), `dialog_act`, `emotion` (EmotionState label), `signals` (always a list —
      `repeated`). Filled by the behavior planner (`supervisor/markup.py::perform`).
    * **Moderation.** `safety` (an `InputSafety`) fills `input.safety` (field 17 → 12)
      and mirrors its intents onto `input_intents` (field 10). Child-side verdicts only;
      an output-side block has no contract field (it goes to the parent review queue).
    * **Actions** via `encode_action`.
    * **Event subscription.** `subscribe_events` fills
      `RemoteChatAction.EventSubscription{clear, active[]}` (remote-chat-protocol.md:103-106)
      on `response_actions[0]` (a bare `{output_type}` entry if there is no action), mirrored
      onto the legacy singular `response_action` (mqtt-and-conversation.md §4.1). Without
      it the robot discards its own vision events.
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
        output["signals"] = [signals] if isinstance(signals, str) else list(signals)
    resp = {"command": "remote_chat", "result": rc.name, "backend": backend,
            "event_id": event_id, "output": output, "end_turn": bool(end_turn)}
    ra = [encode_action(a) for a in (actions or [])]
    if subscribe_events:
        if not ra:
            ra.append({"output_type": "GLOBAL"})
        ra[0]["event_subscription"] = {"active": list(subscribe_events), "clear": False}
        resp["response_action"] = ra[0]          # legacy singular, kept in sync
    if ra:
        resp["response_actions"] = ra
    if modules is not None:
        resp["modules"] = modules
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
