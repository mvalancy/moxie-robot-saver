"""
Wire encoders — the JSON shapes on the robot-cloud MQTT bus, kept in the SDK (no
transport deps) so they're pure + unit-testable. Today: the RemoteChat response.

These match the recovered protos (docs/reverse-engineering/protocol/) and the
implementation contract (docs/architecture/ai-seam.md §2).
"""
from __future__ import annotations
from .types import ResultCode


def build_chat_response(event_id, text, markup="", *, backend="router",
                        result=ResultCode.SUCCESS, actions=None, end_turn=False,
                        mood=None, dialog_act=None, modules=None,
                        chunk_num=None, is_completed=None, safety=None) -> dict:
    """Build the RemoteChatResponse JSON.

    Matches embodied/robotbrain/RemoteChat.proto: `result` is the ResultCode enum
    NAME, `output` is a RemoteChatOutput (text/markup + optional scored fields),
    `response_actions` are RemoteChatActions.

    **Multi-chunk (streaming) turns.** One `event_id` may be answered by several
    responses: `result=REPLY_PENDING` (ResultCode 9) means "more chunks to come" and
    `chunk_num` (RemoteChat.proto field 22) orders them; the robot/SIM plays the chunks
    of an event_id in `chunk_num` order (docs/architecture/sim-as-a-client.md:77).
    `is_completed` sets `consistency_control.is_completed`
    (`RemoteConsistencyControl`, field 18 — RemoteChat.proto:201-205), which marks the
    last chunk of the sequence. Both are omitted unless a caller asks for them, so a
    plain single-chunk reply stays byte-identical to what we sent before (chunk 0 /
    not-streaming is the proto default anyway).

    **Moderation.** `safety` (a `moxie_sdk.safety.InputSafety`) fills
    `RemoteChatResponse.input.safety` — `input` is field 17, a `RemoteChatInput`, whose
    field 12 is the `InputSafety{is_unsafe, blocked_by, intents, phrase_id}` message
    (RemoteChat.proto:180-186,:198,:335). Its `intents` are also mirrored onto
    `RemoteChatResponse.input_intents` (field 10, `repeated string`) so a client that
    reads only the flat field still sees the verdict. `RemoteChatInput` is by definition
    the brain's read of *the child's input*, so only a pre-inference (child-side) verdict
    is published here; a block on Moxie's own output has no field in the contract and is
    recorded in the parent review queue instead (docs/architecture/ai-seam.md §2)."""
    rc = result if isinstance(result, ResultCode) else ResultCode(result)
    output = {"text": text, "markup": markup or text}
    if mood:
        output["mood"] = mood
    if dialog_act:
        output["dialog_act"] = dialog_act
    resp = {"command": "remote_chat", "result": rc.name, "backend": backend,
            "event_id": event_id, "output": output, "end_turn": bool(end_turn)}
    ra = []
    for a in (actions or []):
        ra.append({"output_type": "GLOBAL", "action": a.type.value,
                   "module_id": a.module_id, "content_id": a.content_id})
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


# CloudQuery -> the CloudQueryResponse field the answer is keyed under, and that
# field's empty value. Transcribed from the recovered
# `embodied.logging.CloudQueryResponse` (docs/reverse-engineering/protocol/
# recovered-proto/embodied/logging/Cloud.proto:310-352, catalogued in
# proto-catalog.md:213 + :466). Repeated fields default to [], message fields to {}.
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
    """Build the `query_result` JSON (a CloudQueryResponse) that answers a robot's
    `client-service-activity-log` / `subtopic:"query"` request.

    Published to `/devices/{id}/commands/query_result`
    (docs/reverse-engineering/protocol/cloud-protocol.md:147,
    docs/architecture/mqtt-and-conversation.md:296).

    Two things the robot needs and a generic `result` key cannot give it:
      * `request_id` — echoed from `CloudQueryRequest.request_id` (field 5) into
        `CloudQueryResponse.request_id` (field 3) so the robot can correlate the
        answer with its outstanding request. Omitted when the request carried none.
      * the payload keyed by its **own** CloudQueryResponse field — `schedule`,
        `mentor_behaviors`, `license_values`, … — not a generic `result`.

    `payload=None` sends that field's empty value (we answer honestly-empty until
    there is a schedule/mentor-behavior store behind it).

    `response_code` (field 99, QUERY_OK / QUERY_NO_CHANGE / QUERY_NETWORK_FAIL) is
    omitted by default: cloud-protocol.md:232-237 documents the enum but not its JSON
    spelling (name vs. int), and a field-proven server sends the answer without it.
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


# `embodied.robotbrain.MentorBehavior` fields 1-7 — one record of "what the child did"
# (docs/reverse-engineering/protocol/recovered-proto/embodied/robotbrain/
# MentorBehavior.proto:26-36). `action` is a MentorAction (UNKNOWN/QUIT/REFUSED/COMPLETED/
# REQUESTED/PRESENTED/SCHEDULED/SUGGESTED) and `ended_reason` an EndedReason; the docs give
# the enums but not their JSON spelling, so we keep whatever the robot sent verbatim.
# Envelope fields 100 (`software_version`) / 101 (`module_name`) are per-report metadata,
# not history, and are dropped — as OpenMoxie's field-proven `MentorBehavior` model does.
MENTOR_BEHAVIOR_FIELDS = ("module_id", "content_id", "content_day", "timestamp",
                          "action", "instance_id", "ended_reason")


def parse_mentor_behavior(report):
    """Extract one MentorBehavior record from a robot's activity-log report.

    The robot reports a completed/abandoned activity on
    `/devices/{id}/events/client-service-activity-log` — the same topic as the pull
    queries, multiplexed by content rather than `subtopic`
    (docs/reverse-engineering/protocol/cloud-protocol.md:172, "…or a `mentor_behavior`
    report"). The carrier is an `embodied.logging.ActivityUpdate`, whose field 14 *is*
    `mentor_behavior` (Cloud.proto:241) — so the record arrives under that key.

    Accepts either the whole envelope (`{"mentor_behavior": {...}, "timestamp": …}`) or a
    bare record. Returns the record reduced to `MENTOR_BEHAVIOR_FIELDS`, or None if there
    is no usable record (no `module_id` → nothing a schedule could ever act on).
    """
    if isinstance(report, dict) and isinstance(report.get("mentor_behavior"), dict):
        report = report["mentor_behavior"]
    if not isinstance(report, dict):
        return None
    rec = {k: report[k] for k in MENTOR_BEHAVIOR_FIELDS
           if k in report and report[k] not in (None, "")}
    return rec if rec.get("module_id") else None
