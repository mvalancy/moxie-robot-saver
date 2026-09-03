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
                        chunk_num=None, is_completed=None, safety=None,
                        subscribe_events=None, mood_intensity=None, emotion=None,
                        signals=None) -> dict:
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

    **Scored output.** `RemoteChatOutput` is a *scored* line, not just text
    (ai-seam.md §2, "Response out (a)"): `mood` + `mood_intensity` are the emotional
    performance to render on the face, and `dialog_act` / `emotion` / `signals` are what
    the line MEANS. `mood` and `emotion` are label strings — `mood` from `ePlaybackMood`
    (`happy`, `curious`, …; the int form lives in the `cmd:playback-mood` mark inside
    `markup`) and `emotion` from `RemoteDialog.EmotionState`. `signals` accepts one
    `RemoteSignals.Signal` name or a list of them and always goes out as a list, because
    the field is `repeated` (remote-chat-protocol.md:124-126). Every one of them is
    omitted when empty, so a turn that scores nothing is byte-identical to what we sent
    before. Who fills them: the behavior planner, through `supervisor/markup.py::perform`
    — see backlog/expressiveness.md §2.3 (C3).

    **Moderation.** `safety` (a `moxie_sdk.safety.InputSafety`) fills
    `RemoteChatResponse.input.safety` — `input` is field 17, a `RemoteChatInput`, whose
    field 12 is the `InputSafety{is_unsafe, blocked_by, intents, phrase_id}` message
    (RemoteChat.proto:180-186,:198,:335). Its `intents` are also mirrored onto
    `RemoteChatResponse.input_intents` (field 10, `repeated string`) so a client that
    reads only the flat field still sees the verdict. `RemoteChatInput` is by definition
    the brain's read of *the child's input*, so only a pre-inference (child-side) verdict
    is published here; a block on Moxie's own output has no field in the contract and is
    recorded in the parent review queue instead (docs/architecture/ai-seam.md §2).

    **Event subscription.** `subscribe_events` (robot event names, e.g.
    `["eb-found-face", "eb-lost-target"]`) fills
    `RemoteChatAction.EventSubscription{clear, active[]}` — the contract's own way for a
    brain to ask the robot to *push* it perception events (remote-chat-protocol.md:103-106;
    ai-seam.md §2(b); the record's `clear`/`active` field names per OpenMoxie
    `doc/RemoteModuleAPI.md` §"Event subscription record", MIT). Without it the robot
    discards its own vision events — they are "internal events that are discarded by the
    application stack unless the active module is specifically interested". It rides an
    *action-less* `response_actions[0]` (a bare `{output_type}` entry is the shape a
    field-proven server sends) and is mirrored onto the legacy singular `response_action`
    (mqtt-and-conversation.md §4.1). Omitted when empty/None, so every reply that does not
    ask for events is byte-identical to what we sent before."""
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
    ra = []
    for a in (actions or []):
        ra.append({"output_type": "GLOBAL", "action": a.type.value,
                   "module_id": a.module_id, "content_id": a.content_id})
    if subscribe_events:
        # An action-less entry carrying only the subscription: we are not asking the
        # robot to launch/exit anything, only to start pushing us these events.
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
